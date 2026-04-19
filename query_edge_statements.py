#!/usr/bin/env python3
"""
Polaris retrieval demo: question -> statements -> papers over the statement-backed graph.

Searches edge_statements by query text; returns matching statements with edge and paper metadata.
Modes: lexical (FTS5 or Python fallback), semantic (embeddings), hybrid (lexical + semantic).

Usage:
    python query_edge_statements.py --db inspire.sqlite --query "Sudakov resummation"
    python query_edge_statements.py --db inspire.sqlite --build-embeddings
    python query_edge_statements.py --db inspire.sqlite --query "CSS" --mode semantic --top-k 10
    python query_edge_statements.py --db inspire.sqlite --query "TMD evolution" --mode hybrid --by-edge
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

# Embeddings: optional deps (sentence_transformers, sklearn, numpy)
def _require_embedding_deps():
    try:
        import numpy as np  # noqa: F401
        from sklearn.neighbors import NearestNeighbors  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except ImportError as e:
        print(
            "Error: Semantic mode requires: pip install sentence-transformers scikit-learn numpy",
            file=sys.stderr,
        )
        raise SystemExit(1) from e

EMBED_DIR = Path("data/embeddings")
DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"  # small, fast, 384-dim


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


# ---------------------------------------------------------------------------
# Semantic retrieval (embeddings)
# ---------------------------------------------------------------------------

def _retrieval_text(row: tuple, mode: str) -> str:
    """Build text to embed. row = (child_cn, child_title, parent_cn, parent_title, statement)."""
    child_cn, child_title, parent_cn, parent_title, statement = row
    st = (statement or "").strip()
    if mode == "statement-only":
        return st
    ct = (child_title or "").strip()[:200]
    pt = (parent_title or "").strip()[:200]
    return f"Citing: {ct}. Cited: {pt}. {st}"


def build_embeddings(
    conn: sqlite3.Connection,
    embed_dir: Path,
    model_name: str = DEFAULT_EMBED_MODEL,
    retrieval_text_mode: str = "statement-and-titles",
) -> None:
    """Embed all rows in edge_statements; save to embed_dir."""
    _require_embedding_deps()
    import numpy as np
    from sentence_transformers import SentenceTransformer
    import csv

    cur = conn.cursor()
    cur.execute("""
        SELECT child_cn, child_title, parent_cn, parent_title, statement
        FROM edge_statements_with_meta
    """)
    rows = list(cur.fetchall())
    if not rows:
        print("No rows in edge_statements_with_meta.", file=sys.stderr)
        return

    texts = [_retrieval_text(r, retrieval_text_mode) for r in rows]
    for i, t in enumerate(texts):
        if not t.strip():
            texts[i] = " "

    embed_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading model {model_name}...")
    model = SentenceTransformer(model_name)
    print(f"Encoding {len(texts)} texts...")
    t0 = time.perf_counter()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
    elapsed = time.perf_counter() - t0
    print(f"Encoded in {elapsed:.2f}s, shape {embeddings.shape}")

    np.save(embed_dir / "statement_embeddings.npy", embeddings.astype(np.float32))

    meta_path = embed_dir / "metadata.csv"
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write("index,child_cn,parent_cn,statement,child_title,parent_title\n")
        for i, r in enumerate(rows):
            child_cn, child_title, parent_cn, parent_title, statement = r
            def esc(s):
                s = (s or "").replace('"', '""')
                return f'"{s}"'
            f.write(f"{i},{child_cn},{parent_cn},{esc(statement)},{esc(child_title or '')},{esc(parent_title or '')}\n")

    config = {
        "model_name": model_name,
        "retrieval_text_mode": retrieval_text_mode,
        "n_rows": len(rows),
        "dim": int(embeddings.shape[1]),
    }
    with open(embed_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print(f"Saved to {embed_dir}: statement_embeddings.npy, metadata.csv, config.json")
    size_mb = (embed_dir / "statement_embeddings.npy").stat().st_size / (1024 * 1024)
    print(f"Index size: {size_mb:.2f} MB")


def load_embeddings(embed_dir: Path) -> tuple:
    """Load embeddings, metadata, config. Returns (embeddings, metadata_list, config)."""
    _require_embedding_deps()
    import numpy as np
    import csv

    emb_path = embed_dir / "statement_embeddings.npy"
    meta_path = embed_dir / "metadata.csv"
    config_path = embed_dir / "config.json"
    if not emb_path.exists() or not meta_path.exists() or not config_path.exists():
        return (None, None, None)

    embeddings = np.load(emb_path)
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    metadata = []
    with open(meta_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            metadata.append({
                "child_cn": int(row["child_cn"]),
                "parent_cn": int(row["parent_cn"]),
                "statement": row["statement"],
                "child_title": row["child_title"],
                "parent_title": row["parent_title"],
            })
    return (embeddings, metadata, config)


def search_semantic(
    query: str,
    top_k: int,
    embed_dir: Path,
) -> list[dict]:
    """Nearest-neighbor search over statement embeddings. Returns same dict shape as lexical."""
    _require_embedding_deps()
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from sklearn.neighbors import NearestNeighbors

    loaded = load_embeddings(embed_dir)
    embeddings, metadata, config = loaded
    if embeddings is None or metadata is None:
        return []

    model_name = config.get("model_name", DEFAULT_EMBED_MODEL)
    model = SentenceTransformer(model_name)
    query_emb = model.encode([query], normalize_embeddings=True)

    nn = NearestNeighbors(n_neighbors=min(top_k, len(metadata)), metric="cosine")
    nn.fit(embeddings)
    dists, indices = nn.kneighbors(query_emb)

    hits = []
    for idx, dist in zip(indices[0], dists[0]):
        m = metadata[idx]
        sim = float(1.0 - dist)
        hits.append({
            "score": round(sim, 4),
            "child_cn": m["child_cn"],
            "child_title": m["child_title"] or "",
            "parent_cn": m["parent_cn"],
            "parent_title": m["parent_title"] or "",
            "statement": m["statement"] or "",
        })
    return hits


def search_hybrid(
    conn: sqlite3.Connection,
    query: str,
    top_k: int,
    embed_dir: Path,
    use_fts: bool | None,
    alpha: float = 0.5,
) -> list[dict]:
    """Combine lexical and semantic: merge by normalized score."""
    fetch_k = min(max(top_k * 3, 50), 500)
    lex_hits, _ = search(conn, query, fetch_k, use_fts)
    sem_hits = search_semantic(query, fetch_k, embed_dir)

    if not lex_hits and not sem_hits:
        return []
    if not lex_hits:
        return sem_hits[:top_k]
    if not sem_hits:
        return lex_hits[:top_k]

    def key(h):
        return (h["child_cn"], h["parent_cn"], (h["statement"] or "")[:500])

    lex_by_key = {key(h): h for h in lex_hits}
    sem_by_key = {key(h): h for h in sem_hits}
    all_keys = set(lex_by_key) | set(sem_by_key)

    lex_scores = [lex_by_key[k]["score"] for k in all_keys if k in lex_by_key]
    sem_scores = [sem_by_key[k]["score"] for k in all_keys if k in sem_by_key]
    lex_max = max(lex_scores) if lex_scores else 1.0
    lex_min = min(lex_scores) if lex_scores else 0.0
    sem_max = max(sem_scores) if sem_scores else 1.0
    sem_min = min(sem_scores) if sem_scores else 0.0
    lex_span = lex_max - lex_min if lex_max > lex_min else 1.0
    sem_span = sem_max - sem_min if sem_max > sem_min else 1.0

    combined = []
    for k in all_keys:
        h_lex = lex_by_key.get(k)
        h_sem = sem_by_key.get(k)
        base = h_sem if h_sem is not None else h_lex
        s_lex = (h_lex["score"] - lex_min) / lex_span if h_lex else 0.0
        s_sem = (h_sem["score"] - sem_min) / sem_span if h_sem else 0.0
        combined.append((alpha * s_lex + (1 - alpha) * s_sem, base))
    combined.sort(key=lambda x: -x[0])
    out = []
    for s, h in combined[:top_k]:
        out.append({**h, "score": round(s, 4)})
    return out


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
    ap.add_argument("--query", "-q", help="Physics query string (required unless --build-embeddings)")
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
        help="Force Python fallback instead of FTS5 (lexical mode)",
    )
    ap.add_argument(
        "--build-fts",
        action="store_true",
        help="Rebuild FTS5 table before querying",
    )
    ap.add_argument(
        "--build-embeddings",
        action="store_true",
        help="Build and save statement embeddings (no query).",
    )
    ap.add_argument(
        "--embeddings-dir",
        type=Path,
        default=EMBED_DIR,
        help=f"Directory for embedding index (default: {EMBED_DIR})",
    )
    ap.add_argument(
        "--mode",
        choices=["lexical", "semantic", "hybrid"],
        default="lexical",
        help="Retrieval mode: lexical (FTS/fallback), semantic (embeddings), hybrid (both)",
    )
    ap.add_argument(
        "--retrieval-text",
        choices=["statement-only", "statement-and-titles"],
        default="statement-and-titles",
        help="Text to embed: statement only, or statement + citing/cited titles (build only)",
    )
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"Error: Database not found: {args.db}", file=sys.stderr)
        return 1

    # Build-embeddings path: no query required
    if args.build_embeddings:
        conn = sqlite3.connect(args.db)
        try:
            build_embeddings(
                conn,
                args.embeddings_dir,
                model_name=DEFAULT_EMBED_MODEL,
                retrieval_text_mode=args.retrieval_text,
            )
        finally:
            conn.close()
        return 0

    if not args.query:
        print("Error: --query is required unless using --build-embeddings.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        if args.build_fts:
            ensure_fts(conn, rebuild=True)

        t0 = time.perf_counter()
        use_fts = False if args.no_fts else None
        fetch_k = args.top_k * 5 if args.by_edge else args.top_k

        if args.mode == "lexical":
            hits, method = search(conn, args.query, fetch_k, use_fts)
        elif args.mode == "semantic":
            loaded = load_embeddings(args.embeddings_dir)
            if loaded[0] is None:
                print(
                    f"Error: No embedding index at {args.embeddings_dir}. Run with --build-embeddings first.",
                    file=sys.stderr,
                )
                return 1
            hits = search_semantic(args.query, fetch_k, args.embeddings_dir)
            method = "semantic"
        else:
            # hybrid
            loaded = load_embeddings(args.embeddings_dir)
            if loaded[0] is None:
                print(
                    f"Error: No embedding index at {args.embeddings_dir}. Run with --build-embeddings first.",
                    file=sys.stderr,
                )
                return 1
            hits = search_hybrid(conn, args.query, fetch_k, args.embeddings_dir, use_fts)
            method = "hybrid"

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
