#!/usr/bin/env python3
"""
Polaris Stage 1: build high-recall TMD seed set from INSPIRE mirror SQLite.

Reads read-only from papers (+ optional paper_keywords). Writes only under
data/tmd_field_discovery/ unless overridden.

Does NOT modify ingest scripts, inspire_manifest_cn, or DB schema.
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
# 100-term TMD / transverse-spin / SIDIS qualifier list (lowercase phrases).
# High recall — expect false positives downstream. One phrase per line; no blanks.
# -----------------------------------------------------------------------------
_TMD_LINES = """
tmd pdf
tmd fragmentation
tmd evolution
tmd factorization
tmd soft function
transverse momentum dependent
transverse-momentum-dependent
transverse momentum distribution
transverse spin asymmetry
transverse single spin
transverse single-spin
intrinsic transverse momentum
semi-inclusive deep inelastic scattering
sidis
semi-inclusive production
generalized transverse momentum
generalized parton distribution
generalized gluon distribution
gtmd
parton distribution in transverse momentum
single transverse-spin asymmetry
single-transverse-spin asymmetry
single spin asymmetry
azimuthal asymmetry
weighted cross section
weighted cross-section
collins-soper-sterman
collins soper sterman
collins-soper formalism
collins-soper scale
collins-soper frame
collins fragmentation function
collins effect
boer-mulders function
boer mulders
boer-mulders
worm-gear
pretzelosity
transverse helicity distribution
transversity distribution
transversity
sivers function
sivers
q_t spectrum
small q_t resummation
small-q resummation
small qt resummation
low q_t
low transverse momentum
transverse momentum resummation
transverse momentum broadening
transverse momentum spectrum
transverse momentum weighting
transverse momentum dependent pdf
kt factorization
kt-factorization
k_t factorization
k ⊥ factorization
non-collinear factorization
noncollinear factorization
transverse momentum factorization
unintegrated gluon distribution
unintegrated quark distribution
small-x resummation
css formalism
soft collinear effective theory
soft gluon resummation
sudakov resummation
sudakov factor
sudakov suppression
rapidity anomalous dimension
rapidity renormalization group
soft factorization theorem
evolution kernel
tmd kernel
impact parameter dependent
impact parameter distribution
twist-three
twist-3 distribution
twist three
large-b prescription
small-b expansion
wilson line
gauge link
staple wilson line
fundamental wilson line
light-cone gauge
light cone gauge
subtracted cross section
unsubtracted cross section
soft overlap subtraction
hadronic tensor
polarized fragmentation function
tensor charge
soffer bound
drell-yan
drell yan
three-dimensional structure
three dimensional structure
partonic transverse momentum
"""

TMD_TERMS = tuple(
    ln.strip()
    for ln in _TMD_LINES.strip().splitlines()
    if ln.strip()
)
if len(TMD_TERMS) != 100:
    raise RuntimeError(
        f"TMD_TERMS must contain exactly 100 entries; got {len(TMD_TERMS)}"
    )


def normalize_text(s: str) -> str:
    """Lowercase, unify dashes, collapse whitespace, light phrase normalization."""
    t = (s or "").lower()
    for ch in ("\u2013", "\u2014", "\u2212"):
        t = t.replace(ch, "-")
    t = re.sub(r"\s+", " ", t).strip()
    # Optional canonical phrases (simple substring-friendly forms)
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
    """Return sorted unique terms that appear as substrings in text."""
    hits = [term for term in terms if term in text]
    return sorted(set(hits))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build TMD seed set from INSPIRE mirror DB")
    ap.add_argument(
        "--db",
        default="inspire_mirror.sqlite",
        help="Path to SQLite mirror (read-only)",
    )
    ap.add_argument(
        "--out-dir",
        default="data/tmd_field_discovery",
        help="Output directory (seed_set.csv, seed_metadata.json)",
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

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        print(f"Error: database file not found: {db_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seed_csv = out_dir / "seed_set.csv"
    meta_json = out_dir / "seed_metadata.json"

    conn_uri = db_path.expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(conn_uri, uri=True)
    conn.row_factory = sqlite3.Row

    if not table_exists(conn, "papers"):
        print("Error: table 'papers' not found.", file=sys.stderr)
        conn.close()
        return 1

    include_kw = not args.no_keywords

    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

    term_counter: Counter[str] = Counter()
    seed_count = 0
    terms_tuple = tuple(TMD_TERMS)

    # Re-implement iterator with configurable fetch size
    def rows() -> Iterable[tuple[int, str]]:
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
        iterator = tqdm(iterator, total=total, unit="paper", desc="Scanning")

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

    meta = {
        "total_papers_scanned": total,
        "num_seed_papers": seed_count,
        "fraction_seed_papers": round(frac, 6),
        "top_matched_terms": [{"term": t, "count": c} for t, c in top20],
        "db_path": str(db_path),
        "include_keywords": include_kw,
        "tmd_terms_count": len(TMD_TERMS),
        "output_csv": str(seed_csv.resolve()),
    }
    with open(meta_json, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")

    print(f"Wrote {seed_csv}")
    print(f"Wrote {meta_json}")
    print(f"total_papers_scanned: {total}")
    print(f"num_seed_papers: {seed_count}")
    print(f"fraction_seed_papers: {frac:.6f}")
    print("top_matched_terms (first 10):")
    for t, c in top20[:10]:
        print(f"  {t!r}: {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
