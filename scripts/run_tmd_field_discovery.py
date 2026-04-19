#!/usr/bin/env python3
"""
Orchestrate TMD field discovery: inspect → neighborhood → cluster → select → BGE-M3.

Does not modify the ingest pipeline or the SQLite DB (read-only for all DB access).
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from tmd_discovery_common import repo_root


def run_step(log: logging.Logger, argv: list[str], cwd: Path) -> None:
    log.info("Running: %s", " ".join(argv))
    subprocess.run(argv, check=True, cwd=str(cwd))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("run_tmd")

    ap = argparse.ArgumentParser(description="Run full TMD field discovery pipeline")
    ap.add_argument("--db", default="inspire_mirror.sqlite")
    ap.add_argument("--seed-csv", default="data/tmd_field_discovery/seed_set_strong.csv")
    ap.add_argument("--seed-column", default="recid")
    ap.add_argument("--out-dir", default="data/tmd_field_discovery")
    ap.add_argument("--kmeans-k", type=int, default=50)
    ap.add_argument("--svd-dims", type=int, default=100)
    ap.add_argument("--skip-inspect", action="store_true")
    ap.add_argument("--skip-bgem3", action="store_true")
    ap.add_argument("--bgem3-model", default="BAAI/bge-m3")
    ap.add_argument("--bgem3-device", default=None)
    args = ap.parse_args()

    root = repo_root()
    py = sys.executable
    scripts = root / "scripts"

    common = [
        py,
        str(scripts / "inspect_tmd_inputs.py"),
        "--db",
        args.db,
        "--seed-csv",
        args.seed_csv,
    ]

    if not args.skip_inspect:
        run_step(log, common, root)

    run_step(
        log,
        [
            py,
            str(scripts / "build_tmd_neighborhood.py"),
            "--db",
            args.db,
            "--seed-csv",
            args.seed_csv,
            "--seed-column",
            args.seed_column,
            "--out-dir",
            args.out_dir,
        ],
        root,
    )

    run_step(
        log,
        [
            py,
            str(scripts / "cluster_tmd_neighborhood.py"),
            "--input",
            f"{args.out_dir}/neighborhood.csv",
            "--out-dir",
            args.out_dir,
            "--kmeans-k",
            str(args.kmeans_k),
            "--svd-dims",
            str(args.svd_dims),
        ],
        root,
    )

    run_step(
        log,
        [
            py,
            str(scripts / "select_tmd_clusters.py"),
            "--neighborhood",
            f"{args.out_dir}/neighborhood.csv",
            "--assignments",
            f"{args.out_dir}/cluster_assignments.csv",
            "--cluster-summary",
            f"{args.out_dir}/cluster_summary.json",
            "--seed-csv",
            args.seed_csv,
            "--seed-column",
            args.seed_column,
            "--out-dir",
            args.out_dir,
        ],
        root,
    )

    if not args.skip_bgem3:
        cmd = [
            py,
            str(scripts / "refine_tmd_boundary_bgem3.py"),
            "--core-csv",
            f"{args.out_dir}/core_tmd_papers.csv",
            "--boundary-csv",
            f"{args.out_dir}/boundary_papers.csv",
            "--model",
            args.bgem3_model,
            "--out-dir",
            args.out_dir,
        ]
        if args.bgem3_device:
            cmd.extend(["--device", args.bgem3_device])
        run_step(log, cmd, root)

    log.info("Pipeline finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
