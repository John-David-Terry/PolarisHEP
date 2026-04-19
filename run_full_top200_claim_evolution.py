#!/usr/bin/env python3
"""
Full top-200 claim-evolution pipeline: structured, benchmarked scaling experiment.

Runs the Polaris claim-evolution pipeline on the full processable top-200 set:
  Stage 1: Processability accounting (manifest, TEI, paper_statements, citations)
  Stage 2: Paper-level statement extraction (--all, optional --skip-existing)
  Stage 3: Claim evolution (stress-test style) on all processable papers
  Stage 4: Build claim evolution cards with key follow-up papers
  Stage 5: Benchmark report (A–H)

Fully processable = in manifest + has TEI + has valid paper_statements (with claims) + ≥1 citation in edge_statements.

Usage:
  python run_full_top200_claim_evolution.py                    # run all stages
  python run_full_top200_claim_evolution.py --stage 1         # accounting only
  python run_full_top200_claim_evolution.py --stage 2          # extraction only
  python run_full_top200_claim_evolution.py --stage 3          # claim evolution only
  python run_full_top200_claim_evolution.py --stage 4          # cards only
  python run_full_top200_claim_evolution.py --report-only      # benchmark from existing outputs
  python run_full_top200_claim_evolution.py --skip-existing     # incremental (skip existing extraction)
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


DEFAULT_MANIFEST = "top200_manifest_fixed.csv"
DEFAULT_TEI_DIR = "data/tei/top200"
DEFAULT_PAPER_STATEMENTS_DIR = "data/paper_statements"
DEFAULT_DB = "inspire.sqlite"
DEFAULT_STRESS_OUT = "data/claim_evolution_stress_test"
DEFAULT_CARDS_DIR = "data/claim_evolution_cards"
BENCHMARK_REPORT_PATH = "data/full_top200_benchmark_report.json"


def load_manifest(manifest_path: Path) -> list[dict]:
    if not manifest_path.exists():
        return []
    rows = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                row["cn"] = int(row["cn"])
            except (ValueError, KeyError):
                continue
            rows.append(row)
    return rows


def stage1_processability(
    manifest_path: Path,
    tei_dir: Path,
    paper_statements_dir: Path,
    db_path: Path,
) -> dict:
    """Compute processability counts. Returns dict with all counts and lists."""
    total_in_manifest = 0
    with_tei = []
    with_paper_statements = []
    with_citations = set()
    fully_processable = []

    rows = load_manifest(manifest_path)
    total_in_manifest = len(rows)
    manifest_cns = {r["cn"] for r in rows}

    if tei_dir.exists():
        for f in tei_dir.glob("*.tei.xml"):
            try:
                cn = int(f.stem.replace(".tei", ""))
            except ValueError:
                cn = int(f.stem)
            if cn in manifest_cns:
                with_tei.append(cn)

    if paper_statements_dir.exists():
        for f in paper_statements_dir.glob("*.json"):
            if f.name.startswith("all_"):
                continue
            try:
                cn = int(f.stem)
            except ValueError:
                continue
            if cn not in manifest_cns:
                continue
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
            except Exception:
                continue
            if not data.get("claims") or not data.get("_meta", {}).get("extraction_succeeded"):
                continue
            with_paper_statements.append(cn)

    if db_path.exists():
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        try:
            cur.execute("SELECT DISTINCT parent_cn FROM edge_statements")
            with_citations = {r[0] for r in cur.fetchall()}
        except sqlite3.OperationalError:
            pass
        conn.close()

    with_tei_set = set(with_tei)
    with_ps_set = set(with_paper_statements)
    for cn in manifest_cns:
        if cn in with_tei_set and cn in with_ps_set and cn in with_citations:
            fully_processable.append(cn)
    fully_processable.sort()

    return {
        "total_in_manifest": total_in_manifest,
        "with_tei": len(with_tei),
        "with_paper_statements_valid": len(with_paper_statements),
        "with_citations": len(with_citations & manifest_cns),
        "fully_processable": len(fully_processable),
        "fully_processable_cns": fully_processable,
        "with_tei_cns": sorted(with_tei),
    }


def run_stage1(manifest_path: Path, tei_dir: Path, paper_statements_dir: Path, db_path: Path) -> dict:
    counts = stage1_processability(manifest_path, tei_dir, paper_statements_dir, db_path)
    print("Stage 1: Processability accounting")
    print("=" * 60)
    print(f"  1. Total papers in manifest: {counts['total_in_manifest']}")
    print(f"  2. With TEI in {tei_dir}: {counts['with_tei']}")
    print(f"  3. With valid paper_statements JSON (claims + extraction_succeeded): {counts['with_paper_statements_valid']}")
    print(f"  4. With ≥1 citation in edge_statements (as parent): {counts['with_citations']}")
    print(f"  5. Fully processable (TEI + paper_statements + citations): {counts['fully_processable']}")
    print()
    return counts


def run_stage2(manifest_path: Path, tei_dir: Path, out_dir: Path, db_path: Path, skip_existing: bool) -> None:
    print("Stage 2: Paper-level statement extraction (--all)")
    print("=" * 60)
    cmd = [
        sys.executable,
        "extract_paper_statements.py",
        "--manifest", str(manifest_path),
        "--tei-dir", str(tei_dir),
        "--out-dir", str(out_dir),
        "--all",
    ]
    if skip_existing:
        cmd.append("--skip-existing")
    if db_path.exists():
        cmd.extend(["--db", str(db_path)])
    subprocess.run(cmd, check=True, cwd=Path(__file__).resolve().parent)
    print()


def run_stage3(db_path: Path, paper_statements_dir: Path, out_dir: Path) -> None:
    print("Stage 3: Claim evolution (stress-test style, --all)")
    print("=" * 60)
    subprocess.run([
        sys.executable,
        "stress_test_claim_evolution.py",
        "--db", str(db_path),
        "--paper-statements-dir", str(paper_statements_dir),
        "--out-dir", str(out_dir),
        "--all",
    ], check=True, cwd=Path(__file__).resolve().parent)
    print()


def run_stage4(source_dir: Path, out_dir: Path) -> None:
    print("Stage 4: Build claim evolution cards")
    print("=" * 60)
    subprocess.run([
        sys.executable,
        "build_claim_evolution_cards.py",
        "--source-dir", str(source_dir),
        "--out-dir", str(out_dir),
    ], check=True, cwd=Path(__file__).resolve().parent)
    print()


def run_benchmark_report(
    cards_dir: Path,
    stress_dir: Path,
    report_path: Path,
    processability: dict | None = None,
) -> dict:
    """Generate benchmark report A–H from existing card and stress-test outputs."""
    rel_types = ("uses", "supports", "refines", "limits", "disputes")
    card_files = sorted(cards_dir.glob("*.json")) if cards_dir.exists() else []
    card_files = [f for f in card_files if not f.name.startswith("all_")]

    total_cards = 0
    zero_match = 0
    at_least_one_match = 0
    at_least_one_non_use = 0
    limits_or_disputes = 0
    only_uses_supports = 0
    field_status_dist = {}
    relation_totals = {r: 0 for r in rel_types}
    n_with_key_follow_up = 0
    n_key_refines = n_key_limits = n_key_disputes = 0
    distinct_key_papers = {r: set() for r in rel_types}
    total_claims = 0
    total_citation_statements = 0
    total_aligned_matches = 0

    for p in card_files:
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            continue
        for c in doc.get("claims", []):
            total_cards += 1
            total_claims += 1
            rc = c.get("relation_counts") or {}
            total = sum(rc.get(r, 0) for r in rel_types)
            if total == 0:
                zero_match += 1
            else:
                at_least_one_match += 1
                total_aligned_matches += total
            if (rc.get("refines") or 0) + (rc.get("limits") or 0) + (rc.get("disputes") or 0) >= 1:
                at_least_one_non_use += 1
            if (rc.get("limits") or 0) >= 1 or (rc.get("disputes") or 0) >= 1:
                limits_or_disputes += 1
            if total >= 1 and (rc.get("refines") or 0) == 0 and (rc.get("limits") or 0) == 0 and (rc.get("disputes") or 0) == 0:
                only_uses_supports += 1
            for r in rel_types:
                relation_totals[r] += rc.get(r, 0)
            fs = c.get("field_status", "")
            field_status_dist[fs] = field_status_dist.get(fs, 0) + 1
            kfu = c.get("key_follow_up_papers") or {}
            if any(kfu.get(r) for r in rel_types):
                n_with_key_follow_up += 1
            if kfu.get("refines"):
                n_key_refines += 1
            if kfu.get("limits"):
                n_key_limits += 1
            if kfu.get("disputes"):
                n_key_disputes += 1
            for r in rel_types:
                for paper in (kfu.get(r) or []):
                    cn = paper.get("child_cn")
                    if cn is not None:
                        distinct_key_papers[r].add(cn)

    for p in stress_dir.glob("*.json") if stress_dir.exists() else []:
        if p.name.startswith("all_"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            continue
        total_citation_statements += obj.get("_meta", {}).get("n_citation_statements", 0)

    stress_files = list(stress_dir.glob("*.json")) if stress_dir.exists() else []
    stress_files = [f for f in stress_files if not f.name.startswith("all_")]

    report = {
        "A_coverage": {
            "total_top200_papers_in_manifest": processability.get("total_in_manifest") if processability else None,
            "papers_with_tei": processability.get("with_tei") if processability else None,
            "papers_with_paper_statements_extracted": processability.get("with_paper_statements_valid") if processability else None,
            "papers_with_downstream_citation_statements": processability.get("with_citations") if processability else None,
            "papers_with_full_claim_evolution_output": len(stress_files),
            "papers_with_final_cards": len(card_files),
            "total_claim_evolution_cards": total_cards,
            "total_claims_extracted": total_claims,
            "total_citation_statements_considered": total_citation_statements,
            "total_aligned_claim_response_matches": total_aligned_matches,
        },
        "B_claim_card_coverage": {
            "total_cards": total_cards,
            "cards_zero_downstream_matches": zero_match,
            "cards_at_least_one_match": at_least_one_match,
            "cards_at_least_one_non_use_relation": at_least_one_non_use,
            "cards_limits_or_disputes": limits_or_disputes,
            "cards_only_uses_supports": only_uses_supports,
            "field_status_distribution": field_status_dist,
        },
        "C_relation_distribution": relation_totals,
        "D_key_follow_up": {
            "claims_with_at_least_one_key_follow_up": n_with_key_follow_up,
            "claims_with_key_refines": n_key_refines,
            "claims_with_key_limits": n_key_limits,
            "claims_with_key_disputes": n_key_disputes,
            "distinct_downstream_papers_by_relation": {r: len(distinct_key_papers[r]) for r in rel_types},
        },
        "E_quality_sampling": {
            "strong_cards_note": "Inspect cards with field_status in (refined, contested, adopted_with_limits) and multiple relation types.",
            "weak_cards_note": "Cards with weak_signal or only uses/supports with a single citation.",
        },
        "F_showcase_patterns": {
            "note": "See FULL_TOP200_CLAIM_EVOLUTION.md for: adopted methodology, supported, refined, limited, contested, dominant follow-up examples.",
        },
        "G_failure_modes": {
            "missing_tei": "Papers in manifest without TEI are skipped in extraction.",
            "extraction_failures": "LLM parse errors or empty claims yield JSON without extraction_succeeded.",
            "no_citations": "Papers with no edge_statements as parent get no claim evolution output.",
            "weak_signal_cards": "Claims with zero aligned citations get field_status weak_signal and empty key_follow_up.",
            "relation_label_noise": "Some refines/limits/disputes may be misclassified; follow-up selection inherits noise.",
        },
        "H_overall_evaluation": {
            "pipeline_useful_at_scale": "Yes, when extraction and citation data exist; bottleneck is coverage (TEI + citations).",
            "card_artifact": "Claim evolution card remains the right canonical Polaris artifact.",
            "main_bottleneck": "Coverage: only papers with both valid paper_statements and ≥1 downstream citation get full cards; relation quality and generic citation language are secondary.",
        },
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("Benchmark report (A–D)")
    print("=" * 60)
    print("A. Coverage / pipeline accounting")
    for k, v in report["A_coverage"].items():
        print(f"  {k}: {v}")
    print("B. Claim card coverage")
    for k, v in report["B_claim_card_coverage"].items():
        print(f"  {k}: {v}")
    print("C. Relation distribution at scale")
    for k, v in report["C_relation_distribution"].items():
        print(f"  {k}: {v}")
    print("D. Key follow-up paper coverage")
    for k, v in report["D_key_follow_up"].items():
        print(f"  {k}: {v}")
    print()
    print(f"Full report written to {report_path}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Full top-200 claim-evolution pipeline (benchmarked)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST, help="Top-200 manifest CSV")
    ap.add_argument("--tei-dir", default=DEFAULT_TEI_DIR, help="TEI directory for top200")
    ap.add_argument("--paper-statements-dir", default=DEFAULT_PAPER_STATEMENTS_DIR, help="Paper statements output")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite database")
    ap.add_argument("--stress-out", default=DEFAULT_STRESS_OUT, help="Claim evolution stress-test output")
    ap.add_argument("--cards-dir", default=DEFAULT_CARDS_DIR, help="Claim evolution cards output")
    ap.add_argument("--stage", type=int, default=None, choices=[1, 2, 3, 4], help="Run only this stage (1=accounting, 2=extraction, 3=stress, 4=cards)")
    ap.add_argument("--skip-existing", action="store_true", help="Skip papers that already have valid paper_statements (Stage 2)")
    ap.add_argument("--report-only", action="store_true", help="Only run processability + benchmark report from existing outputs")
    ap.add_argument("--report-path", default=BENCHMARK_REPORT_PATH, help="Path for benchmark JSON report")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    manifest_path = root / args.manifest
    tei_dir = root / args.tei_dir
    paper_statements_dir = root / args.paper_statements_dir
    db_path = root / args.db
    stress_out = root / args.stress_out
    cards_dir = root / args.cards_dir
    report_path = root / args.report_path

    processability = None
    if args.report_only:
        processability = stage1_processability(manifest_path, tei_dir, paper_statements_dir, db_path)
        run_stage1(manifest_path, tei_dir, paper_statements_dir, db_path)
        run_benchmark_report(cards_dir, stress_out, report_path, processability=processability)
        return

    if args.stage in (1, None):
        processability = run_stage1(manifest_path, tei_dir, paper_statements_dir, db_path)
    if args.stage in (2, None):
        run_stage2(manifest_path, tei_dir, paper_statements_dir, db_path, args.skip_existing)
    if args.stage in (3, None):
        if not db_path.exists():
            print("Skipping Stage 3: DB not found.", file=sys.stderr)
        else:
            run_stage3(db_path, paper_statements_dir, stress_out)
    if args.stage in (4, None):
        if stress_out.exists():
            run_stage4(stress_out, cards_dir)
        else:
            print("Skipping Stage 4: stress-test output dir missing.", file=sys.stderr)

    if processability is None:
        processability = stage1_processability(manifest_path, tei_dir, paper_statements_dir, db_path)
    run_benchmark_report(cards_dir, stress_out, report_path, processability=processability)


if __name__ == "__main__":
    main()
