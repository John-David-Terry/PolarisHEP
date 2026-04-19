# Claim-Tracking Benchmark (Polaris)

Proof-of-concept experiment: **paper-level claims + citation-level statements → claim evolution through the literature.**

Goal: determine whether we can align later citation statements to specific claims and classify how the field uses, supports, refines, limits, or disputes each claim.

---

## High-level summary

- **What was built:** A script `claim_tracking.py` that, for selected benchmark papers, loads (1) extracted claims from `data/paper_statements/{cn}.json` and (2) citation statements from `edge_statements` where `parent_cn` = that paper. For each citation statement, an LLM classifies which claim(s) it relates to and the relation type (`uses`, `supports`, `refines`, `limits`, `disputes`). Outputs are one JSON per paper in `data/claim_tracking/` with claims, matches (citation + relation), and a short grounded summary per claim.
- **Did the experiment work?** Yes. The pipeline runs end-to-end. On 4 papers (11 claims, 67 citation statements), 18 citation statements were aligned to at least one claim. Relation labels are mostly **uses** (16) and **supports** (4); **refines**, **limits**, and **disputes** did not appear in this small set. So the **signal is present**: we can link citation sentences to specific claims and get plausible relation types. The field’s response to MSTW (810127) is clearly “use as methodology,” which matches expectations.
- **Can Polaris track the life of a claim?** In principle, yes. We can say things like “many later papers use this as methodology” (810127) or “one later paper uses this, one supports it” (829121). We do not yet see refines/limits/disputes in this benchmark, which may be due to corpus size, citation style, or the need for a larger/different benchmark.
- **Patterns observed:** (1) **Uses** dominates when the cited paper is a standard tool (e.g. MSTW PDFs). (2) **Supports** appears when a later paper cites a specific result (e.g. αs value). (3) Papers with technical, narrow claims (e.g. 862424 qT/collinear anomaly) had zero alignments in this run—citation sentences may be too generic or the model was conservative. (4) Some claims attract no aligned citations in the current set (e.g. 846542 C2, C3; 862424 all).
- **Main weaknesses:** (1) Relation set is skewed to **uses**; **refines**/**limits**/**disputes** need more data or clearer prompts. (2) One paper (862424) had 0 alignments despite 13 citations—possible mismatch between claim phrasing and how citers refer to the work. (3) Same citation can be aligned to multiple claims (by design); duplicate-looking entries can appear when the same sentence is returned for more than one claim. (4) Minimal summaries are count-based only; no narrative beyond counts.

---

## Benchmark paper selection

| Control number | Title (short) | Why chosen | Extracted claims | Citation statements |
|----------------|---------------|------------|------------------|----------------------|
| **810127** | Parton distributions for the LHC (MSTW 2008) | Top-cited in subgraph; PDF set widely used as methodology | 2 | 39 |
| **862424** | Drell-Yan at small qT, collinear anomaly | Important theory paper; clear technical claims | 4 | 13 |
| **829121** | 3,4,5-flavor NNLO parton from DIS | PDF/αs determination; mix of methods and results | 2 | 10 |
| **846542** | First unbiased global NLO PDF (NNPDF2.0) | Methodology and consistency claims | 3 | 5 |

All four have both paper-level extraction in `data/paper_statements/` and citation statements in `edge_statements` with `parent_cn` = that paper.

---

## Alignment coverage

| Paper | Claims | Citation statements | Aligned (distinct) |
|-------|--------|----------------------|--------------------|
| 810127 | 2 | 39 | 15 |
| 862424 | 4 | 13 | 0 |
| 829121 | 2 | 10 | 2 |
| 846542 | 3 | 5 | 1 |
| **Total** | **11** | **67** | **18** |

- **Unaligned:** 67 − 18 = 49 citation statements were not assigned to any claim (either the model returned an empty list or the citation was judged not to refer to a specific claim).

---

## Relation distribution

| Relation | Count (all papers) |
|----------|--------------------|
| uses | 16 |
| supports | 4 |
| refines | 0 |
| limits | 0 |
| disputes | 0 |

