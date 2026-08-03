---
title: "feat: Dilute Phase Mask Generation workflow"
type: feat
status: active
date: 2026-05-18
origin: docs/brainstorms/2026-05-18-dilute-phase-mask-requirements.md
---

# feat: Dilute Phase Mask Generation workflow

## Overview

Add a new interactive single-dataset workflow under the **Workflows**
sidebar: **Dilute phase mask generation**. The user runs `Grouped
Threshold` iteratively against an *in-memory NaN-subtracted* copy of
the active channel, accepts each round's condensed mask through the
existing `ThresholdQCController`, and on **Done** the workflow writes
one final binary mask = `(in_cell) AND NOT cumulative_dilated_condensed`
to `/masks/<user_name>`. No intermediate state persists to the
`.h5`.

This is the **first interactive single-dataset workflow** under the
Workflows tab (every existing entry is multi-dataset batch). The
plan therefore also introduces the scaffolding for that shape
(`gui/workflows/dilute_phase/`) without disturbing the multi-dataset
runner pattern in `gui/workflows/base_runner.py`.

---

## Problem Frame

See origin: `docs/brainstorms/2026-05-18-dilute-phase-mask-requirements.md`.
Briefly: a single `Grouped Threshold` pass cannot isolate the dilute
phase because the brightest condensed objects dominate the metric
distribution. The user wants an iterative peeling workflow that
progressively NaN-subtracts each round's accepted condensed mask
from a working buffer and finally writes one in-cell-domain dilute
mask. The biological dilute phase lives **inside** cells; the final
mask is bounded to the active segmentation's domain.

---

## Requirements Trace

- R1. New entry on the Workflows sidebar opens an interactive
  single-dataset workflow on the currently open dataset
  (`docs/brainstorms/2026-05-18-dilute-phase-mask-requirements.md`
  Users / Where it lives).
- R2. Configure-once setup: dilute mask name, dilation radius,
  Grouped Threshold settings; locked after Round 1
  (origin Conceptual procedure step 1).
- R3. Round N runs Grouped Threshold on a working buffer, opens
  `ThresholdQCController` for user accept/refine/reject, dilates the
  accepted mask by `dilation_radius_px`, sets those pixels to NaN
  in the working buffer, and unions into a running
  `cumulative_condensed` (origin steps 2-5).
- R4. Per-round preview: a transient scalar napari layer shows the
  NaN-subtracted buffer; a transient labels overlay shows the
  cumulative-condensed union (origin step 6 + Viewer side effects).
- R5. On **Done**, compute `dilute_mask = (in_cell) AND NOT cumulative_condensed`,
  write via `store.write_mask`, auto-select via
  `session.set_active_mask` (origin Output).
- R6. On **Cancel**, drop all in-memory state; zero `.h5` diffs
  (origin Non-functional + AE-2).
- R7. Re-entry guard: only one workflow at a time, sharing the
  existing `launcher.is_workflow_locked` primitive (origin
  Non-functional).
