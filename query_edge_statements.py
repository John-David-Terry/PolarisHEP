#!/usr/bin/env python3
"""
Polaris retrieval demo: question -> statements -> papers over the statement-backed graph.

Searches edge_statements by query text; returns matching statements with edge and paper metadata.
Uses FTS5 if available, else Python lexical scoring.

Usage:
    python query_edge_statements.py --db inspire.sqlite --query "Sudakov resummation"
    python query_edge_statements.py --db inspire.sqlite --query "TMD evolution" --top-k 5 --json
    python query_edge_statements.py --db inspire.sqlite --query "factorization" --by-edge
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Normalization and scoring (fallback when FTS5 not available)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    t = text.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def tokenize(text: str) -> list[str]:
    """Split on whitespace; drop empty."""
    return [w for w in normalize(text).split() if w]


def score_statement(query_tokens: list[str], statement: str) -> float:
    """
    Simple lexical score: term overlap + optional phrase bonus.
    No embeddings; auditable.
    """
    if not query_tokens:
        return 0.0
    st_tokens = tokenize(statement)
    if not st_tokens:
        return 0.0
    st_set = set(st_tokens)
    st_lower = (statement or "").lower()
    score = 0.0
    for q in query_tokens:
        if q in st_set:
            # term frequency in statement (cap at 5)
            tf = min(st_tokens.count(q), 5)
            score += 1.0 + 0.2 * tf
        # phrase bonus: contiguous query phrase in statement
        if len(query_tokens) > 1 and q in st_lower:
            score += 0.1
    phrase = " ".join(query_tokens)
    if phrase in st_lower:
        score += 2.0
    return score


# ---------------------------------------------------------------------------
# FTS5 setup and search
# ---------------------------------------------------------------------------

def ensure_fts(conn: sqlite3.Connection, rebuild: bool = False) -> bool:
    """
    Create and populate edge_statements_fts if missing or rebuild requested.
    Returns True if FTS5 is available and ready, False to use fallback.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='edge_statements'"
    )
    if not cur.fetchone():
        return False
    if rebuild:
        cur.execute("DROP TABLE IF EXISTS edge_statements_fts")
        conn.commit()
    else:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='edge_statements_fts'"
        )
        if cur.fetchone():
            return True
    try:
        cur.execute("""
            CREATE VIRTUAL TABLE edge_statements_fts USING fts5(
                statement,
                child_cn UNINDEXED,
                parent_cn UNINDEXED
            )
        """)
        conn.commit()
    except sqlite3.OperationalError as e:
        if "fts5" in str(e).lower() or "no such module" in str(e).lower():
            return False
        raise
    cur.execute("""
        INSERT INTO edge_statements_fts(statement, child_cn, parent_cn)
        SELECT statement, child_cn, parent_cn FROM edge_statements
    """)
    conn.commit()
    return True


def fts_query_to_match(phrase: str) -> str:
    """
    Turn a user query into FTS5 MATCH expression.
    Use AND for multiword (all terms must appear); escape double-quotes.
    For phrase search, user can pass e.g. '"exact phrase"' in the query.
    """
    phrase = (phrase or "").strip()
    phrase = phrase.replace('"', '""')
    tokens = [t for t in phrase.split() if t]
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]
    return " AND ".join(tokens)


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    top_k: int,
) -> list[dict]:
    """Search using FTS5; return list of hit dicts with score, edge, titles, statement."""
    cur = conn.cursor()
    match_expr = fts_query_to_match(query)
    if not match_expr:
        return []
    # bm25(edge_statements_fts) returns negative (lower is better); use -bm25 as score
    cur.execute("""
        SELECT
            fts.child_cn,
            fts.parent_cn,
            fts.statement,
            -bm25(edge_statements_fts) AS score
        FROM edge_statements_fts fts
        WHERE edge_statements_fts MATCH ?
        ORDER BY score DESC
        LIMIT ?
    """, (match_expr, top_k))
    rows = cur.fetchall()
    # Attach titles
    out = []
    for child_cn, parent_cn, statement, score in rows:
        cur.execute(
            "SELECT title FROM papers WHERE control_number = ?", (child_cn,)
        )
        child_title = (cur.fetchone() or (None,))[0]
        cur.execute(
            "SELECT title FROM papers WHERE control_number = ?", (parent_cn,)
        )
        parent_title = (cur.fetchone() or (None,))[0]
        out.append({
            "score": round(score, 4),
            "child_cn": child_cn,
            "child_title": child_title or "",
            "parent_cn": parent_cn,
            "parent_title": parent_title or "",
            "statement": statement or "",
        })
    return out


def search_fallback(
    conn: sqlite3.Connection,
    query: str,
    top_k: int,
) -> list[dict]:
    """Search by loading statements and scoring in Python."""
    cur = conn.cursor()
    cur.execute("""
        SELECT child_cn, child_title, parent_cn, parent_title, statement
        FROM edge_statements_with_meta
    """)
    rows = cur.fetchall()
    q_tokens = tokenize(query)
    scored = []
    for child_cn, child_title, parent_cn, parent_title, statement in rows:
        s = score_statement(q_tokens, statement or "")
        if s > 0:
            scored.append((s, child_cn, child_title or "", parent_cn, parent_title or "", statement or ""))
    scored.sort(key=lambda x: -x[0])
    return [
        {
            "score": round(score, 4),
            "child_cn": child_cn,
            "child_title": child_title,
            "parent_cn": parent_cn,
            "parent_title": parent_title,
            "statement": statement,
        }
        for score, child_cn, child_title, parent_cn, parent_title, statement in scored[:top_k]
    ]


