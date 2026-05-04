---
title: "feat: Phasor plot — Clear within ROI (manual exclusion filter)"
type: feat
status: completed
date: 2026-05-04
origin: docs/brainstorms/2026-05-04-phasor-clear-within-roi-requirements.md
---

# feat: Phasor plot — Clear within ROI (manual exclusion filter)

## Overview

Add a "Clear within selected ROI" action and a "Reset cleared" action to the phasor plot toolbar. Clicking Clear consumes the currently-selected ROI: it computes the ROI's inside-mask, ORs it into a per-session `_cleared_mask` bitmap on `PhasorPlotWindow`, and removes the ROI from the list. The cleared mask becomes a sixth term in the existing AND chain inside `compute_valid_phasor_pixels()`, applied as `valid &= ~cleared_mask`. Because every visible-pixel consumer (histogram render, valid-pixel counter, "Apply Visible as Mask" output) goes through that single function, cleared pixels disappear consistently from all of them. Reset cleared wipes the bitmap; dataset change auto-resets it.

The feature is non-destructive (G/S arrays are never mutated), per-session ephemeral (no `.h5` or JSON persistence), and uses no undo history (single Reset).

---

## Problem Frame

The phasor histogram frequently contains a small off-cluster population (e.g. lysosomes appearing as a faint cluster to the right of the cytoplasmic cluster). Today there is no way to subtract a region of phasor space from the visible histogram. The only available tools are inclusionary: draw an ROI and emit it as a mask via "Apply Visible as Mask". Users who want "everything except the lysosome cluster" must either trace around it with an inclusion ROI (fiddly and lossy) or accept the contamination in the resulting mask. (See origin: `docs/brainstorms/2026-05-04-phasor-clear-within-roi-requirements.md`)

---

## Requirements Trace

- R1. **Cumulative cleared-pixel bitmap, per-session.** `_cleared_mask` lives on `PhasorPlotWindow`; never persisted; resets on dataset change. (origin R1)
- R2. **Two new toolbar buttons.** "Clear within selected ROI" (greyed when no ROI selected) and "Reset cleared" (greyed when nothing cleared); enable/disable updates reactively. (origin R2)
- R3. **Composition with existing filters via AND.** `(NOT cleared_mask)` joins the existing AND chain inside `compute_valid_phasor_pixels()` so histogram, valid-pixel counter, and "Apply Visible as Mask" output all see the same exclusion. (origin R3)
- R4. **Single Reset, no undo or history.** One button wipes the entire cleared mask; dataset change also auto-resets. (origin R4)
- R5. **No on-plot visual indicator.** Cleared pixels just disappear; only feedback is the histogram and valid-pixel counter. (origin R5)
- R6. **Selected ROI is consumed.** Clear removes the consumed ROI from the list via the same removal path used by Remove (so the napari preview layer sweep happens identically). (origin R6)
- R7. **Performance is negligible.** One ellipse hit-test plus one boolean OR; reuse existing `_filter_timer` debounce. (origin R7)

---

## Scope Boundaries

- No persistence of cleared pixels to `.h5` or to the Save ROIs JSON.
- No per-clear undo, no history stack, no staged-confirm preview.
- No on-plot decoration of historical clear regions (no dashed outlines, no hatched fills).
- No new ROI shapes — uses whatever ellipse the existing system supports.
- No multi-ROI batch clear ("clear within all selected ROIs at once").
- No propagation of the cleared filter to non-phasor windows (measurements, lifetime, etc.).
- No mutation of the underlying `(g, s)` arrays — must remain a filter, not a data edit.
- No promotion of the cleared mask into a `Session` field — kept as `PhasorPlotWindow` local state per the GUI state ownership rules in `CLAUDE.md` (so Clear and Reset stay strict Actions, not Selectors).
- **No propagation to `RunPhasorGMM`.** The use case at `src/percell4/application/use_cases/run_phasor_gmm.py:203` is a third caller of `compute_valid_phasor_pixels`. It must continue to omit `cleared_mask_flat` (default `None`) — GMM is invoked from `FlimPanel`, a different window, and the cleared mask is local to `PhasorPlotWindow` per the storage decision. Adding the parameter with default `None` to the function signature is sufficient; no change at the GMM call site.

---

## Context & Research

### Relevant Code and Patterns

