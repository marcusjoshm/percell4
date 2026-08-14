---
title: Always-Visible Session Selection Window
type: feat
status: completed
date: 2026-05-13
origin: docs/brainstorms/2026-05-13-session-selection-window-requirements.md
---

# Always-Visible Session Selection Window

## Overview

Introduce a dedicated wide, always-on-top top-level window — the **Session window** — that owns the canonical Selectors for `session.active_channel`, `session.active_segmentation`, and `session.active_mask`. Retire the per-panel channel-override combos shipped in PR #11 and earlier, and remove the corresponding Selector combos from the Data tab. Every module reads `session.active_*` directly. The Phasor Window's mask-naming bug (mask name derived from a stale `active_channel`) auto-resolves as a free consequence.

The result: one canonical place to pick what you're working on, always reachable without tab navigation, and no divergent state between surfaces.

---

## Problem Frame

(see origin: `docs/brainstorms/2026-05-13-session-selection-window-requirements.md`)

The current per-panel channel-override pattern produces three failure modes the user has experienced firsthand: divergent truth (panel says mNG, Data tab says CA-SiR), cross-feature leakage (Phasor Window names a mask after CA-SiR even though it was computed from mNG), and selection friction for `active_mask` / `active_segmentation` (only changeable via Data tab). This plan retires that pattern. The GUI state-handling audit's OQ-3 ("how do per-module Selectors synchronize with the Data tab?") is resolved by eliminating per-module Selectors entirely.

---

## Requirements Trace

- R1. Dedicated Session window owns the three canonical Selectors.
- R2. Window is wide and short, designed to pin at the screen's top edge.
- R3. Default always-on-top with a user-toggleable "Pin on top" control; setting persists across launches.
- R4. Combos populate from Session (`channel_names` metadata, segmentation/mask lists) and subscribe to Session events.
- R5. Combo changes write to Session via `set_active_channel | set_active_segmentation | set_active_mask`.
- R6. Window geometry persists across launches.
- R7. Per-panel `_channel_combo` widgets removed from `segmentation_panel.py`, `grouped_seg_panel.py`, `flim_panel.py`; each panel reads `session.active_channel` at Run time.
- R8. No module introduces its own active-selection override for channel, mask, or segmentation.
- R9. Phasor Window mask-naming default continues to derive from `session.active_channel`.
- R10. Data tab "active" Selector combos removed.
- R11. Data tab retains management widgets (rename/delete/list) and metadata display.
- R12. `Session.set_dataset` lifecycle (auto-select first channel/seg/mask) unchanged.
- R13. Creator auto-select behavior unchanged.

---

## Scope Boundaries

- No transient "use this just for this run" override (considered and rejected in origin).
- No per-module independent active state.
- `filter_ids` and `selection` are not exposed in the Session window.
- napari layer-list clicks remain forbidden as Session writers (unchanged from CLAUDE.md rule).
- Audit OQ-1 (napari active layer as session state), OQ-2 (Creator StateChange-flag conventions), OQ-4 (napari native keybinding suppression) are not addressed here.
- No multi-channel selection.
- Data tab management widgets are not redesigned.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — canonical pattern for a `QMainWindow`-based top-level peer view that subscribes to Session events. SessionWindow mirrors its shape but is dramatically smaller.
- `src/percell4/interfaces/gui/main_window.py:571-586` — window creation and registration in `self._windows: dict[str, QWidget]`. The Session window registers as a new key (e.g., `"session"`).
- `src/percell4/interfaces/gui/main_window.py:619-624` — the existing `_on_layer_selection_changed` callback drives per-panel `update_channels` rebinds. After U3 there are no panel combos to rebind; this callback either collapses (no longer needed) or repurposes if the Session window subscribes directly to `Event.DATASET_CHANGED` (recommended).
- `src/percell4/interfaces/gui/task_panels/data_panel.py:72-97, 243-253` — current Data-tab Selector wiring shape (`currentTextChanged` → `_on_active_*_combo_changed` → `data_model.set_active_*`). The Session window's Selectors mirror this pattern in a new module.
- `src/percell4/application/session.py` — Session API: `set_active_channel | set_active_segmentation | set_active_mask` (no-op short-circuit on equal); events `ACTIVE_CHANNEL_CHANGED`, `ACTIVE_SEGMENTATION_CHANGED`, `ACTIVE_MASK_CHANGED`, `DATASET_CHANGED`; `subscribe(event, cb)` returning an unsubscribe function.
- `src/percell4/gui/viewer.py:606-613` — canonical QSettings pattern: `QSettings("LeeLabPerCell4", "PerCell4")` with key namespace `"<window>/geometry"`. Reuse the same org/app strings; add `"session_window/geometry"` and `"session_window/pin_on_top"`.
- `src/percell4/interfaces/gui/peer_views/phasor_plot.py:1846-1850` — site of the mask-naming-from-`active_channel` derivation. No code change needed here; behavior corrects itself when `active_channel` reflects user intent (R9).

