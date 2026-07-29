---
title: "phasor_roi_to_mask takes kwargs, not a PhasorROI object"
category: logic-errors
tags: [phasor, api-mismatch, function-signature, roi]
module: domain/flim/phasor, interfaces/gui/peer_views/phasor_plot
symptom: "TypeError: phasor_roi_to_mask() missing 1 required positional argument: 'radii'"
root_cause: "New call site passed the PhasorROI dataclass object instead of its individual fields (center, radii, angle_rad)"
severity: low
date: 2026-04-17
---

# phasor_roi_to_mask Takes kwargs, Not a PhasorROI Object

## Symptom

Clicking "Apply Visible as Mask" in the phasor plot raises:
```
TypeError: phasor_roi_to_mask() missing 1 required positional argument: 'radii'
```

## Root Cause

`phasor_roi_to_mask` is a domain function with explicit parameters:

```python
def phasor_roi_to_mask(g_map, s_map, center, radii, angle_rad=0.0) -> NDArray[np.bool_]:
```

The existing call site in `_compute_combined_mask` calls it correctly:
```python
widget.cached_mask = phasor_roi_to_mask(
    g, s, center=roi.center, radii=roi.radii, angle_rad=np.radians(roi.angle_deg),
)
```

The new call site in `_on_apply_mask` (added during the per-ROI mask refactor) incorrectly passed the `PhasorROI` dataclass as the third positional arg:
```python
# WRONG: passes PhasorROI object as `center`
w.cached_mask = phasor_roi_to_mask(self._g_map, self._s_map, w.phasor_roi)
```

Python interpreted `w.phasor_roi` as the `center` argument, leaving `radii` missing.

## Fix

```python
# CORRECT: unpack ROI fields into kwargs
roi = w.phasor_roi
w.cached_mask = phasor_roi_to_mask(
    self._g_map, self._s_map,
    center=roi.center, radii=roi.radii,
    angle_rad=np.radians(roi.angle_deg),
)
```

## Also Fixed: Session Unsubscribe ValueError

`closeEvent` called `unsub()` for each Session subscription, but if the window was closed twice (hide-on-close + Qt destruction), the callback was already removed. Fixed by wrapping in `try/except ValueError: pass`.

## Prevention

When calling a domain function from a new code path, copy the call pattern from an existing call site — don't guess the API from the dataclass shape. The function signature is the contract, not the data model.

```bash
# Find existing call patterns:
grep -n "phasor_roi_to_mask" src/percell4/interfaces/gui/peer_views/phasor_plot.py
```