- R8. Mask name must be unique across `/masks/`, `/labels/`, and
  `channel_names` validated up-front
  (origin UI Setup block + learnings entry #1).
- R9. NaN subtraction semantics: NaN, not zero. Every consumer
  (σ blur, per-cell metric, per-group threshold, napari display)
  must be NaN-aware (origin NaN propagation).
- R10. Intermediate rounds write **nothing** to the `.h5` —
  no `/masks/<round_name>`, no `/groups/<round_name>`,
  no `/measurements` mutation (origin Output + Non-functional;
  upgraded from origin assumption per research finding that the
  current `write_measurements_to_store=False` flag is insufficient).

**Origin acceptance examples carried forward:**
AE-1 three-round happy path, AE-2 Cancel after rounds,
AE-3 missing prerequisites, AE-4 duplicate mask name,
AE-5 cell entirely subtracted.

---

## Scope Boundaries

- No per-round undo / step-back. Cancel-and-restart is the recovery.
- No mid-flow retuning of GT settings or dilation radius.
- No persistence of intermediate condensed masks or working buffers.
- No batch / multi-dataset version of this workflow.
- No modifications to `/intensity`.
- `view_bin > 1` is not blocked but is not a design goal; the
  workflow runs at the currently active bin and writes at that
  resolution.

---

## Context & Research

### Relevant Code and Patterns

- **Grouped Threshold panel & settings**: `src/percell4/gui/grouped_seg_panel.py`
  (settings widgets are a contiguous block in `_build_ui`, lines
  67-134; cleanly extractable).
- **ThresholdQCController**: `src/percell4/gui/threshold_qc.py`
  (constructor takes `channel_image` directly; smoothing path in
  `_init`/`_show_group_qc`/`_update_preview` calls
  `apply_gaussian_smoothing` and skimage threshold methods which
  are NOT NaN-safe; `_finalize` writes `/masks/<name>`,
  `/groups/<name>`, and calls `viewer_win.add_mask` regardless of
  `write_measurements_to_store`).
- **Multi-dataset reuse of QC**: `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py`
  (template for the constructor invocation and `on_complete` →
  `PhaseResult` plumbing).
- **Per-cell metric kernel**: `src/percell4/domain/measure/measurer.py::measure_cells`;
  builtin metrics in `src/percell4/domain/measure/metrics.py` already
  use `np.nanmean` / `np.nansum` / `np.nanstd` / etc., so per-cell
  recomputation on a NaN-containing buffer is natively NaN-aware.
- **Existing Gaussian helper**: `src/percell4/domain/measure/thresholding.py::apply_gaussian_smoothing`
  (lines 79-92, delegates to `scipy.ndimage.gaussian_filter`, NOT
  NaN-safe).
- **AcceptThreshold use case**: `src/percell4/application/use_cases/accept_threshold.py`
  (canonical Creator pattern: store.write → refresh_resource_lists
  → set_active_mask; mirror this for the dilute mask).
- **Workflows host integration**: `src/percell4/interfaces/gui/main_window.py:325-348`
  (sidebar panel) and `:1380-1418` (lock primitive
  `set_workflow_locked` / `is_workflow_locked`).
- **napari transient-layer convention**: leading-underscore names
  (e.g. `_group_preview`, `_workflow_seg_qc_image`) are universally
  treated as transient and excluded from segmentation
  classification by the launcher's three-tier sync classifier.
- **`store.write_mask`**: `src/percell4/store.py:427-450`
  (coerces to `uint8`, requires 2D).
- **Mask-naming auto-select test pattern**: `tests/test_gui/test_add_layer_write_layer_sets_active.py`
  (template for the Done-button regression test).
- **Active-segmentation labels access**: `LauncherWindow._get_active_seg_labels`
  at `src/percell4/interfaces/gui/main_window.py:972-1001` is the
  callback the panel will consume; an alternative is
  `store.read_labels(session.active_segmentation)` if the workflow
  prefers the store-side path (e.g. for the final in-cell-domain
  mask compose, which has no viewer dependency).

### Institutional Learnings

- `docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md`
  — validate the dilute mask name at the Configure-once step against
  all three flat namespaces (`list_masks`, `list_labels`,
  `channel_names`). Don't rely on `add_mask`'s late hard-block.
- `docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`
  — every transient layer uses `_` prefix; teardown must fire on
  Cancel even if iteration didn't progress; `_torn_down` flag
  guards coalesced callbacks.
- `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
  — reuse `launcher.set_workflow_locked` (acquire last on install,
  release first on teardown); install order is strict, teardown is
  the reverse with `contextlib.suppress`.
- `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
  — the existing `write_measurements_to_store=False` flag does NOT
  suppress the controller's per-round `/masks/<name>` and
  `/groups/<name>` writes. The prescribed evolution is a 3-arg
  `on_complete(success, msg, measurements_df)` callback; this plan
  extends it to also return the accepted mask.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  — working buffer is window-scoped state only: never on `Session`,
  never on `DatasetStore`, never written to a `_working` HDF5
  group. Apply identical invalidation at every mutation funnel
  (round-accept, Cancel, exception).
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  — the Done button is a **Creator**: must both write the resource
  AND auto-select via `session.set_active_mask`. Encapsulate as a
  use case (`accept_dilute_mask`) so the audit's `session.set_active_*`
  grep lands on a use case, not GUI code.
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
  — store-write before viewer add; go through `ViewerWindow.add_mask`
  (it tags `metadata[PERCELL_TYPE_KEY] = LAYER_TYPE_MASK`).
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  + retraction at `consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`
  — extract Action-shaped settings (metric, algorithm, σ);
  do NOT extract Selector-shaped state (channel/seg/mask — those
  live on `SessionWindow`).
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
  — wire every `valueChanged` / `editingFinished` /
  `currentIndexChanged` / `textChanged` to the Start button's
  enable-state; cover the signal path with at least one qtbot test
  that uses `lineedit.insert(...)` (not just `setText`).
- No prior learning exists for NaN-safe Gaussian or NaN-aware
  per-pixel thresholding in this codebase — those will be captured
  as new learnings via `/ce-compound` after this lands.

### External References

None — local patterns cover everything. Normalized-convolution
NaN-safe Gaussian is textbook and a single short helper.

---

## Key Technical Decisions

- **Subtraction semantic = NaN, not zero** (origin decision). Every
  consumer along the round path must be NaN-aware. Decided in the
  brainstorm; this plan implements the propagation in three
  surfaces: σ smoothing, per-pixel threshold, per-cell metric (the
  last is already NaN-safe via existing builtin metrics).
- **NaN-safe Gaussian via internal dispatch in `apply_gaussian_smoothing`**.
  Add a `np.isnan(image).any()` branch in
  `apply_gaussian_smoothing` (`src/percell4/domain/measure/thresholding.py`)
  that routes to a new `nan_safe_gaussian_filter` under
  `src/percell4/domain/image/gaussian.py`. Zero behavioral change
  for clean-image callers; existing single_cell workflow is
  unaffected. Rejected alternative: separate sibling function —
  requires caller-side discipline (every consumer must know whether
  their image has NaN), and ThresholdQCController is consumed in
  both clean (single_cell) and NaN (dilute) paths.
- **NaN-aware per-pixel threshold inside ThresholdQCController**.
  Filter `pixels = pixels[np.isfinite(pixels)]` before passing to
  `THRESHOLD_METHODS[...]` (Otsu / Triangle / Li); compose mask as
  `(image > threshold) & np.isfinite(image)`. Localized 2-3-line
  surgical changes; clean-image behavior is identical.
- **ThresholdQCController no-persist mode (`persist_round_outputs: bool = True`)**.
  When `False`: skip `store.write_mask`, skip
  `store.write_dataframe` (groups), skip
  `CellDataModel.set_measurements`, skip `viewer_win.add_mask`,
  skip `session.refresh_resource_lists` / `set_active_mask`. Instead,
  pass the accepted mask array to a 3-arg `on_complete(success,
  msg, mask_array)` callback (backward-compatible: 2-arg callbacks
  still supported via `inspect.signature` check or a sentinel
  default). Multi-dataset workflow keeps default `True` (no behavior
  change). The existing `write_measurements_to_store` flag is
  subsumed into this single switch in the same change; document the
  consolidation in `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
  as resolved.
- **Final-mask Creator use case**. Create `application/use_cases/accept_dilute_mask.py`
  mirroring `accept_threshold.py`. Pattern is store.write_mask →
  refresh_resource_lists → set_active_mask. This puts the
  `session.set_active_mask` call inside `application/use_cases/`,
  where the audit's I1 grep expects Creators to live.
- **Workflow surface = a plain `QObject` controller + a `QWidget` panel, NOT a `BaseWorkflowRunner` subclass**.
  `BaseWorkflowRunner` is generator-driven for multi-phase batch
  runs; this workflow has one interactive loop on one dataset. A
  controller (`DilutePhaseMaskController(QObject)`) that owns
  `(working_buffer, cumulative_condensed, locked_config, round_n)`
  is simpler and more honest. The launcher acquires
  `set_workflow_locked(True)` on open and releases on Done/Cancel.
- **Transient layer naming**: `_dilute_workflow_view` (working
  buffer scalar) and `_dilute_workflow_condensed` (cumulative
  union labels overlay). Both `_`-prefixed per convention.
- **Active segmentation source for the final compose**: read via
  `store.read_labels(session.active_segmentation)` rather than the
  viewer-layer path. Removes one viewer dependency from the
  final-write code and keeps `accept_dilute_mask` Qt-free.

---

## Open Questions

### Resolved During Planning

- **Settings-widget extraction shape** → Extract to
  `src/percell4/gui/_grouped_threshold_settings.py` as a `QWidget`
  with `current_config() -> GroupedThresholdConfig` dataclass and
  `set_enabled(bool)`. `grouped_seg_panel.py` consumes it (no
  behavior change). The 2026-05-14 Selector-consolidation
  retraction does not apply — these are Action-shaped settings,
  not Selector-shaped state.
- **`ThresholdQCController` coupling** → Confirmed it does NOT
  re-read channel/segmentation from store/session; safe to pass an
  in-memory NaN-containing buffer once NaN-aware paths land.
- **Per-cell metric recomputation per round** → Use
  `measure_cells(working_buffer, seg_labels, metrics=[locked_metric])`
  directly; builtin metrics are already NaN-safe.
- **NaN-safe Gaussian helper placement** →
  `src/percell4/domain/image/gaussian.py` in a new
  `domain/image/` package (the brainstorm assumed this package
  existed; it does not — greenfielding it is U1).
- **napari layer collision sentinel** → `_dilute_workflow_view`
  and `_dilute_workflow_condensed`; document alongside the existing
  single-cell workflow's layer names.

### Deferred to Implementation

- Exact NaN-safe Gaussian boundary handling: scipy's `gaussian_filter`
  with `mode='constant', cval=0` is the cleanest analogue;
  normalized convolution divides by a kernel-weight image computed
  from `np.isfinite(image)`. The implementer should pick the mode
  (`'constant'` vs `'nearest'` vs `'reflect'`) that matches the
  existing thresholding pipeline's perceived behavior at image
  edges and pin it via tests. Default to `'constant'` with `cval=0`.
- Exact `on_complete` signature evolution shape — whether to use
  `inspect.signature(cb).parameters` to detect 2-arg vs 3-arg or
  introduce a sentinel default. Pick whichever is shorter and
  doesn't import `inspect` if it can be avoided.
- Whether `ViewerWindow.add_image(working_buffer, name="_dilute_workflow_view", ...)`
  needs explicit `contrast_limits=` to avoid recomputation drift
  between rounds (each round changes the finite range). The
  controller may need to capture initial contrast limits from
  Round 0 and apply them across rounds; pin during implementation.

---

## Output Structure

(new directories/files only; existing-file modifications are listed
in the per-unit `**Files:**` sections)

    src/percell4/
      application/
        use_cases/
          accept_dilute_mask.py          (new — U5)
      domain/
        image/                            (new package — U1)
          __init__.py
          gaussian.py
      gui/
        _grouped_threshold_settings.py   (new — U4)
        workflows/
          dilute_phase/                   (new subpackage — U6, U7)
            __init__.py
            controller.py
            panel.py
    tests/
      test_application/
        test_accept_dilute_mask.py        (new — U5)
      test_domain/
        test_nan_safe_gaussian.py         (new — U1)
        test_apply_gaussian_smoothing_nan_dispatch.py  (new — U2)
      test_gui/
        test_grouped_threshold_settings_widget.py      (new — U4)
        test_threshold_qc_nan_aware.py                  (new — U2 + U3)
        test_threshold_qc_persist_round_outputs.py      (new — U3)
        test_dilute_phase_panel.py                      (new — U7)
        test_dilute_phase_controller.py                 (new — U6)
        test_dilute_phase_workflow_sidebar.py           (new — U8)

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
                          ┌────────────────────────────────────────────┐
                          │ LauncherWindow ─ Workflows sidebar         │
                          │   [Single-cell thresholding analysis]      │
                          │   [Dilute phase mask generation] ◀ U8 add  │
                          └─────────────────┬──────────────────────────┘
                                            │ click → check is_workflow_locked
                                            ▼                  ▲
                          ┌────────────────────────────────────┴─────┐
                          │ DilutePhaseMaskPanel (Qt widget)         │  ── U7
                          │   Setup block (locked after Round 1)      │
                          │   ┌ GroupedThresholdSettingsWidget ─ U4 ┐ │
                          │   │   metric / algo / GMM/KMeans / σ    │ │
                          │   └─────────────────────────────────────┘ │
                          │   Iteration block                          │
                          │   [Run another round] [Done] [Cancel]      │
                          └─────────────────┬──────────────────────────┘
                                            │ drives
                                            ▼
                          ┌────────────────────────────────────────────┐
                          │ DilutePhaseMaskController (QObject) ─ U6   │
                          │   working_buffer (NaN propagating)         │
                          │   cumulative_condensed (bool union)        │
                          │   locked_config, round_n                   │
                          │                                            │
                          │   For each round:                          │
                          │     1. measure_cells(buffer, seg_labels)   │
                          │     2. Worker(group_cells_gmm/kmeans, …)   │
                          │     3. ThresholdQCController(              │
                          │          channel_image=buffer,             │
                          │          persist_round_outputs=False, ─ U3 │
                          │          on_complete=_round_done)          │
                          │     4. accepted_mask ← on_complete cb      │
                          │     5. dilate(accepted_mask, radius)       │
                          │     6. buffer[dilated] = NaN               │
                          │     7. cumulative |= dilated               │
                          │     8. refresh _dilute_workflow_* layers   │
                          │                                            │
                          │   On Done:                                 │
                          │     dilute = in_cell & ~cumulative          │
                          │     AcceptDiluteMask.execute(name, dilute) │
                          │   On Cancel: drop all state, no .h5 diffs  │
                          └─────────────────┬──────────────────────────┘
                                            │
                                            ▼
                          ┌────────────────────────────────────────────┐
                          │ AcceptDiluteMask use case ─ U5             │
                          │   store.write_mask(name, dilute_uint8)     │
                          │   session.refresh_resource_lists(masks=…)  │
                          │   session.set_active_mask(name)            │
                          └────────────────────────────────────────────┘

   Cross-cutting NaN-safety (U1 + U2 + U3):
     domain/image/gaussian.py: nan_safe_gaussian_filter(img, σ)
       └─ used by domain/measure/thresholding.apply_gaussian_smoothing
            (internal dispatch: branches on np.isnan(img).any())
              └─ called from threshold_qc.ThresholdQCController._init
     threshold_qc per-group threshold: filter to np.isfinite before
       skimage threshold methods; mask compose ANDs np.isfinite(img)
```

---

## Implementation Units

- U1. **NaN-safe Gaussian helper (`domain/image/gaussian.py`)**

**Goal:** Provide a NaN-aware `nan_safe_gaussian_filter(image, sigma)`
helper using normalized convolution so downstream consumers can
smooth images that contain NaN holes without poisoning every pixel
within `~3σ` of a hole.

**Requirements:** R9.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/domain/image/__init__.py`
- Create: `src/percell4/domain/image/gaussian.py`
- Test: `tests/test_domain/test_nan_safe_gaussian.py`

**Approach:**
- Function signature: `nan_safe_gaussian_filter(image: NDArray[np.floating], sigma: float, *, mode: str = "constant", cval: float = 0.0) -> NDArray[np.float32]`.
- Implementation: normalized convolution.
  Replace NaN with 0 in a copy; build a boolean `finite_mask`
  from `np.isfinite(image)`; convolve both with the same Gaussian
  kernel (via `scipy.ndimage.gaussian_filter` on each); divide
  the smoothed-with-zero image by the smoothed mask, suppressing
  division warnings; re-introduce NaN where the smoothed mask
  weight is zero (no finite neighbor in kernel footprint).
- Preserve dtype to `float32` to match
  `apply_gaussian_smoothing`'s existing contract.
- Handle the all-NaN input degenerate case: return an all-NaN
  array of the same shape.

**Execution note:** Implement test-first; the NaN-equivalence
properties are easy to specify before the implementation lands.

**Patterns to follow:** existing `apply_gaussian_smoothing` in
`src/percell4/domain/measure/thresholding.py` for signature shape
and dtype handling. No other prior art in the codebase.

**Test scenarios:**
- Happy path: clean image (no NaN). Output equals
  `scipy.ndimage.gaussian_filter(image.astype(np.float32), sigma)`
  to within a small tolerance (the normalized variant is
  mathematically equivalent on a fully-finite input).
- Happy path: image with isolated NaN pixels. Output is finite at
  every pixel whose kernel footprint includes at least one finite
  neighbor; NaN at pixels whose footprint is all NaN.
- Edge case: image where one half is NaN and the other half is
  finite. Output along the boundary smoothly blends only the
  finite half; no NaN bleed beyond pixels whose entire footprint
  is NaN.
- Edge case: σ = 0. Output equals the input (NaN preserved
  exactly).
- Edge case: all-NaN input. Output is all-NaN, no warnings raised.
- Edge case: 2D shape `(8, 8)` and a larger `(128, 128)` shape;
  both succeed and have the expected dtype (`float32`).

**Verification:** Tests pass. `nan_safe_gaussian_filter` is
importable from `percell4.domain.image`.

---

- U2. **NaN-aware dispatch inside `apply_gaussian_smoothing`**

**Goal:** Make the existing
`percell4.domain.measure.thresholding.apply_gaussian_smoothing`
NaN-safe via internal dispatch on `np.isnan(image).any()`.
Clean-image consumers keep the existing scipy fast path; NaN-image
consumers (the dilute workflow) get the U1 helper transparently.

**Requirements:** R9. Indirectly enables R3 and R4.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/domain/measure/thresholding.py`
- Test: `tests/test_domain/test_apply_gaussian_smoothing_nan_dispatch.py`

**Approach:**
- In `apply_gaussian_smoothing(image, sigma)`, check
  `np.isnan(image).any()`. If false, behave exactly as today
  (`scipy.ndimage.gaussian_filter` direct call, no copy). If true,
  delegate to `nan_safe_gaussian_filter` from U1.
- Document the dispatch in the function docstring (one line) — a
  cheap concession to the reader.
- The check is `O(n)` but in practice runs once per round per
  workflow; the workflow's image sizes are routine.

**Execution note:** Test-first; the dispatch test is two lines.

**Patterns to follow:** existing structure of
`apply_gaussian_smoothing` — keep the function shape (single
positional `image`, single positional `sigma`, float32 return).

**Test scenarios:**
- Happy path: clean image input. Output bit-for-bit (or
  `np.allclose` at machine precision) equals direct
  `scipy.ndimage.gaussian_filter`. Pin via a recorded reference
  value to guard against silent behavior drift in scipy.
- Happy path: NaN-containing image input. Output equals
  `nan_safe_gaussian_filter` output (test by patching both helpers
  and verifying which got called, OR by structural equivalence).
- Edge case: σ = 0 on a NaN image; output preserves NaN.

**Verification:** Tests pass. Existing thresholding tests still
pass (`pytest tests/test_domain/test_thresholding.py` if present;
otherwise the threshold-related test files).

---

- U3. **ThresholdQCController: NaN-aware paths + no-persist mode**

**Goal:** Make `ThresholdQCController` (a) work correctly when
`channel_image` contains NaN, and (b) support a no-persist mode
that returns the accepted mask via `on_complete` instead of
writing to the store, viewer, or session.

**Requirements:** R3, R9, R10. Enables R6 by making intermediate
rounds truly stateless.

**Dependencies:** U2 (for the smoothing path).

**Files:**
- Modify: `src/percell4/gui/threshold_qc.py`
- Test: `tests/test_gui/test_threshold_qc_nan_aware.py`
- Test: `tests/test_gui/test_threshold_qc_persist_round_outputs.py`

**Approach:**
- **NaN awareness** (small surgical changes):
  - `_show_group_qc` and `_update_preview`: before calling
    `THRESHOLD_METHODS[method](pixels)`, filter
    `pixels = pixels[np.isfinite(pixels)]`. If the result is empty,
    fall through with a sentinel ("no finite pixels in group")
    rather than crash.
  - When applying the per-pixel threshold to produce the per-group
    mask, compose as
    `(image > threshold) & np.isfinite(image)` so NaN pixels are
    never included in the mask.
  - `_update_stats_display`: `nanmean` / `nanmax` for any inline
    statistics that read pixel arrays directly.
- **No-persist mode**:
  - New parameter `persist_round_outputs: bool = True` on
    `__init__`. The existing `write_measurements_to_store`
    parameter is subsumed; in this change, deprecate it with a
    `DeprecationWarning` that emits when callers pass it
    explicitly, and route `write_measurements_to_store=False`
    semantics into `persist_round_outputs=False`. The multi-dataset
    workflow at
    `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py`
    updates to the new flag in the same commit.
  - When `persist_round_outputs=False`, `_finalize` skips:
    `store.write_mask`, `store.write_dataframe` for the groups
    DataFrame, `CellDataModel.set_measurements`,
    `viewer_win.add_mask`, `session.refresh_resource_lists`,
    `session.set_active_mask`. Cleanup of QC-private layers
    (`_group_preview`, `_group_image`, `_group_threshold_preview`,
    `_group_roi`) still runs.
  - `on_complete` signature evolves from
    `Callable[[bool, str], None]` to
    `Callable[[bool, str, np.ndarray | None], None]`. Backward
    compatibility: detect the callback's parameter count once at
    construction (via `inspect.signature(cb).parameters` or a
    sentinel default) and call accordingly. Existing 2-arg callers
    in the codebase
    (`gui/workflows/single_cell/threshold_qc_queue.py:180-199`,
    `gui/grouped_seg_panel.py:_on_qc_complete`) continue to work
    unchanged.
- Update the canonical-source / tech-debt doc
  `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
  in a follow-up `/ce-compound-refresh` (out of scope for this
  unit; flagged in Handoff).

**Execution note:** Test-first for the no-persist mode (easy to
specify); characterization-first for the NaN paths (assert current
crash behavior in a test, then implement the fix and flip the
assertion).

**Patterns to follow:** Existing `_finalize` branch structure in
`threshold_qc.py:733-741`. Existing two-callback shape conversion
patterns elsewhere (none directly, but introspection-on-callable is
straightforward).

**Test scenarios:**
- **NaN aware**:
  - Happy path: ThresholdQCController constructed with a clean
    `channel_image` produces a non-empty mask after Otsu (unchanged
    from today).
  - Happy path: ThresholdQCController constructed with a
    NaN-containing `channel_image` (half the image NaN) does not
    raise on init or QC; pixels-in-mask are all from the finite
    half.
  - Edge case: a group whose cells fall entirely in the NaN region
    yields an empty mask without raising; status string indicates
    no finite pixels.
  - Covers AE-5.
- **No-persist mode**:
  - Happy path: `persist_round_outputs=False` + accept the round.
    `on_complete` is called with `(True, <msg>, mask_array)`. The
    `DatasetStore` mock receives zero write calls. The viewer mock
    receives zero `add_mask` calls. `Session.set_active_mask` is
    not called.
  - Happy path: `persist_round_outputs=True` (default).
    `on_complete(True, <msg>, mask_array)` AND store/viewer/session
    are all called (existing behavior preserved). 3-arg callback
    receives the mask.
  - Backward compat: 2-arg `on_complete` (legacy single_cell
    workflow shape) is invoked correctly under both flag values;
    no exception about argument count.
  - Edge case: user rejects the round (cancels QC). `on_complete`
    is called with `(False, <msg>, None)` — no mask. No store
    writes regardless of flag.
  - Edge case: `write_measurements_to_store=False` (legacy flag)
    still raises a `DeprecationWarning` but maps to
    `persist_round_outputs=False`.

**Verification:** New tests pass; pre-existing
`tests/test_gui/test_threshold_qc*.py` (whatever's there today)
still pass.

---

- U4. **Extract `GroupedThresholdSettingsWidget`**

**Goal:** Pull the metric / algorithm / GMM-or-Kmeans-options /
σ widget block out of `grouped_seg_panel.py` into a reusable
`QWidget` so both the existing panel and the new dilute-phase
panel render an identical settings surface.

**Requirements:** R2.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/gui/_grouped_threshold_settings.py`
- Modify: `src/percell4/gui/grouped_seg_panel.py`
- Test: `tests/test_gui/test_grouped_threshold_settings_widget.py`

**Approach:**
- New widget class `GroupedThresholdSettingsWidget(QWidget)` that
  owns the five widgets from `grouped_seg_panel.py:67-134` plus
  `_on_algorithm_changed`. Initial values come from constructor
  kwargs (defaults match today's defaults).
- Public API:
  - `current_config() -> GroupedThresholdConfig` returning a
    frozen dataclass with `metric: str`, `algorithm: str` (`"GMM"`
    or `"K-means"`), `gmm_criterion: str`, `gmm_max_components: int`,
    `kmeans_n_clusters: int`, `sigma: float`.
  - `set_enabled(enabled: bool)` enabling/disabling every contained
    widget without changing visibility.
  - A Qt signal `config_changed = Signal()` that fires when any
    child widget emits its respective `*Changed` signal — for
    consumers that need to react to setting edits
    (the dilute-phase panel's Start-button enable state).
- `grouped_seg_panel.py` is refactored to instantiate the widget,
  `addWidget` it into the existing layout, and read
  `settings_widget.current_config()` instead of poking
  `self._metric_combo.currentText()` etc. The Run button and
  status label remain in `grouped_seg_panel.py`. Behavior must be
  bit-identical from the user's perspective.

**Patterns to follow:** existing private widget convention
(`src/percell4/gui/_stitching_flim_form.py`, `_dialog_utils.py`).
Use `@dataclass(frozen=True)` for the config object.

**Test scenarios:**
- Happy path: construct widget with defaults; `current_config()`
  returns the expected default `GroupedThresholdConfig`.
- Happy path: programmatically change metric / algo / σ; next
  `current_config()` reflects the change.
- Happy path: switching algorithm to K-means hides GMM options
  group and reveals K-means options group; `current_config()`
  reports `algorithm="K-means"` with the K-means cluster count.
- Edge case: `config_changed` fires once per user edit (use
  `qtbot.waitSignal` with `timeout=200`).
- Edge case: `set_enabled(False)` disables every child widget;
  `set_enabled(True)` re-enables them.
- Integration: instantiate `GroupedSegPanel` (the existing one);
  drive its `_on_run` flow with a mocked data_model and assert
  the panel still reads the right values from the new widget
  (regression test for the refactor).

**Verification:** Pre-existing `tests/test_gui/test_grouped_seg_panel*.py`
all pass without modification (or with modifications that purely
update mock paths, not behavior). New widget tests pass.

---

- U5. **`AcceptDiluteMask` use case**

**Goal:** A Qt-free use case that writes the final dilute-phase
mask to `/masks/<name>` and auto-selects it on the session,
mirroring the canonical Creator pattern from `accept_threshold.py`.

**Requirements:** R5.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/application/use_cases/accept_dilute_mask.py`
- Test: `tests/test_application/test_accept_dilute_mask.py`

**Approach:**
- Class `AcceptDiluteMask` with `__init__(self, *, repo: DatasetRepository, session: Session)`
  (mirror existing use cases — they take ports, not concrete
  classes; check `accept_threshold.py` for the exact protocol
  surface).
- Method `execute(self, handle: DatasetHandle, mask_name: str, mask: NDArray[np.bool_]) -> None`:
  1. Validate input shape is 2D and dtype is boolean.
  2. `self._repo.write_mask(handle, mask_name, mask.astype(np.uint8))`.
  3. `self._session.refresh_resource_lists(mask_names=self._repo.list_masks(handle))`.
  4. `self._session.set_active_mask(mask_name)`.

**Execution note:** Test-first. The use case is small enough that
its tests double as its specification.

**Patterns to follow:** `src/percell4/application/use_cases/accept_threshold.py`
(constructor signature, port wiring, store-before-session call
order).

**Test scenarios:**
- Covers AE-1. Happy path: `execute(handle, "dilute_v1", bool_array)`
  results in `/masks/dilute_v1` on disk with `uint8` dtype, the
  session emits `ACTIVE_MASK_CHANGED`, and `session.active_mask`
  equals `"dilute_v1"`.
- Happy path: `refresh_resource_lists` is called before
  `set_active_mask` (assert via call-order on a `MagicMock`
  session).
- Edge case: non-2D input raises `ValueError` before any store
  write.
- Edge case: non-boolean (e.g. `int32`) input raises `ValueError`
  before any store write.
- Integration: `execute` runs end-to-end against a real
  `Hdf5DatasetRepository` over a `tmp_path` `.h5` and a real
  `Session`; subscribers to `Event.ACTIVE_MASK_CHANGED` fire
  exactly once.

**Verification:** Tests pass. The grep audit
`grep -rn "session.set_active_" src/percell4/application/use_cases/`
finds the new file alongside `accept_threshold.py`.

---

- U6. **`DilutePhaseMaskController` (workflow orchestration)**

**Goal:** Qt `QObject` controller that owns the round state
machine: working buffer, cumulative-condensed union, locked
config, round counter. Drives the per-round Worker + QC, applies
dilation and NaN subtraction, refreshes transient napari layers,
and on Done/Cancel commits or discards.

**Requirements:** R3, R4, R5, R6, R7.

**Dependencies:** U1, U2, U3, U5.

**Files:**
- Create: `src/percell4/gui/workflows/dilute_phase/__init__.py`
- Create: `src/percell4/gui/workflows/dilute_phase/controller.py`
- Test: `tests/test_gui/test_dilute_phase_controller.py`

**Approach:**
- `DilutePhaseMaskController(QObject)` constructor takes:
  - `viewer_win`, `data_model`, `store`, `session`,
  - `channel_image: NDArray[np.float32]` (initial working buffer
    seed, captured at Start to avoid mid-flow drift),
  - `seg_labels: NDArray[np.int32]` (the active-segmentation
    labels, captured at Start),
  - `locked_config: GroupedThresholdConfig`,
  - `dilation_radius_px: int`,
  - `final_mask_name: str`.
- Signals: `round_complete = Signal(int)`, `workflow_done = Signal()`,
  `workflow_cancelled = Signal()`, `error = Signal(str)`.
- State (private):
  - `_working_buffer: NDArray[np.float32]` — starts as
    `channel_image.copy().astype(np.float32)`.
  - `_cumulative_condensed: NDArray[np.bool_]` — starts all False.
  - `_round_n: int = 0`.
  - `_round_qc_controller: ThresholdQCController | None` for the
    currently active round.
  - `_grouping_worker: Worker | None`.
  - `_torn_down: bool = False` (per the modal-tool pattern).
- Methods:
  - `start_round()`: increments `_round_n`, kicks off the
    per-round pipeline:
    1. `df = measure_cells(self._working_buffer, self._seg_labels, metrics=[self._locked_config.metric])`.
    2. Build `values` and `cell_labels` arrays from the recomputed
       column (drop NaN rows automatically).
    3. Start a `Worker` for `group_cells_gmm` or `group_cells_kmeans`
       (per `locked_config.algorithm`); on `finished`, proceed.
    4. Instantiate `ThresholdQCController(
         channel_image=self._working_buffer,
         seg_labels=self._seg_labels,
         persist_round_outputs=False,
         on_complete=self._on_round_qc_complete, ...)`.
  - `_on_round_qc_complete(success, msg, mask_or_none)`:
    - If `success=False` or `mask_or_none is None`: emit
      `round_complete(self._round_n)` but DO NOT mutate buffer or
      cumulative (the round was effectively a no-op).
    - Else:
      - `dilated = scipy.ndimage.binary_dilation(mask, structure=disk(self._dilation_radius_px))`.
      - `self._working_buffer[dilated] = np.nan`.
      - `self._cumulative_condensed |= dilated`.
      - Refresh transient napari layers
        (`_dilute_workflow_view`, `_dilute_workflow_condensed`).
      - Emit `round_complete(self._round_n)`.
  - `finish()`: compute
    `in_cell = self._seg_labels > 0`,
    `dilute = in_cell & ~self._cumulative_condensed`,
    invoke `AcceptDiluteMask.execute(...)`, teardown transient
    layers, emit `workflow_done`.
  - `cancel()`: teardown transient layers, drop in-memory state,
    emit `workflow_cancelled`. NO store writes.
  - `_teardown()`: idempotent (`_torn_down` flag); remove transient
    napari layers via `contextlib.suppress(Exception)`; release any
    in-flight `_grouping_worker` reference; clear
    `_round_qc_controller`.
- Disk structuring element: `scipy.ndimage.generate_binary_structure`
  rejected — use `skimage.morphology.disk(radius)` for a true disk
  (rotationally symmetric) since particles are roughly isotropic.
- Transient layers updated through `ViewerWindow.add_image` /
  `add_labels` helpers (they handle name collisions by replacing).
  Provide `contrast_limits` captured from Round 0 to avoid
  per-round drift (see deferred question).

**Execution note:** Test-first for the state-machine
transitions; the controller is the riskiest unit and easy to spec
with a fake viewer + fake store.

**Patterns to follow:**
- `src/percell4/gui/threshold_qc.py:_cleanup_all` and `_torn_down`
  flag from
  `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`.
- `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py`
  for `ThresholdQCController` invocation and `on_complete` plumbing
  (now in 3-arg shape).
- `src/percell4/gui/workers.py::Worker` for the GMM/K-means worker
  pattern (signal-driven; do not block the main thread).
- Existing
  `src/percell4/interfaces/gui/peer_views/session_window.py` for
  the layer-cleanup-on-teardown pattern using
  `contextlib.suppress`.

**Test scenarios:**
- Covers AE-1. Happy path (3 rounds, accept each, Done): real
  fake-viewer + fake-store fixture; verify after Done the only
  `.h5` write was to `/masks/dilute_v1`, `session.active_mask ==
  "dilute_v1"`, and transient layers are gone.
- Covers AE-2. Happy path (2 rounds, Cancel): no `/masks/*` writes
  occurred at any point; `session.active_mask` unchanged from its
  pre-start value; transient layers gone.
- Covers AE-5. Edge case (cell entirely subtracted): after round 2,
  manipulate the fake seg_labels + working buffer so that one
  cell's pixels are all NaN. Round 3's `measure_cells` call drops
  that cell's row; the round proceeds without raising; the
  cell contributes zero pixels to the final dilute mask.
- Edge case: user rejects a round's QC (`success=False`). The
  working buffer is unchanged; `_round_n` still increments;
  `_cumulative_condensed` is unchanged; user can run another round
  with the same buffer.
- Edge case: `cancel()` called while a `Worker` is mid-flight.
  Teardown does not deadlock and does not raise; worker reference
  is cleared.
- Edge case: `_teardown()` called twice. Second call is a no-op.
- Integration: real `ThresholdQCController` (no mock) in
  `persist_round_outputs=False` mode wired to the controller; one
  end-to-end round produces a correctly-dilated NaN-subtracted
  buffer.
- Error path: per-cell metric returns all-NaN (every cell's pixels
  are NaN). `start_round()` emits `error(...)` with an informative
  message; no crash.

**Verification:** Tests pass. The controller can be exercised
without `napari.Viewer.show()` thanks to the
`viewer_win.add_image` / `add_labels` mocks.

---

- U7. **`DilutePhaseMaskPanel` (Qt panel UI)**

**Goal:** The user-facing Qt widget that hosts the setup block
(name lineedit + dilation spinbox + settings widget + Start
button) and the iteration block (round counter + Run / Done /
Cancel buttons). Drives the controller (U6).

**Requirements:** R1, R2, R8.

**Dependencies:** U4, U6.

**Files:**
- Create: `src/percell4/gui/workflows/dilute_phase/panel.py`
- Test: `tests/test_gui/test_dilute_phase_panel.py`

**Approach:**
- `DilutePhaseMaskPanel(QWidget)` constructor takes
  `(parent, store, data_model, session, viewer_win,
    get_active_seg_labels: Callable[[], NDArray[np.int32] | None])`.
- UI structure:
  - Setup block (QGroupBox or vertical layout):
    - Read-out fields (disabled `QLabel`s):
      Active dataset path, active channel name, active segmentation
      name. Refresh on `Event.ACTIVE_*_CHANGED` while setup is
      live; freeze once Round 1 starts.
    - `QLineEdit` for mask name. Default text `"dilute_phase"`.
    - `QSpinBox` for dilation radius (range 0..50, default 5).
    - `GroupedThresholdSettingsWidget` from U4.
    - `QPushButton` "Start".
  - Iteration block (initially hidden):
    - `QLabel` round counter / status.
    - `QPushButton` "Run another round".
    - `QPushButton` "Done — Save dilute phase mask".
    - `QPushButton` "Cancel".
- Validation (drives Start enable-state):
  - `session.active_channel`, `session.active_segmentation`,
    `session.active_dataset` all non-None.
  - Mask name non-empty and unique across `store.list_masks()`,
    `store.list_labels()`, and `session.dataset.metadata["channel_names"]`
    (the three-namespace check per learnings entry #1).
  - Settings widget reports a valid config (always true given
    spinbox ranges).
- Wire every `*Changed` / `editingFinished` / `textChanged` signal
  on settings, name, and dilation widgets to a single
  `_revalidate()` slot that re-runs the check and updates Start's
  enable state.
- On Start: instantiate `DilutePhaseMaskController` with the
  captured snapshot of `(channel_image, seg_labels, locked_config,
  dilation_radius, mask_name)`. Collapse the setup block (disable),
  show the iteration block, kick off Round 1.
- "Run another round" → `controller.start_round()`.
- "Done" → `controller.finish()`.
- "Cancel" → `controller.cancel()`.
- The panel listens for `controller.round_complete`,
  `controller.workflow_done`, `controller.workflow_cancelled`,
  `controller.error` and updates the iteration block accordingly.

**Patterns to follow:**
- `src/percell4/gui/add_layer_dialog.py` for name-validation +
  enable-state wiring.
- `src/percell4/gui/grouped_seg_panel.py` for the layout idiom
  and read-out fields.
- `src/percell4/gui/workflows/single_cell/config_dialog.py` for
  workflow-config dialog scaffolding (only loosely — this is a
  panel, not a modal dialog).

**Test scenarios:**
- Covers AE-3. Happy path: all prerequisites set, valid name,
  Start button enabled.
- Covers AE-3. Edge case: `session.active_segmentation = None`.
  Start disabled. Inline status reads
  `"Select an active segmentation in the Session window."`.
- Covers AE-4. Edge case: `/masks/dilute_v1` already exists in
  store. Enter `"dilute_v1"` as name. Start disabled. Inline error
  visible, suggested fallback `"dilute_v1_2"` rendered nearby.
- Edge case: name collides with a `/labels/<name>` entry. Start
  disabled.
- Edge case: name collides with a channel name. Start disabled.
- Happy path: user types `"dilute_v1"` via `qtbot.keyClicks` (not
  `setText`); revalidation fires; Start enabled.
- Happy path: clicking Start collapses the setup block (every
  widget under setup is disabled) and reveals the iteration block.
- Happy path: clicking "Run another round" calls
  `controller.start_round()` exactly once.
- Happy path: clicking "Done" calls `controller.finish()` exactly
  once.
- Happy path: clicking "Cancel" calls `controller.cancel()` exactly
  once.

**Verification:** Tests pass. Panel renders without errors against
a fake viewer.

---

- U8. **Workflows sidebar entry + launcher integration**

**Goal:** Surface the workflow in the Workflows sidebar tab. Lock
the launcher on open via the existing
`launcher.set_workflow_locked(True)` primitive; unlock on Done /
Cancel / error.

**Requirements:** R1, R7.

**Dependencies:** U7.

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Test: `tests/test_gui/test_dilute_phase_workflow_sidebar.py`

**Approach:**
- `_create_workflows_panel`: add a second `QPushButton`
  ("Dilute phase mask generation") between the existing
  single-cell button and the `addStretch()` call, with a tooltip
  describing the workflow.
- New handler `_on_open_dilute_phase_workflow`:
  1. Re-entry guard: if `self.is_workflow_locked`, show status
     message and return.
  2. Build the panel via `DilutePhaseMaskPanel(parent=...)`,
     passing the launcher's `_get_active_seg_labels` callback.
  3. Open the panel in a `QDialog` wrapper (modeless) OR push it
     into the sidebar stacked widget — DEFERRED to implementation
     based on whichever fits the launcher's existing
     stacked-widget pattern best. The current single-cell entry
     uses a `QDialog`; mirror that for consistency.
  4. Hold a strong reference on the launcher
     (`self._active_dilute_workflow_panel = panel`) to prevent
     premature GC of the panel and its controller.
  5. Call `self.set_workflow_locked(True)` AFTER successful panel
     construction.
  6. Wire `panel.workflow_done` / `panel.workflow_cancelled` to
     a `_on_dilute_workflow_finished` method that:
     - Calls `self.set_workflow_locked(False)`.
     - Clears `self._active_dilute_workflow_panel = None`.
     - Closes the panel.

**Patterns to follow:**
- `_on_open_single_cell_workflow` (`main_window.py:350-457`) for
  the dialog + reference-holding + lock acquire/release pattern.
- `napari-modal-tool-overlay-pattern` doc for the strict
  install/teardown ordering.

**Test scenarios:**
- Happy path: click the sidebar button; panel opens; launcher is
  locked.
- Happy path: panel emits `workflow_done`; launcher is unlocked;
  panel reference cleared.
- Happy path: panel emits `workflow_cancelled`; launcher is
  unlocked.
- Edge case: click sidebar button while already locked. No second
  panel constructed; status message indicates a workflow is
  running.
- Edge case: panel emits `error`. Launcher is unlocked; panel
  reference cleared; status bar shows the error message.
- Edge case: window close while workflow open. Cancel is invoked,
  state is dropped, no `.h5` diffs.

**Verification:** Tests pass. The Workflows sidebar shows two
entries. Re-opening the workflow after a complete Done cycle
works.

---

## System-Wide Impact

- **Interaction graph:**
  - `apply_gaussian_smoothing` (`thresholding.py`) is consumed by
    `ThresholdQCController.__init__` AND
    `ThresholdQCController._update_preview`. Both paths inherit the
    NaN-aware dispatch via U2 transparently.
  - `ThresholdQCController` is consumed by
    `gui/workflows/single_cell/threshold_qc_queue.py` AND the new
    dilute-phase controller. U3's no-persist mode is opt-in
    (default keeps existing single-cell behavior).
  - `Session.set_active_mask` continues to be the sole mutator for
    `active_mask` — `AcceptDiluteMask` calls it; no new mutator
    introduced.
- **Error propagation:**
  - Round failures (e.g. all-NaN per-cell metric column) emit
    `controller.error(msg)`. The panel surfaces it as an inline
    status; the controller leaves the working buffer and
    cumulative-condensed unchanged so the user can adjust
    perspective and retry.
  - `accept_dilute_mask.execute` lets `ValueError` from bad input
    shape/dtype propagate; the panel catches it, surfaces the
    error to status bar, and DOES NOT trigger teardown — Cancel
    remains the explicit exit.
- **State lifecycle risks:**
  - Working buffer is `QObject`-scoped on the controller. Three
    mutation funnels: `_on_round_qc_complete` (round accept),
    `_teardown` (cancel/exception), `finish` (Done). All three
    funnels release the buffer; `_torn_down` flag is idempotent.
  - `cumulative_condensed` lives next to the buffer; same lifecycle.
  - No partial-write risk on `/masks/<final>`: the only write is at
    the end via the use case in a single transaction.
- **API surface parity:**
  - `ThresholdQCController` callback signature evolves to 3-arg.
    Backward compat preserved (detect arity).
  - `write_measurements_to_store` parameter emits
    `DeprecationWarning` if passed explicitly; subsumed by
    `persist_round_outputs`. Document in the threshold-qc tech-debt
    learning during the `/ce-compound-refresh` follow-up.
- **Integration coverage:**
  - End-to-end controller test (U6) uses a real
    `ThresholdQCController` in no-persist mode + fake viewer/store
    — proves the U3 contract holds in practice.
  - End-to-end use-case test (U5) round-trips through a real
    `Hdf5DatasetRepository` over `tmp_path` — proves the Creator
    pattern.
- **Unchanged invariants:**
  - `/intensity` is never mutated. Verified by U6's AE-2 test
    (Cancel after rounds: zero `.h5` writes).
  - Existing single-cell workflow's per-round writes still happen
    (default `persist_round_outputs=True`).
  - The launcher's `set_workflow_locked` semantics are unchanged;
    this plan adds a second consumer.
  - `grouped_seg_panel.py` user-visible behavior unchanged after
    U4 (regression test in U4's integration scenario).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| NaN-safe Gaussian boundary handling diverges from scipy's default and silently shifts the existing single-cell workflow's per-round mask shapes. | U2's clean-image fast path bypasses the new helper entirely. NaN-image dispatch is only triggered by the dilute workflow. Pinned via the "bit-for-bit clean-image" test in U2. |
| `ThresholdQCController` callback-arity backward-compatibility branch silently breaks an obscure 2-arg consumer. | Grep for all `on_complete=` callsites in the codebase (currently 2: `threshold_qc_queue.py`, `grouped_seg_panel.py`) and explicitly test both shapes in U3. The arity detection happens at construction and is logged once for visibility. |
| Per-round QC opens a modal-style controller; if the user closes the launcher window mid-QC, state leaks. | Controller's `_teardown` is wired to launcher's `closeEvent` via the existing `is_workflow_locked` machinery; teardown is idempotent and uses `contextlib.suppress`. Tested in U8. |
| Mask-name validation against three flat namespaces is duplicated logic that may drift from `AddLayerDialog`'s. | Extract validation to a small helper in `gui/_resource_name_validation.py` (or co-locate in U7 inline and refactor later if duplication grows; defer the helper as a deferred-implementation question — see Open Questions). The plan does not strictly require extraction; consistency is best-effort. |
| Cancel-during-Worker leaves dangling thread. | `Worker.requestInterruption()` + `Worker.wait()` pattern from `gui/workers.py`; U6's "Cancel during Worker" test exercises it. |
| `view_bin > 1` interaction: working buffer captured at view_bin, but `seg_labels` and final mask write resolution may mismatch. | Capture both `channel_image` AND `seg_labels` at Start through the same `view_bin` lens. The final mask write goes through `store.write_mask` which assumes native shape; the existing single-cell workflow handles this resolution via the same pattern (`threshold_qc_queue.py:100-110`). For first delivery: workflow is best-tested at `view_bin=1`; document the v_bin assumption in `controller.py` docstring. Defer multi-bin verification to a follow-up. |

---

## Documentation / Operational Notes

- After this lands, run `/ce-compound-refresh` on
  `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
  to record the API evolution (the prescribed 3-arg `on_complete`
  shape is now implemented; the tech-debt entry becomes resolved
  history).
- Capture three new learnings via `/ce-compound` after merge:
  1. NaN-safe Gaussian convention (U1+U2).
  2. NaN-aware per-pixel threshold + mask compose pattern (U3).
  3. Interactive single-dataset workflow scaffolding pattern
     (`gui/workflows/dilute_phase/`) — first of its kind under
     Workflows tab.
- Update `docs/audits/gui-element-classification.yaml` to record
  the dilute panel's Start, Run-another-round, Done, Cancel
  buttons (Done = Creator; others = Actions).

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-18-dilute-phase-mask-requirements.md`
- Related learnings (full list in Context & Research):
  - `docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md`
  - `docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`
  - `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
  - `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
  - `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  - `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  - `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
  - `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  - `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
- Related code (key reuse seams):
  - `src/percell4/gui/grouped_seg_panel.py` (settings extraction)
  - `src/percell4/gui/threshold_qc.py` (NaN + no-persist)
  - `src/percell4/domain/measure/thresholding.py` (NaN dispatch)
  - `src/percell4/domain/measure/measurer.py::measure_cells`
  - `src/percell4/application/use_cases/accept_threshold.py` (Creator template)
  - `src/percell4/interfaces/gui/main_window.py` (Workflows host)
  - `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py` (QC reuse template)