### Institutional Learnings

- `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md` — documents the "fourth site triggers extract" rule for the panel-channel-override pattern. **This plan supersedes that learning.** U5 marks it superseded with a back-reference to this plan and the origin requirements doc. Do not delete the file; preserve the historical context.
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md` — parent drift-class learning. Still applies; this plan is one resolution of that class for the channel-override case (the resolution is "don't have N sites at all").
- `docs/audits/gui-element-classification.yaml`, `docs/audits/session-mutation-graph.md` — Selector/Creator/Action taxonomy and the I1 invariant. The Session window's three combos are Selectors under I1; the removal of panel `_channel_combo` widgets eliminates ambiguity in the classification (they were correctly classified as Selectors today but were the source of OQ-3's open question).

### External References

- None. Standard Qt patterns (`QMainWindow`, `Qt.WindowStaysOnTopHint`, `QSettings`) are well-established and have local precedents already in the codebase.

---

## Key Technical Decisions

- **New module at `src/percell4/interfaces/gui/peer_views/session_window.py`.** The file lives alongside Phasor Window and Data Plot for parity with the existing "peer view" pattern. The peer_views directory is the right home for top-level windows that observe Session state.
- **Register under key `"session"` in `LauncherWindow._windows`.** Mirrors `"phasor_plot"`, `"viewer"`, etc. Opened during `LauncherWindow` construction (not on-demand) so it's there from the moment the app starts.
- **Pin-on-top via `Qt.WindowStaysOnTopHint`.** Toggle implemented by `setWindowFlag(Qt.WindowStaysOnTopHint, on=...)`. Note that flipping window flags on an already-shown Qt window requires `show()` to be called again — the toggle handler must do this.
- **Geometry + pin state persistence via `QSettings("LeeLabPerCell4", "PerCell4")`.** Keys: `"session_window/geometry"`, `"session_window/pin_on_top"` (default `True`). Mirrors `src/percell4/gui/viewer.py:606-613`.
- **The Session window subscribes directly to `Event.DATASET_CHANGED`, `ACTIVE_CHANNEL_CHANGED`, `ACTIVE_SEGMENTATION_CHANGED`, `ACTIVE_MASK_CHANGED`.** No need to route through `_on_layer_selection_changed`. This decouples the new window from the napari-layer-selection coupling that callback embodies.
- **Resource-list refresh.** Session metadata can grow (a Creator adds a mask, segmentations get renamed). The Session window subscribes to `Event.CHANNEL_LIST_CHANGED`, `Event.SEGMENTATION_LIST_CHANGED`, and `Event.MASK_LIST_CHANGED` (verified at `src/percell4/application/session.py:29-31`). These fire from `Session.refresh_resource_lists` and from `Session.set_dataset`. See `tests/test_gui_workflows/test_creator_live_combo_refresh.py` for the canonical subscription pattern.
- **Default window placement on first launch.** Geometry: width ≈ 720px, height ≈ 80px, top-edge centered horizontally on the primary screen. On subsequent launches, the QSettings-restored geometry wins.
- **Test posture.** Test-first for U1 (new module with clear contract). U2–U4 are integration changes — test alongside, not strictly test-first.

---

## Open Questions

### Resolved During Planning

- **Q (R6): Where to persist geometry?** A: `QSettings("LeeLabPerCell4", "PerCell4")` using key `"session_window/geometry"` and `"session_window/pin_on_top"`. Mirrors `src/percell4/gui/viewer.py:606-613` precedent.
- **Q (R10): Delete Data-tab Selectors outright or keep as read-only bridge?** A: Delete outright. Origin doc's "Data tab should still be for changing names and deleting layers and showing metadata" implies clean removal. A read-only bridge would duplicate state display the Session window already provides.
- **Q (R7): Create `src/percell4/gui/_channel_combo.py` helper?** A: Do not create. The helper's purpose was to dedupe a pattern this plan retires.
- **Q (panels): What happens to the per-panel `update_channels` methods?** A: Removed entirely. Panels no longer maintain their own channel combo. They read `session.active_channel` directly at Run time (the Run-time read site already exists at `flim_panel.py:_get_active_channel` and analogous sites in the seg panels).
- **Q (audit): What gets updated in audit artifacts?** A: U5 updates `gui-element-classification.yaml` (drop three Selector entries for the panel combos; drop three Data-tab Selector entries; add three Selector entries for the Session window combos) and `session-mutation-graph.md` writer table for `active_channel | active_segmentation | active_mask`.

### Deferred to Implementation

- [Affects U1][Needs verification on macOS] Does `Qt.WindowStaysOnTopHint` survive Mission Control / virtual desktop transitions cleanly for the Session window? Manual test required at the end of U2. If broken, the pin-on-top toggle remains useful as a partial workaround.
- [Affects U3] Are there other read sites in any panel that reach for a channel via `viewer.layers` or via a panel-local cache (rather than `session.active_channel`)? Audit during U3 — `grouped_seg_panel.py`, `segmentation_panel.py`, `flim_panel.py` are the known ones; verify no others exist in `analysis_panel.py` or `io_panel.py`.
- [Affects U4] Does `data_panel.py` have any non-Selector consumers of the active combos (e.g., status read-outs that show "currently selected: X")? If so, replace the read source with a `session.active_*` read at refresh time rather than removing the read-out.

---

## Implementation Units

- U1. **Create the Session window**

**Goal:** Implement `SessionWindow` as a wide, always-on-top top-level Qt window with three Selector combos (Channel, Mask, Segmentation), a "Pin on top" toggle, and a dataset-name header. Subscribes to Session events; writes to Session on combo changes; persists geometry and pin state via QSettings.

**Requirements:** R1, R2, R3, R4, R5, R6, R12.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/interfaces/gui/peer_views/session_window.py`
- Test: `tests/test_gui_workflows/test_session_window.py`

