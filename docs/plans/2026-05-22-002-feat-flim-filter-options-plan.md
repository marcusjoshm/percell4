---
title: "feat: FLIM phasor & lifetime filter options (true-unfiltered, median, wavelet)"
type: feat
status: completed
date: 2026-05-22
---

# feat: FLIM phasor & lifetime filter options (true-unfiltered, median, wavelet)

## Overview

Two related FLIM changes:

1. **Truly-unfiltered phasor + opt-in median filter.** Today `ComputePhasor` applies an unconditional 3×3 median filter to the canonical `phasor/<ch>/{g,s}` maps (the "unfiltered" cloud is silently median-filtered to match the legacy `flimfret` pipeline). Make `phasor/<ch>/{g,s}` truly unfiltered. Add a user-configurable median filter (kernel side-length in pixels) and, on the Phasor Plot window, expose **two mutually-exclusive checkboxes** — "Median filter" and "Wavelet filter" — replacing today's single "Filtered" checkbox. With both unchecked the cloud is truly unfiltered.

2. **Selectable Compute Lifetime source.** `ComputeLifetime` currently auto-prefers the wavelet result (`g_filtered`) and silently falls back to `g`. Replace this implicit behavior with an explicit user choice: compute lifetime from **unfiltered**, **median-filtered**, or **wavelet-filtered** phasor, chosen via a dropdown next to the Compute Lifetime button.

**New pipeline:** phasor → calibrate → save. Median(N×N) and wavelet become mutually-exclusive *display/derivation* options applied on top of the saved unfiltered g/s — never stacked. Wavelet now runs on truly-unfiltered g/s (previously it ran on median-filtered g/s).

---

## Problem Frame

The "unfiltered" label currently lies: `compute_phasor.py:116-118` runs `scipy.ndimage.median_filter(size=3)` on every phasor map before saving, so the canonical g/s — and therefore the "unfiltered" cloud in the Phasor Plot and the unfiltered branch of Compute Lifetime — is actually 3×3-median-filtered. This was a deliberate decision to match `flimfret`'s ground-truth output (see Institutional Learnings), but it removes the user's ability to (a) see genuinely raw phasor data, (b) tune the median kernel, and (c) choose independently which filter (if any) feeds lifetime computation.

The user wants explicit, user-controlled filtering: a true unfiltered baseline, an opt-in median filter with a configurable kernel, the existing wavelet filter, and the ability to pick any of the three as the lifetime source.

---

## Requirements Trace

- R1. `phasor/<ch>/{g,s}` written by `ComputePhasor` are truly unfiltered (no median filter applied at compute time).
- R2. A median filter with a user-configurable kernel side-length (in pixels) can be applied to the phasor maps, computed on-demand from the unfiltered g/s.
- R3. The Phasor Plot window exposes "Median filter" and "Wavelet filter" checkboxes that are **mutually exclusive**; both unchecked shows the truly-unfiltered cloud. The active filter feeds every "visible pixels" consumer (histogram, napari preview, apply-as-mask) identically.
- R4. The median kernel size is user-adjustable from the UI; changing it re-derives and refreshes the displayed median cloud.
- R5. Compute Lifetime offers an explicit source choice — Unfiltered / Median / Wavelet — via a dropdown beside the Compute Lifetime button; the chosen source is stamped on the written lifetime layer.
- R6. The Wavelet option/checkbox is unavailable (disabled, not hidden) when no wavelet result (`g_filtered`/`s_filtered`) exists for the active channel.

---

## Scope Boundaries

