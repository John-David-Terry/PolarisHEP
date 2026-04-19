"""Shared helpers for INSPIRE corpus embedding (text layout, DB read-only)."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import unicodedata
from pathlib import Path

_DASH_TRANSLATION = dict.fromkeys(
    map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), ord("-")
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_db_path(root: Path, primary: str) -> Path:
    db_path = (root / primary).expanduser().resolve()
    if db_path.is_file():
        return db_path
    alt = root / "inspire.sqlite"
    if alt.is_file():
        logging.getLogger("inspire_emb").warning("Primary DB missing (%s); using inspire.sqlite", db_path)
        return alt.resolve()
    raise FileNotFoundError(f"database file not found: {db_path}")


def connect_readonly_sqlite(db_path: Path) -> sqlite3.Connection:
    """
    Open SQLite read-only via URI (?mode=ro).

    https://www.sqlite.org/uri.html
    """
    uri = db_path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_cell(s: str | None) -> str:
    if s is None:
        return ""
    return str(s)


def normalize_dashes(s: str) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFKC", s)
    t = t.translate(_DASH_TRANSLATION)
    return re.sub(r"\s+", " ", t).strip()


def normalize_keywords_blob(raw: str | None) -> tuple[str, list[str]]:
    """Return '; '-joined unique keywords preserving order."""
    if not raw:
        return "", []
    parts = [normalize_dashes(p) for p in raw.replace(";", "\n").split("\n")]
    parts = [p for p in parts if p]
    uniq = list(dict.fromkeys(parts))
    return "; ".join(uniq), uniq


def canonical_embedding_text(title: str, keywords_joined: str, abstract: str) -> str:
    """
    Single canonical layout for embedding (must match across build + embed).

    [Title] / [Keywords] / [Abstract] sections; omit empty sections entirely.
    """
    parts: list[str] = []
    t = normalize_dashes(normalize_cell(title))
    k = normalize_cell(keywords_joined).strip()
    a = normalize_dashes(normalize_cell(abstract))

    if t:
        parts.append("[Title]\n" + t)
    if k:
        parts.append("[Keywords]\n" + k)
    if a:
        parts.append("[Abstract]\n" + a)
    return "\n\n".join(parts)


def text_hash_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def field_pattern_flags(has_t: bool, has_a: bool, has_k: bool) -> str:
    """Human-readable availability pattern for stats."""
    bits = []
    bits.append("T" if has_t else "-")
    bits.append("A" if has_a else "-")
    bits.append("K" if has_k else "-")
    return "".join(bits)


def keyword_count_from_joined(kw_joined: str) -> int:
    if not kw_joined.strip():
        return 0
    return len([x for x in kw_joined.split(";") if x.strip()])


def load_sentence_transformer_model(model_name: str, device: str | None):
    """
    Load SentenceTransformer-compatible checkpoint.

    Defaults to explicit Transformer+Pooling+Normalize with safetensors weights so
    older PyTorch versions avoid vulnerable torch.load paths on `.bin` checkpoints.
    """
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
