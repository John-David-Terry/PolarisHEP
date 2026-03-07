#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import re
import sqlite3
import time
from typing import Any, Dict, Iterable

import requests
from tqdm import tqdm

API_BASE = "https://inspirehep.net/api/literature"

RETRY_CODES = {429, 500, 502, 503, 504}

LIT_REF_RE = re.compile(r"/api/literature/(\d+)$")


def debug_exception(e: Exception, url: str, r: requests.Response | None = None) -> None:
    print("\n" + "=" * 80)
    print("ERROR while processing URL:")
    print(url)
    print("Exception:", type(e).__name__, str(e))
    if r is not None:
        try:
            print("HTTP status:", r.status_code)
        except Exception:
            pass
        try:
            txt = r.text
            print("Response head:")
            print(txt[:300].replace("\n", "\\n"))
        except Exception:
            pass
    print("=" * 80 + "\n")


def get_with_retry(sess, url, timeout=60, max_tries=10):
    for attempt in range(1, max_tries + 1):
        try:
            r = sess.get(url, timeout=timeout)
            if r.status_code == 200:
                try:
                    _ = r.json()  # force JSON parse here
                    return r
                except ValueError:
                    pass  # bad JSON → retry

            if r.status_code in RETRY_CODES:
                pass  # retry below
            else:
                r.raise_for_status()

        except requests.RequestException:
            pass

        sleep = min(2 ** attempt, 30) * (0.7 + 0.6 * random.random())
        time.sleep(sleep)

    raise RuntimeError(f"Failed after {max_tries} retries: {url}")


def pick_title(md: Dict[str, Any]) -> str:
    titles = md.get("titles") or []
    if titles and isinstance(titles, list):
        return titles[0].get("title") or ""
    return ""


def pick_abstract(md: Dict[str, Any]) -> str:
    abstracts = md.get("abstracts") or []
    if abstracts and isinstance(abstracts, list):
        return abstracts[0].get("value") or ""
    return ""


def pick_date(md: Dict[str, Any]) -> str:
    imprints = md.get("imprints") or []
    if imprints and isinstance(imprints, list):
        d = imprints[0].get("date")
        if d:
            return d
    return md.get("preprint_date") or ""


def pick_arxiv(md: Dict[str, Any]) -> tuple[str, str]:
    arx = md.get("arxiv_eprints") or []
    if arx and isinstance(arx, list):
        value = arx[0].get("value") or ""
        cats = arx[0].get("categories") or []
        cat0 = cats[0] if cats else ""
        return value, cat0
    return "", ""


def pick_doi(md: Dict[str, Any]) -> str:
    dois = md.get("dois") or []
    if dois and isinstance(dois, list):
        return dois[0].get("value") or ""
    return ""


