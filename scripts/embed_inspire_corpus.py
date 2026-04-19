#!/usr/bin/env python3
"""
STEP 3: Embed usable papers into a single float16 memmap; resumable checkpointed run.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from inspire_embedding_common import (
    canonical_embedding_text,
    connect_readonly_sqlite,
    load_sentence_transformer_model,
    normalize_keywords_blob,
    repo_root,
    resolve_db_path,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_progress(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_progress(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_log(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def append_chunk_record(chunk_log_path: Path, record: dict) -> None:
    chunk_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(chunk_log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def fetch_keywords_for_ids(conn, ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for i in range(0, len(ids), 400):
        sub = ids[i : i + 400]
        ph = ",".join("?" * len(sub))
        q = f"""
            SELECT control_number, GROUP_CONCAT(keyword, '; ')
            FROM paper_keywords
            WHERE control_number IN ({ph})
            GROUP BY control_number
        """
        for cn, blob in conn.execute(q, sub):
            out[int(cn)] = blob or ""
    return out


def fetch_papers_for_ids(conn, ids: list[int]) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for i in range(0, len(ids), 400):
        sub = ids[i : i + 400]
        ph = ",".join("?" * len(sub))
        q = f"SELECT control_number, title, abstract FROM papers WHERE control_number IN ({ph})"
        for cn, title, abstract in conn.execute(q, sub):
            out[int(cn)] = (str(title or ""), str(abstract or ""))
    return out


def build_texts_batch(conn, ids: list[str] | list[int]) -> list[str]:
    ids_i = [int(x) for x in ids]
    kw = fetch_keywords_for_ids(conn, ids_i)
    pm = fetch_papers_for_ids(conn, ids_i)
    texts: list[str] = []
    for cn in ids_i:
        title, abstract = pm.get(cn, ("", ""))
        raw_kw = kw.get(cn, "")
        kw_joined, _ = normalize_keywords_blob(raw_kw)
        texts.append(canonical_embedding_text(title, kw_joined, abstract))
    return texts


def build_texts_batch_threadsafe(db_path: Path, ids: list[int]) -> list[str]:
    """Open a dedicated read-only connection per call (safe across threads)."""
    conn = connect_readonly_sqlite(db_path)
    try:
        return build_texts_batch(conn, ids)
    finally:
        conn.close()


def git_sha_optional(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def finalize_paper_index_model(
    index_path: Path,
    model_name: str,
) -> None:
    """Mark usable rows as embedded (single rewrite at end of successful run)."""
    df = pd.read_parquet(index_path)
    df.loc[df["is_usable_text"] == True, "embedding_written"] = True  # noqa: E712
    df.loc[df["is_usable_text"] == True, "model_name"] = model_name  # noqa: E712
    df.to_parquet(index_path, index=False, compression="zstd")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("embed")

    ap = argparse.ArgumentParser(description="Embed INSPIRE corpus into float16 memmap")
    ap.add_argument("--db", default="inspire_mirror.sqlite")
    ap.add_argument("--output-root", default="outputs_no_sync/inspire_embeddings")
    ap.add_argument("--paper-index", default="", help="Override paper_index.parquet path")
    ap.add_argument("--model-name", default="BAAI/bge-m3")
    ap.add_argument("--device", default=None)
    ap.add_argument("--fetch-chunk-size", type=int, default=10000, help="DB ids per text-build batch")
    ap.add_argument("--embed-batch-size", type=int, default=64)
    ap.add_argument("--num-workers", type=int, default=3, help="Threads for prefetching next text batch")
    ap.add_argument("--normalize-embeddings", action="store_true")
    ap.add_argument("--no-normalize-embeddings", action="store_false", dest="normalize_embeddings")
    ap.set_defaults(normalize_embeddings=True)
    ap.add_argument("--dtype-storage", choices=("float16", "float32"), default="float16")
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--no-resume", action="store_false", dest="resume")
    ap.add_argument("--force-rebuild", action="store_true")
    ap.add_argument("--max-rows", type=int, default=0, help="Stop after this many embedded rows (smoke test)")
    args = ap.parse_args()

    root = repo_root()
    db_path = resolve_db_path(root, args.db)
    out_root = (root / args.output_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    logs_dir = out_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "embedding_run.log"

    index_path = Path(args.paper_index) if args.paper_index else out_root / "paper_index.parquet"
    if not index_path.is_file():
        raise FileNotFoundError(f"paper_index.parquet not found: {index_path}")

    memmap_path = out_root / "embeddings.f16.memmap"
    if args.dtype_storage == "float32":
        memmap_path = out_root / "embeddings.f32.memmap"

    progress_path = out_root / "embedding_progress.json"
    manifest_path = out_root / "embedding_manifest.json"

    chunk_log_path = out_root / "completed_chunks.jsonl"

    if args.force_rebuild:
        for p in (memmap_path, progress_path, manifest_path, chunk_log_path):
            if p.is_file():
                p.unlink()
                log.info("Removed %s", p)

    df = pd.read_parquet(index_path)
    if "is_usable_text" not in df.columns:
        raise ValueError("paper_index missing is_usable_text")
    usable = df[df["is_usable_text"]].sort_values("row_idx")
    if usable.empty:
        raise RuntimeError("No usable papers in paper_index")

    if not np.array_equal(usable["row_idx"].to_numpy(), np.arange(len(usable), dtype=np.int64)):
        raise ValueError("row_idx must be contiguous 0..N-1 for usable rows")

    paper_ids = usable["paper_id"].astype(np.int64).tolist()
    n_usable = len(paper_ids)

    log.info("Loading model %s", args.model_name)
    model = load_sentence_transformer_model(args.model_name, args.device)
    dim = model.get_sentence_embedding_dimension()

    dtype_np = np.float16 if args.dtype_storage == "float16" else np.float32

    if memmap_path.is_file() and args.resume and not args.force_rebuild:
        mm = np.memmap(memmap_path, dtype=dtype_np, mode="r+")
        if mm.shape != (n_usable, dim):
            raise ValueError(
                f"memmap shape {mm.shape} != expected ({n_usable}, {dim}); use --force-rebuild"
            )
        log.info("Opened existing memmap %s", memmap_path)
    else:
        if progress_path.is_file() and not args.force_rebuild:
            log.warning("Replacing memmap while progress exists — use --force-rebuild for clean resume")
        mm = np.memmap(memmap_path, dtype=dtype_np, mode="w+", shape=(n_usable, dim))
        log.info("Created memmap %s shape=%s dtype=%s", memmap_path, mm.shape, dtype_np)

    prog = read_progress(progress_path) if args.resume and not args.force_rebuild else {}
    start_row = int(prog.get("last_completed_row_idx", -1)) + 1
    if prog.get("embedding_dim") and int(prog["embedding_dim"]) != dim:
        raise ValueError("embedding_dim mismatch vs checkpoint; use --force-rebuild")
    if start_row >= n_usable:
        log.info("Nothing to do — memmap already complete through row %s", n_usable - 1)
        write_manifest(
            manifest_path,
            db_path,
            args.model_name,
            dim,
            args.dtype_storage,
            args.normalize_embeddings,
            df,
            n_usable,
            memmap_path,
            index_path,
            root,
            run_status="completed",
        )
        return 0

    end_row_exclusive = n_usable
    if args.max_rows:
        end_row_exclusive = min(n_usable, start_row + args.max_rows)

    progress_state = {
        "last_completed_row_idx": start_row - 1,
        "rows_written_successfully": max(0, start_row),
        "rows_attempted_cumulative": 0,
        "number_skipped": int(prog.get("number_skipped", 0)),
        "completed_batches": int(prog.get("completed_batches", 0)),
        "model_name": args.model_name,
        "embedding_dim": dim,
        "embedding_dtype": np.dtype(dtype_np).name,
        "normalize_embeddings": bool(args.normalize_embeddings),
        "started_at_utc": prog.get("started_at_utc") or utc_now_iso(),
        "status": "in_progress",
        "total_usable_rows": n_usable,
        "embedding_end_row_exclusive": end_row_exclusive,
        "resume_start_row": start_row,
    }
    write_progress(progress_path, progress_state)
    write_manifest(
        manifest_path,
        db_path,
        args.model_name,
        dim,
        args.dtype_storage,
        args.normalize_embeddings,
        df,
        n_usable,
        memmap_path,
        index_path,
        root,
        run_status="in_progress",
    )

    t0 = time.perf_counter()
    batches_done = progress_state["completed_batches"]

    prefetch_pool = None
    pbar = None
    prefetch_pool = ThreadPoolExecutor(max_workers=max(1, args.num_workers))

    def schedule_text_build(ids_slice: list[int]) -> Future:
        return prefetch_pool.submit(build_texts_batch_threadsafe, db_path, ids_slice)

    pending = None
    idx = start_row

    pbar = tqdm(
        total=end_row_exclusive,
        initial=start_row,
        desc="Embed corpus",
        unit="row",
        dynamic_ncols=True,
        mininterval=0.5,
        smoothing=0.08,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {postfix}",
    )

    def _postfix() -> dict:
        elapsed = time.perf_counter() - t0
        thr = (idx - start_row) / max(elapsed, 1e-6) if idx > start_row else 0.0
        return {
            "emb": idx,
            "skip": progress_state["number_skipped"],
            "r/s": f"{thr:.2f}",
        }

    pbar.set_postfix(**_postfix())

    try:
        while idx < end_row_exclusive:
            batch_limit = args.embed_batch_size
            remaining = end_row_exclusive - idx
            batch_limit = min(batch_limit, remaining)

            batch_start_idx = idx
            end = min(idx + batch_limit, end_row_exclusive)
            ids_batch = paper_ids[idx:end]

            if pending is None:
                pending = schedule_text_build(ids_batch)

            texts = pending.result()
            pending = None

            next_remaining = end_row_exclusive - end
            next_sz = min(args.embed_batch_size, next_remaining) if next_remaining > 0 else 0
            next_end = min(end + next_sz, end_row_exclusive)
            if next_sz > 0 and next_end > end:
                ids_next = paper_ids[end:next_end]
                pending = schedule_text_build(ids_next)

            progress_state["rows_attempted_cumulative"] = progress_state.get("rows_attempted_cumulative", 0) + len(
                ids_batch
            )

            emb = model.encode(
                texts,
                batch_size=min(args.embed_batch_size, len(texts)),
                normalize_embeddings=args.normalize_embeddings,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            emb = np.asarray(emb, dtype=np.float32)
            if emb.shape != (len(ids_batch), dim):
                raise RuntimeError(f"embedding shape mismatch {emb.shape} vs ids {len(ids_batch)}")

            mm[idx:end] = emb.astype(dtype_np)
            mm.flush()

            batches_done += 1
            elapsed = time.perf_counter() - t0
            thr = (end - start_row) / max(elapsed, 1e-6)

            progress_state.update(
                {
                    "last_completed_row_idx": end - 1,
                    "rows_written_successfully": end,
                    "completed_batches": batches_done,
                    "updated_at_utc": utc_now_iso(),
                    "throughput_rows_per_sec_est": round(thr, 4),
                    "status": "in_progress",
                    "current_batch_index": batches_done,
                }
            )
            write_progress(progress_path, progress_state)

            append_chunk_record(
                chunk_log_path,
                {
                    "chunk_start_row": batch_start_idx,
                    "chunk_end_row_exclusive": end,
                    "embedded_count": len(ids_batch),
                    "skipped_count": 0,
                    "batch_index": batches_done,
                    "timestamp_utc": utc_now_iso(),
                    "throughput_rows_per_sec": round(thr, 4),
                },
            )

            append_log(
                log_file,
                f"{utc_now_iso()} rows {batch_start_idx}-{end - 1} embedded batch={batches_done} thr={thr:.2f} rows/s",
            )

            idx = end
            # Single refresh: avoid tqdm duplicate lines (update + stale postfix).
            pbar.update(end - batch_start_idx, refresh=False)
            pbar.set_postfix(**_postfix())

        if idx >= n_usable:
            progress_state["status"] = "completed"
            progress_state["completed_at_utc"] = utc_now_iso()
            progress_state["last_completed_row_idx"] = n_usable - 1
            progress_state["rows_written_successfully"] = n_usable
            write_progress(progress_path, progress_state)
        elif end_row_exclusive < n_usable:
            progress_state["status"] = "partial_max_rows"
            progress_state["completed_at_utc"] = utc_now_iso()
            progress_state["last_completed_row_idx"] = idx - 1
            progress_state["rows_written_successfully"] = idx
            write_progress(progress_path, progress_state)
            log.info("Stopped early (row cap); next resume from row %s", idx)
        else:
            progress_state.setdefault("status", "interrupted")
            progress_state["completed_at_utc"] = utc_now_iso()
            write_progress(progress_path, progress_state)

    except Exception as exc:
        progress_state["status"] = "failed"
        progress_state["error"] = repr(exc)
        progress_state["completed_at_utc"] = utc_now_iso()
        write_progress(progress_path, progress_state)
        write_manifest(
            manifest_path,
            db_path,
            args.model_name,
            dim,
            args.dtype_storage,
            args.normalize_embeddings,
            df,
            n_usable,
            memmap_path,
            index_path,
            root,
            run_status="failed",
        )
        log.exception("Embedding failed")
        raise

    finally:
        if pbar is not None:
            pbar.close()
        if prefetch_pool is not None:
            prefetch_pool.shutdown(wait=True)

    final_status = progress_state.get("status", "interrupted")
    write_manifest(
        manifest_path,
        db_path,
        args.model_name,
        dim,
        args.dtype_storage,
        args.normalize_embeddings,
        df,
        n_usable,
        memmap_path,
        index_path,
        root,
        run_status={
            "completed": "completed",
            "partial_max_rows": "partial",
            "interrupted": "interrupted",
            "failed": "failed",
            "in_progress": "in_progress",
        }.get(final_status, "interrupted"),
    )

    if progress_state.get("status") == "completed":
        finalize_paper_index_model(index_path, args.model_name)
        log.info("Finished embedding. manifest=%s", manifest_path)
    else:
        log.warning("Run ended without full completion — skipped finalize of embedding_written flags in parquet")
    return 0


def write_manifest(
    manifest_path: Path,
    db_path: Path,
    model_name: str,
    dim: int,
    dtype_storage: str,
    normalized: bool,
    df: pd.DataFrame,
    n_usable: int,
    memmap_path: Path,
    index_path: Path,
    root: Path,
    run_status: str = "initialized",
) -> None:
    total = len(df)
    skipped = total - n_usable
    m_bytes = memmap_path.stat().st_size if memmap_path.is_file() else 0
    manifest = {
        "corpus_source_db_path": str(db_path),
        "model_name": model_name,
        "embedding_dim": dim,
        "storage_dtype": dtype_storage,
        "normalized_embeddings": bool(normalized),
        "total_papers_indexed": total,
        "usable_papers": n_usable,
        "skipped_papers": int(skipped),
        "memmap_path": str(memmap_path),
        "memmap_file_bytes": m_bytes,
        "paper_index_path": str(index_path),
        "progress_path": str(manifest_path.with_name("embedding_progress.json")),
        "chunk_log_path": str(manifest_path.with_name("completed_chunks.jsonl")),
        "run_status": run_status,
        "updated_at_utc": utc_now_iso(),
        "git_sha": git_sha_optional(root),
        "python": sys.version.split()[0],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