- **Single-source-of-truth filter chain:** `src/percell4/domain/flim/phasor_display.py:15` — `compute_valid_phasor_pixels()`. Already takes `labels_flat`, `filter_ids`, `mask_flat`, `intensity_flat`, `intensity_threshold`, `ref_circle_center`, `ref_circle_radius`. The new `cleared_mask_flat` parameter slots in here; existing pattern of "shape mismatch silently bypasses" is the precedent we deliberately deviate from for this filter (see Key Technical Decisions).
- **Visible-pixel helper for the GUI:** `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — `_compute_visible_valid_2d()` at line 1192 calls `compute_valid_phasor_pixels()` once and reshapes; `_compute_filtered_binary()` at line 1222 ANDs each ROI's `cached_mask` with `_compute_visible_valid_2d()` to produce the per-ROI binary. Both will see the cleared mask via the upstream change — no per-call-site plumbing.
- **ROI removal pattern:** `_on_remove_roi()` at `phasor_plot.py:821` pops the widget, removes pyqtgraph items, reindexes labels, invalidates per-ROI `cached_mask`, resets selection, refreshes the ROI list, updates the cluster center marker, emits `preview_roi_removed(name)`, and starts the preview timer. This sequence is load-bearing — Clear must reuse it (refactor into `_remove_roi_widget(index)` helper) so the napari preview layer is swept by the same `preview_roi_removed` signal path.
- **ROI inside-mask call signature:** `phasor_roi_to_mask()` in `src/percell4/domain/flim/phasor.py:157`. Takes kwargs `(g, s, center=..., radii=..., angle_rad=np.radians(roi.angle_deg))` — copy from `_compute_filtered_binary` line-for-line; do NOT pass the dataclass.
- **Dataset-change reset hook:** `_on_dataset_changed()` at `phasor_plot.py:1320` clears `_roi_widgets`, resets `_selected_roi_index`, emits `preview_all_cleared`, resets `_filtered_check` and `_mask_filter_check`. The `_cleared_mask = None` reset slots in here.
- **Debounce timer:** `_filter_timer` at `phasor_plot.py:298` (150 ms single-shot) — already drives `_refresh_histogram`. Reuse it for refreshes triggered by Clear / Reset cleared.
- **Existing per-filter regression suite:** `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py` — five `test_apply_respects_*` tests (one per existing filter) plus `test_apply_equals_napari_preview` (structural-equality contract). Mirror this exact shape for the new cleared-mask filter.
- **GUI element classification audit:** `docs/audits/gui-element-classification.yaml` — both new buttons must be registered as Actions (no session-field mutation).

### Institutional Learnings

- **`docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`** — established `_compute_visible_valid_2d()` as the single source of truth after three call sites had drifted. Adding the cleared mask at the upstream `compute_valid_phasor_pixels()` level (not at one call site) preserves that invariant. The `test_apply_equals_napari_preview` structural-equality test is the load-bearing contract; per-filter tests are necessary but not sufficient.
- **`docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`** — ROI removal must emit `preview_roi_removed(name)` for the launcher to sweep the corresponding `_phasor_roi_preview_<name>` napari layer. Test asserting only signal emission is insufficient when the fixture has no real napari viewer; assert the napari preview prefix sweep too.
- **`docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`** — both new buttons are strict Actions (storing `_cleared_mask` on `PhasorPlotWindow` keeps Clear from writing any of the five session selection fields). Must be registered in `docs/audits/gui-element-classification.yaml`.
- **`docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`** — pixel-aligned bitmaps stored separately from the array they filter risk silent misalignment. The shape guard inside `compute_valid_phasor_pixels` already silently bypasses on mismatch; for the cleared mask we deliberately surface a status-bar message instead, since silent bypass would be a user footgun ("I cleared lysosomes, why are they back?").
- **`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`** (Vector 1) — co-locate the dataset-change reset in the existing `_on_dataset_changed` handler; do not invent a new Session-level event.
- **`docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`** (Patterns 2, 3, 5) — coalesce refreshes via the existing `_filter_timer` rather than adding a new timer; identity-capture widgets in lambdas (already done in current code); per-ROI `cached_mask` does NOT need invalidation when `_cleared_mask` changes (those represent ROI-shape membership, not visibility — they stay correct).
- **`docs/solutions/logic-errors/phasor-roi-to-mask-api-mismatch.md`** — call `phasor_roi_to_mask` with kwargs, not the dataclass.

### External References

- Not applicable. All patterns are local; no external research warranted.

---

## Key Technical Decisions

- **Add `cleared_mask_flat` as a sixth keyword-only parameter to `compute_valid_phasor_pixels()`, not as a local-only term in `_compute_visible_valid_2d()`.**
  *Rationale:* The single-source-of-truth fix from `phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md` ensured every consumer of the visible-pixel predicate routes through this one function — *in principle*. In practice, `_refresh_histogram` (`phasor_plot.py:1475`) still calls `compute_valid_phasor_pixels` directly instead of going through `_compute_visible_valid_2d`. Extending the upstream signature is necessary but not sufficient — see the next decision.

- **Refactor `_refresh_histogram` to call `_compute_visible_valid_2d()` instead of duplicating `compute_valid_phasor_pixels` directly.** *(P0 finding: without this refactor, the new `cleared_mask_flat` parameter would only flow through Apply / preview, not through the actual histogram render — the secondary cluster would continue to appear in the plot.)*
  *Rationale:* Closes the call-site drift permanently. Today there are two consumers of the filter chain inside `phasor_plot.py` (the `_compute_visible_valid_2d` helper and the histogram render) plus a third in `RunPhasorGMM` (out of scope for this plan). Routing the histogram render through `_compute_visible_valid_2d` makes the helper genuinely the single integration point that the prior bug fix intended. The histogram path's status-bar handling for `mask_bypassed` (`phasor_plot.py:1467-1495`) needs to be preserved through the refactor — surface the bypassed-status check by reading `_compute_visible_valid_2d`'s side state OR by computing `mask_bypassed` separately in the histogram path before delegating the valid mask. See U2 for the file-by-file change.

- **Surface a status-bar message on shape mismatch for the cleared mask, instead of silent bypass.**
  *Rationale:* The existing `mask_flat` and `intensity_flat` filters silently bypass on shape mismatch — but those filters' "off" state is unsurprising (no mask = no filter). For the cleared mask, silent bypass would mean "I cleared lysosomes, then I switched harmonic / channel and they reappeared without explanation." The shape mismatch is also unlikely in practice (G/S maps share `(H, W)` across harmonics for a given channel), but the safety net matters when it does fire. The bypass itself stays the same; only the user notification differs.

- **Store `_cleared_mask` on `PhasorPlotWindow`, not on `Session`.**
  *Rationale:* Per the GUI state ownership rules in `CLAUDE.md`, the five session selection fields are owned exclusively by Selectors and Creators. Storing `_cleared_mask` on the peer view keeps both new buttons classified as strict Actions (they don't write any session field) and avoids inventing a new Session-level event class. Other peer views don't consume the cleared mask, so peer-local storage is sufficient.

- **Refactor `_on_remove_roi` into `_remove_roi_widget(index)` helper and call it from both Remove and Clear.**
  *Rationale:* The removal sequence (pop, remove pyqtgraph items, reindex labels, invalidate per-ROI caches, reset selection, refresh list, update cluster marker, emit `preview_roi_removed`, debounce preview) is load-bearing for napari layer cleanup — reproducing it inline in Clear is an immediate bug magnet. Mechanical extract.

- **Lazy-allocate `_cleared_mask` from the current G/S shape on first Clear; reset on every G/S frame change via `set_phasor_data`.** *(P0 finding: the cleared mask must be tied to the (g, s) frame, not just to the dataset — channel switch, harmonic switch, wavelet recompute, and cache reload all rebuild G/S and would silently mis-align cleared pixels if the bitmap survived.)*
  *Rationale:* `set_phasor_data` (`phasor_plot.py:1280-1295`) is the single funnel through which every new G/S frame enters the window. The existing code at lines 1284-1295 already invalidates per-ROI `cached_mask` and `_active_mask_flat` here, with explicit alignment-invariant rationale: *"the cached mask flat may have been loaded against an earlier frame whose spatial alignment differs even when shapes match (rotation/flip applied to /decay between computes, channel switch, dataset switch)."* `_cleared_mask` is the same class of pixel-bound bitmap and lives next to those resets. Co-locating the invalidation here covers all recompute paths in one line and matches an existing pattern. The per-session ephemeral lifetime in origin R1 is preserved (the bitmap still resets on dataset change, since dataset change funnels through `set_phasor_data`).

- **Do not invalidate per-ROI `cached_mask` when `_cleared_mask` changes.**
  *Rationale:* Per-ROI `cached_mask` represents ROI-shape membership (does pixel `p` fall inside ROI ellipse `r`?), which is independent of visibility. The downstream AND in `_compute_filtered_binary` (`cached_mask & visible`) already picks up the new visible state via `_compute_visible_valid_2d()` flowing from the upstream parameter. No invalidation needed.

---

## Open Questions

### Resolved During Planning

- **Where does `compute_valid_phasor_pixels` actually live?** `src/percell4/domain/flim/phasor_display.py:15` (the brainstorm doc had it in `phasor.py` — corrected here).
- **How many call sites does `compute_valid_phasor_pixels` have?** Three in production code: `phasor_plot.py:1210` (inside `_compute_visible_valid_2d`), `phasor_plot.py:1475` (inside `_refresh_histogram`), and `run_phasor_gmm.py:203`. The plan refactors `_refresh_histogram` to delegate to `_compute_visible_valid_2d`, so after this change `phasor_plot.py` has one effective integration point. The GMM call site stays unchanged (see Scope Boundaries).
- **Should the cleared mask carry across harmonic switches?** Mechanically the bitmap can survive (pixel-space mask), but every harmonic switch goes through `set_phasor_data` to install the new G/S frame, and `set_phasor_data` now resets `_cleared_mask` (per the lifecycle decision above). Net effect: the cleared mask resets on harmonic switch, channel switch, dataset switch, and wavelet recompute — i.e., any time the user is looking at a new (g, s) frame. This matches user mental model ("I clicked Clear in *this* view, so a fresh view starts clean").
- **Should Reset cleared trigger any companion resets (ROIs, intensity threshold, ref-circle)?** No — keep its scope strictly to `_cleared_mask`. The Action-contract rule applies.

### Deferred to Implementation

- **Exact button labels.** "Clear within selected ROI" is descriptive but may be too long for the toolbar. Implementer can shorten to "Clear in ROI" or "Subtract ROI" if layout demands; pick what fits and matches the surrounding button voice.
- **Whether to add a confirmation prompt before Reset cleared on a large mask.** Origin R4 implies no — the user explicitly chose "no undo, no history". Implementer should default to no confirmation; revisit only if real usage produces complaints.
- **Whether Reset cleared should also stop and restart the preview timer for snappier UX.** Minor polish; default to "fire `_filter_timer.start()` and let debounce do its job" (the existing pattern).

---

## Implementation Units

- U1. **Extend `compute_valid_phasor_pixels` with a `cleared_mask_flat` parameter**

**Goal:** Add the inverted cleared-mask AND term to the single-source-of-truth filter chain, with the same shape-guard convention as `mask_flat`.

**Requirements:** R3, R7

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/domain/flim/phasor_display.py`
- Test: `tests/test_flim/test_phasor_display.py`