def pick_keywords(md: Dict[str, Any]) -> list[str]:
    # INSPIRE schema commonly uses: metadata.keywords = [{"value": "..."}]
    kws = md.get("keywords") or []
    out: list[str] = []
    if isinstance(kws, list):
        for k in kws:
            if isinstance(k, dict):
                v = (k.get("value") or "").strip()
                if v:
                    out.append(v)
            elif isinstance(k, str) and k.strip():
                out.append(k.strip())
    # de-dupe while preserving order
    seen = set()
    deduped = []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def iter_cited_control_numbers(md: Dict[str, Any]) -> Iterable[int]:
    # INSPIRE references often contain: {"record": {"$ref": "https://.../api/literature/<cn>"}}
    refs = md.get("references") or []
    if not isinstance(refs, list):
        return
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        rec = ref.get("record") or {}
        if isinstance(rec, dict):
            href = rec.get("$ref") or rec.get("ref") or ""
            if isinstance(href, str):
                m = LIT_REF_RE.search(href.strip())
                if m:
                    yield int(m.group(1))


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS papers (
      control_number INTEGER PRIMARY KEY,
      title TEXT,
      abstract TEXT,
      date TEXT,
      arxiv_id TEXT,
      arxiv_cat TEXT,
      doi TEXT,
      inspire_url TEXT,
      updated_at_utc INTEGER
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS citations (
      citing INTEGER NOT NULL,
      cited  INTEGER NOT NULL,
      updated_at_utc INTEGER NOT NULL,
      PRIMARY KEY (citing, cited)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_citations_citing ON citations(citing)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_citations_cited  ON citations(cited)")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS paper_keywords (
      control_number INTEGER NOT NULL,
      keyword TEXT NOT NULL,
      source TEXT NOT NULL,
      updated_at_utc INTEGER NOT NULL,
      PRIMARY KEY (control_number, keyword, source)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keywords_kw ON paper_keywords(keyword)")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS meta (
      k TEXT PRIMARY KEY,
      v TEXT
    )
    """)
    conn.commit()


def upsert_paper(conn: sqlite3.Connection, recid: int, row: Dict[str, Any]) -> None:
    conn.execute("""
    INSERT INTO papers(control_number, title, abstract, date, arxiv_id, arxiv_cat, doi, inspire_url, updated_at_utc)
    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(control_number) DO UPDATE SET
      title=excluded.title,
      abstract=excluded.abstract,
      date=excluded.date,
      arxiv_id=excluded.arxiv_id,
      arxiv_cat=excluded.arxiv_cat,
      doi=excluded.doi,
      inspire_url=excluded.inspire_url,
      updated_at_utc=excluded.updated_at_utc
    """, (
        recid,
        row.get("title", ""),
        row.get("abstract", ""),
        row.get("date", ""),
        row.get("arxiv_id", ""),
        row.get("arxiv_cat", ""),
        row.get("doi", ""),
        row.get("inspire_url", ""),
        int(time.time()),
    ))


def insert_citations(conn: sqlite3.Connection, citing: int, cited_list: Iterable[int]) -> None:
    now = int(time.time())
    rows = [(citing, cited, now) for cited in cited_list if cited and cited != citing]
    if not rows:
        return
    conn.executemany("""
      INSERT OR IGNORE INTO citations(citing, cited, updated_at_utc)
      VALUES(?, ?, ?)
    """, rows)


def insert_keywords(conn: sqlite3.Connection, recid: int, keywords: list[str], source: str = "inspire") -> None:
    now = int(time.time())
    rows = [(recid, kw, source, now) for kw in keywords]
    if not rows:
        return
    conn.executemany("""
      INSERT OR REPLACE INTO paper_keywords(control_number, keyword, source, updated_at_utc)
      VALUES(?, ?, ?, ?)
    """, rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="inspire.sqlite")
    ap.add_argument("--query", default='collection:Literature and _exists_:abstracts')
    ap.add_argument("--size", type=int, default=1000)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.1)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    # Set busy timeout to 60 seconds (waits for locks instead of failing immediately)
    conn.execute("PRAGMA busy_timeout=60000")
    ensure_schema(conn)

    sess = requests.Session()
    sess.headers.update({"Accept": "application/json"})

    # requests handles URL encoding
    params = {"q": args.query, "size": args.size, "page": 1}
    r = get_with_retry(sess, sess.prepare_request(requests.Request("GET", API_BASE, params=params)).url)

    r.raise_for_status()
    data = r.json()
    next_url = data.get("links", {}).get("self")

    total_ingested = 0
    pbar = tqdm(total=args.max if args.max > 0 else None, unit="paper")

    while next_url:
        # Use get_with_retry for pagination too - critical for reliability
        r = get_with_retry(sess, next_url, timeout=60)
        r.raise_for_status()
        data = r.json()

        hits = data.get("hits", {}).get("hits", []) or []
        for h in hits:
            md = h.get("metadata") or {}
            recid = md.get("control_number")
            if not recid:
                continue

            title = pick_title(md)
            abstract = pick_abstract(md)
            date = pick_date(md)
            arxiv_id, arxiv_cat = pick_arxiv(md)
            doi = pick_doi(md)
            inspire_url = (h.get("links") or {}).get("self") or ""

            upsert_paper(conn, int(recid), {
                "title": title,
                "abstract": abstract,
                "date": date,
                "arxiv_id": arxiv_id,
                "arxiv_cat": arxiv_cat,
                "doi": doi,
                "inspire_url": inspire_url,
            })

            # Extract and store keywords
            kws = pick_keywords(md)
            insert_keywords(conn, int(recid), kws, source="inspire")

            # Extract and store citation edges (references = cited papers)
            cited = list(iter_cited_control_numbers(md))
            insert_citations(conn, int(recid), cited)

            total_ingested += 1
            pbar.update(1)

            if args.max and total_ingested >= args.max:
                conn.commit()
                pbar.close()
                return 0

        conn.commit()
        next_url = (data.get("links") or {}).get("next") or ""
        conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", ("last_next_url", next_url))
        conn.commit()

        if args.sleep:
            time.sleep(args.sleep)

        if not hits:
            break

    pbar.close()
    print(f"Done. Ingested/updated {total_ingested} records into {args.db}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
