#!/usr/bin/env python3
"""
Polaris: build strong-signal TMD seed set (reduced term list, higher precision).

Reads read-only from papers (+ optional paper_keywords). Writes only under
data/tmd_field_discovery/: seed_set_strong.csv, seed_metadata_strong.json.

Does NOT modify ingest scripts, inspire_manifest_cn, or DB schema.
Does NOT overwrite seed_set.csv / seed_metadata.json (full-recall build).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[misc, assignment]


# -----------------------------------------------------------------------------
# Strong TMD seed terms — fixed list (no additions).
# -----------------------------------------------------------------------------
STRONG_TMD_TERMS: tuple[str, ...] = (
    "transverse momentum dependent",
    "tmd distribution",
    "tmd pdf",
    "tmdpdf",
    "tmd fragmentation function",
    "tmdff",
    "tmd factorization",
    "tmd evolution",
    "collins-soper equation",
    "collins-soper kernel",
    "css evolution",
    "tmd soft function",
    "tmd beam function",
    "tmd matching",
    "tmd formalism",
    "sivers function",
    "collins function",
    "boer-mulders function",
    "transversity tmd",
    "sivers distribution",
    "boer-mulders distribution",
    "collins fragmentation function",
    "polarized tmd",
    "spin-dependent tmd",
    "sivers asymmetry",
    "collins asymmetry",
    "boer-mulders asymmetry",
    "qiu-sterman function",
    "etqs function",
    "gluon tmd",
    "gluon sivers function",
    "small-x tmd",
    "tmd soft factor",
    "tmd jet function",
    "tmd fragmenting jet function",
    "tmd renormalization",
    "tmd process dependence",
    "sivers sign change",
    "non-universality of tmds",
    "gauge link tmd",
    "wilson line tmd",
    "lattice tmd",
    "tmd on the lattice",
    "collins subtraction",
    "scet tmd factorization",
    "tmd global fit",
    "tmd phenomenology",
    "extraction of sivers function",
    "extraction of collins function",
    "extraction of boer-mulders function",
    "tmd parameterization",
    "nonperturbative tmd",
    "tmd fit to sidis",
    "tmd fit to drell-yan",
    "tmd fit to e+e-",
)


def normalize_text(s: str) -> str:
    """Lowercase, unify dashes, collapse whitespace, light phrase normalization."""
    t = (s or "").lower()
    for ch in ("\u2013", "\u2014", "\u2212"):
        t = t.replace(ch, "-")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\bdrell\s+yan\b", "drell-yan", t)
    t = re.sub(r"\bcollins\s+soper\b", "collins-soper", t)
    return t


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def match_terms(text: str, terms: Iterable[str]) -> list[str]:
    hits = [term for term in terms if term in text]
    return sorted(set(hits))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build strong-signal TMD seed set from INSPIRE mirror DB"
    )
    ap.add_argument(
        "--db",
        default="inspire_mirror.sqlite",
        help="Path to SQLite mirror (read-only)",
    )
    ap.add_argument(
        "--out-dir",
        default="data/tmd_field_discovery",
        help="Output directory",
    )
    ap.add_argument(
        "--no-keywords",
        action="store_true",
        help="Ignore paper_keywords even if present",
    )
    ap.add_argument(
        "--chunk-fetch",
        type=int,
        default=5000,
        help="SQLite cursor fetchmany size",
    )
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    db_path = (repo_root / args.db).expanduser().resolve()
    if not db_path.is_file():
        alt = repo_root / "inspire.sqlite"
        if alt.is_file():
            print(
                f"Note: using {alt.name} (read-only); {args.db} not found.",
                file=sys.stderr,
            )
            db_path = alt.resolve()
        else:
            print(f"Error: database file not found: {db_path}", file=sys.stderr)
            return 1

    out_dir = (repo_root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    seed_csv = out_dir / "seed_set_strong.csv"
    meta_json = out_dir / "seed_metadata_strong.json"

    conn_uri = db_path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(conn_uri, uri=True)

    if not table_exists(conn, "papers"):
        print("Error: table 'papers' not found.", file=sys.stderr)
        conn.close()
        return 1

    include_kw = not args.no_keywords
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

    term_counter: Counter[str] = Counter()
    seed_count = 0
    terms_tuple = STRONG_TMD_TERMS

    def rows():
        if include_kw and table_exists(conn, "paper_keywords"):
            sql = """
            SELECT p.control_number,
                   IFNULL(p.title, ''),
                   IFNULL(p.abstract, ''),
                   IFNULL(GROUP_CONCAT(pk.keyword, ' '), '')
            FROM papers p
            LEFT JOIN paper_keywords pk ON pk.control_number = p.control_number
            GROUP BY p.control_number
            """
        else:
            sql = """
            SELECT control_number,
                   IFNULL(title, ''),
                   IFNULL(abstract, '')
            FROM papers
            """
        cur = conn.execute(sql)
        while True:
            batch = cur.fetchmany(args.chunk_fetch)
            if not batch:
                break
            if include_kw and table_exists(conn, "paper_keywords"):
                for recid, title, abstract, kw in batch:
                    yield int(recid), normalize_text(
                        " ".join([title, abstract, kw])
                    )
            else:
                for recid, title, abstract in batch:
                    yield int(recid), normalize_text(" ".join([title, abstract]))

    iterator = rows()
    if tqdm is not None:
        iterator = tqdm(iterator, total=total, unit="paper", desc="Strong seed scan")

    with open(seed_csv, "w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["recid", "matched_terms"])
        for recid, text in iterator:
            matched = match_terms(text, terms_tuple)
            if not matched:
                continue
            seed_count += 1
            for m in matched:
                term_counter[m] += 1
            w.writerow([recid, "|".join(matched)])

    conn.close()

    frac = (seed_count / total) if total else 0.0
    top20 = term_counter.most_common(20)

    prev_meta_path = out_dir / "seed_metadata.json"
    comparison = None
    if prev_meta_path.is_file():
        try:
            prev = json.loads(prev_meta_path.read_text(encoding="utf-8"))
            prev_n = int(prev.get("num_seed_papers", 0))
            comparison = {
                "previous_seed_list": "full_100_term_build",
                "previous_num_seed_papers": prev_n,
                "strong_num_seed_papers": seed_count,
                "reduction_ratio_vs_previous": (
                    round(prev_n / seed_count, 4) if seed_count else None
                ),
                "fraction_previous_retained": (
                    round(seed_count / prev_n, 6) if prev_n else None
                ),
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            comparison = None

    meta = {
        "total_papers_scanned": total,
        "num_seed_papers": seed_count,
        "fraction_seed_papers": round(frac, 8),
        "strong_terms_count": len(STRONG_TMD_TERMS),
        "top_matched_terms": [{"term": t, "count": c} for t, c in top20],
        "db_path": str(db_path),
        "include_keywords": include_kw,
        "output_csv": str(seed_csv.resolve()),
        "comparison_vs_full_seed": comparison,
    }
    with open(meta_json, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")

    print(f"Wrote {seed_csv}")
    print(f"Wrote {meta_json}")
    print(f"total_papers_scanned: {total}")
    print(f"num_seed_papers (strong): {seed_count}")
    print(f"fraction_seed_papers: {frac:.8f}")
    if comparison:
        print(
            "vs full seed_set.csv:",
            comparison["previous_num_seed_papers"],
            "→",
            seed_count,
            f"(~{comparison['reduction_ratio_vs_previous']}× smaller)"
            if comparison.get("reduction_ratio_vs_previous")
            else "",
        )
    print("top_matched_terms (first 10):")
    for t, c in top20[:10]:
        print(f"  {t!r}: {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