- Not building a "reproduce flimfret exactly" toggle. The old behavior (median-then-wavelet, 3×3) is intentionally dropped from the UI; a default median kernel of 3 reproduces only the *median-only* leg, not median+wavelet. See Risks.
- Not persisting median-filtered maps to HDF5. Median is computed on-demand (decision below). No new `g_median`/`s_median` dataset paths and no associated cache-invalidation surface.
- Not changing the wavelet algorithm, the DTCWT/Wiener math, or the `dtcwt` NumPy-2.0 shims.
- Not changing phasor calibration, harmonic handling, view-bin behavior, or the GMM/ROI/ref-circle controls.
- Not coordinating with the `feat/FLIM-complex-wavelet-filter` worktree. This plan targets `main`; the worktree owner reconciles conflicts in `phasor_plot.py`/`flim_panel.py` later.

### Deferred to Follow-Up Work

- Capture a `/ce-compound` learning for `compute_lifetime.py` source-selection after this lands — that use case currently has zero institutional coverage.

---

## Context & Research

### Relevant Code and Patterns

- **Phasor math (no filtering):** `src/percell4/domain/flim/phasor.py` — `compute_phasor`, `phasor_to_lifetime`. Median should be added as a sibling domain helper here (or a new `domain/flim/median.py`).
- **The unconditional median to remove:** `src/percell4/application/use_cases/compute_phasor.py:116-118` (`median_filter(g_map, size=3)` / same for `s_map`). Invalidation of derived layers at lines 164-173 stays as-is.
- **Lifetime source selection (to make explicit):** `src/percell4/application/use_cases/compute_lifetime.py:62-83` — currently tries `g_filtered`/`s_filtered`, falls back to `g`/`s`; writes `phasor/<ch>/lifetime` with a `source` attr (lines 106-109). `LifetimeResult` dataclass carries `source`.
- **Wavelet use case (input changes, no code change):** `src/percell4/application/use_cases/apply_wavelet.py` reads `phasor/<ch>/{g,s}` — after R1 these are unfiltered, so wavelet now runs on unfiltered data. Intensity is derived from `decay.sum(axis=-1)` — preserve this.
- **Phasor Plot window:** `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — `PhasorPlotWindow`. Existing `self._filtered_check` ("Filtered", `_on_filtered_toggled`, lines 371-375 / 1531) to replace; `_get_active_gs_maps()` (lines 1317-1322) picks displayed vs unfiltered; `set_phasor_data(...)` (lines 1479-1529) is the data-in entry point; `_compute_visible_valid_2d()` is the shared "visible pixels" helper feeding histogram + preview + apply-as-mask; `_refresh_histogram` is the refresh hook.
- **FLIM task panel (GUI wiring + use-case callers):** `src/percell4/interfaces/gui/task_panels/flim_panel.py` — `_on_compute_phasor` (line ~318), `_on_apply_wavelet` (line ~431), `_on_compute_lifetime` (lines 510-550), Compute Lifetime button built lines 229-234, `_wavelet_level` spinbox (lines 112-115) is the pattern to mirror for a kernel spinbox.
- **Repository port:** `src/percell4/ports/dataset_repository.py` — `read_array(..., view_bin=)`, `write_array`, `read_metadata`, `delete_path`. Impl `src/percell4/store.py` (`DatasetStore`).

### Institutional Learnings

- `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md` (Issue #7): the 3×3 median was deliberately added to match `flimfret`'s "unfiltered" output (`~/flimfret/docs/phasor_plot_pipeline_reference.md` §2.5). Removing it intentionally breaks that equivalence — surfaced in Risks.
- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`: the exact precedent for threading a new param (here: `median_size`, lifetime `source`) from GUI → use case. Rules: a receiver-side kwarg with no sender wiring is dead code; forward explicitly at every call site (never default silently); **capture session/widget values into the worker kwargs dict before constructing the QThread Worker.**
- `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`: any new filter checkbox on the plot must flow through the shared `_compute_visible_valid_2d()` helper so histogram, napari preview, and apply-as-mask stay pixel-identical. Pin with a structural-equality test.
- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`: derive intensity as `decay.sum(axis=-1)` from `/decay/<ch>` — never `/intensity[ch_idx]` — for any intensity-weighted operation.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` & `compute_phasor.py:164-173`: keep the derived-layer invalidation enumeration intact when phasor recomputes.
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`: the new checkboxes/dropdown are **Action**-class (read session/HDF5, never write the five session selection fields). No Creator obligations because median is on-demand and lifetime keeps writing to the existing `phasor/<ch>/lifetime` path already covered by the current Creator flow.

### External References

- None needed. scipy `median_filter` and the local wavelet pipeline are well-established in-repo; no external research warranted.

---

## Key Technical Decisions

- **Median is computed on-demand, not persisted.** Median is cheap and deterministic given a kernel size, so both the Phasor Plot and Compute Lifetime derive it from the unfiltered g/s at use time. Avoids new HDF5 paths and the multi-vector staleness surface. (User decision.)
- **Median and wavelet are mutually exclusive.** New pipeline is phasor → calibrate → save; the two filters are alternative views over the saved unfiltered g/s, never stacked. The two checkboxes behave like a radio pair (checking one unchecks the other; both off = unfiltered). (User decision.)
- **Wavelet now runs on truly-unfiltered g/s.** A direct consequence of R1 + mutual exclusivity. No code change in `apply_wavelet.py` (it already reads `g`/`s`), but its output changes vs. today. Accepted (see Risks).
- **Kernel expressed as odd side-length k (pixels per side), scipy `size=k`.** Range 3–15, step 2, default 3 (k=3 ⇒ the old 9-pixel 3×3 footprint). Tooltip clarifies total pixels = k². A k=3 median reproduces the old median-only behavior.
- **Median domain helper preserves current scipy semantics.** Extract the existing `scipy.ndimage.median_filter(size=k)` behavior verbatim into a domain function parameterized by `k`; NaN propagation matches today's behavior (no new NaN-safe normalization unless a test shows regression).
- **Lifetime keeps a single output path `phasor/<ch>/lifetime`** stamped with `source` (and `median_size` when applicable) attrs — mirrors current write contract; no new lifetime dataset paths.

---

## Open Questions

### Resolved During Planning

- Median storage: on-demand (not HDF5). — User.
- Filter relationship: mutually exclusive, wavelet on unfiltered. — User.
- Lifetime source UI: dropdown beside the button. — User.
- Worktree coordination: plan against `main` only. — User.
- Kernel unit: odd side-length k (default 3), scipy `size=k`. — Planning decision; total pixels = k² surfaced in tooltip.

### Deferred to Implementation

- Exact widget placement/layout of the median-kernel spinbox relative to the checkboxes (Phasor Plot) and the dropdown (FlimPanel) — pick the layout that reads cleanly in each existing group.
- Whether the Phasor Plot needs its own kernel spinbox separate from FlimPanel's lifetime kernel spinbox, or one shared value — default to per-context spinboxes; revisit if it feels redundant during implementation.
- Median-cloud caching key in the window (kernel size + source identity) to avoid recompute on every histogram refresh — settle against the real `_refresh_histogram` call frequency.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Filter-source resolution is one decision shared by display and lifetime:

| Active selection | Source g/s used | Where derived |
|---|---|---|
| none (both checkboxes off) | `phasor/<ch>/{g,s}` (unfiltered) | read from HDF5 |
| median | `median_filter(unfiltered_g/s, size=k)` | on-demand (domain helper) |
| wavelet | `phasor/<ch>/{g_filtered,s_filtered}` | read from HDF5 (must exist) |

```
Phasor Plot window
  checkboxes [Median] [Wavelet]  (mutually exclusive)  + kernel spin (k)
        │
        ▼
  _get_active_gs_maps()  ──►  _compute_visible_valid_2d()  ──►  { histogram, napari preview, apply-as-mask }
        ▲                                  (single shared helper)
   on-demand median(k) from unfiltered g/s

