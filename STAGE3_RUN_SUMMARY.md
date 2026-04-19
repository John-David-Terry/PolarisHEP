# PolarisHEP Stage 3 run summary

**Command run:** `.venv/bin/python run_full_top200_claim_evolution.py --stage 3`  
**Date:** 2026-03-08

---

## A. High-level summary (for John)

**Did Stage 3 complete successfully?**  
Yes. The run finished with exit code 0. All 64 processable papers were processed without errors.

**How many papers were processed?**  
**64 papers.** A paper is processable for Stage 3 if it has (1) valid paper_statements with non-empty claims and (2) at least one downstream citation in `edge_statements`. All 64 eligible papers were processed; 64 output files were written to `data/claim_evolution_stress_test/`. No papers were skipped and no failures occurred.

**How many claims got meaningful downstream matches?**  
**99 of 174 claims** (57%) had at least one matched citation statement (non-unrelated). **75 claims** (43%) had zero matches. Across all claims, **260 claim–citation matches** were assigned (uses, supports, refines, limits, disputes). Mean matches per claim is 1.49; median is 1.

**Do the outputs look scientifically useful overall?**  
Yes. The system is **not** only finding “uses”: relation totals are **uses 38, supports 112, refines 28, limits 63, disputes 19**. So refines, limits, and disputes are well represented. **48 of 64 papers** have at least one non-use relation; **26 papers** have at least one “limits” match and **7** have at least one “disputes” match. Sampled outputs show that matches are often on-topic and labels (e.g. “limits” for TMD factorization caveats, “uses” for MSTW PDF usage) are reasonable. Some papers have generic or repetitive alignments (same citation snippet matched to multiple claims).

**Should we move on to Stage 4?**  
Yes. Stage 3 outputs are suitable for building claim evolution cards. Recommend running Stage 4 to produce cards and key follow-up papers for all 64 papers.

---

## B. Low-level summary (for ChatGPT)

### 1. Coverage benchmarks

| Metric | Value |
|--------|--------|
| Number of papers **eligible** for Stage 3 at start | 64 |
| Number of papers **actually processed** | 64 |
| Number of **output files** written in `data/claim_evolution_stress_test/` | 64 |
| Number of papers **skipped** | 0 |
| Number of **failures** | 0 |

Eligibility = has valid `data/paper_statements/{cn}.json` with non-empty `claims` and at least one row in `edge_statements` with `parent_cn` = that paper.

### 2. Match / alignment benchmarks

| Metric | Value |
|--------|--------|
| Total number of **claims** analyzed | 174 |
| Total number of **downstream citation statements** considered | 363 |
| Total number of **claim–citation matches** (non-unrelated) | 260 |
| Number of claims with **≥ 1** matched citation | 99 |
| Number of claims with **0** matched citations | 75 |
| **Mean** matched citation statements per claim | 1.494 |
| **Median** matched citation statements per claim | 1.0 |

### 3. Relation distribution benchmarks

Totals across all non-unrelated matches:

| Relation | Total |
|----------|--------|
| uses | 38 |
| supports | 112 |
| refines | 28 |
| limits | 63 |
| disputes | 19 |

**Total** = 260.

- Number of **papers** with at least one **non-use** relation (supports/refines/limits/disputes): **48**
- Number of papers with at least one **limit**: **26**
- Number of papers with at least one **dispute**: **7**

### 4. Quality sampling (5 papers)

**810127 (Parton distributions for the LHC – MSTW):** Strong case. Many citations explicitly use MSTW2008 PDFs; labels are “uses” or “supports.” One citation (ABKM09 fit, αs comparison) is labeled “limits” with a plausible explanation. Matches are on-topic and informative.

**862424 (Drell–Yan small qT, collinear anomaly):** Strong. Claim C2 (naive factorization broken by collinear anomaly) has one “supports” and one “limits” (TMD factorization limitations from a later paper). Explanations are grounded. Good mix of relation types.

**823754 (Observation of Sivers effect in DIS):** Weak/generic. Only one downstream citation (lattice QCD / TMD review). The **same** generic TMD paragraph is aligned to all three claims with labels “supports” and “refines.” The relation to the specific Sivers **observation** claim is loose; this is a case where one citation is over-used across claims.

**779762 (Wilson lines and TMD PDFs, RG analysis):** Mixed. One citation (lattice QCD, gauge invariance / Wilson lines) is aligned to C1 as “limits” (renormalization/gauge concerns) and to C3 as “supports.” The “limits” assignment is reasonable; the same snippet for two claims is somewhat generic.

**716284 (Resummation of threshold logarithms in EFT):** Weak. Only 1 citation statement; **0** alignments to its 2 claims. So both claims have zero matches. The single citing sentence did not trigger any claim–citation link (either correctly unrelated or missed).

**Summary:** Matched statements often do relate to the claim; assigned labels (uses/supports/refines/limits/disputes) are mostly reasonable. Some outputs are generic (one citation matched to many claims) or have zero matches when citation set is small.

### 5. Failure benchmark

**Failures:** None. All 64 eligible papers were processed and produced valid JSON in `data/claim_evolution_stress_test/{cn}.json`.

### 6. Scientific usefulness benchmark

- **Scientifically meaningful?** Yes. The outputs support questions like “How is this claim used, supported, refined, limited, or disputed?” with evidence tied to specific citation sentences and explanations.
- **Mostly “uses” or also refines/limits/disputes?** The system is **already detecting** meaningful non-use relations: **refines 28, limits 63, disputes 19** vs uses 38, supports 112. Limits are the second-largest category after supports.
- **Stage 4 worth running immediately?** Yes. Recommend running Stage 4 to build claim evolution cards and key follow-up papers for all 64 papers.

### Recommendation

- Proceed to **Stage 4** to generate cards and key follow-up papers from the 64 Stage 3 outputs.
- No changes to Stage 3 logic required for this run. Optional future improvement: reduce over-alignment of a single citation to many claims (e.g. cap or down-weight when the same snippet is matched to multiple claims in one paper).
