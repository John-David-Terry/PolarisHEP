# Embedding retrieval validation report

This report summarizes: (1) build and query checks, (2) benchmark comparisons (lexical vs semantic vs hybrid), (3) retrieval-text comparison (statement-only vs statement-and-titles), (4) performance, (5) recommendation for default mode.

---

## A. Build checks

| Check | Result |
|-------|--------|
| Embedding build completed without error | ✅ Yes |
| Row count in metadata matches embedding matrix | ✅ 363 rows in both (metadata.csv has 1 header + 363 data rows) |
| config.json matches actual embedding dimensionality | ✅ dim: 384, shape (363, 384) |
| No missing statements in metadata sidecar | ✅ All rows present |

**Output files (statement-and-titles build):**

- `data/embeddings/statement_embeddings.npy` — ~0.53 MB
- `data/embeddings/metadata.csv` — ~412 KB
- `data/embeddings/config.json` — model_name: all-MiniLM-L6-v2, retrieval_text_mode: statement-and-titles, n_rows: 363, dim: 384

**Build time:** ~10–30 s (model load + 363 encodes). Subsequent builds ~4–10 s with warm cache.

---

## B. Benchmark comparisons (lexical vs semantic vs hybrid)

### Concept-linking (alias / terminology)

| Query | Lexical | Semantic | Hybrid |
|-------|---------|----------|--------|
| **CSS** | Hits: TMD/Sivers/PDF (lexical match on “CSS” in text). | Low scores (~0.11); threshold/Sudakov resummation. Weak for acronym. | Top hits = lexical-style (TMD evolution, Sivers); hybrid dominated by lexical. |
| **Collins Soper Sterman** | **0 hits** (AND of three terms). | **Hits:** TMD factorization, Wilson lines, renormalization-group (parent e.g. “Renormalization-group properties of transverse-momentum dependent…”). Conceptually related to CSS formalism. | N/A (semantic needed to get any hit). |
| **TMD evolution** | Strong: explicit “TMD” + “evolution” in text. | **Strong:** TMD evolution, Sivers, TMDPDFs, evolution kernel. Very relevant. | Combines both; sensible. |
| **qT resummation** | Good when phrase appears. | Good: Sudakov/soft resummation, factorization; related to qT resummation. | Good. |
| **Sudakov resummation** | Good. | Parent “On Sudakov and soft resummations in QCD”; excellent. | Good. |
| **transverse momentum dependent evolution** | Phrase match. | Same as TMD evolution style; relevant. | Good. |
| **factorization** | Good. | Good (e.g. “Factorization at the LHC”); one junk statement (encoding artifact in corpus). | Good. |
| **Higgs** | Good. | “QCD Radiative Corrections to Higgs Physics”, “Higgs Production”; excellent. | Good. |
| **QCD resummation** | Good. | Resummation/QCD; sensible. | Good. |
| **nonperturbative** | Good. | Relevant (nonperturbative, TMD, etc.). | Good. |

### Failure-mode queries

| Query | Lexical | Semantic |
|-------|---------|----------|
| **banana** | 0 hits. | Low scores (~0.11–0.14); returns unrelated physics (PDFs, parton). Not confident; acceptable. |
| **QCD** | Many hits (term very common). | High score (0.62); “QCD Radiative Corrections”, “threshold effects in QCD”. Broad but on-topic. |
| **Short/ambiguous** | Depends on term. | Semantic can be permissive; for “CSS” alone, semantic top was not clearly better than lexical. |

**Summary:** Semantic **fixes** “Collins Soper Sterman” (0 lexical → relevant TMD/factorization hits). For “TMD evolution”, “Sudakov resummation”, “Higgs”, “qT resummation”, semantic is **at least as good** and often very strong. For “CSS” alone, semantic did not clearly outperform lexical (low scores, threshold resummation); **hybrid** kept the best lexical-style hits. Unrelated (“banana”) stays low-confidence; broad (“QCD”) remains broad but sensible.

---

## C. Retrieval text: statement-only vs statement-and-titles

| Query | statement-only (semantic) | statement-and-titles (semantic) |
|-------|---------------------------|----------------------------------|
| **CSS** | Weak/vague (e.g. jet energy-loss, empty statements); score ~0.28. | Slightly better structure; acronym may align with “Cited” title. Still not ideal. |
| **TMD evolution** | **Excellent:** explicit TMD evolution sentences; scores 0.55–0.62. | **Excellent:** same quality; titles add context. |
| **qT resummation** | Good (resummation/factorization). | Good. |
| **Sudakov resummation** | Good. | Good. |

