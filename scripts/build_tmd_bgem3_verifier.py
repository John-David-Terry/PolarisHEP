#!/usr/bin/env python3
"""
BGE-M3 verification layer: score = sim(seed centroid) - sim(hard-nonTMD centroid).

Uses existing TMD discovery artifacts only; does not rerun the clustering pipeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tmd_discovery_common import combined_text, read_seed_ids, repo_root

DEFAULT_MODEL = "BAAI/bge-m3"
BATCH_ENCODE = 32
RNG = np.random.RandomState

# Strong TMD markers — exclude clusters whose top_terms contain these (hard-negative selection)
DEFAULT_TMD_MARKERS = (
    "tmd",
    "tmds",
    "sivers",
    "collins",
    "transversity",
    "drell",
    "drell yan",
    "semi-inclusive",
    "semi inclusive",
)

# Junk / markup-heavy clusters (optional exclusion)
DEFAULT_JUNK_CLUSTER_HINTS = ("math display", "mrow", "msub", "<mi")


def load_bgem3_model(model_name: str, device: str | None):
    """Load BGE-M3 with safetensors when possible (compat with older torch)."""
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.models import Normalize, Pooling
    from sentence_transformers.models import Transformer as STTransformer

    transformer = STTransformer(
        model_name,
        model_args={"use_safetensors": True, "trust_remote_code": True},
    )
    pooling = Pooling(transformer.get_word_embedding_dimension(), pooling_mode="cls")
    normalize = Normalize()
    return SentenceTransformer(modules=[transformer, pooling, normalize], device=device)


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def centroid_normalized(embs: np.ndarray) -> np.ndarray:
    """Mean of row vectors, then L2-normalize to unit length."""
    if embs.size == 0:
        raise ValueError("empty embedding set for centroid")
    v = embs.mean(axis=0, keepdims=True)
    return l2_normalize(v)


def cosine_sim_rows_to_centroid(embs_norm: np.ndarray, centroid_norm: np.ndarray) -> np.ndarray:
    """Rows and centroid are L2-normalized; cosine = dot."""
    return (embs_norm * centroid_norm).sum(axis=1)


def paper_text(neighborhood_row: dict | None, fallback_title: str, fallback_abstract: str) -> str:
    if neighborhood_row and str(neighborhood_row.get("text", "")).strip():
        return str(neighborhood_row["text"]).strip()
    return combined_text(fallback_title, fallback_abstract)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def choose_hard_negative_clusters(
    summary: list[dict],
    core_cluster_ids: set[int],
    max_seed_fraction: float,
    min_cluster_size: int,
    tmd_markers: tuple[str, ...],
    exclude_cluster_ids: set[int],
    exclude_junk_terms: bool,
) -> list[int]:
    """Pick clusters for hard negatives: not core, large enough, low seed frac, no TMD markers in top_terms."""
    out: list[int] = []
    for c in summary:
        cid = int(c["cluster_id"])
        if cid in core_cluster_ids or cid in exclude_cluster_ids:
            continue
        sz = int(c["size"])
        if sz < min_cluster_size:
            continue
        frac = float(c["seed_fraction"])
        if frac > max_seed_fraction:
            continue
        terms_blob = " ".join(c.get("top_terms") or []).lower()
        if any(m in terms_blob for m in tmd_markers):
            continue
        if exclude_junk_terms and any(h in terms_blob for h in DEFAULT_JUNK_CLUSTER_HINTS):
            continue
        out.append(cid)
    return sorted(out)


def deterministic_split(ids: list[int], validation_frac: float, seed: int) -> tuple[list[int], list[int]]:
    """Shuffle then split reference / validation."""
    rng = RNG(seed)
    xs = list(ids)
    rng.shuffle(xs)
    n_val = max(1, int(round(len(xs) * validation_frac))) if xs else 0
    if len(xs) <= 2 and validation_frac > 0:
        n_val = min(1, len(xs) // 2 or 1)
    val = xs[:n_val]
    ref = xs[n_val:]
    if not ref:
        ref = val[:-1]
        val = val[-1:]
    if not val and len(xs) >= 2:
        val = [xs[-1]]
        ref = xs[:-1]
    return ref, val


def encode_texts(model, texts: list[str], batch_size: int) -> np.ndarray:
    """Encode texts; normalize row-wise."""
    arr = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    mat = np.asarray(arr, dtype=np.float64)
    return l2_normalize(mat)


def auto_thresholds(pos_val: np.ndarray, hneg_val: np.ndarray) -> tuple[float, float]:
    """Choose upper (core bar) and lower (reject bar) from validation distributions."""
    pos_val = np.asarray(pos_val, dtype=float)
    hneg_val = np.asarray(hneg_val, dtype=float)
    if pos_val.size == 0 or hneg_val.size == 0:
        raise ValueError("need non-empty validation scores for auto thresholds")

    upper = float(np.percentile(pos_val, 20))
    lower = float(np.percentile(hneg_val, 80))

    if upper <= lower:
        gap = max(0.02, (float(np.max(hneg_val)) - float(np.min(pos_val))) * 0.1)
        mid = (float(np.median(pos_val)) + float(np.median(hneg_val))) / 2
        upper = mid + gap / 2
        lower = mid - gap / 2
        if upper <= lower:
            upper = lower + 0.05

    return upper, lower


def threshold_grid_sweep(pos_val: np.ndarray, hneg_val: np.ndarray, n_grid: int = 21) -> list[dict]:
    """Sweep (lower, upper) pairs derived from pooled score range; report recalls."""
    combined = np.concatenate([pos_val, hneg_val])
    lo, hi = float(np.min(combined)), float(np.max(combined))
    if hi <= lo:
        hi = lo + 1e-6
    lowers = np.linspace(lo, hi, n_grid)
    rows = []
    for lower in lowers:
        for upper in lowers:
            if upper <= lower:
                continue
            pos_recall = float(np.mean(pos_val >= upper)) if len(pos_val) else 0.0
            hneg_reject = float(np.mean(hneg_val <= lower)) if len(hneg_val) else 0.0
            rows.append(
                {
                    "lower_threshold": round(float(lower), 6),
                    "upper_threshold": round(float(upper), 6),
                    "positive_recall_frac_ge_upper": pos_recall,
                    "hardneg_rejection_frac_le_lower": hneg_reject,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("bgem3_verifier")

    ap = argparse.ArgumentParser(description="BGE-M3 verifier: seed vs hard-nonTMD centroid score")
    ap.add_argument("--out-dir", default="data/tmd_field_discovery", help="Output directory")
    ap.add_argument("--seed-file", default="data/tmd_field_discovery/seed_set_strong.csv")
    ap.add_argument("--seed-column", default="recid")
    ap.add_argument("--boundary-file", default="data/tmd_field_discovery/boundary_papers.csv")
    ap.add_argument("--external-file", default="data/tmd_field_discovery/external_papers.csv")
    ap.add_argument("--core-file", default="data/tmd_field_discovery/core_tmd_papers.csv")
    ap.add_argument("--cluster-assignments", default="data/tmd_field_discovery/cluster_assignments.csv")
    ap.add_argument("--cluster-summary", default="data/tmd_field_discovery/cluster_summary.json")
    ap.add_argument("--cluster-candidates", default="data/tmd_field_discovery/tmd_cluster_candidates.json")
    ap.add_argument("--neighborhood", default="data/tmd_field_discovery/neighborhood.csv")
    ap.add_argument("--model-name", default=DEFAULT_MODEL)
    ap.add_argument("--device", default=None)
    ap.add_argument("--batch-size", type=int, default=BATCH_ENCODE)
    ap.add_argument("--validation-frac", type=float, default=0.2)
    ap.add_argument("--random-state", type=int, default=42)
    ap.add_argument("--upper-threshold", default="auto", help="'auto' or numeric")
    ap.add_argument("--lower-threshold", default="auto", help="'auto' or numeric")
    ap.add_argument("--max-hardneg-seed-fraction", type=float, default=0.02)
    ap.add_argument("--min-hardneg-cluster-size", type=int, default=40)
    ap.add_argument("--easy-negative-sample-size", type=int, default=150)
    ap.add_argument("--exclude-clusters", default="", help="comma-separated cluster IDs to exclude")
    ap.add_argument("--exclude-junk-clusters-by-terms", action="store_true", default=True)
    ap.add_argument("--no-exclude-junk-clusters-by-terms", action="store_false", dest="exclude_junk_clusters_by_terms")
    ap.add_argument("--include-suspicious-core", action="store_true")
    ap.add_argument("--suspicious-core-clusters", default="32", help="comma-separated cluster ids")
    ap.add_argument("--extra-tmd-markers", default="", help="comma-separated extra substrings to treat as TMD markers")
    args = ap.parse_args(argv)

    root = repo_root()
    out_dir = (root / args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "seed": (root / args.seed_file).resolve(),
        "boundary": (root / args.boundary_file).resolve(),
        "external": (root / args.external_file).resolve(),
        "core": (root / args.core_file).resolve(),
        "assignments": (root / args.cluster_assignments).resolve(),
        "summary": (root / args.cluster_summary).resolve(),
        "candidates": (root / args.cluster_candidates).resolve(),
        "neighborhood": (root / args.neighborhood).resolve(),
    }
    for name, p in paths.items():
        if not p.is_file():
            raise FileNotFoundError(f"required input missing ({name}): {p}")

    summary = load_json(paths["summary"])
    candidates = load_json(paths["candidates"])
    core_cluster_ids = set(int(x) for x in candidates.get("core_cluster_ids", []))

    extra_markers = tuple(x.strip().lower() for x in args.extra_tmd_markers.split(",") if x.strip())
    tmd_markers = tuple(dict.fromkeys(DEFAULT_TMD_MARKERS + extra_markers))

    exclude_ids = set(int(x.strip()) for x in args.exclude_clusters.split(",") if x.strip())
    if args.include_suspicious_core:
        exclude_ids |= set(int(x.strip()) for x in args.suspicious_core_clusters.split(",") if x.strip())

    hard_neg_clusters = choose_hard_negative_clusters(
        summary,
        core_cluster_ids,
        args.max_hardneg_seed_fraction,
        args.min_hardneg_cluster_size,
        tmd_markers,
        exclude_ids,
        args.exclude_junk_clusters_by_terms,
    )
    if not hard_neg_clusters:
        raise RuntimeError(
            "No hard-negative clusters selected. Relax --max-hardneg-seed-fraction, "
            "--min-hardneg-cluster-size, or TMD marker rules."
        )

    log.info("Hard-negative clusters: %s", hard_neg_clusters)

    # Tables
    nb = pd.read_csv(paths["neighborhood"], dtype={"control_number": int})
    nb_map = nb.set_index("control_number").to_dict("index")

    def has_usable_text(cn: int) -> bool:
        row = nb_map.get(cn)
        if row is None:
            return False
        t = paper_text(row, str(row.get("title", "")), str(row.get("abstract", "")))
        return bool(t and t.strip())

    assign = pd.read_csv(paths["assignments"], dtype={"control_number": int, "cluster_id": int})

    seed_ids = read_seed_ids(paths["seed"], args.seed_column)
    seed_set = set(seed_ids)

    hard_neg_mask = assign["cluster_id"].isin(hard_neg_clusters)
    hard_neg_cns = assign.loc[hard_neg_mask, "control_number"].unique().tolist()
    hard_neg_cns = [int(cn) for cn in hard_neg_cns if cn not in seed_set and has_usable_text(cn)]

    seed_with_text = [int(s) for s in seed_ids if has_usable_text(int(s))]
    if len(seed_with_text) < 4:
        raise RuntimeError(f"Too few seeds with usable text: {len(seed_with_text)}")

    ref_seeds, val_seeds = deterministic_split(seed_with_text, args.validation_frac, args.random_state)
    ref_hn, val_hn = deterministic_split(hard_neg_cns, args.validation_frac, args.random_state + 1)

    log.info(
        "Split seeds: ref=%d val=%d | hard neg papers: ref=%d val=%d",
        len(ref_seeds),
        len(val_seeds),
        len(ref_hn),
        len(val_hn),
    )

    # Easy negatives: sample from external papers with text
    easy_neg_cns: list[int] = []
    if args.easy_negative_sample_size > 0 and paths["external"].is_file():
        ext_df = pd.read_csv(paths["external"], dtype={"control_number": int})
        pool = [int(r) for r in ext_df["control_number"].tolist() if has_usable_text(int(r))]
        rng = RNG(args.random_state + 2)
        rng.shuffle(pool)
        easy_neg_cns = pool[: args.easy_negative_sample_size]

    ref_ez: list[int] = []
    val_ez: list[int] = []
    if easy_neg_cns:
        ref_ez, val_ez = deterministic_split(easy_neg_cns, args.validation_frac, args.random_state + 3)

    def texts_for_ids(ids: list[int]) -> tuple[list[int], list[str]]:
        texts_out: list[str] = []
        ids_out: list[int] = []
        for cn in ids:
            row = nb_map.get(cn)
            title = str(row.get("title", "")) if row is not None else ""
            abstract = str(row.get("abstract", "")) if row is not None else ""
            tx = paper_text(row, title, abstract) if row is not None else ""
            if not tx.strip():
                continue
            ids_out.append(cn)
            texts_out.append(tx)
        return ids_out, texts_out

    log.info("Loading BGE-M3: %s", args.model_name)
    model = load_bgem3_model(args.model_name, args.device)

    # Encode reference sets for centroids
    ref_seed_ids, ref_seed_texts = texts_for_ids(ref_seeds)
    ref_hn_ids, ref_hn_texts = texts_for_ids(ref_hn)
    if len(ref_seed_ids) < 2 or len(ref_hn_ids) < 2:
        raise RuntimeError(
            f"Insufficient reference embeddings: seeds={len(ref_seed_ids)} hard_neg={len(ref_hn_ids)}"
        )

    emb_ref_pos = encode_texts(model, ref_seed_texts, args.batch_size)
    emb_ref_hneg = encode_texts(model, ref_hn_texts, args.batch_size)

    c_pos = centroid_normalized(emb_ref_pos)
    c_hneg = centroid_normalized(emb_ref_hneg)

    # Optional easy-neg centroid (secondary)
    c_easy = None
    if ref_ez:
        _, ez_tx = texts_for_ids(ref_ez)
        if ez_tx:
            emb_ez = encode_texts(model, ez_tx, args.batch_size)
            c_easy = centroid_normalized(emb_ez)

    # Validation embeddings (held-out only — never mixed into centroids)
    val_seed_ids, val_seed_texts = texts_for_ids(val_seeds)
    val_hn_ids, val_hn_texts = texts_for_ids(val_hn)
    val_ez_ids, val_ez_texts = texts_for_ids(val_ez)

    def score_batch(ids: list[int], texts: list[str]) -> pd.DataFrame:
        if not texts:
            return pd.DataFrame(
                columns=["paper_id", "sim_to_seed", "sim_to_nonTMD", "score", "sim_to_easyneg_optional"]
            )
        embs = encode_texts(model, texts, args.batch_size)
        s_pos = cosine_sim_rows_to_centroid(embs, c_pos)
        s_hn = cosine_sim_rows_to_centroid(embs, c_hneg)
        score = s_pos - s_hn
        s_easy = None
        if c_easy is not None:
            s_easy = cosine_sim_rows_to_centroid(embs, c_easy)
        rows = []
        for i, cn in enumerate(ids):
            rec = {
                "paper_id": cn,
                "sim_to_seed": float(s_pos[i]),
                "sim_to_nonTMD": float(s_hn[i]),
                "score": float(score[i]),
            }
            if s_easy is not None:
                rec["sim_to_easyneg_optional"] = float(s_easy[i])
            rows.append(rec)
        return pd.DataFrame(rows)

    val_parts = []
    if val_seed_texts:
        df_vs = score_batch(val_seed_ids, val_seed_texts)
        df_vs["label_true"] = "positive"
        val_parts.append(df_vs)
    if val_hn_texts:
        df_vh = score_batch(val_hn_ids, val_hn_texts)
        df_vh["label_true"] = "hard_negative"
        val_parts.append(df_vh)
    if val_ez_texts:
        df_ve = score_batch(val_ez_ids, val_ez_texts)
        df_ve["label_true"] = "easy_negative"
        val_parts.append(df_ve)

    val_df = pd.concat(val_parts, ignore_index=True) if val_parts else pd.DataFrame()

    pos_val_scores = val_df.loc[val_df["label_true"] == "positive", "score"].values
    hneg_val_scores = val_df.loc[val_df["label_true"] == "hard_negative", "score"].values

    upper = args.upper_threshold
    lower = args.lower_threshold
    auto_note = ""
    if str(upper).lower() == "auto" or str(lower).lower() == "auto":
        u_auto, l_auto = auto_thresholds(pos_val_scores, hneg_val_scores)
        upper = u_auto if str(args.upper_threshold).lower() == "auto" else float(upper)
        lower = l_auto if str(args.lower_threshold).lower() == "auto" else float(lower)
        auto_note = f"auto-calibrated upper={u_auto:.6f} lower={l_auto:.6f}"
    else:
        upper = float(upper)
        lower = float(lower)

    if upper <= lower:
        log.warning("upper (%s) <= lower (%s); widening band", upper, lower)
        mid = (upper + lower) / 2
        upper = mid + 0.03
        lower = mid - 0.03

    log.info("Thresholds: UPPER=%s LOWER=%s %s", upper, lower, auto_note)

    # Validation stats
    def stat_block(name: str, arr: np.ndarray) -> dict:
        arr = np.asarray(arr, dtype=float)
        if arr.size == 0:
            return {"count": 0}
        return {
            "count": int(arr.size),
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
        }

    val_summary = {
        "positive_validation_scores": stat_block("pos", pos_val_scores),
        "hard_negative_validation_scores": stat_block("hneg", hneg_val_scores),
        "easy_negative_validation_scores": stat_block(
            "ez",
            val_df.loc[val_df["label_true"] == "easy_negative", "score"].values,
        ),
        "upper_threshold": upper,
        "lower_threshold": lower,
        "threshold_auto_note": auto_note,
        "overlap_note": _overlap_note(pos_val_scores, hneg_val_scores),
    }

    sweep = threshold_grid_sweep(pos_val_scores, hneg_val_scores)
    val_summary["threshold_sweep_sample"] = sweep[: min(50, len(sweep))]

    # Counts relative to chosen thresholds on validation
    val_df_scored = val_df.copy()
    if not val_df_scored.empty:
        val_df_scored["assigned_label"] = val_df_scored["score"].apply(
            lambda s: "core" if s >= upper else ("reject" if s <= lower else "boundary")
        )

    # Score boundary papers (primary) — batched encode
    bdf = pd.read_csv(paths["boundary"], dtype={"control_number": int})
    b_cns = [int(x) for x in bdf["control_number"].tolist()]
    b_ids, b_texts = texts_for_ids(b_cns)
    cluster_map = assign.set_index("control_number")["cluster_id"].to_dict()
    boundary_rows: list[dict] = []
    if b_ids:
        embs_b = encode_texts(model, b_texts, args.batch_size)
        s_pos_b = cosine_sim_rows_to_centroid(embs_b, c_pos)
        s_hn_b = cosine_sim_rows_to_centroid(embs_b, c_hneg)
        score_b = s_pos_b - s_hn_b
        s_easy_b = cosine_sim_rows_to_centroid(embs_b, c_easy) if c_easy is not None else None
        for i, cn in enumerate(b_ids):
            lid = "core" if score_b[i] >= upper else ("reject" if score_b[i] <= lower else "boundary")
            row = {
                "paper_id": cn,
                "title": str(nb_map.get(cn, {}).get("title", ""))[:500],
                "cluster_id": cluster_map.get(cn, ""),
                "sim_to_seed": float(s_pos_b[i]),
                "sim_to_nonTMD": float(s_hn_b[i]),
                "score": float(score_b[i]),
                "assigned_label": lid,
            }
            if s_easy_b is not None:
                row["sim_to_easyneg_optional"] = float(s_easy_b[i])
            boundary_rows.append(row)

    b_verify = pd.DataFrame(boundary_rows)

    promoted = b_verify[b_verify["assigned_label"] == "core"]
    kept_b = b_verify[b_verify["assigned_label"] == "boundary"]
    rejected = b_verify[b_verify["assigned_label"] == "reject"]

    reject_branch_used = int(len(rejected)) > 0
    weak_separation = bool(val_summary.get("overlap_note", "").startswith("WEAK"))

    ref_sets = {
        "positive_reference_count": len(ref_seed_ids),
        "positive_validation_count": len(val_seed_ids),
        "hard_negative_reference_count": len(ref_hn_ids),
        "hard_negative_validation_count": len(val_hn_ids),
        "easy_negative_reference_count": len(ref_ez),
        "easy_negative_validation_count": len(val_ez_ids),
        "hard_negative_cluster_ids": hard_neg_clusters,
        "validation_frac": args.validation_frac,
        "random_state": args.random_state,
        "max_hardneg_seed_fraction": args.max_hardneg_seed_fraction,
        "min_hardneg_cluster_size": args.min_hardneg_cluster_size,
        "tmd_markers_excluded_from_hardneg_clusters": list(tmd_markers),
        "core_cluster_ids_excluded_from_hardneg": sorted(core_cluster_ids),
    }

    (out_dir / "bgem3_verifier_reference_sets.json").write_text(
        json.dumps(ref_sets, indent=2) + "\n", encoding="utf-8"
    )

    val_out = val_df.copy()
    if not val_out.empty:
        val_out = val_out.rename(columns={"paper_id": "paper_id"})
        cols = ["paper_id", "label_true", "sim_to_seed", "sim_to_nonTMD", "score"]
        if "sim_to_easyneg_optional" in val_out.columns:
            cols.append("sim_to_easyneg_optional")
        val_out.to_csv(out_dir / "bgem3_validation_scores.csv", index=False, columns=[c for c in cols if c in val_out.columns])

    val_summary["chosen_upper_threshold"] = upper
    val_summary["chosen_lower_threshold"] = lower
    val_summary["reject_branch_used_on_boundary"] = reject_branch_used
    val_summary["boundary_counts"] = {
        "promoted_to_core": int(len(promoted)),
        "kept_boundary": int(len(kept_b)),
        "rejected": int(len(rejected)),
        "total_boundary_scored": int(len(b_verify)),
    }
    val_summary["threshold_sweep_grid_size"] = len(sweep)

    if not val_out.empty:
        val_summary["validation_assigned_counts"] = val_out.groupby("label_true")["score"].count().to_dict()
    if not val_df_scored.empty:
        val_summary["validation_label_distribution"] = val_df_scored.groupby("assigned_label").size().to_dict()

    if not reject_branch_used:
        val_summary["reject_branch_unused_explanation"] = (
            "No boundary paper had score ≤ LOWER_THRESHOLD. "
            "Distributions may overlap; try lowering --lower-threshold manually or tightening hard-negative clusters."
        )

    (out_dir / "bgem3_validation_summary.json").write_text(
        json.dumps(val_summary, indent=2, default=str) + "\n", encoding="utf-8"
    )

    if not b_verify.empty:
        b_verify.to_csv(out_dir / "bgem3_boundary_verification_scores.csv", index=False)

    promoted.to_csv(out_dir / "bgem3_promoted_to_core.csv", index=False)
    kept_b.to_csv(out_dir / "bgem3_kept_boundary.csv", index=False)
    rejected.to_csv(out_dir / "bgem3_rejected.csv", index=False)

    # Suspicious core (optional)
    if args.include_suspicious_core:
        sus_ids = {int(x.strip()) for x in args.suspicious_core_clusters.split(",") if x.strip()}
        cdf = pd.read_csv(paths["core"], dtype={"control_number": int})
        c_assign = assign.set_index("control_number")
        sus_cns = [
            int(r)
            for r in cdf["control_number"].tolist()
            if int(r) in c_assign.index and int(c_assign.loc[int(r), "cluster_id"]) in sus_ids
        ]
        s_ids, s_texts = texts_for_ids(sus_cns)
        sus_rows = []
        if s_ids:
            em_s = encode_texts(model, s_texts, args.batch_size)
            sp = cosine_sim_rows_to_centroid(em_s, c_pos)
            sh = cosine_sim_rows_to_centroid(em_s, c_hneg)
            scr = sp - sh
            for i, cn in enumerate(s_ids):
                lid = "core" if scr[i] >= upper else ("reject" if scr[i] <= lower else "boundary")
                sus_rows.append(
                    {
                        "paper_id": cn,
                        "title": str(nb_map.get(cn, {}).get("title", ""))[:500],
                        "cluster_id": int(c_assign.loc[cn, "cluster_id"]),
                        "sim_to_seed": float(sp[i]),
                        "sim_to_nonTMD": float(sh[i]),
                        "score": float(scr[i]),
                        "assigned_label": lid,
                    }
                )
        pd.DataFrame(sus_rows).to_csv(out_dir / "bgem3_suspicious_core_verification_scores.csv", index=False)

    # Report markdown
    report = _build_report_md(
        ref_sets,
        val_summary,
        promoted,
        rejected,
        weak_separation,
        reject_branch_used,
        len(ref_hn_ids),
    )
    (out_dir / "bgem3_verifier_report.md").write_text(report, encoding="utf-8")

    log.info(
        "Done. Boundary: promoted=%d boundary=%d rejected=%d",
        len(promoted),
        len(kept_b),
        len(rejected),
    )
    return 0


def _overlap_note(pos_val: np.ndarray, hneg_val: np.ndarray) -> str:
    if pos_val.size == 0 or hneg_val.size == 0:
        return "insufficient validation scores"
    pmin, pmax = float(np.min(pos_val)), float(np.max(pos_val))
    hmin, hmax = float(np.min(hneg_val)), float(np.max(hneg_val))
    overlap = not (pmin > hmax or hmin > pmax)
    if overlap:
        return (
            f"WEAK separation: positive score range [{pmin:.4f},{pmax:.4f}] vs "
            f"hard-negative [{hmin:.4f},{hmax:.4f}] overlap."
        )
    return "Score ranges appear separated on validation."


def _build_report_md(
    ref_sets: dict,
    val_summary: dict,
    promoted: pd.DataFrame,
    rejected: pd.DataFrame,
    weak_separation: bool,
    reject_branch_used: bool,
    hard_neg_ref_count: int,
) -> str:
    def _table(df: pd.DataFrame, cols: list[str]) -> str:
        if df.empty:
            return "(none)"
        sub = df[cols] if all(c in df.columns for c in cols) else df
        return sub.head(20).to_string(index=False)

    lines = [
        "# BGE-M3 verifier report",
        "",
        "## Reference sets",
        "",
        f"- Positive (seed) reference papers: **{ref_sets['positive_reference_count']}**",
        f"- Hard-negative reference papers: **{ref_sets['hard_negative_reference_count']}**",
        f"- Easy-negative reference papers: **{ref_sets['easy_negative_reference_count']}**",
        "",
        "## Hard-negative clusters",
        "",
        f"`{ref_sets['hard_negative_cluster_ids']}`",
        "",
        "## Validation score summary (highlights)",
        "",
        f"- Positives (held-out): {val_summary.get('positive_validation_scores', {})}",
        f"- Hard negatives (held-out): {val_summary.get('hard_negative_validation_scores', {})}",
        f"- Overlap: {val_summary.get('overlap_note', '')}",
        "",
        "## Thresholds",
        "",
        f"- **UPPER** (≥ → core): `{val_summary.get('chosen_upper_threshold')}`",
        f"- **LOWER** (≤ → reject): `{val_summary.get('chosen_lower_threshold')}`",
        "",
        "## Boundary outcomes",
        "",
        json.dumps(val_summary.get("boundary_counts", {}), indent=2),
        "",
        f"- **Reject branch used:** {reject_branch_used}",
        "",
        "## Top 20 promoted (by score)",
        "",
        _table(
            promoted.sort_values("score", ascending=False).head(20) if len(promoted) else promoted,
            ["paper_id", "score", "title"],
        ),
        "",
        "## Top 20 rejected (lowest scores)",
        "",
        _table(
            rejected.sort_values("score", ascending=True).head(20) if len(rejected) else rejected,
            ["paper_id", "score", "title"],
        ),
        "",
        "## Caveats",
        "",
        f"- Hard-negative pool size (reference): {hard_neg_ref_count}",
        f"- Separation note: {'Weak/overlapping validation distributions.' if weak_separation else 'See overlap_note in bgem3_validation_summary.json.'}",
        "",
    ]
    if not reject_branch_used and val_summary.get("reject_branch_unused_explanation"):
        lines.extend(
            [
                "### Reject branch unused",
                "",
                val_summary["reject_branch_unused_explanation"],
                "",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
