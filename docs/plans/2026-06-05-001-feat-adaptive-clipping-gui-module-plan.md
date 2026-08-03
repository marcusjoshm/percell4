---
title: "feat: Adaptive Local Clipping module in the Analysis tab"
type: feat
status: active
date: 2026-06-05
---

# feat: Adaptive Local Clipping module in the Analysis tab

## Overview

Add an interactive **Adaptive Local Clipping** module to the Analysis tab of the Launcher Window, immediately after the existing **Grouped Thresholding** module. It exposes the already-validated `adaptive` local-threshold puncta detector (the production stress-granule recipe) directly in the GUI, with a configurable window, `k`, Gaussian smoothing, and a particle-size filter — plus an **Auto adaptive window size** option that sizes the window to the granules in the image via an Otsu first-pass. The module is a **Creator**: it writes a new `/masks/<name>` layer, auto-selects it, and shows it in napari, reusing the existing detector pipeline rather than reimplementing detection.

---

## Problem Frame

The `adaptive` detector (window 15, k 2.25) was selected as the production stress-granule method, but it is only reachable today through the headless batch pipeline (`apply_threshold_headless`) and ad-hoc scripts (`scripts/gen_puncta_masks.py`) — there is no GUI surface. Separately, field work showed the optimal **window scales with granule size** and changes per dataset/condition (a 15 px window hollows out large granules; ~50 px is right for matured granules). The user verified by hand that estimating the window from the **mean particle size of an Otsu first-pass mask** works well. This module brings `adaptive` into the interactive GUI and automates the one parameter that varies per dataset.

---

## Requirements Trace

- R1. A new "Adaptive Local Clipping" module appears in the Analysis tab **immediately after** "Grouped Thresholding".
- R2. The module exposes: adaptive **window size (px)**, **k** (sigma multiplier), **Gaussian σ** (smoothing), a **particle-size filter** (numeric value + a **px² / µm² units dropdown**), and an **"Auto adaptive window size"** checkbox.
- R3. **Manual mode** (auto unchecked): run whole-frame adaptive local clipping with the configured settings, reusing the existing `adaptive` detector / `detect_two_pass` path.
- R4. **Auto mode** (auto checked): run an Otsu first-pass, estimate the window from the Otsu mask's **mean** particle size, then run adaptive with the estimated window and **all other** configured settings unchanged.
- R5. The result is persisted as a `/masks/<name>` layer following the four-step **Creator contract** (store → viewer → refresh → set-active), as `{0,1}` uint8.
- R6. Heavy compute (Otsu first-pass + adaptive) runs **off the UI thread** in a `Worker`.
- R7. The µm² size-filter option requires `pixel_size_um` in dataset metadata; when absent, surface an explicit error rather than silently defaulting.

---

## Scope Boundaries

- **Whole-frame detection only.** Detection runs on the active channel's 2D image, not per cell-group; no segmentation/grouping is required. Per-cell assignment is left to the existing **Particle Analysis** step downstream (intersect the mask with the active segmentation).
- **Single 2D frame.** Operates on the active channel's currently-displayed image array (from the napari layer), mirroring the existing Whole-Field and Grouped Thresholding panels. Multi-timepoint time-lapse handling is deferred.
- **Interactive mask Creator only.** No integration with the registered-analysis framework (Scripts tab / batch CSV). No `/measurements` writes.
- **No new detector.** Reuse the existing `adaptive` detector and `detect_two_pass`; this plan adds GUI + a thin whole-frame wrapper + an auto-window estimator only.
- The auto-window calibration **factor** is an internal constant, not a UI field.

### Deferred to Follow-Up Work

- Time-lapse (multi-frame) detection — a later iteration if needed.
- Optionally bridging `adaptive` into the registered-analysis framework for batch/CSV output — separate effort.

---

## Context & Research

### Relevant Code and Patterns

