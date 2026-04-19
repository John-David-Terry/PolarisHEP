# Semantic retrieval over the statement-backed graph

Embedding-based retrieval is implemented in `query_edge_statements.py` alongside lexical (FTS5 / Python fallback). The embedding model is used **only for retrieval**; evidence remains the actual citation statements from the graph.

## Model and storage

- **Model:** `all-MiniLM-L6-v2` (sentence-transformers). Small (~80MB), fast, 384-dim. Good for phrase-level semantic similarity; no GPU required.
- **Storage:** `data/embeddings/` (or `--embeddings-dir`):
  - `statement_embeddings.npy` — float32 array, shape (n_statements, 384)
  - `metadata.csv` — index, child_cn, parent_cn, statement, child_title, parent_title
  - `config.json` — model_name, retrieval_text_mode, n_rows, dim
- **Similarity:** Cosine (normalized vectors); score = 1 - cosine_distance.
- **Index:** sklearn `NearestNeighbors(metric="cosine")`; fit at query time (instant for this corpus size).

## Retrieval text

Two options (set at **build** time with `--retrieval-text`):

1. **statement-only** — embed only the citation sentence. Best when the statement is self-contained.
2. **statement-and-titles** (default) — embed `"Citing: {child_title}. Cited: {parent_title}. {statement}"`. Helps when the query refers to paper topics; recommended for alias/synonym matching.

## Commands

```bash
# Build index (run once, or after adding statements)
python query_edge_statements.py --db inspire.sqlite --build-embeddings
python query_edge_statements.py --db inspire.sqlite --build-embeddings --retrieval-text statement-only

# Query
python query_edge_statements.py --db inspire.sqlite --query "CSS" --mode semantic --top-k 10
python query_edge_statements.py --db inspire.sqlite --query "Collins Soper Sterman" --mode semantic --top-k 10
python query_edge_statements.py --db inspire.sqlite --query "TMD evolution" --mode hybrid --by-edge --top-k 10
```

## Modes

- **lexical** (default) — FTS5 or Python term-overlap. No embedding deps.
- **semantic** — nearest-neighbor over statement embeddings. Requires `--build-embeddings` and deps.
- **hybrid** — merge lexical and semantic candidates; score = 0.5 * norm(lex) + 0.5 * norm(sem). Requires embeddings.

## Dependencies (semantic/hybrid only)

```bash
pip install -r requirements-embeddings.txt
# or: pip install sentence-transformers scikit-learn numpy
```

If you see import errors (e.g. `regex`), use a fresh virtualenv and install there.

## Benchmark queries (lexical vs semantic vs hybrid)

Run and compare for:

- **Alias / abbreviation:** CSS, Collins Soper Sterman, Collins-Soper-Sterman  
- **TMD:** TMD, TMD evolution, transverse momentum dependent evolution  
- **Resummation:** qT resummation, transverse momentum resummation, Sudakov, Sudakov resummation, QCD resummation  
- **General:** factorization, Higgs, nonperturbative, transverse momentum broadening  
- **Failure / broad:** banana, very broad (e.g. QCD)

Compare:

1. Lexical top results  
2. Semantic top results (after `--build-embeddings`)  
3. Hybrid top results  

Focus: does semantic improve **CSS → Collins–Soper–Sterman**, **TMD → transverse momentum dependent**, **qT resummation → transverse momentum resummation**?

## Performance (expected)

- **Build:** ~30–60 s for ~360 statements (model load + encode). Index size ~0.5 MB npy + metadata.
- **Query (semantic):** model load once then NN search; first query ~1–2 s, subsequent same process ~0.1 s (model in memory).
- **Query (lexical):** FTS5 ~2–6 ms; fallback ~100 ms.

## Edge aggregation

`--by-edge` works for all modes: group by (child_cn, parent_cn), aggregate score = sum(statement scores), show top statement(s). Same as before.