FlimPanel "Compute Lifetime"
  source dropdown [Unfiltered|Median|Wavelet] + kernel spin (k, when Median)
        │  (capture into worker kwargs BEFORE QThread)
        ▼
  ComputeLifetime.execute(channel, source=, median_size=, view_bin=)
        │   source → unfiltered g/s | median(k) | g_filtered/s_filtered
        ▼
  write phasor/<ch>/lifetime  (attrs: source, median_size)
```

---

## Implementation Units

- U1. **Median-filter domain helper for phasor maps**

**Goal:** Provide a pure, kernel-parameterized median filter for g/s maps that both the GUI and lifetime use case can call.

**Requirements:** R2, R4

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/flim/phasor.py` (add `median_filter_gs(g, s, size) -> tuple[np.ndarray, np.ndarray]`) — or create `src/percell4/domain/flim/median.py` if it reads cleaner alongside existing helpers.
- Test: `tests/test_flim/test_phasor.py` (add a median-filter test class).

**Approach:**
- Wrap `scipy.ndimage.median_filter(map_, size=size)` on each of g and s, returning `float32`. Mirror the exact call currently at `compute_phasor.py:116-118` so k=3 is byte-identical to today's median.
- Validate `size` is an odd integer ≥ 3; raise `ValueError` otherwise (callers pass UI-constrained values, but the domain guards itself).
- Stays in `domain/` — scipy is allowed there; no Qt/h5py imports (import-linter contract).

