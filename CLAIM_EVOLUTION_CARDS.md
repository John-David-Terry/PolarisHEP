# Claim Evolution Cards (Canonical Polaris Artifact)

Claim evolution cards are the **standard scientific output object** for the current stage of Polaris: a grounded, readable summary of how the field responded to each paper-level claim (uses, supports, refines, limits, disputes).

---

## High-level summary (key follow-up papers)

- **What was built:** The card pipeline `build_claim_evolution_cards.py` now adds **key follow-up papers** to each claim. It reads stress-test output from `data/claim_evolution_stress_test/` and produces cards with: claim text, relation counts, representative examples, interpretation, **field_status**, and **key_follow_up_papers** (top 1–3 downstream papers per relation type by citation count), each with `child_cn`, `child_title`, `n_matches`, representative snippets, and `why_selected`; plus **key_follow_up_interpretation**. Outputs: `data/claim_evolution_cards/{cn}.json`, `{cn}.md`, `all_cards.jsonl`.
- **Did adding key follow-up papers make the cards more useful?** Yes. The cards now answer "which later papers most strongly refined/limited/disputed this claim?" in one place, without building direct claim-to-claim links, strengthening the vertical artifact and moving Polaris closer to scientific lineage in an auditable way.
- **Strongest follow-up patterns:** **Limits** and **supports** have the most key follow-up coverage (10 and 9 claims; 8 distinct downstream papers each for limits/uses). **Refines** and **disputes** are rarer but when present highlight the most relevant downstream work (e.g. one paper dominating dispute with 5 aligned citations).
- **Main remaining weakness:** Selection is by citation count only; a single generic mention can make a paper "key." Relation-label noise can make the listed follow-up paper less semantically sharp. Weak-signal claims still have no follow-up papers.
- **Should this be the standard Polaris artifact?** Yes. Key follow-up papers should be part of the standard claim evolution card schema—the right intermediate step before any claim-to-claim graph: grounded, deterministic, and useful for "which papers matter most for this claim's downstream evolution?"

---

## Source and selection rule

- **Canonical source:** `data/claim_evolution_stress_test/`. Stress-test outputs are used (rather than the first claim-tracking run) because they contain **richer relations** (refines, limits, disputes) and **explanations** per alignment.
- **Selection rule:** **One card per extracted claim.** Every claim in the stress-test JSON for a paper gets a card, including claims with zero aligned citations (those receive relation_counts all zero, empty representative_examples, and field_status `weak_signal`).

---

## Card schema

Each paper document in `data/claim_evolution_cards/{cn}.json` has the form:

```json
{
  "control_number": 862424,
  "title": "...",
  "claims": [
    {
      "claim_id": "C1",
      "claim_text": "...",
      "relation_counts": {
        "uses": 0,
        "supports": 1,
        "refines": 0,
        "limits": 0,
        "disputes": 0
      },
      "representative_examples": {
        "uses": [],
        "supports": [ { "child_cn", "child_title", "citation_statement", "explanation" } ],
        "refines": [],
        "limits": [],
        "disputes": []
      },
      "interpretation": "...",
      "field_status": "adopted"
    }
  ],
  "_meta": { "source": "claim_evolution_stress_test", "n_claims": N }
}
```

- **claim_text:** From paper-level extraction (stress-test input).
- **relation_counts:** Counts of aligned citation statements per relation type.
- **representative_examples:** For each relation type, up to 3 examples; each example has `child_cn`, `child_title`, `citation_statement` (max 500 chars), and `explanation` when present (max 300 chars). Deduped by citation snippet (first 80 chars).
- **interpretation:** Carried over from stress-test (count-based, e.g. “N later paper(s) use this … and M limit its domain”).
- **field_status:** Assigned by rule (see below).
- **key_follow_up_papers:** For each relation type, the top 1–3 downstream papers by number of aligned citation statements (see "Key follow-up papers" below). Each entry: `child_cn`, `child_title`, `n_matches`, `representative_statements` (up to 3 snippets), `why_selected`.
- **key_follow_up_interpretation:** Short grounded sentence(s) about which papers most strongly refine, limit, dispute, or support the claim.

### Key follow-up papers (selection rule)

- **Canonical source for matches:** `data/claim_evolution_stress_test/` (relation-level matches with child_cn, relation, citation_statement, explanation).
- **Scoring:** For each claim and each relation type, group aligned citation matches by downstream paper (child_cn). Score each paper = number of matched citation statements for that claim and relation. No tie-break needed beyond count; ties are broken by child_cn ascending for determinism.
- **Selection:** Top 1–3 papers per relation type (papers with at least one match). Per paper: store `child_cn`, `child_title`, `n_matches`, up to 3 `representative_statements` (citation snippets), and `why_selected` (e.g. "N citation statement(s) aligned as limits (key follow-up for this claim).").
- **Interpretation:** One short sentence per relation type when key papers exist (e.g. "The strongest downstream refinement comes from: [title] (N citation(s)).").

### field_status rules (auditable)

Applied in order; first match wins:

