# PolarisHEP Stage 4 run summary

**Command run:** `.venv/bin/python run_full_top200_claim_evolution.py --stage 4`  
**Date:** 2026-03-08

---

## A. High-level summary (for John)

**Did Stage 4 complete successfully?**  
Yes. The run finished with exit code 0. All 64 Stage 3 outputs were turned into claim evolution cards.

**How many card files were generated?**  
- **64** card JSON files: `data/claim_evolution_cards/{cn}.json`  
- **64** card Markdown files: `data/claim_evolution_cards/{cn}.md`  
- **64** records in `data/claim_evolution_cards/all_cards.jsonl` (one per paper)

**Do the cards look useful overall?**  
Yes. Cards are readable and structured: claim text, relation counts, representative citations, field status, key follow-up papers, and short interpretations. Strong cards (e.g. 810127, 862424, 946813) clearly summarize how the field used, supported, limited, or disputed each claim. Weaker cards (e.g. 716284 with no matches, 823754 with one generic citation) are still coherent and correctly labeled as weak_signal or adopted/refined.

**Do the field status outputs look reasonable?**  
Yes. Distribution: weak_signal 75, adopted 46, adopted_with_limits 30, refined 15, contested 8. Status aligns with relation counts (e.g. contested when disputes ≥ 1, adopted_with_limits when limits ≥ 1). No obvious mislabels in the sample.

**Should the benchmark report be regenerated?**  
Yes. The runner already wrote an updated `data/full_top200_benchmark_report.json` after Stage 4 (64 papers with cards, 174 total cards, full relation and field_status counts). For a single consolidated view, run:  
`.venv/bin/python run_full_top200_claim_evolution.py --report-only`  
That will refresh processability and the benchmark report from the current card set.

---

## B. Low-level summary (for ChatGPT)

### 1. Coverage benchmarks

| Metric | Value |
|--------|--------|
| Number of **Stage 3 input files** available at start | 64 |
| Number of **papers processed** in Stage 4 | 64 |
| Number of **card JSON files** written | 64 |
| Number of **card Markdown files** written | 64 |
| Number of **records** in `all_cards.jsonl` | 64 |
| Number of papers **skipped** | 0 |
| Number of **failures** | 0 |

### 2. Card coverage benchmarks

Across all generated cards (174 claim-level cards):

| Metric | Value |
|--------|--------|
| Total number of **claims** represented in cards | 174 |
| Claims with **≥ 1** matched citation statement | 99 |
| Claims with **0** matched citation statements | 75 |
| Claims with **≥ 1 non-use** relation (refines/limits/disputes) | 53 |
| Claims with **≥ 1 limit** | 34 |
| Claims with **≥ 1 dispute** | 8 |

(38 claim cards have at least one limit or dispute; 34 have ≥1 limit, 8 have ≥1 dispute, with overlap where a claim has both.)

### 3. Field status benchmarks

Distribution of `field_status` across all 174 claim cards:

| Field status | Count |
|--------------|--------|
| weak_signal | 75 |
| adopted | 46 |
| adopted_with_limits | 30 |
| refined | 15 |
| contested | 8 |

No other statuses are present. Schema is consistent.

### 4. Key follow-up benchmarks

| Metric | Value |
|--------|--------|
| Claims with **≥ 1 key follow-up paper** | 99 |
| Claims with **key refining** papers | 21 |
| Claims with **key limiting** papers | 34 |
| Claims with **key disputing** papers | 8 |
| **Distinct downstream papers** selected as key follow-up (any relation) | 15 (uses) + 25 (supports) + 12 (refines) + 20 (limits) + 6 (disputes) — not deduplicated across relation types; total unique papers would be a union of these sets. |

(Report gives distinct_downstream_papers_by_relation; total distinct papers appearing as key follow-up at least once can be computed from card JSONs if needed.)

### 5. Quality sampling (5 cards)

**862424 (Drell–Yan small qT, collinear anomaly):** Strong. Readable; claim text, counts, and interpretations align. C1 adopted, C2 adopted_with_limits (support + limit), C3 refined. Representative citations (e.g. TMD factorization limitations) are informative. Key follow-up papers (e.g. 2669575 for limits) make sense.

**810127 (Parton distributions for the LHC – MSTW):** Strong. C1 adopted_with_limits (many uses, one limits); C2 contested (uses, supports, limits, disputes). Key follow-up includes “PDF fit in the fixed-flavor-number scheme” for limits and disputes. Representative evidence and why_selected are coherent. Field status (adopted_with_limits, contested) is reasonable.

**946813 (Factorization theorem Drell–Yan low qT):** Strong. C1 has uses, supports, refines, and limits; field_status adopted_with_limits. Key follow-up papers and representative statements are on-topic (EFT methodology, factorization theorem, limitations). Card is informative for claim evolution.

**823754 (Sivers effect in DIS):** Weaker/generic. Readable and coherent. All three claims tied to the same downstream paper (946664, lattice QCD) with one citation snippet. Representative statements are generic TMD context rather than specific to the Sivers observation. Field status (adopted, refined) is consistent with the relation counts but the evidence is thin.

**716284 (Resummation threshold logarithms EFT):** Weak. Only two claims; both have zero matches. Cards correctly show weak_signal, empty representative evidence, and “No citation statements in this set were aligned to this claim.” Structure is correct; content is empty because Stage 3 had no alignments for this paper.

**Summary:** Cards are readable and coherent. Field status matches relation counts. Representative citations and key follow-up papers are informative when matches are present and varied; when there is only one citation or no matches, cards are appropriately thin or weak_signal.

### 6. Failure benchmark

**Failures:** None. All 64 Stage 3 input files were processed; 64 JSON and 64 Markdown card files were written; `all_cards.jsonl` has 64 lines.

### 7. Scientific usefulness benchmark

- **Scientifically useful as claim evolution summaries?** Yes. Cards answer “How did the field react to this claim?” with relation counts, representative evidence, and key follow-up papers. They support reporting and manual review.
- **Field status assignments reasonable overall?** Yes. weak_signal for no matches; adopted when only uses/supports; adopted_with_limits when limits present; refined when refines present; contested when disputes present. Order of precedence in the schema is respected.
- **Good enough for benchmark reporting and manual review?** Yes. The full top-200 benchmark report has been updated (64 papers with cards, 174 cards, relation and field_status distributions). Cards are ready for systematic inspection and for use in reports or downstream tools.

### Recommendation

- Regenerate the benchmark report once if desired:  
  `.venv/bin/python run_full_top200_claim_evolution.py --report-only`  
- Use the 64 card JSON/MD files and `all_cards.jsonl` as the canonical claim-evolution artifact for the current pipeline run. No Stage 4 logic or schema changes are required for this run.
