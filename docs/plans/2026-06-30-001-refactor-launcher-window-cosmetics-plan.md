---
title: "refactor: Launcher window cosmetic / UX cleanup"
type: refactor
status: active
date: 2026-06-30
origin: docs/brainstorms/2026-06-30-launcher-window-cosmetic-refactor-requirements.md
---

# refactor: Launcher window cosmetic / UX cleanup

## Overview

A presentation-and-pruning pass over the PerCell4 launcher window (`src/percell4/interfaces/gui/main_window.py`, `LauncherWindow`). Re-labels and re-groups controls to match how the user thinks, removes retired/redundant affordances, and consolidates two near-identical tabs. No engine behavior changes — every retained capability stays reachable. The launcher goes from **8 tabs to 7** (Scripts + Workflows merge), the I/O tab from **8 buttons to 5** (two become menu buttons), the Segment tab loses a redundant Save button, the Analysis tab loses a module and is reordered, and the Cell Filter Selector relocates to the Viewer tab.

---

## Problem Frame

The launcher works correctly but is organized around the code's technical structure rather than the user's mental model: I/O button names assume insider knowledge, retired analysis methods clutter the Adaptive Local Clipping and Analysis panels, a redundant Save button persists despite full auto-save, the Cell Filter (a selection control) sits among analyses, and Scripts/Workflows are split across two tabs the user treats as one. See origin: `docs/brainstorms/2026-06-30-launcher-window-cosmetic-refactor-requirements.md`.

---

## Requirements Trace

**I/O tab**
- R1. Five I/O buttons matching the user's verbs: New Dataset, Open Dataset, `Add Data ▾`, Close Dataset, `Export ▾`. → U3
- R2. `Add Data ▾` is a menu button exposing Layer… (AddLayerDialog) and Batch TCSPC… (BatchTCSPCDialog). → U3
- R3. `Export ▾` is a menu button exposing Measurements (CSV)…, Images (TIFF)…, Phasor (.npz)…. → U3

**Viewer tab**
- R4. Add `Hide Viewer` alongside `Open Viewer`; viewer singleton + layers persist. → U1
- R5. Move Cell Filter (Clear Selection / Filter to Selection / Clear Filter / count) from Analysis to the Viewer tab; behavior unchanged. → U2

**Segment tab**
- R6. Merge Manual Editing + Label Cleanup into one editing module. → U4
- R7. Remove redundant `Save Labels to HDF5` button; add an "edits auto-saved" reassurance. → U4

**Analysis tab**
- R8. Remove the Iterative Otsu Thresholding module. → U5
- R9. Reorder to Adaptive Local Clipping → Particle Analysis → Measurements → Grouped → Whole-Field. → U7
- R10. Strip Adaptive Local Clipping to auto-extract (two-pass) only; keep Auto-detect-smallest, Smallest Ø (its field back-fills the LoG-detected diameter after a run — this replaces the separate Detected Ø readout, which belonged to the removed particle mode), Gaussian σ, Min particle size. → U6
- R11. Remove unused Adaptive Local Clipping fields (method dropdown, Size percentile, Size cutoff Ø, Auto-start-window, Iterations, Noise combo, k, Window). → U6
- R12. CNR tools (Classify / Segment by CNR) remain unchanged. → U6 (constraint)

**Analyses & Workflows tab**
- R13. Merge Scripts + Workflows into one `Analyses & Workflows` tab with two sections (`Analyses` + `Workflows`). → U8

**Cross-cutting**
- R14. All changes confined to the launcher tree; FLIM and Data tabs, viewer internals, and engine behavior untouched. → scope boundary, all units

**Origin acceptance examples:** AE1 (Hide→Open preserves the viewer instance) → U1; AE2 (edits persist with no Save button) → U4; AE3 (Add Data menu → Batch TCSPC opens unchanged dialog) → U3; AE4 (Adaptive Clip shows only the kept fields) → U6.

---

## Scope Boundaries

- No changes to detection, measurement, or workflow **engine** behavior — presentation and dead-UI removal only.
- FLIM and Data tabs untouched.
- Cell Filter stays a launcher control (Viewer tab) — **not** docked inside the napari viewer window.
- Iterative Otsu removal is **GUI-panel-only**. The workflow method (`workflows/models.py` `IterativeOtsuSettings`, `workflows/phases.py`, `workflows/artifacts.py`) and domain layer (`domain/measure/iterative_otsu*.py`) stay — they are live and serialization-bearing.
- Adaptive Local Clipping cleanup removes the **GUI affordance + GUI-only worker functions + GUI settings widgets** for retired modes. Shared domain functions (`domain/measure/auto_extraction.auto_extract`, etc.) are untouched.
- Scripts/Workflows merge is **visual only** — the `@register_analysis` registry and the hard-coded workflow handlers are not unified.
- No broad theme overhaul; reuse `theme.py` constants and existing section idioms.

