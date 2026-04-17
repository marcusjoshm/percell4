---
title: "dtcwt fails with NumPy 2.0: asfarray and issubsctype removed"
category: build-errors
tags: [numpy, dtcwt, wavelet, compatibility, numpy2, deprecation]
module: domain/flim/wavelet_filter
symptom: "Wavelet filter error: `np.asfarray` was removed in the NumPy 2.0 release. Use `np.asarray` with a proper dtype instead. Then: `np.issubsctype` was removed in the NumPy 2.0 release. Use `np.issubdtype` instead."
root_cause: "dtcwt package (>=0.14) uses np.asfarray and np.issubsctype internally, both removed in NumPy 2.0"
severity: medium
date: 2026-04-17
---

# dtcwt Fails with NumPy 2.0: asfarray and issubsctype Removed

## Symptoms

Clicking "Apply Wavelet Filter" in the FLIM tab shows:
1. First: `Wavelet filter error: np.asfarray was removed in the NumPy 2.0 release`
2. After fixing that: `Wavelet filter error: np.issubsctype was removed in the NumPy 2.0 release`

## Root Cause

The `dtcwt` package (DTCWT wavelet transform, used for phasor denoising) internally calls two NumPy functions that were removed in NumPy 2.0:

- `np.asfarray(X)` — used in `dtcwt/utils.py:105` and `dtcwt/numpy/common.py`
- `np.issubsctype(arg1, arg2)` — used in `dtcwt/numpy/lowlevel.py` and `dtcwt/sampling.py`

The `dtcwt` package has not released a NumPy 2.0-compatible version as of 2026-04.

## Fix

Add compatibility shims at the top of the wavelet filter module, **before** `dtcwt` is imported (by any downstream code):

```python
# domain/flim/wavelet_filter.py (top of file, after numpy import)

import numpy as np

# NumPy 2.0 removed several functions that dtcwt still uses internally.
# Restore them as shims so dtcwt works with NumPy 2.x.
if not hasattr(np, "asfarray"):
    np.asfarray = lambda a, dtype=np.float64: np.asarray(a, dtype=dtype)
if not hasattr(np, "issubsctype"):
    np.issubsctype = lambda arg1, arg2: np.issubdtype(np.result_type(arg1), arg2)
```

The `hasattr` guard ensures the shim only activates on NumPy 2.x — on NumPy 1.x the original functions still exist.

## Why Monkey-Patching is Acceptable Here

- `dtcwt` is an optional dependency with no active maintainer for NumPy 2.0 compat
- The shims are exact behavioral replacements (not approximations)
- The guard (`if not hasattr`) is safe — no effect on NumPy 1.x
- The alternative (pinning NumPy <2.0) would block all other packages from upgrading

## Prevention

When upgrading NumPy to a new major version, check optional dependencies for removed API usage:

```bash
grep -r "np\.asfarray\|np\.issubsctype\|np\.bool\b\|np\.int\b\|np\.float\b\|np\.complex\b\|np\.object\b\|np\.str\b" .venv/lib/python*/site-packages/dtcwt/
```

Other NumPy 2.0 removals that may hit third-party packages: `np.bool`, `np.int`, `np.float`, `np.complex`, `np.object`, `np.str` (all aliases removed).

## Related

- [NumPy 2.0 migration guide](https://numpy.org/doc/stable/numpy_2_0_migration_guide.html)
- `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md` — another NumPy 2.x behavioral change
