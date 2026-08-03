---
title: "feat: Auto extraction (two-pass) mode for Adaptive Local Clipping"
type: feat
status: active
date: 2026-06-23
---

# feat: Auto extraction (two-pass) mode for Adaptive Local Clipping

## Overview

Add a fully-automated **two-pass total feature extraction** as a new option under
the Adaptive Local Clipping panel's "Auto window method" dropdown. The user
supplies only the **smallest particle diameter** (their optical resolution
limit); everything else is measured from the image:

- **Fine pass** — `window = fill_factor × smallest particle Ø` (default 3×),
  `k = 1`. Catches and fills small particles (large ones hole out here).
- **Coarse pass** — `window = fill_factor × largest particle Ø`, where the
  largest Ø is measured by a Laplacian-of-Gaussian (LoG, 99th percentile of blob
  diameters), and `k` is the **noise-symmetry floor** (the smallest `k` whose
  band-passed negative tail implies a false-rate ≤ `fdr`). Fills large particles
  without oversampling the dilute phase. Added **only** when the coarse window
  exceeds the fine window; otherwise a single pass runs.

The two passes are OR-unioned with per-pass hole-filling. This is a faithful port
of the user-provided, eye-validated reference (`auto_extraction.py` / `.md`,
G3BP1 Dice 0.893) into PerCell4 conventions. Output is one combined binary mask,
saved via the existing Creator path.

---

## Problem Frame