**Patterns to follow:**
- `src/percell4/domain/image/gaussian.py:nan_safe_gaussian_filter` for the shape of a kernel-parameterized domain filter helper (kernel expressed as a scalar arg).

**Test scenarios:**
- Happy path: a known small g/s array with a salt-and-pepper outlier → median(size=3) removes the outlier, output dtype float32, shape preserved.
- Happy path: `size=5` produces a smoother result than `size=3` on the same input (different from the size=3 output).
- Edge case: input containing NaN (zero-photon pixels) → behavior matches direct `scipy.ndimage.median_filter` (document/assert the NaN propagation, no crash).
- Error path: `size=2` (even) and `size=1` → `ValueError`.

**Verification:**
- New domain helper exists, is import-linter-clean (no domain→infra imports), and k=3 output equals the previous inline median behavior on a fixture.

---

- U2. **Make ComputePhasor write truly-unfiltered g/s**

**Goal:** Remove the unconditional 3×3 median so `phasor/<ch>/{g,s}` are genuinely raw.

**Requirements:** R1

**Dependencies:** None (independent of U1, but R3/U3 display correctness depends on this landing)

**Files:**
- Modify: `src/percell4/application/use_cases/compute_phasor.py` (delete the median block at lines 116-118; drop the now-unused `from scipy.ndimage import median_filter` import if nothing else uses it).
- Test: `tests/test_use_cases.py` (extend `TestComputePhasor*`).

**Approach:**
- Remove the median lines only. Keep calibration (lines 108-114) and the derived-layer invalidation (lines 164-173, drops `g_filtered`/`s_filtered`/`lifetime_filtered`) exactly as-is — recomputing raw phasor must still invalidate stale wavelet/lifetime layers.
- T1 audit-scoped file: consult `docs/solutions/` (already done via learnings research) before editing.

**Patterns to follow:**
- Existing `TestComputePhasorFreshMetadata` / `TestComputePhasorInvalidatesWavelet` in `tests/test_use_cases.py` using the `FakeRepo` fixture that records `written_arrays`.

**Test scenarios:**
- Happy path: written `phasor/<ch>/g` and `/s` equal the calibrated direct `compute_phasor(...)` output (i.e., NOT median-filtered) — assert against a fixture where a 3×3 median would visibly differ.
- Integration: recomputing phasor still deletes pre-existing `g_filtered`/`s_filtered`/`lifetime_filtered` (preserve `TestComputePhasorInvalidatesWavelet`).
- Edge case: zero-photon pixels remain NaN in the saved unfiltered maps (no median smearing across the NaN boundary).

