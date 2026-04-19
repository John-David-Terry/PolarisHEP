# PolarisHEP

PolarisHEP is a pipeline for building a **statement-backed citation graph** from INSPIRE HEP literature: ingest papers and citations, extract citation-context statements from TEI (GROBID output), then query by physics topic to get relevant statements and papers.

This README is a **detailed handoff for another Cursor instance or developer**: how to use the code, pipeline order, and what action items remain.

---

## Table of contents

1. [High-level pipeline overview](#1-high-level-pipeline-overview)
2. [Repository layout](#2-repository-layout)
3. [Dependencies and setup](#3-dependencies-and-setup)
4. [Pipeline stages in order](#4-pipeline-stages-in-order)
5. [Script reference](#5-script-reference)
6. [Database schema and artifacts](#6-database-schema-and-artifacts)
7. [External inputs (not produced by this repo)](#7-external-inputs-not-produced-by-this-repo)
8. [Action items and gaps](#8-action-items-and-gaps)
9. [Quick start for the demo](#9-quick-start-for-the-demo)
10. [References](#10-references)

---

## 1. High-level pipeline overview

```
[INSPIRE API] --> ingest_inspire.py / parallel ingest --> inspire.sqlite (papers, citations, paper_keywords)
                                                                    |
[External] --> subgraph + top200 construction (NOT IN REPO) -------> top200_lookup, subgraph_* tables/views
                                                                    |
[External] --> GROBID PDF->TEI ------------------------------------> data/tei/citers/*.tei.xml
                                                                    |
extract_citation_contexts.py (TEI + top200_lookup) -----------------> citation_mentions
                                                                    |
build_edge_statements.py ------------------------------------------> edge_statements, demo_edges, *_with_meta views
                                                                    |
query_edge_statements.py (user query) -----------------------------> ranked statements + papers (demo)
```

**Demo goal:** User asks a physics question → system returns matching citation statements and the (citing, cited) paper pairs. The graph is **statement-backed**: an edge exists only if we have at least one recovered citation-context statement for it.

---

## 2. Repository layout

| Path | Description |
|------|-------------|
| `ingest_inspire.py` | Single-process INSPIRE API ingestion into SQLite (papers, citations, keywords). |
| `ingest_all_years.sh` | Generates work items (query slices by date) for full or parallel ingest; can write `work_items.txt`. |
| `run_part.sh` | Runs one worker: reads a work-items chunk, writes to one shard DB. |
| `run_parallel_ingest.sh` | Orchestrates: generate work items → split → N workers → merge shards. |
| `merge_shards.py` | Merges shard SQLite DBs into one `inspire.sqlite`. |
| `build_references.py` | Legacy: fetches citation edges per paper from API (ingest_inspire.py now does this during ingest). |
| `extract_citation_contexts.py` | Reads TEI in `data/tei/citers/`, matches refs to top200 via `top200_lookup`, writes `citation_mentions`. |
| `build_edge_statements.py` | Copies `citation_mentions` → `edge_statements`; creates `demo_edges`, `*_with_meta` views. Idempotent. |
| `query_edge_statements.py` | Retrieval demo: search statements by query (FTS5 or Python fallback), return edges + paper metadata. |
| `extract_paper_statements.py` | Paper-level extraction: TEI → LLM → structured claims/methods/assumptions/limitations/results with evidence; writes `data/paper_statements/*.json`. Use `--all` for full manifest (with TEI), `--skip-existing` for incremental. |
| `claim_tracking.py` | Claim-tracking experiment: align paper-level claims to citation statements, classify relation (uses/supports/refines/limits/disputes); writes `data/claim_tracking/*.json`. |
| `stress_test_claim_evolution.py` | Claim-evolution stress test: targeted papers + revised prompt to surface refines/limits/disputes; adds explanation per match; writes `data/claim_evolution_stress_test/*.json`. Use `--all` to run on all processable papers (have paper_statements + ≥1 citation). |
| `build_claim_evolution_cards.py` | Build canonical claim evolution cards from stress-test output (includes key follow-up papers per claim); writes `data/claim_evolution_cards/*.json` and `*.md`. Use `--report` for coverage and showcase. |
| `run_full_top200_claim_evolution.py` | Full top-200 claim-evolution run: processability accounting, extraction (--all, --skip-existing), stress-test (--all), cards, benchmark report. Use `.venv/bin/python` for subprocess LLM/lxml. |
| `regenerate_top200.py` | Builds `top200_manifest.csv` from DB views `subgraph_rank_*`, `subgraph_nodes_*_top200`. |
| `retrieve.py` | Downloads arXiv PDFs for papers in `top200_manifest_fixed.csv` → `data/arxiv_pdfs/25808/`. |
| `retrieve_citers.py` | Downloads arXiv PDFs for papers in `top200_citers_manifest.csv` → `data/arxiv_pdfs/25808_citers/`. |
| `PAPER_STATEMENTS_EXTRACTION.md` | Paper-level extraction: pipeline, benchmark, commands, and schema. |
| `CLAIM_TRACKING_BENCHMARK.md` | Claim-tracking experiment: paper + citation alignment, relation typing, benchmark results, mini-case studies. |
| `CLAIM_EVOLUTION_STRESS_TEST.md` | Stress test: targeted papers + revised prompt; refines/limits/disputes detection, explanations, comparison to first benchmark. |
| `CLAIM_EVOLUTION_CARDS.md` | Canonical claim evolution cards: schema, source, field_status rules, coverage, showcase examples. |
| `FULL_TOP200_CLAIM_EVOLUTION.md` | Full top-200 benchmark: processable set, stages, commands, benchmark report A–H, showcase and failure modes. |
| `FIXES_APPLIED.md` | Log of critical fixes (pagination retry, merge schema, locks, etc.). |
| `PARALLEL_INGESTION.md` | How to run parallel ingestion with shards. |
| `data/tei/citers/` | GROBID TEI XML files (one per citing paper, `{control_number}.tei.xml`). **Not generated by this repo.** |
| `data/tei/top200/` | TEI for top200 papers (GROBID); used by `extract_paper_statements.py`. |
| `data/paper_statements/` | Paper-level extraction output: one JSON per paper (claims, methods, assumptions, limitations, results + evidence). |
| `data/claim_tracking/` | Claim-tracking output: one JSON per benchmark paper (claims, citation matches, relation types, summaries). |
| `data/claim_evolution_stress_test/` | Stress-test output: one JSON per paper (claims, matches with relation + explanation). |
| `data/claim_evolution_cards/` | Canonical claim evolution cards: one JSON + one Markdown per paper (claim text, relation counts, representative examples, key follow-up papers per relation type, interpretation, field_status). |
| `data/arxiv_pdfs/25808/` | PDFs for top200 (from `retrieve.py`). |
| `data/arxiv_pdfs/25808_citers/` | PDFs for citers (from `retrieve_citers.py`). |
| `data/arxiv_pdfs/logs/` | Success/fail CSVs for PDF downloads. |

---

## 3. Dependencies and setup

- **Python 3** (tested on 3.11).
- **Libraries:** `requests`, `tqdm`, `lxml`. For paper-level extraction, `openai` (and `OPENAI_API_KEY`). Install with:
  ```bash
  pip install requests tqdm lxml
  pip install openai   # for extract_paper_statements.py
  ```
- **SQLite** with FTS5 (used by `query_edge_statements.py` for search). Most Python sqlite3 builds include FTS5; if not, the script falls back to Python-side lexical scoring.
- **Bash** for shell scripts (`ingest_all_years.sh`, `run_part.sh`, `run_parallel_ingest.sh`).
- **Optional:** `gsplit` (GNU `split`) for `run_parallel_ingest.sh` on macOS. On Linux, the script may need to use `split -n l/N` instead of `gsplit -n "l/N"` (see [Action items](#8-action-items-and-gaps)).

---

## 4. Pipeline stages in order

### Stage 1: Ingest INSPIRE metadata into SQLite

**Purpose:** Populate `inspire.sqlite` with papers, citations, and keywords from the INSPIRE API.

**Option A – Single process (small scale):**
```bash
python ingest_inspire.py --db inspire.sqlite --query 'collection:Literature and _exists_:abstracts' --max 5000
```

**Option B – Parallel (shards then merge):**
```bash
# Generate work items (date slices)
GENERATE_WORK_ITEMS=1 WORK_ITEMS_FILE=work_items.txt bash ingest_all_years.sh

# Run parallel ingest (e.g. 4 workers)
./run_parallel_ingest.sh 4
# This: splits work_items.txt, runs run_part.sh per shard, then merge_shards.py
```

**Outputs:** `inspire.sqlite` with tables `papers`, `citations`, `paper_keywords`, `meta`.

**Note:** `ingest_all_years.sh` uses `START_YEAR`/`END_YEAR` and slice size limits; see script header. For a small test, reduce range or use Option A with `--max`.

---

### Stage 2: Subgraph and top200 (external to this repo)

**Purpose:** Define a citation subgraph (e.g. from a seed paper) and a “top 200” set of papers; produce lookup and ranking structures.

**This repo does not contain scripts that:**

- Build the citation subgraph from `papers` + `citations`.
- Create tables/views such as:
  - `subgraph_edges_25808_present`, `subgraph_nodes_25808_*`, `subgraph_rank_25808_*`
  - `top200_lookup(parent_cn, arxiv_id_norm, doi_norm)`
  - `subgraph_nodes_25808_present_top200` (or similar)

**You must implement or run an external process** (e.g. notebook or separate repo) that:

1. Takes `papers` and `citations` in `inspire.sqlite`.
2. Builds the subgraph (e.g. BFS from seed control number 25808) and “present” nodes/edges.
3. Ranks nodes (e.g. by in/out degree within subgraph).
4. Creates:
   - `top200_lookup`: one row per top-200 paper with normalized `arxiv_id_norm`, `doi_norm` (and `parent_cn` = control number).
   - Views/tables expected by `regenerate_top200.py`: e.g. `subgraph_rank_25808_present`, `subgraph_nodes_25808_present_top200`.

**Outputs (in DB):** `top200_lookup`, `subgraph_*` tables/views. Optionally CSVs such as `top200_manifest_fixed.csv`, `top200_citers_manifest.csv` (see Stage 4).

---

### Stage 3: TEI from GROBID (external to this repo)

**Purpose:** Turn PDFs of citing papers into TEI XML so we can extract citation-context sentences.

**This repo does not run GROBID.** TEI files must be produced elsewhere.

- **Input:** PDFs of papers that cite the top200 (e.g. under `data/arxiv_pdfs/25808_citers/` after Stage 4).
- **Process:** Run GROBID (e.g. batch or API) to get one TEI XML per PDF.
- **Output:** Place TEI files under `data/tei/citers/` with names `{control_number}.tei.xml` (control number = INSPIRE paper ID of the citing paper).

**Convention:** Each file is the TEI for the paper with that `control_number` (the “child” / citing paper). References in the TEI (`<ref type="bibr">`, `<listBibl>`, `<biblStruct>`) are matched to top200 papers via arXiv/DOI in `top200_lookup`.

---

### Stage 4: Extract citation contexts into DB

**Purpose:** From TEI + `top200_lookup`, populate `citation_mentions(child_cn, parent_cn, sentence)`.

**Prerequisites:**  
- `inspire.sqlite` with `papers`, `citations`, `paper_keywords`, and **`top200_lookup`**.  
- `data/tei/citers/*.tei.xml` present (from Stage 3).

**Command:**
```bash
python extract_citation_contexts.py
```

**Note:** Script uses hardcoded `DB = "inspire.sqlite"` and `TEI_DIR = "data/tei/citers"`. Edit these if your paths differ.

**Output:** Table `citation_mentions` with one row per (citing paper, cited top200 paper, sentence) where the sentence is the ancestor `<p>` of the `<ref type="bibr">` in the TEI.

---

### Stage 5: Build statement-backed edge layer

**Purpose:** Copy `citation_mentions` into `edge_statements` and create demo views. Graph is statement-backed: edges exist only where we have at least one statement.

**Prerequisites:** Table `citation_mentions` exists (Stage 4).

**Command:**
```bash
python build_edge_statements.py --db inspire.sqlite
```

**Outputs:**

- Table: `edge_statements(child_cn, parent_cn, statement)`  
- Views: `demo_edges` (one row per edge with `n_statements`), `demo_edges_with_meta`, `edge_statements_with_meta`  
- Indexes on `edge_statements` for child, parent, (child, parent)

Idempotent: safe to run repeatedly; refreshes from `citation_mentions` each time.

---

### Stage 6: Retrieval demo (question → statements → papers)

**Purpose:** Let a user type a physics query and get ranked citation statements plus child/parent paper metadata.

**Prerequisites:** `edge_statements` (and thus `build_edge_statements.py` and Stage 4–5) have been run.

**Commands:**
```bash
# Basic query
python query_edge_statements.py --db inspire.sqlite --query "Sudakov resummation"

# Top 5, JSON
python query_edge_statements.py --db inspire.sqlite --query "TMD evolution" --top-k 5 --json

# Rank by edge (aggregate score per edge)
python query_edge_statements.py --db inspire.sqlite --query "factorization" --by-edge --top-k 10

# Force Python fallback if FTS5 unavailable
python query_edge_statements.py --db inspire.sqlite --query "Higgs" --no-fts
```

**Behavior:** Searches over `edge_statements.statement` (FTS5 if available, else Python lexical scoring). Returns score, child_cn, child_title, parent_cn, parent_title, statement. Optional `--by-edge` aggregates by (child_cn, parent_cn) and reports edge-level score and top statements.

---

### Optional: Regenerate top200 manifest CSV

**Purpose:** Export top200 list from DB (after subgraph/top200 objects exist) to CSV.

**Prerequisites:** Views `subgraph_rank_25808_present` and `subgraph_nodes_25808_present_top200` (or equivalent) exist (Stage 2).

**Command:**
```bash
python regenerate_top200.py --db inspire.sqlite --output top200_manifest.csv --sort-by total --limit 200
```

**Output:** `top200_manifest.csv` (or `top200_manifest_fixed.csv` if you rename/copy). Used by `retrieve.py` and downstream for “top200” and “citers” manifests.

---

### Optional: Download arXiv PDFs

**Purpose:** Fetch PDFs for papers listed in a manifest (for later GROBID processing or inspection).

**Top200 papers (expects `top200_manifest_fixed.csv`):**
```bash
python retrieve.py
```
Writes to `data/arxiv_pdfs/25808/`, logs under `data/arxiv_pdfs/logs/`.

**Citing papers (expects `top200_citers_manifest.csv`):**
```bash
python retrieve_citers.py
```
Writes to `data/arxiv_pdfs/25808_citers/`, logs under `data/arxiv_pdfs/logs/`.

Manifests must have columns including `cn`, `arxiv_id`; they are typically produced from the DB (e.g. from top200/subgraph logic in Stage 2) and are not generated by the scripts in this repo.

---

## 5. Script reference

| Script | Inputs | Outputs | Idempotent / Notes |
|--------|--------|--------|--------------------|
| `ingest_inspire.py` | `--db`, `--query`, `--size`, `--max`, `--sleep` | `papers`, `citations`, `paper_keywords`, `meta` | Yes (upsert). Pagination uses retry. |
| `ingest_all_years.sh` | Env: `GENERATE_WORK_ITEMS`, `WORK_ITEMS_FILE`; vars: `START_YEAR`, `END_YEAR`, `DB` | Work items on stdout or file | Generates work items; no DB write if only generating. |
| `run_part.sh` | `<part_file>` (work items), `<shard_db>` | One shard DB per worker; lock file | Uses lock file; one shard per worker. |
| `run_parallel_ingest.sh` | `work_items.txt` (or generates it), num workers | Shards then merged `inspire.sqlite` | Calls ingest_all_years, run_part, merge_shards. Uses `gsplit` (see Action items). |
| `merge_shards.py` | `--target`, `--shards` | Merged DB with explicit column lists | Idempotent merge (OR REPLACE / OR IGNORE). |
| `extract_citation_contexts.py` | `inspire.sqlite`, `data/tei/citers/*.tei.xml`, `top200_lookup` | `citation_mentions` | Appends; re-run duplicates unless you truncate. No CLI args. |
| `build_edge_statements.py` | `inspire.sqlite`, `citation_mentions` | `edge_statements`, views, indexes | Idempotent (delete + insert + recreate views). |
| `query_edge_statements.py` | `--db`, `--query`, `--top-k`, `--json`, `--by-edge`, `--no-fts`, `--build-fts` | Readable or JSON results | Creates FTS table if missing (when not `--no-fts`). |
| `regenerate_top200.py` | `--db`, `--output`, `--sort-by`, `--limit` | CSV manifest | Reads from subgraph views only. |
| `retrieve.py` | `top200_manifest_fixed.csv` | PDFs in `data/arxiv_pdfs/25808/`, logs | Skips existing; 1s sleep between requests. |
| `retrieve_citers.py` | `top200_citers_manifest.csv` | PDFs in `data/arxiv_pdfs/25808_citers/`, logs | Same. |
| `build_references.py` | `inspire.sqlite` (papers) | Citations from API per paper | Legacy; ingest_inspire already pulls citations. |

---

## 6. Database schema and artifacts

**Core tables (created by ingest):**

- `papers(control_number, title, abstract, date, arxiv_id, arxiv_cat, doi, inspire_url, updated_at_utc)`
- `citations(citing, cited, updated_at_utc)` with indexes on citing, cited
- `paper_keywords(control_number, keyword, source, updated_at_utc)`
- `meta(k, v)`

**Statement pipeline (after Stage 4–5):**

- `citation_mentions(child_cn, parent_cn, sentence)` — from TEI + top200_lookup
- `edge_statements(child_cn, parent_cn, statement)` — copy of citation_mentions for demo
- `demo_edges` — unique (child_cn, parent_cn) with `n_statements`
- `demo_edges_with_meta` — demo_edges + paper titles/arxiv/doi for child and parent
- `edge_statements_with_meta` — edge_statements + child_title, parent_title

**External / from Stage 2:**

- `top200_lookup(parent_cn, arxiv_id_norm, doi_norm)` — used by extract_citation_contexts
- `subgraph_edges_25808_present`, `subgraph_rank_25808_present`, `subgraph_nodes_25808_present_top200`, etc. — used by regenerate_top200 and optionally by build_edge_statements benchmarks

**Search (query_edge_statements.py):**

- `edge_statements_fts` — FTS5 virtual table on `edge_statements` (statement, child_cn, parent_cn); created on first query unless `--no-fts`.

---

## 7. External inputs (not produced by this repo)

| Artifact | Who produces it | Used by |
|----------|-----------------|--------|
| `top200_lookup` + subgraph tables/views | External pipeline (e.g. notebook) from papers + citations | extract_citation_contexts, regenerate_top200, build_edge_statements (optional cross-check) |
| `data/tei/citers/*.tei.xml` | GROBID (batch or API) from PDFs | extract_citation_contexts |
| `top200_manifest_fixed.csv`, `top200_citers_manifest.csv` | Export from DB (subgraph/top200 logic) or external | retrieve.py, retrieve_citers.py |
| PDFs in `data/arxiv_pdfs/` | retrieve.py, retrieve_citers.py (from manifests) | GROBID (outside repo) |

---

## 8. Action items and gaps

**For another Cursor instance or developer:**

1. **Implement or wire subgraph + top200 construction**
   - From `papers` and `citations`, build subgraph (e.g. seed 25808), “present” nodes/edges, and ranking.
   - Create `top200_lookup(parent_cn, arxiv_id_norm, doi_norm)` and the views/tables expected by `regenerate_top200.py` (e.g. `subgraph_rank_25808_present`, `subgraph_nodes_25808_present_top200`).
   - Optionally export `top200_manifest_fixed.csv` and `top200_citers_manifest.csv` (or document how they are generated).

2. **TEI generation**
   - Add or document a GROBID batch/API step: PDFs (e.g. from `data/arxiv_pdfs/25808_citers/`) → TEI → `data/tei/citers/{control_number}.tei.xml`.
   - Ensure control numbers in filenames match INSPIRE paper IDs for citing papers.

3. **extract_citation_contexts.py**
   - Add CLI (e.g. `--db`, `--tei-dir`) and make it idempotent (e.g. truncate `citation_mentions` before insert, or use a single run after TEI is ready).

4. **run_parallel_ingest.sh**
   - Uses `gsplit` (GNU split). On Linux, replace with `split -n l/$NUM_WORKERS -d "$WORK_ITEMS_FILE" work_items_part_` (or equivalent) if `gsplit` is not installed.

5. **Requirements**
   - Add `requirements.txt` with `requests`, `tqdm`, `lxml` (and any others you need).

6. **.gitignore**
   - Ignore `.venv/`, `*.sqlite`, `data/tei/`, `data/arxiv_pdfs/`, large CSVs, `work_items*.txt`, `worker_*.log`, `*.lock`, etc., to avoid committing data and shards.

7. **Retrieval**
   - Current demo is lexical (FTS5 or Python). Optional next steps: query expansion (e.g. “CSS” → “Collins Soper Sterman”), OR in FTS, embeddings/semantic ranking, or hybrid.

8. **Empty statements**
   - Some `citation_mentions` rows have empty/whitespace `sentence` (e.g. from TEI). Optionally filter in `build_edge_statements.py` (e.g. `WHERE TRIM(sentence) != ''`) or in extraction.

---

## 9. Quick start for the demo

If you already have `inspire.sqlite` with `citation_mentions` (and optionally `edge_statements`):

```bash
# Build or refresh statement-backed layer
python build_edge_statements.py --db inspire.sqlite

# Run retrieval
python query_edge_statements.py --db inspire.sqlite --query "Sudakov resummation" --top-k 10
```

If you have a fresh DB with only ingest data, you must:

1. Add `top200_lookup` and (for full pipeline) subgraph tables/views (Stage 2).
2. Add TEI under `data/tei/citers/` (Stage 3).
3. Run `extract_citation_contexts.py` (Stage 4).
4. Then run `build_edge_statements.py` and `query_edge_statements.py` as above.

---

## 10. References

- **INSPIRE API:** https://inspirehep.net/api  
- **GROBID:** https://github.com/kermitt2/grobid  
- **FIXES_APPLIED.md:** Critical fixes (pagination retry, merge schema, locks, etc.)  
- **PARALLEL_INGESTION.md:** Sharded parallel ingestion workflow  