- **Module shape to mirror:** `src/percell4/gui/grouped_seg_panel.py` (panel: `get_store`/`get_viewer_window`/`show_status` callbacks, Run button, `Worker` compute) + `src/percell4/gui/_grouped_threshold_settings.py` (settings `QWidget` with `current_config() -> frozen dataclass` and a `config_changed = Signal()`).
- **Mount point:** `src/percell4/interfaces/gui/task_panels/analysis_panel.py:176-188` builds the Grouped Thresholding `QGroupBox`; insert the new module's `QGroupBox` right after line 188 (before Measurements at 191). `main_window.py:_create_analysis_panel` already injects the needed callbacks — no change there.
- **Creator contract (canonical):** `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` and the reference impl `src/percell4/application/use_cases/accept_threshold.py` + its caller `analysis_panel.py:494-508`. Four ordered steps: `store.write_mask` → `viewer_win.add_mask` → `session.refresh_resource_lists(mask_names=...)` → `session.set_active_mask`. The Qt-free use case owns steps 1/3/4; the **panel** owns step 2 (`add_mask`) after `.execute()` returns.
- **Detector + pipeline to reuse:** `domain/measure/puncta_detectors.py` (`_adaptive`, emits `{0,1}` uint8), `domain/measure/puncta_pipeline.py` (`detect_two_pass(smoothed_image, group_label_mask, settings, ...)`), `workflows/models.py` (`PunctaDetectorSettings`, `ParticleSettings`). The validated gallery masks were produced via this exact detector.
- **Whole-frame as a single group:** `detect_two_pass` takes a `group_label_mask`; passing an all-`True` mask runs the detector over the whole frame with one σ — the same computation validated against the WT image during field work. `_isolate` NaN-fills out-of-group pixels (none, with all-True), gaussian-peak fits the whole-frame background, `_adaptive` thresholds locally.
- **px↔µm² conversion (reuse):** `ParticleSettings(min_area, min_area_unit ∈ {"px","um2"})` (`workflows/models.py:234-266`) and the resolution at `workflows/phases.py:965-990` (µm² → px divides by `pixel_size_um²`; missing calibration fails explicitly). Pixel size: `store.metadata["pixel_size_um"]`, read via `phases.py:_read_pixel_size_um`.
- **Worker:** `src/percell4/gui/workers.py` (`Worker` QThread; `finished`/`progress`/`error(WorkerError)`), as used by `grouped_seg_panel.py`.
- **Name prompt + viewer mask add:** `src/percell4/gui/_resource_name_prompt.py` (`prompt_for_resource_name`, dedupes against `store.list_masks()`); `src/percell4/gui/viewer.py:320` (`ViewerWindow.add_mask`, binary colormap, blocks name collisions).

### Institutional Learnings

- **Creator four-step sequence** — each skipped step fails silently (skip add_mask → mask in HDF5 but napari empty; skip set_active → "mask '' not found"). Order matters: refresh before set-active.
- **Mask layer hygiene** (`ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`, `add-mask-name-collision-image-layer-crash-2026-05-15.md`): write to HDF5 **before** the napari add; use a unique/namespaced mask name; persist `{0,1}` uint8 (no float cast — `DirectLabelColormap` raises on non-int); the existing `viewer.add_mask` path handles the mask-vs-segmentation tagging.
- **Read canonical state directly** (`consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`): read `session.active_channel` directly; **no** local channel-override combo. The Run button is a Creator — it must not mutate the other session fields as a side effect.
- **Wire user-edit signals** (`conventions/qt-wire-user-edit-signals-2026-05-12.md`): connect `valueChanged`/`toggled`/`activated`, not programmatic setters.
- **Forward every param end-to-end** (`integration-issues/phasor-view-bin-not-forwarded-...`): unit tests that only assert kwarg acceptance once masked a broken end-to-end chain — include an end-to-end test that the panel's configured window/k/σ/size-filter actually reach the detector and change the mask.
- **Avoid auto/manual feedback loops** (`logic-errors/gmm-roi-spinbox-anchor-feedback-loop-2026-05-03.md`): the auto-window estimate sets the (disabled) window spinbox once after the run; manual edits must not re-trigger estimation.
- **T1 modules** (`store.py`, `application/use_cases/*`): re-run `python3 scripts/learnings_applicability.py <path>` on the new use-case path before editing.

---

## Key Technical Decisions

- **Whole-frame via a single full-`True` group** passed to `detect_two_pass`, rather than the per-group `apply_threshold_headless` path. Rationale: the user chose whole-frame; it needs no segmentation/grouping config; it reuses the validated detector unchanged; and it sidesteps the `ThresholdingRound` round-name regex coupling.
- **Auto-window estimator:** `window = make_odd(round(FACTOR × mean_equiv_diameter))`, where `mean_equiv_diameter` = mean equivalent diameter (`2·√(area/π)`) of the Otsu first-pass mask's connected components (above a tiny noise floor), clamped to `[11, 151]`. **Mean** is used per the user's by-hand finding. `FACTOR` is a single internal constant.
- **Creator split:** a new Qt-free use case `AcceptPunctaMask` owns store-write + refresh + set-active; the panel owns `viewer.add_mask`. Mirrors `AcceptThreshold` exactly.
- **Mask written via `store.write_mask(user_name, mask)`** (user-chosen name from the prompt), not via a round name — avoids `_ROUND_NAME_RE` constraints.
- **Size filter** is applied through the detector's existing `min_spot_px` (area, px) by converting the UI value+unit to px area first (reusing the ParticleSettings µm²→px logic). µm² with no `pixel_size_um` → explicit error.
- **Gaussian field is σ** (smoothing sigma), labeled "Gaussian σ", consistent with the Whole-Field thresholding UI and the production recipe (σ=1.0).

