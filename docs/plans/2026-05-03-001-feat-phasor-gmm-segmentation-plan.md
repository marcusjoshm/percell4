---
title: "feat: Phasor segmentation — intensity / reference-circle filters + GMM ROI placement"
type: feat
status: active
date: 2026-05-03
origin: docs/brainstorms/2026-05-03-phasor-gmm-segmentation-requirements.md
---

# feat: Phasor segmentation — intensity / reference-circle filters + GMM ROI placement

## Overview

Add automated phasor-cluster ROI placement to PerCell4's FLIM tab. Users gain three GUI-local filters (intensity threshold, reference circle anchored at a target lifetime on the universal semicircle, plus the existing active-mask filter) that AND-compose with the existing cell-selection filter and restrict both the on-screen phasor histogram AND the GMM input pixels. A new "Phasor Segmentation" group runs `sklearn.mixture.GaussianMixture` on the filtered `(g, s)` pixels (with intensity weighting via subsampling) and appends one ROI per fitted Gaussian to the existing phasor ROI list. Each GMM-origin ROI carries its eigenstructure, so per-ROI `cov_f` (stretch) and `shift` (translate along principal axis) spinboxes regenerate `center` / `radii` from the fit rather than from the user's drag state.

The downstream "Apply Visible as Mask" flow is unchanged: each visible ROI (manual or GMM) writes to `/masks/<roi_name>` exactly as today.

---

## Problem Frame

Manual ROI placement on the phasor plot is slow, subjective, and irreproducible for datasets with multiple overlapping populations (condensed vs. dilute phase, autofluorescence vs. label, multiple metabolic states). The reference scripts at `/Users/leelab/ComplexWaveletFilter/{Circular_ROI_lifetime.py,CondensedPhaseGMM.py}` show the intended automation: filter the phasor by intensity / a tau-anchored circle / a binary mask, fit a Gaussian mixture, and place ROIs whose center, axes, and angle come from each component's mean and covariance — then refine per-cluster via cov_f scaling and shift along the principal axis. None of this exists in PerCell4 today.

(see origin: `docs/brainstorms/2026-05-03-phasor-gmm-segmentation-requirements.md`)

---

## Requirements Trace

- R1. Three new filter controls — intensity threshold + reference circle in FLIM tab; existing active-mask checkbox stays on the phasor plot toolbar. All AND-compose with the cell-selection filter and restrict both display and GMM input. (see origin R1)
- R2. "Phasor Segmentation" group in FLIM tab — Shape combo (Circle/Ellipse), N-clusters (Auto-with-BIC/AIC or fixed n), cov_f, shift, "Run GMM" button. (see origin R2)
- R3. GMM fit + ROI placement — sklearn GaussianMixture on a max-100k subsample of valid pixels weighted by intensity, BIC/AIC sweep when Auto, append-only into the existing ROI list. (see origin R3)
- R4. Per-ROI shift / stretch — cov_f and shift spinboxes in the Selected-ROI panel for GMM-origin ROIs, regenerate center/radii from stored eigenstructure. Hidden for manual ROIs. (see origin R4)
- R5. PhasorROI gains `origin` + `GMMFit` fields, JSON round-trip extends with backward-compatible defaults. (see origin R5)
- R6. Composition with existing pipelines — Apply Visible as Mask, live preview overlay, Save/Load JSON, cell-selection filter, wavelet-Filtered checkbox all unchanged. (see origin R6)
- R7. Performance — filter composition is one boolean AND each. GMM runs on a `QThread` worker so the UI stays responsive; full BIC sweep (n=2..n_max) is ≤ a few seconds at the 100k-pixel cap (default n_max=4 keeps it tight; users can opt up to n_max=6+ at additional latency). (see origin R7, doc-review F8 perf calibration)

---

## Scope Boundaries

### Deferred for later

Carried from origin — product/version sequencing:

- Cell-aware GMM input beyond `session.filter_ids` (already AND-composes via `compute_valid_phasor_pixels`).
- Per-cluster shape choice (current scope is one shape per run).
- GMM without subsampling above the 100k pixel cap.
- Save GMM fit metadata to HDF5 alongside masks (JSON ROI Save/Load is enough).
- "Remove all GMM ROIs" button (the new `origin` field makes this trivial later).
- GMM `covariance_type` modes beyond `full`.
- Reference-circle anchor by direct `(G, S)` (tau-only input).

### Outside this product's identity

Carried from origin — positioning rejection:

- A new ROI editor UI parallel to the phasor plot's (GMM ROIs flow through the existing list).
- Server-side / batch GMM segmentation (interactive desktop tool only).
- Direct cluster-membership masks bypassing the ROI step (users wanting full coverage can set `cov_f = 5+`).

### Deferred to Follow-Up Work

Plan-local — none. The seven units below land together as a coherent feature.

---

## Context & Research

### Relevant Code and Patterns

- **Use case shape** — `src/percell4/application/use_cases/compute_phasor.py` and `apply_wavelet.py`. Canonical layout: `@dataclass` result type at module top, class with `__init__(repo, session)`, `execute(...)` reads `handle = session.dataset` (raises `NoDatasetError` if `None`), reads inputs via `repo.read_array(handle, "phasor/<ch>/g")`, calls pure domain functions, returns the result dataclass. Heavy imports (e.g., `from percell4.domain.flim.wavelet_filter import ...`) live inside `execute()` to satisfy import-linter and keep import time low. Use the `_read_fresh_metadata` helper pattern (`compute_phasor.py:42-57`) to defeat the in-session staleness vector when reading FLIM frequency.
- **QThread workers** — `src/percell4/gui/workers.py`. The generic `Worker(QThread)` class is the canonical surface; callers pass a callable + args (`Worker(run_cellpose, image, ...)`) and connect `finished`/`progress`/`error` signals. **Subclassing is not the convention.** `error` emits `WorkerError` (from `percell4.workflows.diagnostics`), not a plain string. Caller must hold a reference (`self._worker`) to prevent GC mid-thread; reference at `gui/segmentation_panel.py:265-270`.
- **FLIM task panel** — `src/percell4/interfaces/gui/task_panels/flim_panel.py`. `__init__` takes `data_model: CellDataModel` plus several `Callable` callbacks (`get_repo`, `get_viewer_window`, `get_phasor_window`, etc.) — **no launcher reference**. `_build_ui()` lays out vertical `QGroupBox` sections; handlers import use cases lazily; status messages flow through `self._show_status(...)`.
- **PhasorPlotWindow ROI machinery** — `src/percell4/interfaces/gui/peer_views/phasor_plot.py`. `_ROIWidget` bundles `(roi, curve, phasor_roi, cached_mask)`. ROI movement signal connects via `lambda _r, _w=widget: self._on_roi_moved_widget(_w)` (identity capture, not index — survives removal/renumbering). Each ROI has a `pg.RectROI` + `pg.PlotCurveItem` pair. `_preview_timer` (100 ms) and `_filter_timer` (150 ms) coalesce updates.
- **Pure-pixel filter composition** — `src/percell4/domain/flim/phasor_display.py:compute_valid_phasor_pixels`. Current signature: `(g_flat, s_flat, *, labels_flat, filter_ids, mask_flat) -> NDArray[np.bool_]`. AND-composes finiteness, cell-selection (`np.isin(labels_flat, list(filter_ids))`), and mask (`mask_flat.astype(bool)`). Mask shape mismatch is silently bypassed in the pure function; the caller surfaces a status message. The plan extends this signature.
- **Closed-form universal-circle anchor** — for the reference-circle filter, prefer the closed form `G_c = 1 / (1 + (ωτ)²)`, `S_c = ωτ / (1 + (ωτ)²)` with `ω = 2π × harmonic × freq_mhz × 10⁶`. The `scipy.minimize` solver in `CondensedPhaseGMM.calculate_g_and_s` is overkill for this exact case.
- **Existing sklearn precedent** — `src/percell4/domain/measure/grouper.py:92,124,145` already uses `from sklearn.mixture import GaussianMixture` with the lazy-import-inside-function pattern. Mirror this in the new pure GMM helpers.
- **DTCWT NumPy-2 shim** — `src/percell4/domain/flim/wavelet_filter.py` head guards. Do not remove; new FLIM domain code that imports `dtcwt` (transitively or otherwise) relies on this file having been imported first.

### Institutional Learnings

(All paths below are repo-relative under `docs/solutions/`.)

- **`logic-errors/phasor-roi-to-mask-api-mismatch.md`** — `phasor_roi_to_mask` takes `(g, s, *, center, radii, angle_rad)` kwargs. Never pass a `PhasorROI` positionally. Closure during `Session` unsubscribe in `closeEvent` requires `try/except ValueError`.
- **`ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`** — Pattern 3 (identity-based widget lookup), Pattern 5 (per-ROI `cached_mask` invalidation rules), Pattern 7 (array-form `setData(x=, y=)`). **Critical**: any new filter knob in the FLIM tab MUST invalidate every ROI's `cached_mask` plus `_active_mask_array` / `_active_mask_flat`.
- **`logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`** — Five compounding cache vectors between HDF5 and the screen. Use `repo.read_metadata(handle)` (not `handle.metadata.get(...)`) for any FLIM frequency / harmonic read inside the use case. Validate in-process; never via subprocess.
- **`logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`** — **Always derive intensity from `/decay/<ch>.sum(axis=-1)`, never read `/intensity[ch_idx]`.** This applies to GMM weights and to the intensity threshold filter. Add the alignment-invariant assertion to the use-case test.
- **`logic-errors/numpy-isin-fails-with-python-sets.md`** — In NumPy 2.x `np.isin(arr, python_set)` silently returns all-False. Always pass `list(...)` or `np.fromiter(...)` to `np.isin`.
- **`architecture-decisions/session-bridge-event-forwarding.md`** — New filter values are GUI-local, not Session state. Do not route them through `StateChange` / `CellDataModel`. Push directly from FlimPanel into PhasorPlotWindow via a public method.
- **`architecture-patterns/gui-action-contract-exhaustiveness.md`** — Selector / Creator / Action taxonomy is enforced. The new filter spinboxes, "Run GMM" button, and per-ROI cov_f/shift spinboxes are all **Actions** (no Session writes). Apply Visible as Mask is the existing Creator. Update `docs/audits/gui-element-classification.yaml`.
- **`architecture-patterns/session-to-napari-one-way-push.md`** — Not directly hit by this plan (GMM does not write to HDF5; the existing Apply flow handles that), but relevant when `place_gmm_rois` triggers a downstream mask write later.
- **`ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`** — Same as above; relevant only via the existing `_on_phasor_mask_applied` path, which is unchanged here.
- **`ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`** — When `_update_preview` rebuilds the `DirectLabelColormap` after GMM ROIs are added, do NOT wrap inside `events.colormap.blocker()`. The existing `_colormap_dirty` flag is the correct re-entrancy guard.
- **`architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`** — `_torn_down` flag pattern + synchronous `timer.stop()` ordering for QTimer-coalesced refreshes; `contextlib.suppress(ValueError)` for safe teardown of session subscriptions.
- **`architecture-decisions/decouple-task-panels-callback-injection.md`** — FlimPanel must keep its callback-injection contract; do not add a `launcher` reference to wire the Worker.
- **`ui-bugs/percell4-phasor-plot-axis-desync.md`** — Reapply `enableAutoSIPrefix(False)` and `disableAutoRange()` after any data refresh; pyqtgraph re-enables them on each data change. The reference-circle overlay must be in actual data coordinates G ∈ [0,1], S ∈ [0,0.5].

### External References