**Verification:**
- After Compute Phasor, the saved g/s match raw `compute_phasor` output and the wavelet/lifetime invalidation still fires.

---

- U3. **Phasor Plot: mutually-exclusive Median/Wavelet checkboxes + on-demand median kernel**

**Goal:** Replace the single "Filtered" checkbox with mutually-exclusive "Median filter" and "Wavelet filter" checkboxes plus a median kernel spinbox; route the active filter through the shared visible-pixels helper.

**Requirements:** R3, R4, R6

**Dependencies:** U1, U2

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_gui_workflows/test_phasor_mask_filter.py` (extend) and/or a new `tests/test_gui_workflows/test_phasor_filter_checkboxes.py`.

**Approach:**
- Replace `self._filtered_check` with `self._median_check` and `self._wavelet_check`, wired as a mutually-exclusive pair (a `QButtonGroup` with `exclusive=True` allowing all-off, or manual toggle handlers that uncheck the sibling). Both off ⇒ unfiltered.
- Add `self._median_kernel_spin` (QSpinBox, range 3–15, step 2, default 3; tooltip "median window side length in pixels; total = k²"). Changing it, while median is active, re-derives and refreshes.
- Extend `_get_active_gs_maps()` to return: unfiltered g/s (none), `median_filter_gs(unfiltered, size=k)` (median), or wavelet g_filtered/s_filtered (wavelet). Cache the median result keyed by kernel size to avoid recompute on every `_refresh_histogram`.
- The Wavelet checkbox is enabled only when wavelet maps are present (carry forward the existing enable/disable logic from `set_phasor_data`, lines 1517-1526); disabled-not-hidden.
- All consumers (histogram, napari preview, apply-as-mask) must read through the same `_get_active_gs_maps()` / `_compute_visible_valid_2d()` path — do not add a parallel median branch anywhere else.
- These are **Action**-class controls (no session writes). Update `docs/audits/gui-element-classification.yaml` to rename/replace `phasor_plot.filtered_checkbox` with the two new entries.

**Patterns to follow:**
- Existing `_on_filtered_toggled` → cache-invalidate → `_refresh_histogram` flow; `_on_mask_filter_toggled`.
- `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md` shared-helper rule.

**Test scenarios:**
- Happy path: with both off, histogram/visible pixels derive from unfiltered g/s.
- Happy path: checking Median derives visible pixels from `median_filter_gs(unfiltered, k)`; changing k from 3→5 changes the displayed cloud.
- Edge case: checking Median while Wavelet is checked unchecks Wavelet (and vice versa); both can be off.
- Edge case: Wavelet checkbox disabled when no `g_filtered` present for the channel; enabled after wavelet computed.
- Integration (parity): apply-as-mask output equals the napari preview equals the histogram's visible-pixel set for each of unfiltered/median/wavelet — structural-equality test (`test_apply_equals_napari_preview` analog) covering the new median branch.

**Verification:**
- Toggling each checkbox and changing the kernel updates histogram, preview, and apply-as-mask identically; no parallel filter path exists.

---

- U4. **ComputeLifetime: explicit source (unfiltered / median / wavelet)**

**Goal:** Replace the implicit "filtered-if-exists" behavior with an explicit, caller-chosen source plus median kernel.

**Requirements:** R5

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/application/use_cases/compute_lifetime.py`
- Test: `tests/test_use_cases.py` (extend lifetime tests).

**Approach:**
- Change `execute` signature to `execute(channel, source="unfiltered", median_size=3, view_bin=1)` where `source ∈ {"unfiltered","median","wavelet"}`.
- Resolve g/s by source: `unfiltered` → `phasor/<ch>/{g,s}`; `median` → `median_filter_gs(unfiltered, median_size)` (U1); `wavelet` → `phasor/<ch>/{g_filtered,s_filtered}`. For `wavelet`, raise a clear error if the filtered maps are absent (the GUI prevents this via R6, but the use case guards itself).
- Keep writing `phasor/<ch>/lifetime`; stamp attrs `source` and (when median) `median_size`. Extend `LifetimeResult` if needed to carry `median_size`.
- Preserve `view_bin` threading and `phasor_to_lifetime` math. Remove the silent `g_filtered`→`g` fallback (now an explicit choice).
- T1 audit-scoped file.