**Approach:**
- Add a new keyword-only parameter `cleared_mask_flat: NDArray[np.bool_] | None = None` to `compute_valid_phasor_pixels()`, placed after `ref_circle_radius` to avoid disturbing existing positional/keyword call sites.
- After the existing five filter blocks, append: `if cleared_mask_flat is not None and cleared_mask_flat.size == g_flat.size: valid = valid & ~cleared_mask_flat.astype(bool)`.
- Update the function docstring to document the new filter: change the opening line "Five filters compose with AND" to "Six filters compose with AND" and add a "(6) Cleared mask — when ``cleared_mask_flat`` is provided and matches ``g_flat`` size, exclude pixels where the cleared mask is True. Shape mismatch silently bypasses (caller surfaces a status message — see U2)." entry.

**Patterns to follow:**
- Mirror the `mask_flat` block exactly: same shape-guard idiom (`.size == g_flat.size`), same `astype(bool)` coercion, same silent-bypass-on-mismatch behavior at this layer.

**Test scenarios:**
- Happy path: `cleared_mask_flat=None` → identical output to current (no-op when omitted).
- Happy path: `cleared_mask_flat` all-False → identical output to current.
- Happy path: `cleared_mask_flat` with some True → output excludes exactly those pixels (test with simple synthetic G/S).
- Edge case: `cleared_mask_flat` all-True → output is all-False.
- Edge case: composition with `mask_flat` AND `filter_ids` AND `cleared_mask_flat` → result equals the boolean intersection of all three predicates against `valid`.
- Edge case: `cleared_mask_flat.size != g_flat.size` → silently bypassed (no exception, no spurious filter); verifies the shape guard.
- Edge case: `cleared_mask_flat` provided as `uint8` 0/1 array (not strict bool) → `astype(bool)` coercion handles it correctly.

