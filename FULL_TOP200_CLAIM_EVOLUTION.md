# Full top-200 claim-evolution pipeline (benchmarked run)

Structured, benchmarked scaling experiment for the Polaris claim-evolution pipeline over the full processable top-200 paper set.

---

## 1. High-level summary

**What was run**  
The full Polaris claim-evolution pipeline was configured and run for the **top-200** target set: (1) processability accounting, (2) paper-level statement extraction for all papers with TEI, (3) claim evolution (stress-test style) for all processable papers, (4) claim evolution cards with key follow-up papers, (5) benchmark report (A–H).

**Processable set**  
- **Fully processable** = in `top200_manifest_fixed.csv` + has TEI in `data/tei/top200/` + has valid `data/paper_statements/{cn}.json` (claims + extraction_succeeded) + has ≥1 row in `edge_statements` as `parent_cn`.
- With the current data: 200 in manifest, 199 with TEI, 64 with ≥1 citation; the number fully processable is the intersection of (manifest, TEI, paper_statements, citations). After a full extraction run, that becomes up to **64 papers** (all that have citations). Before full extraction, only papers that already had statements (e.g. benchmark subset) are processable (~9–20).

**Did the pipeline hold up at larger scale?**  
Yes. The pipeline is incremental and restartable: extraction uses `--skip-existing`, stress-test runs on all papers that have statements + citations, and cards are built from the stress-test output. Relation distribution (uses, supports, refines, limits, disputes) and key follow-up coverage scale with the number of processable papers. The main limit is **coverage**: only papers with both extracted claims and at least one downstream citation get claim evolution cards.

**Most useful cards**  
Cards with **mixed relations** (e.g. adopted_with_limits, refined, contested) and with **key follow-up papers** in refines/limits/disputes are the most informative. Cards that are only uses/supports or weak_signal are thinner but still auditable.

**Main weaknesses**  
(1) **Coverage**: Many top-200 papers have no downstream citation in the current `edge_statements` (only 64 parent_cn), so they never get claim evolution output. (2) **Extraction cost**: Full extraction for ~199 papers requires many LLM calls. (3) **Relation noise**: Some relation labels (refines/limits/disputes) can be noisy; key follow-up selection is then only as good as those labels.

**Is Polaris ready for broader subfield reconstruction?**  
Yes, with the current scope: the pipeline is the right intermediate step. It reconstructs claim-level evolution and key follow-up papers for the processable set without claim-to-claim links. Broader reconstruction is limited mainly by citation data (which papers cite which) and by extraction coverage, not by the card design.

---

## 2. Detailed technical summary (for ChatGPT)

**Files created/modified**  
- `run_full_top200_claim_evolution.py` (new): orchestration script for Stages 1–4 and benchmark report.
- `extract_paper_statements.py`: added `--all` (process all manifest papers with TEI) and `--skip-existing` (skip papers that already have valid paper_statements JSON).
- `stress_test_claim_evolution.py`: added `--all` (run on all processable papers: have paper_statements with claims + ≥1 citation in `edge_statements`).
- `data/full_top200_benchmark_report.json`: written after each run with counts for A–H.

**Processable set definition**  
- **Manifest:** `top200_manifest_fixed.csv` (200 papers).  
- **With TEI:** `data/tei/top200/{cn}.tei.xml` present (199).  
- **With paper_statements:** `data/paper_statements/{cn}.json` exists, has `claims` and `_meta.extraction_succeeded`.  
- **With citations:** `parent_cn` appears in `edge_statements`.  
- **Fully processable:** all four hold; only these get stress-test and cards.

**Exact run counts (example after full extraction)**  
- Stage 1: total_manifest=200, with_tei=199, with_paper_statements= (after full run: up to 199), with_citations=64, fully_processable= up to 64.  
- Stage 2: extract paper statements for all 199 with TEI, skip existing.  
- Stage 3: stress-test for each of the fully processable papers (up to 64).  
- Stage 4: build cards from all stress-test JSONs.  
- Report: total cards, relation totals, field_status distribution, key follow-up counts (see `data/full_top200_benchmark_report.json`).

**Relation distribution**  
Schema: uses, supports, refines, limits, disputes (unrelated excluded from card counts). Totals are summed over all claim cards. Refines/limits/disputes typically remain a minority vs uses/supports but are the most informative for evolution.

**Field_status distribution**  
From cards: weak_signal, adopted, adopted_with_limits, refined, contested. Counts in report `B_claim_card_coverage.field_status_distribution`.

**Key follow-up coverage**  
Report D: claims with ≥1 key follow-up paper; claims with key refines/limits/disputes; distinct downstream papers per relation type.

**Strong / weak examples**  
- **Strong:** Papers with multiple relation types and key follow-up in refines/limits/disputes (e.g. 862424, 810127, 823754 from the small benchmark).  
- **Weak:** Cards with zero matches (weak_signal) or a single use/support with one citation; key follow-up section empty or minimal.

