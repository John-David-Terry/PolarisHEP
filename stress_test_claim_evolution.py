#!/usr/bin/env python3
"""
Claim-evolution stress test: targeted benchmark to surface refines, limits, disputes—
not just uses/supports. Uses revised relation-typing prompt and adds explanation per match.

Inputs: same as claim_tracking (paper_statements + edge_statements).
Output: data/claim_evolution_stress_test/{cn}.json with claims, matches (relation + explanation),
        claim evolution cards (summary + interpretation).

Usage:
  python stress_test_claim_evolution.py --db inspire.sqlite
  python stress_test_claim_evolution.py --db inspire.sqlite --paper 862424
  python stress_test_claim_evolution.py --report
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

DEFAULT_DB = "inspire.sqlite"
DEFAULT_PAPER_STATEMENTS_DIR = "data/paper_statements"
DEFAULT_STRESS_TEST_OUT = "data/claim_evolution_stress_test"
PREVIOUS_BENCHMARK_DIR = "data/claim_tracking"

RELATION_TYPES = ("uses", "supports", "refines", "limits", "disputes", "unrelated")

# Stress-test papers: chosen to maximize chance of refines/limits/disputes.
# All have paper_statements + at least 1 citation in edge_statements.
# Order: formalism/domain/qualification-heavy first.
STRESS_TEST_PAPERS = (
    862424,  # Drell-Yan small qT, collinear anomaly — factorization subtleties
    763778,  # Renormalization, Wilson lines, TMD PDFs — formalism later refined
    779762,  # Wilson lines and TMD PDFs: renormalization-group analysis — gauge-link subtleties
    829121,  # 3,4,5-flavor NNLO parton — scheme/domain
    823754,  # Observation of Sivers effect in DIS — claim that gets qualified
    618943,  # Exponentiation Drell-Yan near threshold — domain/validity
    846542,  # NNPDF2.0 — methodology + consistency claims
    877524,  # FEWZ 2.0 — code/methodology
    810127,  # MSTW PDFs — contrast: methodology-heavy (expect uses)
)


def load_paper_claims(paper_statements_dir: Path, control_number: int) -> dict | None:
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
# Revised prompt: explicitly surface refines, limits, disputes; require explanation
# ---------------------------------------------------------------------------

STRESS_RELATION_SYSTEM = """You are an expert at analyzing how scientific papers cite, qualify, and build on prior work.
Given a cited paper's claims and a sentence from a later paper that cites it, determine which claims (if any) the citation relates to and HOW.

Do NOT default to "uses" when the citation could instead reflect refinement, limitation, or dispute.
Consider each relation type explicitly:

- uses: the citing paper applies the claim directly as methodology, framework, or tool (e.g. "we use the PDF set from [X]").
- supports: the citing paper confirms the claim or cites a result as consistent with it.
- refines: the citing paper extends, sharpens, or reformulates the claim (e.g. adds Wilson-line structure, different scheme, improved formalism).
- limits: the citing paper restricts the claim's domain of validity, adds conditions, or states where it breaks down (e.g. "only valid for ...", "breaks down when ...").
- disputes: the citing paper challenges, questions, or contradicts the claim (e.g. "in contrast to [X]", "however [X] argued ...").

For each (claim_id, relation) you assign, provide a short "explanation" (one sentence) grounded in the citation text.
Output valid JSON only: a list of objects with keys "claim_id" (e.g. "C1"), "relation" (one of the types above), and "explanation" (short string). If the citation does not refer to any specific claim, return []."""

STRESS_USER_TEMPLATE = """Cited paper title: {title}

Claims from the cited paper:
{claims_text}

Citation statement (from a later paper that cites the above):
"{citation_statement}"

