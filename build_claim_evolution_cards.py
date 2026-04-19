#!/usr/bin/env python3
"""
Build canonical claim evolution cards from stress-test claim-evolution outputs.

Canonical source: data/claim_evolution_stress_test/ (richer relations and explanations).
Converts each paper's claims into a standard card format: claim text, relation counts,
representative examples per relation type, grounded interpretation, field_status, and
key_follow_up_papers (top 1–3 downstream papers per relation type by citation count).

Key follow-up selection: for each claim and relation type, group aligned citations by
child paper; score = number of statements; take top 3 papers per relation (deterministic:
sort by n_matches desc, then child_cn asc).

Output: data/claim_evolution_cards/{cn}.json, {cn}.md, all_cards.jsonl.

Usage:
  python build_claim_evolution_cards.py
  python build_claim_evolution_cards.py --paper 862424
  python build_claim_evolution_cards.py --report
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_SOURCE_DIR = "data/claim_evolution_stress_test"
DEFAULT_OUT_DIR = "data/claim_evolution_cards"
RELATION_TYPES = ("uses", "supports", "refines", "limits", "disputes")
MAX_REPRESENTATIVE_PER_TYPE = 3
MAX_KEY_FOLLOW_UPS_PER_RELATION = 3
MAX_REPRESENTATIVE_STATEMENTS_PER_FOLLOW_UP = 3


def field_status(relation_counts: dict[str, int]) -> str:
    """
    Assign a compact field_status label from counts. Rules (order matters):
    - contested: at least one dispute
    - adopted_with_limits: at least one limit (and no dispute, for clarity)
    - refined: at least one refine, no dispute
    - adopted: at least one use or support, no limit/refine/dispute
    - weak_signal: no aligned citations
    - mixed: has both adoption and limit/refine (handled by adopted_with_limits / refined)
    """
    u = relation_counts.get("uses", 0)
    s = relation_counts.get("supports", 0)
    r = relation_counts.get("refines", 0)
    l = relation_counts.get("limits", 0)
    d = relation_counts.get("disputes", 0)
    if d >= 1:
        return "contested"
    if l >= 1:
        return "adopted_with_limits"
    if r >= 1:
        return "refined"
    if u >= 1 or s >= 1:
        return "adopted"
    return "weak_signal"


def build_key_follow_up_papers(matches: list[dict]) -> dict[str, list[dict]]:
    """
    For each relation type, identify the top downstream papers by number of aligned citation
    statements (score = count per (child_cn, relation)). Deterministic: sort by (n_matches desc, child_cn asc).
    Returns key_follow_up_papers[rel] = list of up to MAX_KEY_FOLLOW_UPS_PER_RELATION papers, each with
    child_cn, child_title, n_matches, representative_statements (up to 3), why_selected.
    """
    # Group by (child_cn, relation) -> list of matches
    by_paper_rel: dict[tuple[int, str], list[dict]] = {}
    for m in matches:
        rel = m.get("relation")
        child_cn = m.get("child_cn")
        if rel not in RELATION_TYPES or child_cn is None:
            continue
        key = (child_cn, rel)
        if key not in by_paper_rel:
            by_paper_rel[key] = []
        by_paper_rel[key].append(m)

    # For each relation type: sort (child_cn, rel) groups by n_matches desc, child_cn asc; take top N papers
    out = {rel: [] for rel in RELATION_TYPES}
    for rel in RELATION_TYPES:
        groups = [(k, v) for k, v in by_paper_rel.items() if k[1] == rel]
        groups.sort(key=lambda x: (-len(x[1]), x[0][0]))  # n_matches desc, then child_cn asc
        for (child_cn, _), group_matches in groups[: MAX_KEY_FOLLOW_UPS_PER_RELATION]:
            first = group_matches[0]
            child_title = (first.get("child_title") or "")[:200]
            n_matches = len(group_matches)
            # Representative statements: up to 3, deduped by snippet
            stmts = []
            seen = set()
            for m in group_matches:
                s = (m.get("citation_statement") or "").strip()[:400]
                key = s[:80]
                if key in seen:
                    continue
                seen.add(key)
                stmts.append(s)
                if len(stmts) >= MAX_REPRESENTATIVE_STATEMENTS_PER_FOLLOW_UP:
                    break
            why = f"{n_matches} citation statement(s) aligned as {rel} (key follow-up for this claim)." if n_matches > 1 else f"Single downstream citation aligned as {rel}."
            out[rel].append({
                "child_cn": child_cn,
                "child_title": child_title,
                "n_matches": n_matches,
                "representative_statements": stmts,
                "why_selected": why,
            })
    return out


def key_follow_up_interpretation(key_follow_up: dict[str, list]) -> str:
    """Short grounded sentence(s) about key follow-up papers for this claim."""
    parts = []
    for rel in ("refines", "limits", "disputes", "supports", "uses"):
        papers = key_follow_up.get(rel) or []
        if not papers:
            continue
        p = papers[0]
        title = (p.get("child_title") or "").strip()
        if len(title) > 60:
            title = title[:57] + "..."
        n = p.get("n_matches", 0)
        if rel == "refines":
            parts.append(f"The strongest downstream refinement comes from: {title} ({n} citation(s)).")
        elif rel == "limits":
            parts.append(f"Later work that limits this claim is concentrated in: {title} ({n} citation(s)).")
        elif rel == "disputes":
            parts.append(f"Dispute is concentrated in: {title} ({n} citation(s)).")
        elif rel == "supports":
            if not any(key_follow_up.get(r) for r in ("refines", "limits", "disputes")):
                parts.append(f"Key supporting paper: {title} ({n} citation(s)).")
        elif rel == "uses":
            if not any(key_follow_up.get(r) for r in ("refines", "limits", "disputes", "supports")):
                parts.append(f"Key downstream use: {title} ({n} citation(s)).")
    return " ".join(parts) if parts else ""


def build_representative_examples(matches: list[dict]) -> dict[str, list[dict]]:
    """Group matches by relation type; for each type return up to MAX_REPRESENTATIVE_PER_TYPE items, deduped by citation snippet."""
    by_rel = {rel: [] for rel in RELATION_TYPES}
    for m in matches:
        rel = m.get("relation")
        if rel not in by_rel:
            continue
        item = {
            "child_cn": m.get("child_cn"),
            "child_title": (m.get("child_title") or "")[:200],
            "citation_statement": (m.get("citation_statement") or "")[:500],
        }
        if m.get("explanation"):
            item["explanation"] = (m.get("explanation") or "")[:300]
        by_rel[rel].append(item)

    # Dedupe by citation_statement (first 80 chars) and take up to N per type
    out = {}
    for rel in RELATION_TYPES:
        seen = set()
        kept = []
        for item in by_rel[rel]:
            key = (item.get("citation_statement") or "")[:80]
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
            if len(kept) >= MAX_REPRESENTATIVE_PER_TYPE:
                break
        out[rel] = kept
    return out


def claim_to_card(claim: dict) -> dict:
    """Turn one stress-test claim block into a claim evolution card (with key follow-up papers)."""
    summary = claim.get("summary") or {}
    relation_counts = {r: summary.get(r, 0) for r in RELATION_TYPES}
    matches = claim.get("matches") or []
    rep = build_representative_examples(matches)
    key_follow_up = build_key_follow_up_papers(matches)
    key_interp = key_follow_up_interpretation(key_follow_up)
    interpretation = (claim.get("interpretation") or claim.get("summary_sentence") or "").strip()
    if not interpretation:
        interpretation = "No citation statements in this set were aligned to this claim."
    card = {
        "claim_id": claim.get("claim_id", ""),
        "claim_text": (claim.get("claim_text") or "").strip(),
        "relation_counts": relation_counts,
        "representative_examples": rep,
        "interpretation": interpretation,
        "field_status": field_status(relation_counts),
        "key_follow_up_papers": key_follow_up,
        "key_follow_up_interpretation": key_interp,
    }
    return card


def build_cards_for_paper(source_path: Path) -> dict | None:
    """Load stress-test JSON and build card structure for one paper."""
    if not source_path.exists():
        return None
    with open(source_path, encoding="utf-8") as f:
        data = json.load(f)
    cards = [claim_to_card(c) for c in data.get("claims", [])]
    return {
        "control_number": data.get("control_number"),
        "title": (data.get("title") or "").strip(),
        "claims": cards,
        "_meta": {
            "source": "claim_evolution_stress_test",
            "n_claims": len(cards),
        },
    }


def write_markdown(card_doc: dict, out_path: Path) -> None:
    """Write one Markdown file per paper for human inspection (includes key follow-up papers)."""
    lines = [
        f"# Paper: {card_doc.get('title', '')}",
        f"\nControl number: {card_doc.get('control_number', '')}\n",
    ]
    for c in card_doc.get("claims", []):
        lines.append(f"## Claim {c.get('claim_id', '')}")
        lines.append(f"\n**Claim:** {c.get('claim_text', '')}\n")
        rc = c.get("relation_counts") or {}
        lines.append("**Counts:** " + ", ".join(f"{k}={rc.get(k, 0)}" for k in RELATION_TYPES) + "\n")
        lines.append(f"**Field status:** {c.get('field_status', '')}\n")
        lines.append(f"**Interpretation:** {c.get('interpretation', '')}\n")
        # Key follow-up papers
        kfu = c.get("key_follow_up_papers") or {}
        kfu_interp = (c.get("key_follow_up_interpretation") or "").strip()
        if kfu_interp:
            lines.append(f"**Key follow-up note:** {kfu_interp}\n")
        has_any = any(kfu.get(rel) for rel in RELATION_TYPES)
        if has_any:
            lines.append("**Key follow-up papers:**\n")
            for rel in RELATION_TYPES:
                papers = kfu.get(rel) or []
                if not papers:
                    continue
                lines.append(f"- **{rel.capitalize()}:**")
                for p in papers:
                    title = (p.get("child_title") or "").strip()
                    if len(title) > 70:
                        title = title[:67] + "..."
                    n = p.get("n_matches", 0)
                    lines.append(f"  - {title} (control_number: {p.get('child_cn')}, {n} citation(s)) — {p.get('why_selected', '')}")
                    for st in (p.get("representative_statements") or [])[:2]:
                        if len(st) > 180:
                            st = st[:177] + "..."
                        lines.append(f"    - \"{st}\"")
                lines.append("")
        lines.append("**Representative evidence:**\n")
        rep = c.get("representative_examples") or {}
        for rel in RELATION_TYPES:
            items = rep.get(rel) or []
            if not items:
                continue
            lines.append(f"- [{rel}]")
            for item in items:
                st = (item.get("citation_statement") or "").strip()
                if len(st) > 200:
                    st = st[:200] + "..."
                lines.append(f"  - {st}")
                if item.get("explanation"):
                    ex = item["explanation"]
                    lines.append(f"    _(Explanation: {ex[:150]}{'...' if len(ex) > 150 else ''})_")
            lines.append("")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def run_report(out_dir: Path) -> None:
    """Print coverage, card-quality, and key-follow-up stats."""
    if not out_dir.exists():
        print(f"Output dir not found: {out_dir}")
        return
    files = sorted(out_dir.glob("*.json"))
    files = [f for f in files if not f.name.startswith("all_")]
    if not files:
        print("No card JSONs found.")
        return
    n_papers = len(files)
    n_cards = 0
    n_zero_match = 0
    n_non_use_relation = 0
    n_limits_or_disputes = 0
    n_only_uses_supports = 0
    field_status_counts = {}
    # Key follow-up stats
    n_with_key_follow_up = 0
    n_with_key_refines = 0
    n_with_key_limits = 0
    n_with_key_disputes = 0
    n_with_key_only_uses_supports = 0
    n_weak_signal_no_follow_up = 0
    distinct_uses = set()
    distinct_supports = set()
    distinct_refines = set()
    distinct_limits = set()
    distinct_disputes = set()
    # Showcase candidates: (n_matches, paper_cn, paper_title, claim_id, claim_preview, follow_up_title)
    limits_cases = []
    disputes_cases = []
    refines_cases = []
    dominant_cases = []  # (max_n, paper_cn, paper_title, claim_id, claim_preview)
    for p in files:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        paper_cn = doc.get("control_number")
        paper_title = (doc.get("title") or "")[:60]
        for c in doc.get("claims", []):
            n_cards += 1
            rc = c.get("relation_counts") or {}
            total = sum(rc.get(r, 0) for r in RELATION_TYPES)
            if total == 0:
                n_zero_match += 1
            if (rc.get("refines") or 0) + (rc.get("limits") or 0) + (rc.get("disputes") or 0) >= 1:
                n_non_use_relation += 1
            if (rc.get("limits") or 0) >= 1 or (rc.get("disputes") or 0) >= 1:
                n_limits_or_disputes += 1
            if total >= 1 and (rc.get("refines") or 0) == 0 and (rc.get("limits") or 0) == 0 and (rc.get("disputes") or 0) == 0:
                n_only_uses_supports += 1
            fs = c.get("field_status", "")
            field_status_counts[fs] = field_status_counts.get(fs, 0) + 1
            # Key follow-up
            kfu = c.get("key_follow_up_papers") or {}
            has_any_kfu = any(kfu.get(rel) for rel in RELATION_TYPES)
            if has_any_kfu:
                n_with_key_follow_up += 1
            if kfu.get("refines"):
                n_with_key_refines += 1
            if kfu.get("limits"):
                n_with_key_limits += 1
            if kfu.get("disputes"):
                n_with_key_disputes += 1
            if has_any_kfu and (kfu.get("uses") or kfu.get("supports")) and not (kfu.get("refines") or kfu.get("limits") or kfu.get("disputes")):
                n_with_key_only_uses_supports += 1
            for rel in RELATION_TYPES:
                for paper in (kfu.get(rel) or []):
                    cn = paper.get("child_cn")
                    if cn is None:
                        continue
                    if rel == "uses":
                        distinct_uses.add(cn)
                    elif rel == "supports":
                        distinct_supports.add(cn)
                    elif rel == "refines":
                        distinct_refines.add(cn)
                    elif rel == "limits":
                        distinct_limits.add(cn)
                    elif rel == "disputes":
                        distinct_disputes.add(cn)
            if not has_any_kfu and total == 0:
                n_weak_signal_no_follow_up += 1
            # Showcase: for limits/disputes/refines, take top paper (by n_matches)
            claim_preview = (c.get("claim_text") or "")[:80].replace("\n", " ")
            if len(claim_preview) >= 80:
                claim_preview = claim_preview[:77] + "..."
            for rel, lst in [("limits", limits_cases), ("disputes", disputes_cases), ("refines", refines_cases)]:
                papers = kfu.get(rel) or []
                if papers:
                    top = papers[0]
                    lst.append((top.get("n_matches", 0), paper_cn, paper_title, c.get("claim_id", ""), claim_preview, (top.get("child_title") or "")[:50]))
            # Dominant: one downstream paper has 2+ matches and is the only key paper for that relation (or has majority)
            for rel in RELATION_TYPES:
                papers = kfu.get(rel) or []
                if len(papers) == 1 and papers[0].get("n_matches", 0) >= 2:
                    dominant_cases.append((papers[0].get("n_matches", 0), paper_cn, paper_title, c.get("claim_id", ""), claim_preview, (papers[0].get("child_title") or "")[:50], rel))
    print("Claim evolution cards: coverage")
    print("=" * 50)
    print(f"  Papers processed: {n_papers}")
    print(f"  Claims -> cards: {n_cards}")
    print(f"  Claims with zero downstream matches: {n_zero_match}")
    print(f"  Claims with at least one non-use relation (refines/limits/disputes): {n_non_use_relation}")
    print(f"  Claims with limits or disputes: {n_limits_or_disputes}")
    print(f"  Claims with only uses/supports (and at least one match): {n_only_uses_supports}")
    print(f"  Field status distribution: {field_status_counts}")
    print()
    print("Key follow-up papers: coverage")
    print("=" * 50)
    print(f"  Claims with at least one key follow-up paper: {n_with_key_follow_up}")
    print(f"  Claims with key papers in refines: {n_with_key_refines}")
    print(f"  Claims with key papers in limits: {n_with_key_limits}")
    print(f"  Claims with key papers in disputes: {n_with_key_disputes}")
    print(f"  Claims with only uses/supports follow-ups: {n_with_key_only_uses_supports}")
    print(f"  Weak-signal claims (no matches, no follow-up papers): {n_weak_signal_no_follow_up}")
    print()
    print("Distinct downstream papers as key follow-up (totals across all claims)")
    print("=" * 50)
    print(f"  key uses papers: {len(distinct_uses)}")
    print(f"  key supports papers: {len(distinct_supports)}")
    print(f"  key refines papers: {len(distinct_refines)}")
    print(f"  key limits papers: {len(distinct_limits)}")
    print(f"  key disputes papers: {len(distinct_disputes)}")
    print()
    print("Showcase cases (key follow-up papers)")
    print("=" * 50)
    for label, cases in [
        ("Strong 'limits' follow-up (top 2 by n_matches)", sorted(limits_cases, key=lambda x: -x[0])[:2]),
        ("Strong 'disputes' follow-up (top 2)", sorted(disputes_cases, key=lambda x: -x[0])[:2]),
        ("Strong 'refines' follow-up (top 2)", sorted(refines_cases, key=lambda x: -x[0])[:2]),
        ("One paper dominates (top 2)", sorted(dominant_cases, key=lambda x: -x[0])[:2]),
    ]:
        print(f"  {label}:")
        for t in cases:
            if not t:
                continue
            if len(t) == 7:
                n, pcn, ptitle, cid, preview, follow_title, rel = t
                print(f"    Paper {pcn} Claim {cid} [{rel}]: \"{follow_title}\" ({n} citation(s))")
            else:
                n, pcn, ptitle, cid, preview, follow_title = t
                print(f"    Paper {pcn} Claim {cid}: \"{preview}\" -> \"{follow_title}\" ({n} citation(s))")
        if not cases:
            print("    (none)")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build claim evolution cards from stress-test output")
    ap.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR, help="Stress-test JSON directory")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory for cards")
    ap.add_argument("--paper", type=int, default=None, help="Process only this control number")
    ap.add_argument("--report", action="store_true", help="Print coverage report only")
    args = ap.parse_args()

    source_dir = Path(args.source_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.report:
        run_report(out_dir)
        return

    if not source_dir.exists():
        print(f"Source dir not found: {source_dir}")
        return

    sources = sorted(source_dir.glob("*.json"))
    sources = [s for s in sources if not s.name.startswith("all_")]
    if args.paper:
        sources = [s for s in sources if s.stem == str(args.paper)]
    if not sources:
        print("No source JSONs found.")
        return

    for src in sources:
        card_doc = build_cards_for_paper(src)
        if not card_doc:
            continue
        cn = card_doc.get("control_number")
        if cn is None:
            continue
        out_json = out_dir / f"{cn}.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(card_doc, f, indent=2, ensure_ascii=False)
        print(f"Wrote {out_json}")
        write_markdown(card_doc, out_dir / f"{cn}.md")
        print(f"Wrote {out_dir / f'{cn}.md'}")

    # Aggregate JSONL
    jsonl_path = out_dir / "all_cards.jsonl"
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
