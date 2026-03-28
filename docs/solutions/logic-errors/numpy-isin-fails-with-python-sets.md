---
title: "np.isin returns all-False with Python sets in NumPy 2.x"
category: logic-errors
tags: [numpy, isin, set, list, filtering, silent-failure]
module: [gui/phasor_plot, gui/launcher, model]
date: 2026-03-28
symptom: "Phasor histogram shows 'No valid phasor data' after clicking Filter to Selection. Phasor mask doesn't restrict to filtered cells. Cell measurements include all cells instead of filtered subset. No error raised — failure is completely silent."
root_cause: "np.isin(array, python_set) returns an all-False boolean array in NumPy 2.x. Python sets are not treated as array-like by NumPy's isin implementation. The function silently returns wrong results instead of raising an error."
---

# np.isin Returns All-False with Python Sets in NumPy 2.x

## Problem

After implementing a cell filter system using `CellDataModel._filtered_ids: set[int]`, three features silently broke:

1. **Phasor histogram filtering** — showed "No valid phasor data" after applying filter (0 pixels matched)
2. **Phasor mask cell restriction** — mask covered all pixels instead of just filtered cells
3. **Measurement cell restriction** — measured all cells instead of filtered subset

No exceptions were raised. The failure was completely silent.

## Investigation

The filter system stored filtered cell IDs as a `set[int]` for O(1) membership tests (used by `pandas.DataFrame.isin()` which handles sets correctly). The set was then passed directly to `np.isin()` at three call sites:

```python
# All three returned all-False arrays
cell_mask = np.isin(self._labels_flat, filtered_ids)   # phasor histogram
cell_mask = np.isin(self._labels, filtered_ids)         # phasor mask
cell_mask = np.isin(labels, filtered_ids)                # launcher measure/particles
```

## Root Cause

`np.isin(array, test_elements)` requires `test_elements` to be array-like. In NumPy 2.x, Python `set` objects are **not** treated as array-like. Instead of raising a `TypeError`, `np.isin` silently returns an all-False boolean array.

```python
import numpy as np
arr = np.array([1, 2, 3, 179])

np.isin(arr, {179})     # array([False, False, False, False])  ← WRONG
np.isin(arr, [179])     # array([False, False, False,  True])  ← correct
np.isin(arr, (179,))    # array([False, False, False,  True])  ← correct
```

This is a NumPy 2.x behavior change. In NumPy 1.x, sets were coerced to arrays and worked correctly.

**Contrast with pandas:** `pd.Series.isin({179})` works correctly with sets. This inconsistency between pandas and numpy is what made the bug hard to spot — `model.filtered_df` (which uses `df["label"].isin(filtered_ids)`) worked fine, while the numpy call sites silently failed.

## Solution

Wrap `filtered_ids` in `list()` at every `np.isin` call site:

```python
# Before (broken in NumPy 2.x):
cell_mask = np.isin(self._labels_flat, filtered_ids)

# After (works in all NumPy versions):
cell_mask = np.isin(self._labels_flat, list(filtered_ids))
```

Three files fixed:
- `src/percell4/gui/phasor_plot.py` — 2 call sites (histogram filter + mask restriction)
- `src/percell4/gui/launcher.py` — 1 call site (`_apply_cell_filter`)

The `list()` conversion is O(k) where k is the number of filtered cells (typically <1000), which is negligible compared to the O(n) `np.isin` scan over millions of pixels.

## Why It Was Hard to Find

1. **No error raised** — `np.isin` silently returns wrong results instead of failing
2. **pandas isin works fine** — `df.isin(set)` is correct, creating a false sense of safety
3. **The fix had been correct originally** — `list()` wrappers were present, then removed during a code review cleanup as "unnecessary" since "np.isin accepts any iterable"
4. **The symptom was misleading** — "No valid phasor data" suggested a data loading issue, not a filtering bug

## Prevention

### Rule: Always pass lists or arrays to np.isin, never sets

```python
# NEVER do this:
np.isin(array, some_set)
np.isin(array, some_frozenset)

# ALWAYS do this:
np.isin(array, list(some_set))
np.isin(array, np.array(list(some_set)))
```

### When to watch for this

- Any code path where a Python `set` is passed to a NumPy function expecting array-like input
- Code review: if someone removes a `list()` wrapper around a set argument to `np.isin`, flag it
- After NumPy upgrades: test all `np.isin` call sites with both sets and lists

### Testing

```python
def test_isin_with_set():
    """Verify np.isin works with our filtered_ids type."""
    arr = np.array([1, 2, 3, 4, 5])
    filtered = {2, 4}
    result = np.isin(arr, list(filtered))
    assert result.sum() == 2
    assert result[1] and result[3]
```

## Related Documentation

- [Selection/Filtering/Multi-ROI Patterns](../ui-bugs/percell4-selection-filtering-multi-roi-patterns.md) — broader architecture doc for the filter system
- [NumPy 2.0 migration guide](https://numpy.org/devdocs/numpy_2_0_migration_guide.html) — documents array-like behavior changes
