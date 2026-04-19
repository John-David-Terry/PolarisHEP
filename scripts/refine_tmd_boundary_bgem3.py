#!/usr/bin/env python3
"""
STEP 4: BGE-M3 refinement on core ∪ boundary only (post coarse text clustering).

Does not classify the corpus; promotes or demotes boundary papers using dense similarity.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

import numpy as np
from tqdm import tqdm

from tmd_discovery_common import combined_text, repo_root

# --- Tunable decision thresholds (cosine similarity, normalized embeddings) -----
PROMOTE_IF_MAX_SIM_TO_CORE_GE = 0.72
PROMOTE_IF_SIM_TO_SEED_CENTROID_GE = 0.64
REJECT_IF_MAX_SIM_TO_CORE_LE = 0.42
TOP_K_MEAN_CORE = 5
BATCH_ENCODE = 32
DEFAULT_MODEL = "BAAI/bge-m3"
# -------------------------------------------------------------------------------

try:
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.models import Normalize, Pooling, Transformer as STTransformer
except ImportError as e:  # pragma: no cover
    SentenceTransformer = None  # type: ignore
    STTransformer = None  # type: ignore
    Pooling = None  # type: ignore
    Normalize = None  # type: ignore
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


def load_bge_model(model_name: str, device: str | None):
    """
    Load BGE-M3 with weights from safetensors when available.

    Older PyTorch (<2.6) cannot satisfy transformers' torch.load guard for `.bin`
    checkpoints; forcing safetensors avoids that path entirely.
    """
    assert STTransformer is not None and Pooling is not None and Normalize is not None
    transformer = STTransformer(
        model_name,
        model_args={"use_safetensors": True, "trust_remote_code": True},
    )
    pooling = Pooling(transformer.get_word_embedding_dimension(), pooling_mode="cls")
    normalize = Normalize()
    return SentenceTransformer(modules=[transformer, pooling, normalize], device=device)


def normalize_rows(rows: list[dict]) -> np.ndarray:
    """L2-normalize embedding matrix (rows)."""
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return rows / norms


def cosine_sim_matrix(a_norm: np.ndarray, b_norm: np.ndarray) -> np.ndarray:
    """Cosine similarity = dot product when rows are L2-normalized."""
    return np.dot(a_norm, b_norm.T)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("bgem3")

    ap = argparse.ArgumentParser(description="BGE-M3 refinement for TMD boundary")
    ap.add_argument("--core-csv", default="data/tmd_field_discovery/core_tmd_papers.csv")
    ap.add_argument("--boundary-csv", default="data/tmd_field_discovery/boundary_papers.csv")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="HF model id for BGE-M3")
    ap.add_argument("--out-dir", default="data/tmd_field_discovery")
    ap.add_argument("--device", default=None, help="e.g. cuda, cpu, mps")
    args = ap.parse_args()

    if _IMPORT_ERROR is not None:
        raise ImportError(
            "sentence_transformers is required for BGE-M3 refinement. "
            "Install with: pip install -r requirements-embeddings.txt"
        ) from _IMPORT_ERROR

    root = repo_root()
    out_dir = (root / args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    core_path = (root / args.core_csv).expanduser().resolve()
    bnd_path = (root / args.boundary_csv).expanduser().resolve()

    if not core_path.is_file():
        raise FileNotFoundError(core_path)
    if not bnd_path.is_file():
        raise FileNotFoundError(bnd_path)

    def load_csv(p: Path) -> list[dict]:
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    core_rows = load_csv(core_path)
    boundary_rows = load_csv(bnd_path)

    def embed_text(row: dict) -> str:
        if row.get("text") and str(row["text"]).strip():
            return str(row["text"]).strip()
        return combined_text(row.get("title") or "", row.get("abstract") or "")

    core_texts = [embed_text(r) or "[empty]" for r in core_rows]
    bnd_texts = [embed_text(r) or "[empty]" for r in boundary_rows]

    log.info("Loading model %s (safetensors)", args.model)
    model = load_bge_model(args.model, args.device)

    def encode_batch(texts: list[str]) -> np.ndarray:
        embs = model.encode(
            texts,
            batch_size=BATCH_ENCODE,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        return np.asarray(embs, dtype=np.float64)

    log.info("Encoding %d core papers", len(core_texts))
    core_emb = encode_batch(core_texts)
    core_emb = normalize_rows(core_emb)

    seed_mask = np.array([int(r.get("is_seed", 0) or 0) for r in core_rows], dtype=bool)
    if seed_mask.any():
        seed_centroid = normalize_rows(core_emb[seed_mask].mean(axis=0, keepdims=True))
    else:
        seed_centroid = normalize_rows(core_emb.mean(axis=0, keepdims=True))

    core_centroid = normalize_rows(core_emb.mean(axis=0, keepdims=True))

    log.info("Encoding %d boundary papers", len(bnd_texts))
    if bnd_texts:
        b_emb = encode_batch(bnd_texts)
        b_emb = normalize_rows(b_emb)
        sim_core_c = (b_emb * core_centroid).sum(axis=1)
        sim_seed_c = (b_emb * seed_centroid).sum(axis=1)
        max_to_core = np.zeros(len(bnd_texts), dtype=np.float64)
        mean_topk = np.zeros(len(bnd_texts), dtype=np.float64)
        if len(core_emb) > 0:
            sims = cosine_sim_matrix(b_emb, core_emb)
            max_to_core = sims.max(axis=1)
            k = min(TOP_K_MEAN_CORE, sims.shape[1])
            if k > 0:
                part = np.partition(sims, -k, axis=1)[:, -k:]
                mean_topk = part.mean(axis=1)
    else:
        sim_core_c = np.array([])
        sim_seed_c = np.array([])
        max_to_core = np.array([])
        mean_topk = np.array([])

    scores_rows = []
    promoted: list[dict] = []
    remaining: list[dict] = []
    rejected: list[dict] = []

    for i, row in enumerate(boundary_rows):
        rec = {
            "control_number": row.get("control_number", ""),
            "similarity_to_core_centroid": float(sim_core_c[i]),
            "similarity_to_seed_centroid": float(sim_seed_c[i]),
            "max_similarity_to_any_core": float(max_to_core[i]),
            "mean_top_k_similarity_to_core": float(mean_topk[i]),
            "decision": "",
        }
        mx = float(max_to_core[i])
        ss = float(sim_seed_c[i])
        if mx <= REJECT_IF_MAX_SIM_TO_CORE_LE:
            rec["decision"] = "reject"
            rejected.append(row)
        elif mx >= PROMOTE_IF_MAX_SIM_TO_CORE_GE and ss >= PROMOTE_IF_SIM_TO_SEED_CENTROID_GE:
            rec["decision"] = "promote"
            promoted.append(row)
        else:
            rec["decision"] = "boundary"
            remaining.append(row)
        scores_rows.append(rec)

    refined_core = list(core_rows) + promoted

    fields = [
        "control_number",
        "similarity_to_core_centroid",
        "similarity_to_seed_centroid",
        "max_similarity_to_any_core",
        "mean_top_k_similarity_to_core",
        "decision",
    ]
    scores_path = out_dir / "bgem3_boundary_scores.csv"
    with open(scores_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in scores_rows:
            w.writerow(r)

    def write_named_csv(name: str, rows: list[dict]) -> None:
        p = out_dir / name
        if not rows:
            p.write_text("", encoding="utf-8")
            return
        flds = list(rows[0].keys())
        with open(p, "w", newline="", encoding="utf-8") as f:
            ww = csv.DictWriter(f, fieldnames=flds)
            ww.writeheader()
            for rr in rows:
                ww.writerow(rr)

    write_named_csv("bgem3_refined_core.csv", refined_core)
    write_named_csv("bgem3_remaining_boundary.csv", remaining)
    write_named_csv("bgem3_rejected_boundary.csv", rejected)

    summary = {
        "model": args.model,
        "thresholds": {
            "PROMOTE_IF_MAX_SIM_TO_CORE_GE": PROMOTE_IF_MAX_SIM_TO_CORE_GE,
            "PROMOTE_IF_SIM_TO_SEED_CENTROID_GE": PROMOTE_IF_SIM_TO_SEED_CENTROID_GE,
            "REJECT_IF_MAX_SIM_TO_CORE_LE": REJECT_IF_MAX_SIM_TO_CORE_LE,
            "TOP_K_MEAN_CORE": TOP_K_MEAN_CORE,
        },
        "counts": {
            "core_input": len(core_rows),
            "boundary_input": len(boundary_rows),
            "promoted_from_boundary_to_core": len(promoted),
            "remaining_boundary": len(remaining),
            "rejected_from_boundary": len(rejected),
            "refined_core_total": len(refined_core),
        },
        "note": "Promotions require BOTH max similarity to any core paper and similarity to the seed centroid.",
    }
    (out_dir / "bgem3_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    log.info("Summary: %s", json.dumps(summary["counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
