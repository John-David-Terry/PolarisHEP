#!/usr/bin/env python3
"""
STEP 3: Select seed-enriched clusters as first-pass TMD core; define boundary / external.

Uses cluster statistics + seed density only; graph topology is not a label signal.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from tmd_discovery_common import combined_text, read_seed_ids, repo_root

# --- Tunable selection (edit here) -------------------------------------------
MIN_SEED_COUNT_FOR_CORE_CLUSTER = 8
MIN_SEED_FRACTION_FOR_CORE_CLUSTER = 0.012
MIN_CLUSTER_SIZE_FOR_CORE = 40
NUM_BORDERLINE_CLUSTERS_TO_REVIEW = 12
MIN_SEED_COUNT_FOR_BOUNDARY_CLUSTER = 1
# If a paper is not in a core/boundary cluster but title/abstract match these
# phrases, treat as boundary (ambiguous / partial textual evidence).
TMD_TEXT_HINTS = (
    "transverse momentum",
    "tmd",
    "sivers function",
    "collins function",
    "boer-mulders",
    "generalized parton distribution",
)
# -----------------------------------------------------------------------------


def load_cluster_summary(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("select")

    ap = argparse.ArgumentParser(description="Select TMD-like clusters from coarse clustering")
    ap.add_argument("--neighborhood", default="data/tmd_field_discovery/neighborhood.csv")
    ap.add_argument("--assignments", default="data/tmd_field_discovery/cluster_assignments.csv")
    ap.add_argument("--cluster-summary", default="data/tmd_field_discovery/cluster_summary.json")
    ap.add_argument("--seed-csv", default="data/tmd_field_discovery/seed_set_strong.csv")
    ap.add_argument("--seed-column", default="recid")
    ap.add_argument("--out-dir", default="data/tmd_field_discovery")
    args = ap.parse_args()

    root = repo_root()
    nb_path = (root / args.neighborhood).expanduser().resolve()
    asg_path = (root / args.assignments).expanduser().resolve()
    summ_path = (root / args.cluster_summary).expanduser().resolve()
    seed_csv = (root / args.seed_csv).expanduser().resolve()
    out_dir = (root / args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    for p in (nb_path, asg_path, summ_path, seed_csv):
        if not p.is_file():
            raise FileNotFoundError(f"missing required file: {p}")

    seeds = set(read_seed_ids(seed_csv, args.seed_column))

    neighborhood: dict[int, dict] = {}
    with open(nb_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cn = int(row["control_number"])
            neighborhood[cn] = row

    assignment_by_cn: dict[int, dict] = {}
    with open(asg_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cn = int(row["control_number"])
            assignment_by_cn[cn] = row

    summary = load_cluster_summary(summ_path)
    # Rank clusters by seed enrichment
    ranked = sorted(
        summary,
        key=lambda x: (-(x.get("seed_fraction") or 0), -(x.get("seed_count") or 0), -(x.get("size") or 0)),
    )

    core_clusters: set[int] = set()
    for cl in ranked:
        cid = int(cl["cluster_id"])
        sz = int(cl["size"])
        sc = int(cl["seed_count"])
        frac = float(cl["seed_fraction"])
        if sc >= MIN_SEED_COUNT_FOR_CORE_CLUSTER and frac >= MIN_SEED_FRACTION_FOR_CORE_CLUSTER:
            if sz >= MIN_CLUSTER_SIZE_FOR_CORE:
                core_clusters.add(cid)

    remaining_for_boundary = [cl for cl in ranked if int(cl["cluster_id"]) not in core_clusters]
    boundary_clusters: set[int] = set()
    # Next moderately seed-enriched clusters after core: follow global ranking, skip core.
    for cl in remaining_for_boundary:
        cid = int(cl["cluster_id"])
        sc = int(cl["seed_count"])
        if sc < MIN_SEED_COUNT_FOR_BOUNDARY_CLUSTER:
            continue
        boundary_clusters.add(cid)
        if len(boundary_clusters) >= NUM_BORDERLINE_CLUSTERS_TO_REVIEW:
            break

    # Build paper sets
    core_cns: set[int] = set()
    for cn, row in assignment_by_cn.items():
        cid = int(row["cluster_id"])
        if cid in core_clusters:
            core_cns.add(cn)
    core_cns |= seeds

    boundary_cns: set[int] = set()
    for cn, row in assignment_by_cn.items():
        cid = int(row["cluster_id"])
        if cid in boundary_clusters and cn not in core_cns:
            boundary_cns.add(cn)

    # Keyword-assisted boundary for low-seed clusters
    def text_hints_hit(row: dict) -> bool:
        blob = combined_text(row.get("title") or "", row.get("abstract") or "").lower()
        return any(h in blob for h in TMD_TEXT_HINTS)

    for cn, row in neighborhood.items():
        if cn in core_cns or cn in boundary_cns:
            continue
        if cn not in assignment_by_cn:
            if cn in seeds:
                continue
            if text_hints_hit(row):
                boundary_cns.add(cn)
            continue
        cid = int(assignment_by_cn[cn]["cluster_id"])
        if cid in core_clusters or cid in boundary_clusters:
            continue
        if text_hints_hit(row):
            boundary_cns.add(cn)

    all_nb = set(neighborhood.keys())
    external_cns = all_nb - core_cns - boundary_cns

    def row_for_csv(cn: int, split: str, extra: dict | None = None) -> dict:
        nb = neighborhood.get(cn, {})
        r = assignment_by_cn.get(cn, {})
        title = nb.get("title", "")
        abstract = nb.get("abstract", "")
        text = combined_text(title, abstract)
        out = {
            "control_number": cn,
            "cluster_id": r.get("cluster_id", ""),
            "is_seed": 1 if cn in seeds else int(nb.get("is_seed", 0) or 0),
            "title": title,
            "abstract": abstract,
            "text": text,
            "split": split,
        }
        if extra:
            out.update(extra)
        return out

    core_rows = [row_for_csv(cn, "core_tmd") for cn in sorted(core_cns)]
    boundary_rows = [row_for_csv(cn, "boundary") for cn in sorted(boundary_cns)]
    external_rows = [row_for_csv(cn, "external") for cn in sorted(external_cns)]

    candidates = {
        "core_cluster_ids": sorted(core_clusters),
        "boundary_cluster_ids": sorted(boundary_clusters),
        "selection_constants": {
            "MIN_SEED_COUNT_FOR_CORE_CLUSTER": MIN_SEED_COUNT_FOR_CORE_CLUSTER,
            "MIN_SEED_FRACTION_FOR_CORE_CLUSTER": MIN_SEED_FRACTION_FOR_CORE_CLUSTER,
            "MIN_CLUSTER_SIZE_FOR_CORE": MIN_CLUSTER_SIZE_FOR_CORE,
            "NUM_BORDERLINE_CLUSTERS_TO_REVIEW": NUM_BORDERLINE_CLUSTERS_TO_REVIEW,
            "MIN_SEED_COUNT_FOR_BOUNDARY_CLUSTER": MIN_SEED_COUNT_FOR_BOUNDARY_CLUSTER,
            "tmd_text_hints": list(TMD_TEXT_HINTS),
        },
        "top_seed_enriched_clusters_preview": [
            {
                "cluster_id": int(c["cluster_id"]),
                "size": int(c["size"]),
                "seed_count": int(c["seed_count"]),
                "seed_fraction": float(c["seed_fraction"]),
                "top_terms": list(c.get("top_terms") or [])[:12],
            }
            for c in ranked[:15]
        ],
        "counts": {
            "neighborhood_total": len(all_nb),
            "core_papers": len(core_cns),
            "boundary_papers": len(boundary_cns),
            "external_papers": len(external_cns),
        },
    }
    (out_dir / "tmd_cluster_candidates.json").write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")

    default_fields = ["control_number", "cluster_id", "is_seed", "title", "abstract", "text", "split"]

    def write_csv(name: str, rows: list[dict]) -> None:
        p = out_dir / name
        fields = list(rows[0].keys()) if rows else default_fields
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow(r)

    write_csv("core_tmd_papers.csv", core_rows)
    write_csv("boundary_papers.csv", boundary_rows)
    write_csv("external_papers.csv", external_rows)

    meta_nb = {}
    meta_path = out_dir / "neighborhood_metadata.json"
    if meta_path.is_file():
        meta_nb = json.loads(meta_path.read_text(encoding="utf-8"))

    cluster_run = {}
    cr_path = out_dir / "cluster_run_metadata.json"
    if cr_path.is_file():
        cluster_run = json.loads(cr_path.read_text(encoding="utf-8"))

    report_lines = [
        "# TMD field discovery — first-pass report",
        "",
        "This is a **first-pass** field map from text clustering; it is not final truth. "
        "The citation graph only proposed candidates; cluster structure comes from TF–IDF on title+abstract. ",
        "",
        "## Neighborhood",
        "",
        f"- Total unique papers in 1-hop neighborhood: **{len(all_nb)}**",
        f"- Seed count (strong CSV): **{len(seeds)}**",
    ]
    if meta_nb:
        report_lines.append(f"- From build metadata — papers with nonempty text: **{meta_nb.get('num_with_nonempty_text', 'n/a')}**")
    report_lines.extend(
        [
            "",
            "## Clustering (coarse)",
            "",
            f"- Papers clustered (with usable text): **{cluster_run.get('n_samples_clustered', len(assignment_by_cn))}** "
            f"(see `cluster_run_metadata.json`).",
            "",
            "## Top seed-enriched clusters (ranked)",
            "",
        ]
    )
    for c in ranked[:10]:
        tt = ", ".join((c.get("top_terms") or [])[:10])
        report_lines.append(
            f"- **cluster {int(c['cluster_id'])}** — size={int(c['size'])}, "
            f"seeds={int(c['seed_count'])}, seed_fraction={float(c['seed_fraction']):.4f} — terms: *{tt}*"
        )
    report_lines.extend(
        [
            "",
            "## Core cluster selection",
            "",
            "Clusters selected as **core TMD** satisfied all of:",
            "",
            f"- `seed_count >= {MIN_SEED_COUNT_FOR_CORE_CLUSTER}`",
            f"- `seed_fraction >= {MIN_SEED_FRACTION_FOR_CORE_CLUSTER}`",
            f"- `size >= {MIN_CLUSTER_SIZE_FOR_CORE}`",
            "",
            f"Selected core cluster ids: `{sorted(core_clusters)}`",
            "",
            "The **core paper set** is the union of all papers in those clusters **and** all strong-seed papers (even if a seed lacked usable text).",
            "",
            "## Boundary / external",
            "",
            f"- **Boundary** clusters: `{sorted(boundary_clusters)}` — highest-ranked non-core clusters that still contain at least "
            f"`{MIN_SEED_COUNT_FOR_BOUNDARY_CLUSTER}` seed paper(s), capped at ~{NUM_BORDERLINE_CLUSTERS_TO_REVIEW}. "
            "Also, papers outside those clusters may be pulled into boundary when title/abstract match explicit TMD keyword hints (ambiguous textual evidence only).",
            "",
            "- **External**: all other neighborhood papers.",
            "",
            "## Output sizes",
            "",
        ]
    )
    report_lines.extend(
        [
            f"| split | count |",
            f"|------|------:|",
            f"| core_tmd | {len(core_cns)} |",
            f"| boundary | {len(boundary_cns)} |",
            f"| external | {len(external_cns)} |",
            "",
            "## BGE-M3",
            "",
            "Boundary refinement with BGE-M3 runs only on **core ∪ boundary** (see `refine_tmd_boundary_bgem3.py`). ",
        ]
    )

    (out_dir / "tmd_field_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    log.info("Core clusters: %s", sorted(core_clusters))
    log.info("Boundary clusters: %s", sorted(boundary_clusters))
    log.info("Counts core=%d boundary=%d external=%d", len(core_cns), len(boundary_cns), len(external_cns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