**Patterns to follow:**
- Current `compute_lifetime.py:62-83` source resolution and `source`-attr stamping; `phasor-view-bin-not-forwarded` forwarding discipline.

**Test scenarios:**
- Happy path: `source="unfiltered"` reads g/s and stamps `source="unfiltered"`.
- Happy path: `source="median", median_size=5` produces lifetime from the size-5 median of g/s and stamps `source="median"`, `median_size=5`.
- Happy path: `source="wavelet"` reads `g_filtered`/`s_filtered` and stamps `source="wavelet"`.
- Error path: `source="wavelet"` with no `g_filtered` present → clear error (KeyError-derived or domain error), not a silent fallback.
- Error path: invalid `source` value → `ValueError`.
- Integration: `view_bin=3` still upsamples/stamps the bin attr as in the existing view-bin test.

**Verification:**
- Each source yields the expected lifetime values and stamped attrs; no implicit fallback remains.

---

- U5. **FlimPanel: Compute Lifetime source dropdown + kernel, forwarded to use case**

**Goal:** Surface the lifetime source choice and median kernel in the FLIM panel and forward them correctly to `ComputeLifetime`.

**Requirements:** R5, R6

**Dependencies:** U4

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
- Test: `tests/test_gui_workflows/test_flim_panel_cache.py` (extend) or new `tests/test_gui_workflows/test_compute_lifetime_source.py`.

**Approach:**
- Add `self._lifetime_source_combo` (QComboBox: Unfiltered / Median / Wavelet) beside the Compute Lifetime button (lines 229-234) and `self._lifetime_median_kernel` (QSpinBox 3–15 step 2 default 3), enabled only when source = Median.
- Disable the Wavelet option when no `g_filtered` exists for the active channel (R6) — disabled-not-hidden, mirroring the plot's wavelet enablement.
- In `_on_compute_lifetime` (lines 510-550): read source + kernel from the widgets, **capture them (and `view_bin=session.active_bin`) into the worker kwargs dict before constructing the QThread Worker**, then pass `source=` / `median_size=` into `ComputeLifetime.execute`. Never default silently at the call site.
- Action-class controls (no session writes). Update `docs/audits/gui-element-classification.yaml` with the new entries.

**Patterns to follow:**
- `self._wavelet_level` spinbox wiring (lines 112-115) and its read in `_on_apply_wavelet`; the worker-kwargs capture discipline from `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`.

**Test scenarios:**
- Happy path: selecting each source and clicking Compute Lifetime calls `ComputeLifetime.execute` with the matching `source=` (and `median_size=` when Median) — assert via a recording mock that captures per-call kwargs.
- Edge case: changing source to Median enables the kernel spinbox; other sources disable it.
- Edge case: Wavelet option disabled when no wavelet result for the active channel; enabled after Apply Wavelet runs.
- Integration: the napari "Lifetime (<ch>)" layer is added/refreshed after compute, and the value forwarded equals the widget state at click time (not a stale/live session read mismatch).

**Verification:**
- Each dropdown selection drives the corresponding use-case call with correctly forwarded params; no dead receiver-side kwargs.

---

## System-Wide Impact

