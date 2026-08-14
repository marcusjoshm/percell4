---
title: GMM ROI cov_f spinbox translated the ROI instead of stretching it (anchor feedback loop)
date: 2026-05-03
category: logic-errors
module: src/percell4/domain/flim/phasor.py
problem_type: logic_error
component: tooling
symptoms:
  - "cov_f spinbox in the phasor plot's Selected-ROI panel translated the GMM ROI instead of growing/shrinking it"
  - "Each cov_f tick added the same shift vector again, walking the ROI diagonally across the phasor plot"
  - "ROI radii stayed visually unchanged when cov_f was adjusted, contradicting the parameter's documented effect"
root_cause: logic_error
resolution_type: code_fix
severity: high
related_components:
  - src/percell4/interfaces/gui/peer_views/phasor_plot.py
  - src/percell4/application/use_cases/run_phasor_gmm.py
  - tests/test_flim/test_phasor_gmm.py
tags:
  - phasor
  - gmm
  - roi
  - spinbox
  - parameter-design
  - feedback-loop
  - anchor-semantics
  - non-orthogonal-parameters
---

# GMM ROI cov_f spinbox translated the ROI instead of stretching it (anchor feedback loop)

## Problem

The GMM-based phasor ROI placement feature exposed two scalar coefficients (`cov_f` and `shift`) as spinboxes in the phasor plot's Selected-ROI panel. Editing `cov_f` was supposed to grow or shrink the ROI radii proportional to `√λ_major` / `√λ_minor`. Instead, every `cov_f` tick translated the ROI without changing its size — and the translation compounded with each subsequent edit. The bug was reported on the first manual test of the shipped feature.

## Symptoms

- User-reported regression on the Selected-ROI panel of the phasor plot. Before/after screenshots showed bumping `cov_f` from `2.0` to `2.1` with `shift = -1.5` shifting the ROI down-left, with radii visually unchanged.
- Each subsequent `cov_f` tick translated the ROI further along the major axis. Stretch-only edits had translation side effects; shift-only edits compounded across edits.
- Two parameters that the UI presented as independent were coupled in practice — turning the "stretch" knob moved the ROI; turning the "shift" knob *also* moved the ROI, but by a different amount each time.

## What Didn't Work

**1. The original "drag-preserving" design (the source of the bug).**

The slot was structured to keep a manual drag intact across spinbox edits by passing the current ROI center as an anchor:

```python
def _on_cov_f_changed(self, value: float) -> None:
    widget.phasor_roi.gmm_fit.cov_f = float(value)
    # Pass the current center as anchor so a manual drag is preserved:
    self._apply_gmm_geometry(widget, anchor=widget.phasor_roi.center)
```

```python
def gmm_to_phasor_roi_geometry(..., cov_f, shift, anchor=None):
    delta_g = shift * sqrt_major * cos_a
    delta_s = shift * sqrt_major * sin_a
    base_g, base_s = anchor if anchor is not None else mean
    center = (base_g + delta_g, base_s + delta_s)   # ← bug
```

When `anchor = phasor_roi.center`, the center already encoded the previous `shift × sqrt_major × <cos|sin>` displacement; recomputing `center = anchor + shift*…` therefore added the shift on top of itself. `sqrt_major` depends on `cov_f`, so wiggling `cov_f` re-baked an ever-growing translation into the center even though `cov_f` is supposed to affect only the radii.

