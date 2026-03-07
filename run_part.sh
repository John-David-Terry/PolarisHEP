#!/usr/bin/env bash
set -euo pipefail

# Usage: run_part.sh <part_file> <shard_db>
# Example: run_part.sh work_items_part_00 inspire_0.sqlite

PART_FILE="$1"
SHARD_DB="$2"
SIZE=1000
SLEEP=0.2

if [[ ! -f "$PART_FILE" ]]; then
  echo "Error: Part file '$PART_FILE' not found" >&2
  exit 1
fi

# Safety check: prevent multiple workers from using the same shard DB
LOCK_FILE="${SHARD_DB}.lock"
if [[ -f "$LOCK_FILE" ]]; then
  echo "Error: Lock file exists for $SHARD_DB. Another worker may be using it." >&2
  echo "If you're sure no other worker is running, remove: $LOCK_FILE" >&2
  exit 1
fi

# Create lock file
echo "$$" > "$LOCK_FILE"
trap "rm -f '$LOCK_FILE'" EXIT INT TERM

echo "[$(date +%H:%M:%S)] Starting ingestion for $SHARD_DB from $PART_FILE"
# Note: ingest_inspire.py will create the schema automatically and includes keywords/citations

total_lines=$(wc -l < "$PART_FILE" | tr -d ' ')
processed=0

while IFS='|' read -r query label; do
  [[ -z "$query" ]] && continue
  echo "[$(date +%H:%M:%S)] Processing: $label"
  
  until python ingest_inspire.py --db "$SHARD_DB" --query "$query" --size "$SIZE" --max 0 --sleep "$SLEEP"
  do
    echo "[$(date +%H:%M:%S)] !! $label failed; retrying"
    sleep 30
  done
  
  ((processed++))
  if (( processed % 10 == 0 )); then
    echo "[$(date +%H:%M:%S)] Progress: $processed/$total_lines completed for $SHARD_DB"
  fi
done < "$PART_FILE"

echo "[$(date +%H:%M:%S)] Completed $SHARD_DB: $processed/$total_lines items processed"

