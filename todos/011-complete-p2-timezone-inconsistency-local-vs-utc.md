---
status: pending
priority: p2
issue_id: "011"
tags: [code-review, correctness, workflows]
dependencies: []
---

# Timezone inconsistency: `create_run_folder` uses local time, `RunLog` uses UTC

## Problem Statement

Two places in the workflow subpackage stamp timestamps — using different timezones:

- `src/percell4/workflows/artifacts.py:83`:
  ```python
  ts = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")
  ```
  Local time. Folder name: `run_2026-04-10_143022_ab12cd34`.

- `src/percell4/workflows/run_log.py:58`:
  ```python
  datetime.now(UTC).isoformat()
  ```
  UTC. Log entry: `"ts": "2026-04-10T19:30:22+00:00"`.

A user in US/Central reading `run_config.json` (which records `started_at` via `datetime.now(UTC).astimezone()` — wait, verify which — but in any case mixing UTC and local) will see a 5-hour offset between the run folder name and the log entries, for the same run.

Additionally:
- Around DST boundaries (fall back), two runs one hour apart can produce identical local timestamps. The uuid suffix fixes filesystem collisions, but the folder **name** no longer sorts correctly in lexicographic order.
- The plan does not specify a canonical timezone.

## Findings

- **kieran-python-reviewer** (S14): two-timezone split is confusing and DST-fragile.

## Proposed Solutions

### Option A — Use UTC everywhere (Recommended)

Consistency with the run log. UTC has no DST, sorts lexicographically, and scientific provenance should be timezone-agnostic anyway.

```python
# artifacts.py
def create_run_folder(output_parent: Path) -> Path:
    ...
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")  # 'Z' suffix makes UTC explicit
    suffix = uuid.uuid4().hex[:8]
    folder = output_parent / f"run_{ts}_{suffix}"
    ...
```

Folder name becomes: `run_2026-04-10T193022Z_ab12cd34`.

- **Pros**: Single timezone, DST-safe, sorts correctly, explicit `Z` suffix avoids ambiguity. Matches `run_log.jsonl`.
- **Cons**: Users glancing at the folder name need to know UTC, not their local time. Minor — the run log and `run_config.json` have the timestamps in the same form.
- **Effort**: Small.
- **Risk**: Low.

### Option B — Use local time everywhere

Change `run_log.py` to use local time.

- **Pros**: Folder names match the user's wall clock.
- **Cons**: DST-fragile. Logs are less portable. Downgrades provenance quality.

### Option C — Use local for the folder name, UTC for everything else, and document it

- **Pros**: Folder name stays human-friendly.
- **Cons**: Permanent dual-timezone footgun. First person to compare a folder name to a log entry will be confused.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/artifacts.py:83` — `create_run_folder` timestamp format
- Verify `RunMetadata.started_at` / `finished_at` are stored as UTC datetimes (they're currently typed as `datetime` — confirm the creator uses `datetime.now(UTC)`)
- Update `docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md` examples that show `run_2026-04-10_143022_ab12cd34` → change to the new format

## Acceptance Criteria

- [ ] `create_run_folder` produces a UTC-based folder name
- [ ] `RunMetadata.started_at` / `finished_at` are UTC datetimes
- [ ] `run_log.jsonl` ts entries are still UTC
- [ ] `test_artifacts.py` round-trip uses UTC datetimes in fixtures
- [ ] Plan document's folder-layout example updated

## Work Log

- 2026-04-10 — Flagged by kieran-python-reviewer.

## Resources

- `src/percell4/workflows/artifacts.py:83`
- `src/percell4/workflows/run_log.py:58`
- Review source: kieran-python-reviewer Should #14
