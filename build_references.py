#!/usr/bin/env python3
from __future__ import annotations

import random
import argparse
import sqlite3
import time
from typing import Iterable, Optional

import requests
from tqdm import tqdm

API_BASE = "https://inspirehep.net/api/literature"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS citations (
      citing INTEGER,
      cited INTEGER,
      PRIMARY KEY (citing, cited)
    )
    """)
    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_citations_citing ON citations(citing)
    """)
    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_citations_cited ON citations(cited)
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS meta (
      k TEXT PRIMARY KEY,
      v TEXT
    )
    """)
    conn.commit()


def meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(conn: sqlite3.Connection, key: str, val: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (key, val))
    conn.commit()


def iter_papers(conn: sqlite3.Connection) -> Iterable[int]:
    for (cn,) in conn.execute("SELECT control_number FROM papers ORDER BY control_number"):
        yield int(cn)


def in_universe(conn: sqlite3.Connection, recid: int) -> bool:
    row = conn.execute("SELECT 1 FROM papers WHERE control_number=? LIMIT 1", (recid,)).fetchone()
    return row is not None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="inspire.sqlite")
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    # Set busy timeout to 60 seconds (waits for locks instead of failing immediately)
    conn.execute("PRAGMA busy_timeout=60000")
    ensure_schema(conn)

    # Resume: remember last processed control_number
    last = meta_get(conn, "edges_last_control_number")
    last_cn = int(last) if last and last.isdigit() else -1

    sess = requests.Session()
    sess.headers.update({"Accept": "application/json"})

    # Preload universe membership check via a temp table for speed (optional).
    # Keep it simple: use DB lookups (fine for ~10k).
    papers = [cn for cn in iter_papers(conn) if cn > last_cn]

    pbar = tqdm(total=len(papers), unit="paper")
    inserted_edges = 0

    for cn in papers:
        url = f"{API_BASE}/{cn}"
        r = sess.get(url, timeout=args.timeout)
        r.raise_for_status()
        data = r.json()

        md = (data.get("metadata") or {})
        refs = md.get("references") or []

        for ref in refs:
            # references[].record.$ref looks like ".../api/literature/<recid>"
            rec = (ref.get("record") or {})
            ref_url = rec.get("$ref") or ""
            if not ref_url:
                continue
            try:
                cited = int(ref_url.rstrip("/").split("/")[-1])
            except Exception:
                continue

            if not in_universe(conn, cited):
                continue

            conn.execute(
                "INSERT OR IGNORE INTO citations(citing, cited) VALUES(?,?)",
                (cn, cited),
            )
            inserted_edges += 1

        conn.commit()
        meta_set(conn, "edges_last_control_number", str(cn))

        if args.sleep:
            time.sleep(args.sleep)

        pbar.update(1)

    pbar.close()
    print(f"Done. Inserted/checked ~{inserted_edges} in-universe edges.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
