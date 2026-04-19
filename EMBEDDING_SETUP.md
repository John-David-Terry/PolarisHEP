# Embedding environment setup (PolarisHEP semantic retrieval)

This document gives the **exact** working installation path for the embedding stack so that `query_edge_statements.py --mode semantic` and `--build-embeddings` run successfully.

## Problem

- `sentence-transformers` 5.x requires PyTorch ≥ 2.4; many systems have PyTorch 2.2.
- Newer `transformers` can hit import errors (e.g. `nn` not defined, or `regex` conflicts).
- NumPy 2.x can be incompatible with older PyTorch builds.

## Working stack (tested)

- **Python:** 3.11 (e.g. `/usr/local/bin/python3.11` or `python3.11` from Homebrew).
- **Exact install (recommended):**

```bash
# 1. Use Python 3.11
python3.11 --version   # should be 3.11.x

# 2. Upgrade regex (avoids circular import with older transformers)
python3.11 -m pip install --upgrade 'regex'

# 3. Install sentence-transformers 2.x (works with torch 2.2)
python3.11 -m pip install 'sentence-transformers>=2.2,<3.0'

# 4. Pin NumPy to 1.x for torch compatibility
python3.11 -m pip install 'numpy<2'
```

## Versions that worked

| Package              | Version  |
|----------------------|----------|
| Python               | 3.11.x   |
| numpy                | 1.26.4   |
| torch                | 2.2.2    |
| sentence-transformers| 2.7.0    |
| transformers         | 4.57.6   |
| scikit-learn         | 1.8.0    |
| regex                | 2026.2.28|

## Optional: isolated venv

If you prefer not to touch the system Python:

```bash
python3.11 -m venv .venv_embeddings
.venv_embeddings/bin/pip install --upgrade pip
.venv_embeddings/bin/pip install 'regex' 'numpy<2' 'sentence-transformers>=2.2,<3.0' scikit-learn
# Then run with:
.venv_embeddings/bin/python query_edge_statements.py --db inspire.sqlite --build-embeddings
.venv_embeddings/bin/python query_edge_statements.py --db inspire.sqlite --query "TMD evolution" --mode semantic --top-k 5
```

Note: some venv setups still see system site-packages; if you get version conflicts, use the global `python3.11` install above.

## Verify

```bash
python3.11 -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('all-MiniLM-L6-v2')
e = m.encode(['test'])
print('shape', e.shape)
print('OK')
"
# Expected: shape (1, 384) then OK
```

## Build and query

```bash
# Build (once)
python3.11 query_edge_statements.py --db inspire.sqlite --build-embeddings

# Query
python3.11 query_edge_statements.py --db inspire.sqlite --query "Collins Soper Sterman" --mode semantic --top-k 10
```

See `EMBEDDING_VALIDATION.md` for benchmark results and default-mode recommendation.

## Exact commands to run

```bash
# Setup (once per environment)
python3.11 -m pip install --upgrade 'regex'
python3.11 -m pip install 'sentence-transformers>=2.2,<3.0'
python3.11 -m pip install 'numpy<2'

# Build embeddings (default: statement-and-titles)
python3.11 query_edge_statements.py --db inspire.sqlite --build-embeddings

# Optional: statement-only index (overwrites default)
python3.11 query_edge_statements.py --db inspire.sqlite --build-embeddings --retrieval-text statement-only

# Lexical (default mode)
python3.11 query_edge_statements.py --db inspire.sqlite --query "Sudakov resummation" --mode lexical --top-k 10

# Semantic
python3.11 query_edge_statements.py --db inspire.sqlite --query "Collins Soper Sterman" --mode semantic --top-k 10

# Hybrid
python3.11 query_edge_statements.py --db inspire.sqlite --query "TMD evolution" --mode hybrid --top-k 10

# Edge-level aggregation (any mode)
python3.11 query_edge_statements.py --db inspire.sqlite --query "TMD evolution" --mode semantic --by-edge --top-k 5
```
