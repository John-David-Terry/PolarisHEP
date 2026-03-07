#!/usr/bin/env python3
"""
Regenerate top200_manifest.csv sorted by internal citation metrics.

Usage:
    python regenerate_top200.py --sort-by total    # Sort by indegree + outdegree
    python regenerate_top200.py --sort-by indegree # Sort by indegree (default)
    python regenerate_top200.py --sort-by outdegree # Sort by outdegree
    python regenerate_top200.py --limit 200         # Change number of papers
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


def regenerate_manifest(
    db_path: str,
    output_path: str,
    sort_by: str = "total",
    limit: int = 200,
) -> None:
    """Regenerate top200_manifest.csv sorted by specified metric."""
    conn = sqlite3.connect(db_path)
    
    # Build the ORDER BY clause based on sort_by
    if sort_by == "total":
        order_by = "(indeg_in_subgraph + outdeg_in_subgraph) DESC"
        metric_name = "total_internal_citations"
    elif sort_by == "indegree":
        order_by = "indeg_in_subgraph DESC"
        metric_name = "indeg_in_subgraph"
    elif sort_by == "outdegree":
        order_by = "outdeg_in_subgraph DESC"
        metric_name = "outdeg_in_subgraph"
    else:
        print(f"Error: Unknown sort-by option: {sort_by}", file=sys.stderr)
        print("Valid options: total, indegree, outdegree", file=sys.stderr)
        sys.exit(1)
    
    # Query the ranking table joined with papers for full metadata
    query = f"""
    SELECT 
        r.cn,
        r.indeg_in_subgraph,
        r.outdeg_in_subgraph,
        r.depth,
        COALESCE(p.arxiv_id, r.arxiv_id) as arxiv_id,
        COALESCE(p.doi, r.doi_cached) as doi,
        p.inspire_url,
        COALESCE(p.title, r.title_cached) as title
    FROM subgraph_rank_25808_present r
    LEFT JOIN papers p ON r.cn = p.control_number
    WHERE r.cn IN (SELECT control_number FROM subgraph_nodes_25808_present_top200)
    ORDER BY {order_by}
    LIMIT ?
    """
    
    cursor = conn.execute(query, (limit,))
    rows = cursor.fetchall()
    
    # Write to CSV
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # Header
        writer.writerow([
            'cn',
            'indeg_in_subgraph',
            'outdeg_in_subgraph',
            'total_internal_citations',
            'depth',
            'arxiv_id',
            'doi',
            'inspire_url',
            'title'
        ])
        
        # Data rows
        for row in rows:
            cn, indeg, outdeg, depth, arxiv_id, doi, inspire_url, title = row
            total = indeg + outdeg if indeg and outdeg else (indeg or 0) + (outdeg or 0)
            writer.writerow([
                cn,
                indeg or 0,
                outdeg or 0,
                total,
                depth or 0,
                arxiv_id or '',
                doi or '',
                inspire_url or '',
                title or ''
            ])
    
    conn.close()
    print(f"Generated {output_path} with {len(rows)} papers")
    print(f"Sorted by: {sort_by} ({metric_name})")
    print(f"Top paper: cn={rows[0][0]}, indegree={rows[0][1]}, outdegree={rows[0][2]}, total={rows[0][1] + rows[0][2] if rows[0][1] and rows[0][2] else (rows[0][1] or 0) + (rows[0][2] or 0)}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate top200_manifest.csv sorted by internal citation metrics"
    )
    ap.add_argument(
        "--db",
        default="inspire.sqlite",
        help="Path to inspire.sqlite database"
    )
    ap.add_argument(
        "--output",
        default="top200_manifest.csv",
        help="Output CSV file path"
    )
    ap.add_argument(
        "--sort-by",
        choices=["total", "indegree", "outdegree"],
        default="total",
        help="Sort by: total (indegree+outdegree), indegree, or outdegree"
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Number of papers to include (default: 200)"
    )
    
    args = ap.parse_args()
    
    if not Path(args.db).exists():
        print(f"Error: Database not found: {args.db}", file=sys.stderr)
        return 1
    
    regenerate_manifest(args.db, args.output, args.sort_by, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

