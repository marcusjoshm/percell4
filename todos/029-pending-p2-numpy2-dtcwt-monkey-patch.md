---
status: pending
priority: p2
issue_id: "029"
tags: [code-review, shim, numpy, dtcwt, dependency]
dependencies: []
---

# Replace NumPy 2.0 monkey-patches with pinned dtcwt or fork

## Problem Statement

`domain/flim/wavelet_filter.py` monkey-patches `np.asfarray` and `np.issubsctype` onto the numpy module at import time. This is fragile — it modifies global state, depends on import ordering (our module must load before dtcwt), and will silently break if dtcwt changes its internal usage.

## Findings

- **Location**: `src/percell4/domain/flim/wavelet_filter.py` lines 18-22
- **Root cause**: dtcwt >=0.14 uses deprecated NumPy functions removed in NumPy 2.0
- **Impact**: Works now but is a time bomb. Any third-party code that imports dtcwt before our module runs will crash.

## Proposed Solutions

### Option A: Pin dtcwt and contribute upstream fix (Recommended)
Fork dtcwt, replace `np.asfarray` → `np.asarray(x, dtype=float)` and `np.issubsctype` → `np.issubdtype` in the fork. Pin to the fork until upstream merges. Remove our monkey-patch.
- Effort: Medium (fork + 5-line fix + PR upstream)
- Risk: Low

### Option B: Vendor the dtcwt transform code
Copy only the `Transform2d` forward/inverse we use (~200 lines) into our codebase. Remove the dtcwt dependency entirely.
- Effort: Medium
- Risk: Medium — must verify numerical equivalence

### Option C: Keep monkey-patch but move to an early init module
Create `percell4/_compat.py` imported at app startup (before anything else). Centralizes all compat shims.
- Effort: Small
- Risk: Low — but still a monkey-patch

## Acceptance Criteria

- [ ] No `np.asfarray =` or `np.issubsctype =` in the codebase
- [ ] Wavelet filter works with NumPy 2.x
- [ ] No import-order dependency
