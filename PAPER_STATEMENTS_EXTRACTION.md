# Paper-Level Statement Extraction (Polaris)

This document describes the **paper-level statement extraction pipeline** for top-200 papers: what was built, how to run it, benchmark results, and next steps.

---

## High-level summary (for handoff)

- **What was built:** A single script (`extract_paper_statements.py`) that reads GROBID TEI for selected top-200 papers, extracts abstract + body text (up to ~14k chars), and calls an LLM (OpenAI) to fill a fixed schema: **claims**, **methods**, **assumptions**, **limitations**, **results**, each with optional **evidence** snippets. Outputs one JSON per paper and an aggregate JSONL; optional SQLite table `paper_statements`.
- **Did it work?** Yes. On a 9-paper subset, all had successful extraction; claims and methods are well populated and grounded; assumptions and limitations are sometimes sparser. Evidence quotes are short and traceable.
- **What we can extract now:** Structured, grounded statements about what each paper claims, which methods it uses, what it assumes, what it states as limitations, and key results—suitable for comparison with citation-level statements and for deeper reasoning.
- **Main weaknesses:** Assumptions/limitations depend on explicit wording; very math-heavy papers may get fewer fine-grained extractions; pipeline currently uses only TEI (no fallback to raw PDF text).
- **Is this the right direction?** Yes. It complements citation-context retrieval with author-stated content and is the right next step toward deeper scientific reasoning in Polaris.

---

## 1. What was built

- **Script:** `extract_paper_statements.py`
  - Reads **TEI XML** (GROBID output) from `data/tei/top200/` for each selected paper.
  - Extracts **abstract + body sections** (intro, methods, conclusion, etc.) up to a character limit (~14k) for LLM context.
  - Calls an **LLM** (OpenAI API) to produce **structured JSON** with:
    - **claims**, **methods**, **assumptions**, **limitations**, **results**
    - Each item has `text` and `evidence` (short quote from the paper).
  - Writes **one JSON per paper** to `data/paper_statements/{control_number}.json` and an aggregate `all_papers.jsonl`.
  - Optional: writes to SQLite table `paper_statements(control_number, statement_type, statement_text, evidence_text)` if `--db` is set.

**Design principle:** The LLM is used only for **structured extraction** from the provided text. It does not do free-form QA or invent content; every extracted item is intended to be grounded in the paper, with evidence snippets.

---

## 2. Text source

- **Primary source:** **Existing GROBID TEI** for top-200 papers in `data/tei/top200/`.
- There are **199** TEI files there; the script uses them as-is.
- **No PDF text extraction** was implemented: TEI was available and is higher quality (sections, paragraphs, formulas marked).
- If TEI is missing for a paper, that paper is skipped.
- **Recommendation:** If you need papers without TEI, run GROBID on the corresponding PDFs in `data/arxiv_pdfs/25808/` and place the resulting TEI in `data/tei/top200/` with the same naming (`{control_number}.tei.xml`).

---

## 3. Paper selection (benchmark set)

The benchmark subset is **19 papers** chosen for:

- **High impact** in the subgraph (high indegree in `top200_manifest_fixed.csv`).
- **Topic variety:** PDFs/global fits, TMDs, resummation, spin asymmetries, experiment, theory.

**Control numbers:**  
810127, 729695, 750627, 779762, 763778, 708985, 594939, 618943, 711854, 698679, 823754, 771566, 846542, 862424, 693371, 877524, 829121, 713783, 789754.

These are defined in `BENCHMARK_CN` in `extract_paper_statements.py`. You can override with `--paper CN` (single paper) or `--limit N` (first N of the benchmark list).

---

## 4. Extraction pipeline details

- **Chunking:** One call per paper. The input is **abstract + body sections** in order, truncated to `--max-chars` (default 14,000).
- **Prompt:** System prompt instructs the model to extract only explicit content and to provide evidence quotes. User prompt includes paper title and the text excerpt; the model returns a single JSON object with the five list keys.
- **Model:** Default `gpt-4o-mini`; override with `--model`.
- **Reproducibility:** Same TEI + same model + temperature 0.1 should give similar (not bit-identical) extractions. For full reproducibility, pin the OpenAI model version and document it.