1. **contested** — `disputes >= 1`
2. **adopted_with_limits** — `limits >= 1`
3. **refined** — `refines >= 1`
4. **adopted** — `uses >= 1` or `supports >= 1`
5. **weak_signal** — no aligned citations (all counts 0)

---

## Coverage (current benchmark)

From `python build_claim_evolution_cards.py --report`:

| Metric | Value |
|--------|--------|
| Papers processed | 9 |
| Claims → cards | 26 |
| Claims with zero downstream matches | 8 |
| Claims with ≥1 non-use relation (refines/limits/disputes) | 12 |
| Claims with limits or disputes | 10 |
| Claims with only uses/supports (and ≥1 match) | 6 |
| **Field status distribution** | weak_signal: 8, adopted_with_limits: 9, adopted: 6, contested: 1, refined: 2 |

### Key follow-up papers: benchmark report

**A. Coverage**

| Metric | Value |
|--------|--------|
| Claims with at least one key follow-up paper | 18 |
| Claims with key papers in refines | 3 |
| Claims with key papers in limits | 10 |
| Claims with key papers in disputes | 1 |
| Claims with only uses/supports follow-ups | 6 |
| Weak-signal claims (no matches, no follow-up papers) | 8 |

**B. Relation-specific follow-up coverage (distinct downstream papers across all claims)**

| Relation | Distinct key follow-up papers |
|----------|-------------------------------|
| uses | 8 |
| supports | 9 |
| refines | 3 |
| limits | 8 |
| disputes | 1 |

**C. Showcase cases** (from report output)

- **Strong limits:** e.g. Paper 810127 Claim C1, Paper 829121 Claim C1 → key limiting paper "PDF fit in the fixed-flavor-number scheme" (2 citations each).
- **Strong disputes:** Paper 810127 Claim C2 → "PDF fit in the fixed-flavor-number scheme" (5 citations).
- **Strong refines:** Paper 810127 Claim C2 (ABM11), Paper 823754 Claim C2 (Sivers/Boer–Mulders).
- **One paper dominates:** Paper 810127 Claim C2 [disputes] (5 citations from one paper); Paper 810127 Claim C1 [limits] (2 citations from one paper).

**D. Card usefulness**

- The key-follow-up section makes the card more informative: it answers "which later papers most strongly refined/limited/disputed this claim?" without building claim-to-claim links.
- It moves Polaris closer to scientific lineage (downstream papers are named and ranked by evidence strength) without overclaiming: selection is deterministic and grounded in citation counts.
- Selected follow-up papers are plausible where relation labels are accurate; in a few cases relation noise (e.g. "disputes" on methodology contrasts) can make the listed paper less semantically sharp.

**E. Failure modes**

- **Weak generic citations:** A key follow-up paper can be chosen on a single short methodology mention; the card still reflects that the paper is the top contributor for that relation on that claim.
- **Ties / weak evidence:** When several downstream papers tie at 1 citation each, all appear; the "key" paper is then the one with smallest child_cn (deterministic but not always the most important).
- **Noisy relation labels:** If refines/limits/disputes are over- or under-applied, follow-up selection inherits that noise.
- **Shallow cards:** Claims with zero or one citation still get a key follow-up entry when applicable; the card is grounded but may feel thin.
- **Too little downstream evidence:** Weak-signal claims have no key follow-up papers; the card correctly shows empty key_follow_up_papers.

**F. Recommendation**

**Yes.** "Key follow-up papers" should be part of the standard claim evolution card schema at this stage. It strengthens the vertical artifact (paper → claim → citation responses → card) with a concise, auditable list of the most important downstream papers per relation type and supports questions like "which later papers most strongly limited this claim?" without introducing claim-to-claim links.

---

## Card quality

- **Readability:** The Markdown files (e.g. `862424.md`) are easy to scan: claim, counts, field status, interpretation, then representative evidence by type. JSON is consistent and machine-friendly.
- **Representative examples:** Examples are drawn from the stress-test matches, grouped by relation, deduped, and capped at 3 per type. They are grounded and usually informative; in a few cases a different choice might be more illustrative, but the rule is simple and auditable.
- **Interpretations:** They reflect the evidence (count-based, no free narrative). For mixed relations they correctly summarize e.g. “used and limited” or “supported and disputed.”
- **Standalone use:** A single card (one claim + counts + examples + interpretation + field_status) is a usable scientific artifact for “how did the field respond to this claim?”

---

## Showcase examples

- **Mostly used:** 810127 C1 (MSTW PDFs) — uses=10, supports=3, limits=2; field_status adopted_with_limits. Interpretation: “10 later paper(s) use this as methodology or framework and 3 support or confirm it and 2 limit its domain or add caveats.”
- **Supported:** 862424 C1 (exact qT expression) — supports=1 only; field_status adopted. Representative example: citation fixing coefficients from Drell–Yan calculations, with explanation.
- **Refined:** 862424 C3 (factorization theorem for two transverse PDFs) — supports=2, refines=1; field_status refined. Representative refines: “TMD factorization … has since evolved into an independent and powerful tool…”
- **Limited:** 862424 C2 (naive factorization broken by collinear anomaly) — supports=1, limits=1; field_status adopted_with_limits. Representative limits: “theorem … suffers from several theoretical inconsistencies and practical limitations…”
- **Disputed:** 810127 C2 (MSTW supersede MRST; use for LHC) — uses=2, supports=18, refines=1, limits=6, disputes=5; field_status contested. Representative disputes: later fit contrasting αs and methodology with MSTW.

