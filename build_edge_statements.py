#!/usr/bin/env python3
"""
Build the statement-backed edge layer for the Polaris demo.

Creates edge_statements from citation_mentions, plus demo_edges and metadata views.
Idempotent: safe to run multiple times.

Usage:
    python build_edge_statements.py --db inspire.sqlite

Regenerate from scratch: same command (idempotent). Requires citation_mentions
to exist; run extract_citation_contexts.py first if needed.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def run(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # Ensure citation_mentions exists
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='citation_mentions'"
    )
    if not cur.fetchone():
        print("Error: citation_mentions table not found. Run extract_citation_contexts.py first.", file=sys.stderr)
        sys.exit(1)

    # 1. Create edge_statements table (idempotent)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS edge_statements (
        child_cn  INTEGER NOT NULL,
        parent_cn INTEGER NOT NULL,
        statement TEXT NOT NULL
    )
    """)
    conn.commit()

    # Indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edge_statements_child ON edge_statements(child_cn)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edge_statements_parent ON edge_statements(parent_cn)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edge_statements_child_parent ON edge_statements(child_cn, parent_cn)")
    conn.commit()

    # 2. Populate from citation_mentions (refresh: delete then insert)
    cur.execute("DELETE FROM edge_statements")
    cur.execute("""
    INSERT INTO edge_statements (child_cn, parent_cn, statement)
    SELECT child_cn, parent_cn, sentence
    FROM citation_mentions
    """)
    conn.commit()

    # 3. Drop and recreate views (idempotent)
    for view in ("demo_edges", "demo_edges_with_meta", "edge_statements_with_meta"):
        cur.execute(f"DROP VIEW IF EXISTS {view}")
    conn.commit()

    cur.execute("""
    CREATE VIEW demo_edges AS
    SELECT
        child_cn,
        parent_cn,
        COUNT(*) AS n_statements
    FROM edge_statements
    GROUP BY child_cn, parent_cn
    """)

    cur.execute("""
    CREATE VIEW demo_edges_with_meta AS
    SELECT
        e.child_cn,
        pc.title AS child_title,
        pc.arxiv_id AS child_arxiv_id,
        pc.doi AS child_doi,
        e.parent_cn,
        pp.title AS parent_title,
        pp.arxiv_id AS parent_arxiv_id,
        pp.doi AS parent_doi,
        e.n_statements
    FROM demo_edges e
    LEFT JOIN papers pc ON pc.control_number = e.child_cn
    LEFT JOIN papers pp ON pp.control_number = e.parent_cn
    """)

    cur.execute("""
    CREATE VIEW edge_statements_with_meta AS
    SELECT
        es.child_cn,
        pc.title AS child_title,
        es.parent_cn,
        pp.title AS parent_title,
        es.statement
    FROM edge_statements es
    LEFT JOIN papers pc ON pc.control_number = es.child_cn
    LEFT JOIN papers pp ON pp.control_number = es.parent_cn
    """)
    conn.commit()


