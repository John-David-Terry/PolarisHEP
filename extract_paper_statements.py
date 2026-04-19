#!/usr/bin/env python3
"""
Paper-level statement extraction for Polaris top-200 papers.

Reads TEI from data/tei/top200/, extracts abstract + intro/conclusion-style
sections, runs an LLM to produce structured claims/methods/assumptions/
limitations/results with evidence snippets. Writes one JSON per paper to
data/paper_statements/.

Text source: existing GROBID TEI for top-200 papers (data/tei/top200/).
If TEI is missing for a paper, that paper is skipped.

Usage:
  python extract_paper_statements.py --manifest top200_manifest_fixed.csv --tei-dir data/tei/top200
  python extract_paper_statements.py --limit 5
  python extract_paper_statements.py --paper 810127
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    etree = None

# Optional: OpenAI for extraction
try:
    import openai
except ImportError:
    openai = None

# Default paths (repo root)
DEFAULT_MANIFEST = "top200_manifest_fixed.csv"
DEFAULT_TEI_DIR = "data/tei/top200"
DEFAULT_OUT_DIR = "data/paper_statements"
# Max characters of paper text to send to the LLM (abstract + body)
MAX_PAPER_CHARS = 14_000

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


# ---------------------------------------------------------------------------
# TEI text extraction
# ---------------------------------------------------------------------------

def _text_of_el(el) -> str:
    """Get plain text from an element, collapsing whitespace; replace formula with placeholder."""
    if el is None:
        return ""
    parts = []
    for node in el.iter():
        if node.tag.endswith("}formula") or (node.tag == "formula"):
            parts.append(" [formula] ")
            continue
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def get_text_from_tei(tei_path: str | Path, max_chars: int = MAX_PAPER_CHARS) -> str:
    """
    Extract abstract + body text from a GROBID TEI file.
    Prefers: abstract (full), then body sections in order (Introduction, then
    others, stopping when total length reaches max_chars).
    """
    if etree is None:
        raise RuntimeError("lxml is required for TEI extraction; install with: pip install lxml")
    path = Path(tei_path)
    if not path.exists():
        raise FileNotFoundError(tei_path)
    root = etree.parse(str(path)).getroot()
    out = []

    # Abstract
    for p in root.xpath(".//tei:abstract//tei:p", namespaces=TEI_NS):
        out.append(_text_of_el(p))
    abstract_block = "\n\n".join(s for s in out if s)
    if abstract_block:
        out = [abstract_block]
    else:
        out = []

    # Body: top-level divs (section-level)
    body = root.xpath(".//tei:body", namespaces=TEI_NS)
    if not body:
        return "\n\n".join(out)[:max_chars] if out else ""

    for div in body[0].xpath("./tei:div", namespaces=TEI_NS):
        head_el = div.xpath("./tei:head", namespaces=TEI_NS)
        head = head_el[0].text or "" if head_el else ""
        if head:
            head = _text_of_el(head_el[0])
        paras = []
        for p in div.xpath(".//tei:p", namespaces=TEI_NS):
            paras.append(_text_of_el(p))
        section_text = (f"\n\n## {head}\n\n" if head else "\n\n") + "\n\n".join(paras)
        out.append(section_text)

    combined = "\n\n".join(out)
    if len(combined) > max_chars:
        combined = combined[: max_chars] + "\n\n[... text truncated ...]"
    return combined


# ---------------------------------------------------------------------------
# Benchmark paper set (high-impact, varied)
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: str) -> list[dict]:
    """Load top200 manifest CSV; return list of dicts with cn, title, arxiv_id, etc."""
    rows = []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            row["cn"] = int(row["cn"])
            rows.append(row)
    return rows


# Papers selected for benchmark: high indegree, mix of theory/experiment/TMD/PDF/resummation
BENCHMARK_CN = [
    810127,   # Parton distributions for the LHC (MSTW)
    729695,   # Collins and Sivers asymmetries (experiment)
    750627,   # kT factorization violation
    779762,   # Wilson lines and TMD PDFs
    763778,   # Renormalization, Wilson lines, TMD PDFs
    708985,   # Gauge-links
    594939,   # Soft gluon resummation (CSS-style)
    618943,   # Drell-Yan exponentiation
    711854,   # Collins effect SIDIS and e+e-
    698679,   # Sivers at RHIC
    823754,   # Sivers in DIS (experiment)
    771566,   # Azimuthal and single spin asymmetries
    846542,   # Global NLO PDF determination
    862424,   # Drell-Yan at small qT, collinear anomaly
    693371,   # Threshold resummation Higgs EFT
    877524,   # FEWZ 2.0
    829121,   # NNLO parton from DIS
    713783,   # Single transverse-spin in Drell-Yan
    789754,   # TMD in diquark spectator model
]


def get_benchmark_papers(manifest_rows: list[dict], limit: int | None = None) -> list[dict]:
    """Return manifest entries for benchmark control numbers, optionally limited."""
    by_cn = {r["cn"]: r for r in manifest_rows}
    selected = []
    for cn in BENCHMARK_CN:
        if cn in by_cn:
            selected.append(by_cn[cn])
        if limit and len(selected) >= limit:
            break
    return selected


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """You are an expert at extracting structured scientific content from physics papers.
Your task is to extract only what is explicitly stated in the provided text. Do not infer, invent, or summarize beyond the text.
For each extracted item, provide a short "evidence" quote from the paper that supports it.
If a category has no clear content in the text, return an empty list.
Output valid JSON only, no markdown code fence."""

EXTRACTION_USER_TEMPLATE = """Extract structured content from this physics paper. Return a single JSON object with these keys (each a list of objects with "text" and "evidence"):
- claims: main scientific claims or conclusions stated by the authors
- methods: approaches, formalisms, or techniques used (e.g. factorization, resummation, schemes)
- assumptions: explicit assumptions or working hypotheses
- limitations: stated limitations, caveats, or scope restrictions
- results: key numerical or qualitative results (e.g. fits, predictions, comparisons)

