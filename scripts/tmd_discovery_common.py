"""Shared helpers for TMD field discovery scripts (paths, read-only SQLite, text)."""

from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_db_path(root: Path, primary: str) -> Path:
    """Return existing DB path; fall back from inspire_mirror.sqlite to inspire.sqlite."""
    db_path = (root / primary).expanduser().resolve()
    if db_path.is_file():
        return db_path
    alt = root / "inspire.sqlite"
    if alt.is_file():
        logging.getLogger("tmd").warning("Primary DB missing (%s); using inspire.sqlite", db_path)
        return alt.resolve()
    raise FileNotFoundError(f"database file not found: {db_path} (fallback inspire.sqlite also missing)")


def connect_readonly_sqlite(db_path: Path) -> sqlite3.Connection:
    """
    Open SQLite in read-only mode via URI (?mode=ro).

    Uses absolute path URI; see https://www.sqlite.org/uri.html
    """
    uri = db_path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_cell(s: str | None) -> str:
    if s is None:
        return ""
    return str(s)


def combined_text(title: str, abstract: str) -> str:
    t, a = normalize_cell(title).strip(), normalize_cell(abstract).strip()
    if a:
        return t + "\n\n" + a if t else a
    return t


def read_seed_ids(seed_csv: Path, id_column: str) -> list[int]:
    if not seed_csv.is_file():
        raise FileNotFoundError(f"seed CSV not found: {seed_csv}")
    with open(seed_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or id_column not in reader.fieldnames:
            raise ValueError(f"seed CSV missing column {id_column!r}; got {reader.fieldnames}")
        out: list[int] = []
        for row in reader:
            raw = row.get(id_column, "").strip()
            if not raw:
                continue
            out.append(int(raw))
    return sorted(set(out))


def batches(items: list[int], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
