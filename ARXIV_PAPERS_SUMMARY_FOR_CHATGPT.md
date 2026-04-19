# Details for ChatGPT: Papers Downloaded from arXiv (PolarisHEP)

This document describes the arXiv PDFs that the PolarisHEP project has downloaded: where they live, how they were chosen, what metadata we have, and how they connect to the rest of the pipeline.

---

## 1. Overview

We have **two sets** of papers downloaded from arXiv:

| Set | Purpose | Manifest | PDF directory | Approx. count |
|-----|---------|----------|----------------|---------------|
| **Top 200** | Highly cited papers in a citation subgraph (seed 25808) | `top200_manifest_fixed.csv` | `data/arxiv_pdfs/25808/` | **199 PDFs** (one per paper with arXiv ID) |
| **Citers** | Papers that cite the top 200 (used for citation-context extraction) | `top200_citers_manifest.csv` | `data/arxiv_pdfs/25808_citers/` | **563 PDFs** |

- **Manifests** list INSPIRE control number (`cn`), arXiv ID, DOI, INSPIRE URL, and title.
- **PDFs** are stored as `{control_number}.pdf` (e.g. `810127.pdf`, `618943.pdf`).
- Downloads are done by **`retrieve.py`** (top 200) and **`retrieve_citers.py`** (citers); both use 1 s delay between requests and write success/fail logs under `data/arxiv_pdfs/logs/`.

---

## 2. Top 200 papers (`data/arxiv_pdfs/25808/`)

- **Source list:** `top200_manifest_fixed.csv` (200 rows + header; one row per paper).
- **Columns:** `cn`, `indeg_in_subgraph`, `depth`, `arxiv_id`, `doi`, `inspire_url`, `title`.
- **Selection:** Papers in a citation subgraph (built from seed paper 25808), ranked by in-degree + out-degree within that subgraph, limited to 200. So these are the “top 200” most internally cited papers in the subgraph.
- **Domain:** HEP/physics: parton distributions, TMDs, transverse spin, Drell–Yan, Sivers, Collins, factorization, resummation, etc.
- **Examples from the manifest:**
  - `cn=810127`, arxiv_id=`0901.0002`, title="Parton distributions for the LHC"
  - `cn=729695`, arxiv_id=`hep-ex/0610068`, title="A New measurement of the Collins and Sivers asymmetries..."
  - `cn=750627`, arxiv_id=`0705.2141`, title="$k_{T}$ factorization is violated in production of high-transverse-momentum particles..."
  - `cn=779762`, arxiv_id=`0802.2821`, title="Wilson lines and transverse-momentum dependent parton distribution functions: A Renormalization-group analysis"
- **Download outcome:** 199 PDFs in `data/arxiv_pdfs/25808/` (some manifest rows have no arXiv ID and are skipped; filenames are `{cn}.pdf`).

---

## 3. Citer papers (`data/arxiv_pdfs/25808_citers/`)

- **Source list:** `top200_citers_manifest.csv` (701 rows + header).
- **Columns:** `cn`, `arxiv_id`, `doi`, `inspire_url`, `title`.
- **Selection:** Papers that **cite** at least one of the top 200 (the “citer” papers). Used so we can run GROBID on their PDFs and extract **citation-context statements** (sentences where they cite the top 200).
- **Domain:** Same HEP/physics ecosystem; mix of experimental and theory.
- **Examples from the manifest:**
  - `cn=618943`, arxiv_id=`hep-ph/0305179`, title="Exponentiation of the Drell-Yan cross-section near partonic threshold..."
  - `cn=631364`, arxiv_id=`hep-ph/0310271`, title="Exponentiation at partonic threshold for the Drell-Yan cross-section"
  - `cn=666733`, arxiv_id=`hep-ph/0412138`, title="Quark initial state interaction in deep inelastic scattering and the Drell-Yan process"
- **Download outcome:** 563 PDFs in `data/arxiv_pdfs/25808_citers/` (again, one file per paper with an arXiv ID; filename `{cn}.pdf`).

---

## 4. File layout and naming

- **Paths:**
  - Top 200: `data/arxiv_pdfs/25808/{cn}.pdf`
  - Citers:   `data/arxiv_pdfs/25808_citers/{cn}.pdf`
- **`cn`** = INSPIRE HEP control number (integer), e.g. 810127, 618943. Same ID used in the SQLite DB (`papers.control_number`, `citations`, etc.) and in TEI filenames (`data/tei/citers/{cn}.tei.xml`).
- **arXiv ID** in the manifests can be:
  - New-style: `0901.0002`, `0705.2141`
  - Old-style: `hep-ph/0305179`, `hep-ex/0610068`
  Scripts normalize (strip `arxiv:`, strip version like `v1`) and request `https://arxiv.org/pdf/{arxiv_id}.pdf`.

---

## 5. Logs (success / failure)

Under `data/arxiv_pdfs/logs/`:

- **Top 200:** `arxiv_success.csv`, `arxiv_fail.csv` — columns `cn`, `arxiv_id`, and either `status` (e.g. DOWNLOADED, ALREADY_EXISTS) or `error`.
- **Citers:** `arxiv_citers_success.csv`, `arxiv_citers_fail.csv` — same idea.

So you can see exactly which papers were attempted, which succeeded, and which failed (no arXiv ID, HTTP error, etc.).

---

## 6. How these PDFs are used in the pipeline

1. **Top 200 PDFs**  
   Optional: can be sent to GROBID to get TEI for the top 200 themselves. Currently the main use of GROBID in the repo is for **citer** PDFs.

2. **Citer PDFs**  
   - Run through **GROBID** (outside this repo) to produce **TEI** under `data/tei/citers/{cn}.tei.xml`.
   - **`extract_citation_contexts.py`** reads that TEI and `top200_lookup` in the DB, finds in-text references to the top 200 (by arXiv/DOI), and extracts the enclosing paragraph as the “citation statement,” stored in **`citation_mentions`**.
   - **`build_edge_statements.py`** copies that into **`edge_statements`** and builds demo views.
   - **`query_edge_statements.py`** (lexical/semantic/hybrid) searches those statements and returns (citing paper, cited paper, statement).

So: **the downloaded arXiv PDFs (especially the citers) are the source of the citation-context text we search over**; the TEI is the intermediate format between PDF and DB.

---

## 7. Manifest formats (for parsing)

**top200_manifest_fixed.csv**  
- Header: `cn,indeg_in_subgraph,depth,arxiv_id,doi,inspire_url,title`  
- 200 data rows (one per top-200 paper).  
- Titles may contain commas and are quoted in CSV.

**top200_citers_manifest.csv**  
- Header: `cn,arxiv_id,doi,inspire_url,title`  
- 700 data rows (papers that cite the top 200).  
- Same CSV conventions.

---

## 8. Summary for ChatGPT

- **Two groups of papers:** (1) **199 top-200 papers** from a citation subgraph, (2) **563 citer papers** that cite those top 200.
- **Stored as:** one PDF per paper at `data/arxiv_pdfs/25808/{cn}.pdf` and `data/arxiv_pdfs/25808_citers/{cn}.pdf`; `cn` = INSPIRE control number.
- **Metadata:** in the two CSVs: `cn`, `arxiv_id`, `doi`, `inspire_url`, `title`; top-200 CSV also has `indeg_in_subgraph`, `depth`.
- **Content:** HEP papers (parton distributions, TMDs, transverse spin, Drell–Yan, factorization, resummation, etc.).
- **Role:** Citer PDFs → GROBID → TEI → citation-context extraction → statement-backed graph and retrieval. Top-200 PDFs are available for the same pipeline if desired.
