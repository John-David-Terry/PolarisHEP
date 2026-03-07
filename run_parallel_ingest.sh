#!/usr/bin/env bash
set -euo pipefail

# This script orchestrates parallel ingestion using shard databases
# Usage: ./run_parallel_ingest.sh [num_workers]

NUM_WORKERS="${1:-4}"
WORK_ITEMS_FILE="work_items.txt"
TARGET_DB="inspire.sqlite"

echo "=== Parallel INSPIRE Ingestion ==="
echo "Workers: $NUM_WORKERS"
echo ""
echo "WARNING: With $NUM_WORKERS workers, API request volume increases ~${NUM_WORKERS}x."
echo "Make sure --sleep in ingest_inspire.py is conservative (default: 0.2s)."
echo "Monitor for rate limiting (429 errors) and adjust if needed."
echo ""

# Step 1: Generate work items (if not already done)
if [[ ! -f "$WORK_ITEMS_FILE" ]]; then
  echo "Step 1: Generating work items..."
  GENERATE_WORK_ITEMS=1 WORK_ITEMS_FILE="$WORK_ITEMS_FILE" bash ingest_all_years.sh
  echo ""
fi

TOTAL_ITEMS=$(wc -l < "$WORK_ITEMS_FILE" | tr -d ' ')
echo "Total work items: $TOTAL_ITEMS"
echo ""

# Step 2: Split work items into chunks
echo "Step 2: Splitting work items into $NUM_WORKERS parts..."
gsplit -n "l/$NUM_WORKERS" -d "$WORK_ITEMS_FILE" work_items_part_
echo "Created work_items_part_00 through work_items_part_$(printf "%02d" $((NUM_WORKERS-1)))"
echo ""

# Step 3: Run parallel workers
echo "Step 3: Starting $NUM_WORKERS parallel workers..."
for i in $(seq -f "%02g" 0 $((NUM_WORKERS-1))); do
  part_file="work_items_part_$i"
  shard_db="inspire_$i.sqlite"
  
  if [[ ! -f "$part_file" ]]; then
    echo "Warning: Part file $part_file not found, skipping"
    continue
  fi
  
  echo "Starting worker $i: $shard_db"
  bash run_part.sh "$part_file" "$shard_db" > "worker_${i}.log" 2>&1 &
done

echo "Waiting for all workers to complete..."
wait

echo ""
echo "All workers completed. Checking results..."
for i in $(seq -f "%02g" 0 $((NUM_WORKERS-1))); do
  shard_db="inspire_$i.sqlite"
  lock_file="${shard_db}.lock"
  if [[ -f "$shard_db" ]]; then
    size=$(du -h "$shard_db" | cut -f1)
    echo "  $shard_db: $size"
  else
    echo "  $shard_db: NOT FOUND"
  fi
  # Clean up any leftover lock files
  [[ -f "$lock_file" ]] && rm -f "$lock_file"
done
echo ""

# Step 4: Merge shards
echo "Step 4: Merging shards into $TARGET_DB..."
shard_dbs=()
for i in $(seq 0 $((NUM_WORKERS-1))); do
  shard_db="inspire_$i.sqlite"
  if [[ -f "$shard_db" ]]; then
    shard_dbs+=("$shard_db")
  fi
done

if (( ${#shard_dbs[@]} == 0 )); then
  echo "Error: No shard databases found to merge" >&2
  exit 1
fi

python merge_shards.py --target "$TARGET_DB" --shards "${shard_dbs[@]}"

echo ""
echo "=== Ingestion Complete ==="
echo "Target database: $TARGET_DB"
echo "Shard databases: ${shard_dbs[*]}"
echo ""
echo "To clean up shards: rm inspire_*.sqlite"