External research was skipped — local patterns are dense and well-established for every facet of this work (sklearn GMM precedent in `domain/measure/grouper.py`, Worker pattern in `gui/workers.py`, use-case shape across `application/use_cases/`, dataclass JSON round-trip in `phasor_plot.py`).

---

## Key Technical Decisions

- **Sklearn declared as a top-level project dependency** with a tight version range (`scikit-learn>=1.3,<2.0`) rather than relying on cellpose's transitive pull. The cellpose range is broad and could drop it; explicit deps are honest about runtime requirements. NOT placed in `[flim]` extras — `[flim]` is reserved for `dtcwt` (gated install with an `ImportError` UX); GMM segmentation is core FLIM analysis and there is precedent in `domain/measure/grouper.py`.
- **Universal-circle anchor uses the closed form**, not the iterative `scipy.minimize` from the reference script. `(G_c, S_c) = (1/(1+(ωτ)²), ωτ/(1+(ωτ)²))` is exact, faster, and has no convergence failure mode. The pure helper raises `ValueError` for `tau_ns < 0`.
- **GMM intensity weighting via subsampling, not `np.repeat`**. The reference script's `np.repeat(G, intensity_weights)` blows up to billions of entries at typical photon counts. We sample `min(N_valid, MAX_GMM_PIXELS=100_000)` indices weighted by intensity (`np.random.default_rng(seed=0).choice` with `p=intensity/intensity.sum()`), giving stable behavior under typical dataset sizes. **Known property** (carried from the reference scripts): intensity weighting biases the fit toward bright pixels — clusters dominated by a small number of very bright pixels can crowd out diffuse populations. Documented; not mitigated in v1 (origin Scope: deferred).
- **GMM placement mutates only local UI state.** "Run GMM" appends entries to `PhasorPlotWindow._roi_widgets`; it does not write to HDF5 and does not mutate any of the five Session selection fields (`active_*`, `filter_ids`, `selection`). Under the Selector/Creator/Action taxonomy this is an **Action** (no Session writes). The existing Apply Visible as Mask flow remains the Creator. (U7 audit entries for new widgets carry a one-line note explaining that `gmm_fit` mutation lives on `PhasorROI`, not Session.)
- **GMMFit metadata stored on the dataclass, drives cov_f/shift recomputation.** Spinboxes recompute `radii` from stored `(lambda_major, lambda_minor, principal_angle_rad)` and recompute `center` from the **current** `phasor_roi.center` plus a delta along the principal axis — i.e., dragging the ROI is **preserved** across cov_f / shift edits. The "Reset to fit" button is the explicit affordance for snapping back to the cluster mean (`mean_g`, `mean_s`). This change versus the original "snap-back-on-spinbox" design eliminates a UX surprise (origin Doc-Review A12 / D4).
- **Filter values are GUI-local, not Session state.** The new intensity threshold and reference-circle params live on `FlimPanel` and push into `PhasorPlotWindow` via a new public method (`set_phasor_filters`). They do NOT propagate as Session events. (Origin: brainstorm R1; reinforced by `architecture-decisions/session-bridge-event-forwarding.md` — Session events cost a sweep across every panel; GUI-local filters do not justify it.)
- **GMM-origin ROIs default `cov_f = 2.0`, `shift = 0.0`**, matching the reference scripts' working defaults. Initial radii: ellipse uses `(cov_f × √λ_major, cov_f × √λ_minor)`; circle uses `(cov_f × √λ_minor, cov_f × √λ_minor)` (matches `Circular_ROI_lifetime.py:169` — inscribed in the cluster's minor extent). Eigenvalues are clamped to `max(λ, 1e-6 × trace(cov))` so singular / near-singular covariance does not produce zero-radius ROIs.
- **Auto BIC/AIC sweep starts at `n=2`** (not `n=1`). A single Gaussian over the whole filtered phasor is never useful as an ROI. When the user wants `n=1` they can disable Auto and pick it manually.
- **GMM result carries the dataset identity it was computed on.** `RunPhasorGMMResult` includes a `dataset_path: Path` and `dataset_path_token: str` snapshot. `_on_gmm_finished` discards the result and shows a status message when the current `session.dataset.path` no longer matches. This protects against dataset switches mid-flight (origin Doc-Review A1).
- **Cell-selection filter applies to GMM input.** `RunPhasorGMM.execute` reads `session.active_segmentation` labels via `repo.read_labels` and passes `labels_flat` to `compute_valid_phasor_pixels`, satisfying brainstorm R6 in v1 rather than deferring it. Tolerates missing segmentation as no-cell-filter.
- **`Worker.progress` is not used** for the BIC/AIC sweep. The generic `Worker(QThread)` cannot inject a progress callback into the wrapped use case (`gui/workers.py` runs `self._fn(*args, **kwargs)` directly). Status flows: a synchronous "Running GMM…" message before `worker.start()`, then a single "GMM placed N ROIs (n=…, criterion=…)" on `finished`. Matches the simpler `segmentation_panel.py:265` pattern.
- **Eigenstructure-aware center computation, single source of truth.** `(G_c, S_c)` from `universal_circle_gs(harmonic, tau_ns, freq_mhz)` is computed in **two places** (the use case, and `PhasorPlotWindow.set_phasor_filters`) but each computes against a snapshot of `(harmonic, tau_ns, freq_mhz)` at the moment of its respective call. If metadata changes mid-session, the user must re-toggle the filter to refresh. Acceptable for v1; revisit if it becomes a UX pain point.
- **JSON gains a `schema_version` field** at the top level. `to_dict` writes `schema_version: 2`; `from_dict` reads any version and tolerates missing fields (origin handling). Old builds reading v2 JSON will detect the version and surface a one-time warning to the user that some fields will be lost on save. This protects against silent permanent data loss in mixed-version workflows (origin Doc-Review A7).

---

## Open Questions

### Resolved During Planning (post doc-review)

- **Sklearn dependency declaration** — `scikit-learn>=1.3,<2.0` in `[project.dependencies]`, not `[flim]` extras.
- **Universal-circle solver** — closed form. `tau_ns < 0` raises `ValueError`.
- **GMM weighting strategy** — subsample to 100k weighted by intensity, fixed seed 0. Bias toward bright pixels documented but not mitigated in v1.
- **"Run GMM" classification** — Action (no Session writes; only mutates UI-local `_roi_widgets` and dataclass state).
- **Filter state location** — GUI-local on FlimPanel, pushed via `set_phasor_filters` to PhasorPlotWindow.
- **Auto BIC/AIC sweep range** — starts at `n=2`. Default `n_max=4` (originally 6; doc-review F8 flagged the perf claim as optimistic — 4 is a tighter default and the user can opt into 6).
- **Drag preservation under cov_f/shift edits** — spinbox recompute uses the **current** `phasor_roi.center` as the anchor (drag preserved); only "Reset to fit" returns to the cluster mean.
- **Cell-selection filter application** — implemented in v1 via `repo.read_labels(handle, session.active_segmentation)` inside the use case.
- **`Worker.progress` for the GMM sweep** — not used; the generic Worker cannot route progress from the wrapped callable. Single before/after status messages instead.
- **Mid-flight dataset switch protection** — `RunPhasorGMMResult` includes `dataset_path` snapshot; `_on_gmm_finished` discards the result on mismatch.
- **`flim_frequency_mhz` missing** — FlimPanel disables the reference-circle checkbox + spinboxes when freq is absent; PhasorPlotWindow's `set_phasor_filters` early-returns the ref-circle path when freq is None. Reference-circle filter requires freq for both display and use case.
- **JSON schema_version** — `to_dict` writes `schema_version: 2`. Old builds loading v2 JSON warn the user.
- **10-ROI cap on `place_gmm_rois`** — pre-checked: `n_total = len(_roi_widgets) + len(geometries); if n_total > 10:` truncate to fit and surface a status message naming the truncation.
- **COLOR_CYCLE indexing** — GMM ROI color is `COLOR_CYCLE[(len(_roi_widgets) + i) % len(COLOR_CYCLE)]`, continuing the global cycle.
- **Eigenvalue clamping** — `gmm_to_phasor_roi_geometry` enforces `λ ≥ max(1e-6 × trace(cov), 1e-9)` to prevent zero-radius ROIs on near-singular covariance.
- **`Reset to fit` button placement** — inline below the cov_f and shift spinboxes, in the Selected-ROI panel; visible only for GMM-origin ROIs.
- **Auto/Criterion combo behavior** — `setEnabled(False)` (NOT `setVisible(False)`) when Auto is unchecked. Layout stays put; users see the disabled control.
- **Spinbox signal type** — all filter spinboxes (intensity threshold, ref-circle tau, ref-circle radius) connect via `valueChanged` (not `editingFinished`) so the existing `_filter_timer` 150ms debounce coalesces rapid changes. Same pattern for cov_f / shift.
- **Reference-circle viewport handling** — when the radius would push the circle outside the existing `S=[0, 0.7]` plot range, clip the curve points to the viewport before `setData`. The filter still applies to every pixel; only the visualization is clipped. (Default tau=2.5ns + radius=0.5 + freq=80MHz puts top-of-circle at S≈0.99, well outside the range.)
- **Cov_f / shift spinbox ranges** — Selected-ROI panel spinboxes use **the same ranges** as the Phasor Segmentation panel (cov_f 0.5–5.0, shift -2.0 to 2.0, both step 0.1, 1-decimal display) — eliminates the brainstorm-vs-plan range discrepancy doc-review D7 flagged. Tighter range than originally documented (was 0.1–10.0 in U4); reasoning: outside this range, ROIs are usually nonsensical for FLIM data.

### Deferred to Implementation

- **Use case test fixtures for synthetic 2-cluster phasor data** — exact mean/cov choices settle when writing the tests (U1).
- **`enableAutoSIPrefix(False)` re-application points** — verify whether the existing `_refresh_histogram` already covers all paths the reference-circle overlay touches; add explicit calls in `set_phasor_filters` if not.
- **WorkerError public surface** — the U6 error handler reads `err.message`; verify against `percell4.workflows.diagnostics.WorkerError` during implementation.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
FlimPanel filter widgets (intensity, ref-circle)
  ↓ (GUI-local push, never via Session)
FlimPanel._push_filters_to_phasor_plot
  ├── if phasor_win is None: return  ← defensive guard
  └── phasor_win.set_phasor_filters(intensity_threshold, ref_circle_tau_ns, ref_circle_radius)
       ├── if tau_ns and freq_mhz None: status + skip ref-circle  ← P0 guard
       ├── compute (G_c, S_c) = universal_circle_gs(harmonic, tau_ns, freq_mhz)
       ├── update reference-circle PlotCurveItem (clipped to S∈[0,0.7])
       ├── invalidate every _ROIWidget.cached_mask
       ├── invalidate _active_mask_array / _active_mask_flat
       └── _filter_timer.start()  (150ms debounce)
            ↓
            _refresh_histogram
              ↓
              compute_valid_phasor_pixels(
                g_flat, s_flat,
                labels_flat, filter_ids,            ← existing
                mask_flat,                          ← existing
                intensity_flat, intensity_threshold,← NEW
                ref_circle_center, ref_circle_radius← NEW
              )

