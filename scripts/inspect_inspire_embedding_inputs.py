#!/usr/bin/env python3
"""
STEP 0: Inspect SQLite schema for corpus embedding (papers, paper_keywords).

Read-only DB; writes outputs_no_sync/inspire_embeddings/input_inspection.txt
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from inspire_embedding_common import connect_readonly_sqlite, repo_root, resolve_db_path


def schema_for_table(conn, name: str) -> str:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchall()
    return "\n".join(r[0] or "" for r in rows) or "(no sql found)"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("inspect")

    ap = argparse.ArgumentParser(description="Inspect INSPIRE DB for embedding pipeline")
    ap.add_argument("--db", default="inspire_mirror.sqlite")
    ap.add_argument(
        "--output-root",
        default="outputs_no_sync/inspire_embeddings",
        help="Ignored output directory root",
    )
    args = ap.parse_args()

    root = repo_root()
    db_path = resolve_db_path(root, args.db)
    out_root = (root / args.output_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    log_dir = out_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_root / "input_inspection.txt"

    lines: list[str] = []
    lines.append("INSPIRE corpus embedding — input inspection")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Repo root: {root}")
    lines.append(f"Database: {db_path}")
    lines.append(f"SQLite URI: {db_path.as_uri()}?mode=ro")
    lines.append("(opened read-only via URI mode=ro)")
    lines.append("")

    conn = connect_readonly_sqlite(db_path)

    for tbl in ("papers", "paper_keywords"):
        lines.append(f"--- SCHEMA: {tbl} ---")
        lines.append(schema_for_table(conn, tbl))
        lines.append("")

    for tbl in ("papers", "paper_keywords"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            lines.append(f"ROW COUNT {tbl}: {n}")
        except Exception as e:
            lines.append(f"ROW COUNT {tbl}: ERROR {e}")
    lines.append("")

    lines.append("--- SAMPLE papers (3 rows) ---")
    try:
        cur = conn.execute("SELECT * FROM papers ORDER BY control_number LIMIT 3")
        cols = [d[0] for d in cur.description]
        for i, row in enumerate(cur.fetchall(), 1):
            lines.append(f"sample {i}: {dict(zip(cols, row))}")
    except Exception as e:
        lines.append(f"ERROR: {e}")
    lines.append("")

    lines.append("--- SAMPLE keyword aggregations (3 papers, GROUP_CONCAT) ---")
    try:
        cur = conn.execute(
            """
            SELECT control_number, GROUP_CONCAT(keyword, '; ') AS keywords_agg
            FROM paper_keywords
            GROUP BY control_number
            ORDER BY control_number
            LIMIT 3
            """
        )
        cols = [d[0] for d in cur.description]
        for i, row in enumerate(cur.fetchall(), 1):
            lines.append(f"sample {i}: {dict(zip(cols, row))}")
    except Exception as e:
        lines.append(f"ERROR: {e}")

    conn.close()

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    log.info("Wrote %s", out_path)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
