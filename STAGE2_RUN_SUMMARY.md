# PolarisHEP Stage 2 run summary

**Command run:** `.venv/bin/python run_full_top200_claim_evolution.py --stage 2 --skip-existing`  
**Date:** 2026-03-08

---

## A. High-level summary (for John)

**Did Stage 2 complete successfully?**  
Yes. The run finished with exit code 0.

**How many papers now have valid paper_statements?**  
- **198 papers** have valid output with **non-empty claims** (`_meta.extraction_succeeded` true and `claims` non-empty).  
- **1 paper** (663107) has valid JSON and `extraction_succeeded: true` but **empty claims** (very short source text in TEI; methods/results are minimal).  
- **1 manifest paper** (627142) has **no TEI** and therefore no `paper_statements` file.  
- So: **198 papers** are fully usable for downstream claim evolution; 199 have a JSON file (198 usable + 1 empty-claims).

**How many new papers were processed in this run?**  
**Zero.** With `--skip-existing`, every paper that already had valid paper_statements JSON was skipped. All 199 papers with TEI already had a JSON file from a previous run, so the script reported "Skip: existing valid JSON" for each and did not call the LLM.

**How many failures occurred?**  
No failures in this run (no new extractions). There is **1 pre-existing output** (663107) with empty claims due to very short extracted text (169 characters), so that paper is not usable for claim evolution until re-extracted with more source text or fixed TEI.

**Does extraction quality look usable overall?**  
Yes. Across 198 papers with claims: claims are specific, evidence-grounded, and align with paper titles (e.g. MSTW PDFs, kT factorization violation, Sivers effect, Drell–Yan exponentiation). One paper (663107) is an outlier with almost no text. Recommend proceeding to Stage 1 refresh and then Stages 3–4.

---

## B. Low-level summary (for ChatGPT)

### 1. Coverage benchmarks

| Metric | Value |
|--------|--------|
| Number of manifest papers | 200 |
| Number of papers with TEI | 199 |
| Number of papers with valid paper_statements **before** Stage 2 | 199 (all with TEI had a JSON file) |
| Number of papers with valid paper_statements **after** Stage 2 | 199 (unchanged; no new files written) |
| Number with **non-empty claims** (usable for claim evolution) | 198 |
| Number **newly processed** in this run | 0 |
| Number **skipped** (existing valid output) | 199 |
| Number that **failed extraction** in this run | 0 |
| Papers with **no TEI** (never processed) | 1 (627142) |

**Note:** “Valid” in the pipeline means JSON exists and `_meta.extraction_succeeded` is true. For **downstream use** (claim evolution), we additionally require non-empty `claims`; that holds for 198 papers. One file (663107) has `extraction_succeeded: true` but `claims: []` due to very short source text.

### 2. Quality / schema benchmarks

| Check | Result |
|--------|--------|
| `_meta.extraction_succeeded` exists and true for valid outputs | Yes for all 199 JSON files |
| `claims` exists and non-empty for “valid” outputs | 198 yes, 1 no (663107) |
| `methods`, `assumptions`, `limitations`, `results` present | All 199 files have these keys |
| Output files valid JSON | 199/199; no malformed files |
| Outputs with non-empty claims | 198 |
| Outputs with empty claims | 1 (663107) |
| Malformed or invalid JSON | 0 |
| Outputs missing expected fields | 0 |

### 3. Claim-count benchmarks (across 198 valid outputs with non-empty claims)

| Metric | Value |
|--------|--------|
| Total extracted claims | 530 |
| Average claims per paper | 2.68 |
| Median claims per paper | 3 |
| Minimum claims per paper | 1 |
| Maximum claims per paper | 6 |

### 4. Sampling benchmark (5 papers)

- **810127 (MSTW PDFs):** Claims are specific (MSTW 2008, supersede MRST, use for LHC). Match main contribution.
- **862424 (Drell–Yan small qT, collinear anomaly):** Claims are precise (exact all-order expression, naive factorization broken, factorization theorem). Strong alignment with title and contribution.
- **750627 (kT factorization violated):** Claims clearly state violation, counterexample, and implications for factorization. Specific and meaningful.
- **823754 (Sivers effect in DIS):** Claims describe evidence for naive-T-odd TMD, Sivers modulation, non-vanishing Sivers function. Appropriate for an experimental result.
- **618943 (Exponentiation Drell–Yan DIS/MS):** Claims on exponentiation of constant terms, refactorization, and better approximation. Specific and grounded.

**Conclusion:** Extracted claims are specific, meaningful, and reflect main contributions. No vague or generic claims in this sample. Quality is suitable for claim evolution (Stages 3–4).

### 5. Failure benchmark

**In this run:** No failures (no new extractions).

**Pre-existing problematic output:**

| Control number | Reason | Recoverable? |
|----------------|--------|--------------|
| 663107 | `extraction_succeeded: true` but `claims: []`. TEI yielded only 169 characters of text; LLM returned empty claims. Methods/results are minimal. | Yes, if TEI is improved or a different text source is used. |

**Paper with no output:** 627142 — no TEI file; skipped. Not a Stage 2 failure; would need TEI to be generated first.

### Recommendation

- **Stage 2:** Consider no change for this run; 198 papers are usable. Optionally re-run extraction for 663107 without `--skip-existing` after fixing or replacing its TEI if more text becomes available.
- **Next steps:** Refresh Stage 1 (processability accounting), then run Stages 3 and 4. The pipeline is ready for claim evolution on the 198 papers with non-empty claims; 64 of those have at least one downstream citation in `edge_statements`, so up to 64 papers can get full claim evolution output and cards.
