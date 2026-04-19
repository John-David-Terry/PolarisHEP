#!/usr/bin/env python3
"""
Claim-tracking benchmark: align paper-level claims to citation-level statements
and classify how later papers relate to each claim (uses, supports, refines, limits, disputes).

Inputs:
  - Paper-level claims from data/paper_statements/{cn}.json
  - Citation statements from edge_statements (parent_cn = benchmark paper)

Output: data/claim_tracking/{cn}.json per paper with claims, matches, relation counts, summaries.

Usage:
  python claim_tracking.py --db inspire.sqlite --paper 810127
  python claim_tracking.py --db inspire.sqlite
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

try:
    import openai
except ImportError:
    openai = None

# Default paths
DEFAULT_DB = "inspire.sqlite"
DEFAULT_PAPER_STATEMENTS_DIR = "data/paper_statements"
DEFAULT_OUT_DIR = "data/claim_tracking"

RELATION_TYPES = ("uses", "supports", "refines", "limits", "disputes", "unrelated")

# Benchmark papers: have paper-level extraction AND enough citation statements (parent_cn)
# 810127: MSTW PDFs, 39 citations; 862424: qT resummation/collinear anomaly, 13; 829121: NNLO parton, 10; 846542: NNPDF2.0, 5
BENCHMARK_PAPERS = (810127, 862424, 829121, 846542)


def load_paper_claims(paper_statements_dir: Path, control_number: int) -> dict | None:
    """Load paper-level claims from data/paper_statements/{cn}.json. Returns None if missing."""
    path = paper_statements_dir / f"{control_number}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    claims = data.get("claims") or []
    if not claims:
        return None
    return {
        "control_number": control_number,
        "title": data.get("title", ""),
        "claims": claims,
    }


def load_citation_statements(conn: sqlite3.Connection, parent_cn: int) -> list[dict]:
    """Load citation statements where parent_cn = cited paper (and get child title from papers)."""
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT es.child_cn, es.parent_cn, es.statement, pc.title AS child_title
            FROM edge_statements es
            LEFT JOIN papers pc ON pc.control_number = es.child_cn
            WHERE es.parent_cn = ?
        """, (parent_cn,))
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        cur.execute("SELECT child_cn, parent_cn, statement FROM edge_statements WHERE parent_cn = ?", (parent_cn,))
        rows = [(r[0], r[1], r[2], None) for r in cur.fetchall()]
    return [
        {"child_cn": r[0], "parent_cn": r[1], "statement": r[2], "child_title": r[3] or ""}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# LLM: claim–citation alignment and relation typing
# ---------------------------------------------------------------------------

RELATION_SYSTEM = """You are an expert at analyzing how scientific papers cite and use prior work.
Given a cited paper's claims and a sentence from a later paper that cites it, determine which claims (if any) the citation relates to and how.
Relation types:
- uses: the citing paper uses the claim as methodology, framework, or tool.
- supports: the citing paper supports or confirms the claim.
- refines: the citing paper refines, extends, or narrows the claim.
- limits: the citing paper limits the domain of validity, adds caveats, or restricts scope.
- disputes: the citing paper disputes, questions, or contradicts the claim.
Only assign a relation when the citation clearly refers to that claim. If the citation does not refer to any specific claim, return an empty list.
Output valid JSON only: a list of objects with keys "claim_id" (e.g. "C1") and "relation" (one of the types above)."""

RELATION_USER_TEMPLATE = """Cited paper title: {title}

Claims from the cited paper:
{claims_text}

Citation statement (from a later paper that cites the above):
"{citation_statement}"

Which claim(s) does this citation relate to, and how? Return a JSON array of {{ "claim_id": "C1", "relation": "uses" }}. Use claim_id C1, C2, ... as listed. If none, return []."""


def classify_citation_to_claims(
    title: str,
    claims_with_ids: list[tuple[str, str]],
    citation_statement: str,
    model: str = "gpt-4o-mini",
) -> list[dict]:
    """For one citation statement, return list of { claim_id, relation } for each claim it relates to."""
    if not citation_statement or not claims_with_ids:
        return []
    if openai is None:
        return []
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return []
    claims_text = "\n".join(f"{cid}: {text}" for cid, text in claims_with_ids)
    user = RELATION_USER_TEMPLATE.format(
        title=title,
        claims_text=claims_text,
        citation_statement=citation_statement[:2000],
    )
    client = openai.OpenAI(api_key=api_key)
    raw = ""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": RELATION_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n```\s*$", "", raw)
        out = json.loads(raw)
        if not isinstance(out, list):
            return []
        result = []
        for item in out:
            if not isinstance(item, dict):
                continue
            cid = item.get("claim_id")
            rel = (item.get("relation") or "").lower()
            if cid and rel and rel in RELATION_TYPES and rel != "unrelated":
                result.append({"claim_id": str(cid), "relation": rel})
        return result
    except (json.JSONDecodeError, Exception):
        return []