**Verification:**
- All `test_phasor_display.py` cases pass; the new cleared-mask cases assert outputs by direct boolean comparison on small synthetic arrays.

---

- U2. **Add `_cleared_mask` field, lifecycle, and helpers to `PhasorPlotWindow`; wire into `_compute_visible_valid_2d`; refactor `_refresh_histogram` to delegate**

**Goal:** Hold the cleared bitmap on the window, reset it on every (g, s) frame change via `set_phasor_data`, and pass it through the upstream filter call so every visible-pixel consumer (Apply, preview, AND the histogram render) sees it.

**Requirements:** R1, R3, R6, R7

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py` (extended in U5)

**Approach:**
- In `__init__`, after the existing `_roi_widgets` initialization (`phasor_plot.py:286`), add `self._cleared_mask: np.ndarray | None = None`.
- Add private helper `_apply_clear_to_roi(widget: _ROIWidget) -> None`:
  1. Compute the ROI's inside-mask via `phasor_roi_to_mask(g, s, center=roi.center, radii=roi.radii, angle_rad=np.radians(roi.angle_deg))` using the active G/S maps from `_get_active_gs_maps()`.
  2. **Early-return if the inside-mask is all-False** (`if not roi_inside.any(): post status "ROI has no pixels in active region — nothing to clear"; return`). Avoids polluting `_cleared_mask` with an empty allocation and keeps the lazy-allocation invariant clean ("`_cleared_mask is not None` ⇒ at least one pixel cleared").
  3. If `self._cleared_mask is None`, allocate `self._cleared_mask = np.zeros_like(g, dtype=bool)`.
  4. Shape-check: if `self._cleared_mask.shape != g.shape`, post a status-bar message ("cleared mask shape mismatch — resetting") and reallocate to `np.zeros_like(g, dtype=bool)` before OR'ing.
  5. OR the ROI inside-mask into `self._cleared_mask`.
- Add private helper `_reset_cleared_mask() -> None`: set `self._cleared_mask = None`, call `self._refresh_histogram()` synchronously (Reset is a discrete action — no debounce needed), and trigger button enable-state update (see U3).
- **Refactor `_refresh_histogram` (`phasor_plot.py:1454-1495`) to delegate the valid mask to `_compute_visible_valid_2d`** instead of calling `compute_valid_phasor_pixels` directly. The histogram path's existing `mask_bypassed` status check (lines 1467-1495) must be preserved — compute it inline in `_refresh_histogram` (using `_load_active_mask_flat()` and the `_mask_filter_check` state), then read the valid mask from `_compute_visible_valid_2d().ravel()` and proceed with the existing `g_flat[valid] / s_flat[valid] / np.histogram2d(...)` pipeline. Without this refactor the cleared mask never reaches the rendered histogram (the secondary cluster would still be drawn).
- In `_compute_visible_valid_2d` (`phasor_plot.py:1210`), pass `cleared_mask_flat=self._cleared_mask.ravel() if self._cleared_mask is not None else None` to the `compute_valid_phasor_pixels()` call. If `self._cleared_mask.size != g.size` at call time, surface a status-bar message with an 8-second timeout (`self._status.showMessage(msg, 8000)` — long enough to survive subsequent refresh churn that overwrites the status bar with per-ROI counts) and pass `None`.
- In `set_phasor_data` (`phasor_plot.py:1280-1295`), add `self._cleared_mask = None` next to the existing per-ROI `cached_mask` and `_active_mask_flat` resets. Same alignment rationale as those resets — the cleared mask is bound to the (g, s) frame, not to abstract pixel indices.
- `_on_dataset_changed` (`phasor_plot.py:1320`) does not need its own cleared-mask reset since dataset change funnels through `set_phasor_data`. Verify this and document.

**Patterns to follow:**
- The `mask_flat` plumbing for "Filter by active mask" — same flat-array passthrough pattern.
- `_load_active_mask_flat()` for the conditional `.ravel()` plus None-handling idiom.
- Existing status-bar message style for filter-related notifications (compose via the existing status bar API used elsewhere in this file).

**Test scenarios:**
- Happy path: with `_cleared_mask = None`, `_compute_visible_valid_2d()` returns the same boolean array as before this change (no behavioral drift when nothing is cleared).
- Happy path: `_apply_clear_to_roi` on a fresh window allocates `_cleared_mask` to `g.shape` and OR's the ROI's inside mask in.
- Happy path: a second `_apply_clear_to_roi` with a different ROI ORs additional True pixels into the existing `_cleared_mask` (cumulative).
- Edge case: `_apply_clear_to_roi` with an ROI whose inside-mask is all-False (ellipse on NaN region) → early-returns with status message; `_cleared_mask` stays `None`.
- Edge case: `_apply_clear_to_roi` when G/S shape mismatches the existing `_cleared_mask` reallocates and surfaces a status message (no AttributeError, no silent corruption).
- Lifecycle: `set_phasor_data` resets `_cleared_mask` to `None` (this is the single funnel, so this test covers dataset change, channel switch, harmonic switch, wavelet recompute, and cache reload).
- Lifecycle: `_reset_cleared_mask()` sets `_cleared_mask` back to `None` and triggers a histogram refresh.
- Integration: after `_apply_clear_to_roi`, `_compute_visible_valid_2d()` returns False at exactly the pixels inside the consumed ROI (and elsewhere unchanged).
- **Integration (the load-bearing one): after `_apply_clear_to_roi`, the rendered histogram (`_refresh_histogram` output — assert via the underlying `np.histogram2d` input or by reading back `self._hist_item`'s image data) excludes pixels in the cleared region.** Without this assertion, the P0 refactor of `_refresh_histogram` is unverified by the test suite.

**Verification:**
- Outcome: `_compute_visible_valid_2d()` reflects the cleared-mask state on every call after Clear, AND the histogram render path goes through it (no separate `compute_valid_phasor_pixels` call remains in `_refresh_histogram`).

---

- U3. **Refactor `_on_remove_roi` into `_remove_roi_widget(index)`; add Clear and Reset toolbar buttons**

**Goal:** Add the two new toolbar buttons with correct enable/disable behavior, and ensure Clear consumes the selected ROI through the same proven removal path used by Remove.

**Requirements:** R2, R4, R6

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`