**Approach:**
- Subclass `QMainWindow`. Central widget is a `QHBoxLayout` row containing: dataset-name `QLabel` (left), three `QLabel + QComboBox` pairs ("Channel:", "Mask:", "Segmentation:"), and a `QCheckBox` labeled "Pin on top" (right).
- Constructor takes the shared `CellDataModel` (matching the pattern in `phasor_plot.py`). Stores `self._session = data_model.session`.
- Population: read `session.dataset.metadata.get("channel_names", [])` for channel; read mask and segmentation names from Session/dataset metadata (use the same source that `data_panel._refresh_seg_combos` and `_refresh_mask_combos` use — verify exact source during implementation).
- Subscribe to `Event.DATASET_CHANGED`, `Event.ACTIVE_CHANNEL_CHANGED`, `Event.ACTIVE_SEGMENTATION_CHANGED`, `Event.ACTIVE_MASK_CHANGED`, `Event.CHANNEL_LIST_CHANGED`, `Event.SEGMENTATION_LIST_CHANGED`, `Event.MASK_LIST_CHANGED`. Each handler refreshes the corresponding combo without firing `currentTextChanged` (use `blockSignals` or a `_loading` guard, mirroring the data_panel pattern).
- Combo `currentTextChanged` handler calls `session.set_active_channel | set_active_segmentation | set_active_mask` with the new name (or `None` for placeholder/empty). Session's no-op short-circuit prevents echo loops, but still guard with `_loading` for safety during programmatic population.
- Pin-on-top toggle: when checked, call `self.setWindowFlag(Qt.WindowStaysOnTopHint, True)` then `self.show()`; when unchecked, the opposite. Save state to QSettings on change. On window construction, restore from QSettings (default `True`).
- Geometry persistence: in `closeEvent`, save `self.saveGeometry()` to QSettings. In constructor, after layout setup, attempt `self.restoreGeometry(saved_bytes)`; if no saved geometry, default to width=720, height=80, top-edge-centered on `QApplication.primaryScreen().availableGeometry()`.
- Dataset-name header label is updated on `Event.DATASET_CHANGED` from `session.dataset.path.stem` (mirror how Phasor Window or Data tab displays dataset name; verify).

