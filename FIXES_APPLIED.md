# Critical Fixes Applied

## Issues Fixed

### 1. ✅ **Pagination Retry Logic** (CRITICAL)
**Problem**: `ingest_inspire.py` was using `sess.get()` directly for pagination, which would crash on transient failures instead of retrying.

**Fix**: Changed line 270 to use `get_with_retry(sess, next_url, timeout=60)` so all pagination requests have retry/backoff logic.

**Impact**: Prevents shards from dying midstream due to transient API failures.

### 2. ✅ **Work Items Generation Validation**
**Problem**: No validation that work items are in correct `QUERY|LABEL` format, and stderr messages could leak into output.

**Fix**: 
- Added format validation (must contain exactly one pipe)
- Redirected all status messages to stderr (`>&2`)
- Only valid work items are written to file

**Impact**: Ensures deterministic, clean work items file.

### 3. ✅ **Merge Schema Safety**
**Problem**: Using `SELECT *` could fail if schemas drift between shards.

**Fix**: Changed to explicit column lists for all tables:
- `papers`: All 9 columns explicitly listed
- `citations`: 3 columns explicitly listed  
- `paper_keywords`: 4 columns explicitly listed

**Impact**: Prevents merge failures due to schema differences.

### 4. ✅ **Missing Tables Handling**
**Problem**: "Handles missing tables gracefully" was silently skipping shards with no tables.

**Fix**: 
- Added explicit check and warning if no tables found
- Added try/except with error messages for each merge operation
- Errors are raised (not silently ignored) to prevent data loss

**Impact**: Prevents silent data loss, makes issues visible.

### 5. ✅ **Shard DB Lock Protection**
**Problem**: No protection against multiple workers using the same shard DB.

**Fix**: Added lock file mechanism in `run_part.sh`:
- Creates `${SHARD_DB}.lock` with PID
- Checks for existing lock before starting
- Auto-cleans on exit (trap)
- Parallel ingest script cleans up locks after completion

**Impact**: Prevents database corruption from concurrent writes.

### 6. ✅ **Rate Limiting Warning**
**Problem**: No warning about increased API request volume with parallel workers.

**Fix**: Added warning in `run_parallel_ingest.sh` about N× request volume and recommendation to monitor for 429 errors.

**Impact**: Users are aware of rate limiting risks.

## Verification Checklist

- [x] Pagination uses `get_with_retry()` - **FIXED**
- [x] Work items are valid `QUERY|LABEL` format - **FIXED**
- [x] No stderr leakage in work items file - **FIXED**
- [x] Merge uses explicit column lists - **FIXED**
- [x] Missing tables don't silently drop data - **FIXED**
- [x] Shard DBs protected from concurrent access - **FIXED**
- [x] Keywords/citations enabled in workers - **VERIFIED** (uses full `ingest_inspire.py`)
- [x] Deterministic work items generation - **VERIFIED** (fixed iteration order, cached counts)

## Remaining Considerations

1. **API Rate Limiting**: With 4 workers, monitor for 429 errors. Consider:
   - Increasing `--sleep` in `run_part.sh` if needed
   - Using fewer workers if rate limited
   - Adding exponential backoff for 429 specifically

2. **Cache Determinism**: Work items generation uses cached counts. If INSPIRE updates metadata between runs, counts may differ. This is expected behavior.

3. **Merge Idempotency**: Merge operations are safe to run multiple times:
   - `papers`: OR REPLACE (latest wins)
   - `citations`: OR IGNORE (no duplicates)
   - `paper_keywords`: OR REPLACE (latest wins)

## Testing Recommendations

Before running on 1.5M papers:
1. Test with small date range (e.g., 1 month)
2. Verify work items file format
3. Test one worker end-to-end
4. Test merge with 2 shards
5. Monitor API rate limits with 4 workers

