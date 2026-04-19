#!/usr/bin/env python3
"""
STEP 1: Build 1-hop citation neighborhood around the strong seed set.

Candidate papers = seeds ∪ {papers that cite any seed} ∪ {papers cited by any seed}.

Reads SQLite read-only; writes neighborhood.csv and neighborhood_metadata.json under data/tmd_field_discovery/.
Citation links are used only for candidate generation, not for TMD classification.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

from tmd_discovery_common import (
    batches,
    combined_text,
    connect_readonly_sqlite,
    normalize_cell,
    read_seed_ids,
    repo_root,
    resolve_db_path,
)


# SQLite parameter limit — stay under default max variable number (~999).
SQLITE_VARS = 400


def chunked_distinct_query(
    conn,
    sql_template: str,
    id_lists: list[list[int]],
    desc: str,
) -> set[int]:
    """Run multiple IN-batched queries and union results."""
    out: set[int] = set()
    for chunk in tqdm(id_lists, desc=desc, leave=False):
        if not chunk:
            continue
        placeholders = ",".join("?" * len(chunk))
        sql = sql_template.format(ph=placeholders)
        cur = conn.execute(sql, chunk)
        out.update(int(r[0]) for r in cur if r[0] is not None)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("neighborhood")

    ap = argparse.ArgumentParser(description="Build TMD 1-hop citation neighborhood")
    ap.add_argument("--db", default="inspire_mirror.sqlite", help="SQLite path (read-only)")
    ap.add_argument(
        "--seed-csv",
        default="data/tmd_field_discovery/seed_set_strong.csv",
        help="Strong seed CSV path",
    )
    ap.add_argument("--seed-column", default="recid", help="Column with INSPIRE control_number / recid")
    ap.add_argument(
        "--out-dir",
        default="data/tmd_field_discovery",
        help="Output directory",
    )
    args = ap.parse_args()

    root = repo_root()
    db_path = resolve_db_path(root, args.db)
    seed_csv = (root / args.seed_csv).expanduser().resolve()
    out_dir = (root / args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    neighborhood_csv = out_dir / "neighborhood.csv"
    meta_json = out_dir / "neighborhood_metadata.json"

    seeds = read_seed_ids(seed_csv, args.seed_column)
    seed_set = set(seeds)
    log.info("Loaded %d unique seed control_numbers", len(seed_set))

    uri_note = db_path.as_uri() + "?mode=ro"
    log.info("Connecting read-only: %s", uri_note)

    conn = connect_readonly_sqlite(db_path)

    seed_batches = list(batches(sorted(seed_set), SQLITE_VARS))
    # Papers that cite at least one seed: citing where cited ∈ seeds
    cites_seed_placeholders = "{ph}"
    citing_sql = f"SELECT DISTINCT citing FROM citations WHERE cited IN ({cites_seed_placeholders})"
    papers_citing_seed = chunked_distinct_query(
        conn,
        citing_sql,
        seed_batches,
        "papers citing seed",
    )
    # Papers cited by at least one seed: cited where citing ∈ seeds
    cited_sql = f"SELECT DISTINCT cited FROM citations WHERE citing IN ({cites_seed_placeholders})"
    papers_cited_by_seed = chunked_distinct_query(
        conn,
        cited_sql,
        seed_batches,
        "papers cited by seed",
    )

    candidates = seed_set | papers_citing_seed | papers_cited_by_seed
    log.info(
        "Neighborhood candidates: seeds=%d, cite_seed=%d, cited_by_seed=%d, union=%d",
        len(seed_set),
        len(papers_citing_seed - seed_set),
        len(papers_cited_by_seed - seed_set),
        len(candidates),
    )

    # Link counts for final candidate list only (batched).
    cand_list = sorted(candidates)
    seed_tuple = tuple(sorted(seed_set))
    seed_ph = ",".join("?" * len(seed_tuple))

    num_seed_links_out: dict[int, int] = defaultdict(int)  # paper cites these seeds
    num_seed_links_in: dict[int, int] = defaultdict(int)  # seeds cite paper

    for chunk in tqdm(list(batches(cand_list, SQLITE_VARS)), desc="seed link counts"):
        ch_ph = ",".join("?" * len(chunk))
        q_out = (
            f"SELECT citing, COUNT(DISTINCT cited) FROM citations "
            f"WHERE citing IN ({ch_ph}) AND cited IN ({seed_ph}) GROUP BY citing"
        )
        for citing, cnt in conn.execute(q_out, (*chunk, *seed_tuple)):
            num_seed_links_out[int(citing)] = int(cnt)
        q_in = (
            f"SELECT cited, COUNT(DISTINCT citing) FROM citations "
            f"WHERE cited IN ({ch_ph}) AND citing IN ({seed_ph}) GROUP BY cited"
        )
        for cited, cnt in conn.execute(q_in, (*chunk, *seed_tuple)):
            num_seed_links_in[int(cited)] = int(cnt)

    keywords_by_paper: dict[int, str] = {}
    for chunk in tqdm(list(batches(cand_list, SQLITE_VARS)), desc="paper_keywords"):
        ch_ph = ",".join("?" * len(chunk))
        q_kw = (
            f"SELECT control_number, GROUP_CONCAT(keyword, '; ') "
            f"FROM paper_keywords WHERE control_number IN ({ch_ph}) GROUP BY control_number"
        )
        for cn, kws in conn.execute(q_kw, chunk):
            keywords_by_paper[int(cn)] = kws or ""

    rows_out: list[dict] = []
    num_with_title = num_with_abstract = num_nonempty_text = 0

    for chunk in tqdm(list(batches(cand_list, SQLITE_VARS)), desc="papers rows"):
        ch_ph = ",".join("?" * len(chunk))
        q_p = f"SELECT control_number, title, abstract FROM papers WHERE control_number IN ({ch_ph})"
        by_cn = {}
        for cn, title, abstract in conn.execute(q_p, chunk):
            by_cn[int(cn)] = (normalize_cell(title), normalize_cell(abstract))

        for cn in chunk:
            title, abstract = by_cn.get(cn, ("", ""))
            if cn not in by_cn:
                log.warning("control_number %s not in papers table — empty row", cn)
            text = combined_text(title, abstract)
            has_text = 1 if text.strip() else 0
            if title.strip():
                num_with_title += 1
            if abstract.strip():
                num_with_abstract += 1
            if has_text:
                num_nonempty_text += 1

            is_seed = 1 if cn in seed_set else 0
            in_cites_seed = 1 if cn in papers_citing_seed else 0
            in_cited_by_seed = 1 if cn in papers_cited_by_seed else 0

            rows_out.append(
                {
                    "control_number": cn,
                    "is_seed": is_seed,
                    "in_cites_seed": in_cites_seed,
                    "in_cited_by_seed": in_cited_by_seed,
                    "num_seed_links_in": num_seed_links_in.get(cn, 0),
                    "num_seed_links_out": num_seed_links_out.get(cn, 0),
                    "title": title,
                    "abstract": abstract,
                    "keywords": keywords_by_paper.get(cn, ""),
                    "text": text,
                    "has_text": has_text,
                }
            )

    conn.close()

    fieldnames = [
        "control_number",
        "is_seed",
        "in_cites_seed",
        "in_cited_by_seed",
        "num_seed_links_in",
        "num_seed_links_out",
        "title",
        "abstract",
        "keywords",
        "text",
        "has_text",
    ]
    with open(neighborhood_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows_out:
            w.writerow(row)

    meta = {
        "db_path": str(db_path),
        "seed_csv": str(seed_csv),
        "seed_column": args.seed_column,
        "seed_count": len(seed_set),
        "num_citing_seed": len(papers_citing_seed),
        "num_cited_by_seed": len(papers_cited_by_seed),
        "num_citing_seed_distinct": len(papers_citing_seed),
        "num_cited_by_seed_distinct": len(papers_cited_by_seed),
        "total_unique_neighborhood": len(candidates),
        "num_with_title": num_with_title,
        "num_with_abstract": num_with_abstract,
        "num_with_nonempty_text": num_nonempty_text,
        "notes": (
            "Graph is candidate generation only. "
            "num_citing_seed counts distinct papers that cite at least one seed paper; "
            "num_cited_by_seed counts distinct papers cited by at least one seed paper."
        ),
    }
    meta_json.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    log.info("Wrote %s", neighborhood_csv)
    log.info("Wrote %s", meta_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