---

## Failure modes

- **Too little downstream evidence:** 8 claims have zero aligned citations; their cards are honest (weak_signal) but not informative for evolution.
- **Generic matches:** Some “uses” or “supports” are short methodology mentions; the card still reflects that the claim is used rather than refined/limited/disputed.
- **Interpretation strength:** All interpretations are count-based; they do not say “strongly” or “weakly” contested. field_status adds a simple label; more nuance would require extra rules or narrative generation.
- **Multiple claims, same evidence:** When one citation is aligned to several claims, it appears in each card’s representative_examples; cards remain per-claim consistent but evidence is shared.
- **Low-information cards:** weak_signal cards with no examples are placeholders; they are useful for coverage but not for “how did the field respond?”

---

## Recommendation

- **Card format as canonical output:** Yes. The claim evolution card is the right standard Polaris artifact at this stage: it is grounded, comparable across papers, and suitable for benchmarking, inspection, and later UI.
- **Next steps after cards:** (1) Use cards as the primary object for any Polaris demo or report. (2) Optionally add a second pass to pick “best” representative examples (e.g. by length or explanation quality). (3) Scale card generation to more papers as more stress-test (or claim-tracking) outputs become available. (4) Keep field_status rules explicit and extend only when needed (e.g. “mixed” or “strongly_contested”).

---

## Technical summary for ChatGPT

- **Files created:** `build_claim_evolution_cards.py`, `data/claim_evolution_cards/{cn}.json` (9 papers), `data/claim_evolution_cards/{cn}.md` (9 papers), `data/claim_evolution_cards/all_cards.jsonl`, `CLAIM_EVOLUTION_CARDS.md`. README updated.
- **Upstream source:** `data/claim_evolution_stress_test/` (canonical); stress-test outputs have relation types and explanations; first claim-tracking run is not used for cards.
- **Schema:** See “Card schema” above. Each claim has claim_id, claim_text, relation_counts (uses/supports/refines/limits/disputes), representative_examples (dict of lists, max 3 per type, with child_cn, child_title, citation_statement, explanation), interpretation (string), field_status (enum: contested, adopted_with_limits, refined, adopted, weak_signal).
- **Representative examples:** From stress-test `matches`; group by relation; dedupe by citation_statement[:80]; take first 3 per type.
- **Interpretation:** Copied from stress-test claim’s `interpretation` or `summary_sentence` (count-based).
- **field_status rules:** See “field_status rules” above; order: contested → adopted_with_limits → refined → adopted → weak_signal.
- **Coverage:** 9 papers, 26 cards; 8 weak_signal, 10 with limits or disputes, 6 only uses/supports.
- **Showcase:** See “Showcase examples” above (used, supported, refined, limited, disputed).
- **Failure modes:** Zero-match claims; generic uses; count-only interpretation; shared evidence across claims.
- **Next:** Treat cards as canonical; optionally refine example selection; scale with more papers.
- **Key follow-up papers (this step):** Same script; no new file. Canonical source for matches: `data/claim_evolution_stress_test/`. For each claim and relation type, group matches by child_cn; score = count; sort by (n_matches desc, child_cn asc); take top 3 per relation. Schema addition: `key_follow_up_papers` (per-relation list of { child_cn, child_title, n_matches, representative_statements, why_selected }), `key_follow_up_interpretation`. Benchmark: 18 claims with ≥1 key follow-up; 10 limits, 3 refines, 1 disputes; distinct key papers: uses 8, supports 9, refines 3, limits 8, disputes 1. Recommendation: yes, include key follow-up in standard card schema. Next bottleneck: relation-label quality and coverage; later, optional claim-to-claim links.

---

## Commands

```bash
# Generate enhanced cards (with key follow-up papers) for one paper
python build_claim_evolution_cards.py --paper 862424

# Generate enhanced cards for the full benchmark (all papers in stress-test output)
python build_claim_evolution_cards.py

# Coverage and key-follow-up report (papers, claims, key follow-up counts, showcase cases)
python build_claim_evolution_cards.py --report
```

**Where to find outputs:**

- `data/claim_evolution_cards/{cn}.json` — structured cards per paper (includes key_follow_up_papers)
- `data/claim_evolution_cards/{cn}.md` — human-readable summary per paper (includes Key follow-up papers section)
- `data/claim_evolution_cards/all_cards.jsonl` — one JSON object per paper (all claims) per line

**Requires:** Existing `data/claim_evolution_stress_test/*.json` (from `stress_test_claim_evolution.py`).