**Conclusion:** **statement-and-titles** is recommended: it helps when the query matches paper topics (e.g. “Collins Soper Sterman” in a cited title). For concept-heavy statements (e.g. “TMD evolution”), both are good; statement-and-titles is safer for mixed query types.

---

## D. Performance

| Metric | Value |
|--------|--------|
| One-time embedding build time | ~10–30 s (first run with model download ~30 s; rebuild ~4–10 s) |
| Embedding storage size | ~0.53 MB (.npy) + ~0.4 MB (metadata.csv) |
| Semantic query latency | ~10–15 s (first query includes model load); subsequent ~10–12 s (model in process) |
| Lexical query latency (FTS5) | ~2–6 ms |
| Lexical query latency (fallback) | ~100 ms |
| Hybrid query latency | ~14–15 s (runs both lexical and semantic) |
| First-query model load | Noticeable (~10–14 s); one-time per process. |

---

## E. Examples where semantic clearly helped

1. **Collins Soper Sterman** — Lexical: 0. Semantic: TMD factorization, Wilson lines, renormalization-group (parent “transverse-momentum dependent”), conceptually related to CSS.
2. **TMD evolution** — Semantic: direct “evolution of TMDs”, “evolution kernel”, “TMDPDFs”, “TMD Evolution for Transverse Single Spin Asymmetry”.
3. **qT resummation** — Semantic: Sudakov/soft resummation, factorization; connects to qT resummation language.
4. **Sudakov resummation** — Semantic: parent “On Sudakov and soft resummations in QCD”; very on-topic.
5. **Higgs** — Semantic: “QCD Radiative Corrections to Higgs Physics”, “Higgs Production”; clear and relevant.

---

## F. Examples where semantic was vague or weak

1. **CSS** (acronym only) — Top semantic hits: threshold/SV cross section, not explicitly Collins–Soper–Sterman; scores ~0.11.
2. **statement-only + “CSS”** — Jet energy-loss, empty statements; weak.
3. **factorization** — One hit with junk statement (corpus/encoding artifact).
4. **banana** — Returns physics (PDFs, parton) with low scores; not nonsense but not relevant (acceptable for OOD query).

---

## G. Edge-level aggregation (--by-edge)

For “TMD evolution” with `--by-edge --mode semantic`, edges with multiple matching statements (e.g. 3 statements) get summed scores and rank higher; top edges are “Model-Independent Evolution of TMD…” → “Calculation of TMD Evolution…”, etc. **Edge-level grouping is useful for the demo:** one row per (citing, cited) paper pair with a representative statement and n_statements.

---

## H. Recommendation: default retrieval mode

**Recommendation: keep default as lexical, add semantic as an option.**

Reasons:

1. **Lexical is fast and deterministic** (FTS5 ~ms; no model load). Best for exact phrase and for environments where the embedding stack is not installed.
2. **Semantic clearly adds value** for concept linking (e.g. “Collins Soper Sterman” → 0 lexical vs relevant semantic hits) and for “TMD evolution”, “Sudakov resummation”, “Higgs”, etc.
3. **Hybrid** is useful when you want both: e.g. “CSS” hybrid kept strong lexical-style TMD hits while still allowing semantic to contribute.
4. **First-query cost** of semantic (~10–15 s) is high for a default; lexical is better as default for quick, scripted, or dependency-light use.
5. **Explicit mode choice** makes it clear what is being used and keeps the door open to making semantic or hybrid the default later (e.g. after caching the model or index).

**Suggested default:** `--mode lexical` (current). Document semantic and hybrid as the way to “connect different language describing the same physics” and to improve alias/terminology coverage. Optional future step: default to `--mode hybrid` if/when query latency is acceptable (e.g. with a long-lived service that keeps the model loaded).

---

## I. Next bottleneck after this

1. **Statement coverage** — Only 363 statements; many subgraph edges have no statement. Scaling TEI extraction and statement-backed edges will matter more than retrieval nuance.
2. **Statement quality** — Empty or junk statements (e.g. encoding artifacts) hurt both lexical and semantic; cleaning or filtering at ingest time would help.
3. **Paper-level summaries** — Currently retrieval is over citation sentences only; paper-level summaries could improve recall and explanation.
4. **GUI / UX** — For demos, a simple UI (query box → statement + paper list) would make the value of semantic/hybrid obvious without CLI.
5. **Model load latency** — Caching the loaded model (or a small search service) would make semantic/hybrid more suitable as a default in interactive use.