Which claim(s) does this citation relate to, and how? For each, give relation (uses/supports/refines/limits/disputes) and a short explanation. Return a JSON array of {{ "claim_id": "C1", "relation": "...", "explanation": "..." }}. Use claim_id C1, C2, ... as listed. If none, return []."""


def classify_citation_to_claims_stress(
    title: str,
    claims_with_ids: list[tuple[str, str]],
    citation_statement: str,
    model: str = "gpt-4o-mini",
) -> list[dict]:
    """One citation -> list of { claim_id, relation, explanation }."""
    if not citation_statement or not claims_with_ids:
        return []
    if openai is None:
        return []
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return []
    claims_text = "\n".join(f"{cid}: {text}" for cid, text in claims_with_ids)
    user = STRESS_USER_TEMPLATE.format(
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
                {"role": "system", "content": STRESS_RELATION_SYSTEM},
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
            expl = (item.get("explanation") or "").strip()[:300]
            if cid and rel and rel in RELATION_TYPES and rel != "unrelated":
                result.append({"claim_id": str(cid), "relation": rel, "explanation": expl})
        return result
    except (json.JSONDecodeError, Exception):
        return []


def run_stress_test_for_paper(
    conn: sqlite3.Connection,
    paper_data: dict,
    citations: list[dict],
    model: str,
) -> dict:
    control_number = paper_data["control_number"]
    title = paper_data["title"]
    claims_raw = paper_data["claims"]
    claims_with_ids = [(f"C{i+1}", (c.get("text") or "")[:800]) for i, c in enumerate(claims_raw)]
    claim_id_to_index = {cid: i for i, (cid, _) in enumerate(claims_with_ids)}
    n_claims = len(claims_raw)
    claim_matches = [[] for _ in range(n_claims)]

    for cit in citations:
        alignments = classify_citation_to_claims_stress(
            title, claims_with_ids, cit["statement"], model=model
        )
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
                "explanation": (a.get("explanation") or "").strip()[:300],
            })

    out_claims = []
    for i, c in enumerate(claims_raw):
        cid = f"C{i+1}"
        matches = claim_matches[i]
        summary = {r: sum(1 for m in matches if m["relation"] == r) for r in RELATION_TYPES if r != "unrelated"}
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
        # Representative examples (first 4 matches)
        representative = matches[:4]
        out_claims.append({
            "claim_id": cid,
            "claim_text": c.get("text", ""),
            "matches": matches,
            "summary": summary,
            "summary_sentence": summary_sentence,
            "representative_matches": representative,
            "interpretation": summary_sentence,
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
    seen = set()
    for cl in out.get("claims", []):
        for m in cl.get("matches", []):
            key = (m.get("child_cn"), (m.get("citation_statement") or "")[:100])
            seen.add(key)
    return len(seen)


def report_stress(out_dir: Path) -> None:
    """Print stress-test stats and compare to previous benchmark."""
    if not out_dir.exists():
        print(f"Output dir not found: {out_dir}")
        return
    files = sorted(out_dir.glob("*.json"))
    files = [f for f in files if not f.name.startswith("all_")]
    if not files:
        print("No stress-test JSONs found.")
        return

    totals = {"uses": 0, "supports": 0, "refines": 0, "limits": 0, "disputes": 0}
    total_claims = total_citations = total_aligned = 0

    print("Claim-evolution stress test: alignment and relation distribution")
    print("=" * 60)
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
        u = s = r = l = d = 0
        for cl in obj.get("claims", []):
            for m in cl.get("matches", []):
                rel = m.get("relation", "")
                if rel == "uses": u += 1
                elif rel == "supports": s += 1
                elif rel == "refines": r += 1
                elif rel == "limits": l += 1
                elif rel == "disputes": d += 1
        totals["uses"] += u
        totals["supports"] += s
        totals["refines"] += r
        totals["limits"] += l
        totals["disputes"] += d
        print(f"  {cn} {title}...")
        print(f"    claims: {n_claims}, citations: {n_cit}, aligned: {n_aligned}")
        print(f"    uses={u}, supports={s}, refines={r}, limits={l}, disputes={d}")

    print("-" * 60)
    print("Stress-test totals")
    print(f"  papers: {len(files)}, claims: {total_claims}, citations: {total_citations}, aligned: {total_aligned}")
    print(f"  uses: {totals['uses']}, supports: {totals['supports']}, refines: {totals['refines']}, limits: {totals['limits']}, disputes: {totals['disputes']}")

    # Compare to previous benchmark
    prev_dir = out_dir.parent / PREVIOUS_BENCHMARK_DIR
    if prev_dir.exists():
        prev_totals = {"uses": 0, "supports": 0, "refines": 0, "limits": 0, "disputes": 0}
        for q in prev_dir.glob("*.json"):
            if q.name.startswith("all_"):
                continue
            try:
                with open(q, encoding="utf-8") as f:
                    o = json.load(f)
                for cl in o.get("claims", []):
                    for m in cl.get("matches", []):
                        rel = m.get("relation", "")
                        if rel in prev_totals:
                            prev_totals[rel] += 1
            except Exception:
                pass
        print()
        print("Comparison to previous claim-tracking benchmark")
        print("-" * 60)
        print("  Previous (4 papers): uses=%d, supports=%d, refines=%d, limits=%d, disputes=%d"
              % (prev_totals["uses"], prev_totals["supports"], prev_totals["refines"], prev_totals["limits"], prev_totals["disputes"]))
        print("  Stress test (9 papers): uses=%d, supports=%d, refines=%d, limits=%d, disputes=%d"
              % (totals["uses"], totals["supports"], totals["refines"], totals["limits"], totals["disputes"]))
        more_refines = totals["refines"] > prev_totals["refines"]
        more_limits = totals["limits"] > prev_totals["limits"]
        more_disputes = totals["disputes"] > prev_totals["disputes"]
        print("  More refines than previous: %s | More limits: %s | More disputes: %s"
              % (more_refines, more_limits, more_disputes))


def main() -> None:
    ap = argparse.ArgumentParser(description="Claim-evolution stress test: surface refines/limits/disputes")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite database")
    ap.add_argument("--paper-statements-dir", default=DEFAULT_PAPER_STATEMENTS_DIR, help="Paper statements JSONs")
    ap.add_argument("--out-dir", default=DEFAULT_STRESS_TEST_OUT, help="Output directory")
    ap.add_argument("--paper", type=int, default=None, help="Run for this control number only")
    ap.add_argument("--all", action="store_true", help="Run on all processable papers (have paper_statements + ≥1 citation in DB)")
    ap.add_argument("--model", default="gpt-4o-mini", help="OpenAI model")
    ap.add_argument("--report", action="store_true", help="Print report and comparison only")
    args = ap.parse_args()

    if args.report:
        report_stress(Path(args.out_dir))
        return

    if openai is None or not os.environ.get("OPENAI_API_KEY"):
        print("Warning: openai / OPENAI_API_KEY required.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    paper_statements_dir = Path(args.paper_statements_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.paper:
        papers_to_run = [args.paper]
    elif args.all:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT parent_cn FROM edge_statements")
        parents_with_citations = {r[0] for r in cur.fetchall()}
        papers_to_run = []
        for p in sorted(paper_statements_dir.glob("*.json")):
            if p.name.startswith("all_"):
                continue
            try:
                cn = int(p.stem)
            except ValueError:
                continue
            if cn not in parents_with_citations:
                continue
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if not (data.get("claims") and data.get("_meta", {}).get("extraction_succeeded")):
                continue
            papers_to_run.append(cn)
        papers_to_run.sort()
        print(f"Running stress test on {len(papers_to_run)} processable papers (have claims + ≥1 citation).")
    else:
        papers_to_run = list(STRESS_TEST_PAPERS)
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
        out = run_stress_test_for_paper(conn, paper_data, citations, model=args.model)
        out["_meta"]["n_aligned"] = compute_meta_aligned(out)
        out_path = out_dir / f"{cn}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"  Wrote {out_path}")

    conn.close()

    jsonl_path = out_dir / "all_stress_test.jsonl"
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