FlimPanel._on_run_gmm
  ├── if _gmm_worker.isRunning(): return  ← double-click race guard
  ├── if active_channel is None: status + return
  ├── if phasor_win is None: status + return
  ├── kwargs = {channel, shape, n_components, criterion, n_max, cov_f, shift,
  │             intensity_threshold, ref_circle_tau_ns, ref_circle_radius,
  │             mask_filter_active, use_filtered_gs, harmonic}
  └── self._gmm_worker = Worker(uc.execute, **kwargs); start()
       ↓
       uc = RunPhasorGMM(repo, session)
         ├── handle = session.dataset                          (NoDatasetError if None)
         ├── dataset_path = handle.path                         ← snapshot for mismatch check
         ├── g, s = repo.read_array("phasor/<ch>/{g,s} or g_filtered,s_filtered")
         ├── intensity = repo.read_array("decay/<ch>").sum(-1, dtype=float64).astype(float32)
         │     ↑ NEVER /intensity (alignment invariant)
         ├── labels = repo.read_labels(handle, session.active_segmentation) if seg else None
         ├── (G_c, S_c) = universal_circle_gs(harmonic, tau, freq_mhz)
         │     freq_mhz from repo.read_metadata(handle); raise ValueError if missing+ref_active
         ├── valid = compute_valid_phasor_pixels(g, s, labels, filter_ids, mask, intensity, ...)
         ├── (g_v, s_v, w_v) = subsample(valid, max_pixels=100_000, seed=0)
         ├── components = gmm_fit_phasor(g_v, s_v, w_v, n, criterion, n_min=2, n_max)
         └── return RunPhasorGMMResult(geometries, ..., dataset_path)
  ↓ (worker.finished signal on UI thread)
FlimPanel._on_gmm_finished(result)
  ├── if result.dataset_path != session.dataset.path: status + return  ← P0 mismatch check
  ├── if phasor_win is None: return
  └── phasor_win.place_gmm_rois(geometries, shape, criterion, sampled_pixels)
       ├── if _g_map is None: status + return
       ├── available = 10 - len(_roi_widgets)
       │   if available <= 0: status "ROI list full"; return
       │   truncated = geometries[:available]
       └── for (i, geo) in enumerate(truncated):
             color = COLOR_CYCLE[(len(_roi_widgets) + i) % len(COLOR_CYCLE)]
             gmm_fit = GMMFit(..., cov_f=2.0, shift=0.0)
             phasor_roi = PhasorROI(origin="gmm", gmm_fit=gmm_fit, ...)
             _create_roi_widget(phasor_roi)        ← R3 "only append"

  ↓ (worker.error signal on UI thread)
FlimPanel._on_gmm_error(err)
  ├── re-enable Run GMM button
  └── status f"GMM error: {err.message}"           ← place_gmm_rois NOT called
```

```
Selected-ROI panel (existing) + new spinboxes (R4)
┌─────────────────────────────────────┐
│ Name:  [GMM_2          ]            │
│ Angle: [   17°]                     │
│ cov_f: [2.0]   ◀── enabled if GMM   │
│ Shift: [0.0]   ◀── enabled if GMM   │
│ [Reset to fit]  ◀── enabled if GMM  │
│ ☑ Visible                           │
└─────────────────────────────────────┘
                                   GMM-only spinboxes drive (drag-preserving):
   (mean_g, mean_s, λ_major, λ_minor, θ) ← stored in PhasorROI.gmm_fit (constant)
   anchor = phasor_roi.center             ← preserves manual drag
   Δ      = shift × √λ_major × (cos θ, sin θ)
   center = anchor + Δ
   radii  = ellipse: (cov_f × √λ_major, cov_f × √λ_minor)
            circle:  (cov_f × √λ_minor, cov_f × √λ_minor)  (inscribed in cluster minor extent)
   angle_deg = θ × 180/π   (ellipse only; 0 for circle)

   "Reset to fit" overrides anchor → (mean_g, mean_s).
   RectROI.blockSignals(True/False) wraps the programmatic setPos/setSize.
```

---

## Implementation Units

- U1. **Pure GMM helpers in `domain/flim/phasor.py` + sklearn dep**

**Goal:** Add the pure numpy/sklearn functions that drive the GMM workflow without any Qt or h5py coupling, and declare scikit-learn as an explicit project dependency.

**Requirements:** R3, R4

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/flim/phasor.py`
- Modify: `pyproject.toml` (add `scikit-learn>=1.3` to `[project.dependencies]`)
- Test: `tests/test_flim/test_phasor_gmm.py`