Each list item must be: {{ "text": "...", "evidence": "short quote from the paper" }}.
Evidence must be a verbatim or near-verbatim snippet from the provided text. If you cannot find evidence, use an empty string for "evidence".
Do not add any key not listed above. Do not invent content.

Paper title: {title}

Paper text (excerpt):
{text}
"""


def extract_with_llm(title: str, paper_text: str, model: str = "gpt-4o-mini") -> dict | None:
    """Call OpenAI API to get structured extraction. Returns dict or None if API unavailable."""
    if not paper_text.strip():
        return None
    if openai is None:
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    client = openai.OpenAI(api_key=api_key)
    user = EXTRACTION_USER_TEMPLATE.format(title=title, text=paper_text[:MAX_PAPER_CHARS])
    raw = ""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Allow optional markdown code block
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n```\s*$", "", raw)
        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        return {"_parse_error": str(e), "_raw": raw}


def build_output(control_number: int, title: str, extraction: dict | None, text_preview: str) -> dict:
    """Build the final JSON output for one paper."""
    out = {
        "control_number": control_number,
        "title": title,
        "claims": [],
        "methods": [],
        "assumptions": [],
        "limitations": [],
        "results": [],
    }
    if extraction and "_parse_error" not in extraction:
        for key in ("claims", "methods", "assumptions", "limitations", "results"):
            val = extraction.get(key)
            if isinstance(val, list):
                out[key] = [{"text": str(x.get("text", "")), "evidence": str(x.get("evidence", ""))} for x in val]
    else:
        if extraction and "_parse_error" in extraction:
            out["_extraction_error"] = extraction.get("_parse_error", "")
    out["_meta"] = {
        "text_source": "TEI (GROBID)",
        "text_length": len(text_preview),
        "extraction_succeeded": extraction is not None and "_parse_error" not in extraction,
    }
    return out


# ---------------------------------------------------------------------------
# DB integration (optional)
# ---------------------------------------------------------------------------

def save_to_sqlite(db_path: str, out: dict) -> None:
    """Insert extracted statements into paper_statements table if present."""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS paper_statements (
                control_number INTEGER NOT NULL,
                statement_type TEXT NOT NULL,
                statement_text TEXT NOT NULL,
                evidence_text TEXT,
                PRIMARY KEY (control_number, statement_type, statement_text)
            )
        """)
        cn = out["control_number"]
        for stype in ("claims", "methods", "assumptions", "limitations", "results"):
            for item in out.get(stype, []):
                text = (item.get("text") or "").strip()
                evidence = (item.get("evidence") or "").strip()
                if not text:
                    continue
                cur.execute(
                    "INSERT OR REPLACE INTO paper_statements (control_number, statement_type, statement_text, evidence_text) VALUES (?,?,?,?)",
                    (cn, stype, text, evidence),
                )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def report_benchmark(out_dir: str) -> None:
    """Print coverage and field stats from existing JSON outputs."""
    out_path = Path(out_dir)
    if not out_path.exists():
        print(f"Output dir not found: {out_path}")
        return
    jsons = sorted(out_path.glob("*.json"))
    if not jsons:
        print("No JSON files found.")
        return
    fields = ("claims", "methods", "assumptions", "limits", "results")
    # normalize key (we use "limitations" in schema)
    key_map = {"limits": "limitations"}
    counts = {f: [] for f in fields}
    papers_with = {f: 0 for f in fields}
    extraction_ok = 0
    for p in jsons:
        if p.name.startswith("all_"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            continue
        if obj.get("_meta", {}).get("extraction_succeeded"):
            extraction_ok += 1
        for k in fields:
            key = key_map.get(k, k)
            n = len(obj.get(key) or [])
            counts[k].append(n)
            if n > 0:
                papers_with[k] += 1
    n = len(counts["claims"])
    print(f"Papers with JSON: {n}")
    print(f"Extraction succeeded (LLM): {extraction_ok}")
    print("\nField population (papers with ≥1 item):")
    for k in fields:
        key = key_map.get(k, k)
        print(f"  {key}: {papers_with[k]}/{n}")
    print("\nAverage count per paper:")
    for k in fields:
        key = key_map.get(k, k)
        avg = sum(counts[k]) / n if n else 0
        print(f"  {key}: {avg:.1f}")
    print("\nTotal items across all papers:")
    for k in fields:
        key = key_map.get(k, k)
        print(f"  {key}: {sum(counts[k])}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract paper-level statements from top-200 TEI")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST, help="Path to top200 manifest CSV")
    ap.add_argument("--tei-dir", default=DEFAULT_TEI_DIR, help="Directory of TEI files (e.g. data/tei/top200)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory for JSON files")
    ap.add_argument("--limit", type=int, default=None, help="Process only this many papers (for testing)")
    ap.add_argument("--paper", type=int, default=None, help="Process only this control number")
    ap.add_argument("--all", action="store_true", help="Process all papers in manifest that have TEI (not just benchmark set)")
    ap.add_argument("--skip-existing", action="store_true", help="Skip papers that already have a valid JSON in --out-dir")
    ap.add_argument("--db", default="", help="If set, also write to this SQLite DB (paper_statements table)")
    ap.add_argument("--model", default="gpt-4o-mini", help="OpenAI model for extraction")
    ap.add_argument("--no-llm", action="store_true", help="Only extract text; do not call LLM (for debugging)")
    ap.add_argument("--max-chars", type=int, default=MAX_PAPER_CHARS, help="Max paper text length sent to LLM")
    ap.add_argument("--report", action="store_true", help="Only print benchmark stats from existing JSON in --out-dir")
    args = ap.parse_args()

    if args.report:
        report_benchmark(args.out_dir)
        return

    if etree is None:
        print("Error: lxml is required. Install with: pip install lxml", file=sys.stderr)
        sys.exit(1)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Error: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    tei_dir = Path(args.tei_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_manifest(str(manifest_path))
    if args.paper:
        papers = [r for r in rows if r["cn"] == args.paper]
        if not papers:
            print(f"Error: paper {args.paper} not in manifest", file=sys.stderr)
            sys.exit(1)
    elif args.all:
        papers = rows
        if args.limit:
            papers = papers[: args.limit]
    else:
        papers = get_benchmark_papers(rows, limit=args.limit)

    if not papers:
        print("No papers to process.", file=sys.stderr)
        sys.exit(0)

    has_llm = openai is not None and os.environ.get("OPENAI_API_KEY") and not args.no_llm
    if not has_llm and not args.no_llm:
        print("Warning: OPENAI_API_KEY not set or openai not installed. Running with --no-llm (text extraction only).", file=sys.stderr)

    stats = {"ok": 0, "no_tei": 0, "extraction_failed": 0, "extraction_ok": 0}

    for i, row in enumerate(papers):
        cn = row["cn"]
        title = row.get("title", "")
        tei_path = tei_dir / f"{cn}.tei.xml"
        print(f"[{i+1}/{len(papers)}] {cn} {title[:50]}...")

        if not tei_path.exists():
            print(f"  Skip: no TEI at {tei_path}")
            stats["no_tei"] += 1
            continue

        if args.skip_existing:
            existing = out_dir / f"{cn}.json"
            if existing.exists():
                try:
                    with open(existing, encoding="utf-8") as f:
                        ex = json.load(f)
                    if ex.get("_meta", {}).get("extraction_succeeded") and (ex.get("claims") or ex.get("methods")):
                        print(f"  Skip: existing valid JSON")
                        stats["ok"] += 1
                        continue
                except Exception:
                    pass

        try:
            text = get_text_from_tei(tei_path, max_chars=args.max_chars)
        except Exception as e:
            print(f"  Error reading TEI: {e}")
            stats["extraction_failed"] += 1
            continue

        if len(text) < 100:
            print(f"  Skip: too little text ({len(text)} chars)")
            stats["extraction_failed"] += 1
            continue

        stats["ok"] += 1
        extraction = None
        if has_llm:
            extraction = extract_with_llm(title, text, model=args.model)
            if extraction and "_parse_error" not in extraction:
                stats["extraction_ok"] += 1
            else:
                stats["extraction_failed"] += 1

        out = build_output(cn, title, extraction, text)
        out_path = out_dir / f"{cn}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"  Wrote {out_path}")

        if args.db and out.get("claims") or out.get("methods") or out.get("assumptions") or out.get("limitations") or out.get("results"):
            save_to_sqlite(args.db, out)

    # Aggregate JSONL
    jsonl_path = out_dir / "all_papers.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as jf:
        for p in sorted(out_dir.glob("*.json")):
            if p.name == "all_papers.jsonl":
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    obj = json.load(f)
                if "_meta" in obj:
                    jf.write(json.dumps(obj, ensure_ascii=False) + "\n")
            except Exception:
                pass
    print(f"Wrote aggregate {jsonl_path}")

    print("\nSummary:")
    print(f"  Text extracted (TEI found): {stats['ok']}")
    print(f"  No TEI: {stats['no_tei']}")
    if has_llm:
        print(f"  LLM extraction succeeded: {stats['extraction_ok']}")
        print(f"  LLM extraction failed/parse error: {stats['extraction_failed'] - (stats['ok'] - stats['extraction_ok'])}")


if __name__ == "__main__":
    main()
