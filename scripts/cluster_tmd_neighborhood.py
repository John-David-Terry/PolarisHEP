#!/usr/bin/env python3
"""
STEP 2: TF-IDF + TruncatedSVD + KMeans clustering on neighborhood text.

Uses only rows with has_text = 1. Field structure is inferred from text, not citations.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import unicodedata
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from tqdm import tqdm

from tmd_discovery_common import repo_root


# Default KMeans — user may override CLI
DEFAULT_K = 50
DEFAULT_SVD_DIMS = 100
RANDOM_STATE = 42

# Unicode dashes → ASCII hyphen (physics titles/abstracts)
_DASH_TRANSLATION = dict.fromkeys(
    map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), ord("-")
)


def preprocess_text(raw: str) -> str:
    """Lowercase, NFKC, dash normalization, whitespace collapse."""
    if not raw:
        return ""
    t = unicodedata.normalize("NFKC", raw)
    t = t.translate(_DASH_TRANSLATION)
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("cluster")

    ap = argparse.ArgumentParser(description="Cluster TMD neighborhood with TF-IDF/SVD/KMeans")
    ap.add_argument(
        "--input",
        default="data/tmd_field_discovery/neighborhood.csv",
        help="neighborhood.csv path",
    )
    ap.add_argument("--out-dir", default="data/tmd_field_discovery", help="Output directory")
    ap.add_argument("--kmeans-k", type=int, default=DEFAULT_K, help="Number of clusters")
    ap.add_argument("--svd-dims", type=int, default=DEFAULT_SVD_DIMS, help="TruncatedSVD dimensions")
    ap.add_argument("--max-df", type=float, default=0.85, help="TF-IDF max_df")
    ap.add_argument("--min-df", type=int, default=None, help="TF-IDF min_df (auto if omitted)")
    ap.add_argument("--n-init", type=int, default=20, help="KMeans n_init")
    args = ap.parse_args()

    root = repo_root()
    in_path = (root / args.input).expanduser().resolve()
    out_dir = (root / args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_path.is_file():
        raise FileNotFoundError(f"neighborhood.csv not found: {in_path}")

    rows: list[dict] = []
    with open(in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("has_text", "")).strip() not in ("1", "True", "true"):
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            rows.append(row)

    n_samples = len(rows)
    if n_samples < 2:
        raise ValueError(f"Not enough papers with usable text: {n_samples}")

    processed = [preprocess_text(r.get("text", "")) for r in rows]
    min_df = args.min_df
    if min_df is None:
        min_df = min(5, max(2, n_samples // 500))
        min_df = max(2, min_df)
    log.info("Clustering %d papers; min_df=%s max_df=%s k=%d svd=%d", n_samples, min_df, args.max_df, args.kmeans_k, args.svd_dims)

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=min_df,
        max_df=args.max_df,
        stop_words="english",
        sublinear_tf=True,
        dtype=np.float64,
    )
    X = vectorizer.fit_transform(processed)
    n_features = X.shape[1]
    if n_features < 2:
        raise ValueError(f"TF-IDF vocabulary too small: {n_features} features")

    n_comp = min(
        args.svd_dims,
        max(2, n_features - 1),
        max(2, n_samples - 1),
    )
    log.info("TruncatedSVD n_components=%d (features=%d)", n_comp, n_features)

    svd = TruncatedSVD(n_components=n_comp, random_state=RANDOM_STATE)
    X_reduced = svd.fit_transform(X)

    k = min(args.kmeans_k, n_samples)
    kmeans = KMeans(
        n_clusters=k,
        random_state=RANDOM_STATE,
        n_init=args.n_init,
    )
    labels = kmeans.fit_predict(X_reduced)

    feat_names = vectorizer.get_feature_names_out()

    cluster_sizes: dict[int, int] = {}
    cluster_seed_counts: dict[int, int] = {}
    cluster_text_lens: dict[int, list[int]] = {}

    assignments_path = out_dir / "cluster_assignments.csv"
    with open(assignments_path, "w", newline="", encoding="utf-8") as f:
        fields = ["control_number", "cluster_id", "is_seed", "title", "abstract", "text_length"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row, cid in zip(rows, labels):
            cid_i = int(cid)
            cluster_sizes[cid_i] = cluster_sizes.get(cid_i, 0) + 1
            is_seed = str(row.get("is_seed", "0")).strip() in ("1", "True", "true")
            if is_seed:
                cluster_seed_counts[cid_i] = cluster_seed_counts.get(cid_i, 0) + 1
            title = row.get("title") or ""
            abstract = row.get("abstract") or ""
            tl = len((title or "") + (abstract or ""))
            cluster_text_lens.setdefault(cid_i, []).append(tl)
            w.writerow(
                {
                    "control_number": int(row["control_number"]),
                    "cluster_id": cid_i,
                    "is_seed": 1 if is_seed else 0,
                    "title": title,
                    "abstract": abstract,
                    "text_length": len((row.get("text") or "")),
                }
            )

    # Mean TF-IDF per cluster (transparent top terms)
    summary: list[dict] = []
    top_terms_rows: list[dict] = []

    for cid in tqdm(sorted(cluster_sizes.keys()), desc="cluster top terms"):
        mask = labels == cid
        sub = X[mask]
        if sub.shape[0] == 0:
            continue
        mean_vec = np.asarray(sub.mean(axis=0)).ravel()
        top_n = min(25, mean_vec.size)
        top_idx = np.argsort(mean_vec)[-top_n:][::-1]
        top_terms = [str(feat_names[i]) for i in top_idx if mean_vec[i] > 0]
        top_weights = [float(mean_vec[i]) for i in top_idx if mean_vec[i] > 0]

        for rank, (term, wt) in enumerate(zip(top_terms, top_weights), start=1):
            top_terms_rows.append(
                {
                    "cluster_id": cid,
                    "rank": rank,
                    "term": term,
                    "weight_or_score": wt,
                }
            )

        sc = cluster_seed_counts.get(cid, 0)
        sz = cluster_sizes[cid]
        mean_tl = float(np.mean(cluster_text_lens.get(cid, [0])))
        summary.append(
            {
                "cluster_id": cid,
                "size": sz,
                "seed_count": sc,
                "seed_fraction": sc / sz if sz else 0.0,
                "top_terms": top_terms[:15],
                "mean_text_length_chars": round(mean_tl, 2),
                "tfidf_top_term_weights": dict(zip(top_terms[:15], [float(w) for w in top_weights[:15]])),
            }
        )

    summary.sort(key=lambda x: x["cluster_id"])
    cluster_summary_path = out_dir / "cluster_summary.json"
    cluster_summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    top_terms_path = out_dir / "cluster_top_terms.csv"
    with open(top_terms_path, "w", newline="", encoding="utf-8") as f:
        wt = csv.DictWriter(f, fieldnames=["cluster_id", "rank", "term", "weight_or_score"])
        wt.writeheader()
        for r in top_terms_rows:
            wt.writerow(r)

    meta_extra = {
        "n_samples_clustered": n_samples,
        "n_features_tfidf": int(n_features),
        "svd_components_used": int(n_comp),
        "kmeans_k_used": int(k),
        "min_df": min_df,
        "max_df": args.max_df,
        "random_state": RANDOM_STATE,
    }
    meta_path = out_dir / "cluster_run_metadata.json"
    meta_path.write_text(json.dumps(meta_extra, indent=2) + "\n", encoding="utf-8")

    log.info("Wrote %s", assignments_path)
    log.info("Wrote %s", cluster_summary_path)
    log.info("Wrote %s", top_terms_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