**Approach:**
- Extract the body of `_on_remove_roi` (`phasor_plot.py:821-840`) into a new private helper `_remove_roi_widget(index: int) -> None` containing the pop / pyqtgraph-removal / label-reindex / cached-mask invalidation / selection reset / list refresh / cluster-marker update / `preview_roi_removed.emit` / `_preview_timer.start()` sequence. Have `_on_remove_roi` call `self._remove_roi_widget(self._selected_roi_index)` after its own guard check.
- In the toolbar construction near the existing Add ROI / Remove buttons (`phasor_plot.py:431-435`), add two new buttons:
  - **Clear within selected ROI** (label may shorten to "Clear in ROI" if layout requires) — connected to `_on_clear_within_roi`.
  - **Reset cleared** — connected to `_on_reset_cleared`.
- `_on_clear_within_roi(self) -> None`:
  1. Guard: if `self._selected_roi_index is None or not self._roi_widgets`, return.
  2. Capture `widget = self._roi_widgets[self._selected_roi_index]` and `index = self._selected_roi_index`.
  3. Call `self._apply_clear_to_roi(widget)` (from U2).
  4. If U2's early-return fired (no pixels cleared), return without consuming the ROI.
  5. Call `self._remove_roi_widget(index)`.
  6. **Synchronously refresh** — call `self._refresh_histogram()` and `self._update_preview()` directly. Skip `_filter_timer.start()`. Rationale: Clear is a discrete user action, not a continuous slider drag; debouncing creates a race window where the napari preview shows pre-Clear pixels while a user could click Apply Visible as Mask within the 150 ms window. Apply is fully synchronous and would emit a payload that disagrees with what the preview displays.
  7. Emit `_clear_state_changed` (see button-state plumbing below).
