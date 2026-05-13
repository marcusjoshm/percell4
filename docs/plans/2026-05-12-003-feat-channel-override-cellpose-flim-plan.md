---
title: "feat: Channel-override combo in Cellpose and FLIM panels"
type: feat
status: completed
date: 2026-05-12
---

# feat: Channel-override combo in Cellpose and FLIM panels

## Overview

Mirror the Grouped Thresholding panel's per-module **channel override**
combo in two more places: the **Cellpose** section of the Segment tab,
and the **FLIM** panel. Users can pick a different channel for the
module's run without changing the session's active channel.

The Grouped panel already has this UI. The two new sites currently
read `self.data_model.session.active_channel` directly — that becomes
"read the panel's combo, default-seeded from the session active
channel" everywhere these panels make a run decision.

---

## Problem Frame

The session's `active_channel` is global — it follows the user's
selection in napari. Today, both the Cellpose run and the FLIM compute
flows always use that global channel:

- `src/percell4/gui/segmentation_panel.py:391` (`_on_run_cellpose`) reads
  `self.data_model.session.active_channel` directly. A read-only
  `QLabel` at `:108-112` displays it without affording change.
- `src/percell4/interfaces/gui/task_panels/flim_panel.py:243-244`
  returns `self.data_model.session.active_channel` from
  `_get_active_channel()`. Phasor compute and wavelet filter (lines 267
  and 373) both go through this method; FLIM has no channel UI at all.

The Grouped Thresholding panel already solved the same problem:
`src/percell4/gui/grouped_seg_panel.py:67-72` renders a "Channel:" +
`QComboBox` row, refreshed from session dataset metadata via
`update_channels()` (lines 168-189), and reads
`self._channel_combo.currentText()` at run time (line 208). The launcher
wires `update_channels()` to napari layer-selection events and dataset
load (`interfaces/gui/main_window.py:624, 933`).

This plan extends that exact pattern to the other two write paths.

---

## Requirements Trace

- **R1.** The Cellpose section in the Segment tab gets a "Channel:" +
  `QComboBox` row in place of the read-only `_channel_label`. The combo
  defaults to the session's active channel and re-seeds when napari's
  active layer changes or a new dataset loads.
- **R2.** The FLIM panel gets the same row at the top of its UI.
  Today the FLIM panel has no channel UI; this adds one.
- **R3.** Both panels read from `self._channel_combo.currentText()`
  when starting a run — not from `session.active_channel` directly.