So in this run, **uses** and **supports** are the only relation types that appear. **Refines**, **limits**, and **disputes** are plausible for future runs with more or different citations and possibly sharper prompts.

---

## Quality inspection

- **Alignment:** Where alignments exist (810127, 829121, 846542), citation statements are generally about the cited paper’s PDF set or result; assigning them to “MSTW 2008 PDFs” or “αs determination” is sensible. For 862424, the lack of alignments may mean citers refer to the paper as a whole (“we use the framework of [X]”) rather than to a specific claim.
- **Relation labels:** “Uses” fits when later papers adopt MSTW/CT10/NNPDF as their PDF choice; “supports” fits when a later paper quotes the αs value from 829121. No clear mislabels in the sample.
- **Does “uses” absorb too much?** In this benchmark, yes—most aligned citations are methodological (choice of PDF set). That is consistent with the role of these papers. To get more **refines**/**limits**/**disputes**, we’d need citations that explicitly extend, restrict, or argue with a claim.
- **Why are limits/disputes rare?** Likely a mix of (1) small corpus, (2) citation style (methodology and support are more common in our sample), and (3) model conservatism (only assigning when the relation is clear).

---

## Mini-case studies (3 claims)

### Case 1: 810127 C1 — “We present updated … parton distribution functions ('MSTW 2008') …”

- **Claim:** MSTW 2008 PDFs from global analysis of hard-scattering data.
- **Representative citation statements and relations:**
  - LHCb long-lived particles: “The acceptance factor obtained from Pythia with the MSTW2008 PDF set [28]…” → **uses**
  - Higgs radiative corrections: “for parton density sets we use MSTW 2008NNLO [88]” → **uses**
  - Forward-backward asymmetry: “reweight … to obtain samples that mimic … MST[W]” → **uses**
  - Z production PbPb: “using MSTW08 PDFs [22] and modelling energy loss” → **uses**
- **Interpretation:** The claim is about providing a PDF set; the field’s response in this sample is almost entirely **uses**—later papers adopt MSTW as their PDF choice. This is exactly the “methodology” pattern.

### Case 2: 810127 C2 — “These parton distributions supersede … 'MRST' sets and should be used for the first LHC data-taking”

- **Claim:** MSTW supersedes MRST; recommended for LHC.
- **Representative citation statements and relations:**
  - TMD evolution paper: “f up/P … has been taken from the MSTW data set [40]” → **uses**
  - PDF fit (fixed-flavor): discussion of χ² profiles and data sets, reference to global PDF fits → **supports** (appears multiple times from same paper)
- **Interpretation:** Again mostly **uses** (MSTW as dataset/methodology). **Supports** appears where the citing paper discusses global fits and is consistent with the “should be used” claim. No refines/limits/disputes in this set.

### Case 3: 829121 C1 — “We obtain at NNLO αs(M²Z) = 0.1135 ± 0.0014 …”

- **Claim:** NNLO αs determination (fixed-flavor and BMSN).
- **Representative citation statements and relations:**
  - ABKM09/PDF fit: “predicted cross sections … from NNLO variants of the ABKM09 fit [12]” and uncertainty on PDFs/αs → **uses**
  - ABM11 parton: “αs(MZ) = 0.1134 ± 0.0011 … comparable with our earlier determination αs(MZ) = 0.1135 ± 0.0014(exp.) [6]” → **supports**
- **Interpretation:** One citation **uses** the paper’s framework (ABKM09/NNLO); one **supports** the specific αs result by comparing to it. Good illustration of uses vs supports.

---

## Failure modes

