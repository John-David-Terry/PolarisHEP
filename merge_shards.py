#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table (supports attached schema like 's0.table')."""
    try:
        result = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == column for row in result)
    except sqlite3.Error:
        return False


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure target database has the correct schema."""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS papers (
      control_number INTEGER PRIMARY KEY,
      title TEXT,
      abstract TEXT,
      date TEXT,
      arxiv_id TEXT,
      arxiv_cat TEXT,
      doi TEXT,
      inspire_url TEXT,
      updated_at_utc INTEGER
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS citations (
      citing INTEGER NOT NULL,
      cited  INTEGER NOT NULL,
      updated_at_utc INTEGER,
      PRIMARY KEY (citing, cited)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_citations_citing ON citations(citing)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_citations_cited  ON citations(cited)")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS paper_keywords (
      control_number INTEGER NOT NULL,
      keyword TEXT NOT NULL,
      source TEXT NOT NULL,
      updated_at_utc INTEGER,
      PRIMARY KEY (control_number, keyword, source)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keywords_kw ON paper_keywords(keyword)")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS meta (
      k TEXT PRIMARY KEY,
      v TEXT
    )
    """)
    conn.commit()


def merge_shards(target_db: str, shard_dbs: list[str]) -> None:
    """Merge shard databases into the target database."""
    # Check all shard files exist
    missing = [s for s in shard_dbs if not Path(s).exists()]
    if missing:
        print(f"Error: Shard databases not found: {missing}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(target_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    
    ensure_schema(conn)

    total_papers = 0
    total_citations = 0
    total_keywords = 0

    for i, shard_db in enumerate(shard_dbs):
        if not Path(shard_db).exists():
            print(f"Warning: Skipping missing shard: {shard_db}")
            continue

        print(f"Merging shard {i+1}/{len(shard_dbs)}: {shard_db}")
        
        # Attach shard database
        conn.execute(f"ATTACH DATABASE '{shard_db}' AS s{i}")
        
        try:
            # Check if tables exist in shard
            tables_check = conn.execute(f"""
                SELECT name FROM s{i}.sqlite_master 
                WHERE type='table' AND name IN ('papers', 'citations', 'paper_keywords')
            """).fetchall()
            existing_tables = {row[0] for row in tables_check}
            
            if not existing_tables:
                print(f"  Warning: No tables found in shard {shard_db}, skipping")
                conn.execute(f"DETACH DATABASE s{i}")
                continue
            
            papers_count = 0
            citations_count = 0
            keywords_count = 0
            
            # Merge papers (OR REPLACE - latest wins)
            # Use explicit column list to avoid schema drift issues
            if 'papers' in existing_tables:
                try:
                    shard_has_updated = has_column(conn, f"s{i}.papers", "updated_at_utc")
                    target_has_updated = has_column(conn, "papers", "updated_at_utc")
                    
                    if target_has_updated:
                        if shard_has_updated:
                            cursor = conn.execute(f"""
                                INSERT OR REPLACE INTO papers(
                                    control_number, title, abstract, date, arxiv_id, 
                                    arxiv_cat, doi, inspire_url, updated_at_utc
                                )
                                SELECT 
                                    control_number, title, abstract, date, arxiv_id, 
                                    arxiv_cat, doi, inspire_url, updated_at_utc
                                FROM s{i}.papers
                            """)
                        else:
                            cursor = conn.execute(f"""
                                INSERT OR REPLACE INTO papers(
                                    control_number, title, abstract, date, arxiv_id, 
                                    arxiv_cat, doi, inspire_url, updated_at_utc
                                )
                                SELECT 
                                    control_number, title, abstract, date, arxiv_id, 
                                    arxiv_cat, doi, inspire_url, NULL
                                FROM s{i}.papers
                            """)
                    else:
                        cursor = conn.execute(f"""
                            INSERT OR REPLACE INTO papers(
                                control_number, title, abstract, date, arxiv_id, 
                                arxiv_cat, doi, inspire_url
                            )
                            SELECT 
                                control_number, title, abstract, date, arxiv_id, 
                                arxiv_cat, doi, inspire_url
                            FROM s{i}.papers
                        """)
                    papers_count = cursor.rowcount
                    total_papers += papers_count
                except sqlite3.Error as e:
                    print(f"  Error merging papers from {shard_db}: {e}", file=sys.stderr)
                    raise
            
            # Merge citations (OR IGNORE - avoid duplicates)
            if 'citations' in existing_tables:
                try:
                    shard_has_updated = has_column(conn, f"s{i}.citations", "updated_at_utc")
                    target_has_updated = has_column(conn, "citations", "updated_at_utc")
                    
                    if target_has_updated:
                        if shard_has_updated:
                            cursor = conn.execute(f"""
                                INSERT OR IGNORE INTO citations(citing, cited, updated_at_utc)
                                SELECT citing, cited, updated_at_utc FROM s{i}.citations
                            """)
                        else:
                            cursor = conn.execute(f"""
                                INSERT OR IGNORE INTO citations(citing, cited, updated_at_utc)
                                SELECT citing, cited, NULL FROM s{i}.citations
                            """)
                    else:
                        cursor = conn.execute(f"""
                            INSERT OR IGNORE INTO citations(citing, cited)
                            SELECT citing, cited FROM s{i}.citations
                        """)
                    citations_count = cursor.rowcount
                    total_citations += citations_count
                except sqlite3.Error as e:
                    print(f"  Error merging citations from {shard_db}: {e}", file=sys.stderr)
                    raise
            
            # Merge keywords (OR REPLACE - latest wins)
            if 'paper_keywords' in existing_tables:
                try:
                    shard_has_updated = has_column(conn, f"s{i}.paper_keywords", "updated_at_utc")
                    target_has_updated = has_column(conn, "paper_keywords", "updated_at_utc")
                    
                    if target_has_updated:
                        if shard_has_updated:
                            cursor = conn.execute(f"""
                                INSERT OR REPLACE INTO paper_keywords(control_number, keyword, source, updated_at_utc)
                                SELECT control_number, keyword, source, updated_at_utc FROM s{i}.paper_keywords
                            """)
                        else:
                            cursor = conn.execute(f"""
                                INSERT OR REPLACE INTO paper_keywords(control_number, keyword, source, updated_at_utc)
                                SELECT control_number, keyword, source, NULL FROM s{i}.paper_keywords
                            """)
                    else:
                        cursor = conn.execute(f"""
                            INSERT OR REPLACE INTO paper_keywords(control_number, keyword, source)
                            SELECT control_number, keyword, source FROM s{i}.paper_keywords
                        """)
                    keywords_count = cursor.rowcount
                    total_keywords += keywords_count
                except sqlite3.Error as e:
                    print(f"  Error merging keywords from {shard_db}: {e}", file=sys.stderr)
                    raise
            
            conn.commit()
            print(f"  Papers: {papers_count}, Citations: {citations_count}, Keywords: {keywords_count}")
            
        except sqlite3.Error as e:
            print(f"Error merging {shard_db}: {e}", file=sys.stderr)
            conn.rollback()
        finally:
            conn.execute(f"DETACH DATABASE s{i}")

    conn.close()
    print(f"\nMerge complete: {target_db}")
    print(f"Total: {total_papers} papers, {total_citations} citations, {total_keywords} keywords")


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge shard databases into a single database")
    ap.add_argument("--target", default="inspire.sqlite", help="Target database file")
    ap.add_argument("--shards", nargs="+", required=True, help="Shard database files to merge")
    args = ap.parse_args()

    merge_shards(args.target, args.shards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

