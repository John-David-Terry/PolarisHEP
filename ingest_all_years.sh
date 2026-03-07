#!/usr/bin/env bash
set -euo pipefail

DB="inspire.sqlite"
START_YEAR=1687
END_YEAR=2025
SLEEP_BETWEEN_RETRIES=30
MAX_SLICE_RESULTS=10000
SIZE=1000
SLEEP=0.2
MAX_JOBS=4  # Number of parallel jobs (SQLite serializes writes, so >1 may cause lock contention)
# Tuning guide:
# - If you see frequent retries/lock errors from ingest_inspire.py, set MAX_JOBS=1
# - MAX_JOBS>1 helps if bottleneck is network/API, but can hurt if bottleneck is SQLite writes
# - With WAL mode, you can usually run 2-4 jobs, but monitor for lock contention
COUNT_SLEEP=0.1  # Rate limit for count queries (separate from ingestion)
CACHE_DIR="${TMPDIR:-/tmp}/inspire_cache_${START_YEAR}_${END_YEAR}"

echo "Starting INSPIRE ingestion"
echo "DB: ${DB}"
echo "Years: ${START_YEAR} → ${END_YEAR}"
echo "Max slice results: ${MAX_SLICE_RESULTS}"
echo "Parallel jobs: ${MAX_JOBS}"
echo ""
echo "WARNING: SQLite serializes writes. With MAX_JOBS>1, you may experience"
echo "lock contention. WAL mode and busy_timeout are enabled in the Python scripts."
echo "For heavy parallelism, consider MAX_JOBS=1 or using separate shard DBs."
echo ""
echo "Count query cache: $CACHE_DIR (persists across runs)"
echo "Count query rate limit: ${COUNT_SLEEP}s between queries"
echo ""

# -------------------- DATE HELPERS --------------------

py_next_month() {
  python - <<'PY' "$1"
y,m,_ = map(int, __import__("sys").argv[1].split("-"))
print(f"{y+1}-01-01" if m==12 else f"{y}-{m+1:02d}-01")
PY
}

py_next_day() {
  python - <<'PY' "$1"
from datetime import date, timedelta
y,m,d = map(int, __import__("sys").argv[1].split("-"))
print((date(y,m,d)+timedelta(days=1)).isoformat())
PY
}

py_days_in_month() {
  python - <<'PY' "$@"
import calendar
print(calendar.monthrange(int(__import__("sys").argv[1]), int(__import__("sys").argv[2]))[1])
PY
}

# -------------------- COUNT (NO SQLITE) --------------------

# Simple on-disk cache for count queries (persists across runs for same year range)
mkdir -p "$CACHE_DIR"
# Note: Cache is NOT auto-deleted - it persists to speed up restarts
# To clear: rm -rf "$CACHE_DIR"
# Optional: Add version/epoch to cache dir name (e.g., _v1_2026-01) to invalidate
# when INSPIRE metadata updates, or trust cached -1 failures only for short periods

get_total_cached() {
  local query="$1"
  # Use shasum (macOS) or sha256sum (Linux) with fallback
  local cache_key
  if command -v shasum >/dev/null 2>&1; then
    cache_key=$(echo -n "$query" | shasum -a 256 | cut -d' ' -f1)
  elif command -v sha256sum >/dev/null 2>&1; then
    cache_key=$(echo -n "$query" | sha256sum | cut -d' ' -f1)
  else
    # Fallback: use md5sum if available, otherwise a simple hash
    if command -v md5sum >/dev/null 2>&1; then
      cache_key=$(echo -n "$query" | md5sum | cut -d' ' -f1)
    else
      # Last resort: use a simple hash based on query length and first chars
      cache_key=$(echo -n "$query" | od -A n -t x1 | tr -d ' \n' | head -c 64)
    fi
  fi
  local cache_file="$CACHE_DIR/$cache_key"
  
  # Check cache first
  if [[ -f "$cache_file" ]]; then
    cat "$cache_file"
    return
  fi
  
  # Not cached - fetch and cache
  local result=$(get_total "$query")
  echo "$result" > "$cache_file"
  echo "$result"
  # Rate limit count queries
  sleep "$COUNT_SLEEP"
}

