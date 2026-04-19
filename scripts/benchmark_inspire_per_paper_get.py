#!/usr/bin/env python3
"""
One-off benchmark: bulk search -> recids needing full record -> timed per-paper GETs.
"""
from __future__ import annotations

import random
import re
import sys
import time
from typing import Any

import requests

BULK_URL = "https://inspirehep.net/api/literature"
PAPER_URL = "https://inspirehep.net/api/literature/{recid}"
RETRY_CODES = {429, 500, 502, 503, 504}
LIT_REF_RE = re.compile(r"/api/literature/(\d+)$")


def get_with_retry(
    sess: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
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


def pick_abstract(md: dict[str, Any]) -> str:
    abstracts = md.get("abstracts") or []
    if abstracts and isinstance(abstracts, list):
        return (abstracts[0].get("value") or "").strip()
    return ""


def refs_missing_or_empty(md: dict[str, Any]) -> bool:
    if "references" not in md:
        return True
    refs = md.get("references")
    if not isinstance(refs, list):
        return True
    return len(refs) == 0


def abstract_missing(md: dict[str, Any]) -> bool:
    return not pick_abstract(md)


def iter_cited_recids(md: dict[str, Any]) -> list[int]:
    out: list[int] = []
    refs = md.get("references") or []
    if not isinstance(refs, list):
        return out
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        rec = ref.get("record") or {}
        if isinstance(rec, dict):
            href = (rec.get("$ref") or rec.get("ref") or "").strip()
            if isinstance(href, str):
                m = LIT_REF_RE.search(href)
                if m:
                    out.append(int(m.group(1)))
    return out


def pick_arxiv(md: dict[str, Any]) -> str:
    arx = md.get("arxiv_eprints") or []
    if arx and isinstance(arx, list):
        return (arx[0].get("value") or "").strip()
    return ""


def pick_doi(md: dict[str, Any]) -> str:
    dois = md.get("dois") or []
    if dois and isinstance(dois, list):
        return (dois[0].get("value") or "").strip()
    return ""


def main() -> int:
    sess = requests.Session()
    sess.headers.update({"Accept": "application/json", "User-Agent": "PolarisHEP-benchmark/1.0"})

    r = get_with_retry(sess, BULK_URL, params={"q": "control_number:1->2000000", "size": 1000})
    bulk = r.json()
    hits = (bulk.get("hits") or {}).get("hits") or []

    need_full: list[int] = []
    for h in hits:
        md = h.get("metadata") or {}
        cn = md.get("control_number")
        if cn is None:
            continue
        rid = int(cn)
        if refs_missing_or_empty(md) or abstract_missing(md):
            need_full.append(rid)

    # First 1000 from filter (or all if fewer — e.g. only one bulk page of 1000 may yield <1000).
    target = need_full[:1000]

    if not target:
        print("No recids matched filter.", file=sys.stderr)
        return 1

    t0 = time.perf_counter()

    total_refs = 0
    with_abstract = 0
    with_refs = 0
    papers_fetched = 0

    for recid in target:
        url = PAPER_URL.format(recid=recid)
        rr = get_with_retry(sess, url)
        data = rr.json()
        md = data.get("metadata") or {}
        papers_fetched += 1

        if pick_abstract(md):
            with_abstract += 1

        refs = md.get("references") or []
        if isinstance(refs, list) and len(refs) > 0:
            with_refs += 1

        cited = iter_cited_recids(md)
        total_refs += len(cited)

    t1 = time.perf_counter()

    total_time = t1 - t0
    n = max(papers_fetched, 1)
    pct_abs = 100.0 * with_abstract / n
    pct_refs = 100.0 * with_refs / n
    sec_per = total_time / n
    avg_refs = total_refs / n

    print("=== per-paper GET benchmark ===")
    print(f"bulk_hits: {len(hits)}")
    print(f"recids_matching_filter (refs missing/empty OR abstract missing): {len(need_full)}")
    print(f"papers_fetched: {papers_fetched}")
    print(f"total_time_seconds: {total_time:.4f}")
    print(f"seconds_per_paper: {sec_per:.6f}")
    print(f"percent_with_abstract_now: {pct_abs:.2f}")
    print(f"percent_with_references_now: {pct_refs:.2f}")
    print(f"average_references_per_paper (parsed cited recids): {avg_refs:.4f}")
    print(f"total_references_cited_recids: {total_refs}")
    print(f"recids_in_target_list (first 1000 needing full): {len(target)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