- `_on_reset_cleared(self) -> None`: call `self._reset_cleared_mask()` (from U2). Synchronous refresh inside `_reset_cleared_mask` updates the histogram; emit `_clear_state_changed`.
- **Button-state plumbing — use a private Qt signal instead of enumerating call sites.** Define `_clear_state_changed = Signal()` on `PhasorPlotWindow`. Connect it to `_update_clear_buttons_enabled` once at `__init__`. Emit it from:
  - `_on_roi_list_selection` (selection changed)
  - `_on_add_roi` (new ROI auto-selects)
  - `_remove_roi_widget` (selection cleared)
  - `_apply_clear_to_roi` (cleared mask now non-None)
  - `_reset_cleared_mask` (cleared mask now None)
  - `set_phasor_data` (cleared mask reset; selection unchanged but enable-state may need refresh anyway)
  - `_on_load_rois` (ROI list replaced; selection reset)
  - `place_gmm_rois` (multiple ROIs added)

  This keeps button enable-state correct without an enumerate-call-sites trap. New mutation sites added in the future just emit the signal; the connection wiring stays in one place. The signal-based pattern is also what `gui-action-contract-exhaustiveness.md` recommends to avoid the regression class.

- `_update_clear_buttons_enabled(self) -> None` (slot connected to `_clear_state_changed`):
  - **Clear within selected ROI** enabled iff `self._selected_roi_index is not None and 0 <= self._selected_roi_index < len(self._roi_widgets)`.
  - **Reset cleared** enabled iff `self._cleared_mask is not None`. The U2 early-return guarantees that any non-None `_cleared_mask` has at least one True pixel, so the `.any()` reduction is unnecessary (and was O(H*W) per call — measurable on 4K images).

**Patterns to follow:**
- Toolbar button construction style at `phasor_plot.py:431-435` (Add ROI / Remove buttons).
- ROI-selection signal pattern at `_on_roi_list_selection` (`phasor_plot.py` near line 909) — hook button-state updates here.
- Identity-capture pattern in any per-widget lambdas (already used in current code; do not regress).

**Test scenarios:**
- Happy path (Remove unchanged): `_on_remove_roi` continues to remove the selected ROI exactly as before; existing `test_phasor_remove_roi.py` remains green without modification.
- Happy path (Clear): with one ROI selected, clicking Clear consumes it: ROI list shrinks by one, `_cleared_mask` becomes non-None with True at the consumed ROI's pixels, `preview_roi_removed` is emitted with the consumed ROI's name.
- Happy path (Reset): with `_cleared_mask` non-empty, clicking Reset sets `_cleared_mask` back to `None`, histogram refresh fires, Reset button becomes disabled.
- Edge case (button enable): with no ROI selected, Clear button is disabled; selecting an ROI enables it.
- Edge case (button enable): with `_cleared_mask = None` or all-False, Reset button is disabled; after one Clear, Reset becomes enabled.
- Edge case (Clear with no selection): direct call to `_on_clear_within_roi` returns early without exception (defensive).
- Integration: Clear consumes ROI → napari preview layer for that ROI is removed (assert the `preview_roi_removed` signal AND, if a real-viewer fixture is available, that `_phasor_roi_preview_<name>` no longer exists in the viewer).
- Integration: dataset change resets `_cleared_mask` AND disables Reset button.

**Verification:**
- Outcome: pressing Clear with a selected ROI removes that ROI, hides its pixels from the histogram immediately (no debounce gap during which Apply could emit a stale payload), and emits `preview_roi_removed`. Pressing Reset with cleared pixels restores them. Buttons grey out / enable correctly across all mutation sites because all paths emit `_clear_state_changed`.

---

- U4. **Register the two new buttons in the GUI element classification audit**

**Goal:** Per the project's GUI state ownership rules, every interactive UI element is classified in `docs/audits/gui-element-classification.yaml` as Selector, Creator, or Action. Both new buttons are Actions (no session-field mutation).

**Requirements:** R2 (audit hygiene)

**Dependencies:** U3 (the buttons must exist before they can be classified)

**Files:**
- Modify: `docs/audits/gui-element-classification.yaml`

**Approach:**
- Add an entry for **Clear within selected ROI**: class `Action`, owner `PhasorPlotWindow`, reads `_selected_roi_index`/`_roi_widgets`/active G/S maps, writes `_cleared_mask` (peer-local, NOT a session field).
- Add an entry for **Reset cleared**: class `Action`, owner `PhasorPlotWindow`, reads `_cleared_mask`, writes `_cleared_mask` (peer-local).
- Mirror the YAML shape used by adjacent entries in the file.

**Patterns to follow:**
- Existing entries for Remove ROI and Apply Visible as Mask (other Action-class buttons in the same window).

**Test scenarios:**
- Test expectation: none — pure documentation/audit update with no runtime behavior. Verification is human review of the YAML diff.

**Verification:**
- Outcome: the audit file enumerates both new buttons; a reviewer can confirm neither writes any of the five session selection fields.

---

- U5. **Add unit and integration tests covering the cleared-mask filter end-to-end**

**Goal:** Mirror the existing per-filter regression suite for the new filter, and extend the structural-equality contract test to cover it.

**Requirements:** R1, R3, R4, R6 (and the load-bearing invariants from `phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`)

**Dependencies:** U1, U2, U3

**Files:**
- Modify: `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py`
- Modify: `tests/test_flim/test_phasor_display.py` (covered in U1 but listed here for completeness if additional integration cases land here)