def run_claim_tracking_for_paper(
    conn: sqlite3.Connection,
    paper_data: dict,
    citations: list[dict],
    model: str,
) -> dict:
    """Build claim-tracking output for one paper: align citations to claims and type relations."""
    control_number = paper_data["control_number"]
    title = paper_data["title"]
    claims_raw = paper_data["claims"]
    # Assign claim IDs
    claims_with_ids = [(f"C{i+1}", (c.get("text") or "")[:800]) for i, c in enumerate(claims_raw)]
    claim_id_to_index = {cid: i for i, (cid, _) in enumerate(claims_with_ids)}

    # Per-claim: list of matches and summary counts
    n_claims = len(claims_raw)
    claim_matches = [[] for _ in range(n_claims)]
    for cit in citations:
        alignments = classify_citation_to_claims(title, claims_with_ids, cit["statement"], model=model)
        for a in alignments:
            cid = a.get("claim_id")
            rel = a.get("relation")
            if cid not in claim_id_to_index or not rel:
                continue
            idx = claim_id_to_index[cid]
            claim_matches[idx].append({
                "child_cn": cit["child_cn"],
                "child_title": (cit.get("child_title") or "")[:200],
                "citation_statement": (cit["statement"] or "")[:500],
                "relation": rel,
            })

    # Build output structure
    out_claims = []
    for i, c in enumerate(claims_raw):
        cid = f"C{i+1}"
        matches = claim_matches[i]
        summary = {r: sum(1 for m in matches if m["relation"] == r) for r in RELATION_TYPES if r != "unrelated"}
        # Grounded summary sentence from counts
        parts = []
        if summary.get("uses", 0) > 0:
            parts.append(f"{summary['uses']} later paper(s) use this as methodology or framework")
        if summary.get("supports", 0) > 0:
            parts.append(f"{summary['supports']} support or confirm it")
        if summary.get("refines", 0) > 0:
            parts.append(f"{summary['refines']} refine or extend it")
        if summary.get("limits", 0) > 0:
            parts.append(f"{summary['limits']} limit its domain or add caveats")
        if summary.get("disputes", 0) > 0:
            parts.append(f"{summary['disputes']} dispute or question it")
        if not parts:
            summary_sentence = "No citation statements in this set were aligned to this claim."
        else:
            summary_sentence = (" and ".join(parts) if len(parts) > 1 else parts[0]) + "."
        out_claims.append({
            "claim_id": cid,
            "claim_text": c.get("text", ""),
            "matches": matches,
            "summary": summary,
            "summary_sentence": summary_sentence,
        })

    return {
        "control_number": control_number,
        "title": title,
        "claims": out_claims,
        "_meta": {
            "n_claims": n_claims,
            "n_citation_statements": len(citations),
        },
    }


def compute_meta_aligned(out: dict) -> int:
    """Count distinct citations that were aligned to at least one claim."""
    seen = set()
    for cl in out.get("claims", []):
        for m in cl.get("matches", []):
            key = (m.get("child_cn"), (m.get("citation_statement") or "")[:100])
            seen.add(key)
    return len(seen)


