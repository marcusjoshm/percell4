---
title: "Atomic file writes — tmp + os.replace, no platform branching"
date: 2026-04-30
category: architecture-patterns
module: percell4.store, percell4.project, percell4.workflows.artifacts
problem_type: convention
component: tooling
canonical_source: src/percell4/store.py
applies_to:
  - "src/percell4/store.py"
  - "src/percell4/project.py"
  - "src/percell4/workflows/artifacts.py"
  - "any future write-an-output-file site"
duplicates_at:
  - {path: "src/percell4/store.py", note: "DatasetStore.create_atomic (lines 471-498) — canonical implementation"}
  - {path: "src/percell4/project.py", note: "ProjectIndex._write_atomic (lines 111-124) — independent re-implementation of the same idiom"}
  - {path: "src/percell4/workflows/artifacts.py", note: "module docstring claims tmp + os.replace; verify in source"}
status: pre_canonical
tags:
  - atomic-write
  - filesystem
  - hdf5
  - convention
related_components: [io, tooling]
---

# Atomic-write contract

> **Status: pre_canonical.** Two independent implementations of the same idiom (`DatasetStore.create_atomic` and `ProjectIndex._write_atomic`); should converge on a single helper.

## Convention

When writing a file whose partial state would be observable as corruption (the `.h5` dataset, `project.csv`, run-folder artifacts):

1. `tempfile.mkstemp(suffix=".tmp", dir=<target-parent>)` — same parent dir as the target so `os.replace` is atomic across no filesystem boundary.
2. Close the fd immediately; write to the path string.
3. On success: `os.replace(tmp_path, final_path)`.
4. On any exception: `os.unlink(tmp_path)` if it exists; re-raise.
5. **No platform branching.** `os.replace` is atomic on POSIX and Windows (Python 3.3+); do not introduce `if os.name == "nt": ...` branches.

## Canonical implementations

- `store.DatasetStore.create_atomic(path, build_fn)` (lines 471-498) — for new `.h5` files. Caller-supplied `build_fn(h5_file)` populates the file under the open `h5py.File(tmp_path, "w")` handle, then `os.replace` makes it visible.
- `project.ProjectIndex._write_atomic(df)` (lines 111-124) — for `project.csv`.

## Drift / re-implementation

Today these are two separate implementations of the same five-step idiom. A `percell4/io/atomic.py` (or addition to `percell4/store.py`) helper:

```python
def atomic_write(target: Path, write_fn: Callable[[Path], None]) -> None:
    """tmp file in same dir, write_fn(tmp), os.replace, cleanup on raise."""
    ...
```

would let `ProjectIndex._write_atomic` and `DatasetStore.create_atomic` share the framing.

## Important non-application

Per-operation HDF5 mutations (`DatasetStore.write_array`, `set_metadata`, `delete_item`, `append_decay_layers`) open the file in append mode and rely on h5py + per-channel `flush+fsync` (`store.py:421-431`) for crash-safety, not tmp-file replacement. The atomic-write contract applies to **whole-file replacement**, not in-place mutation of an existing `.h5`. This is a deliberate split — re-creating a 5+ GB `.h5` for every layer add would be untenable.

## Reuse rule

> Any new whole-file output (a sidecar log, a derived export, a config dump) MUST use the atomic-write helper rather than `open(path, "w")` directly. Direct writes that don't go through tmp + `os.replace` are a drift violation flagged by this audit.