**Approach:**
- Add `test_apply_respects_cleared_mask`: mirror the structure of `test_apply_respects_active_mask_filter` (`test_phasor_apply_visible_as_mask.py:73`). Set up a phasor window with two synthetic ROIs over disjoint regions, Clear one of them, Apply the other, assert the Apply output excludes pixels in the cleared region.
- Extend `test_apply_equals_napari_preview` (`test_phasor_apply_visible_as_mask.py:97`) to add a case with a non-empty `_cleared_mask`. Assert the `mask_applied` payload for the surviving ROI equals the corresponding `preview_roi_upserted` payload pixel-for-pixel — this is the load-bearing structural-equality contract.
- Add `test_clear_consumes_roi_and_emits_removed_signal`: assert that after Clear, the ROI list shrinks by one, `preview_roi_removed` was emitted with the consumed ROI's name, and `_cleared_mask` has True at exactly the consumed ROI's interior pixels.
- Add `test_reset_cleared_restores_visibility`: Clear → assert valid-pixel count drops; Reset → assert valid-pixel count returns to pre-Clear value; Reset button disables itself afterward.
- Add `test_clear_button_disabled_with_no_selection` and `test_reset_button_disabled_with_empty_mask`: pure UI-state assertions on `isEnabled()` after constructing the window and after relevant state transitions.
- Add `test_dataset_change_resets_cleared_mask`: Clear → switch dataset → assert `_cleared_mask is None` and Reset button is disabled.
- Add `test_set_phasor_data_resets_cleared_mask`: Clear → call `set_phasor_data` with a fresh G/S frame (simulates harmonic switch / wavelet recompute / channel switch) → assert `_cleared_mask is None`. This is the load-bearing alignment-invariant test.
- Add `test_clear_does_not_invalidate_surviving_roi_cached_mask`: create two ROIs, prime `roi2.cached_mask` via `_compute_filtered_binary(roi2)`, then Clear roi1. Assert `roi2.cached_mask is` the same object (identity, not equality) AND that `_compute_filtered_binary(roi2)` returns a binary that excludes the cleared region. Pins both the per-ROI cache invariant AND the AND-composition correctness in one test.
- Add `test_clear_does_not_write_session_fields`: wrap the session with a spy that fails on any `set_active_*` / `set_filter_ids` / `set_selection` call. Invoke `_on_clear_within_roi` and `_on_reset_cleared`. Assert no session-mutator was called. Encodes the Action contract by behavior, not by audit comment.
- Add `test_histogram_render_excludes_cleared_pixels`: create a window with a known G/S frame, Clear an ROI, then read the histogram render output (via `self._hist_item`'s image data or by asserting on the `np.histogram2d` input arrays). Assert the cleared region's bins are zero. Without this test, the U2 refactor of `_refresh_histogram` is unverified — Apply / preview tests alone do not cover the histogram render path.
- Add `test_apply_button_during_clear_no_race`: Clear → immediately invoke `_on_apply_mask` (no event-loop spin between them). Assert the Apply payload AND the napari preview payload both exclude cleared pixels. Pins the synchronous-refresh decision from U3.

**Patterns to follow:**
- Existing `phasor_window` and `session_with_dataset` fixtures in `test_phasor_apply_visible_as_mask.py`.
- The `test_apply_respects_*` pattern (one test per filter, same skeleton).
- The structural-equality assertion pattern in `test_apply_equals_napari_preview` (compare the `mask_applied` payload to `preview_roi_upserted` for the same ROI).

**Test scenarios:**
- Happy path: `test_apply_respects_cleared_mask` — Apply output excludes cleared pixels.
- Happy path: extended `test_apply_equals_napari_preview` — preview and Apply payloads remain equal when a cleared mask is in play.
- Integration: `test_clear_consumes_roi_and_emits_removed_signal` — ROI removed AND signal emitted AND cleared bitmap populated.
- Integration: `test_reset_cleared_restores_visibility` — full Clear/Reset round-trip.
- Edge case: `test_clear_button_disabled_with_no_selection`, `test_reset_button_disabled_with_empty_mask`.
- Lifecycle: `test_dataset_change_resets_cleared_mask`.

**Verification:**
- Outcome: full test suite green; new tests fail meaningfully if the upstream filter chain or the consumed-ROI removal path regresses.

---

## System-Wide Impact

- **Interaction graph:** Three production call sites consume the filter chain — `_compute_visible_valid_2d` (`phasor_plot.py:1210`), `_refresh_histogram` (`phasor_plot.py:1475`), and `RunPhasorGMM.execute` (`run_phasor_gmm.py:203`). U2 refactors `_refresh_histogram` to delegate to `_compute_visible_valid_2d`, leaving one effective integration point inside `phasor_plot.py`. After the refactor, every visible-pixel consumer (histogram render, per-ROI preview, Apply Visible as Mask, valid-pixel counter) genuinely flows through the single helper. The GMM call site stays unchanged (cleared mask is local to `PhasorPlotWindow`; GMM runs from `FlimPanel`).
- **Error propagation:** Shape-mismatch between `_cleared_mask` and the active G/S maps is the only realistic error path. Surfaced as a status-bar message with an 8-second timeout (avoids being immediately overwritten by per-ROI count messages); the cleared filter is bypassed for that frame to keep the histogram rendering. The shape-mismatch path is also defensively unlikely after the U2 lifecycle change, since `set_phasor_data` resets `_cleared_mask` whenever the G/S frame changes.
- **State lifecycle risks:** `_cleared_mask` outliving the (g, s) frame it was drawn against is the primary risk. Mitigated by the `set_phasor_data` reset, which fires on dataset change, channel switch, harmonic switch, wavelet recompute, and cache reload. Per-ROI `cached_mask` is independent and is NOT invalidated when `_cleared_mask` changes (the AND-composition `cached_mask & visible` in `_compute_filtered_binary` picks up the new visible state automatically; pinned by `test_clear_does_not_invalidate_surviving_roi_cached_mask`).
- **API surface parity:** `compute_valid_phasor_pixels` gains an optional kwarg with default `None`, preserving every existing call site's behavior. The 18 unit-test call sites in `tests/test_flim/test_phasor_display.py` continue to work unchanged.
- **Integration coverage:** Three load-bearing cross-layer tests:
  - `test_apply_equals_napari_preview` (extended) — Apply output equals preview output pixel-for-pixel, including with a cleared mask in play.
  - `test_histogram_render_excludes_cleared_pixels` — the rendered histogram (not just Apply / preview) actually excludes cleared pixels. Without this, the U2 refactor of `_refresh_histogram` is silently unverified.
  - `test_set_phasor_data_resets_cleared_mask` — the alignment invariant is enforced by behavior, not just by the comment in `set_phasor_data`.
- **Unchanged invariants:** "Apply output ⊆ napari preview ⊆ histogram" (the contract established by `phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`) must continue to hold with the cleared-mask filter active. The structural-equality test extension proves this. None of the five session selection fields (`active_channel`, `active_segmentation`, `active_mask`, `filter_ids`, `selection`) are written by either new button — encoded by `test_clear_does_not_write_session_fields`.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Clear handler reproduces the consumed-ROI removal sequence inline and drifts from `_on_remove_roi`, breaking `preview_roi_removed` emission and napari layer cleanup. | U3 mandates extracting `_remove_roi_widget(index)` and routing both Remove and Clear through it. The integration test `test_clear_consumes_roi_and_emits_removed_signal` asserts the signal is emitted with the consumed ROI's name. |
| `_cleared_mask` survives a (g, s) frame change (channel switch, harmonic switch, wavelet recompute, cache reload), silently masking the wrong pixels in the new frame. **This is the highest-impact risk** — when shapes happen to match (common case for same-camera channel switches), there is no shape-mismatch fallback, so the corruption is silent. | U2 adds `_cleared_mask = None` to `set_phasor_data` alongside the existing per-ROI / `_active_mask_flat` resets — same alignment rationale, same site, one funnel for all recompute paths. `test_set_phasor_data_resets_cleared_mask` enforces this. |
| `_refresh_histogram` continues to call `compute_valid_phasor_pixels` directly and ignores `cleared_mask_flat`, so the rendered histogram still shows cleared pixels even though Apply / preview hide them. | U2 refactors `_refresh_histogram` to delegate to `_compute_visible_valid_2d`. `test_histogram_render_excludes_cleared_pixels` enforces this — Apply / preview tests alone cannot catch the failure. |
| User clicks Apply Visible as Mask within the 150 ms debounce window after Clear. Apply is synchronous and would emit a payload that disagrees with the still-stale napari preview the user is looking at. | U3 makes the post-Clear refresh synchronous (skip `_filter_timer`). `test_apply_button_during_clear_no_race` enforces this. |
| `_cleared_mask` shape silently drifts from the active G/S maps despite the lifecycle reset (e.g., a future feature mutates G/S in place without going through `set_phasor_data`). | Defense-in-depth: U1's shape guard at the upstream layer + U2's status-bar message at the call site (with 8-second timeout to survive status-bar churn). The user gets a visible notification rather than silent restoration. |
| Future contributor inlines `cached_mask & visible` and caches the combined result, breaking the "per-ROI `cached_mask` doesn't need invalidation when `_cleared_mask` changes" claim. | `test_clear_does_not_invalidate_surviving_roi_cached_mask` pins both the perf claim (object identity check) and the correctness claim (output excludes cleared region). |
| Empty inside-mask (ROI on NaN region) pollutes `_cleared_mask` with a non-None all-False bitmap, breaking the "non-None ⇒ has cleared pixels" invariant that the simplified Reset enable-state rule depends on. | U2's early-return in `_apply_clear_to_roi` when `roi_inside.any()` is False. ROI is not consumed; status message explains why. |

---

## Documentation / Operational Notes

- No user-facing docs changes required for v1 (no online help system in place; the buttons are self-explanatory and follow existing voice).
- `docs/audits/gui-element-classification.yaml` updated as part of U4.
- No migration concerns — no on-disk format change.
- No rollout / monitoring considerations — pure GUI feature behind no flag.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-04-phasor-clear-within-roi-requirements.md](docs/brainstorms/2026-05-04-phasor-clear-within-roi-requirements.md)
- Single-source-of-truth precedent: `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`
- Removal-signal precedent: `docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`
- Action contract rule: `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
- Cross-layer alignment: `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`
- Lifecycle reset pattern: `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
- Multi-ROI patterns: `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
- ROI API: `docs/solutions/logic-errors/phasor-roi-to-mask-api-mismatch.md`
- Primary edit site: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Filter chain home: `src/percell4/domain/flim/phasor_display.py`
- Existing test suite to mirror: `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py`
