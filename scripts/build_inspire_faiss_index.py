#!/usr/bin/env python3
"""
STEP 4 (optional): Build a Faiss inner-product index from float16 memmap + manifest.

Separate from embedding; run after embeddings exist.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from tqdm import tqdm

from inspire_embedding_common import repo_root


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("faiss_build")

    ap = argparse.ArgumentParser(description="Build Faiss index from INSPIRE embedding memmap")
    ap.add_argument("--output-root", default="outputs_no_sync/inspire_embeddings")
    ap.add_argument("--manifest", default="", help="embedding_manifest.json path")
    ap.add_argument("--index-type", default="Flat", choices=("Flat",))
    ap.add_argument("--mmap-read-chunk-rows", type=int, default=50_000)
    args = ap.parse_args()

    root = repo_root()
    out_root = (root / args.output_root).expanduser().resolve()
    manifest_path = Path(args.manifest) if args.manifest else out_root / "embedding_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mem_path = Path(manifest["memmap_path"])
    if not mem_path.is_file():
        raise FileNotFoundError(f"memmap missing: {mem_path}")

    dim = int(manifest["embedding_dim"])
    n_usable = int(manifest["usable_papers"])
    dtype_str = manifest.get("storage_dtype", "float16")
    dtype_np = np.float16 if dtype_str == "float16" else np.float32

    try:
        import faiss  # type: ignore
    except ImportError as e:
        raise ImportError(
            "faiss-cpu (or faiss-gpu) is required. Install e.g. `pip install faiss-cpu`."
        ) from e

    mm = np.memmap(mem_path, dtype=dtype_np, mode="r", shape=(n_usable, dim))

    faiss_dir = out_root / "faiss_index"
    faiss_dir.mkdir(parents=True, exist_ok=True)

    normalized = bool(manifest.get("normalized_embeddings", False))
    metric = "IP" if normalized else "L2"
    log.info("Building Faiss index metric=%s (normalized=%s)", metric, normalized)

    if normalized:
        index = faiss.IndexFlatIP(dim)
    else:
        index = faiss.IndexFlatL2(dim)

    chunk_rows = args.mmap_read_chunk_rows
    start = 0
    pbar = tqdm(total=n_usable, desc="Faiss add vectors", unit="vec", dynamic_ncols=True)
    while start < n_usable:
        end = min(start + chunk_rows, n_usable)
        chunk = np.asarray(mm[start:end], dtype=np.float32)
        index.add(chunk)
        pbar.update(end - start)
        start = end
    pbar.close()

    index_path = faiss_dir / "faiss_flat.index"
    faiss.write_index(index, str(index_path))

    meta = {
        "faiss_index_path": str(index_path),
        "metric": metric,
        "embedding_dim": dim,
        "num_vectors": n_usable,
        "source_manifest": str(manifest_path),
        "paper_index_path": manifest.get("paper_index_path"),
    }
    (faiss_dir / "faiss_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    log.info("Wrote %s", index_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