The existing "Multi-scale (particle range)" mode doubles windows from a per-cell
Otsu size assessment. Otsu sizing fails on data with a bright, broadly
distributed dilute phase (the phase overlaps particles in intensity). The
reference solves both ends more principledly: a **LoG** sizes the largest
particle by curvature (immune to the dilute phase), the **smallest** is supplied
(it is confounded with noise and cannot be measured robustly — it is really the
user's resolution limit), and the coarse `k` is set by **noise symmetry** rather
than guessed. The result is an effectively parameter-free extractor given the
particle sizes.

---

## Requirements Trace

- R1. New "Auto extraction (two-pass)" option in the Auto-window-method dropdown,
  a per-cell engine switch (like Otsu-detect-particle / Multi-scale).
- R2. User supplies the **smallest particle diameter** (px or µm); the fine
  window = `fill_factor × smallest`.
- R3. The **largest particle diameter** is measured by LoG (99th pct); the coarse
  window = `fill_factor × largest`.
- R4. The coarse `k` is the auto noise-symmetry floor (target `fdr`); the fine
  `k = 1`.
- R5. Second (coarse) pass runs only when `coarse_window > fine_window`; passes
  are OR-unioned with per-pass hole-filling; `min_spot_px` filters the union once.
- R6. Output one combined per-cell mask via the existing Creator save; print a
  debug line with the passes `[(window, k), …]`, the measured largest Ø, and the
  fine window.
- R7. Prerequisites: active segmentation + single-frame (per-cell σ); abort
  cleanly otherwise (mirrors the per-cell modes).

---

## Scope Boundaries

- **Not** changing or replacing the existing modes (granule-size / otsu-mean /
  Otsu-detect-particle / Multi-scale / manual).
- **No** new dependency — `scikit-image` (LoG `blob_log`) is already a dep.
- **`fdr` stays fixed** at the reference default (0.1) — not a GUI field. The
  reference documents it as effectively inert on well-separated data and a
  *diagnostic*, not a routine knob, near the noise floor. Exposable later.
- **No** headless/batch surface (panel only; port the shared core later).
- **No** direct-window / `coarse_window` / `largest_particle_diameter` overrides
  in the GUI (the reference's manual overrides) — the GUI is the auto front-end.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/domain/measure/adaptive_clip.py` — `detect_adaptive_per_cell`
  (`:206-253`) is functionally the reference's `detect_single_pass` (per-cell
  band-pass `diff = work − gaussian(work,(w−1)/6)`, threshold `diff > k·σ`, σ via
  the shared `per_cell_sigma`). It lacks only **per-pass hole-filling**. Reuse it
  for both passes rather than porting a near-duplicate detector.
- `per_cell_sigma(work, labels)` (`:73`) + `apply_gaussian_smoothing` — the
  shared σ + smoothing the new module must reuse so its noise floor matches the
  detection comparison exactly.
- `detect_adaptive_multiscale` / `multiscale_windows` / `_run_multiscale_mode` —
  the closest sibling: a per-cell, segmentation-required, single-frame engine
  switch with a worker body, pre-flight, and terminal debug. Mirror its shape.
- `src/percell4/gui/_adaptive_clip_settings.py` — `_WINDOW_METHOD_*` dropdown +
  `_ENGINE_SWITCH_CODES` + `_active_mode()` + `_apply_mode_gating()`; add the new
  method here exactly like `multiscale`.
- `src/percell4/gui/adaptive_clip_panel.py` — `_on_run` dispatch chain;
  `run_adaptive_detection_multiscale` worker body; `_on_detect_done` returns
  `(mask, window_used)` and runs the Creator save — reusable as-is.

### Institutional Learnings

- `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md` —
  the band-pass/z-score model and the F formula behind "window = N × diameter".
  Note the reference uses `fill_factor = 3` (its own validated choice), distinct
  from the eye-validated `6×d_min` of the *other* modes; both are kept (separate
  methods, separate validations).
- `docs/solutions/architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md`
  — give each new knob its own field/default; per-cell methods short-circuit the
  per-group gate; validate on noisy fixtures.
- Per-cell σ ≈ noise only when noise-dominated; textured cells inflate MAD — the
  reference's near-noise caveat (DCP2). Carry into the debug, not a knob.

---

## Key Technical Decisions

- **Reuse `detect_adaptive_per_cell` for both passes**, adding a backward-
  compatible `fill_holes: bool = False` param, instead of porting the reference's
  `detect_single_pass` (avoids a near-duplicate; keeps σ definitionally shared).
- **Compute `work` + `per_cell_sigma` once** in `auto_extract` and thread them
  into the noise-floor helper so its z-scores exactly match the detection
  comparison. (The two `detect_adaptive_per_cell` pass calls recompute the same
  deterministic `work`/σ internally — acceptable for a single interactive run.)
- **Windows need not be odd** — `detect_adaptive_per_cell` maps any int window to
  `σ_bg = (w−1)/6`, so `round(3×Ø)` is passed as-is (the reference's `_win`),
  floored at `PARTICLE_WINDOW_MIN`.
- **Smallest particle Ø is a GUI input in px or µm**; µm → px via the dataset
  pixel size (reuse the panel's `_pixel_size_um`). `fill_factor` and `fdr` fixed
  at reference defaults (module constants).
- **`presmooth` = the panel's Gaussian σ** (default 1.0, matching the convention
  and the detector); passed through to both passes and the LoG/noise-floor.
- **`k` field is ignored** in this mode (fine `k=1` fixed, coarse auto) — gate it
  off for clarity, like other per-cell-specific fields.
- **Reuse `_on_detect_done`** — the worker returns `(mask, fine_window)`; the
  passes are printed to the terminal debug, the generic save status applies.

---

## Open Questions

### Resolved During Planning

- *Where does it attach?* → A new dropdown option under "Auto window method", an
  engine switch (not a whole-frame finder), exempt from the finder drift guard.
- *What does the user supply?* → Only the smallest particle Ø (px/µm). Largest
  measured (LoG), coarse k auto (noise floor).
- *New detector or reuse?* → Reuse `detect_adaptive_per_cell` + `fill_holes`.
- *Expose `fdr`?* → No (fixed 0.1); the reference frames it as a diagnostic.

### Deferred to Implementation

- Default smallest-Ø spinbox value (reference G3BP1 used 3 px) and µm default.
- Exact debug-line format for the passes.
- Whether to surface the measured largest Ø in a readout (vs terminal only) —
  default terminal-only to keep the form lean.

---

## Implementation Units

- U1. **Add `fill_holes` to `detect_adaptive_per_cell`**

**Goal:** Enable per-pass interior hole-filling without a duplicate detector.

**Requirements:** R5

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/measure/adaptive_clip.py`
- Modify: `tests/test_measure/test_adaptive_clip.py`

**Approach:**
- Add `fill_holes: bool = False` (keyword) to `detect_adaptive_per_cell`. When
  True, apply `scipy.ndimage.binary_fill_holes` to the accumulated `out` **before**
  `_filter_by_area`. Default False keeps every existing caller byte-identical.

**Patterns to follow:**
- The reference `detect_single_pass`'s `fill_holes` placement (fill then area
  filter); `_filter_by_area` usage already in the function.

**Test scenarios:**
- Happy path: a ring-shaped detection (hollow disc) becomes solid with
  `fill_holes=True`; unchanged with `fill_holes=False`.
- Edge: `fill_holes=True` does not merge two separate components.
- Integration (characterization): existing callers (default `fill_holes=False`)
  produce identical masks (existing suite still green).

**Verification:**
- New hole-fill test passes; all existing `test_adaptive_clip.py` tests pass.

---

- U2. **New domain module `auto_extraction.py`**

**Goal:** Port the reference's two-pass extractor into pure domain, reusing the
shared σ + detector.

**Requirements:** R2, R3, R4, R5, R6

**Dependencies:** U1

**Files:**
- Create: `src/percell4/domain/measure/auto_extraction.py`
- Create: `tests/test_measure/test_auto_extraction.py`
- Modify: `src/percell4/domain/measure/CLAUDE.md`

**Approach:**
- `measure_largest_particle_diameter(image, labels, *, percentile=99.0,
  presmooth_sigma_px=1.0, max_sigma=20.0, num_sigma=12, threshold_rel=0.1) ->
  float` — LoG (`skimage.feature.blob_log`, lazy import) on the in-cell,
  1–99.9-percentile-normalized smoothed image; returns the `percentile`-th of
  in-cell blob diameters (`2√2·σ`); `0.0` when no blobs / degenerate.
- `noise_symmetry_floor_k(work, labels, sigma, window, *, fdr=0.1, k_floor=1.0,
  k_ceiling=15.0, step=0.25) -> float` — over the per-cell band-passed z-scores,
  the smallest `k` with `pos≥20` and `neg ≤ fdr·pos`; takes precomputed
  `work`/`sigma` so it matches detection exactly.
- `auto_extract(image, labels, *, smallest_particle_px, fill_factor=3.0,
  fdr=0.1, presmooth_sigma_px=1.0, min_spot_px=2, size_percentile=99.0,
  max_sigma=20.0, fill_holes=True) -> (mask uint8, info dict)` — fine window =
  `round(fill_factor·smallest)`, floored at `PARTICLE_WINDOW_MIN`; run pass 1 via
  `detect_adaptive_per_cell(..., k=1, min_spot_px=1, fill_holes=...)`; measure
  largest → coarse window; if `coarse > fine`, compute `k_coarse` (noise floor)
  and OR-union pass 2; `_filter_by_area` the union once at `min_spot_px`. `info`:
  `passes [(w,k),…]`, `fine_window`, `largest_particle_px`, `second_pass_used`,
  `presmooth`, `n_cells`, `n_components`, `area_px`.
- Module constants `FILL_FACTOR=3.0`, `FDR=0.1`, `SIZE_PERCENTILE=99.0`,
  `MAX_SIGMA=20.0`. Pure domain (numpy/scipy/skimage; lazy `blob_log`).

**Patterns to follow:**
- The reference `auto_extraction.py` for algorithm fidelity; `adaptive_clip.py`
  frozen-report style; reuse `per_cell_sigma` + `apply_gaussian_smoothing` +
  `detect_adaptive_per_cell` (do **not** re-implement the band-pass or σ).

**Test scenarios:**
- Happy path (two-pass): a fixture with small + large particles → both filled
  (large not hollow), `info["second_pass_used"] is True`, two `passes`.
- Happy path (single-pass): only small particles (coarse ≤ fine) → one pass,
  `second_pass_used is False`.
- `measure_largest_particle_diameter`: a known big disc → diameter within
  tolerance of its true size; empty/degenerate → `0.0`.
- `noise_symmetry_floor_k`: rises above `k_floor` on a noisy fixture; returns
  `k_floor` when no cells/z-scores; `k_ceiling` when never satisfied.
- Edge: no segmentation pixels → empty mask, no raise.
- Edge: `min_spot_px` filters the unioned mask (small specks dropped).
- Mask is `{0,1}` uint8.

**Verification:**
- Module tests pass; a wide-size-range fixture fills both ends solid.

---

- U3. **Settings widget: the new method + smallest-Ø field + gating**

**Goal:** Expose "Auto extraction (two-pass)" and its one input.

**Requirements:** R1, R2, R7

**Dependencies:** None

**Files:**
- Modify: `src/percell4/gui/_adaptive_clip_settings.py`
- Modify: `tests/test_gui/test_adaptive_clip_settings_widget.py`

**Approach:**
- Add `"Auto extraction (two-pass)" -> "auto-extract"` to `_WINDOW_METHOD_LABELS`
  / `_WINDOW_METHOD_CODES`; add `"auto-extract"` to `_ENGINE_SWITCH_CODES` (drift-
  guard exempt). Extend `_active_mode()` to return `"auto-extract"`.
- Add a **Smallest particle Ø** spinbox + unit combo (px/µm), live only in
  auto-extract mode (gating). New `AdaptiveClipConfig` fields
  `auto_extract_mode: bool`, `smallest_particle_value: float`,
  `smallest_particle_unit: str` (default 3.0 px). Keep the Min-particle-size
  filter live in this mode; gate `k` off (unused — fine k=1, coarse auto).
- `auto_extract_mode` True iff Auto on AND method code is `auto-extract`.

**Patterns to follow:**
- How `multiscale` / `particle` were added (config flags, gating, `_active_mode`,
  `_adopt_particle_defaults` analog if a default needs adopting on entry).

**Test scenarios:**
- Default config unchanged for existing fields; new fields present with defaults.
- Selecting the method (Auto on) → `auto_extract_mode True`, smallest-Ø field
  enabled, `k` disabled, min-size live.
- Switching away → fields revert; `auto_extract_mode False`.
- `current_config()` reflects the smallest-Ø value + unit and the px/µm code map.

**Verification:**
- Widget tests pass; the drift-guard assertion still holds (engine switch exempt).

---

- U4. **Panel wiring: dispatch, worker, debug, save**

**Goal:** Run auto-extraction from the panel as a per-cell engine.

**Requirements:** R6, R7

**Dependencies:** U2, U3

**Files:**
- Modify: `src/percell4/gui/adaptive_clip_panel.py`
- Modify: `tests/test_gui/test_adaptive_clip_panel.py`

**Approach:**
- Worker body `run_adaptive_auto_extract(image, labels, smallest_particle_px,
  fill_factor, fdr, presmooth, min_spot_px) -> (mask, fine_window)` calling
  `auto_extract(..., return-info)`; prints/returns the `passes` for debug.
- `_run_auto_extract_mode(config, image, is_timelapse, store, viewer_win)`
  mirroring `_run_multiscale_mode`: require single-frame + active segmentation +
  matching shapes; resolve smallest Ø to px (px as-is, or µm via
  `_pixel_size_um`); resolve `min_spot_px` (reuse `resolve_min_area_px`); prompt
  for a mask name; dispatch the worker; reuse `_on_detect_done` / `_on_detect_error`.
- Dispatch in `_on_run`: `if config.auto_extract_mode: self._run_auto_extract_mode(...)`
  (before the auto-finder branch, alongside particle/multiscale).
- Debug: a `_print_auto_extract_*` line with smallest Ø (px), fill_factor, fdr,
  measured largest Ø, and the passes.

**Patterns to follow:**
- `_run_multiscale_mode` (pre-flight + worker + debug); `run_adaptive_detection_multiscale`
  (worker body returning `(mask, window)`); `_on_detect_done` (Creator save).

**Test scenarios:**
- Happy path: method selected + channel + segmentation → worker dispatched with
  the resolved smallest-px and the mask is saved + selected (Creator).
- Pre-flight: no segmentation → status, no worker; time-lapse → status, no worker.
- µm smallest Ø without a pixel size → status abort.
- The resolved `smallest_particle_px` reaching the worker matches `3.0 px`
  (px) and the µm→px conversion (µm).
- Debug prints the passes line.

**Verification:**
- Panel tests pass; an end-to-end run via the synchronous fake Worker creates a
  mask.

---

## System-Wide Impact

- **Interaction graph:** one new dropdown option + one new dispatch branch +
  worker body; no viewer/session changes. `detect_adaptive_per_cell` gains an
  optional `fill_holes` (default off) — every existing caller unchanged.
- **Error propagation:** worker errors via `_on_detect_error`; pre-flight aborts
  before dispatch; LoG/empty-image degeneracies return `0.0`/empty, not raises.
- **API surface parity:** no CLI/headless surface (deferred). `auto_extract` is a
  new public domain function reusing the shared detector.
- **Unchanged invariants:** existing modes, the `{0,1}` mask contract, the
  `6×d_min` rule of the other modes (this mode uses its own `3×Ø`), and
  `detect_adaptive_per_cell`'s default behavior.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| LoG mis-sizes the largest particle on atypical data | 99th-pct (robust to spurious large blobs), `max_sigma` cap; the passes are printed for inspection; coarse pass only added when it exceeds the fine window. |
| Near-noise features (DCP2 regime) flood or miss at k=1 | Inherent physical limit per the reference; surface the passes/decision in debug as a diagnostic, not a silent result. |
| `fill_holes` change alters an existing caller | Default `False`; characterization test pins existing masks. |
| Even windows from `3×Ø` | `detect_adaptive_per_cell` accepts any int window (σ_bg=(w−1)/6); floored at `PARTICLE_WINDOW_MIN`. |
| µm smallest Ø without pixel size | Abort with a clear status (reuse the panel's pixel-size guard). |

---

## Sources & References

- Reference spec/impl (user-provided, authoritative): `auto_extraction.py`,
  `auto_extraction.md`.
- Sibling plan: [docs/plans/2026-06-23-001-feat-cnr-subpopulation-classification-plan.md](docs/plans/2026-06-23-001-feat-cnr-subpopulation-classification-plan.md)
- Convention: `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md`
- Key code: `src/percell4/domain/measure/adaptive_clip.py`,
  `src/percell4/gui/_adaptive_clip_settings.py`,
  `src/percell4/gui/adaptive_clip_panel.py`