---

## 5. Output schema

Each paper JSON has the form:

```json
{
  "control_number": 810127,
  "title": "Parton distributions for the LHC",
  "claims": [ { "text": "...", "evidence": "..." } ],
  "methods": [ { "text": "...", "evidence": "..." } ],
  "assumptions": [ { "text": "...", "evidence": "..." } ],
  "limitations": [ { "text": "...", "evidence": "..." } ],
  "results": [ { "text": "...", "evidence": "..." } ],
  "_meta": {
    "text_source": "TEI (GROBID)",
    "text_length": 14026,
    "extraction_succeeded": true
  }
}
```

If the LLM is not used or parsing fails, the five list fields are empty and `_meta.extraction_succeeded` is false.

---

## 6. Commands

**Dependencies:**

```bash
pip install lxml openai
```

**Environment:**

- `OPENAI_API_KEY` must be set for LLM extraction. If unset, the script runs in text-only mode (writes JSON with empty lists and `extraction_succeeded: false`).

**Run extraction:**

```bash
# Full benchmark set (20 papers)
python extract_paper_statements.py --manifest top200_manifest_fixed.csv --tei-dir data/tei/top200

# Single paper
python extract_paper_statements.py --paper 810127

# First 5 papers (testing)
python extract_paper_statements.py --limit 5

# Text extraction only (no API calls)
python extract_paper_statements.py --no-llm --limit 10

# Also write to SQLite
python extract_paper_statements.py --db inspire.sqlite
```

**Report benchmark stats** (from existing JSONs in `data/paper_statements/`):

```bash
python extract_paper_statements.py --report
```

**Output locations:**

- Per-paper: `data/paper_statements/{cn}.json`
- Aggregate: `data/paper_statements/all_papers.jsonl`

---

## 7. Benchmark results (representative run)

A run over **9 papers** (subset of the 20-paper benchmark) gave:

| Metric | Value |
|--------|--------|
| Papers with JSON | 9 |
| Extraction succeeded (LLM) | 9/9 |
| Papers with ≥1 **claim** | 9/9 |
| Papers with ≥1 **method** | 9/9 |
| Papers with ≥1 **assumption** | 7/9 |
| Papers with ≥1 **limitation** | 7/9 |
| Papers with ≥1 **result** | 8/9 |
| **Average per paper** | claims 2.4, methods 2.4, assumptions 1.2, limitations 1.2, results 1.4 |
| **Totals** | claims 22, methods 22, assumptions 11, limitations 11, results 13 |

**Quality (manual spot-check):**

- **Claims** and **methods** are central and well-supported by evidence.
- **Assumptions** and **limitations** are sometimes fewer or more generic; the model often pulls from intro/conclusion.
- **Evidence** snippets are generally short and traceable to the provided text.
- Papers with heavy formalism (many formulas) still get useful high-level claims/methods; fine-grained formalism is not always reflected.

**Failure modes observed:**

- Occasional generic or vague limitations when the paper does not state them explicitly.
- Some assumptions are implicit in the text; the model may miss them or phrase them loosely.
- Very math-heavy sections contribute less to extraction when truncated.

---

## 8. Optional DB integration

With `--db inspire.sqlite`, the script populates (or creates) a table:

```sql
CREATE TABLE IF NOT EXISTS paper_statements (
    control_number INTEGER NOT NULL,
    statement_type TEXT NOT NULL,   -- 'claims'|'methods'|'assumptions'|'limitations'|'results'
    statement_text TEXT NOT NULL,
    evidence_text TEXT,
    PRIMARY KEY (control_number, statement_type, statement_text)
);
```

This is optional; the main deliverable is the JSON files.

---

## 9. Comparison with citation-level statements

