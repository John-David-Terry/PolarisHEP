# INSPIRE corpus embedding pipeline

One canonical text per paper → one embedding row in a single **float16 memory-mapped matrix**, plus **Parquet metadata** for IDs, field flags, and QA. Outputs are written under **`outputs_no_sync/`** (or `--output-root`) so large binaries stay **outside git sync / Dropbox / iCloud / Cursor indexing**.

## Layout

| Artifact | Purpose |
|----------|---------|
| `paper_index.parquet` | Per-paper metadata: `paper_id`, `row_idx`, `has_*`, `text_field_pattern`, hashes, etc. |
| `embeddings.f16.memmap` | Matrix `(N_usable, dim)` float16 |
| `embedding_progress.json` | Resume state, throughput, status |
| `embedding_manifest.json` | Model, dtype, paths, **`run_status`** |
| `completed_chunks.jsonl` | Append-only chunk completions (recovery aid) |
| `logs/embedding_run.log` | Short append-only text log |
| `samples/text_samples.parquet` | Random sample for sanity checks |

## Canonical text (embedding input)

```
[Title]
…

[Keywords]
kw1; kw2

[Abstract]
…
```

Empty sections omitted. Full text is rebuilt during embedding (not stored for every paper at DB scale).

## Commands

```bash
# Inspect schema + counts
python3.11 scripts/inspect_inspire_embedding_inputs.py --db inspire.sqlite

# Build metadata table only (streams DB)
python3.11 scripts/build_inspire_embedding_texts.py --db inspire.sqlite \
  --output-root outputs_no_sync/inspire_embeddings

# Embed (resumable)
python3.11 scripts/embed_inspire_corpus.py --db inspire.sqlite \
  --model-name BAAI/bge-m3 --device cuda \
  --output-root outputs_no_sync/inspire_embeddings

# Full driver
python3.11 scripts/run_inspire_embedding_pipeline.py --db inspire.sqlite \
  --output-root outputs_no_sync/inspire_embeddings \
  --model-name BAAI/bge-m3 --embed-batch-size 64 --num-workers 3
```

### Pilot (subset)

Build and embed only the first *N* papers / rows:

```bash
python3.11 scripts/run_inspire_embedding_pipeline.py \
  --db inspire.sqlite \
  --output-root outputs_no_sync/inspire_embeddings_pilot \
  --max-papers 50000 \
  --max-rows 50000 \
  --force-rebuild \
  --device cpu
```

`--max-papers` caps **index build**; `--max-rows` caps **embedding** this invocation (resume continues).

On **CPU**, a 50k-paper pilot can take many hours with `bge-m3`; use **`--device cuda`** (or a smaller smoke `--max-rows`) when iterating. Progress is one **`tqdm`** bar per stage (`refresh=False` on updates avoids duplicate terminal lines).

### Resume

- Default **`--resume`**: continues from `embedding_progress.json` (`last_completed_row_idx`).
- **`--force-rebuild`**: deletes memmap, progress, manifest, `completed_chunks.jsonl`.
- **`embedding_written`** / **`model_name`** columns in Parquet are finalized only when status is **`completed`**.

### Faiss (optional, second phase)

```bash
pip install faiss-cpu
python3.11 scripts/build_inspire_faiss_index.py \
  --output-root outputs_no_sync/inspire_embeddings
```

## Why `outputs_no_sync/`?

Large **memmap** + **Parquet** must not live in synced folders or the whole repo tree: cloud sync and IDE indexing can thrash on huge files. Keep the tree under `outputs_no_sync/` **or** pass an absolute path on a local disk via `--output-root`.

## Requirements

Install embedding stack (see `requirements-embeddings.txt`): `sentence-transformers`, `pandas`, `pyarrow`, `numpy`, `tqdm`.