def search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int,
    use_fts: bool | None,
) -> tuple[list[dict], str]:
    """
    Run search; use_fts True = try FTS5, False = force fallback, None = try FTS5 then fallback.
    Returns (hits, method) where method is "fts5" or "fallback".
    """
    if use_fts is not False:
        if ensure_fts(conn):
            hits = search_fts(conn, query, top_k)
            return hits, "fts5"
    hits = search_fallback(conn, query, top_k)
    return hits, "fallback"


def aggregate_by_edge(hits: list[dict], top_k: int, show_all_statements: bool) -> list[dict]:
    """
    Group hits by (child_cn, parent_cn); aggregate score = sum(score); sort by edge score.
    Each edge returns: edge_score, n_matching_statements, top statement(s), child/parent info.
    """
    from collections import defaultdict
    edges: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for h in hits:
        key = (h["child_cn"], h["parent_cn"])
        edges[key].append(h)
    out = []
    for (child_cn, parent_cn), group in edges.items():
        group.sort(key=lambda x: -x["score"])
        total_score = sum(x["score"] for x in group)
        first = group[0]
        edge_row = {
            "edge_score": round(total_score, 4),
            "n_matching_statements": len(group),
            "child_cn": child_cn,
            "child_title": first["child_title"],
            "parent_cn": parent_cn,
            "parent_title": first["parent_title"],
            "top_statement": first["statement"],
        }
        if show_all_statements:
            edge_row["statements"] = [x["statement"] for x in group]
            edge_row["statement_scores"] = [x["score"] for x in group]
        out.append(edge_row)
    out.sort(key=lambda x: -x["edge_score"])
    return out[:top_k]


def output_readable(hits: list[dict], by_edge: bool, top_k: int) -> None:
    """Print human-readable results."""
    if by_edge:
        for i, e in enumerate(hits[:top_k], 1):
            ct = (e.get("child_title") or "")[:70]
            if len(e.get("child_title") or "") > 70:
                ct += "..."
            pt = (e.get("parent_title") or "")[:70]
            if len(e.get("parent_title") or "") > 70:
                pt += "..."
            print(f"\n--- Edge {i} (score={e['edge_score']}, n_statements={e['n_matching_statements']}) ---")
            print(f"  Child  [{e['child_cn']}] {ct}")
            print(f"  Parent [{e['parent_cn']}] {pt}")
            top_st = e.get("top_statement") or ""
            st_list = e.get("statements") or []
            first_st = top_st or (st_list[0] if st_list else "")
            st = (first_st or "")[:300]
            if len(first_st or "") > 300:
                st += "..."
            print(f"  Statement: {st}")
            if st_list and len(st_list) > 1:
                for j, s in enumerate(st_list[1:4], 2):
                    snip = (s or "")[:150] + ("..." if len(s or "") > 150 else "")
                    print(f"    ({j}) {snip}")
    else:
        for i, h in enumerate(hits[:top_k], 1):
            print(f"\n--- Hit {i} (score={h['score']}) ---")
            print(f"  Child  [{h['child_cn']}] {h['child_title'][:70]}...")
            print(f"  Parent [{h['parent_cn']}] {h['parent_title'][:70]}...")
            st = (h["statement"] or "")[:300]
            if len(h["statement"] or "") > 300:
                st += "..."
            print(f"  Statement: {st}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Query statement-backed graph: question -> statements -> papers"
    )
    ap.add_argument("--db", default="inspire.sqlite", help="Path to SQLite database")
    ap.add_argument("--query", "-q", required=True, help="Physics query string")
    ap.add_argument("--top-k", "-k", type=int, default=10, help="Max results to return")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    ap.add_argument(
        "--show-all-statements-per-edge",
        action="store_true",
        help="When using --by-edge, include all statements for each edge",
    )
    ap.add_argument(
        "--by-edge",
        action="store_true",
        help="Rank by edge (aggregate score over statements per edge)",
    )
    ap.add_argument(
        "--no-fts",
        action="store_true",
        help="Force Python fallback instead of FTS5",
    )
    ap.add_argument(
        "--build-fts",
        action="store_true",
        help="Rebuild FTS5 table before querying",
    )
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"Error: Database not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        if args.build_fts:
            ensure_fts(conn, rebuild=True)
        t0 = time.perf_counter()
        hits, method = search(
            conn,
            args.query,
            top_k=args.top_k * 5 if args.by_edge else args.top_k,  # fetch more for edge agg
            use_fts=False if args.no_fts else None,
        )
        if args.by_edge:
            hits = aggregate_by_edge(hits, args.top_k, args.show_all_statements_per_edge)
        else:
            hits = hits[: args.top_k]
        elapsed = time.perf_counter() - t0

        if args.json:
            out = {
                "query": args.query,
                "method": method,
                "elapsed_sec": round(elapsed, 4),
                "top_k": args.top_k,
                "by_edge": args.by_edge,
                "results": hits,
            }
            print(json.dumps(out, indent=2))
        else:
            print(f"Query: \"{args.query}\" (method={method}, elapsed={elapsed:.3f}s, top_k={args.top_k})")
            if not hits:
                print("No matches.")
            else:
                output_readable(hits, args.by_edge, args.top_k)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