- **R4.** Picking a different value in the combo is a **local
  override**: it does not write back to `session.active_channel` (the
  Grouped pattern's behavior). A subsequent napari layer-selection
  change or dataset reload re-seeds the combo from the new active
  channel, clobbering an unused override — same UX trade-off the
  Grouped panel already accepts.
- **R5.** Each panel's empty-combo / missing-channel state surfaces the
  same status message it does today ("Select a channel …"), avoiding
  silent failure when no channel is selectable.
- **R6.** Behavior matches the Grouped panel byte-for-byte where it
  applies: same widget order, same "Channel:" label, same fallback to
  viewer layers when session metadata is absent.

---

## Scope Boundaries

- Do not change the napari → session one-way push or any session
  selection-write rules — only the per-panel local read path changes.
- Do not extract a shared `update_channels_combo(...)` helper in this
  PR. The two new sites are simple enough to copy from
  `grouped_seg_panel.update_channels`, and the three implementations
  diverge slightly on fallback behavior. Drift-class follow-up only if
  a fourth site appears (see Documentation / Operational Notes).
- Do not migrate every `session.active_channel` read in
  `segmentation_panel.py` — only the Cellpose run path (R1's surface).
  The Manual Drawing's image-shape lookup at `_get_image_shape` (and
  the related label-refresh path) keeps reading the session value; the
  override applies only to the Cellpose run.
- Do not refactor the FLIM panel's existing subscription model
  (`Event.DATASET_CHANGED` via `Session.subscribe`) — extend it.
- Do not add a "lock combo to session" toggle. The Grouped pattern's
  fixed default-and-re-seed behavior is the contract being extended.

---

## Context & Research

### Relevant Code and Patterns

- **Reference (canonical implementation)** —
  `src/percell4/gui/grouped_seg_panel.py:67-72` (combo construction in
  `_build_ui`) and `:168-189` (`update_channels`).
- **Cellpose entry to migrate** —
  `src/percell4/gui/segmentation_panel.py:108-112` (read-only label
  today); `:73-77` (`_on_state_changed` calls
  `update_channel_label()`); `:379-382` (`update_channel_label()`
  body); `:391` (the line that reads `session.active_channel` in
  `_on_run_cellpose`).
- **FLIM entry to migrate** —
  `src/percell4/interfaces/gui/task_panels/flim_panel.py:243-244`
  (`_get_active_channel`); call sites at `:267, 373`. The panel
  already subscribes to `Event.DATASET_CHANGED` at `:62-64`.
- **Launcher wire points** —
  `src/percell4/interfaces/gui/main_window.py:619-624` (napari layer
  selection event handler — current code already calls
  `update_channel_label()` on the seg panel and `update_channels()` on
  the grouped panel); `:929-933` (dataset-load handler — already
  calls `update_channels()` on the grouped panel).

### Institutional Learnings

- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
  — this work doesn't add a write-emitting signal (the combos are
  *read-only consumers* of session state at this layer; the user's
  pick is only read at Run time), so no new signal wires are required.
  Mention this so a future reviewer doesn't apply the wiring rule
  reflexively to a pure read widget.
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  — confirms the combos are *Action*-class widgets (read session, do
  not write any selection field). Add audit rows in U3 (deferred — see
  Scope Boundaries).
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  — the lesson is to **copy faithfully** when not extracting. The two
  new sites must mirror `grouped_seg_panel.update_channels` exactly,
  not paraphrase it from intent.

---

## Key Technical Decisions

- **Combo defaults to `session.active_channel`, never writes back.**
  Local override only. Re-seed on napari layer-selection events and
  dataset-load. This matches the Grouped panel's existing contract.
- **Copy `update_channels` faithfully** from
  `grouped_seg_panel.py:168-189` into each new site. Do not paraphrase
  or simplify the session-then-viewer fallback chain.