### Deferred to Follow-Up Work

- Paying down the `segmentation_panel.py` `launcher=self` coupling (documented tech debt). U4 must not deepen it, but the broader decoupling is out of scope here.

---

## Context & Research

### Relevant Code and Patterns

- **Tab registration** — `main_window.py` `LauncherWindow._create_central_widget` builds a single `categories` list of `(name, factory)` tuples; a single `enumerate` loop keeps sidebar-button index == stack index 1:1. Removing a tuple auto-renumbers; only hardcoded index is `_on_sidebar_click(0)` at the end. (Merging tabs = edit this list + one new factory.)
- **Section idiom** — `theme.section_label(text)` (H1, once per panel) + `QGroupBox("Title")` (H2 sections). `io_panel.py` `IoPanel._build_ui` is the canonical `section_label` + two-`QGroupBox` template. Embedding a child panel = wrap the `QWidget` in a `QGroupBox` (as `analysis_panel.py` does for the three embedded panels).
- **Menu buttons (net-new)** — no `QToolButton.setMenu` precedent exists. Closest idiom: `QMenu` + `addAction(...).triggered.connect(...)` in `peer_views/cell_table.py` `CellTableWindow._show_context_menu`. Plan: plain `QPushButton.setMenu(QMenu)` (auto-adds ▾). **Theming caveat:** `QMenu` rules live in the launcher's *local* stylesheet (`main_window.__init__`), not the global `APP_STYLESHEET`; popups are top-level windows — verify the dropdown renders themed, and that the ▾/arrow is not dark-on-dark (see `docs/solutions/ui-bugs/ui-theme-refactor-lessons.md` Bug 3, the temp-SVG-arrow fix).
- **Task-panel callback injection** — `IoPanel` is a pure-action panel receiving injected `Callable`s (`on_import`, `on_load`, `on_add_layer`, `on_batch_tcspc`, `on_close`, `on_export_csv`, `on_export_images`, `on_export_phasor_npz`). Menu consolidation groups the **same callables** under `QMenu` actions; wiring stays in `main_window._create_io_panel`. Do **not** reintroduce `launcher=self` into `IoPanel` (`docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`).
- **Cell Filter block** — `analysis_panel.py` `AnalysisPanel`: handlers `_on_clear_selection` (`set_selection([])`), `_on_filter_to_selection` (reads `selected_ids` → `set_filter(...)`), `_on_clear_filter` (`set_filter(None)`), `_on_filter_state_changed` (reads `is_filtered`/`filtered_df`/`df`, toggles Clear-Filter enabled + count label). Depends only on `self.data_model` (CellDataModel) + `self._show_status`. Moves cleanly.
- **Green/primary button** — no helper; inline `setStyleSheet` snippet using `theme.ACTION_GREEN`/`ACTION_GREEN_HOVER`, reserved for Run/Accept. New menu buttons stay **plain grey** to match `io_panel`.
- **Adaptive auto-extract dispatch** — `adaptive_clip_panel.py` `_run_auto_extract_mode` (the surviving Creator path, gated by `config.auto_extract_mode`) calls `run_adaptive_auto_extract` / `_stack` with `(image, labels, smallest_px, config.gaussian_sigma, min_spot_px)`. Confirms Gaussian σ is live (feeds `presmooth_sigma_px`); k and Window are never passed.

### Institutional Learnings

- **GUI Action/Selector/Creator contract is exhaustive** (`docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`). Every relabeled/moved/merged widget stays exactly one class; the five session fields are written only by Selectors/Creators. Hide Viewer, the I/O menu items, and the merged edit buttons are **Actions** — no off-label session writes.
- **Consolidate canonical state — do not duplicate the Cell Filter Selector** (`.../consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`, high). End state must have exactly **one** writer of `filter_ids` and exactly one Cell Filter widget (`selection` is legitimately multi-writer). Move, don't copy.
- **Session → napari is one-way; napari layer-events → session is forbidden** (`.../session-to-napari-one-way-push.md`). Cell Filter writes go straight to Session, never through `viewer.layers.selection.events.*`. Run the detection grep after the move.
- **Relocated Selector must carry its subscriber-rebind** (`.../session-bridge-event-forwarding.md` + `docs/audits/subscriber-rebind-matrix.md`). Move `_on_filter_state_changed` and its `state_changed` subscription with the widget.
- **Living audit artifacts must be updated in the same PR** — `docs/audits/gui-element-classification.yaml`, `session-mutation-graph.md`, `subscriber-rebind-matrix.md` pin the Cell-Filter Selectors at `analysis_panel.py`; relocating makes those entries stale.
- **Creator four-step contract** (`.../creator-contract-four-step-sequence-2026-05-18.md`, canonical). Save removal: confirm auto-save covers persistence for every path the button covered (it does — `_persist_labels_layer`; neither Save nor auto-save ever did the resource-list refresh, which happens at creation). Keep all four steps in the surviving ALC `_run_auto_extract_mode` Creator.
- **Adaptive-clip time-lapse contract** (`.../extending-per-cell-detection-to-time-lapse-2026-06-25.md`). When removing other ALC modes, keep the `NoParticlesFound` → empty-plane degradation, exact-`T` plane emission, `SIZE_NUM_SIGMA = 30`, and per-frame CNR pooling.
- **Adaptive-clip window/k convention** (`.../adaptive-clip-window-and-k-rules-2026-06-23.md`). Presmooth ideally fixed at 1px and non-user-facing; window derived in physical µm. Note: we keep Gaussian σ visible per the user's explicit brainstorm decision (it is live, defaults to 1.0) — a conscious deviation, recorded below.

