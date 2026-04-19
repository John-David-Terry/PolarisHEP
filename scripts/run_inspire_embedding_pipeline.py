#!/usr/bin/env python3
"""Orchestrate INSPIRE corpus embedding: inspect → build texts → embed → optional Faiss."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path


def run_step(args: list[str], cwd: Path, log: logging.Logger) -> int:
    log.info("Running: %s", " ".join(args))
    return subprocess.run(args, cwd=str(cwd), check=False).returncode


def banner(log: logging.Logger, title: str) -> None:
    log.info("")
    log.info("========== %s ==========", title)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("pipeline")

    ap = argparse.ArgumentParser(description="Full INSPIRE embedding pipeline")
    ap.add_argument("--db", default="inspire_mirror.sqlite")
    ap.add_argument("--output-root", default="outputs_no_sync/inspire_embeddings")
    ap.add_argument("--model-name", default="BAAI/bge-m3")
    ap.add_argument("--device", default=None)
    ap.add_argument("--fetch-chunk-size", type=int, default=10000)
    ap.add_argument("--embed-batch-size", type=int, default=64)
    ap.add_argument("--num-workers", type=int, default=3)
    ap.add_argument("--dtype-storage", choices=("float16", "float32"), default="float16")
    ap.add_argument("--normalize-embeddings", action="store_true", default=True)
    ap.add_argument("--no-normalize-embeddings", action="store_false", dest="normalize_embeddings")
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--no-resume", action="store_false", dest="resume")
    ap.add_argument("--force-rebuild", action="store_true")
    ap.add_argument("--build-faiss", action="store_true")
    ap.add_argument("--skip-inspect", action="store_true")
    ap.add_argument("--skip-build-texts", action="store_true")
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--max-papers", type=int, default=0, help="Passed to build_inspire_embedding_texts (pilot cap)")
    ap.add_argument("--max-rows", type=int, default=0, help="Passed to embed_inspire_corpus (embed cap this run)")
    args, passthrough = ap.parse_known_args()

    root = Path(__file__).resolve().parent.parent
    py = sys.executable
    scripts = root / "scripts"

    common_out = ["--output-root", args.output_root]
    t_pipeline0 = time.perf_counter()

    if not args.skip_inspect:
        banner(log, "STAGE: inspect DB schema")
        t0 = time.perf_counter()
        rc = run_step(
            [py, str(scripts / "inspect_inspire_embedding_inputs.py"), "--db", args.db] + common_out,
            root,
            log,
        )
        log.info("Stage inspect finished in %.1fs", time.perf_counter() - t0)
        if rc != 0:
            return rc

    if not args.skip_build_texts:
        banner(log, "STAGE: build paper_index + text metadata")
        t0 = time.perf_counter()
        cmd = [
            py,
            str(scripts / "build_inspire_embedding_texts.py"),
            "--db",
            args.db,
            "--fetch-chunk-size",
            str(args.fetch_chunk_size),
        ] + common_out
        if args.max_papers:
            cmd.extend(["--max-papers", str(args.max_papers)])
        cmd.extend(passthrough)
        rc = run_step(cmd, root, log)
        log.info("Stage build texts finished in %.1fs", time.perf_counter() - t0)
        if rc != 0:
            return rc

    if not args.skip_embed:
        banner(log, "STAGE: embed corpus (memmap)")
        t0 = time.perf_counter()
        cmd = [
            py,
            str(scripts / "embed_inspire_corpus.py"),
            "--db",
            args.db,
            "--model-name",
            args.model_name,
            "--embed-batch-size",
            str(args.embed_batch_size),
            "--num-workers",
            str(args.num_workers),
            "--dtype-storage",
            args.dtype_storage,
        ]
        if args.device:
            cmd.extend(["--device", args.device])
        if args.normalize_embeddings:
            cmd.append("--normalize-embeddings")
        else:
            cmd.append("--no-normalize-embeddings")
        if args.resume:
            cmd.append("--resume")
        else:
            cmd.append("--no-resume")
        if args.force_rebuild:
            cmd.append("--force-rebuild")
        if args.max_rows:
            cmd.extend(["--max-rows", str(args.max_rows)])
        cmd.extend(common_out)
        cmd.extend(passthrough)
        rc = run_step(cmd, root, log)
        log.info("Stage embed finished in %.1fs", time.perf_counter() - t0)
        if rc != 0:
            return rc

    if args.build_faiss:
        banner(log, "STAGE: Faiss index")
        t0 = time.perf_counter()
        rc = run_step([py, str(scripts / "build_inspire_faiss_index.py")] + common_out, root, log)
        log.info("Stage Faiss finished in %.1fs", time.perf_counter() - t0)
        if rc != 0:
            return rc

    log.info("")
    log.info("All requested stages OK. Total pipeline wall time: %.1fs", time.perf_counter() - t_pipeline0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