- **No shared helper in this PR.** Three call sites is the threshold
  at which the drift cost (per PR #9's learning) starts to dominate.
  At three, the helper extraction is a Lightweight follow-up. With
  this PR landing two new copies, we hit the threshold; revisit when
  the next channel-combo site is proposed.
- **Cellpose `_on_state_changed` migration:** today it calls
  `update_channel_label()` on every channel change. After this PR it
  calls `update_channels()` instead. The old method is deleted.
- **FLIM uses `Session.subscribe(Event.DATASET_CHANGED, ...)` for
  re-seed,** following the panel's existing subscription pattern. The
  launcher's napari-layer-selection handler at
  `main_window.py:619-624` also gets a new line calling
  `self._flim_panel.update_channels()` so the combo follows napari
  selection (matching the Grouped panel's behavior).
- **FLIM combo placement:** at the top of the panel, immediately under
  the "FLIM" title label. Mirrors the Grouped panel's visual order.
  The reference-circle / wavelet / phasor sections remain below
  unchanged.
- **`_get_active_channel` in FLIM panel returns `currentText() or
  None`.** Same nullability contract as today (used at lines 267, 373
  to gate "Select a channel in the viewer first" status messages).

---

## Open Questions

### Resolved During Planning

- **Override scope — local or session-write?** Local. Matches the
  Grouped panel and avoids a cross-panel cascade where a user changing
  Cellpose's combo retargets every other window.
- **Combo seeding when no dataset is loaded?** Empty combo, no
  fallback. The existing Grouped panel returns early when
  `session.dataset is None` and the viewer is absent; copy that
  behavior.
- **Should the FLIM combo also re-seed on `Event.ACTIVE_CHANNEL_CHANGED`?**
  Not via `Session.subscribe`. The Grouped pattern handles
  active-channel changes via the launcher's
  napari-layer-selection callback (`main_window.py:619-624`). Extending
  that callback is the consistent place; adding a second subscription
  path on `Event.ACTIVE_CHANNEL_CHANGED` would diverge from the
  Grouped contract and re-introduce drift.
- **Manual Drawing's image-shape lookup at
  `segmentation_panel.py:_get_image_shape`** — out of scope. Manual
  drawing is a different code path on the same panel; this plan only
  changes the Cellpose section. The shared read-from-`session` call
  there is preserved.

### Deferred to Implementation

- **Exact placement of the combo within the Cellpose section** —
  immediately above or replacing the current "Active Channel:" label
  row. The implementer should pick whichever lays out cleanly in the
  existing `QHBoxLayout`. Visual outcome: a single "Channel: [combo]"
  row where the read-only label used to live.
- **Whether to delete `update_channel_label`** or keep it as a thin
  alias. Recommend delete — no other code in the file references it
  after the migration, and a stale alias invites future drift.

---

## Implementation Units

- U1. **Channel override combo in Cellpose section (Segment tab)**

**Goal:** Replace the read-only `_channel_label` in the Cellpose
section with a `_channel_combo`. Add `update_channels()` mirroring
`grouped_seg_panel.update_channels()`. Migrate `_on_run_cellpose` to
read from the combo. Re-wire `_on_state_changed` to call
`update_channels()` instead of `update_channel_label()`. Add the
launcher's napari-layer-selection callback line that calls
`self._seg_panel.update_channels()` (replacing the existing
`update_channel_label()` call at `main_window.py:622`).

**Requirements:** R1, R3, R4, R5, R6.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/gui/segmentation_panel.py`
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Test: `tests/test_gui/test_segmentation_panel_channel_override.py`

**Approach:**
- In `_build_ui`, replace the QLabel `_channel_label` with a QComboBox
  `_channel_combo` in the same row position. Label text stays
  "Channel:". (The existing `_channel_label` field is read by
  `update_channel_label()` only — once that method is deleted, the
  field has no remaining consumers.)
- Add `update_channels()` method to `SegmentationPanel` — copy
  `grouped_seg_panel.update_channels()` body verbatim, substituting
  `self._channel_combo` and `self._get_viewer_window()` →
  `self._launcher._windows.get("viewer")` (the panel's existing
  viewer-access pattern).
- Update `_on_state_changed` (currently lines 73-77) to call
  `update_channels()` instead of `update_channel_label()`.
- In `_on_run_cellpose`, replace `channel_name =
  self.data_model.session.active_channel` (line 391) with
  `channel_name = self._channel_combo.currentText() or None`.
- Delete `update_channel_label()` method and the `_channel_label`
  field (no remaining consumers after the above).
- In `main_window.py:619-624`, replace
  `self._seg_panel.update_channel_label()` with
  `self._seg_panel.update_channels()`. Add the seg-panel
  `update_channels()` call to the dataset-load flow at
  `main_window.py:929-933`, mirroring the grouped panel's line.

**Patterns to follow:**
- `src/percell4/gui/grouped_seg_panel.py:67-72` (combo construction).
- `src/percell4/gui/grouped_seg_panel.py:168-189`
  (`update_channels()`) — copy faithfully.
- `src/percell4/interfaces/gui/main_window.py:619-624` for the
  launcher wire.

**Test scenarios:**
- Happy path: dataset with `channel_names=["ch0","ch1","ch2"]` and
  active_channel="ch0"; constructed panel's `_channel_combo` enumerates
  the three names and `currentText() == "ch0"`.
- Happy path (run): user changes combo to "ch1"; `_on_run_cellpose` is
  invoked (stub the Worker); the channel passed to the prompt-helper /
  Worker construction reflects "ch1", not the session's "ch0".
- Edge case: no dataset loaded; combo is empty; `_on_run_cellpose`
  short-circuits with the existing "Select a channel in the Data tab
  first" status message (R5 — preserve existing UX).
- Edge case: session active_channel set to a name absent from
  `channel_names` (legacy); `update_channels` populates from
  `channel_names`, combo defaults to the first listed channel (Qt
  combo behavior when `setCurrentText` doesn't match).
- Edge case: dataset has no `channel_names` metadata; viewer has
  Image layers; combo populates from viewer layer names (fallback
  path) — mirrors `grouped_seg_panel.update_channels` lines 183-189.
- Edge case: user changes napari's active layer; launcher fires
  `_on_layer_selection_changed`; verify the seg panel's combo re-seeds
  to the new layer name (mocks the launcher's wire).
- Integration: after replacing `_channel_label` with the combo, the
  existing `tests/test_gui/test_segmentation_panel_*` tests still
  pass — verify by running the relevant subset.

**Verification:**
- The Cellpose section shows "Channel: [combo]" instead of a static
  label.
- Picking a different channel in the combo does not write to
  `session.active_channel` (confirmed via spy).
- Running Cellpose dispatches the Worker with image data from the
  combo-selected layer, not the session's active channel.

---

- U2. **Channel override combo in FLIM panel**

**Goal:** Add a "Channel:" + `QComboBox` row at the top of the FLIM
panel UI. Add `update_channels()`. Migrate `_get_active_channel` to
read from the combo. Wire the launcher's napari-layer-selection
callback to call the FLIM panel's `update_channels()` too.

**Requirements:** R2, R3, R4, R5, R6.

**Dependencies:** None (parallel-safe with U1; both touch
`main_window.py` but disjoint hunks).

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Test: `tests/test_gui/test_flim_panel_channel_override.py`

**Approach:**
- In `_build_ui`, add the channel row immediately under the panel's
  title label (line 73-79). Use the same widget construction as
  `grouped_seg_panel.py:67-72`: a `QHBoxLayout` with `QLabel("Channel:")`
  + `QComboBox` (`self._channel_combo`).
- Add `update_channels()` method — copy
  `grouped_seg_panel.update_channels()` body verbatim, substituting
  the panel's existing `self._get_viewer_window` callable for the
  viewer-layer fallback.
- Update `_get_active_channel` to return
  `self._channel_combo.currentText() or None`.
- Subscribe `update_channels` to `Event.DATASET_CHANGED` via
  `self.data_model.session.subscribe(...)` in `__init__`, alongside
  the existing `_refresh_ref_circle_enabled` subscription at lines
  62-64. The dataset-load path goes through this.
- In `main_window.py:619-624`, add a line calling
  `self._flim_panel.update_channels()` so the combo follows napari
  layer-selection changes — same pattern as the seg and grouped
  calls.
- Call `update_channels()` from `__init__` after `_build_ui` so the
  combo is populated when the panel is first shown into an
  already-loaded dataset.

**Patterns to follow:**
- `src/percell4/gui/grouped_seg_panel.py:67-72, 168-189`.
- The FLIM panel's existing `Session.subscribe(Event.DATASET_CHANGED, ...)`
  pattern at lines 62-64 — extend, don't replace.

**Test scenarios:**
- Happy path: panel constructed against a session with
  `channel_names=["ch0","ch1"]` and active="ch0"; combo enumerates
  both, current text "ch0".
- Happy path (override): user picks "ch1"; both
  `_get_active_channel()` and the phasor / wavelet flow reads "ch1".
  Mock the use cases; assert `channel="ch1"` is passed.
- Edge case: no dataset loaded; combo empty;
  `_get_active_channel()` returns `None`; phasor and wavelet flows
  short-circuit with "Select a channel in the viewer first" (R5).
- Edge case: dataset metadata has `channel_names` but viewer is
  closed; combo populates from session metadata, not from viewer
  layers — preserves the Grouped panel's "session-first" fallback.
- Edge case: napari layer selection changes after a user override;
  launcher's `_on_layer_selection_changed` fires; verify the FLIM
  combo re-seeds to the new layer (combo overrides are not sticky
  across napari selections, same as Grouped).
- Integration: `Event.DATASET_CHANGED` fires on dataset load;
  `update_channels` runs; combo populates from the new dataset's
  metadata.

**Verification:**
- FLIM panel shows "Channel: [combo]" at the top, above the existing
  Phasor and Wavelet sections.
- `_get_active_channel()` returns the combo's current text after
  override.
- Running phasor compute and wavelet filter both honor the combo's
  selection.
- No `session.set_active_channel(...)` call is made when the user
  changes the combo (override is local).

---

## System-Wide Impact

- **Interaction graph:** Three launcher wires extend
  `_on_layer_selection_changed` (`main_window.py:619-624`) — already
  calls `_update_active_channel_label`, `_seg_panel.update_channel_label`
  (becomes `update_channels`), `_grouped_seg_panel.update_channels`,
  and now also adds `_flim_panel.update_channels`. The dataset-load
  flow at `:929-933` gains seg + flim refresh calls.
- **Error propagation:** Empty combo handled as today's
  `session.active_channel is None` case — status-bar message,
  no exception.
- **State lifecycle risks:** A user override is silently re-seeded
  when napari's active layer changes. Documented and accepted (R4)
  to match the Grouped panel's contract.
- **API surface parity:** None — the override is purely panel-local.
  No public APIs change.
- **Integration coverage:** Each panel test covers the napari →
  launcher → panel re-seed path with a mocked viewer-layer event.
- **Unchanged invariants:** `Session.active_channel` is still the
  only "global" channel. Combos read it as a default; they never
  write it.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Deleting `update_channel_label` orphans a launcher call site. | U1's main_window edit replaces the call in the same hunk; grep confirms no other consumers before delete. |
| FLIM panel `_get_active_channel` is called before `update_channels` populates the combo (e.g., constructor race). | U2 calls `update_channels()` at the end of `__init__`, after `_build_ui`. If no session dataset exists yet, the combo is empty and `currentText()` returns `""` — preserved via `or None` in the return. |
| Override sticky-vs-resync ambiguity surprises users ("I picked ch1, then it reset to ch2 when I clicked a layer"). | Documented in R4. Matches Grouped behavior; if users complain, revisit as a follow-up after observing real usage. |
| Three near-identical `update_channels` methods drift over time. | Plan defers extraction explicitly. Three is the threshold; a fourth instance triggers extraction. |

---

## Documentation / Operational Notes

- **Follow-up if a fourth channel-combo emerges:** extract
  `update_channels_combo(combo, session, viewer_getter)` helper into
  `src/percell4/gui/_channel_combo.py` (matching the
  `_resource_name_prompt.py` and `_stitching_flim_form.py` private-
  helper convention). Migrate all four sites in one refactor PR.
- **Audit update:** `docs/audits/gui-element-classification.yaml`
  gains two new entries — `seg_panel.cellpose.channel_combo` and
  `flim_panel.channel_combo`. Both are *Action*-class (read session,
  write no session field). Plan calls this out but leaves it as a
  sweep at PR-creation time; out of unit scope to avoid bundling.

---

## Sources & References

- Reference pattern: `src/percell4/gui/grouped_seg_panel.py:67-72, 168-189`
- Cellpose entry: `src/percell4/gui/segmentation_panel.py:108-112, 379-382, 391`
- FLIM entry: `src/percell4/interfaces/gui/task_panels/flim_panel.py:243-244`
- Launcher wires: `src/percell4/interfaces/gui/main_window.py:619-624, 929-933`
- Related convention: `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
- Related pattern: `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