get_total() {
  python - <<'PY' "$1"
import json, subprocess, urllib.parse, sys, time, random
q = sys.argv[1]
url = "https://inspirehep.net/api/literature?q=" + urllib.parse.quote(q) + "&size=1"
max_tries = 5
for attempt in range(1, max_tries + 1):
    try:
        p = subprocess.run(["curl","-sS","--max-time","30",url],stdout=subprocess.PIPE,text=True,stderr=subprocess.DEVNULL)
        if p.returncode == 0:
            data = json.loads(p.stdout)
            total = data.get("hits", {}).get("total")
            if total is not None:
                print(total)
                sys.exit(0)
    except Exception:
        pass
    if attempt < max_tries:
        sleep_time = min(2 ** attempt, 10) * (0.7 + 0.6 * random.random())
        time.sleep(sleep_time)
# Failed after all retries - return sentinel
print(-1)
PY
}

# -------------------- CONTROL NUMBER BISECTION --------------------

get_global_cn_max() {
  python - <<'PY'
import json, subprocess, time, random, sys
url = "https://inspirehep.net/api/literature?sort=mostrecent&size=1"
max_tries = 5
for attempt in range(1, max_tries + 1):
    try:
        p = subprocess.run(["curl","-sS","--max-time","30",url],stdout=subprocess.PIPE,text=True,stderr=subprocess.DEVNULL)
        if p.returncode == 0:
            data = json.loads(p.stdout)
            cn = data.get("hits", {}).get("hits", [{}])[0].get("metadata", {}).get("control_number")
            if cn is not None:
                print(cn)
                sys.exit(0)
    except Exception:
        pass
    if attempt < max_tries:
        sleep_time = min(2 ** attempt, 10) * (0.7 + 0.6 * random.random())
        time.sleep(sleep_time)
PY
}

CNMAX_GLOBAL="$(get_global_cn_max)"

get_total_cn_range() {
  get_total_cached "de:$1->$2 AND control_number:[$3 TO $4]"
}

find_day_cn_min() {
  local lo=1 hi="$CNMAX_GLOBAL" ans="$CNMAX_GLOBAL"
  while (( lo<=hi )); do
    mid=$(( (lo+hi)/2 ))
    t=$(get_total_cn_range "$1" "$2" "$mid" "$CNMAX_GLOBAL" || echo "-1")
    if [[ "$t" == "-1" ]]; then
      echo "ERROR: Failed to get count for CN bisection (min search)" >&2
      return 1
    fi
    [[ "$t" -gt 0 ]] && ans="$mid" && hi=$((mid-1)) || lo=$((mid+1))
  done
  echo "$ans"
}

find_day_cn_max() {
  local lo=1 hi="$CNMAX_GLOBAL" ans=1
  while (( lo<=hi )); do
    mid=$(( (lo+hi)/2 ))
    t=$(get_total_cn_range "$1" "$2" 1 "$mid" || echo "-1")
    if [[ "$t" == "-1" ]]; then
      echo "ERROR: Failed to get count for CN bisection (max search)" >&2
      return 1
    fi
    [[ "$t" -gt 0 ]] && ans="$mid" && lo=$((mid+1)) || hi=$((mid-1))
  done
  echo "$ans"
}

ingest_slice() {
  local query="$1"
  local label="$2"
  until python ingest_inspire.py --db "$DB" --query "$query" --size "$SIZE" --max 0 --sleep "$SLEEP"
  do
    echo "[$(date +%H:%M:%S)] !! $label failed; retrying"
    sleep "$SLEEP_BETWEEN_RETRIES"
  done
  echo "[$(date +%H:%M:%S)] ✓ $label completed"
}

ingest_slice_worker() {
  local query="$1"
  local label="$2"
  ingest_slice "$query" "$label"
}

ingest_day_by_cn() {
  local ds="$1"
  local de="$2"
  local label="$3"
  cn_min=$(find_day_cn_min "$ds" "$de") || return 1
  cn_max=$(find_day_cn_max "$ds" "$de") || return 1
  echo "  CN range: $cn_min → $cn_max" >&2

  lo="$cn_min"
  while (( lo<=cn_max )); do
    left="$lo"; right="$cn_max"; best="$lo"
    while (( left<=right )); do
      mid=$(( (left+right)/2 ))
      t=$(get_total_cn_range "$ds" "$de" "$lo" "$mid" || echo "-1")
      if [[ "$t" == "-1" ]]; then
        echo "ERROR: Failed to get count for slice bisection, skipping slice" >&2
        return 1
      fi
      [[ "$t" -le "$MAX_SLICE_RESULTS" ]] && best="$mid" && left=$((mid+1)) || right=$((mid-1))
    done
    echo "de:$ds->$de AND control_number:[$lo TO $best]|$label cn:$lo-$best"
    lo=$((best+1))
  done
}

# Job queue management
job_queue=()

add_to_queue() {
  local query="$1"
  local label="$2"
  job_queue+=("$query|$label")
}

