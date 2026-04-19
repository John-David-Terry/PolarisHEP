#!/usr/bin/env python3
"""
STEP 1: Stream all papers + keywords; assign row_idx; write paper_index.parquet + stats.

Does not store full canonical text at corpus scale by default (text rebuilt during embedding).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from inspire_embedding_common import (
    canonical_embedding_text,
    connect_readonly_sqlite,
    normalize_keywords_blob,
    repo_root,
    resolve_db_path,
    text_hash_sha256,
)


def classify_availability(has_t: bool, has_a: bool, has_k: bool) -> str:
    if has_t and has_a and has_k:
        return "title_abstract_keywords"
    if has_t and has_a:
        return "title_abstract"
    if has_t and has_k:
        return "title_keywords"
    if has_a and has_k:
        return "abstract_keywords"
    if has_t:
        return "title_only"
    if has_a:
        return "abstract_only"
    if has_k:
        return "keywords_only"
    return "empty"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("build_texts")

    ap = argparse.ArgumentParser(description="Build canonical-text metadata for INSPIRE embedding")
    ap.add_argument("--db", default="inspire_mirror.sqlite")
    ap.add_argument("--output-root", default="outputs_no_sync/inspire_embeddings")
    ap.add_argument("--fetch-chunk-size", type=int, default=10000)
    ap.add_argument("--sample-size", type=int, default=400)
    ap.add_argument("--max-papers", type=int, default=0, help="If >0, stop after this many papers (smoke test)")
    ap.add_argument("--include-title-in-index", action="store_true", default=True)
    ap.add_argument("--no-title-in-index", action="store_false", dest="include_title_in_index")
    args = ap.parse_args()

    root = repo_root()
    db_path = resolve_db_path(root, args.db)
    out_root = (root / args.output_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "samples").mkdir(parents=True, exist_ok=True)
    (out_root / "logs").mkdir(parents=True, exist_ok=True)

    index_path = out_root / "paper_index.parquet"
    stats_path = out_root / "text_build_stats.json"
    samples_path = out_root / "samples" / "text_samples.parquet"

    conn = connect_readonly_sqlite(db_path)
    total_approx = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    total_target = min(total_approx, args.max_papers) if args.max_papers else total_approx
    log.info("papers.total_in_db=%s | progress_target=%s", total_approx, total_target)

    pattern_counter: Counter[str] = Counter()
    row_idx_next = 0
    stats_chars: list[int] = []
    stats_kw: list[int] = []

    parquet_writer: pq.ParquetWriter | None = None
    schema: pa.Schema | None = None

    reservoir: list[dict] = []
    rng = np.random.RandomState(42)
    usable_for_sample = 0

    last_cn = 0
    processed = 0

    pbar = tqdm(
        total=total_target,
        desc="Build paper index",
        unit="paper",
        dynamic_ncols=True,
        mininterval=0.25,
        miniters=500,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
    )

    while True:
        chunk_size = args.fetch_chunk_size
        rows = conn.execute(
            """
            SELECT control_number, title, abstract
            FROM papers
            WHERE control_number > ?
            ORDER BY control_number
            LIMIT ?
            """,
            (last_cn, chunk_size),
        ).fetchall()
        if not rows:
            break

        ids = [int(r[0]) for r in rows]
        last_cn = ids[-1]

        kw_map: dict[int, str] = {}
        for batch_start in range(0, len(ids), 400):
            sub = ids[batch_start : batch_start + 400]
            ph = ",".join("?" * len(sub))
            q = f"""
                SELECT control_number, GROUP_CONCAT(keyword, '; ')
                FROM paper_keywords
                WHERE control_number IN ({ph})
                GROUP BY control_number
            """
            for cn, blob in conn.execute(q, sub):
                kw_map[int(cn)] = blob or ""

        batch_records: list[dict] = []
        for cn, title, abstract in rows:
            cn = int(cn)
            raw_kw = kw_map.get(cn, "")
            kw_joined, kw_list = normalize_keywords_blob(raw_kw)

            has_t = bool(str(title or "").strip())
            has_a = bool(str(abstract or "").strip())
            has_k = len(kw_list) > 0

            canonical = canonical_embedding_text(str(title or ""), kw_joined, str(abstract or ""))
            usable = bool(canonical.strip())

            pattern = classify_availability(has_t, has_a, has_k)
            pattern_counter[pattern] += 1

            if usable:
                tl = len(canonical)
                tw = len(canonical.split())
                stats_chars.append(tl)
                stats_kw.append(len(kw_list))
                th = text_hash_sha256(canonical)
                ridx = row_idx_next
                row_idx_next += 1
                usable_for_sample += 1
            else:
                tl = tw = 0
                th = ""
                ridx = -1

            title_store = (str(title or "")[:2000]) if args.include_title_in_index else ""

            rec = {
                "paper_id": cn,
                "row_idx": np.int64(ridx),
                "title": title_store,
                "has_title": bool(has_t),
                "has_abstract": bool(has_a),
                "has_keywords": bool(has_k),
                "keyword_count": np.int32(len(kw_list)),
                "text_length_chars": np.int32(min(tl, 2_000_000)),
                "text_length_words": np.int32(min(tw, 500_000)),
                "is_usable_text": bool(usable),
                "model_name": "",
                "text_hash": th,
                "embedding_written": False,
                "source_db": str(db_path),
                "text_field_pattern": pattern,
            }
            batch_records.append(rec)

            if usable and args.sample_size > 0:
                snippet = canonical[:1500].replace("\n", " ")
                samp = {
                    "paper_id": cn,
                    "title": str(title or "")[:500],
                    "keyword_count": len(kw_list),
                    "abstract_snippet": str(abstract or "")[:300],
                    "built_text_sample": snippet,
                    "text_length_chars": tl,
                }
                if len(reservoir) < args.sample_size:
                    reservoir.append(samp)
                else:
                    j = rng.randint(0, usable_for_sample - 1)
                    if j < args.sample_size:
                        reservoir[j] = samp

            processed += 1
            if processed % 2000 == 0 or processed == total_target:
                pbar.set_postfix(
                    usable=int(row_idx_next),
                    skip=int(processed - row_idx_next),
                    refresh=False,
                )
            pbar.update(1)
            if args.max_papers and processed >= args.max_papers:
                break

        if batch_records:
            table = pa.Table.from_pylist(batch_records)
            if parquet_writer is None:
                schema = table.schema
                parquet_writer = pq.ParquetWriter(index_path, schema, compression="zstd")
            parquet_writer.write_table(table)

        if args.max_papers and processed >= args.max_papers:
            break

    if parquet_writer:
        parquet_writer.close()

    conn.close()

    usable_n = int(row_idx_next)
    skipped_n = processed - usable_n
    pbar.set_postfix(usable=usable_n, skipped=skipped_n)
    pbar.close()

    stats = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_path": str(db_path),
        "total_papers_seen": processed,
        "usable_papers": usable_n,
        "skipped_papers": skipped_n,
        "text_field_patterns": dict(pattern_counter),
        "average_keyword_count": float(np.mean(stats_kw)) if stats_kw else 0.0,
        "average_text_length_chars": float(np.mean(stats_chars)) if stats_chars else 0.0,
        "median_text_length_chars": float(np.median(stats_chars)) if stats_chars else 0.0,
        "paper_index_path": str(index_path),
        "notes": "full canonical text is rebuilt during embedding; text_hash is SHA-256 hex for usable rows",
    }
    stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")

    if reservoir:
        pd.DataFrame(reservoir).to_parquet(samples_path, index=False, compression="zstd")

    log.info("Wrote index=%s usable=%d skipped=%d", index_path, usable_n, skipped_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