**Meta-lesson** (session history): this design was *not* the original intent. The brainstorm requirements had specified clean snap-to-mean semantics — every spinbox edit recomputes `center = mean + shift × √λ × …` from scratch. During the `/ce-plan` doc-review pass, persona agents flagged snap-to-mean as a "UX surprise" (would destroy a user's manual drag), and the plan was revised to drag-preservation by substituting `phasor_roi.center` for `mean`. That revision *introduced* the feedback loop. The subsequent fix is architecturally equivalent to the original snap-to-mean design — non-orthogonal parameters in two reference frames cannot coexist no matter how cleverly the slot ordering is arranged. (session history)

**2. The narrower regression-only fix (considered, rejected).**

After seeing the screenshots, a smaller patch was contemplated: when `cov_f` changes, skip the shift application; when `shift` changes, snap to mean. This eliminates the doubling for the immediate case but leaves the underlying confusion in the model — still two parameters in two reference frames, with subtle ordering rules a future contributor would have to memorize. The user redirected toward the fuller redesign by clarifying that manual drag is **not** part of the intended feature: *"This feature is not meant for manual dragging. The purpose of using the GMM is for consistent movements based on data determined metrics."*

## Solution

Replace the scalar `(cov_f, shift)` pair with four per-axis coefficients, all measured from the cluster **mean** (a fixed data invariant), not from the current ROI center. Stretch coefficients drive radii only; shift coefficients drive center only. The two are mathematically orthogonal — changing one cannot affect the output of the other.

The center computation, before vs. after — the bug is the two lines on the right:

```python
# Before: the two buggy lines.
base_g, base_s = anchor if anchor is not None else mean
center = (base_g + delta_g, base_s + delta_s)
```

```python
# After: center is always derived from the cluster mean, never from anchor.
def gmm_to_phasor_roi_geometry(
    mean: tuple[float, float],
    lambda_major: float,
    lambda_minor: float,
    principal_angle_rad: float,
    stretch_parallel: float,
    stretch_perpendicular: float,
    shift_parallel: float,
    shift_perpendicular: float,
    shape: Literal["ellipse", "circle"],
) -> tuple[tuple[float, float], tuple[float, float], float]:
    # Parallel direction = major eigenvector; perpendicular = 90° CCW rotation.
    delta_g = (
        shift_parallel * sqrt_major * cos_a
        - shift_perpendicular * sqrt_minor * sin_a
    )
    delta_s = (
        shift_parallel * sqrt_major * sin_a
        + shift_perpendicular * sqrt_minor * cos_a
    )
    mean_g, mean_s = mean
    center = (mean_g + delta_g, mean_s + delta_s)   # always from mean, never from anchor

    if shape == "ellipse":
        radii = (stretch_parallel * sqrt_major, stretch_perpendicular * sqrt_minor)
    else:  # circle: collapses to a single radius — uses minor extent (matches reference scripts)
        radii = (stretch_perpendicular * sqrt_minor, stretch_perpendicular * sqrt_minor)
    return center, radii, angle_deg
```

The "drag-preserving anchor" pattern was removed entirely. GMM ROIs are non-draggable in the GUI (`roi.translatable = False` and resize handles stripped at construction time — see Prevention for the exact pattern). All four spinboxes (`stretch_parallel`, `stretch_perpendicular`, `shift_parallel`, `shift_perpendicular`) write to `PhasorROI.gmm_fit` and re-derive geometry from `mean` + the four scalars. There is no `anchor` parameter anywhere in the chain. The PhasorROI JSON `schema_version` bumped from 2 → 3 with backward-compat migration on load (old `cov_f` → both stretch axes; old `shift` → `shift_parallel`).

## Why This Works

The fix is two principles, applied together:

1. **Orthogonality of geometric parameters.** The transform decomposes into translation (center) and scale (radii) along two axes (parallel/perpendicular to the major eigenvector). Each spinbox now writes exactly one component of one of those, so changing parameter A cannot mathematically alter the output of parameter B. `stretch_*` appears only in the `radii` expression; `shift_*` appears only in `delta_{g,s}`. The Jacobian between sliders and observables is diagonal.

2. **Fixed reference frame.** All four parameters are measured from the cluster mean, which is a function of the data and does not change when the user edits a spinbox. Because the reference is invariant, recomputing the geometry from scratch on every edit is idempotent — applying the same parameter set twice yields the same ROI. The previous design used `phasor_roi.center` (a state-relative anchor) as the reference, so the reference itself drifted with each edit, producing cumulative error.

The "drag-preserving" feature looked like a UX win but quietly introduced a second reference frame (current ROI state) coexisting with the data-relative one. Removing drag collapses the model back to a single frame.

## Prevention

**Three lessons for future GUI/transform code:**

1. **Orthogonal parameters in geometric controls.** When a UI exposes multiple parameters that drive a transform (translation × scale × rotation, or per-axis variants), each parameter should affect exactly one aspect of the output geometry. If parameter A's slot indirectly causes parameter B's effect to re-apply, you have a feedback loop. Audit by asking, for each parameter pair (A, B): "Does setting A change the *output* of B's contribution?" If yes, the model is non-orthogonal — fix the model, not the slot ordering.

2. **Fixed reference frames.** Measure all parameters from a data invariant (cluster mean, world origin, named reference point, immutable computed value) — never from the current state of the object being transformed. State-relative anchors (`obj.center`, `obj.position`, `last_value`) seem natural for "preserve current pose" semantics but compose badly across multiple parameters and across repeated edits. They cause cumulative drift that is invisible until a user wiggles a slider.

3. **The "preserve user drag" trap.** When a feature exposes data-driven parameters (sliders, spinboxes), letting the user also drag the object directly creates two reference frames that have to coexist. Either disable the drag (`roi.translatable = False`, remove resize handles) or make the drag write back into the parameters so the parameter values stay the single source of truth. The "anchor = current center" pattern attempts neither and creates the feedback loop.

**Regression test (pins lesson 1):**

```python
def test_stretch_does_not_move_center(phasor_window_with_data):
    win = phasor_window_with_data
    geo = _make_geometry(1, mean_g=0.4, mean_s=0.3)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    # Apply non-zero shift first
    win._shift_parallel_spin.setValue(-1.5)
    center_after_shift = win._roi_widgets[0].phasor_roi.center
    # Change stretch — center MUST NOT move
    win._stretch_parallel_spin.setValue(2.1)
    center_after_stretch = win._roi_widgets[0].phasor_roi.center
    assert center_after_stretch[0] == pytest.approx(center_after_shift[0], abs=1e-9)
    assert center_after_stretch[1] == pytest.approx(center_after_shift[1], abs=1e-9)
```

**Concrete code patterns:**

- In transform helpers, accept the data invariant as a *required* positional argument (`mean`, not `anchor=None`). Defaulting to `None` and falling back to a state-relative value is a smell — it lets callers silently opt into the buggier path.
- Treat geometry helpers as pure: `(data_invariant, parameters) → geometry`. When a slot re-derives the object after a parameter change, re-derive end-to-end. Don't pass the current rendered state back in as input.
- For non-draggable Qt/pyqtgraph ROIs, the canonical disable is `roi.translatable = False` plus iterating `roi.handles` and calling `roi.removeHandle(h["item"])`. Set this at construction time, not in a slot, so it cannot be re-enabled by a refresh path.

## Related Issues

- **See also:** [`docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`](../ui-bugs/percell4-selection-filtering-multi-roi-patterns.md) — Patterns 3 (ROI identity-based lambda capture) and 5 (per-ROI mask caching). The GMM ROI inherits both; the orthogonal-coefficient redesign must preserve "invalidate `cached_mask` when any of the four coefficients change."
- **See also:** [`docs/solutions/ui-bugs/percell4-phasor-plot-axis-desync.md`](../ui-bugs/percell4-phasor-plot-axis-desync.md) — different ROI bug class (pyqtgraph coordinate-system desync) but useful as a "phasor ROI behaving visually wrong" precedent.
- **Adjacent:** [`docs/solutions/logic-errors/phasor-roi-to-mask-api-mismatch.md`](./phasor-roi-to-mask-api-mismatch.md) — companion mask-building call path for the elliptical ROI; same module, different subsystem. Reinforces the "explicit kwargs not dataclass" call convention.
- **Implementation companions** (not solutions docs): [`docs/plans/2026-05-03-001-feat-phasor-gmm-segmentation-plan.md`](../../plans/2026-05-03-001-feat-phasor-gmm-segmentation-plan.md), [`docs/brainstorms/2026-05-03-phasor-gmm-segmentation-requirements.md`](../../brainstorms/2026-05-03-phasor-gmm-segmentation-requirements.md). The plan's doc-review section captures the design pivot that introduced this bug.
- **Search hooks for future agents (natural-language phrases not in `tags`):** spinbox double-counted parameter, ROI feedback loop on stretch edit, drag-preserving anchor bug, state-relative reference frame drift, parameter coupling in Qt slot.
