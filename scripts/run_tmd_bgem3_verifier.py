#!/usr/bin/env python3
"""CLI driver for build_tmd_bgem3_verifier.py (path-safe, cwd = repo root)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Run BGE-M3 TMD verifier / refinement layer")
    parser.add_argument("--model-name", default="BAAI/bge-m3")
    parser.add_argument("--seed-file", default="data/tmd_field_discovery/seed_set_strong.csv")
    parser.add_argument("--boundary-file", default="data/tmd_field_discovery/boundary_papers.csv")
    parser.add_argument("--external-file", default="data/tmd_field_discovery/external_papers.csv")
    parser.add_argument("--core-file", default="data/tmd_field_discovery/core_tmd_papers.csv")
    parser.add_argument("--cluster-assignments", default="data/tmd_field_discovery/cluster_assignments.csv")
    parser.add_argument("--cluster-summary", default="data/tmd_field_discovery/cluster_summary.json")
    parser.add_argument("--cluster-candidates", default="data/tmd_field_discovery/tmd_cluster_candidates.json")
    parser.add_argument("--neighborhood", default="data/tmd_field_discovery/neighborhood.csv")
    parser.add_argument("--out-dir", default="data/tmd_field_discovery")
    parser.add_argument("--upper-threshold", default="auto")
    parser.add_argument("--lower-threshold", default="auto")
    parser.add_argument("--validation-frac", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default=None)
    parser.add_argument("--max-hardneg-seed-fraction", type=float, default=0.02)
    parser.add_argument("--min-hardneg-cluster-size", type=int, default=40)
    parser.add_argument("--easy-negative-sample-size", type=int, default=150)
    parser.add_argument("--exclude-clusters", default="")
    parser.add_argument("--include-suspicious-core", action="store_true")
    parser.add_argument("--suspicious-core-clusters", default="32")
    parser.add_argument("--extra-tmd-markers", default="")
    parser.add_argument("--no-exclude-junk-clusters-by-terms", action="store_true")
    args, passthrough = parser.parse_known_args()

    script = root / "scripts" / "build_tmd_bgem3_verifier.py"
    cmd = [
        sys.executable,
        str(script),
        "--model-name",
        args.model_name,
        "--seed-file",
        args.seed_file,
        "--boundary-file",
        args.boundary_file,
        "--external-file",
        args.external_file,
        "--core-file",
        args.core_file,
        "--cluster-assignments",
        args.cluster_assignments,
        "--cluster-summary",
        args.cluster_summary,
        "--cluster-candidates",
        args.cluster_candidates,
        "--neighborhood",
        args.neighborhood,
        "--out-dir",
        args.out_dir,
        "--upper-threshold",
        str(args.upper_threshold),
        "--lower-threshold",
        str(args.lower_threshold),
        "--validation-frac",
        str(args.validation_frac),
        "--random-state",
        str(args.random_state),
        "--batch-size",
        str(args.batch_size),
        "--max-hardneg-seed-fraction",
        str(args.max_hardneg_seed_fraction),
        "--min-hardneg-cluster-size",
        str(args.min_hardneg_cluster_size),
        "--easy-negative-sample-size",
        str(args.easy_negative_sample_size),
        "--exclude-clusters",
        args.exclude_clusters,
        "--suspicious-core-clusters",
        args.suspicious_core_clusters,
        "--extra-tmd-markers",
        args.extra_tmd_markers,
    ]
    if args.device:
        cmd.extend(["--device", args.device])
    if args.include_suspicious_core:
        cmd.append("--include-suspicious-core")
    if args.no_exclude_junk_clusters_by_terms:
        cmd.append("--no-exclude-junk-clusters-by-terms")
    cmd.extend(passthrough)

    return subprocess.run(cmd, cwd=str(root), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