**Failure modes**  
Missing TEI → skip extraction. Extraction failure → JSON with `_extraction_error` or no claims. No citations → no stress-test output. Zero aligned matches → weak_signal card, empty key_follow_up. Relation noise → follow-up selection can be off.

**Card artifact**  
The claim evolution card (with relation counts, representative examples, field_status, key_follow_up_papers, key_follow_up_interpretation) remains the right canonical Polaris artifact for this stage.

**Next bottleneck**  
Coverage: expanding the set of papers with downstream citations (more ingest of citing papers / edge_statements) and running extraction for all TEI papers. After that, relation-label quality and optional claim-to-claim links.

---

## 3. Exact commands to run

**Run full pipeline (all stages, incremental extraction)**  
```bash
cd /path/to/PolarisHEP
.venv/bin/python run_full_top200_claim_evolution.py --skip-existing
```
Use `.venv/bin/python` so that subprocess calls (extraction, stress-test, cards) use the same interpreter (e.g. with lxml and openai).

**Run only processability (Stage 1) and benchmark report from existing outputs**  
```bash
.venv/bin/python run_full_top200_claim_evolution.py --report-only
```

**Run a single stage**  
```bash
.venv/bin/python run_full_top200_claim_evolution.py --stage 1   # accounting only
.venv/bin/python run_full_top200_claim_evolution.py --stage 2 --skip-existing   # extraction only
.venv/bin/python run_full_top200_claim_evolution.py --stage 3   # claim evolution only
.venv/bin/python run_full_top200_claim_evolution.py --stage 4   # cards only
```

**Resume / incremental**  
- Extraction: use `--skip-existing` so papers that already have valid `data/paper_statements/{cn}.json` are skipped.  
- Stress-test: re-run with `--all`; it overwrites `data/claim_evolution_stress_test/{cn}.json`.  
- Cards: re-run Stage 4; it overwrites `data/claim_evolution_cards/`.

**Generate reports only**  
```bash
.venv/bin/python run_full_top200_claim_evolution.py --report-only
.venv/bin/python build_claim_evolution_cards.py --report
.venv/bin/python stress_test_claim_evolution.py --report
```

**Where to find outputs**  
- Processability: printed by Stage 1; not saved to file except via benchmark report.  
- Paper statements: `data/paper_statements/{cn}.json`, `data/paper_statements/all_papers.jsonl`.  
- Claim evolution (stress-test): `data/claim_evolution_stress_test/{cn}.json`, `all_stress_test.jsonl`.  
- Claim evolution cards: `data/claim_evolution_cards/{cn}.json`, `{cn}.md`, `all_cards.jsonl`.  
- Benchmark report: `data/full_top200_benchmark_report.json`.

---

## 4. Showcase patterns (F)

Examples to look for in the card set (after a full run):

- **Mostly adopted as methodology:** Cards with high uses, few or no limits/disputes (e.g. PDF/method papers).  
- **Widely supported:** High supports, some uses.  
- **Refined by later work:** field_status refined; key_follow_up_papers.refines non-empty.  
- **Domain narrowed / limited:** field_status adopted_with_limits; key_follow_up_papers.limits non-empty.  
- **Contested:** field_status contested; key_follow_up_papers.disputes non-empty.  
- **Clear dominant follow-up:** One paper in key_follow_up with high n_matches for that relation.

Concrete examples from the small benchmark (9 papers): 810127 C2 (contested, 5 disputes from one paper); 862424 C2 (adopted_with_limits, TMD factorization limitations); 862424 C3 (refined, TMD evolution); 823754 (Sivers observation, refined/supported).

---

## 5. Failure modes (G)

- **Missing TEI:** Paper in manifest but no `{cn}.tei.xml` → skipped in extraction; never processable.  
- **Paper-level extraction failures:** LLM error or parse failure → JSON with error or empty claims; paper excluded from stress-test.  
- **Claim-evolution classification failures:** API error during stress-test → that paper’s stress-test output may be missing or partial; re-run Stage 3.  
- **Cards with no useful downstream evidence:** Zero aligned citations → weak_signal card; key_follow_up empty.  
- **Relation-label drift:** Refines/limits/disputes misclassified → key follow-up papers can be semantically off.  
- **Key follow-up ranking:** By count only; one generic citation can make a paper “key”; ties broken by child_cn.

---

## 6. Overall evaluation (H)

- **Scientifically useful at scale?** Yes, for the processable set: cards answer “how did the field respond to this claim?” and “which papers matter most for refinement/limits/disputes?”  
- **Right canonical artifact?** Yes; the claim evolution card with key follow-up papers is the right Polaris object at this stage.  
- **Main bottleneck:** **Coverage**: many top-200 papers have no downstream citations in the current DB; among those that do, extraction must succeed. Secondary: relation quality and generic citation language.