process_queue() {
  local total=${#job_queue[@]}
  local completed=0
  local active_pids=()
  local i=0

  echo "Processing $total work items with up to $MAX_JOBS parallel jobs..."

  while (( i < total || ${#active_pids[@]} > 0 )); do
    # Start new jobs up to MAX_JOBS
    while (( ${#active_pids[@]} < MAX_JOBS && i < total )); do
      IFS='|' read -r query label <<< "${job_queue[i]}"
      (
        ingest_slice_worker "$query" "$label"
      ) &
      active_pids+=($!)
      ((i++))
    done

    # Check for finished jobs and wait a bit
    sleep 0.5
    local new_pids=()
    for pid in "${active_pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        new_pids+=("$pid")
      else
        wait "$pid" 2>/dev/null || true
        ((completed++))
      fi
    done
    active_pids=("${new_pids[@]}")
    
    if (( completed % 10 == 0 && completed > 0 )); then
      echo "[Progress] $completed/$total completed, ${#active_pids[@]} active"
    fi
  done

  # Wait for any remaining jobs
  for pid in "${active_pids[@]}"; do
    wait "$pid" 2>/dev/null || true
    ((completed++))
  done

  echo "All $total work items completed."
}

# -------------------- MAIN LOOP --------------------
# Phase 1: Collect all work items into queue

echo "Phase 1: Collecting work items..."

for (( y=START_YEAR; y<=END_YEAR; y++ )); do
  for m in $(seq -w 1 12); do
    ms="$y-$m-01"
    me="$(py_next_month "$ms")"

    t=$(get_total_cached "de:$ms->$me" || echo "-1")
    if [[ "$t" == "-1" ]]; then
      echo "WARNING: Failed to get count for $y-$m, skipping" >&2
      continue
    elif [[ -z "$t" ]] || [[ "$t" == "0" ]]; then
      # No papers for this month, skip
      continue
    elif [[ "$t" -gt 0 ]] && [[ "$t" -le "$MAX_SLICE_RESULTS" ]]; then
      add_to_queue "de:$ms->$me" "$y-$m"
      continue
    fi
    # If we get here, t > MAX_SLICE_RESULTS, so split into days

    days=$(py_days_in_month "$y" "${m#0}")
    for d in $(seq -w 1 "$days"); do
      ds="$y-$m-$d"
      de="$(py_next_day "$ds")"

      td=$(get_total_cached "de:$ds->$de" || echo "-1")
      if [[ "$td" == "-1" ]]; then
        echo "WARNING: Failed to get count for $ds, skipping (will retry on next run)" >&2
      elif [[ -z "$td" ]] || [[ "$td" == "0" ]]; then
        # No papers for this day, skip
        continue
      elif [[ "$td" -gt 0 ]] && [[ "$td" -le "$MAX_SLICE_RESULTS" ]]; then
        add_to_queue "de:$ds->$de" "$ds"
      else
        # td > MAX_SLICE_RESULTS, split by control number
        # ingest_day_by_cn sends logs to stderr (>&2), so they won't be captured by process substitution
        # Only stdout (work items) will be captured
        while IFS= read -r line; do
          # Validate format: must be QUERY|LABEL (exactly one pipe)
          if [[ -n "$line" ]] && [[ "$line" =~ ^[^|]+\|[^|]+$ ]]; then
            job_queue+=("$line")
          fi
        done < <(ingest_day_by_cn "$ds" "$de" "$ds" 2>/dev/null)
      fi
    done
  done
done

echo "Collected ${#job_queue[@]} work items."

# Check if we should just generate work items file
if [[ "${GENERATE_WORK_ITEMS:-}" == "1" ]]; then
  WORK_ITEMS_FILE="${WORK_ITEMS_FILE:-work_items.txt}"
  echo "Writing work items to $WORK_ITEMS_FILE..." >&2
  > "$WORK_ITEMS_FILE"
  for item in "${job_queue[@]}"; do
    # Validate format: must contain exactly one pipe
    if [[ "$item" =~ ^[^|]+\|[^|]+$ ]]; then
      echo "$item" >> "$WORK_ITEMS_FILE"
    else
      echo "WARNING: Skipping invalid work item format: $item" >&2
    fi
  done
  item_count=$(wc -l < "$WORK_ITEMS_FILE" | tr -d ' ')
  echo "Wrote $item_count work items to $WORK_ITEMS_FILE" >&2
  echo "Split with: split -n l/4 -d $WORK_ITEMS_FILE work_items_part_" >&2
  exit 0
fi

# Phase 2: Process queue in parallel
process_queue

echo "Ingestion complete."