**Execution note:** Test-first. The contract is small and well-defined (three Selectors, one toggle, persistence). Write tests for combo population, set_active_* on change, no-echo on programmatic refresh, pin-on-top toggle effect, and geometry round-trip before implementing.

**Patterns to follow:**
- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — QMainWindow shape, Session subscription, unsubscribe in closeEvent.
- `src/percell4/interfaces/gui/task_panels/data_panel.py:72-97` — combo wiring shape (currentTextChanged → set_active_*).
- `src/percell4/gui/viewer.py:600-615` — QSettings org/app strings and geometry save/restore idiom.

**Test scenarios:**
- Happy path: Construct SessionWindow with a Session pointing at a dataset with channels `["mNG", "CA-SiR"]`. Channel combo contains both items in metadata order. Assert.
- Happy path: Session has `active_channel = "mNG"`. After construction, channel combo's current text is "mNG". Same for active_mask and active_segmentation.
- Happy path: User changes channel combo to "CA-SiR" → `session.active_channel == "CA-SiR"` and `ACTIVE_CHANNEL_CHANGED` was emitted exactly once.
- Edge case: Session has no dataset on construction. Combos are empty; dataset-name header shows a placeholder (e.g., "(no dataset)").
- Edge case: Session emits `DATASET_CHANGED` after a new dataset is loaded with channels `["A", "B"]` and `active_channel = "A"`. Combos repopulate; channel combo current text is "A". No spurious `set_active_*` calls fire during refresh.
- Edge case: Session emits `ACTIVE_CHANNEL_CHANGED` from an external caller (e.g., a test directly calls `session.set_active_channel("CA-SiR")`). Channel combo current text updates to "CA-SiR". `set_active_channel` is NOT re-called by the combo's currentTextChanged handler (no echo loop).
- Happy path: Pin-on-top toggle starts `True` (default). After unchecking, `self.windowFlags() & Qt.WindowStaysOnTopHint == 0`. After re-checking, the flag is set again. QSettings value updates on each toggle.
- Happy path: Save geometry on `closeEvent`. Re-construct a new SessionWindow with a fresh QSettings instance pointing at the same store; `self.geometry()` matches the saved geometry.
- Integration: Construct with a Session, add a new mask to `session.dataset.metadata["mask_names"]`, fire the appropriate list-change event. Mask combo gains the new item without losing the current selection.

**Verification:**
- Test file passes.
- Module imports cleanly (`from percell4.interfaces.gui.peer_views.session_window import SessionWindow`).
- No reads of `viewer.layers` from anywhere in the new module.

---

- U2. **Wire SessionWindow into LauncherWindow lifecycle**

**Goal:** Open the Session window during Launcher construction, register it under key `"session"` in `self._windows`, ensure it shows on app launch, and route Session-list-change events to refresh its combos (or rely on its own subscriptions — pick one; default is the window subscribes directly).

**Requirements:** R1, R2 (visible on launch), R6 (geometry restored at launch).

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Modify: `src/percell4/interfaces/gui/peer_views/__init__.py` (export if needed)
- Test: `tests/test_gui_workflows/test_launcher_opens_session_window.py`

