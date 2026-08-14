---
title: "Systematic elimination of re-export shims, monkey-patches, and transitional coupling"
category: architecture-decisions
tags: [shims, re-export, monkey-patch, numpy-compat, callback-injection, tech-debt, cleanup]
module: entire codebase
symptom: "20 re-export shim files with 37 stale imports, NumPy monkey-patches in domain code, launcher coupling via transitional parameter"
root_cause: "Hex architecture refactor moved code to canonical locations but left shims for backward compatibility. Shims accumulated because no cleanup pass was scheduled."
severity: medium
date: 2026-04-17
---

# Eliminating Shims, Monkey-Patches, and Transitional Coupling

## Problem

After the hexagonal architecture refactor, 3 classes of temporary fix remained:

1. **20 re-export shim files** — each 3-9 lines of `from percell4.domain.X import *` redirecting old import paths to canonical locations. 37 files across the codebase still imported from old paths.

2. **NumPy 2.0 monkey-patches** — `np.asfarray` and `np.issubsctype` patched onto the numpy module inside `domain/flim/wavelet_filter.py`, creating an import-order dependency (our module must load before dtcwt).

3. **Transitional launcher coupling** — `GroupedSegPanel` still received `launcher=self` through `AnalysisPanel`, the last remaining `self._launcher` reference in the task panel layer.

## Solution: 4-Phase Cleanup

### Phase 1: Batch import replacement + shim deletion

Used a Python script to replace all 46 old-path imports with canonical paths across 15 files:

```python
REPLACEMENTS = {
    "percell4.measure.measurer": "percell4.domain.measure.measurer",
    "percell4.segment.cellpose": "percell4.adapters.cellpose",
    "percell4.io.importer": "percell4.adapters.importer",
    # ... 17 more
}
# Batch replace in all .py files, skip shim files themselves
```

Then deleted all 20 shim files. Verified with:
```bash
grep -rl "Re-export shim" src/percell4/  # should return nothing
lint-imports                              # 3 contracts kept, 0 broken
pytest                                    # all pass
```

### Phase 2: GroupedSegPanel callback injection

Applied the same pattern as the other 4 panels (documented in `decouple-task-panels-callback-injection.md`):

```python
# BEFORE: launcher=self threaded through AnalysisPanel
GroupedSegPanel(data_model, launcher=self._launcher_for_grouped)

# AFTER: explicit callbacks
GroupedSegPanel(
    data_model,
    get_store=self._get_store,
    get_viewer_window=self._get_viewer_window,
    show_status=self._show_status,
)
```

### Phase 3: Centralized NumPy compat

Moved monkey-patches from `domain/flim/wavelet_filter.py` (wrong layer — domain code shouldn't modify globals) to `src/percell4/_compat.py`:

```python
# _compat.py — imported once at app startup
import numpy as np

if not hasattr(np, "asfarray"):
    np.asfarray = lambda a, dtype=np.float64: np.asarray(a, dtype=dtype)
if not hasattr(np, "issubsctype"):
    np.issubsctype = lambda arg1, arg2: np.issubdtype(np.result_type(arg1), arg2)
```

Imported from `app.py` and `cli/run_pipeline.py` — the two entry points. No import-order dependency in domain code.

### Phase 4: Exception hierarchy

Created `domain/errors.py` with `NoDatasetError`, `NoSegmentationError`, `NoMaskError`, `NoChannelError`. Updated 7 use cases to raise typed exceptions instead of bare `ValueError`.

## Prevention: When to Clean Up Shims

**Rule:** Schedule a shim cleanup pass within 2 weeks of any refactor that creates shims. Don't let them accumulate.

**Detection grep:**
```bash
# Find re-export shims:
grep -rl "Re-export shim\|Backward-compatibility shim" src/percell4/

# Find monkey-patches:
grep -rn "np\.\w\+ = lambda" src/percell4/

# Find transitional coupling:
grep -rn "transitional\|launcher=" src/percell4/interfaces/gui/task_panels/
```

**Batch replacement script pattern:** Use a Python dict of `{old_path: new_path}` with `str.replace()` over all `.py` files, skipping the shim files themselves. Verify with `grep`, `lint-imports`, `pytest`.

## Related Documentation

- `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md` — the callback pattern applied in Phase 2
- `docs/solutions/build-errors/numpy2-dtcwt-removed-functions.md` — the original NumPy compat issue
- `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md` — another transitional coupling pattern