- **Citation too vague to map to a claim:** Many of the 49 unaligned citations describe the cited paper generally (“we use the PDF set from [X]”) without pointing to a specific claim. The model reasonably returns no claim_id.
- **Multiple claims could match:** e.g. “We use MSTW 2008” could support both C1 (we present MSTW) and C2 (use for LHC). The model sometimes assigns to one claim only; we allow multiple (claim_id, relation) per citation.
- **Relation ambiguous:** “We use MSTW” is clearly **uses**; “our value is consistent with [X]” could be **supports** or **refines**. The current schema doesn’t capture that nuance.
- **Citation describes paper generally:** Common in 862424—citations may say “following [X]” or “we use the framework of [X]” without naming the collinear anomaly or qT formula. So no specific claim is aligned.
- **Original claim too broad or too vague:** 846542 C2/C3 (consistency, no tension) are high-level; citation sentences in the set are more about PDF usage than about “tension between datasets.” So those claims get no matches.

---

## Complementarity (paper vs field)

- **810127 (MSTW):** The paper presents and recommends a PDF set. The field mostly **uses** it as methodology (PDF choice in predictions). So: paper says “we provide X and you should use it”; field does use it. Strong agreement.
- **862424 (qT / collinear anomaly):** The paper makes precise technical claims (exact expression, collinear anomaly, factorization). The 13 citation statements did not align to any claim—citations may refer to the paper as a whole. So: paper is remembered as a framework/reference; specific claims are not tracked in this sample.
- **829121 (NNLO parton / αs):** Paper gives αs and illustrates implications. We see one **uses** (NNLO fit as input) and one **supports** (αs value comparison). Field both uses the framework and engages with the numerical result.
- **846542 (NNPDF2.0):** Paper claims unbiased NLO PDF determination and consistency. Only one citation aligned (PDF set usage → **uses**). So: field remembers “NNPDF as a PDF set” more than “no tension” or “methodology.”

**Summary:** Where the paper’s main contribution is a **tool** (PDF set), the field’s response in our sample is mostly **uses**. Where the paper’s contribution is a **specific result** (αs), we also see **supports**. We do not yet see clear **refines**/**limits**/**disputes** in this small benchmark.

---

## Technical summary for ChatGPT

- **Files:** `claim_tracking.py` (new), `data/claim_tracking/{cn}.json` (4 papers), `data/claim_tracking/all_claim_tracking.jsonl`, `CLAIM_TRACKING_BENCHMARK.md`.
- **Inputs:** (1) `data/paper_statements/{cn}.json` → `claims` array (claim text); (2) SQLite `edge_statements` + `papers` for `parent_cn` = benchmark paper → citation statements and child titles.
- **Relation schema:** `uses`, `supports`, `refines`, `limits`, `disputes`; citation can be “unrelated” (not assigned to any claim). No `mentions` in output.
- **Pipeline:** For each citation statement, one LLM call (OpenAI `gpt-4o-mini`): input = paper title + list of claims with IDs (C1, C2, …) + citation text; output = JSON array of `{ "claim_id", "relation" }`. Each (citation, claim_id) pair is stored as a match with that relation. Per-claim summary = counts per relation + one grounded sentence (e.g. “N later paper(s) use this as methodology”).
- **Benchmark counts:** 4 papers, 11 claims, 67 citation statements, 18 aligned; relations: uses 16, supports 4, refines 0, limits 0, disputes 0.
- **Viability:** The pipeline is viable: we can align citations to claims and assign relation types. The signal is clearest for “methodology” papers (uses) and for specific numerical results (supports). Refines/limits/disputes will need more data or different prompts.
- **Next bottleneck:** Scale (more papers, more citations), relation balance (encouraging refines/limits/disputes where present), and possibly multi-claim handling (one citation → multiple claims with different relations).

---

## Commands

```bash
# One paper (e.g. 846542, 5 citations)
python claim_tracking.py --db inspire.sqlite --paper 846542

# Full benchmark set (4 papers)
python claim_tracking.py --db inspire.sqlite

# Report only (from existing JSONs)
python claim_tracking.py --report
```

**Outputs:** `data/claim_tracking/{cn}.json`, `data/claim_tracking/all_claim_tracking.jsonl`.

**Requires:** `OPENAI_API_KEY`, `openai` package, and existing `data/paper_statements/` JSONs and `edge_statements` in the DB.