**Approach:**
- In `LauncherWindow.__init__` (after `self._windows = {}` is created and Session is wired), construct `SessionWindow(data_model=self.data_model)` and store under `self._windows["session"]`. Call `.show()`.
- Existing `_on_layer_selection_changed` callback at `main_window.py:619-624`: this previously drove per-panel `update_channels` calls. After U3 the panel combos no longer exist; this callback collapses. Decide between (a) deleting the callback entirely and (b) keeping a minimal stub. Recommendation: delete the callback and the napari-layer-selection subscription. The Session window subscribes to Session events directly (cleaner; matches I1's spirit).
- Confirm Session window appears in the cleanup loop at `main_window.py:1438-1450` and `:1511`. The window must be closed (`close()` then `deleteLater()`) when the Launcher shuts down so geometry persists.
- On Launcher startup with no dataset yet: SessionWindow is visible with empty combos and "(no dataset)" header. When the user loads a dataset, `Event.DATASET_CHANGED` populates combos via U1's subscription.

**Patterns to follow:**
- `src/percell4/interfaces/gui/main_window.py:571-586` — window construction and registration shape (e.g., how PhasorPlotWindow is created in `_open_window`).
- Look for an existing "always open on launch" precedent. If none exists (PhasorPlotWindow appears to be on-demand), the Session window is the first launch-time peer view. Document this in the implementation note.

**Test scenarios:**
- Happy path: Construct `LauncherWindow` (qtbot fixture) → `launcher._windows["session"]` exists and is a `SessionWindow` instance.
- Happy path: `launcher._windows["session"].isVisible()` is `True` immediately after launcher construction.
- Happy path: Close the launcher → SessionWindow's `closeEvent` runs and writes geometry to QSettings.
- Edge case: Construct two LauncherWindows in sequence in the same test process (rare, but happens with qtbot). The second SessionWindow's geometry matches the first's saved value.
- Integration: Launcher loads a dataset via the existing Load Dataset flow → SessionWindow's combos populate, active selections match what `Session.set_dataset` chose by default.

**Verification:**
- App launches and the Session window appears at the top of the screen.
- Loading a dataset populates the combos.
- Closing the app and re-opening restores the previous geometry and pin state.

---

- U3. **Remove per-panel channel-override combos**

**Goal:** Delete `_channel_combo` widgets and the associated `update_channels` methods from `segmentation_panel.py`, `grouped_seg_panel.py`, and `flim_panel.py`. Each panel reads `session.active_channel` directly at Run time. Update the existing tests that depend on `_channel_combo`.

**Requirements:** R7, R8.

**Dependencies:** U1, U2 (the new Session window must exist so users have a way to switch channels before we strip the panel-level affordance).

**Files:**
- Modify: `src/percell4/gui/segmentation_panel.py`
- Modify: `src/percell4/gui/grouped_seg_panel.py`
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
- Modify: `src/percell4/interfaces/gui/main_window.py` (remove the `_on_layer_selection_changed` → panel `update_channels` dispatch lines if not already removed in U2)
- Modify: `tests/test_gui/test_segmentation_panel_cellpose_name_prompt.py` (remove the `_channel_combo.addItem(channel); setCurrentText(channel)` seeding in the test helper; switch to `model.session.set_active_channel(channel)` only)
- Audit for other affected tests: `tests/test_gui/test_grouped_seg_panel*.py`, any FLIM panel test that touches `_channel_combo`. Modify in place.

**Approach:**
- `segmentation_panel.py`: Remove `self._channel_combo` instantiation, the `chan_row` layout addition, the `update_channels` method, and the subscription that drives it. `_on_run_cellpose` already has a Run-time read path; switch from `self._channel_combo.currentText() or None` to `self.data_model.session.active_channel`. The "no active channel → abort with status message" guard stays.
- `grouped_seg_panel.py`: Same shape. The Run handler is `_on_run` (per the snippet seen earlier). Switch the channel source.
- `flim_panel.py`: Remove `self._channel_combo`, the `chan_row`, `update_channels`, and the `Event.DATASET_CHANGED` subscription. The existing `_get_active_channel` method body becomes `return self.data_model.session.active_channel` (drop the combo path).
- `main_window.py`: Drop the lines in `_on_layer_selection_changed` that call `update_channels()` on the three panels. If U2 already deleted the callback, this is a no-op here.
- Test fixture in `test_segmentation_panel_cellpose_name_prompt.py:81-122`: the comment block citing the channel-override PR (`The combo replaced the read-only label in the channel-override PR...`) becomes stale. Rewrite to seed `model.session.set_active_channel(channel)` and drop the `panel._channel_combo.addItem(...)` lines.

**Patterns to follow:**
- Existing direct read sites for `session.active_channel` in `phasor_plot.py:1849` (mask-naming) and analysis panel (if it reads channel — verify). Pattern: `channel = self._session.active_channel` then guard for `None`.

**Test scenarios:**
- Happy path (Cellpose): Set `session.active_channel = "mNG"` via the Session window (or directly via Session API in tests). Invoke `_on_run_cellpose`. The Worker is called with `channel="mNG"` (or whatever the existing param name is). No reads of any `_channel_combo` occur — the attribute should not exist.
- Happy path (Grouped Seg): Same shape — set Session, invoke Run, verify the use case is called with the Session's active channel.
- Happy path (FLIM): Same shape — `_get_active_channel()` returns `session.active_channel` without consulting a combo.
- Error path: `session.active_channel is None` → existing "no active channel" error/status path fires. The behavior is unchanged from today; only the read source moved.
- Regression: Existing PR #10 name-prompt test (`test_run_cellpose_threads_chosen_name_into_pending_attr`) still passes after the fixture rewrite — verifying the name-prompt feature didn't break.
- Negative: Searching the codebase for `_channel_combo` returns zero matches under `src/percell4/`.

**Verification:**
- Three panels have no `_channel_combo`, no `update_channels`, no `Event.DATASET_CHANGED` subscription tied to the combo.
- All three Run handlers read `session.active_channel`.
- All existing panel tests pass after the test-fixture update.

---

- U4. **Remove Data-tab "active" Selector combos**

**Goal:** Delete `_active_channel_combo`, `_active_seg_combo`, `_active_mask_combo` (and their slot wiring) from `data_panel.py`. Keep all management widgets (rename, delete, list views) and metadata display intact.

**Requirements:** R10, R11.

**Dependencies:** U1, U2 (Session window must be the canonical Selector before Data-tab Selectors are removed).

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/data_panel.py`
- Modify (if affected): any test under `tests/` that exercises `_active_channel_combo` / `_active_seg_combo` / `_active_mask_combo` directly. Grep first.

**Approach:**
- Identify and remove the three combo widgets, their layout rows (channel/seg/mask "active" rows at `:72-97`), their slots (`_on_active_channel_combo_changed`, `_on_active_seg_combo_changed`, `_on_active_mask_combo_changed` at `:243-253`), and any internal subscriptions / refresh helpers that exist solely to feed the three combos (e.g., portions of `_populate_channel_combo` that target the active combo only — if `_populate_channel_combo` also feeds management combos, keep that path).
- Preserve `_mgmt_chan_combo`, `_mgmt_seg_combo`, `_mgmt_mask_combo` and all rename/delete affordances.
- Preserve metadata display and `_info_label`.
- Audit the `_populate_channel_combo` / `_refresh_seg_combos` / `_refresh_mask_combos` methods: they may currently populate BOTH the active and management combos. Split if needed so the management-combo path remains.
- Verify the `state_changed` subscription path: if the Data panel listens for `active_channel` / `active_segmentation` / `active_mask` flags to update something other than the now-removed combos, keep those subscriptions. If the subscription's sole purpose is the active combo, remove.

**Patterns to follow:**
- The management widgets remain unchanged; the goal is surgical removal of the Selector rows only.

**Test scenarios:**
- Happy path: Construct `DataPanel` (qtbot fixture) with a Session pointing at a dataset → no `_active_channel_combo` attribute exists; `_mgmt_chan_combo` does exist and lists channels.
- Happy path: Renaming a channel via the management combo still works end-to-end (delegates to `session.set_active_channel(new_name)` at `:485` for the rename-current case — keep this; it's a legitimate Selector-style write triggered by a Creator/Selector flow per audit semantics).
- Happy path: Deleting a segmentation via the management combo still updates Session correctly.
- Regression: All existing tests that touched the three "active" combos either pass (because they were removed) or fail with a clear "attribute removed" message that points to the new Session window.
- Negative: Grepping for `_active_channel_combo`, `_active_seg_combo`, `_active_mask_combo` in `src/` returns zero matches.

**Verification:**
- Data tab no longer has the three top Selector rows.
- Management widgets still work (rename, delete, list).
- Metadata display still works.
- All existing tests pass after expected-failure-fixture updates.

---

- U5. **Update audit artifacts and supersede the panel-channel-override learning**

**Goal:** Reflect the new model in `docs/audits/gui-element-classification.yaml`, `docs/audits/session-mutation-graph.md`, and the per-module GUI CLAUDE.md files. Mark `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md` superseded with a back-reference to this plan and the origin requirements doc.

**Requirements:** Success criteria — downstream agent handoff.

**Dependencies:** U3, U4 (artifacts must reflect actual code state).

**Files:**
- Modify: `docs/audits/gui-element-classification.yaml` — drop entries for the three panel `_channel_combo` Selectors and the three Data-tab Selectors; add three Selector entries for the Session window combos.
- Modify: `docs/audits/session-mutation-graph.md` — update the `active_channel`, `active_segmentation`, `active_mask` writer tables: remove panel-combo writer rows (if any), remove Data-tab combo writer rows, add Session-window combo writer rows. Update counts in the summary table.
- Modify: `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md` — add a `superseded_by:` frontmatter field pointing to this plan and the origin requirements doc; add a "Superseded" callout at the top of the document with a one-line explanation. Do not delete content; preserve as historical context.
- Modify (if present): `src/percell4/gui/CLAUDE.md` and any per-module CLAUDE.md that documents the channel-override pattern. Update to reflect the new "Session window is canonical" model. Audit by grepping for "channel.*override" and "update_channels" across `src/percell4/**/CLAUDE.md`.
- Modify (if affected): `CLAUDE.md` — the "GUI state ownership" section at lines 44-58 currently describes Selectors/Creators/Actions and the "Five session selection fields" rule. Verify nothing here needs updating; the rules themselves are unchanged. Just confirm no language references the panel-override pattern as canonical.

**Approach:**
- The audit YAML is the source of truth for I1 compliance. After this PR, no Action writes any of the three "active" fields, no per-module Selector exists for them, and the Session window is the single non-Data-tab Selector site. Update the counts at the top of the YAML accordingly.
- The mutation graph's summary table at `session-mutation-graph.md` will see three new entries (Session window combos) and lose six (three panel combos plus three Data-tab combos). Verify the I1 violation counts remain zero or otherwise unchanged from the pre-PR state.
- For the superseded learning, the front-matter pattern looks like: add `superseded_by: docs/plans/2026-05-13-001-feat-session-selection-window-plan.md` (and `docs/brainstorms/2026-05-13-session-selection-window-requirements.md`). The "Superseded" callout at the top is a short paragraph: "This pattern was retired on 2026-05-XX in favor of a single canonical Session window. See ..."

**Test scenarios:**
- Test expectation: none — this is a documentation/artifact update. Verification is by reading the updated files and grepping for stale references.
- Verify: `grep -r "_channel_combo" docs/audits/` returns no live references (only historical narrative).
- Verify: `grep -r "panel-channel-override-pattern" docs/` finds the learning file marked superseded and any back-references in other docs.

**Verification:**
- Audit YAML counts add up correctly.
- Mutation graph's I1 violations count is the pre-PR value (or zero, whichever).
- Superseded learning is discoverable but clearly marked.

---

## System-Wide Impact

- **Interaction graph:** The `_on_layer_selection_changed` callback at `main_window.py:619-624` is removed (U2/U3). The Session window subscribes directly to Session events instead of being driven by the launcher. No new callback chains are introduced.
- **Error propagation:** Each panel's "no active channel" guard moves from reading a panel combo to reading Session. Error behavior unchanged.
- **State lifecycle risks:** Removing the Data-tab Selectors requires careful audit that no helper-method depends on them (`_populate_channel_combo` may serve both active and management combos today — U4 must split if so). Test coverage on existing rename/delete flows protects against regression here.
- **API surface parity:** Three panels lose their `_channel_combo` and `update_channels` API surface. Any external test that called `panel.update_channels()` directly must be removed. Internal-only attribute; no public API impact.
- **Integration coverage:** End-to-end flow: load dataset → Session window shows defaults → user clicks Phasor → mask name uses correct channel. The Phasor Window mask-naming test in `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py` (if it asserts on the channel-in-name) should already pass; verify.
- **Unchanged invariants:** `CellDataModel.state_changed` shape unchanged. Session API unchanged. Audit I1 invariant unchanged (the Session window's combos comply; the panel combos that *also* complied are simply removed). The "napari → session forbidden" rule for layer-list selection events is unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| macOS Mission Control or virtual-desktop transitions may cause the always-on-top window to disappear or behave unexpectedly. | Pin-on-top toggle is the user-facing escape hatch. Manual test at end of U2. If broken, document and accept; the window remains usable when toggled off. |
| Removing the Data-tab Selectors may surprise users who reflexively go to the Data tab to change selection. | The Session window is visually obvious (wide, top-of-screen, always visible). Origin requirements explicitly accept this trade-off. No staged rollout needed for a single-user research tool. |
| `_populate_channel_combo` and the `_refresh_*_combos` methods may serve both active and management combos, and U4 splits the wrong way. | U4's first task is to read these methods carefully and split only what's needed. Tests for the management-combo path stay green. |
| Existing tests assume `_channel_combo` exists on the three panels. | U3 explicitly modifies `test_segmentation_panel_cellpose_name_prompt.py` and audits siblings. Run the test suite at the end of U3 to catch any missed sites. |
| Combo refresh on programmatic events may re-fire `currentTextChanged` and create echo loops. | Use a `_loading` guard or `blockSignals` during refresh; Session's no-op short-circuit in `set_active_*` is a secondary defense. The data_panel pattern already handles this safely — mirror it. |

---

## Documentation / Operational Notes

- After this lands, the recent learning at `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md` is marked superseded by U5. A new learning may be worth capturing post-merge via `/ce-compound`: "Session-state UI consolidation: when N modules grow their own override for the same canonical state, the right move is often to consolidate at the canonical surface, not to refactor the N copies."
- Audit OQ-3 closes with this PR. The origin requirements doc notes this; U5 reflects it in the audit artifacts.
- No user-facing documentation / changelog target exists in the repo today; if one is added later, this change warrants a one-line entry.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-13-session-selection-window-requirements.md](../brainstorms/2026-05-13-session-selection-window-requirements.md)
- **Audit:** [docs/brainstorms/2026-05-01-gui-state-handling-audit-requirements.md](../brainstorms/2026-05-01-gui-state-handling-audit-requirements.md) (resolves OQ-3)
- **Superseded learning:** [docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md](../solutions/conventions/panel-channel-override-pattern-2026-05-13.md)
- **Prior brainstorm (decision being revisited):** [docs/brainstorms/2026-04-17-channel-selection-session-brainstorm.md](../brainstorms/2026-04-17-channel-selection-session-brainstorm.md)
- **Code references:** `src/percell4/interfaces/gui/peer_views/phasor_plot.py`, `src/percell4/interfaces/gui/main_window.py`, `src/percell4/interfaces/gui/task_panels/data_panel.py`, `src/percell4/application/session.py`, `src/percell4/gui/viewer.py` (QSettings precedent).