- **Interaction graph:** Removing the compute-time median (U2) changes the input to `ApplyWavelet` (it reads `g`/`s`, now unfiltered) — wavelet output will differ from today even with no wavelet code change. Compute Lifetime's implicit fallback is removed (U4), so any caller relying on auto-filtered lifetime must now pass a source.
- **Error propagation:** `ComputeLifetime(source="wavelet")` with no wavelet maps now errors explicitly instead of silently falling back; the GUI prevents this (R6) but batch/CLI callers (if any pass through this use case) must handle it.
- **State lifecycle risks:** Median is on-demand, so it adds no HDF5 staleness surface. The existing `compute_phasor.py:164-173` derived-layer invalidation must remain intact (U2).
- **API surface parity:** Check for any CLI/batch caller of `ComputeLifetime` / `ComputePhasor` (e.g. `tests/test_application/test_batch_compute_phasor.py`, batch use cases) — if a CLI surface computes lifetime, it should gain matching `--lifetime-source` / kernel flags or an explicit default, per the forwarding-parity learning. Confirm during U4/U5.
- **Integration coverage:** The apply-as-mask = preview = histogram parity test must cover the new median branch (U3), since unit tests per path cannot catch cross-path drift.
- **Unchanged invariants:** Calibration, harmonic, view-bin, GMM, ROI, ref-circle behavior; the `phasor/<ch>/lifetime` write path and its `source` attr; the wavelet algorithm and `dtcwt` NumPy-2.0 shims.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Dropping the compute-time median breaks scientific equivalence with `flimfret` (whose "unfiltered" is 3×3-median). | Intentional per user. A median kernel of k=3 reproduces the median-only leg; document in the FLIM panel/Phasor Plot tooltips that the cloud is now truly unfiltered by default. Note in release/commit message. |
| Wavelet output changes because it now runs on unfiltered (not median-filtered) g/s. | Expected consequence of the mutually-exclusive model. Call it out in the PR description; re-validate any saved wavelet comparisons. No silent behavior left — both filters are explicit. |
| New filter branch bypasses the shared visible-pixels helper, causing histogram/preview/mask drift. | U3 routes everything through `_get_active_gs_maps()`/`_compute_visible_valid_2d()`; structural-equality parity test pins it (learning: phasor-apply-visible-as-mask). |
| Receiver-side `source`/`median_size` kwargs added but not wired at every caller. | U5 follows the view-bin-forwarding discipline: capture into worker kwargs before QThread, forward at every call site, recording-mock test asserts per-call kwargs. |
| Median recomputed on every histogram refresh causing UI jank. | Cache median maps keyed by kernel size in the window; only re-derive on kernel change or new data. |
| Editing T1 audit-scoped files (`compute_phasor.py`, `compute_lifetime.py`). | Learnings already retrieved; preserve invalidation enumeration and fresh-metadata reads. |

---

## Documentation / Operational Notes

- Update tooltips/labels on the Phasor Plot ("Median filter" / "Wavelet filter", kernel = k²) and the Compute Lifetime source dropdown.
- Update `docs/audits/gui-element-classification.yaml`: replace `phasor_plot.filtered_checkbox` with `phasor_plot.median_checkbox` + `phasor_plot.wavelet_checkbox` (+ kernel spin), and add the FlimPanel lifetime-source combo/kernel — all Action-class.
- The per-module `src/percell4/flim/CLAUDE.md` is already stale (references a non-existent `wavelet_filter_phasor()`); not in scope to fix here but worth a note.
- After landing, capture a `/ce-compound` learning for `compute_lifetime.py` source-selection (no existing coverage).

---

## Sources & References

- Related code: `src/percell4/application/use_cases/compute_phasor.py:116-118`, `compute_lifetime.py:62-83`, `src/percell4/interfaces/gui/peer_views/phasor_plot.py` (`_get_active_gs_maps`, `_compute_visible_valid_2d`, `set_phasor_data`), `src/percell4/interfaces/gui/task_panels/flim_panel.py` (`_on_compute_lifetime`), `src/percell4/domain/flim/phasor.py`.
- Learnings: `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md`, `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`, `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`, `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`, `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`.
- External reference (rationale of record): `~/flimfret/docs/phasor_plot_pipeline_reference.md` §2.5.