### External References

- None — internal Qt/PyQt work following established local patterns. External research intentionally skipped.

---

## Key Technical Decisions

- **Cell Filter is relocated, not duplicated.** Removed from `AnalysisPanel`, added to the Viewer-tab host, with its handlers and `state_changed` subscription. Exactly one writer of **`filter_ids`** afterward. `selection` is intentionally multi-writer (viewer canvas, cell table, data plot, multi-select, threshold-QC, plus the Cell Filter's own Clear Selection), so the single-writer invariant and the move-not-copy rule apply to `filter_ids` and to not duplicating the Cell Filter widget — **not** to `selection`. (see origin: Key Decisions; learning: consolidate-canonical-state)
- **Viewer tab becomes a real `ViewerPanel(QWidget)` class** under `task_panels/`, mirroring `IoPanel`/`AnalysisPanel` constructor-injection (`data_model`, `show_window`, `get_viewer_window`, `show_status`) — rather than inlining filter handlers as `LauncherWindow` methods. Keeps the launcher decoupled and gives the relocated Selector a proper subscriber host.
- **Hide Viewer = pure `window.hide()` with no subscription teardown.** The viewer stays live and subscribed, so the "deaf-on-reopen" failure mode (`docs/solutions/ui-bugs/phasor-plot-deaf-to-session-after-close-reopen-2026-06-18.md`) cannot arise and no `showEvent` resync is needed. No window-flag manipulation, so the `setWindowFlag`-hides-visible pitfall is avoided.
- **Iterative Otsu removal is GUI-only.** Delete `gui/iterative_otsu_panel.py` + `gui/_iterative_otsu_settings.py` (the latter imported only by the former) + the panel's instantiation; keep the workflow method and domain layer. Avoids the additive-serialization legacy-config breakage entirely.
- **Adaptive Local Clipping: keep the live field, cut the dead ones.** Gaussian σ feeds `presmooth_sigma_px` (verified at `adaptive_clip_panel.py:947`) → keep. k and Window are never passed to `run_adaptive_auto_extract` → cut. The Smallest-particle Ø override is the physically meaningful window control (window is derived from it). (see origin: Key Decisions)
- **Gaussian σ stays user-visible** despite the "fix at 1px" convention, honoring the explicit brainstorm decision; field defaults to 1.0. Recorded as a conscious deviation.
- **I/O menu buttons group the existing injected callables under `QMenu`** — no new launcher coupling, no engine routing change.
- **Tab/section/order edits are mechanical** — edit the `categories` list (U8), move `addWidget` blocks (U7); no index or cross-module signal coupling.

---

## Open Questions

### Resolved During Planning

- *Does removing Save lose any edits?* No — auto-save (`_persist_labels_layer` + debounced `_autosave_timer`) covers every manual-edit path; Cellpose/Track Creators persist via their use cases at creation. (Reference-checked.)
- *Are the retired ALC workers / Iterative Otsu panel referenced by CLI or workflows?* The 5 `run_adaptive_detection*` GUI workers: no. The Iterative Otsu **panel/settings**: GUI-only; its **domain/workflow** layers: yes (kept). (Reference-checked.)
- *Does Add Data need both Layer and Batch TCSPC?* Yes — `BatchTCSPCDialog` (multi-dataset batch) and `AddLayerDialog`'s TCSPC tab (single-session) are functionally distinct.
- *Is Gaussian σ dead like k/Window?* No — it is passed as `presmooth_sigma_px`. Keep it.
- *Is `selection` single-writer like `filter_ids`?* No — `selection` is intentionally multi-writer (viewer canvas, cell table, data plot, multi-select, threshold-QC). Only `filter_ids` is single-writer; the U2 grep gate and invariant claims are narrowed accordingly. (doc-review: adversarial)
- *Is the "Detected Ø (µm)" readout fed by auto-extract?* No — it is the removed particle mode's widget; auto-extract back-fills the **Smallest Ø** field instead. The separate readout is dropped in U6. (doc-review: feasibility + adversarial + design-lens)
- *Does `_print_settings_debug` survive the field removal?* No — it reads removed config fields and is called on every Run; U6 rewrites/deletes it to avoid an AttributeError. (doc-review: feasibility)
- *Should the I/O menu items be enable-gated?* No — keep parity with today's always-enabled buttons; per-action gating is out of scope. (doc-review: design-lens)

### Deferred to Implementation

- Exact `QMenu` theming outcome for the I/O menu buttons — verify the popup inherits the launcher's local `QMenu` rules; if it renders default Fusion or a dark-on-dark arrow, replicate the four `QMenu` rules locally and apply the temp-SVG arrow fix.
- Whether `tests/test_gui/test_iterative_otsu_settings_widget.py` is deletable wholesale or partially (depends on whether it imports only the GUI widget — expected yes).
- Exact final wording of the new I/O button labels (New vs Create, etc.) — adjustable at implementation; defaults per the table above.

---

## Implementation Units

- U1. **Extract `ViewerPanel` and add Hide Viewer**

**Goal:** Replace the inline bare-`QWidget` Viewer tab with a `ViewerPanel(QWidget)` class hosting Open Viewer + a new Hide Viewer button.

**Requirements:** R4

**Dependencies:** None

**Files:**
- Create: `src/percell4/interfaces/gui/task_panels/viewer_panel.py`
- Modify: `src/percell4/interfaces/gui/main_window.py` (`_create_viewer_panel` → instantiate `ViewerPanel`; add a `_hide_window("viewer")` helper or pass `get_viewer_window`)
- Test: `tests/test_gui/test_viewer_panel.py`

**Approach:**
- `ViewerPanel(data_model, *, show_window, get_viewer_window, show_status)`, mirroring `IoPanel`/`AnalysisPanel` injection. `section_label("Viewer")` + the two buttons.
- Open Viewer → `show_window("viewer")` (unchanged). Hide Viewer → `win = get_viewer_window(); win and win.hide()`. No subscription teardown, no window-flag change.
- Two separate static buttons (per user decision). Give each a clarifying tooltip so the state is unambiguous: Hide Viewer → "Hide the viewer window; the viewer stays active", Open Viewer → "Open or re-show the viewer window". This keeps the affordance robust regardless of how the window's visibility changed (including the user closing the napari window directly).

**Patterns to follow:** `io_panel.py` `IoPanel` (constructor injection, section/button layout); existing `_create_viewer_panel` for the `show_window("viewer")` call.

**Test scenarios:**
- Covers AE1. Happy path: open viewer (instance created), hide it (`isVisible()` False), open again → same instance object, layers preserved (no reload).
- Edge case: Hide Viewer clicked when no viewer exists yet → no-op, no exception.
- Edge case: a `set_filter` issued while the viewer is hidden is reflected after re-show (viewer stayed subscribed) — round-trip assertion on rendered/selection state.

**Verification:** Viewer tab shows both buttons; hide/show round-trips on a live viewer without destroying it or dropping session sync.

---

- U2. **Relocate the Cell Filter Selector to `ViewerPanel`**

**Goal:** Move the canonical Cell Filter Selector (the sole `filter_ids` writer) from Analysis into the Viewer tab, with its subscriber-rebind, leaving exactly one `filter_ids` writer and one Cell Filter widget.

**Requirements:** R5

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/viewer_panel.py` (add Cell Filter group + handlers + `state_changed` subscription)
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (remove the Cell Filter group, its four handlers, and the filter branch of `_on_state_changed`; keep the Whole-Field channel-label branch)
- Modify: `docs/audits/gui-element-classification.yaml` (rename the three cell-filter `id:` fields from `analysis_panel.*` to `viewer_panel.*`; check no other artifact cross-references the old IDs by string), `docs/audits/subscriber-rebind-matrix.md` (**split** the AnalysisPanel filter row — AnalysisPanel keeps its channel-display `state_changed` subscriber, so add a new `ViewerPanel` row for the filter subscriber rather than repointing), `docs/audits/session-mutation-graph.md` (update the writer/reader count table and the "Borderline: filter / clear-filter buttons" prose section to point at `viewer_panel.py`)
- Test: `tests/test_gui/test_viewer_panel.py`; update `tests/test_gui/test_analysis_widgets.py` if it asserts the filter group's presence in Analysis

**Approach:**
- Port `_on_clear_selection`, `_on_filter_to_selection`, `_on_clear_filter`, `_on_filter_state_changed` verbatim; wire writes straight to `data_model.set_selection`/`set_filter` (never via napari layer events). Subscribe `data_model.state_changed` in `ViewerPanel.__init__`; call the filter-state handler on `change.filter`/`change.data`.
- The tab stays named "Viewer" (per user decision); wrap the Cell Filter controls in their own `QGroupBox("Cell Filter")` so they read as a distinct, findable section below the Open/Hide Viewer buttons.
- After moving, run the detection greps: `viewer.layers.selection.events` must not reach any `set_active_*`/`set_filter`/`set_selection`. The single-writer check is **`filter_ids` only** — exactly one UI `set_filter` writer site (the `model.py` `set_filter` delegate is plumbing, not a second writer). Do **not** reduce `set_selection` to one writer; it is legitimately multi-writer (viewer canvas, cell table, data plot, multi-select, threshold-QC).

**Execution note:** Treat as a Selector relocation, not a copy — verify single-writer before considering the unit done.

**Patterns to follow:** the existing Cell Filter block in `analysis_panel.py`; subscriber pattern in `AnalysisPanel._on_state_changed`.

**Test scenarios:**
- Happy path: select cells → `Filter to Selection` sets `filter_ids`; Clear Filter resets to `None`; Clear Selection empties `selection`.
- Integration: a `set_filter` from elsewhere updates the relocated count label and toggles the Clear-Filter button enabled-state (subscriber-rebind intact).
- Edge case: `Filter to Selection` with empty selection → status message, no filter applied.
- Regression: Analysis tab no longer constructs the Cell Filter group; grep confirms a single UI writer of `filter_ids` (and that `set_selection` writers in viewer/table/plot remain untouched).

**Verification:** Cell Filter works identically from the Viewer tab; Analysis has no filter widgets; audit artifacts updated; single-writer invariant holds.

---

- U3. **I/O menu-button consolidation**

**Goal:** Collapse 8 I/O buttons to 5, with `Add Data ▾` and `Export ▾` as menu buttons over the existing callables.

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/io_panel.py` (rebuild Import/Export groups: New Dataset, Open Dataset, `Add Data ▾`, Close Dataset, `Export ▾`)
- Modify: `src/percell4/interfaces/gui/main_window.py` `_create_io_panel` only if the injected-callable wiring needs relabeling (keep all callables)
- Test: `tests/test_gui/test_io_panel_batch_tcspc_wiring.py` (extend for menu wiring) and/or a new `tests/test_gui/test_io_panel_menus.py`

**Approach:**
- New Dataset → `on_import` (CompressDialog); Open Dataset → `on_load`; Close Dataset → `on_close`.
- `Add Data ▾`: `QPushButton.setMenu(QMenu)` with actions Layer… → `on_add_layer`, Batch TCSPC… → `on_batch_tcspc`.
- `Export ▾`: menu actions Measurements (CSV)… → `on_export_csv`, Images (TIFF)… → `on_export_images`, Phasor (.npz)… → `on_export_phasor_npz`.
- Plain grey buttons (no green). Verify `QMenu` popup theming (see Context caveat).

**Patterns to follow:** `peer_views/cell_table.py` `_show_context_menu` (QMenu/addAction/triggered.connect); `io_panel.py` existing group layout.

**Test scenarios:**
- Covers AE3. Happy path: triggering each menu action invokes the correct injected callable exactly once (assert via stubbed callables).
- Happy path: the five top-level controls exist with expected labels; no orphaned old buttons remain.
- Edge case: menu actions are always enabled, preserving parity with today's always-enabled I/O buttons. Per-action enable-gating (e.g. Export disabled until measurements exist) is explicitly out of scope for this cosmetic pass.

**Verification:** I/O tab shows 5 controls; every prior import/export action remains reachable via a button or menu item; menu popups are themed.

---

- U4. **Segment: merge editing sections, remove Save**

**Goal:** Merge Manual Editing + Label Cleanup into one editing module and delete the redundant Save button.

**Requirements:** R6, R7

**Dependencies:** None

**Files:**
- Modify: `src/percell4/gui/segmentation_panel.py` (collapse the two `QGroupBox` sections into one, e.g. "Edit Labels"; delete the Save `QGroupBox` and `_on_save_labels`; add an "edits auto-saved" muted label)
- Test: `tests/test_gui/test_segmentation_panel_autosave.py` (assert persistence holds with no Save control)

**Approach:**
- Re-parent the Manual Editing buttons and the Label Cleanup controls into one group's layout (e.g. "Edit Labels"); delete the now-empty wrapper. Order them workflow-sequential: Create Empty Labels → Add New Label → Delete Selected Label → Clean Up Labels (relabel sequential), then the cleanup parameters Edge margin → Min cell area → Preview Removal → Apply Removal.
- Remove `btn_save` + `_on_save_labels`. Add a `TEXT_MUTED` label "Edits auto-saved to the dataset." at the **bottom** of the merged group, after the last control row and before `addStretch()`.
- Do **not** deepen the existing `launcher=self` coupling — only touch the section layout and Save removal.

**Patterns to follow:** `io_panel.py` two-group → re-parenting; muted-label style `f"color: {theme.TEXT_MUTED};"`.

**Test scenarios:**
- Covers AE2. Integration: a button-driven edit (e.g., Delete Selected Label) persists to the store via auto-save with no Save click; reload shows the edit.
- Integration: a paint/erase stroke persists after the debounce flush (existing autosave behavior unchanged).
- Regression: the panel exposes no Save control; `_on_save_labels` is gone; the resource list is already populated at creation (not by Save).

**Verification:** One combined editing module; no Save button; edits demonstrably persist; reassurance label present.

---

- U5. **Remove the Iterative Otsu GUI panel**

**Goal:** Delete the Iterative Otsu module from the Analysis tab and its GUI-only files, keeping the workflow/domain layers.

**Requirements:** R8

**Dependencies:** None

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (remove the `IterativeOtsuPanel` import, instantiation, and its `QGroupBox`)
- Delete: `src/percell4/gui/iterative_otsu_panel.py`, `src/percell4/gui/_iterative_otsu_settings.py`
- Delete: `tests/test_gui/test_iterative_otsu_panel.py`, `tests/test_gui/test_iterative_otsu_settings_widget.py`
- Modify: `tests/test_gui/test_analysis_panel_adaptive_module.py` (remove/invert the `panel._iterative_otsu_panel is not None` assertion)

**Approach:**
- GUI-only removal. Do **not** touch `domain/measure/iterative_otsu*.py`, `workflows/models.py`, `workflows/phases.py`, `workflows/artifacts.py` — they back the batch-workflow method and its serialization.
- Confirm `_iterative_otsu_settings.py` has no importer other than the panel (verified) before deleting.

**Patterns to follow:** atomic block removal in `analysis_panel.py` (construct → wrap → addWidget); reverse of `docs/solutions/architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md` (GUI layer only).

**Test scenarios:**
- Test expectation: regression-only. Analysis panel builds without the Iterative Otsu group; no import error from the deleted modules.
- Regression: the batch single-cell workflow that uses `iterative_otsu` round still constructs and serializes (existing `tests/test_workflows/*` remain green — do not modify).

**Verification:** No Iterative Otsu UI; GUI files/tests deleted; workflow + domain tests unaffected.

---

- U6. **Strip Adaptive Local Clipping to auto-extract (two-pass)**

**Goal:** Reduce the Adaptive Local Clipping panel to the auto-extract two-pass mode with only its live fields; remove retired modes, fields, and GUI-only workers.

**Requirements:** R10, R11, R12

**Dependencies:** None

**Files:**
- Modify: `src/percell4/gui/_adaptive_clip_settings.py` (remove widgets/config fields: `window_method` dropdown + the "Auto adaptive window size" checkbox, `window_value`/`window_unit`, `k`, `noise_estimator`, `particle_mode`/`d_min_um`/`particle_percentile`, `multiscale_mode`/`size_cutoff_px`/`ms_auto_start`/`ms_iterations`. **Also remove the separate "Detected Ø (µm)" readout widget `_d_min`, its `set_d_min_um` setter, and the `d_min_um` snapshot in `current_config()`** — it is written only by the removed particle mode; auto-extract back-fills the Smallest Ø field instead. Keep `auto_extract_smallest_auto`, `smallest_particle_value`/`unit`, `gaussian_sigma`, `min_size_value`/`unit`. Rewrite the gating/snapshot helpers — `current_config()`, `_active_mode()`/`_is_auto_extract_mode()`, `_apply_mode_gating()`, `_connect_change_signals()`, `set_enabled()` — to hardcode `auto_extract_mode=True` and drop references to the removed widgets; prune orphaned setters `set_window_value`/`set_d_min_um`)
- Modify: `src/percell4/gui/adaptive_clip_panel.py` (remove the mode dispatch + the GUI-only workers `run_adaptive_detection`, `run_adaptive_detection_stack`, `run_adaptive_detection_per_cell`, `run_adaptive_detection_by_particle_size`, `run_adaptive_detection_multiscale`; keep `run_adaptive_auto_extract`/`_stack`, `_run_auto_extract_mode`, and both CNR tools. **Rewrite or delete `_print_settings_debug`** — it is called by `_on_run` on every Run (≈line 560) and reads the removed `config.window_method`/`k`/`noise_estimator`/`particle_mode`/`d_min_um`/… fields, so leaving it raises AttributeError before the auto-extract dispatch)
- Test: `tests/test_gui/test_adaptive_clip_settings_widget.py`, `tests/test_gui/test_adaptive_clip_panel.py`, `tests/test_gui/test_adaptive_clip_timelapse.py`

**Approach:**
- The panel becomes single-mode (always auto-extract). The `Auto-detect smallest (LoG)` checkbox is the auto/manual toggle; `Smallest particle Ø` (+unit) enables when it is off, and when auto-detect is on it is **disabled and back-filled** with the LoG-measured diameter after a run (via `set_smallest_value`) — this is the detected-size feedback. Gaussian σ and Min particle size remain. There is **no** separate Detected Ø widget.
- Keep the `NoParticlesFound` → empty-plane degradation, exact-`T` emission, `SIZE_NUM_SIGMA = 30`, per-frame CNR pooling, and the four-step Creator sequence in `_run_auto_extract_mode`.
- Remove only GUI-level workers/settings; leave `domain/measure/auto_extraction` and any shared domain helpers intact.

**Patterns to follow:** `_run_auto_extract_mode` (surviving Creator path); convention `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md`; time-lapse contract doc.

**Test scenarios:**
- Covers AE4. Happy path: the settings widget exposes only Auto-detect-smallest, Smallest Ø (+unit), Gaussian σ, Min particle size — no separate Detected Ø widget, no dropdown, k, Window, percentile, cutoff, auto-start, iterations, or noise combo.
- Happy path: auto-detect ON → a 2D run produces a mask and back-fills the **Smallest Ø field** (via `set_smallest_value`); `run_adaptive_auto_extract` receives `(…, smallest_px=None, gaussian_sigma, min_spot_px)`.
- Happy path: auto-detect OFF with a manual Smallest Ø (px and µm) → `smallest_px` resolved correctly; µm with no pixel size → guarded status message, no crash.
- Edge case (time-lapse): a `(T,H,W)` channel auto-extracts per frame; a blob-less frame degrades to an empty plane (NoParticlesFound), output stays exactly `T` planes.
- Regression: the 5 retired worker functions are gone and unreferenced; CNR Classify/Segment tools still build and run.

**Verification:** Panel is single-mode auto-extract with only the kept fields; both 2D and time-lapse runs succeed; CNR tools intact.

---

- U7. **Reorder Analysis modules (detection-first)**

**Goal:** Reorder the remaining Analysis modules top-to-bottom: Adaptive Local Clipping → Particle Analysis → Measurements → Grouped Thresholding → Whole-Field Thresholding.

**Requirements:** R9

**Dependencies:** U2 (Cell Filter removed), U5 (Iterative Otsu removed)

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (`_build_ui` — move the per-module construct+`addWidget` blocks into the new order)
- Test: `tests/test_gui/test_analysis_widgets.py` (assert module order)

**Approach:**
- Pure mechanical reordering of atomic blocks (construct widget → wrap in `QGroupBox` → `addWidget`). Keep `section_label("Analysis")` first and `addStretch()` last. No signal coupling between modules, so order is presentation-only.
- **Sequencing:** this unit edits the same `_build_ui` region as U2 (Cell Filter removal) and U5 (Iterative Otsu removal). Land **U2 → U5 → U7** in order — doing them in parallel on `analysis_panel.py` invites conflicts, and the reorder assumes both removals are already done.

**Patterns to follow:** existing `_build_ui` block structure.

**Test scenarios:**
- Test expectation: layout-only. Assert the `QGroupBox` titles appear in the order Adaptive Local Clipping, Particle Analysis, Measurements, Grouped Thresholding, Whole-Field Thresholding.

**Verification:** Analysis tab renders modules in the detection-first order; all modules still function.

---

- U8. **Merge Scripts + Workflows into one tab**

**Goal:** Replace the separate Scripts and Workflows tabs with a single `Analyses & Workflows` tab containing two `QGroupBox` sections.

**Requirements:** R13

**Dependencies:** None

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py` (add `_create_scripts_workflows_panel`; in `categories`, replace the two tuples with one `("Analyses & Workflows", self._create_scripts_workflows_panel)`; remove `_create_scripts_panel`/`_create_workflows_panel` or fold their bodies into the new factory)
- Test: `tests/test_gui/test_scripts_panel.py` (update for the merged panel); keep `tests/test_gui/test_workflows_panel_*_wiring.py` green

**Approach:**
- New factory: `section_label("Analyses & Workflows")` + `QGroupBox("Analyses")` (registry-driven buttons from `list_analyses()`) + `QGroupBox("Workflows")` (the five `_btn_*` workflow buttons) + `addStretch()`.
- The single `enumerate` loop renumbers sidebar/stack indices automatically; only `_on_sidebar_click(0)` is index-pinned and unaffected. Preserve the `self._btn_*` attributes and their `_on_open_*_workflow` connections so existing wiring tests pass.

**Patterns to follow:** `io_panel.py` (section_label + two QGroupBox); existing `_create_scripts_panel` / `_create_workflows_panel` bodies.

**Test scenarios:**
- Happy path: the merged tab exposes both an Analyses section (one button per registered analysis) and a Workflows section (all five workflow buttons).
- Integration: clicking each workflow button still invokes its `_on_open_*_workflow` handler (existing wiring tests pass unchanged).
- Regression: exactly 7 sidebar tabs exist; no standalone Scripts or Workflows tab remains; tab/stack indices stay 1:1.

**Verification:** One `Analyses & Workflows` tab with two sections (`Analyses` + `Workflows`); every prior script and workflow remains launchable; sidebar count is 7.

---

## System-Wide Impact

- **Interaction graph:** `LauncherWindow` owns the `categories` list and the `_create_*_panel` factories; `ViewerPanel` (new) subscribes to `CellDataModel.state_changed`. The relocated Cell Filter is the sole writer of **`filter_ids`** (consumed by the viewer's `DirectLabelColormap` repaint, the data plot, and the cell table); `selection` remains multi-writer by design.
- **Error propagation:** unchanged — handlers keep their existing status-message guards (e.g., empty selection, µm-without-pixel-size).
- **State lifecycle risks:** the Selector move must not create a second writer (divergent truth) or orphan the subscriber-rebind (stale enabled-state/count). Hide Viewer must not tear down subscriptions.
- **API surface parity:** none external — launcher-internal UI only.
- **Integration coverage:** Cell-Filter-from-Viewer round-trip, Hide→Show viewer persistence, auto-save-without-Save, and time-lapse auto-extract are the cross-layer scenarios unit-level mocks won't fully prove.
- **Unchanged invariants:** the five session selection fields and their single-writer rule; the `@register_analysis` registry and hard-coded workflow handlers (merged visually only); domain/workflow Iterative-Otsu and auto-extraction behavior; FLIM/Data tabs; viewer internals.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Cell Filter duplicated instead of moved (two `filter_ids` writers / two Cell Filter widgets) | Remove from Analysis in the same unit; post-move single-`filter_ids`-writer grep; audit-artifact update (U2). |
| Relocated Selector loses its subscriber-rebind (stale count/button) | Port `_on_filter_state_changed` + `state_changed` subscription into `ViewerPanel`; integration test on a real `set_filter` (U2). |
| Hide Viewer makes the viewer deaf to session after re-show | Pure `hide()` with no teardown; round-trip + filter-while-hidden test (U1). |
| Deleting Iterative Otsu breaks batch-workflow serialization | GUI-only removal; domain/workflow layers untouched; workflow tests stay green (U5). |
| Removing ALC fields/workers drops a still-used path | Reference-checked GUI-only; keep `run_adaptive_auto_extract`/`_stack`, CNR tools, and the time-lapse contract (U6). |
| QMenu popups render unthemed / arrow dark-on-dark | Verify against launcher-local `QMenu` rules; replicate locally + temp-SVG arrow if needed (U3). |
| Save removal perceived as data-loss risk | Auto-save proven to cover all paths; add "edits auto-saved" label; persistence test (U4). |
| `analysis_panel.py` edited by three units (U2 Cell-Filter removal, U5 Iterative-Otsu removal, U7 reorder) | Land U2 → U5 → U7 sequentially, not in parallel; U7's reorder assumes both removals are done. |
| `_print_settings_debug` reads removed config fields → AttributeError on first Run | U6 rewrites/deletes the debug printer in the same edit that removes the fields. |

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-06-30-launcher-window-cosmetic-refactor-requirements.md`
- Key code: `src/percell4/interfaces/gui/main_window.py`, `.../task_panels/io_panel.py`, `.../task_panels/analysis_panel.py`, `src/percell4/gui/segmentation_panel.py`, `src/percell4/gui/adaptive_clip_panel.py`, `src/percell4/gui/_adaptive_clip_settings.py`, `src/percell4/gui/theme.py`, `src/percell4/interfaces/gui/peer_views/cell_table.py`
- Learnings: `gui-action-contract-exhaustiveness.md`, `consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`, `session-to-napari-one-way-push.md`, `creator-contract-four-step-sequence-2026-05-18.md`, `extending-per-cell-detection-to-time-lapse-2026-06-25.md`, `adaptive-clip-window-and-k-rules-2026-06-23.md`, `phasor-plot-deaf-to-session-after-close-reopen-2026-06-18.md`, `decouple-task-panels-callback-injection.md`
- Audit artifacts to update: `docs/audits/gui-element-classification.yaml`, `docs/audits/session-mutation-graph.md`, `docs/audits/subscriber-rebind-matrix.md`