def report_benchmark(out_dir: Path) -> None:
    """Print alignment coverage and relation distribution from existing JSONs."""
    if not out_dir.exists():
        print(f"Output dir not found: {out_dir}")
        return
    files = sorted(out_dir.glob("*.json"))
    files = [f for f in files if not f.name.startswith("all_")]
    if not files:
        print("No claim-tracking JSONs found.")
        return
    total_uses = total_supports = total_refines = total_limits = total_disputes = 0
    total_claims = total_citations = total_aligned = 0
    print("Paper selection and alignment coverage")
    print("-" * 60)
    for p in files:
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        cn = obj.get("control_number", 0)
        title = (obj.get("title") or "")[:50]
        n_claims = len(obj.get("claims", []))
        meta = obj.get("_meta", {})
        n_cit = meta.get("n_citation_statements", 0)
        n_aligned = compute_meta_aligned(obj)
        total_claims += n_claims
        total_citations += n_cit
        total_aligned += n_aligned
        print(f"  {cn} {title}...")
        print(f"    claims: {n_claims}, citation statements: {n_cit}, aligned: {n_aligned}")
        u = s = r = l = d = 0
        for cl in obj.get("claims", []):
            for m in cl.get("matches", []):
                rel = m.get("relation", "")
                if rel == "uses": u += 1
                elif rel == "supports": s += 1
                elif rel == "refines": r += 1
                elif rel == "limits": l += 1
                elif rel == "disputes": d += 1
        total_uses += u
        total_supports += s
        total_refines += r
        total_limits += l
        total_disputes += d
        print(f"    relations: uses={u}, supports={s}, refines={r}, limits={l}, disputes={d}")
    print("-" * 60)
    print("Totals")
    print(f"  papers: {len(files)}, claims: {total_claims}, citation statements: {total_citations}, aligned: {total_aligned}")
    print(f"  uses: {total_uses}, supports: {total_supports}, refines: {total_refines}, limits: {total_limits}, disputes: {total_disputes}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Claim-tracking: align claims to citation statements and type relations")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite database with edge_statements and papers")
    ap.add_argument("--paper-statements-dir", default=DEFAULT_PAPER_STATEMENTS_DIR, help="Directory of paper_statements JSONs")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory for claim-tracking JSONs")
    ap.add_argument("--paper", type=int, default=None, help="Run for this control number only")
    ap.add_argument("--model", default="gpt-4o-mini", help="OpenAI model for classification")
    ap.add_argument("--report", action="store_true", help="Only print benchmark report from existing JSONs in --out-dir")
    args = ap.parse_args()

    if args.report:
        report_benchmark(Path(args.out_dir))
        return

    if openai is None or not os.environ.get("OPENAI_API_KEY"):
        print("Warning: openai not installed or OPENAI_API_KEY not set. Exiting.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    paper_statements_dir = Path(args.paper_statements_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    papers_to_run = [args.paper] if args.paper else list(BENCHMARK_PAPERS)
    for cn in papers_to_run:
        paper_data = load_paper_claims(paper_statements_dir, cn)
        if not paper_data:
            print(f"Skip {cn}: no paper-level claims", file=sys.stderr)
            continue
        citations = load_citation_statements(conn, cn)
        if not citations:
            print(f"Skip {cn}: no citation statements", file=sys.stderr)
            continue
        print(f"Processing {cn} ({paper_data['title'][:50]}...): {len(paper_data['claims'])} claims, {len(citations)} citations")
        out = run_claim_tracking_for_paper(conn, paper_data, citations, model=args.model)
        out["_meta"]["n_aligned"] = compute_meta_aligned(out)
        out_path = out_dir / f"{cn}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"  Wrote {out_path}")

    conn.close()

    # Aggregate JSONL
    jsonl_path = out_dir / "all_claim_tracking.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as jf:
        for p in sorted(out_dir.glob("*.json")):
            if p.name.startswith("all_"):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    obj = json.load(f)
                jf.write(json.dumps(obj, ensure_ascii=False) + "\n")
            except Exception:
                pass
    print(f"Wrote {jsonl_path}")


if __name__ == "__main__":
    main()
