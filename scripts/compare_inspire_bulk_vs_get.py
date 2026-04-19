#!/usr/bin/env python3
"""
Apples-to-apples: same 1000 recids from bulk search vs per-paper GET metadata presence.
"""
from __future__ import annotations

import random
import sys
import time
from typing import Any

import requests

BULK_URL = "https://inspirehep.net/api/literature"
RETRY_CODES = {429, 500, 502, 503, 504}


def get_with_retry(
    sess: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 120,
    max_tries: int = 8,
) -> requests.Response:
    for attempt in range(1, max_tries + 1):
        try:
            r = sess.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                try:
                    _ = r.json()
                    return r
                except ValueError:
                    pass
            if r.status_code in RETRY_CODES:
                pass
            else:
                r.raise_for_status()
        except requests.RequestException:
            pass
        sleep = min(2**attempt, 30) * (0.7 + 0.6 * random.random())
        time.sleep(sleep)
    raise RuntimeError(f"Failed after {max_tries} retries: {url}")


def extract_flags(md: dict[str, Any]) -> dict[str, Any]:
    """Same field semantics for bulk and GET."""
    abstracts = md.get("abstracts") or []
    has_abstract = False
    if isinstance(abstracts, list) and abstracts:
        has_abstract = bool((abstracts[0].get("value") or "").strip())

    refs = md.get("references")
    has_references = isinstance(refs, list) and len(refs) > 0

    kws = md.get("keywords") or []
    has_keywords = False
    if isinstance(kws, list) and kws:
        for k in kws:
            if isinstance(k, dict) and (k.get("value") or "").strip():
                has_keywords = True
                break
            if isinstance(k, str) and k.strip():
                has_keywords = True
                break

    dois = md.get("dois") or []
    has_doi = (
        isinstance(dois, list)
        and len(dois) > 0
        and bool((dois[0].get("value") or "").strip())
    )

    arx = md.get("arxiv_eprints") or []
    has_arxiv = (
        isinstance(arx, list)
        and len(arx) > 0
        and bool((arx[0].get("value") or "").strip())
    )

    has_cc = "citation_count" in md and md.get("citation_count") is not None

    return {
        "has_abstract": has_abstract,
        "has_references": has_references,
        "has_keywords": has_keywords,
        "has_doi": has_doi,
        "has_arxiv": has_arxiv,
        "has_citation_count": has_cc,
        "citation_count": md.get("citation_count"),
    }


def pct(num: int, den: int) -> float:
    if den == 0:
        return 0.0
    return 100.0 * num / den


def main() -> int:
    sess = requests.Session()
    sess.headers.update({"Accept": "application/json", "User-Agent": "PolarisHEP-compare/1.0"})

    # STEP 1 + bulk timing
    t_bulk0 = time.perf_counter()
    r = get_with_retry(sess, BULK_URL, params={"q": "control_number:1->2000000", "size": 1000})
    bulk_json = r.json()
    hits = (bulk_json.get("hits") or {}).get("hits") or []
    bulk_data: dict[int, dict[str, Any]] = {}
    recids: list[int] = []
    for h in hits:
        md = h.get("metadata") or {}
        cn = md.get("control_number")
        if cn is None:
            continue
        rid = int(cn)
        recids.append(rid)
        bulk_data[rid] = extract_flags(md)
    t_bulk1 = time.perf_counter()
    bulk_time = t_bulk1 - t_bulk0

    n = len(recids)
    if n == 0:
        print("No papers in bulk response.", file=sys.stderr)
        return 1

    # STEP 2 + per-paper timing (same recids, same order as bulk)
    t_get0 = time.perf_counter()
    get_data: dict[int, dict[str, Any]] = {}
    for rid in recids:
        url = f"{BULK_URL}/{rid}"
        rr = get_with_retry(sess, url)
        md = (rr.json().get("metadata") or {})
        get_data[rid] = extract_flags(md)
    t_get1 = time.perf_counter()
    get_time = t_get1 - t_get0

    # STEP 3 — field-by-field
    fields = [
        ("abstract", "has_abstract"),
        ("references", "has_references"),
        ("keywords", "has_keywords"),
        ("doi", "has_doi"),
        ("arxiv", "has_arxiv"),
        ("citation_count", "has_citation_count"),
    ]

    rows: list[tuple[str, float, float, float, float]] = []
    for label, key in fields:
        b_pres = g_pres = b_miss_g_has = b_pres_g_miss = 0
        for rid in recids:
            b = bool(bulk_data[rid][key])
            g = bool(get_data[rid][key])
            if b:
                b_pres += 1
            if g:
                g_pres += 1
            if (not b) and g:
                b_miss_g_has += 1
            if b and (not g):
                b_pres_g_miss += 1
        rows.append(
            (
                label,
                pct(b_pres, n),
                pct(g_pres, n),
                pct(b_miss_g_has, n),
                pct(b_pres_g_miss, n),
            )
        )

    bulk_per_1000 = 1000 * bulk_time / n
    get_per_1000 = 1000 * get_time / n

    print()
    print("| Field | Bulk % | GET % | Bulk missed but GET had % | Bulk had but GET missed % |")
    print("| ----- | ------ | ----- | ------------------------- | ------------------------- |")
    for label, pb, pg, miss, sanity in rows:
        print(f"| {label} | {pb:.2f} | {pg:.2f} | {miss:.2f} | {sanity:.2f} |")
    print()
    print("Timing (same n papers):")
    print(f"  Bulk (search + parse): {bulk_time:.4f} s total → {bulk_per_1000:.4f} s per 1000 papers")
    print(f"  Per-paper GET (sequential): {get_time:.4f} s total → {get_per_1000:.4f} s per 1000 papers")
    print(f"  n = {n} recids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