---

## Open Questions

### Resolved During Planning

- Detection scope → **whole frame** (no segmentation required).
- Auto-window statistic → **mean** particle size of the Otsu first-pass mask (user's by-hand result).
- Particle-size filter → **area** (px² / µm²), reusing existing area-based filter.
- Gaussian field → **sigma**.

### Deferred to Implementation

- **Exact `FACTOR` value** for the auto-window estimator: calibrate so the estimate reproduces the user's by-hand results and the known references (small As+Noco granules → window ≈ 15; matured WT granules → window ≈ 50). Pin with a characterization test on a known image; start from `FACTOR ≈ 2.0` and adjust.
- Whether to display the auto-estimated window back into the (disabled) window spinbox after a run (cosmetic; default: yes, display-only, no re-trigger).
- The Otsu first-pass noise floor (min component area) used before computing the mean diameter — small constant, settle during implementation.

---

## High-Level Technical Design

> *Directional guidance for review, not implementation specification.*

**Run flow (Creator, in a Worker):**

```
read session.active_channel ──> image array from napari layer (2D, float)
        │
        ├─ smoothed = gaussian(image, σ)
        │
   auto window? ──yes──> otsu_mask = otsu_first_pass(smoothed)
        │                 window = estimate_adaptive_window(otsu_mask)   # mean-diameter × FACTOR
        │no
        ▼
   settings = PunctaDetectorSettings(detector="adaptive",
                detector_params={window_px: window, k: k},
                min_spot_px = resolve_area_px(size_value, unit, pixel_size_um))
        │
   mask = detect_two_pass(smoothed, full_true_group, settings)      # whole-frame adaptive, {0,1} uint8
        │
   ── back on UI thread ──
   name = prompt_for_resource_name(existing = store.list_masks())
   AcceptPunctaMask(repo, session).execute(mask, name)             # write → refresh → set-active
   viewer.add_mask(mask, name)                                      # step 2 of Creator
   (if auto) window_spin.setValue(window)  # display only
```

**Mode / unit matrix:**

| Auto window | Size unit | Behavior |
|---|---|---|
| off | px² | window from spinbox; `min_spot_px = value` |
| off | µm² | window from spinbox; `min_spot_px = value / pixel_size_um²` (error if no calibration) |
| on | px² | Otsu first-pass → mean-diameter window; `min_spot_px = value` |
| on | µm² | Otsu first-pass → mean-diameter window; `min_spot_px = value / pixel_size_um²` (error if no calibration) |

---

## Implementation Units

- U1. **Whole-frame adaptive runner + auto-window estimator (pure domain)**

**Goal:** Pure, Qt-free functions that (a) run the validated `adaptive` detector over a whole frame, (b) estimate the window from an Otsu first-pass mask, and (c) resolve the size-filter value+unit to a px area.

**Requirements:** R3, R4, R7

**Dependencies:** None

**Files:**
- Create: `src/percell4/domain/measure/adaptive_clip.py`
- Test: `tests/test_measure/test_adaptive_clip.py`

**Approach:**
- `detect_adaptive_whole_frame(image, gaussian_sigma, settings) -> np.uint8 mask`: smooth via `apply_gaussian_smoothing`, build a full-`True` group mask, call `detect_two_pass(smoothed, group_mask, settings)`. Returns the `{0,1}` uint8 mask unchanged.
- `otsu_first_pass(smoothed) -> bool mask`: Otsu threshold (reuse `THRESHOLD_METHODS["otsu"]` or `skimage.filters.threshold_otsu`) on the smoothed image.
- `estimate_adaptive_window(otsu_mask, *, factor=FACTOR, lo=11, hi=151, noise_floor=...) -> int`: label components, drop those below the noise floor, compute mean equivalent diameter (`2·√(area/π)`), `window = make_odd(round(factor × mean_d))`, clamp to `[lo, hi]`. Degenerate (no components) → return a sane default (e.g. `lo`).
- `resolve_min_area_px(value, unit, pixel_size_um) -> int`: `unit=="px"` → `int(value)`; `unit=="um2"` → `int(round(value / pixel_size_um²))`; raise `ValueError` if `pixel_size_um` is falsy. Mirror `phases.py:965-990`.
- Define `FACTOR` as a named module constant (default per deferred calibration).

**Patterns to follow:** `domain/measure/puncta_pipeline.py` (`detect_two_pass`, `_size_filter`), `domain/measure/thresholding.py` (`apply_gaussian_smoothing`, `THRESHOLD_METHODS`), the µm²→px logic in `workflows/phases.py:965-990`.

**Test scenarios:**
- Happy path: a synthetic image with bright blobs → `detect_adaptive_whole_frame` returns a `{0,1}` uint8 mask whose foreground sits on the blobs; dtype and value-set asserted.
- Happy path: an Otsu mask of blobs of known radius → `estimate_adaptive_window` returns an odd int ≈ `factor × known_diameter`, within `[11,151]`.
- Edge case: empty Otsu mask (no components) → estimator returns the clamp floor, does not raise.
- Edge case: estimator output is always odd and clamped (give sizes that would exceed `hi` and fall below `lo`).
- Edge case: a granule larger than a small window hollows; a window ≥ estimator output fills it (characterizes the size↔window relationship).
- Error path: `resolve_min_area_px(v, "um2", None)` raises `ValueError`; `("um2", 0.5)` converts correctly; `("px", _)` passes through.
- Characterization: on a saved reference frame, `estimate_adaptive_window` returns a window in the expected band (pins `FACTOR`).

**Verification:** Detector output dtype/shape/value-set match `detect_two_pass`; estimator is deterministic, odd, clamped; size resolution matches the existing ParticleSettings behavior.

---

- U2. **`AcceptPunctaMask` Creator use case (Qt-free)**

**Goal:** Persist a puncta mask and select it, owning Creator steps 1/3/4 without touching Qt or the viewer.

**Requirements:** R5

**Dependencies:** None

**Files:**
- Create: `src/percell4/application/use_cases/accept_puncta_mask.py`
- Test: `tests/test_application/test_accept_puncta_mask.py`

**Approach:**
- `AcceptPunctaMask(repo, session)` with `execute(mask: np.ndarray, name: str) -> result` that: validates `mask` is `{0,1}` uint8 (coerce/guard), `repo.write_mask(handle, name, mask)`, `session.refresh_resource_lists(mask_names=repo.list_masks(handle))`, `session.set_active_mask(name)`; returns a small result (name, n_positive, n_total). No viewer port. Mirror `application/use_cases/accept_threshold.py` (steps + ordering: refresh before set-active).

**Execution note:** This is a T1 module — run `python3 scripts/learnings_applicability.py src/percell4/application/use_cases/accept_puncta_mask.py` before editing and consult any surfaced canonical sources.

**Patterns to follow:** `src/percell4/application/use_cases/accept_threshold.py` (Creator contract, repo+session only).

**Test scenarios:**
- Happy path: execute with a `{0,1}` mask → `repo.write_mask` called with the name and array; `refresh_resource_lists` called **before** `set_active_mask`; returned name matches.
- Edge case: a non-uint8 / non-binary mask is coerced (or rejected with a clear error) — assert the dtype contract holds at the store boundary.
- Integration: with a real `DatasetStore` fixture, after `execute` the mask is readable via `read_mask(name)` and `session.active_mask == name`.
- Error path: duplicate name behavior is defined (overwrite vs reject) and asserted.

**Verification:** The use case satisfies the four-step Creator contract minus the viewer step; the store-before-list ordering holds; tests pass against a real store fixture.

---

- U3. **Adaptive-clip settings widget**

**Goal:** A reusable settings form exposing all module fields and emitting a frozen config.

**Requirements:** R2, R4

**Dependencies:** None

**Files:**
- Create: `src/percell4/gui/_adaptive_clip_settings.py`
- Test: `tests/test_gui/test_adaptive_clip_settings_widget.py`

**Approach:**
- `AdaptiveClipSettingsWidget(QWidget)` with rows: window size px (`QSpinBox`, odd-enforced/odd-stepped), k (`QDoubleSpinBox`), Gaussian σ (`QDoubleSpinBox`, "None" at 0), particle-size value (`QDoubleSpinBox`) + units `QComboBox` (`px²`, `µm²`), and an "Auto adaptive window size" `QCheckBox`.
- `current_config() -> AdaptiveClipConfig` (frozen dataclass: `window_px, k, gaussian_sigma, min_size_value, min_size_unit, auto_window`).
- `config_changed = Signal()` wired to each widget's **user-edit** signal (`valueChanged`/`toggled`/`activated`).
- Toggling "Auto adaptive window size" **disables** the window spinbox (and visibly marks it "auto"); unchecking re-enables it. The widget never triggers detection — it only reports config.

**Patterns to follow:** `src/percell4/gui/_grouped_threshold_settings.py` (frozen `current_config()`, `config_changed`, `_connect_change_signals`).

**Test scenarios:**
- Happy path: set each field → `current_config()` returns matching values; unit combo maps to `"px"`/`"um2"`.
- Edge case: checking "Auto adaptive window size" disables the window spinbox; unchecking re-enables it; `config.auto_window` reflects the checkbox.
- Edge case: window value is reported odd (or the widget enforces odd).
- Integration: editing any field emits `config_changed` exactly once per user edit (not on programmatic `setValue`).

**Verification:** Widget builds under `qtbot`; config round-trips; auto checkbox gates the window field; signals fire on user edits only.

---

- U4. **Adaptive Local Clipping panel (Creator)**

**Goal:** The panel that runs detection in a worker and creates the mask, including the auto-window first-pass.

**Requirements:** R1, R3, R4, R5, R6, R7

**Dependencies:** U1, U2, U3

**Files:**
- Create: `src/percell4/gui/adaptive_clip_panel.py`
- Test: `tests/test_gui/test_adaptive_clip_panel.py`

**Approach:**
- `AdaptiveClipPanel(QWidget)` with keyword callbacks `get_store`, `get_viewer_window`, `show_status` (mirror `GroupedSegPanel.__init__`). Hosts an `AdaptiveClipSettingsWidget` + a "Run Adaptive Clipping" `QPushButton` + a result `QLabel`.
- On Run (Creator): read `session.active_channel`; resolve the matching `Image` napari layer's 2D array; snapshot `current_config()`; resolve `min_spot_px` via `resolve_min_area_px(value, unit, store.metadata.get("pixel_size_um"))` — on µm²-without-calibration, `show_status` an error and abort. Build `PunctaDetectorSettings(detector_name="adaptive", seed_detector_name="otsu", background_estimator_name="gaussian-peak", detector_params={"window_px": …, "k": …}, min_spot_px=…, spot_scale_prior=(1.0, 4.0))`.
- Compute in a `Worker`: if `auto_window`, `otsu_first_pass` → `estimate_adaptive_window` → override `window_px`; then `detect_adaptive_whole_frame(image, σ, settings)`.
- On `finished` (UI thread): `prompt_for_resource_name(existing=store.list_masks())` (abort on cancel) → `AcceptPunctaMask(repo, session).execute(mask, name)` → `viewer_win.add_mask(mask, name)` → update result label (n positive, chosen window) → if auto, set the disabled window spinbox to the estimate (display only). On `error(WorkerError)`, `show_status` the message.
- Guard rails: no active channel / no viewer / channel layer missing / store missing → `show_status` and abort (mirror `grouped_seg_panel.py:95-124`). The Run button writes **only** `active_mask` (via the use case) — no other session writes.

**Patterns to follow:** `src/percell4/gui/grouped_seg_panel.py` (callbacks, `_on_run` validation, `Worker` usage, `prompt_for_resource_name`), `analysis_panel.py:468-510` (`AcceptThreshold` Creator + `viewer_win.add_mask`).

**Test scenarios:**
- Happy path (manual): channel + viewer set, fake Image layer with blob array, name prompt monkeypatched → `Worker` runs synchronously (or mocked), `store.write_mask` called with the configured window/k reaching the detector, `viewer.add_mask` called, `session.active_mask` set.
- Happy path (auto): auto checked → the Otsu first-pass + estimator drive the window; assert the **estimated** window (not the spinbox value) is what the detector receives, and is written back to the disabled spinbox.
- Error path: µm² unit with `pixel_size_um` absent → status error, **no** mask written.
- Error path: no active channel / channel layer missing → status error, no write.
- Edge case: name prompt cancelled → no `write_mask`, no `set_active_mask`.
- Integration (end-to-end, per the view_bin learning): changing window/k in the settings widget changes the resulting mask — assert the config genuinely flows panel → detector → mask, not just that kwargs were accepted.

**Verification:** Manual and auto runs both produce a selected `/masks/<name>` layer in the viewer; the configured/estimated params reach the detector; errors abort cleanly with status messages; heavy compute is on the worker thread.

---

- U5. **Mount the module in the Analysis tab + classify the Run button**

**Goal:** Place the new module after Grouped Thresholding and record its GUI classification.

**Requirements:** R1

**Dependencies:** U4

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py`
- Modify: `docs/audits/gui-element-classification.yaml`
- Test: `tests/test_gui/test_adaptive_clip_panel.py` (assert module presence) or extend an existing analysis-panel test

**Approach:**
- In `analysis_panel._build_ui`, after the Grouped Thresholding block (line ~188), construct `AdaptiveClipPanel(self.data_model, get_store=self._get_store, get_viewer_window=self._get_viewer_window, show_status=self._show_status)` inside a `QGroupBox("Adaptive Local Clipping")` and `layout.addWidget(...)` — before the Measurements block.
- Add a `gui-element-classification.yaml` entry for the "Run Adaptive Clipping" button as `class: Creator` (model the Whole-Field accept entry at lines 260-268: writes a new `/masks` resource and auto-selects via `set_active_mask`).

**Patterns to follow:** `analysis_panel.py:176-188` (Grouped Thresholding mount), `docs/audits/gui-element-classification.yaml:260-268`.

**Test scenarios:**
- Happy path: an `AnalysisPanel` built under `qtbot` contains a `QGroupBox` titled "Adaptive Local Clipping" positioned after "Grouped Thresholding" and before "Measurements".
- Test expectation: classification YAML change is config-only — assert (or manually verify) the new Creator entry exists and references `set_active_mask`.

**Verification:** The module renders in the Analysis tab in the required position; the classification audit lists the Run button as a Creator; existing analysis-panel tests still pass.

---

## System-Wide Impact

- **Interaction graph:** Run → `Worker` (QThread) → on finish `AcceptPunctaMask` fires `Session` `ACTIVE_MASK_CHANGED` → `CellDataModel._on_mask_changed` → `StateChange(mask=True)` → `ViewerWindow._on_state_changed`. The panel also calls `viewer.add_mask` directly (the layer is added by the panel; the state push selects it).
- **Error propagation:** validation/`pixel_size_um` errors surface via `show_status` and abort before any store write; `Worker` failures arrive as `error(WorkerError)` and abort cleanly (no partial mask).
- **State lifecycle risks:** store-before-layer ordering must hold (write_mask before add_mask) to avoid the napari-empty / "mask not found" failure modes; name collisions are blocked by `prompt_for_resource_name` + `add_mask`.
- **API surface parity:** the headless/batch path (`apply_threshold_headless`) and `scripts/gen_puncta_masks.py` already expose `adaptive`; this adds the interactive surface without changing those. The new whole-frame wrapper is additive.
- **Unchanged invariants:** `PunctaDetectorSettings`, `detect_two_pass`, `_adaptive`, `store.write_mask`, the Creator contract, and the per-group `apply_threshold_headless` path are all reused unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Auto-window `FACTOR` mis-calibrated → wrong window on real data | Pin `FACTOR` with a characterization test against known references (As+Noco ≈ 15, WT ≈ 50); expose the chosen window in the result label so the user sees it. |
| Whole-frame detection picks up extracellular junk | Documented scope; per-cell assignment via downstream Particle Analysis; size filter removes specks. |
| µm² filter with no `pixel_size_um` | Explicit error, no silent default (R7), mirroring `phases.py` behavior. |
| Large images make detection slow / block UI | Run in a `Worker`; the detector is the same one already used headlessly. |
| Auto/manual spinbox feedback loop | Auto sets the disabled spinbox once after the run; manual edits never re-trigger estimation. |
| Mask layer misclassified as segmentation / collision crash | Reuse the existing `viewer.add_mask` path; unique user-chosen name; `{0,1}` uint8 persisted verbatim. |

---

## Sources & References

- Related code: `src/percell4/gui/grouped_seg_panel.py`, `src/percell4/gui/_grouped_threshold_settings.py`, `src/percell4/application/use_cases/accept_threshold.py`, `src/percell4/domain/measure/puncta_pipeline.py`, `src/percell4/workflows/phases.py`, `src/percell4/interfaces/gui/task_panels/analysis_panel.py`, `scripts/gen_puncta_masks.py`
- Learnings: `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`, `docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`, `docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md`
- Method context: `docs/methods/headless-puncta-thresholding.md`, `puncta_mask_gallery/README.md`
