---
status: pending
priority: p2
issue_id: "007"
tags: [code-review, correctness, durability, workflows]
dependencies: []
---

# `write_atomic` has suffix collision risk and does not fsync before replace

## Problem Statement

`src/percell4/workflows/artifacts.py:53-64`:

```python
tmp = path.with_suffix(path.suffix + ".tmp")
```

Two issues:

1. **Suffix collision on multi-dot paths.** `Path("measurements.parquet.gz").with_suffix(".gz.tmp")` returns `measurements.parquet.tmp`, silently clobbering an unrelated file named `measurements.parquet` in the parent. Unlikely in today's code (we only write `run_config.json` via this helper), but a footgun if the helper gets reused.

2. **No fsync of the tmp file before `os.replace`.** The `writer_fn` writes via `tmp.write_text(...)` which does not fsync. On a crash between `writer_fn` returning and `os.replace` completing (or even after), a power loss on ext4 / APFS can leave the user with the replace committed but the file contents on disk being zeros. The run-log helper already fsyncs on every write (`run_log.py:67-70`) — the inconsistency is jarring. For `run_config.json` this is a lost-update scenario that renders the run unreadable post-crash.

## Findings

- **kieran-python-reviewer** (S1): suffix collision + missing fsync. Proposes `tmp = path.with_name(path.name + ".tmp")` or `tempfile.mkstemp`.
- **architecture-strategist** (N3): same point — inconsistent with `run_log.py`'s fsync discipline.

## Proposed Solutions

### Option A — Fix both issues (Recommended)

```python
def write_atomic(path: Path, writer_fn: Callable[[Path], None]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use `.name + ".tmp"` rather than `with_suffix` to avoid clobbering
    # siblings of a multi-dot file (e.g. measurements.parquet.gz).
    tmp = path.with_name(path.name + ".tmp")
    try:
        writer_fn(tmp)
        # Ensure the temp file's contents are on disk before the rename so a
        # crash between write and replace does not surface a zero-length file.
        with open(tmp, "rb") as fd:
            os.fsync(fd.fileno())
    except BaseException:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    os.replace(tmp, path)
    # Optional: fsync the parent directory so the rename itself is durable.
    # Requires opening the directory with os.open(..., O_DIRECTORY) on POSIX;
    # skip on Windows where directory fsync is not supported.
    if os.name == "posix":
        try:
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # best-effort
```

- **Pros**: Eliminates the suffix collision. Fsyncs both the file contents and the parent directory so the rename is fully durable. Matches `run_log.py`'s discipline.
- **Cons**: An extra open+fsync pair per atomic write. For `run_config.json` (written once at start, once at finish) this is imperceptible.
- **Effort**: Small.
- **Risk**: Low. Directory fsync is POSIX-only; the guard keeps Windows working.

### Option B — Fix only the suffix collision, defer fsync

```python
tmp = path.with_name(path.name + ".tmp")
```

- **Pros**: Minimal change, solves the more obvious bug.
- **Cons**: Leaves the crash-safety gap. Inconsistency with `run_log.py` persists.
- **Effort**: Trivial.

### Option C — Pass the file handle to the writer

Change `write_atomic(path, writer_fn)` so `writer_fn` receives an open file handle (already fsync-able) rather than a Path:

```python
def write_atomic(path: Path, writer_fn: Callable[[BinaryIO], None]) -> None:
    ...
    with open(tmp, "wb") as fd:
        writer_fn(fd)
        fd.flush()
        os.fsync(fd.fileno())
    os.replace(tmp, path)
```

- **Pros**: Forces the writer to deal with binary data (consistent encoding), makes fsync unambiguous.
- **Cons**: Breaks the existing callsite in `write_run_config` which uses `tmp.write_text(blob)`. Changes the API for a single Phase 1 callsite.
- **Effort**: Medium.
- **Risk**: Low.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/artifacts.py:40-64` — `write_atomic` implementation
- Add a test in `tests/test_workflows/test_artifacts.py`:
  - Assert no residue with multi-dot paths
  - Assert that after `write_atomic`, the written file contents match (we already have this, just verify the fsync path doesn't break anything)

## Acceptance Criteria

- [ ] `write_atomic(Path("foo.parquet.gz"), ...)` does not clobber `foo.parquet`
- [ ] `write_atomic` fsyncs the tmp file before `os.replace`
- [ ] On POSIX, the parent directory is also fsynced
- [ ] Existing `test_write_atomic_*` tests still pass
- [ ] New test covers the multi-dot edge case

## Work Log

- 2026-04-10 — Identified by kieran-python-reviewer and architecture-strategist.

## Resources

- `src/percell4/workflows/artifacts.py:40-64`
- `src/percell4/workflows/run_log.py:67-70` (the fsync-per-write precedent)
- `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md` (the `os.replace` without prior `unlink` rule)
