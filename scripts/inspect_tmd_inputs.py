#!/usr/bin/env python3
"""
STEP 0: Inspect SQLite schema, table counts, and strong-seed CSV for TMD discovery.

Opens DB read-only via URI (?mode=ro). Writes data/tmd_field_discovery/input_inspection.txt
"""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path


def schema_for_table(conn: sqlite3.Connection, name: str) -> str:
    rows = conn.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchall()
    return "\n".join(r[0] or "" for r in rows) or "(no sql found)"


def sample_rows(conn: sqlite3.Connection, sql: str, n: int) -> list:
    cur = conn.execute(sql + f" LIMIT {int(n)}")
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        out.append(dict(zip(cols, row)))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("inspect")

    ap = argparse.ArgumentParser(description="Inspect TMD field discovery inputs")
    ap.add_argument("--db", default="inspire_mirror.sqlite", help="SQLite path (read-only)")
    ap.add_argument(
        "--seed-csv",
        default="data/tmd_field_discovery/seed_set_strong.csv",
        help="Strong seed CSV path",
    )
    ap.add_argument(
        "--out",
        default="data/tmd_field_discovery/input_inspection.txt",
        help="Inspection report output",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    db_path = (root / args.db).expanduser().resolve()
    seed_csv = (root / args.seed_csv).expanduser().resolve()
    out_path = (root / args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("TMD field discovery — input inspection")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Repo root (inferred): {root}")
    lines.append(f"Requested DB path: {db_path}")
    lines.append(f"Requested seed CSV: {seed_csv}")
    lines.append("")

    if not db_path.is_file():
        alt = root / "inspire.sqlite"
        if alt.is_file():
            log.warning("Primary DB missing; using inspire.sqlite for inspection")
            db_path = alt.resolve()
            lines.append(f"Using fallback DB: {db_path}")
        else:
            lines.append(f"ERROR: database file not found: {db_path}")
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("\n".join(lines))
            return 1

    # SQLite read-only URI (three slashes for absolute path on POSIX)
    uri = db_path.as_uri() + "?mode=ro"
    lines.append(f"SQLite URI: {uri}")
    lines.append("(connection opened read-only via URI mode=ro)")
    lines.append("")

    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    for tbl in ("papers", "citations", "paper_keywords"):
        lines.append(f"--- SCHEMA: {tbl} ---")
        lines.append(schema_for_table(conn, tbl))
        lines.append("")

    for tbl in ("papers", "citations", "paper_keywords"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            lines.append(f"ROW COUNT {tbl}: {n}")
        except sqlite3.Error as e:
            lines.append(f"ROW COUNT {tbl}: ERROR {e}")
    lines.append("")

    lines.append("--- SAMPLE papers (3 rows) ---")
    try:
        samples = sample_rows(conn, "SELECT * FROM papers", 3)
        for i, s in enumerate(samples, 1):
            lines.append(f"sample {i}: {dict(s)}")
    except sqlite3.Error as e:
        lines.append(f"ERROR: {e}")
    lines.append("")

    lines.append("--- SAMPLE citations (3 rows) ---")
    try:
        samples = sample_rows(conn, "SELECT * FROM citations", 3)
        for i, s in enumerate(samples, 1):
            lines.append(f"sample {i}: {dict(s)}")
    except sqlite3.Error as e:
        lines.append(f"ERROR: {e}")
    conn.close()
    lines.append("")

    lines.append("--- STRONG SEED CSV ---")
    if not seed_csv.is_file():
        lines.append(f"ERROR: seed CSV not found: {seed_csv}")
    else:
        lines.append(f"Path exists: {seed_csv}")
        with open(seed_csv, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r, None)
            lines.append(f"Header: {header}")
            n_seed = sum(1 for _ in r)
            lines.append(f"Data rows (excluding header): {n_seed}")
        # detect id column
        with open(seed_csv, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            if rd.fieldnames:
                id_candidates = [x for x in ("control_number", "recid", "cn") if x in rd.fieldnames]
                lines.append(f"ID column candidates present: {id_candidates or rd.fieldnames}")
        lines.append("First 3 data lines:")
        with open(seed_csv, newline="", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 4:
                    break
                lines.append(line.rstrip())

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    log.info("Wrote %s", out_path)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