**Approach:**
- New pure functions, all numpy in/out, sklearn lazy-imported inside the function bodies (matches `domain/measure/grouper.py:92` precedent — parity choice, not an import-linter requirement; sklearn is not in the application/ contract's `forbidden_modules`):
  - `universal_circle_gs(harmonic: int, tau_ns: float, freq_mhz: float) -> tuple[float, float]` — closed form `(G_c, S_c) = (1/(1+x²), x/(1+x²))` with `x = 2π × harmonic × freq_mhz × 1e6 × tau_ns × 1e-9`. Raises `ValueError` for `tau_ns < 0`.
  - `gmm_eigenstructure(cov_matrix: NDArray[np.floating]) -> tuple[float, float, float]` — returns `(lambda_major, lambda_minor, principal_angle_rad)` from `np.linalg.eigh`. Eigenvalues clamped: `λ_minor = max(λ_minor, 1e-6 × trace(cov), 1e-9)`. Prevents zero-radius ROIs on near-singular covariance.
  - `gmm_to_phasor_roi_geometry(mean: tuple[float,float], lambda_major: float, lambda_minor: float, principal_angle_rad: float, cov_f: float, shift: float, shape: str, anchor: tuple[float, float] | None = None) -> tuple[tuple[float,float], tuple[float,float], float]` — returns `(center, radii, angle_deg)`. The shift translation `Δ = shift × √λ_major × (cos θ, sin θ)` is applied to `anchor` (defaults to `mean` for first placement; the per-ROI spinbox handler passes `anchor=phasor_roi.center` to preserve a manual drag). Encapsulates the shift-along-principal-axis math from `CondensedPhaseGMM.segmentation_ROI_parameters`.
  - `gmm_fit_phasor(g: NDArray, s: NDArray, intensity: NDArray, n_components: int | None, criterion: str | None, n_min: int = 2, n_max: int = 4, max_pixels: int = 100_000, random_seed: int = 0) -> GMMFitResult` — subsamples (intensity-weighted) up to `max_pixels`, fits sklearn `GaussianMixture` (one fit if `n_components` is set; sweep `n_min..n_max` and pick lowest BIC/AIC otherwise). The default `n_min=2` reflects the rule that a single Gaussian is never useful as a phasor ROI. `n_max=4` is the tighter default informed by perf observations; the FLIM panel exposes a spinbox to raise it to 6+. Returns `@dataclass GMMFitResult(means, covariances, chosen_n, criterion_value, sampled_pixels)`.
- The `GMMFitResult` dataclass lives in `domain/flim/phasor.py` so the use case (which also lives outside Qt) can construct it without circular imports.
- Edge cases the function must handle:
  - `n_valid_pixels < n_components` (or `< n_min` in auto mode) → raise `ValueError("Not enough valid pixels for n=… clusters")`.
  - Constant-intensity input (sum == 0) → fall back to uniform sampling (`rng.choice` without `p=`).
  - `criterion not in {"BIC", "AIC", None}` → raise `ValueError`.
  - `n_components < 1` → `ValueError`.
  - `tau_ns < 0` (in `universal_circle_gs`) → `ValueError`.

**Patterns to follow:**
- `src/percell4/domain/measure/grouper.py:92,124,145` — lazy `from sklearn... import ...` pattern.
- `src/percell4/domain/flim/phasor.py:compute_phasor` — `@dataclass` result types at module top, `from __future__ import annotations`, PEP 585 lowercase generics, `NDArray[np.floating]`.

**Test scenarios:**
1. Happy path: `universal_circle_gs(harmonic=1, tau_ns=2.5, freq_mhz=80.0)` returns `(G_c, S_c)` lying on the universal circle (assert `(G_c-0.5)² + S_c² ≈ 0.25` to 1e-9 tolerance).
2. Happy path: `universal_circle_gs(harmonic=1, tau_ns=0.0, freq_mhz=80.0)` returns `(1.0, 0.0)` (zero lifetime → DC).
3. Edge case: `universal_circle_gs(harmonic=1, tau_ns=1e6, freq_mhz=80.0)` returns near-`(0.0, 0.0)` (infinite lifetime → origin).
4. Error path: `universal_circle_gs(harmonic=1, tau_ns=-1.0, freq_mhz=80.0)` raises `ValueError`.
5. Happy path: `gmm_eigenstructure` on a known covariance `[[4, 1], [1, 1]]` returns `lambda_major ≈ 4.30`, `lambda_minor ≈ 0.70`, angle within `[-π, π]` matching the dominant eigenvector.
6. Edge case: `gmm_eigenstructure` on a rank-1 covariance `[[1, 1], [1, 1]]` returns `lambda_minor` clamped to ≥ 1e-9 (not zero) — protects downstream radius computation.
7. Happy path: `gmm_to_phasor_roi_geometry` with `shape="ellipse"`, `cov_f=2`, `shift=0`, `anchor=None` returns `radii = (2√λ_major, 2√λ_minor)` and `center == mean`.
8. Happy path: `gmm_to_phasor_roi_geometry` with `shape="ellipse"`, `cov_f=2`, `shift=0.5`, `anchor=None` returns `center` displaced from `mean` by `0.5 × √λ_major × (cos θ, sin θ)` and unchanged radii.
9. Happy path (drag preservation): `gmm_to_phasor_roi_geometry(mean=(0.4,0.3), shift=0.5, anchor=(0.5, 0.35))` returns `center` displaced from `(0.5, 0.35)` (NOT from `mean`) by `0.5 × √λ_major × (cos θ, sin θ)`.
10. Happy path: `gmm_to_phasor_roi_geometry` with `shape="circle"` returns `radii = (cov_f × √λ_minor, cov_f × √λ_minor)` and `angle_deg == 0`.
11. Happy path: `gmm_fit_phasor` on a synthetic 2-cluster mix with `n_components=2` recovers means within 0.02 of truth (cosine-similar covariance via `np.allclose(cov, truth, atol=0.02)`).
12. Happy path: `gmm_fit_phasor` with `criterion="BIC"`, `n_min=2, n_max=4` on a synthetic 2-cluster mix returns `chosen_n == 2`.
13. Happy path: `gmm_fit_phasor` with `criterion="BIC"`, `n_min=2, n_max=4` on a degenerate 1-cluster mix returns `chosen_n == 2` (because `n_min=2`); fitter does its best to find substructure.
14. Happy path: `gmm_fit_phasor` with explicit `n_components=1` is allowed (manual override via the FLIM panel) and returns 1 component.
15. Edge case: `gmm_fit_phasor` with constant intensity (all zeros) falls back to uniform sampling and still produces a valid fit.
16. Edge case: `gmm_fit_phasor` reproducibility — two calls with `random_seed=0` produce identical means.
17. Edge case: `gmm_fit_phasor` with `max_pixels=200` (test-time override) on a 50k-pixel input subsamples deterministically.
18. Edge case: `gmm_fit_phasor` with peak-intensity dataset (1k bright pixels at 10⁵ photons + 100k diffuse at 10²) — the test asserts the documented bias toward bright clusters (means recovered match the bright population, not the diffuse), establishing this as known behavior rather than a regression.
19. Error path: `gmm_fit_phasor` with `n_valid_pixels=10` and `n_components=20` raises `ValueError`.
20. Error path: `gmm_fit_phasor(criterion="XYZ")` raises `ValueError`.
21. Error path: `gmm_fit_phasor(n_components=0)` raises `ValueError`.

**Verification:**
- `pytest tests/test_flim/test_phasor_gmm.py -q` passes with all scenarios green.
- `python -c "import percell4.domain.flim.phasor"` does NOT import sklearn at module load (verify with `sys.modules` snapshot before/after).
- `import-linter` clean (`lint-imports` if configured) — domain layer has no top-level Qt/h5py/sklearn imports.

---

- U2. **Extend `compute_valid_phasor_pixels` for intensity threshold + reference circle**

**Goal:** Add two new filter axes to the pure pixel-validity composer so the histogram and GMM share one composition pipeline.

**Requirements:** R1

**Dependencies:** None (independent of U1)

**Files:**
- Modify: `src/percell4/domain/flim/phasor_display.py`
- Test: `tests/test_flim/test_phasor_display.py` (extend, do not replace)

**Approach:**
- Extend the keyword-only signature:
  ```
  compute_valid_phasor_pixels(
      g_flat, s_flat, *,
      labels_flat, filter_ids,
      mask_flat,
      intensity_flat: NDArray[np.floating] | None = None,    # NEW
      intensity_threshold: float = 0.0,                       # NEW
      ref_circle_center: tuple[float, float] | None = None,   # NEW
      ref_circle_radius: float | None = None,                 # NEW
  ) -> NDArray[np.bool_]
  ```
- New filter steps (each ANDed onto `valid`):
  - Intensity: when `intensity_flat is not None and intensity_threshold > 0`, `valid &= (intensity_flat >= intensity_threshold)`. Silent shape-mismatch bypass (matches existing mask behavior — `intensity_flat.size != g_flat.size` → skip).
  - Reference circle: when `ref_circle_center is not None and ref_circle_radius is not None`, `valid &= ((g_flat - g_c)**2 + (s_flat - s_c)**2 <= ref_circle_radius**2)`.
- All defaults preserve today's behavior — call sites that don't pass the new kwargs are unaffected.
- Re-confirm `np.isin(labels_flat, list(filter_ids))` (NOT `np.isin(..., filter_ids_set)`) per `numpy-isin-fails-with-python-sets.md`.

**Patterns to follow:**
- `src/percell4/domain/flim/phasor_display.py` existing structure — keyword-only after the first two positional, `None`/`0` sentinels for "no filter", silent shape-mismatch bypass.
- Test file `tests/test_flim/test_phasor_display.py` — pure numpy fixtures, one test per filter combination.

**Test scenarios:**
- Happy path: intensity threshold alone — pixels above `threshold` retained; `intensity_flat=None` is a pure no-op.
- Happy path: reference circle alone — pixels inside the circle retained, outside excluded; `ref_circle_radius=None` is a pure no-op.
- Edge case: intensity_flat shape mismatch — silently bypassed, returns same as `intensity_flat=None`.
- Edge case: `intensity_threshold=0.0` is a no-op even if `intensity_flat` is provided.
- Edge case: `ref_circle_radius=0` excludes all pixels (degenerate circle).
- Integration: all five filters AND-composed (cell-selection + mask + intensity + ref-circle + finiteness) — pixel must satisfy all five to be valid.
- Integration: cell-selection + intensity composition still passes `list(filter_ids)` to `np.isin` (no regression on `numpy-isin-fails-with-python-sets`).
- Edge case: empty `frozenset()` for `filter_ids` excludes all pixels (preserves existing semantics).

**Verification:**
- `pytest tests/test_flim/test_phasor_display.py -q` passes with new + existing scenarios.
- Existing call site at `phasor_plot.py:789` continues to work with no kwargs change required (only added kwargs, all optional).

---

- U3. **`RunPhasorGMM` use case**

**Goal:** Encapsulate the read-decay → derive intensity → compose filters → subsample → fit GMM → return geometries pipeline as a Qt-free, testable use case.

**Requirements:** R3, R7

**Dependencies:** U1, U2

**Files:**
- Create: `src/percell4/application/use_cases/run_phasor_gmm.py`
- Test: `tests/test_use_cases.py` (add `TestRunPhasorGMM` class)

**Approach:**
- Module-level `from __future__ import annotations`, `logger = logging.getLogger(__name__)`.
- `@dataclass class PhasorROIGeometry: center, radii, angle_deg, mean_g, mean_s, lambda_major, lambda_minor, principal_angle_rad, label`.
- `@dataclass class RunPhasorGMMResult: geometries, chosen_n, criterion, criterion_value, sampled_pixels, dataset_path: Path` — `dataset_path` is the snapshot of `session.dataset.path` at execute-time, used by `_on_gmm_finished` to detect a mid-flight dataset switch.
- Class shape:
  ```
  class RunPhasorGMM:
      def __init__(self, repo: DatasetRepository, session: Session) -> None: ...
      def execute(
          self, *,
          channel: str,
          shape: str,                         # "circle" | "ellipse"
          n_components: int | None,           # None when auto
          criterion: str | None,              # "BIC" | "AIC" | None
          cov_f: float = 2.0,
          shift: float = 0.0,
          intensity_threshold: float = 0.0,
          ref_circle_tau_ns: float | None = None,
          ref_circle_radius: float | None = None,
          mask_filter_active: bool = False,
          use_filtered_gs: bool = True,
          harmonic: int = 1,                  # passed in by FlimPanel (read from phasor data attrs or active harmonic combo)
          n_max: int = 4,                     # auto-mode upper bound from the FLIM tab spinbox
      ) -> RunPhasorGMMResult: ...
  ```
- `execute()`:
  1. `handle = self._session.dataset` — raise `NoDatasetError` if `None` (`from percell4.domain.errors import NoDatasetError`). Snapshot `dataset_path = handle.path` for the result.
  2. Read `(g, s)`:
     - When `use_filtered_gs=True`: read `phasor/<ch>/g_filtered`, `s_filtered`. Catch `KeyError` and raise `ValueError("Wavelet-filtered phasor not found for channel '<ch>'. Apply Wavelet Filter first or uncheck Filtered.")`.
     - When `use_filtered_gs=False`: read `phasor/<ch>/g`, `s`. Catch `KeyError` and raise `ValueError("Phasor data not found for channel '<ch>'. Compute Phasor first.")`.
  3. Read `decay = repo.read_array(handle, f"decay/{channel}")`. Compute `intensity = decay.sum(axis=-1, dtype=np.float64).astype(np.float32)` — float64 intermediate prevents precision loss on bright pixels (sums > 2²⁴), final cast to float32 saves memory for downstream sampling. **Never read `/intensity[ch_idx]`** (origin: `flim-phasor-cross-layer-alignment-2026-04-29.md`).
  4. Read mask via `repo.read_mask(handle, self._session.active_mask)` if `mask_filter_active and self._session.active_mask is not None`; tolerate `KeyError` / shape mismatch with `mask_flat=None`.
  5. Read segmentation labels for the cell-selection filter: `seg_name = self._session.active_segmentation; labels = repo.read_labels(handle, seg_name) if seg_name else None`. Tolerate `KeyError` → `labels = None`. This satisfies brainstorm R6 "Cell-selection filter — composes via the same boolean AND".
  6. Compute `(G_c, S_c)`:
     - Get `freq_mhz = repo.read_metadata(handle).get("flim_frequency_mhz")` (fresh read; not `handle.metadata` to defeat in-session staleness — see `compute_phasor.py:42-57`'s `_read_fresh_metadata` pattern).
     - When `ref_circle_tau_ns is not None`:
       - If `freq_mhz is None`, raise `ValueError("Reference-circle filter requires flim_frequency_mhz in dataset metadata")`.
       - Compute `(G_c, S_c) = universal_circle_gs(harmonic, ref_circle_tau_ns, freq_mhz)`.
     - Else `(G_c, S_c) = (None, None)`.
  7. Compute `valid = compute_valid_phasor_pixels(g.ravel(), s.ravel(), labels_flat=labels.ravel() if labels is not None else None, filter_ids=self._session.filter_ids, mask_flat=mask_flat, intensity_flat=intensity.ravel(), intensity_threshold=intensity_threshold, ref_circle_center=(G_c, S_c) if G_c is not None else None, ref_circle_radius=ref_circle_radius)`.
  8. Apply the boolean mask, then call `gmm_fit_phasor(g_valid, s_valid, intensity_valid, n_components, criterion, n_min=2, n_max=n_max)`.
  9. Map `(means_, covariances_)` → list of `PhasorROIGeometry` via `gmm_eigenstructure` + `gmm_to_phasor_roi_geometry(anchor=None)` (per component, with the shared `shape`, `cov_f`, `shift`). `label = i + 1` per component (used by PhasorPlotWindow as the integer label for `_compute_combined_mask`).
  10. Return `RunPhasorGMMResult(geometries=…, chosen_n=…, criterion=…, criterion_value=…, sampled_pixels=…, dataset_path=dataset_path)`.
- Lazy imports inside `execute()`: `from percell4.domain.flim.phasor import gmm_fit_phasor, gmm_eigenstructure, gmm_to_phasor_roi_geometry, universal_circle_gs`.

**Patterns to follow:**
- `src/percell4/application/use_cases/compute_phasor.py` — `__init__(repo, session)`, `_read_fresh_metadata` helper, `NoDatasetError` raise pattern, lazy domain imports inside `execute()`.
- `src/percell4/application/use_cases/apply_wavelet.py` — `KeyError → ValueError` translation with a user-actionable message.
- `tests/test_use_cases.py:TestComputePhasorFreshMetadata` (line 274) — `FakeRepo` harness; mirror this directly.

**Test scenarios:**
1. Happy path: 2-cluster synthetic dataset, `n_components=2`, `shape="ellipse"` → returns 2 geometries with means matching the truth.
2. Happy path: same dataset with `criterion="BIC"`, `n_components=None`, `n_max=4` → returns `chosen_n=2`.
3. Happy path: `shape="circle"` → all geometries have equal radii on both axes (`radii[0] == radii[1]`).
4. Happy path: `RunPhasorGMMResult.dataset_path == handle.path` — snapshot is captured.
5. Integration: alignment invariant — `intensity` is derived from `decay.sum(axis=-1, dtype=float64)`, NOT from `/intensity`. Test by writing different values to `/intensity[0]` vs. `decay.sum(-1)` in the FakeRepo and asserting GMM uses the decay-derived weights (origin: `flim-phasor-cross-layer-alignment-2026-04-29.md` Prevention #5).
6. Integration: cell-selection filter — set `session.active_segmentation = "seg1"` and `session.set_filter_ids({1})`; only pixels labeled 1 contribute to the GMM. Verify by comparing `chosen_n` and `means_` against unfiltered fit on the same data.
7. Integration: `use_filtered_gs=False` reads `phasor/<ch>/g` (unfiltered); `use_filtered_gs=True` reads `g_filtered`. Verify both paths.
8. Integration: `_read_fresh_metadata` path — write a different `flim_frequency_mhz` into `repo.metadata` after `set_dataset` (in the FakeRepo); verify the use case sees the fresh value, not a stale snapshot (origin: `in-session-hdf5-staleness-multi-vector`).
9. Integration: float64 intermediate — write a `decay` array where `decay.sum(-1)` exceeds 2²⁴ for some pixels (e.g. uint16 with peak counts × 256 bins); verify the use case's intensity matches `decay.sum(axis=-1, dtype=np.float64).astype(np.float32)` exactly.
10. Edge case: `mask_filter_active=True` with `session.active_mask=None` → mask filter silently bypassed; runs without it.
11. Edge case: `intensity_threshold` excludes too many pixels (`n_valid < n_min`) → `ValueError` propagated from `gmm_fit_phasor`.
12. Edge case: `n_components=1` (manual override) returns 1 geometry — sanity check that the use case doesn't reject it; the n_min=2 floor only applies in auto mode.
13. Error path: missing dataset → raises `NoDatasetError`.
14. Error path: missing `phasor/<ch>/g` (when `use_filtered_gs=False`) → raises `ValueError("Phasor data not found ... Compute Phasor first.")`.
15. Error path: missing `phasor/<ch>/g_filtered` (when `use_filtered_gs=True`) → raises `ValueError("Wavelet-filtered phasor not found ... Apply Wavelet Filter first or uncheck Filtered.")`.
16. Error path: `ref_circle_tau_ns=2.5` with no `flim_frequency_mhz` in metadata → raises `ValueError("Reference-circle filter requires...")`.
17. Error path: `harmonic=0` is malformed; `gmm_fit_phasor` propagates `ZeroDivisionError`/`ValueError` from `universal_circle_gs`. Confirm raises clean error.

**Verification:**
- `pytest tests/test_use_cases.py -k TestRunPhasorGMM -q` green.
- `python -c "from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM; print(RunPhasorGMM)"` — no Qt or h5py imports trigger at module load.
- `import-linter` (`lint-imports`) clean.

---

- U4. **`PhasorROI` + `GMMFit` dataclass + JSON round-trip**

**Goal:** Extend the existing `PhasorROI` dataclass with `origin` and `gmm_fit` fields backward-compatibly, so saved/loaded JSON files survive the format extension.

**Requirements:** R5, R6

**Dependencies:** None

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_gui_workflows/test_phasor_roi_json.py` (new — pure dataclass + JSON tests, no Qt fixtures needed)

**Approach:**
- Add `@dataclass class GMMFit` near `PhasorROI`:
  ```
  @dataclass
  class GMMFit:
      mean_g: float                # cluster mean (G), constant across edits — used by "Reset to fit"
      mean_s: float                # cluster mean (S), constant across edits
      lambda_major: float          # larger eigenvalue, constant
      lambda_minor: float          # smaller eigenvalue, constant
      principal_angle_rad: float   # major-eigenvector angle, constant
      cov_f: float                 # current scalar — mutable via spinbox
      shift: float                 # current scalar — mutable via spinbox
      shape: str                   # "circle" | "ellipse" (constant per ROI; per-cluster shape change deferred)
      criterion: str | None        # "BIC" | "AIC" | None (manual n)
      sampled_pixels: int          # total pixels the entire GMM run sampled — shared across all ROIs from one run

      @classmethod
      def from_dict(cls, d: dict) -> GMMFit: ...    # tolerant; raise ValueError on bad type
      def to_dict(self) -> dict: ...
  ```
- Extend `PhasorROI` with `origin: str = "manual"` and `gmm_fit: GMMFit | None = None`.
- Extend `PhasorROI.from_dict`:
  - `origin = str(d.get("origin", "manual"))` — old JSON loads as `manual`.
  - `gmm_fit_data = d.get("gmm_fit")`; if `isinstance(gmm_fit_data, dict)`, call `GMMFit.from_dict(gmm_fit_data)`, on `ValueError` log+warn and set to `None` (don't fail the whole load).
- Extend `PhasorROI.to_dict`:
  - Always emit `origin`.
  - Emit `gmm_fit: GMMFit.to_dict() if self.gmm_fit else None`.
- **JSON top-level schema versioning**:
  - `_on_save_rois` writes `{"schema_version": 2, "rois": [...]}` (current Save format is `{"rois": [...]}` — version-less is treated as v1).
  - `_on_load_rois` reads `data.get("schema_version", 1)`. If the loaded version is `> 2`, surface a one-time non-blocking warning to the user (`QMessageBox.information("ROI file from a newer build — some fields may be lost on save.")`) so mixed-version workflows do not silently lose `gmm_fit` payloads (origin Doc-Review A7).
- Per `phasor-roi-to-mask-api-mismatch.md`, `phasor_roi_to_mask` is still called with kwargs (`center=`, `radii=`, `angle_rad=`) — no positional regression.
- The `_on_load_rois` per-ROI `try/except ValueError → QMessageBox.warning + continue` pattern is preserved.

**Patterns to follow:**
- Existing `PhasorROI.from_dict`/`to_dict` (`peer_views/phasor_plot.py:62-88`) — `d.get(..., default)` defaulting, `len(...) != 2` validation, `KeyError`/`TypeError → ValueError` translation.
- `src/percell4/domain/flim/phasor.py:GMMFitResult` (created in U1) — same dataclass conventions.

**Test scenarios:**
1. Happy path: `PhasorROI.to_dict() → from_dict()` round-trip preserves all fields including `origin="manual"` and `gmm_fit=None`.
2. Happy path: `PhasorROI(origin="gmm", gmm_fit=GMMFit(...)).to_dict() → from_dict()` round-trip preserves the entire `gmm_fit` payload.
3. Happy path: top-level `schema_version: 2` round-trips on save/load.
4. Edge case: load v1 JSON (no `schema_version`, no `origin`) → ROIs load as `origin == "manual"`, `gmm_fit is None`, no warning.
5. Edge case: load v3 JSON (future version) → information dialog fires once, ROIs still load via tolerant defaulting.
6. Edge case: load JSON with `origin="gmm"` but no `gmm_fit` → `origin == "gmm"`, `gmm_fit is None` (UI must defend by hiding cov_f/shift if `gmm_fit is None`).
7. Edge case: load JSON with malformed `gmm_fit` (missing field, wrong type) → `gmm_fit is None`, `origin` preserved, no exception thrown.
8. Error path: `PhasorROI.from_dict({"name": "X"})` (missing `center`/`radii`) raises `ValueError`.
9. Integration: real fixture file from a previous version (no `origin`, no `schema_version`) loads cleanly into a list of all-manual ROIs.

**Verification:**
- `pytest tests/test_gui_workflows/test_phasor_roi_json.py -q` green.
- Manual smoke: load an old `phasor_rois.json` (saved before this change) via the existing `_on_load_rois` flow — no errors, all ROIs marked manual.

---

- U5. **PhasorPlot: `place_gmm_rois` API + cov_f/shift spinboxes + reference-circle overlay + filter cache invalidation**

**Goal:** Wire the phasor plot to receive GMM-produced geometries, expose per-ROI eigenstructure controls for GMM ROIs only, render the reference-circle overlay, and propagate filter changes through the existing debounce + cache-invalidation machinery.

**Requirements:** R3, R4, R6, R7

**Dependencies:** U4

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_gui_workflows/test_phasor_gmm_workflow.py` (new)

**Approach:**
- **New public method**: `set_phasor_filters(self, *, intensity_threshold: float, ref_circle_tau_ns: float | None, ref_circle_radius: float | None) -> None`.
  - Stores values on `self._intensity_threshold`, `self._ref_circle_tau_ns`, `self._ref_circle_radius`.
  - Resolves `(G_c, S_c)` immediately: read `freq_mhz = self._session.dataset.metadata.get("flim_frequency_mhz") if self._session.dataset else None` (handle.metadata snapshot is acceptable here; the staleness rule is about use cases reading mid-flight, not the GUI's lazy display path). Read `harmonic` from `self._harmonic_combo.currentText()` (already present on the window). Compute `(G_c, S_c) = universal_circle_gs(harmonic, ref_circle_tau_ns, freq_mhz)` only when both `ref_circle_tau_ns is not None` and `freq_mhz is not None`. Otherwise `_ref_circle_center = None`.
  - **Freq-missing defensive guard**: when `ref_circle_tau_ns is not None` but `freq_mhz is None`, the overlay is hidden, `_ref_circle_center` stays None, and `_status.showMessage("Reference circle requires flim_frequency_mhz — filter not applied", 5000)` fires. The histogram path uses `_ref_circle_center` directly — None means no ref-circle filter, so the histogram refreshes correctly without freq.
  - **Invalidates every `_ROIWidget.cached_mask`** plus `_active_mask_array` / `_active_mask_flat` (rule from `percell4-selection-filtering-multi-roi-patterns.md` Pattern 5; multi-vector staleness vector #4).
  - Updates the reference-circle overlay (see below).
  - Calls `self._filter_timer.start()` to debounce the histogram refresh.
- **Reference-circle overlay**: a single `pg.PlotCurveItem` added in `_build_ui`, `setVisible(False)` initially. When `_ref_circle_center is not None and _ref_circle_radius is not None`:
  - Generate 200-point circle in data coordinates `(g, s) = (G_c + r·cos θ, S_c + r·sin θ)`.
  - **Clip to the plot viewport** — points where `s < 0` or `s > 0.7` (the existing `setYRange(0, 0.7)`) are filtered out before `setData`. This prevents pyqtgraph from clipping silently while keeping the visible portion correct. The filter still applies to all matching pixels regardless of overlay visibility (`compute_valid_phasor_pixels` doesn't care about the plot range).
  - `setData(x=g_clipped, y=s_clipped)` (array form per Pattern 7), `setVisible(True)`. Z-value 9 (above histogram, below ROI overlays).
  - When disabled (any of the three conditions: filter unset, freq missing, `_ref_circle_radius is None`), `setVisible(False)`.
- **Pass new filter values into `compute_valid_phasor_pixels`** in `_refresh_histogram`: thread through `intensity_flat`, `intensity_threshold`, `ref_circle_center=self._ref_circle_center`, `ref_circle_radius=self._ref_circle_radius`. `intensity_flat` is `self._intensity.ravel()` when present.
- **`enableAutoSIPrefix(False)` and `disableAutoRange()`** re-applied after `_refresh_histogram` already; verify in U5 implementation that `set_phasor_filters` does not skip these on the no-data path. (Origin: `percell4-phasor-plot-axis-desync.md`.)
- **`place_gmm_rois(geometries: list[PhasorROIGeometry], shape: str, criterion: str | None, sampled_pixels: int) -> None`**:
  - **10-ROI cap pre-check**: `available = 10 - len(self._roi_widgets); if available <= 0: status="ROI list full (10 max) — remove some before running GMM"; return; truncated = geometries[:available]; n_dropped = len(geometries) - len(truncated)`. When `n_dropped > 0`, append the truncation count to the final status message ("GMM placed 3 ROIs (truncated 2 due to 10-ROI cap)").
  - For each `(i, geo)` in `enumerate(truncated)`:
    - Compute `widget_idx = len(self._roi_widgets) + i` (pre-append global position) — used for color cycling so GMM ROIs continue the global cycle and don't collide with manual ROI colors.
    - `color = COLOR_CYCLE[widget_idx % len(COLOR_CYCLE)]`.
    - Build `gmm_fit = GMMFit(mean_g=geo.mean_g, mean_s=geo.mean_s, lambda_major=geo.lambda_major, lambda_minor=geo.lambda_minor, principal_angle_rad=geo.principal_angle_rad, cov_f=2.0, shift=0.0, shape=shape, criterion=criterion, sampled_pixels=sampled_pixels)`. (cov_f and shift defaults are the panel defaults; the geometry was already computed against these defaults inside the use case so the resulting `phasor_roi.center` / `radii` are consistent with `gmm_fit.cov_f=2.0, shift=0.0`.)
    - Build `phasor_roi = PhasorROI(origin="gmm", gmm_fit=gmm_fit, name=_make_unique_name(f"GMM_{geo.label}"), color=color, label=widget_idx + 1, center=geo.center, radii=geo.radii, angle_deg=geo.angle_deg)`.
    - Call `_create_roi_widget(phasor_roi)`.
  - The unique-name helper `_make_unique_name(base)` walks existing names and adds `_2`, `_3`, … if collision (same logic the existing `_on_name_edited` line 437-441 already implements; extract to a helper if not already factored).
  - Honor R3 "only append" — never clear `_roi_widgets`.
  - After placement, mark `_colormap_dirty = True`, refresh the ROI list, set status to `"GMM placed N ROIs (n={chosen_n}, criterion={criterion}, sampled {sampled_pixels:,} pixels)"`. Append truncation note if applicable.
- **Selected-ROI panel additions**: two new `QDoubleSpinBox` rows (cov_f, shift) and a "Reset to fit" `QPushButton`, all created in `_build_ui` and held in `self._cov_f_spin`, `self._shift_spin`, `self._reset_fit_btn`. Layout: cov_f and shift inline below the angle row; "Reset to fit" inline below the spinboxes (matches the visual coupling between the spinboxes and the snap-back affordance). In `_on_roi_list_selection`, **`setEnabled` not `setVisible`** — disabled (greyed) for non-GMM ROIs to keep the layout stable instead of reflowing on selection. Same range as the FlimPanel's segmentation group: `cov_f` 0.5–5.0 step 0.1, `shift` -2.0 to 2.0 step 0.1, both 1-decimal display.
- **`_on_cov_f_changed` / `_on_shift_changed` slots** (drag-preserving):
  - Read `widget.phasor_roi.gmm_fit`. If `None`, return.
  - Read **current** anchor: `anchor = widget.phasor_roi.center` (preserves manual drag position before the spinbox edit).
  - Update `gmm_fit.cov_f` / `gmm_fit.shift` to the new spinbox value.
  - Recompute `(center, radii, angle_deg) = gmm_to_phasor_roi_geometry(mean=(gmm_fit.mean_g, gmm_fit.mean_s), lambda_major=gmm_fit.lambda_major, lambda_minor=gmm_fit.lambda_minor, principal_angle_rad=gmm_fit.principal_angle_rad, cov_f=gmm_fit.cov_f, shift=gmm_fit.shift, shape=gmm_fit.shape, anchor=anchor)`.
  - **Block the RectROI signal** during programmatic updates: wrap the `setPos` + `setSize` block in `widget.roi.blockSignals(True)` / `False`. Otherwise `sigRegionChangeFinished` re-fires `_on_roi_moved_widget` which would round-trip and lose precision. (Origin Doc-Review F6.)
  - Apply to `phasor_roi.center` / `radii` / `angle_deg`. Invalidate `widget.cached_mask`. Update the ellipse curve (`_update_ellipse_curve_for(widget)`). Trigger `self._preview_timer.start()`.
- **`_on_reset_fit_clicked` slot**: same as cov_f/shift handlers but passes `anchor=None` to `gmm_to_phasor_roi_geometry` — explicitly snaps back to the cluster mean.
- **Connect ROI movement signal via identity-capture lambda** (`percell4-selection-filtering-multi-roi-patterns.md` Pattern 3) — already in place for manual ROIs. The slot must `if widget not in self._roi_widgets: return`.
- `_torn_down` flag pattern not strictly required here — `_filter_timer` and `_preview_timer` already exist with their own lifecycles, but ensure `closeEvent` calls `.stop()` on both before the `_unsubs` teardown loop (`napari-modal-tool-overlay-pattern-2026-04-29.md`).
- **Mid-flight dataset switch protection**: `_on_dataset_changed` already tears down `_roi_widgets`. `place_gmm_rois` itself adds a sanity check at entry: `if self._g_map is None: status = "Phasor data missing — GMM result discarded"; return`. The full mismatch check (current dataset vs result.dataset_path) lives in FlimPanel._on_gmm_finished (U6).

**Patterns to follow:**
- `_create_roi_widget` (`peer_views/phasor_plot.py:367`) — RectROI + PlotCurveItem pair, identity-capturing lambda for `sigRegionChangeFinished`.
- `_on_active_mask_changed` (line 700) — invalidate cache + start `_filter_timer` on filter state change.
- `_load_active_mask_flat` (line 730) — the canonical "lazy load + shape validation + silent bypass" pattern.

**Test scenarios:**
1. Happy path: `place_gmm_rois([geo1, geo2], shape="ellipse", criterion="BIC", sampled_pixels=100_000)` on an empty list appends 2 entries; `len(window._roi_widgets) == 2`; ROI list shows GMM_1, GMM_2; status bar shows "GMM placed 2 ROIs (n=2, criterion=BIC, sampled 100,000 pixels)".
2. Happy path: 3 manual ROIs already present; calling `place_gmm_rois([geo])` appends a 4th. Manual ROIs unchanged (R3 "only append").
3. Happy path: 3 manual ROIs already present; the new GMM ROI's color is `COLOR_CYCLE[3]` (continuing the global cycle), not `COLOR_CYCLE[0]` (which would collide).
4. Edge case (10-cap respected): 8 manual ROIs already present; `place_gmm_rois([g1, g2, g3, g4])` appends only 2 (8+2=10), drops 2, status reports "GMM placed 2 ROIs (truncated 2 due to 10-ROI cap)".
5. Edge case (10-cap full): 10 ROIs already present; `place_gmm_rois([g1, g2])` appends 0; status "ROI list full (10 max)…".
6. Edge case: name collision — `GMM_1` already exists; new ROI gets name `GMM_1_2` via `_make_unique_name`; no exception.
7. Happy path: select GMM_1 (with default cov_f=2, shift=0), change cov_f from 2.0 to 3.0 — `phasor_roi.radii` updates to `(3 × √λ_major, 3 × √λ_minor)`; `gmm_fit.cov_f == 3.0`; angle unchanged.
8. Happy path: select GMM_1, change shift from 0.0 to 0.5 — `phasor_roi.center` displaces from current anchor by `0.5 × √λ_major × (cos θ, sin θ)`.
9. Happy path (drag preservation): drag GMM_1 from cluster mean (0.4, 0.3) to (0.5, 0.35) by mouse; then change cov_f from 2.0 to 2.5 — `phasor_roi.center` stays around (0.5, 0.35) (anchor=current center), only radii grow. The user's drag is preserved.
10. Happy path (Reset to fit): after the drag in scenario 9, click "Reset to fit" — `phasor_roi.center` returns to `(gmm_fit.mean_g + shift_offset_g, gmm_fit.mean_s + shift_offset_s)` (with current shift applied to the cluster mean).
11. Happy path (RectROI signal blocking): change cov_f from 2.0 to 3.0; `_on_roi_moved_widget` fires zero times during the slot (signals blocked).
12. Edge case: select a manual ROI — cov_f, shift, and Reset to fit spinboxes/buttons are **disabled** (not hidden) so the layout stays stable.
13. Happy path: `set_phasor_filters(intensity_threshold=1000, ref_circle_tau_ns=None, ref_circle_radius=None)` — every `_ROIWidget.cached_mask` is `None` after the call; `_filter_timer` started; reference-circle overlay hidden.
14. Edge case (freq missing): `set_phasor_filters(ref_circle_tau_ns=2.5, ref_circle_radius=0.5)` with `session.dataset.metadata` lacking `flim_frequency_mhz` — overlay stays hidden; `_ref_circle_center is None`; status message "Reference circle requires flim_frequency_mhz — filter not applied" fires; histogram refreshes without the filter.
15. Edge case (viewport clipping): `set_phasor_filters(ref_circle_tau_ns=2.5, ref_circle_radius=0.5)` with freq=80MHz and harmonic=1 produces (G_c, S_c)≈(0.388, 0.487); top of circle at S≈0.99. The overlay's `setData` receives only points where S ≤ 0.7 — visually a clipped arc.
16. Integration: load a v2 JSON with `origin="gmm"` + `gmm_fit`, then change cov_f via the spinbox — eigenstructure-derived recomputation works against the loaded fit.
17. Integration: `place_gmm_rois` called with `_g_map=None` (post-DATASET_CHANGED but pre-set_phasor_data) returns early with status "Phasor data missing — GMM result discarded".
18. Integration: `closeEvent` stops both timers before unsubscribing (no callback fires after teardown).

**Verification:**
- `pytest tests/test_gui_workflows/test_phasor_gmm_workflow.py -q` green.
- Manual smoke: launch app, compute phasor, manually `phasor_window.place_gmm_rois([...])` from a console, observe ROIs appear and respond to spinboxes.

---

- U6. **FlimPanel: Phasor Filters group + Phasor Segmentation group + GmmWorker plumbing**

**Goal:** Add the user-facing controls in the FLIM tab and wire "Run GMM" through a `Worker` to the `RunPhasorGMM` use case, then dispatch the result to `phasor_window.place_gmm_rois`.

**Requirements:** R1, R2, R7

**Dependencies:** U3, U5

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
- Test: `tests/test_gui_workflows/test_flim_phasor_segmentation.py` (new)

**Approach:**
- **New `Phasor Filters` `QGroupBox`** below `Phasor Analysis`:
  - `QSpinBox` "Intensity ≥" (range 0–10⁹, step 1, default 0).
  - `QCheckBox` "Reference circle" + `QDoubleSpinBox` "τ (ns)" (default 2.5, range 0–100, step 0.1) + `QDoubleSpinBox` "r" (default 0.5, range 0–1, step 0.05). Children of the checkbox are `setEnabled(False)` until the checkbox is checked.
  - Note label: "Active mask filter is on the phasor plot toolbar".
  - **`flim_frequency_mhz`-aware enable/disable**: subscribe to `Event.DATASET_CHANGED` (and re-evaluate after `_on_compute_phasor` succeeds since metadata can be written there). When `session.dataset.metadata.get("flim_frequency_mhz") is None`, force the Reference-circle checkbox `setEnabled(False)` with tooltip "Compute phasor first — flim_frequency_mhz needs to be set". When it's present, allow the checkbox to be checked normally.
  - All four widgets connect to a single private `_push_filters_to_phasor_plot()` slot via `valueChanged` (spinboxes) / `toggled` (checkbox). Slot:
    1. `phasor_win = self._get_phasor_window(); if phasor_win is None: return` — defensive guard against a closed phasor window (origin Doc-Review A11).
    2. Build kwargs from widget state and call `phasor_win.set_phasor_filters(intensity_threshold=…, ref_circle_tau_ns=…, ref_circle_radius=…)`.
- **New `Phasor Segmentation` `QGroupBox`** below `Phasor Filters`:
  - `QComboBox` Shape: `Circle` | `Ellipse` (default Ellipse).
  - `QCheckBox` Auto + `QSpinBox` "n / n_max" (1–10, default 2). When Auto unchecked, label is "n"; when checked, label is "n_max".
  - `QComboBox` Criterion: `BIC` | `AIC`. **`setEnabled(False)` (NOT `setVisible(False)`)** when Auto is unchecked — keeps the layout stable; greyed control still readable.
  - `QDoubleSpinBox` cov_f (0.5–5.0 default 2.0, step 0.1, 1 decimal).
  - `QDoubleSpinBox` Shift (-2.0 to 2.0 default 0.0, step 0.1, 1 decimal).
  - `QPushButton` "Run GMM".
- **`_on_run_gmm` handler**:
  1. **Double-click race guard**: `if getattr(self, "_gmm_worker", None) is not None and self._gmm_worker.isRunning(): return` (defense in depth on top of button-disable, origin Doc-Review A15).
  2. `active_channel = self.data_model.session.active_channel`; if None, status "Select a channel in the viewer first" and return.
  3. `phasor_win = self._get_phasor_window(); if phasor_win is None: status="Open the Phasor Plot first"; return`.
  4. Read all widget values:
     ```
     auto = self._auto_check.isChecked()
     kwargs = dict(
         channel=active_channel,
         shape=self._shape_combo.currentText().lower(),
         n_components=None if auto else self._n_spin.value(),
         criterion=self._criterion_combo.currentText() if auto else None,
         n_max=self._n_spin.value() if auto else 4,
         cov_f=self._cov_f_spin.value(),
         shift=self._shift_spin.value(),
         intensity_threshold=float(self._intensity_threshold_spin.value()),
         ref_circle_tau_ns=self._ref_circle_tau_spin.value() if self._ref_circle_check.isChecked() else None,
         ref_circle_radius=self._ref_circle_radius_spin.value() if self._ref_circle_check.isChecked() else None,
         mask_filter_active=phasor_win._mask_filter_check.isChecked(),
         use_filtered_gs=phasor_win._filtered_check.isChecked(),
         harmonic=int(phasor_win._harmonic_combo.currentText()),
     )
     ```
  5. Lazy-import `RunPhasorGMM`. Build `uc = RunPhasorGMM(self._get_repo(), self.data_model.session)`.
  6. Disable the "Run GMM" button. Status: "Running GMM…".
  7. `self._gmm_worker = Worker(uc.execute, **kwargs)` — hold the reference. (The generic `Worker(QThread)` accepts the callable + kwargs and runs `self._fn(*args, **kwargs)`; verified in `gui/workers.py:55-69`.)
  8. Connect `worker.finished` → `self._on_gmm_finished`, `worker.error` → `self._on_gmm_error`. **Do not connect `worker.progress`** — the generic Worker has no way to inject progress from inside `uc.execute()`, and connecting would be a no-op.
  9. `self._gmm_worker.start()`.
- **`_on_gmm_finished(result: RunPhasorGMMResult)`**:
  - Re-enable Run GMM button.
  - **Dataset-snapshot mismatch check**: `current_dataset = self.data_model.session.dataset; if current_dataset is None or current_dataset.path != result.dataset_path: status="Dataset changed mid-GMM — result discarded"; return` (origin Doc-Review A1, P0).
  - `phasor_win = self._get_phasor_window(); if phasor_win is None: return` (window closed during the fit).
  - Call `phasor_win.place_gmm_rois(result.geometries, shape=self._shape_combo.currentText().lower(), criterion=result.criterion, sampled_pixels=result.sampled_pixels)`.
  - The phasor plot's `place_gmm_rois` handles the 10-cap and emits its own status with truncation info (U5).
- **`_on_gmm_error(err: WorkerError)`**:
  - Re-enable Run GMM button.
  - `self._show_status(f"GMM error: {err.message if hasattr(err, 'message') else err}")` (verify the exact attribute on `percell4.workflows.diagnostics.WorkerError` during implementation).
- **GUI element classification**: every new widget is an Action under the Selector/Creator/Action taxonomy — none of them write to any of the five Session selection fields.

**Patterns to follow:**
- `flim_panel.py:_on_compute_phasor` (line 129) — channel-validation gate, lazy use-case import, `repo = self._get_repo()`, status flow.
- `flim_panel.py:_on_apply_wavelet` (line 188) — `QApplication.setOverrideCursor(Qt.WaitCursor)` ... `restoreOverrideCursor()` for synchronous waits. We use `Worker` instead, so cursor override is replaced by button-disable.
- `gui/segmentation_panel.py:265-270` — canonical Worker invocation: hold `self._worker` reference, connect signals, `.start()`.
- `gui/workers.py:Worker` — generic class; pass `(callable, *args, **kwargs)`.
- Existing `phasor_win.set_phasor_data(...)` push pattern at `flim_panel.py:175` — same one-way push for `set_phasor_filters` and `place_gmm_rois`.

**Test scenarios:**
1. Happy path: with a valid dataset + active channel, click "Run GMM" with `Auto` off, `n=2`, `Shape=Ellipse` → after worker completes, `phasor_win.place_gmm_rois` is called once with 2 geometries; "Run GMM" button is re-enabled.
2. Happy path: with `Auto=True`, `Criterion=BIC`, `n_max=4` → worker invoked with `n_components=None, criterion="BIC", n_max=4`; status bar shows the chosen n on completion.
3. Happy path: change "Intensity ≥" spinbox → `phasor_win.set_phasor_filters` called once with the new threshold.
4. Happy path: check "Reference circle" → tau and radius spinboxes enable; uncheck → they disable AND `phasor_win.set_phasor_filters(ref_circle_tau_ns=None, ref_circle_radius=None)` is called.
5. Happy path: criterion combo is `setEnabled(False)` when Auto is unchecked, and re-enabled when Auto is checked. Geometry of the panel does not reflow.
6. Happy path (dataset-mismatch protection): mock `_on_gmm_finished` to receive a `RunPhasorGMMResult(dataset_path=Path("/tmp/old.h5"))` while `session.dataset.path == Path("/tmp/new.h5")` → `place_gmm_rois` is NOT called; status "Dataset changed mid-GMM — result discarded".
7. Happy path (phasor window closed mid-fit): close phasor window after `worker.start()`; `_on_gmm_finished` returns silently with no AttributeError.
8. Happy path (filter push when phasor window closed): close phasor window, change Intensity ≥ spinbox → `_push_filters_to_phasor_plot` returns early with no exception.
9. Happy path (freq-missing dataset): load a dataset where `metadata.get("flim_frequency_mhz") is None`; reference-circle checkbox is `setEnabled(False)` with tooltip explaining the prerequisite.
10. Happy path (kwargs assembly): with all widgets set to a known fixture, assert `_on_run_gmm` constructs the exact kwargs dict the use case expects (mock the use case's `execute`, capture call kwargs, assert deep-equal).
11. Edge case: click "Run GMM" with no active channel → status "Select a channel..."; worker NOT started; button NOT disabled.
12. Edge case: click "Run GMM" with phasor window closed → status "Open the Phasor Plot first"; worker NOT started.
13. Edge case (double-click race): mock `isRunning` to return True on the first existing worker; second `_on_run_gmm` invocation returns early; first worker reference is preserved.
14. Edge case: click "Run GMM" twice rapidly when button-disable lands cleanly → only one worker created (the second click is ignored by Qt).
15. Error path: `Worker.error` fires with `WorkerError("Phasor data not found...")` → button re-enabled; status shows error message; no `place_gmm_rois` call.
16. Error path: `Worker.error` fires with `WorkerError("Reference-circle filter requires flim_frequency_mhz...")` → user-facing message preserved.
17. Integration: full pipeline — set filter values, click Run GMM, assert use case's `execute()` received the correct `intensity_threshold` and `ref_circle_*` and `harmonic` kwargs (mock the use case to capture).
18. Integration: GUI element classification — `grep -rn "session\.set_active_\|session\.set_filter\|session\.set_selection" src/percell4/interfaces/gui/task_panels/flim_panel.py` returns zero hits (FlimPanel is all-Action; this PR introduces no Session writes).

**Verification:**
- `pytest tests/test_gui_workflows/test_flim_phasor_segmentation.py -q` green.
- Manual smoke (the canonical path the brainstorm acceptance criteria describes):
  1. Compute phasor on a multi-population dataset.
  2. Set Intensity ≥ 1000 → histogram restricts.
  3. Enable Ref Circle τ=2.5, r=0.5 → histogram further restricts; reference circle drawn on plot.
  4. Run GMM, Auto + BIC, n_max=4 → N ROIs appear in list.
  5. Edit cov_f on GMM_1 → ellipse grows symmetrically.
  6. Edit Shift → ellipse translates along principal axis.
  7. Drag, then Reset to fit → snaps back.
  8. Apply Visible as Mask → masks written to /masks/<name> (existing flow, unchanged).

---

- U7. **Audit + canonical-source updates**

**Goal:** Keep the project's architectural-audit artifacts in sync with the new GUI elements.

**Requirements:** Project standards (root `CLAUDE.md` lines 42-50)

**Dependencies:** U6

**Files:**
- Modify: `docs/audits/gui-element-classification.yaml`
- Modify: `docs/audits/session-mutation-graph.md` (no graph changes — confirm that no new Session writes are introduced; record the audit pass)
- Modify: `docs/audits/subscriber-rebind-matrix.md` (only if `PhasorPlotWindow` gains new Event subscriptions — should be no-op for this plan since filter values are GUI-local)

**Approach:**
- Add new entries under the FlimPanel and PhasorPlotWindow sections of `gui-element-classification.yaml`:
  - `FlimPanel.intensity_threshold_spin` — Action
  - `FlimPanel.ref_circle_check`, `ref_circle_tau_spin`, `ref_circle_radius_spin` — Action
  - `FlimPanel.shape_combo`, `auto_check`, `n_clusters_spin`, `criterion_combo`, `cov_f_spin`, `shift_spin`, `run_gmm_btn` — Action
  - `PhasorPlotWindow.cov_f_spin`, `shift_spin`, `reset_to_fit_btn` — Action. **Annotation**: these slots mutate `PhasorROI.gmm_fit` (a dataclass on a UI-owned widget), not Session state. The downstream Apply Visible as Mask Creator reads `phasor_roi.center`/`radii` to write the mask — that's the only path that touches HDF5.
- Re-confirm in `session-mutation-graph.md` that the only Session writes from this plan flow through the existing Apply Visible as Mask path (no change).

**Patterns to follow:**
- Existing entries in `docs/audits/gui-element-classification.yaml` — same YAML shape.

**Test scenarios:**
- Test expectation: none — pure documentation update; correctness is verified by reading the diff against the U6 implementation.

**Verification:**
- The `gui-element-classification.yaml` lists every new widget introduced by U6/U5 with the correct classification.
- `grep -rn "session\.set_active_\|session\.set_filter\|session\.set_selection" src/percell4/interfaces/gui/task_panels/flim_panel.py src/percell4/interfaces/gui/peer_views/phasor_plot.py` (limited to the lines U5/U6 added) returns zero hits — no new Session writes.

---

## System-Wide Impact

- **Interaction graph:**
  - FlimPanel ↔ PhasorPlotWindow: new public methods (`set_phasor_filters`, `place_gmm_rois`) extend the existing one-way push pattern (`set_phasor_data`).
  - PhasorPlotWindow ← Session: existing subscriptions (`FILTER_CHANGED`, `ACTIVE_MASK_CHANGED`, `DATASET_CHANGED`) unchanged; no new subscriptions.
  - GmmWorker → FlimPanel: standard Qt signal/slot via `Worker.finished` / `error` / `progress`.
- **Error propagation:**
  - Use case raises `NoDatasetError`, `ValueError`, `KeyError` → translated to `WorkerError` by the `Worker` machinery → surfaced as a status message in FlimPanel.
  - Per-ROI `gmm_fit` JSON load errors fall through `try/except → QMessageBox.warning + continue` (preserves existing behavior).
- **State lifecycle risks:**
  - **Per-ROI cache invalidation** (`cached_mask`) on every filter knob change is critical — the multi-vector staleness doc lists this as the #4 vector.
  - **`_active_mask_array` / `_active_mask_flat` invalidation** also fires on `set_phasor_filters` even though those caches are conceptually independent — defensive, no-cost.
  - **GMM mid-flight + filter change** — if the user changes a filter while the Worker is running, the in-flight fit is on the pre-change pixels. Document this; don't try to cancel the worker. The result still appends to the list (subject to the dataset-snapshot mismatch check) and the user can re-run.
  - **GMM mid-flight + dataset switch** (P0 from doc-review) — `RunPhasorGMMResult.dataset_path` carries the snapshot; `_on_gmm_finished` discards results from stale datasets with an explicit status message. `_on_dataset_changed` already tears down `_roi_widgets`.
  - **PhasorROI.gmm_fit consistency** — drag-preservation is now the default (cov_f / shift slots use `phasor_roi.center` as the anchor). "Reset to fit" snaps back to the cluster mean explicitly. `gmm_fit.cov_f` and `gmm_fit.shift` track the spinbox values; `gmm_fit.mean_g`/`mean_s`/eigenstructure stay constant.
  - **`flim_frequency_mhz` missing** — both the FlimPanel reference-circle checkbox enable-state and PhasorPlotWindow's `set_phasor_filters` defensive guard prevent the crash path. The use case raises `ValueError` on the same condition.
- **API surface parity:**
  - `compute_valid_phasor_pixels` adds optional kwargs only — all existing callers (one) compile against the same signature. No external consumers.
  - `PhasorROI` JSON gains optional fields — old JSON loads cleanly into `origin="manual"`. Forward compat (old build loading new JSON with `gmm_fit`) silently drops the payload, which is acceptable for a single-user lab tool.
- **Integration coverage:**
  - The U3 alignment-invariant test (intensity from `/decay/<ch>.sum(-1)`, never `/intensity[ch_idx]`) is the key cross-layer behavior unit tests cannot prove from mocks alone.
  - The U5 round-trip load-then-edit test (load JSON → spinbox change → eigenstructure regeneration) crosses the dataclass deserialization + Qt signal layers.
- **Unchanged invariants:**
  - **Apply Visible as Mask** path is unchanged. Each ROI (manual or GMM) writes to `/masks/<roi_name>` exactly as today.
  - **Filtered (wavelet) checkbox** behavior is unchanged.
  - **Cell-selection filter** AND-composes via the same `compute_valid_phasor_pixels` extension.
  - **Selector / Creator / Action taxonomy** — every new control is an Action; no new Session writes.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| sklearn version compatibility — newer sklearn may change `GaussianMixture` defaults (init, tol, n_init). | Pin `>=1.3,<2.0` in `pyproject.toml`. Tests assert `chosen_n` and component recovery on synthetic data, not internal numerics — robust to default changes. Bumping cellpose later requires re-running the GMM test suite. |
| GMM mid-flight filter change leaves a stale fit. | Expected behavior. Status bar reports "GMM placed N ROIs" so the user can see whether to re-run; per-ROI cov_f/shift work regardless. |
| **GMM mid-flight dataset switch (P0)** — result lands on a different dataset, silently producing nonsense ROIs. | `RunPhasorGMMResult.dataset_path` carries the snapshot. `_on_gmm_finished` compares against `session.dataset.path` and discards mismatched results with a status message. Test scenario covers this path. |
| User drags a GMM ROI then edits cov_f / shift — drag intent. | Drag is now **preserved** by default (cov_f/shift slots use `phasor_roi.center` as the anchor for the recomputation). "Reset to fit" is the explicit snap-back affordance. |
| **Reference-circle input has no `flim_frequency_mhz` available (P0)** — synchronous filter path crashed in the original design. | Three-layer defense: (1) FlimPanel disables the Ref Circle checkbox + spinboxes when freq is None; (2) `PhasorPlotWindow.set_phasor_filters` early-returns the ref-circle path with a status message when freq is None; (3) `RunPhasorGMM.execute` raises `ValueError`. Tests cover all three paths. |
| Reference-circle radius pushes overlay outside plot viewport. | Overlay points clipped to `S ∈ [0, 0.7]` before `setData`. Filter still applies to all matching pixels regardless of overlay visibility. |
| 10-ROI cap silently exceeded by GMM placement. | `place_gmm_rois` pre-checks `len(_roi_widgets) + len(geometries)` against 10, truncates with explicit status message naming the count dropped. |
| GMM ROI color collides with existing manual ROI color. | Color index is `(len(_roi_widgets) + i) % len(COLOR_CYCLE)` — continues the global cycle. |
| Singular / near-singular covariance produces zero-radius ROI. | `gmm_to_phasor_roi_geometry` clamps eigenvalues to `max(λ, 1e-6 × trace, 1e-9)`. U1 has a rank-1 covariance test scenario. |
| Auto BIC chooses n=1 — useless single-cluster ROI. | Sweep starts at `n_min=2`. n=1 only available via manual override (user explicitly types n=1 with Auto unchecked). |
| Subsampling biases GMM toward bright pixels. | Documented as a known property carried from the reference scripts. Brightest-cluster bias is honest about its origin; future fix (clip per-pixel weight, alternative weight schemes) is deferred. |
| JSON forward-compat silent data loss across version mixing. | `to_dict` writes `schema_version: 2`. Old builds reading `> 2` warn the user. Multiple worktrees in this repo make this realistic. |
| `set_phasor_filters` crash when phasor window is closed. | `_push_filters_to_phasor_plot` early-returns when `phasor_win is None`. |
| Worker double-click race — second click GCs the still-running thread. | Defense in depth: button-disable + entry guard `if self._gmm_worker.isRunning(): return`. |
| RectROI signal feedback loop on programmatic `setPos`/`setSize`. | cov_f/shift/Reset slots wrap the RectROI updates in `widget.roi.blockSignals(True/False)`. |
| Per-ROI `cached_mask` invalidation forgotten on a new filter knob. | Test: `set_phasor_filters` must zero every `cached_mask` (U5 test scenarios). The pattern doc (`percell4-selection-filtering-multi-roi-patterns.md`) is referenced in U5's Patterns to Follow. |
| `np.isin` regression with sets. | U2 test scenarios explicitly include the cell-selection AND intensity composition path; the existing `compute_valid_phasor_pixels` already converts `frozenset` → `list`. |
| `phasor_roi_to_mask` API mismatch — passing the dataclass positionally. | U5 test scenarios cover live ROI updates which exercise this call path. The learnings doc (`phasor-roi-to-mask-api-mismatch.md`) is in Patterns to Follow. |
| Float32 precision loss in `decay.sum(axis=-1)` for high-photon-count pixels. | Use float64 intermediate (`decay.sum(axis=-1, dtype=np.float64).astype(np.float32)`); test scenario verifies. |
| dtcwt NumPy-2 shim removal during refactor. | Plan does not touch `wavelet_filter.py`. Note in Context that the shim must remain. |

---

## Doc-Review Resolutions (2026-05-03)

`ce-doc-review` ran four reviewer personas (coherence, feasibility, design-lens, adversarial) against this plan. Findings folded into the doc above:

**P0 (cross-persona agreement):**
- Mid-flight dataset switch silently corrupts ROI placement → `RunPhasorGMMResult.dataset_path` snapshot + `_on_gmm_finished` mismatch check.
- Reference-circle path crashes / draws garbage when `flim_frequency_mhz` is missing → three-layer defense (FlimPanel checkbox-disable, `set_phasor_filters` early-return, `RunPhasorGMM.execute` ValueError) plus viewport-clipped overlay.

**P1:**
- 10-ROI cap was bypassed by `place_gmm_rois` → explicit pre-check + truncation + status message.
- `COLOR_CYCLE[idx]` ambiguity collided GMM colors with manual ROI colors → `(len(_roi_widgets) + i) % len(COLOR_CYCLE)`.
- Drag silently lost on cov_f/shift edit → drag-preserving recompute (`anchor = phasor_roi.center`); "Reset to fit" is the explicit snap-back.
- `labels_flat=None` in use case dropped cell-selection filter (contradicts brainstorm R6) → use case reads `repo.read_labels(handle, session.active_segmentation)`.
- `harmonic` source was unresolved → passed in as explicit kwarg from FlimPanel (reads `phasor_win._harmonic_combo`).
- `Worker.progress` was a no-op (generic Worker can't inject progress into wrapped callable) → dropped the connection; single before/after status messages instead.
- Singular covariance → zero-radius ROI → `gmm_eigenstructure` clamps eigenvalues to `max(λ, 1e-6 × trace, 1e-9)`.
- Auto BIC could pick n=1 (useless) → sweep `n_min=2`; manual override still allows n=1.
- Subsample bias toward bright pixels → documented as known property carried from reference scripts; v1 ships with the bias, mitigation deferred.
- JSON forward-compat silent data loss → `to_dict` writes `schema_version: 2`; old builds reading newer JSON warn the user.
- `set_phasor_filters` crashed when phasor window closed → `_push_filters_to_phasor_plot` early-returns on `phasor_win is None`.
- Worker double-click race → entry guard `if self._gmm_worker.isRunning(): return`.
- RectROI signal feedback loop on programmatic update → `widget.roi.blockSignals(True/False)` wrap.
- Float32 precision loss in `decay.sum` → float64 intermediate.

**P2 / clarifications folded in:**
- C2 (kwargs enumeration): full kwargs dict spelled out in U6 approach.
- C5 (GMMFit construction): `place_gmm_rois` explicit GMMFit construction with each field.
- C3 (rename `label_idx` → `label`): done in `PhasorROIGeometry`.
- D5 (`setEnabled` not `setVisible` for criterion combo): codified.
- F8 (perf claim too optimistic at "≤1 s"): R7 loosened to "≤ a few seconds for the full BIC sweep, default `n_max=4`".
- All test scenarios numbered.

**Advisory (FYI, not folded):**
- A18 — fixed seed=0 vs entropy: kept seed=0 for reproducibility; future hidden config option deferred.
- A17 — circle radius from λ_minor (inscribed): preserved to match reference scripts; documented in Key Technical Decisions.
- A16 — per-ROI shape change: deferred (origin Scope Boundaries: "Per-cluster shape choice").
- A14 — `_torn_down` flag: closeEvent stops timers synchronously; full `_torn_down` flag pattern deferred unless implementation reveals a race.
- C4 — duplicate ref-circle center computation in use case + GUI: design choice. Each computes against its own metadata snapshot at the moment of the call. Acceptable for v1.

---

## Documentation / Operational Notes

- **No CLAUDE.md update required for this PR.** Per project rule "per-module CLAUDE.md describes current state only", any changes to `src/percell4/flim/CLAUDE.md` or `src/percell4/interfaces/gui/peer_views/CLAUDE.md` (if present) should follow the implementation, not the plan.
- **Brainstorm document archival**: after this plan ships, archive `docs/brainstorms/2026-05-03-phasor-gmm-segmentation-requirements.md` per the project's "archive brainstorms after implementation" rule.
- **Audit doc updates** are folded into U7.
- **Operational rollout**: single PR, no feature flag, no migration. The new sklearn dep installs via `pip install -e ".[dev]"`.

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-03-phasor-gmm-segmentation-requirements.md`
- **Reference implementations** (read-only context, in another repo): `/Users/leelab/ComplexWaveletFilter/Circular_ROI_lifetime.py`, `/Users/leelab/ComplexWaveletFilter/CondensedPhaseGMM.py`
- **Related brainstorms:** `docs/brainstorms/2026-04-17-phasor-roi-separate-masks-brainstorm.md`, `docs/brainstorms/2026-04-30-phasor-mask-filter-requirements.md`
- **Related code:**
  - `src/percell4/application/use_cases/compute_phasor.py` — use-case shape
  - `src/percell4/application/use_cases/apply_wavelet.py` — error translation pattern
  - `src/percell4/application/session.py` — Event enum, Session API
  - `src/percell4/domain/flim/phasor.py` — pure FLIM math
  - `src/percell4/domain/flim/phasor_display.py` — `compute_valid_phasor_pixels`
  - `src/percell4/domain/measure/grouper.py` — sklearn lazy-import precedent
  - `src/percell4/gui/workers.py` — `Worker` template
  - `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — `PhasorROI`, ROI machinery
  - `src/percell4/interfaces/gui/task_panels/flim_panel.py` — task panel host
- **Institutional learnings (full citations in Context section):**
  - `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  - `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`
  - `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md`
  - `docs/solutions/logic-errors/phasor-roi-to-mask-api-mismatch.md`
  - `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
  - `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  - `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  - `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
  - `docs/solutions/ui-bugs/percell4-phasor-plot-axis-desync.md`
- **Audit artifacts (touched in U7):**
  - `docs/audits/gui-element-classification.yaml`
  - `docs/audits/session-mutation-graph.md`
