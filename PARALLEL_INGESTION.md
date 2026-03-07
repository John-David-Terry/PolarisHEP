# Parallel Ingestion Guide

This guide explains how to use the sharded database approach for parallel ingestion without SQLite lock contention.

## Overview

Instead of having multiple processes write to the same SQLite database (which causes lock contention), we:
1. Generate all work items (query slices)
2. Split them into N chunks
3. Each worker processes one chunk into its own shard database
4. Merge all shards into the final database

## Quick Start

```bash
# Run parallel ingestion with 4 workers (default)
./run_parallel_ingest.sh 4

# Or with a different number of workers
./run_parallel_ingest.sh 8
```

## Manual Steps

### Step 1: Generate Work Items

```bash
GENERATE_WORK_ITEMS=1 WORK_ITEMS_FILE=work_items.txt bash ingest_all_years.sh
```

This creates `work_items.txt` with format: `QUERY|LABEL`

### Step 2: Split Work Items

```bash
split -n l/4 -d work_items.txt work_items_part_
```

This creates:
- `work_items_part_00`
- `work_items_part_01`
- `work_items_part_02`
- `work_items_part_03`

### Step 3: Run Workers in Parallel

```bash
bash run_part.sh work_items_part_00 inspire_0.sqlite > worker_0.log 2>&1 &
bash run_part.sh work_items_part_01 inspire_1.sqlite > worker_1.log 2>&1 &
bash run_part.sh work_items_part_02 inspire_2.sqlite > worker_2.log 2>&1 &
bash run_part.sh work_items_part_03 inspire_3.sqlite > worker_3.log 2>&1 &
wait
```

### Step 4: Merge Shards

```bash
python merge_shards.py --target inspire.sqlite \
  --shards inspire_0.sqlite inspire_1.sqlite inspire_2.sqlite inspire_3.sqlite
```

## Files Created

- `work_items.txt` - All query slices (one per line: `QUERY|LABEL`)
- `work_items_part_00` through `work_items_part_NN` - Split chunks
- `inspire_0.sqlite` through `inspire_N.sqlite` - Shard databases
- `inspire.sqlite` - Final merged database
- `worker_*.log` - Worker logs

## Merge Strategy

The merge script uses:
- **papers**: `INSERT OR REPLACE` (latest wins if duplicate control_number)
- **citations**: `INSERT OR IGNORE` (no duplicates on (citing, cited))
- **paper_keywords**: `INSERT OR REPLACE` (latest wins if duplicate)

## Cleanup

After successful merge, you can delete shards:
```bash
rm inspire_*.sqlite work_items_part_* worker_*.log
```

Keep `work_items.txt` if you want to re-run ingestion later.

## Advantages

- ✅ No SQLite lock contention (each worker has its own DB)
- ✅ True parallelism (limited only by API rate limits)
- ✅ Can resume individual workers if one fails
- ✅ Can merge incrementally (merge daily/weekly)
- ✅ Safe merge operations (handles duplicates correctly)

## Notes

- Each shard database is independent and can be processed separately
- You can merge shards at any time (they're additive)
- If a worker fails, you can re-run just that shard
- The merge operation is idempotent (safe to run multiple times)