def report_benchmarks(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # ---- A. Basic row counts ----
    n_citation_mentions = cur.execute("SELECT COUNT(*) FROM citation_mentions").fetchone()[0]
    n_edge_statements = cur.execute("SELECT COUNT(*) FROM edge_statements").fetchone()[0]
    n_unique_edges = cur.execute("SELECT COUNT(*) FROM demo_edges").fetchone()[0]
    n_unique_child = cur.execute("SELECT COUNT(DISTINCT child_cn) FROM edge_statements").fetchone()[0]
    n_unique_parent = cur.execute("SELECT COUNT(DISTINCT parent_cn) FROM edge_statements").fetchone()[0]

    print("\n" + "=" * 60)
    print("A. Basic row counts")
    print("=" * 60)
    print(f"  citation_mentions rows:     {n_citation_mentions}")
    print(f"  edge_statements rows:       {n_edge_statements}")
    print(f"  unique edges (demo_edges):  {n_unique_edges}")
    print(f"  unique child papers:        {n_unique_child}")
    print(f"  unique parent papers:       {n_unique_parent}")

    # ---- B. Invariant checks ----
    print("\n" + "=" * 60)
    print("B. Invariant checks")
    print("=" * 60)

    # 1. Every citation_mentions row appears in edge_statements (same count, and content match)
    in_cm_not_es = cur.execute("""
        SELECT COUNT(*) FROM citation_mentions cm
        WHERE NOT EXISTS (
            SELECT 1 FROM edge_statements es
            WHERE es.child_cn = cm.child_cn AND es.parent_cn = cm.parent_cn AND es.statement = cm.sentence
        )
    """).fetchone()[0]
    check1 = in_cm_not_es == 0
    print(f"  1. Every citation_mentions row in edge_statements: {'PASS' if check1 else 'FAIL'} (rows in cm not in es: {in_cm_not_es})")

    # 2. Every demo_edges row has n_statements >= 1 (by construction it's COUNT(*))
    min_n = cur.execute("SELECT MIN(n_statements) FROM demo_edges").fetchone()[0]
    check2 = min_n is not None and min_n >= 1
    print(f"  2. Every demo edge has n_statements >= 1: {'PASS' if check2 else 'FAIL'} (min n_statements: {min_n})")

    # 3. No NULLs in edge_statements
    nulls = cur.execute("""
        SELECT COUNT(*) FROM edge_statements
        WHERE child_cn IS NULL OR parent_cn IS NULL OR statement IS NULL
    """).fetchone()[0]
    check3 = nulls == 0
    print(f"  3. No NULL child_cn/parent_cn/statement: {'PASS' if check3 else 'FAIL'} (null rows: {nulls})")

    # 4. No empty statements after trim
    empty_stmt = cur.execute("""
        SELECT COUNT(*) FROM edge_statements WHERE TRIM(statement) = ''
    """).fetchone()[0]
    check4 = empty_stmt == 0
    print(f"  4. No empty-string statements (after trim): {'PASS' if check4 else 'FAIL'} (empty: {empty_stmt})")

    # ---- C. Duplication checks ----
    print("\n" + "=" * 60)
    print("C. Duplication checks")
    print("=" * 60)

    n_total = cur.execute("SELECT COUNT(*) FROM edge_statements").fetchone()[0]
    n_distinct = cur.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT child_cn, parent_cn, statement FROM edge_statements)"
    ).fetchone()[0]
    total_dup = n_total - n_distinct
    print(f"  Duplicate rows (identical child_cn, parent_cn, statement): {total_dup}")

    edges_multi = cur.execute("""
        SELECT COUNT(*) FROM demo_edges WHERE n_statements > 1
    """).fetchone()[0]
    print(f"  Edges with more than one statement: {edges_multi}")

    print("  Top 20 edges by n_statements:")
    for row in cur.execute("""
        SELECT child_cn, parent_cn, n_statements
        FROM demo_edges
        ORDER BY n_statements DESC
        LIMIT 20
    """):
        print(f"    {row[0]} -> {row[1]}: {row[2]} statements")

    # ---- D. Metadata coverage ----
    print("\n" + "=" * 60)
    print("D. Metadata coverage")
    print("=" * 60)

    child_missing = cur.execute("""
        SELECT COUNT(*) FROM edge_statements es
        LEFT JOIN papers p ON p.control_number = es.child_cn
        WHERE p.control_number IS NULL
    """).fetchone()[0]
    parent_missing = cur.execute("""
        SELECT COUNT(*) FROM edge_statements es
        LEFT JOIN papers p ON p.control_number = es.parent_cn
        WHERE p.control_number IS NULL
    """).fetchone()[0]
    print(f"  edge_statements rows with missing child paper in papers: {child_missing}")
    print(f"  edge_statements rows with missing parent paper in papers: {parent_missing}")

    demo_missing_child = cur.execute("""
        SELECT COUNT(*) FROM demo_edges_with_meta WHERE child_title IS NULL OR child_title = ''
    """).fetchone()[0]
    demo_missing_parent = cur.execute("""
        SELECT COUNT(*) FROM demo_edges_with_meta WHERE parent_title IS NULL OR parent_title = ''
    """).fetchone()[0]
    print(f"  demo_edges_with_meta rows with missing child_title: {demo_missing_child}")
    print(f"  demo_edges_with_meta rows with missing parent_title: {demo_missing_parent}")

    # ---- E. Sanity samples ----
    print("\n" + "=" * 60)
    print("E. Sanity samples")
    print("=" * 60)

    print("\n  10 example edges (with first 1-3 statements):")
    seen_edges = set()
    count = 0
    for row in cur.execute("""
        SELECT child_cn, child_title, parent_cn, parent_title, n_statements
        FROM demo_edges_with_meta
        ORDER BY n_statements DESC
    """):
        if count >= 10:
            break
        key = (row[0], row[2])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        count += 1
        child_cn, child_title, parent_cn, parent_title, n_statements = row
        ct = (child_title or "")[:60]
        if child_title and len(child_title) > 60:
            ct += "..."
        pt = (parent_title or "")[:60]
        if parent_title and len(parent_title) > 60:
            pt += "..."
        print(f"\n    Edge {count}: {child_cn} -> {parent_cn} (n_statements={n_statements})")
        print(f"      child:  {ct}")
        print(f"      parent: {pt}")
        for stmt_row in cur.execute(
            "SELECT statement FROM edge_statements WHERE child_cn = ? AND parent_cn = ? LIMIT 3",
            (child_cn, parent_cn),
        ):
            s = (stmt_row[0] or "").strip()[:200]
            if len((stmt_row[0] or "")) > 200:
                s += "..."
            print(f"      statement: {s}")

    print("\n  10 random statement rows:")
    for row in cur.execute("""
        SELECT child_cn, parent_cn, statement FROM edge_statements
        ORDER BY RANDOM() LIMIT 10
    """):
        s = (row[2] or "").strip()[:120]
        if len((row[2] or "")) > 120:
            s += "..."
        print(f"    {row[0]} -> {row[1]}: {s}")

    print("\n  10 longest statements (length):")
    for row in cur.execute("""
        SELECT child_cn, parent_cn, LENGTH(statement), statement
        FROM edge_statements
        ORDER BY LENGTH(statement) DESC
        LIMIT 10
    """):
        s = (row[3] or "").strip()[:100]
        if len((row[3] or "")) > 100:
            s += "..."
        print(f"    len={row[2]}: {s}")

    print("\n  10 shortest non-empty statements:")
    for row in cur.execute("""
        SELECT child_cn, parent_cn, LENGTH(statement), statement
        FROM edge_statements
        WHERE TRIM(statement) != ''
        ORDER BY LENGTH(statement) ASC
        LIMIT 10
    """):
        s = (row[3] or "").strip()
        print(f"    len={row[2]}: {s}")

    # ---- F. Optional subgraph cross-check ----
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subgraph_edges_25808_present'"
    )
    if cur.fetchone():
        print("\n" + "=" * 60)
        print("F. Subgraph cross-check (subgraph_edges_25808_present)")
        print("=" * 60)

        # subgraph_edges_25808_present: (parent, child, depth) = (cited, citing)
        in_both = cur.execute("""
            SELECT COUNT(*) FROM demo_edges e
            WHERE EXISTS (
                SELECT 1 FROM subgraph_edges_25808_present s
                WHERE s.child = e.child_cn AND s.parent = e.parent_cn
            )
        """).fetchone()[0]
        stmt_not_in_sub = cur.execute("""
            SELECT COUNT(*) FROM demo_edges e
            WHERE NOT EXISTS (
                SELECT 1 FROM subgraph_edges_25808_present s
                WHERE s.child = e.child_cn AND s.parent = e.parent_cn
            )
        """).fetchone()[0]
        sub_no_stmt = cur.execute("""
            SELECT COUNT(*) FROM subgraph_edges_25808_present s
            WHERE NOT EXISTS (
                SELECT 1 FROM demo_edges e
                WHERE e.child_cn = s.child AND e.parent_cn = s.parent
            )
        """).fetchone()[0]
        n_sub = cur.execute("SELECT COUNT(*) FROM subgraph_edges_25808_present").fetchone()[0]

        print(f"  Statement-backed edges also in subgraph:     {in_both}")
        print(f"  Statement-backed edges NOT in subgraph:      {stmt_not_in_sub}")
        print(f"  Subgraph edges without statement coverage:  {sub_no_stmt} (of {n_sub} total subgraph edges)")
    else:
        print("\n  (F. Subgraph table subgraph_edges_25808_present not found; skipping cross-check)")

    print("\n" + "=" * 60)
    print("Done.")
    print("=" * 60)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build statement-backed edge table and demo views")
    ap.add_argument("--db", default="inspire.sqlite", help="Path to SQLite database")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"Error: Database not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        run(conn)
        report_benchmarks(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