- **Citation-level** (`edge_statements` / `citation_mentions`): what **other papers** say when they cite a given paper (context sentences).
- **Paper-level** (this pipeline): what the **paper itself** says about its claims, methods, assumptions, limitations, and results.

The two are **complementary**: citation context reflects impact and usage; paper-level extraction reflects author-stated content. Together they support deeper scientific reasoning (e.g. comparing “what the paper claims” vs “how citers describe it”).

---

## 10. Next steps and bottlenecks

1. **Scale:** Run the full 19-paper benchmark (and optionally all 199 with TEI) once stable.
2. **TEI coverage:** Add TEI for any top-200 papers that only have PDFs (run GROBID on `data/arxiv_pdfs/25808/`).
3. **Quality:** Optional post-filter or hand-review of assumptions/limitations; consider section-specific prompts (e.g. “extract only from Introduction”).
4. **Integration:** Use `paper_statements` (or the JSONs) in retrieval/demo (e.g. combine with `query_edge_statements.py` for “paper self-description + citation context”).
5. **Schema extensions:** Add optional fields (e.g. `definitions`, `scope`, `open_questions`) if the prompt and evaluation warrant it.

---

## 11. Files created or modified

| File | Description |
|------|-------------|
| `extract_paper_statements.py` | New: TEI text extraction + LLM extraction + JSON/JSONL/SQLite output, `--report` |
| `data/paper_statements/*.json` | One JSON per processed paper |
| `data/paper_statements/all_papers.jsonl` | Aggregate JSONL (created/updated on each run) |
| `PAPER_STATEMENTS_EXTRACTION.md` | This document |

No changes to existing tables or scripts beyond the optional `paper_statements` table when `--db` is used.

---

## Detailed technical summary for ChatGPT

- **Files created:** `extract_paper_statements.py` (new), `data/paper_statements/*.json` and `all_papers.jsonl`, `PAPER_STATEMENTS_EXTRACTION.md`. README updated with script and paths.
- **Text source:** GROBID TEI in `data/tei/top200/` (199 files). No PDF text extraction; if TEI is missing, paper is skipped. Recommendation: run GROBID on top-200 PDFs for any missing TEI.
- **Paper selection:** 19 benchmark papers from `top200_manifest_fixed.csv` by control number (see `BENCHMARK_CN` in script): high indegree, mix of theory/experiment/TMD/PDF/resummation.
- **Extraction pipeline:** (1) Load manifest CSV; (2) for each selected paper, `get_text_from_tei(tei_path, max_chars=14000)` returns abstract + body divs in order; (3) OpenAI Chat Completions (default `gpt-4o-mini`, temperature 0.1) with system + user prompt; (4) parse JSON from response (strip optional markdown fence); (5) write `{cn}.json` and append to `all_papers.jsonl`; (6) optional `paper_statements` SQLite insert.
- **Prompt structure:** System: extract only explicit content, evidence = quote from paper, output JSON only. User: paper title + text excerpt; required keys `claims`, `methods`, `assumptions`, `limitations`, `results`; each item `{ "text", "evidence" }`.
- **Chunking:** One request per paper; input truncated to 14k chars (abstract + body sections). No section-by-section or multi-call merge.
- **Benchmark (9-paper run):** 9/9 extraction succeeded; claims 9/9, methods 9/9, assumptions 7/9, limitations 7/9, results 8/9; averages ~2.4 claims/methods, ~1.2 assumptions/limitations, ~1.4 results per paper.
- **Quality:** Claims and methods are central and well grounded; assumptions/limitations sometimes sparse or generic; evidence snippets short and traceable; math-heavy truncation can reduce detail.
- **Failure modes:** No TEI → skip; API key missing → text-only mode (empty lists); parse error → `_parse_error` and `_raw` in output; vague or missing assumptions/limitations when not explicit in text.
- **Next bottleneck:** Scaling to full 19 (or 199) papers; optional refinement of prompt for assumptions/limitations; integration with retrieval/demo (paper-level + citation-level); TEI coverage for all top-200.
