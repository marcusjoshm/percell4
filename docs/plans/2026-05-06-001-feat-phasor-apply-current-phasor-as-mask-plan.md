---
title: Add "Apply Current Phasor as Mask" button (and rename existing apply button)
type: feat
status: active
date: 2026-05-06
---

# Add "Apply Current Phasor as Mask" button (and rename existing apply button)

## Overview

The phasor plot window currently has one button — `"Apply Visible as Mask"` —
which writes one binary mask per drawn ROI (each ROI's ellipse intersected with
the active phasor filters). This plan does two things:

1. **Rename** that button to `"Apply ROIs as Masks"` to better describe the
   per-ROI fan-out it actually performs. No functional change.
2. **Add** a sibling button, `"Apply Current Phasor as Mask"`, that writes a
   single binary mask of every pixel currently passing the phasor filters
   (intensity threshold, reference circle, active mask, cell selection,
   manually-cleared region). ROIs are ignored — this captures the literal
   visible filter state, not user-drawn shapes. The button prompts the user
   for a mask name, refuses to overwrite an existing name, warns if the result
   would be empty, and auto-selects the new mask as `session.active_mask`
   (Creator pattern, matching the existing button).

Both buttons gate on phasor data being loaded.

---

## Problem Frame

Today the phasor window can only commit "filtered ROI ellipses" to disk. There
is no way to commit "everything I'm currently looking at" — e.g., when the
user has dialed in an intensity threshold and a lifetime reference circle and
wants that filtered region itself to become a downstream mask. The user must
draw a ROI to capture state that is already visible. The new button removes
that workaround.

The rename is a clarity fix — the existing label hides the per-ROI fan-out.
"Apply ROIs as Masks" makes the contrast with "Apply Current Phasor as Mask"
self-explanatory.

---

## Requirements Trace

- R1. The existing `"Apply Visible as Mask"` button is renamed to
  `"Apply ROIs as Masks"` with no functional change. Its signal, handler,
  payload shape, classification entry id, and tests continue to work.
- R2. A new button labeled `"Apply Current Phasor as Mask"` is added next to
  the renamed button on the phasor plot window's right panel.
- R3. Clicking the new button computes a binary mask equal to the AND of every
  active phasor filter — intensity threshold, reference circle, active mask,
  `session.filter_ids`, and the manually-cleared region. ROI ellipses are not
  consulted. The result is the same predicate the histogram and napari preview
  already use (`_compute_visible_valid_2d()`).
- R4. After click, a modal name dialog opens asking for a mask name, prefilled
  with `phasor_<active_channel>_<N>` where `N` is the smallest positive integer
  that does not collide with any existing mask in `/masks/`.
- R5. On OK with a non-empty name that does not collide with an existing mask,
  the mask is written to HDF5 at `/masks/<name>` and selected as
  `session.active_mask` (Creator pattern).
- R6. If the typed name collides with an existing mask, the dialog is reopened
  with a clear inline message and the same name pre-filled. The user can
  rename or Cancel.
- R7. If the resulting mask contains zero pixels, a yes/no confirmation is
  shown ("This mask contains zero pixels. Save anyway?"). Yes saves the empty
  mask; No cancels without saving.
- R8. Both apply buttons are disabled when no phasor data is loaded
  (`self._g_map is None`). They become enabled when `set_phasor_data()`
  delivers G/S maps and revert to disabled when phasor data is cleared.
- R9. Cancelling the name dialog (or any confirmation) aborts cleanly: no
  signal is emitted, no HDF5 write happens, `session.active_mask` is unchanged.

---

## Scope Boundaries

- No change to how `"Apply ROIs as Masks"` (formerly `"Apply Visible as Mask"`)
  computes per-ROI binaries. The rename is label-only.
- No change to the existing `mask_applied` signal's payload contract.
  The new button uses a separate signal with its own payload, so the
  existing per-ROI consumer is untouched.
- No new use case in `application/use_cases/`. The flow stays GUI-driven via
  signal → launcher → store, mirroring the existing pattern.
- No multi-channel / multi-mask batch operation. One click produces one mask.
- No undo affordance for an accidental save — the user can delete the mask via
  the existing data panel.
- No keyboard shortcut for either button.

---

## Context & Research

### Relevant Code and Patterns

- **Existing button + handler:** `src/percell4/interfaces/gui/peer_views/phasor_plot.py:589-591`
  (button), `_on_apply_mask` at line 1756, signal `mask_applied` declared
  at line 251.
- **Visibility predicate (single source of truth):**
  `_compute_visible_valid_2d()` in `src/percell4/interfaces/gui/peer_views/phasor_plot.py:1292-1338`.
  This already AND's intensity, reference circle, active mask, `filter_ids`,
  and the cleared region. The new button must use this verbatim — see the
  bug history under Institutional Learnings.
- **Launcher subscriber that writes masks:**
  `_on_phasor_mask_applied()` in `src/percell4/interfaces/gui/main_window.py:1092-1131`.
  It calls `store.write_mask(name, binary)`, then
  `session.refresh_resource_lists(mask_names=store.list_masks())`, then
  `data_model.set_active_mask(last_name)`. The new flow follows this exact
  three-step shape but for a single (name, mask) tuple.
- **Modal name-prompt convention:** `QInputDialog.getText(parent, title, label, text=default)`
  used at `src/percell4/interfaces/gui/task_panels/data_panel.py:394-398` and
  `:455-458`. Returns `(text, ok)`. Cancel → `ok=False` → no-op. The
  collision/empty handling on this plan is custom (re-prompt loop) since
  `QInputDialog` itself is single-shot.
- **Store write:** `DatasetStore.write_mask(name, array)` in
  `src/percell4/store.py:251-264` (per-call crash-safe write to `/masks/<name>`).
- **Session events:** `Session.set_active_mask` emits `Event.ACTIVE_MASK_CHANGED`,
  bridged to `StateChange(mask=True)` by `CellDataModel`.
  `refresh_resource_lists(mask_names=...)` emits `Event.MASK_LIST_CHANGED` →
  `StateChange(mask_list=True)`.
- **GUI element classification:** `docs/audits/gui-element-classification.yaml`
  contains `phasor_plot.apply_visible_as_mask_button` classified as `Creator`.

### Institutional Learnings

- `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md` —
  Three independent code paths previously computed "visible valid pixels"
  and Apply diverged from Preview. The fix was a single
  `_compute_visible_valid_2d()` helper. **The new button must call this
  helper, never recompute the predicate.** The existing structural-equality
  test (`test_apply_equals_napari_preview`) is the regression guard for the
  old button; the new button needs a parallel guard.
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md` —
  Selector / Creator / Action contract. The new button is a Creator (writes
  a new resource and auto-selects).
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` —
  When writing a new mask while the same dataset is in-session, enumerate
  caches (HDF5 lib cache, in-memory numpy caches, `_active_mask_array`,
  napari layers) and refresh each. The existing launcher subscriber already
  does the right thing via `session.refresh_resource_lists`, the explicit
  `viewer_win.add_mask(...)` call, and `set_active_mask`; the new launcher
  subscriber must do the same.

### External References

External research was not consulted — this is a well-patterned local change
that mirrors the existing apply flow.

---

## Key Technical Decisions

- **Mask source = filters only, ROIs ignored.** "Current phasor state" means
  the AND of every active filter. Drawn ROIs are user shapes layered on top
  and do not participate. Rationale: gives the user a clean way to capture
  the filter state itself (a real gap today); avoids overlapping semantics
  with the renamed `"Apply ROIs as Masks"`.
- **New signal, not a payload variant.** The new button gets its own signal
  (e.g., `phasor_mask_applied`) emitting a single `(name: str, mask: np.ndarray)`
  tuple. Keeping it separate avoids breaking the per-ROI consumer and avoids
  a payload tagged-union. The launcher subscribes to both.
- **Name collision policy = reject and re-prompt.** Explicit naming implies
  the user wants a new mask, not a silent overwrite. Implementation is a
  small loop: open `QInputDialog.getText` → if empty cancel; if collision
  show a `QMessageBox.warning` and reopen with the same text pre-filled;
  else proceed. Rationale: matches user intent and avoids destroying prior
  work without consent.
- **Default name = `phasor_<channel>_<N>`** where `N` is the smallest
  positive integer that yields a name not already in
  `session.dataset.metadata["mask_names"]`. Falls back to `phasor_<N>`
  (no `unknown` placeholder) when `session.active_channel` is falsy.
  Rationale: clear provenance, no typing for quick saves, no collision
  out of the box; reading from session metadata avoids needing a
  `DatasetStore` handle on the phasor window.
- **Empty-mask policy = warn-then-confirm.** A zero-pixel mask is usually a
  filter mistake but occasionally intentional (e.g., a "this channel has no
  signal here" marker). `QMessageBox.question` with Yes/No.
- **Auto-select after write.** Both buttons are Creators. After
  `store.write_mask(name, binary)` the launcher refreshes the mask list and
  calls `data_model.set_active_mask(name)`. Same as today.
- **Disabled-when-empty gate.** Drive both apply buttons from a single
  helper `_refresh_apply_buttons_enabled()` that reads `self._g_map is
  not None` and calls `setEnabled` on both buttons. Call the helper from
  every site that mutates `self._g_map` (currently `set_phasor_data`,
  `_on_dataset_changed`, and any explicit `self._g_map = None` line —
  enumerate during U2 implementation; do not rely on per-site toggles).
  This avoids the regression where one toggle site is missed and the
  buttons drift from the data state.
- **Disabled-state and clarification tooltips.** Set tooltips on both
  buttons so the disabled-state reason and the output semantics are
  visible without trial-and-error. Suggested copy:
  - `Apply ROIs as Masks` — `"Save one mask per drawn ROI (filters
    applied). Disabled until phasor data is loaded."`
  - `Apply Current Phasor as Mask` — `"Save the current filter
    intersection as a single mask. Drawn ROIs are not included.
    Disabled until phasor data is loaded."`
  Tooltip copy for both buttons should be set once at construction;
  Qt shows tooltips on disabled widgets out of the box, so no extra
  state machine is needed.
- **Mask provenance via HDF5 attributes.** When the launcher writes a
  mask via the new flow, also persist the captured filter parameters as
  HDF5 attributes on `/masks/<name>` (intensity threshold, reference
  circle center+radius if set, active mask name at capture time, hash
  or pixel-count of the cleared region). Without this, two
  `phasor_NADH_*` masks saved minutes apart with different thresholds
  are indistinguishable later. Implementation: extend the launcher slot
  in U3 to write the attribute payload via the existing
  `DatasetStore` attribute API (or via direct h5py if no API exists —
  confirm during U3 implementation).
- **Snapshot-vs-commit tension acknowledged.** Auto-selecting the
  freshly written mask matches the existing per-ROI Creator contract,
  but for a snapshot-style use of the new button it can create a
  self-referential trap: the captured mask depends on the previous
  `active_mask`, and saving immediately replaces it, so a second
  consecutive save reflects the *new* active mask, not the original
  filter state. Mitigation kept lightweight: status-bar message at
  save time reads `Saved phasor_<channel>_<N> (now active — next save
  will be filtered against this mask)` so the researcher sees the
  state change before clicking again. Not a full mode toggle; a
  follow-up plan can introduce a "stash without selecting" variant if
  the workflow demands it.

---

## Open Questions

### Resolved During Planning

- **What does "current phasor state" capture?** Filters only; ROIs ignored.
- **Name collision behavior?** Reject and re-prompt with same name pre-filled.
- **Default name template?** `phasor_<active_channel>_<N>`.
- **Auto-select the new mask?** Yes — Creator pattern, matches existing button.
- **No-data behavior?** Disable both apply buttons when `_g_map is None`.
- **Empty-mask behavior?** Confirmation dialog ("Save anyway?").

### Deferred to Implementation

- Exact Qt signal name for the new signal (`phasor_mask_applied` is a
  reasonable placeholder; settle when wiring). U2 tests should reference
  this signal by attribute, so renaming during U3 wiring stays
  mechanical.
- Whether the re-prompt loop lives inline in the handler or in a small
  private helper (`_prompt_mask_name(default)` returning `str | None`).
  Implementer's call once they see the actual control flow.
- **Button label validation.** The pairing `Apply ROIs as Masks` /
  `Apply Current Phasor as Mask` was chosen for contrast clarity, but
  "Current Phasor" is engineering framing. Before merging U2, run the
  pair past one lab member. Candidate alternatives if the current
  pair tests poorly: `Apply Filtered Region as Mask`,
  `Save Filter Result as Mask`, or keep `Apply Visible as Mask` for
  the new button (since it captures what is literally visible on the
  histogram). Tooltips help but the labels are the primary affordance.
- **`DatasetStore.write_mask` collision policy.** The phasor window
  validates against `session.dataset.metadata["mask_names"]` before
  emitting, but the launcher write happens after a Qt event-loop hop —
  another in-process Creator (e.g., `accept_threshold`,
  `add_layer_dialog`) could land a same-named mask between check and
  write. The current plan accepts this race because PerCell4 is single-
  process and concurrent in-process Creators on the same name are
  unlikely in normal workflows. Confirm during U3 implementation
  whether `write_mask` overwrites silently or raises; if it overwrites
  and the race is judged real after live use, move the collision check
  into `_on_phasor_current_mask_applied` (re-read
  `session.dataset.metadata["mask_names"]` immediately before
  `store.write_mask`).
- **Empty-mask UX revisit.** The plan keeps the warn-then-confirm
  dialog (per the planning Q&A), but the alternative — refusing
  zero-pixel saves outright with an inline message — has lower
  friction for the dominant "filter mistake" case. Revisit after the
  feature ships if researchers consistently click "Yes" or report
  confusion. Cheaper to remove the confirmation than to add it later.
- **Snapshot-without-active-replacement variant.** A future
  enhancement could add a checkbox or modifier-click that writes the
  mask without auto-selecting it, for users who want to stash without
  disrupting the in-flight active mask. Out of scope for this plan;
  the status-bar message added in U3 is the lightweight mitigation
  for now.

---

## Implementation Units

- U1. **Rename `"Apply Visible as Mask"` to `"Apply ROIs as Masks"`**

**Goal:** Update the existing button label and every label-coupled reference
without changing behavior.

**Requirements:** R1.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Modify: `docs/audits/gui-element-classification.yaml`
- Modify: `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py`
  (rename the test module to match if it asserts on label text; otherwise
  leave the filename as historical and only update assertions)

**Approach:**
- Change the `QPushButton(...)` label string at `phasor_plot.py:589` from
  `"Apply Visible as Mask"` to `"Apply ROIs as Masks"`.
- Update the comment block at `phasor_plot.py:237` ("emitted when user
  clicks ...") and the explanatory comments at `:295`, `:895`, `:1597` to
  use the new label.
- In `docs/audits/gui-element-classification.yaml`, rename the entry id
  from `phasor_plot.apply_visible_as_mask_button` to
  `phasor_plot.apply_rois_as_masks_button`. Update the `widget_text` /
  `notes` fields if they reference the old label. Class stays `Creator`.
- Search the test file for any assertion that finds the button by its
  text (e.g., `findChild(QPushButton, ...)`, `findButtonByText(...)`,
  `text() == "Apply Visible as Mask"`) and update those strings. Internal
  test names that mention "apply visible" can stay — they describe the
  feature, not the label.
- Do not rename `_on_apply_mask`, the `mask_applied` signal, or the
  launcher subscriber. The label is the only user-visible change.

**Patterns to follow:**
- The existing button construction at `phasor_plot.py:589-591`.

**Test scenarios:**
- Happy path: After rename, the button is findable by its new label
  `"Apply ROIs as Masks"`. Existing 9 tests in
  `test_phasor_apply_visible_as_mask.py` continue to pass without
  modification beyond label-string updates.
- Edge case: `gui-element-classification.yaml` parses cleanly with the
  new id and no orphan references.

**Verification:**
- `python main.py` opens the phasor window and the renamed button is
  visible with the new label.
- `pytest tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py`
  passes.
- Searching the repo for `"Apply Visible as Mask"` returns zero hits.

---

- U2. **Add `"Apply Current Phasor as Mask"` button, name dialog, and signal on PhasorPlotWindow**

**Goal:** Build the new button, the name-prompt + collision + empty-mask
dialogs, and a new signal carrying `(name, binary_mask)` to the launcher.
Also add the disabled-when-empty gate that applies to both apply buttons.

**Requirements:** R2, R3, R4, R6, R7, R8, R9.

**Dependencies:** U1 (so the layout block around `phasor_plot.py:589` is
already in its final shape before adding the sibling button).

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Modify: `docs/audits/gui-element-classification.yaml`
- Modify: `src/percell4/interfaces/gui/peer_views/CLAUDE.md` if it exists
  and documents the phasor window's signals (add the new signal to the
  list)
- Test: `tests/test_gui_workflows/test_phasor_apply_current_phasor_as_mask.py`

**Approach:**
- Declare a new Qt signal on `PhasorPlotWindow`, e.g.
  `phasor_mask_applied = Signal(object)` carrying a tuple
  `(name: str, mask: np.ndarray[uint8, 2D])`. Add it next to the existing
  `mask_applied` declaration around line 251 with a matching docstring.
- Add `QPushButton("Apply Current Phasor as Mask")` immediately below the
  renamed button at `phasor_plot.py:591`. Connect `clicked` to a new
  `_on_apply_current_phasor_as_mask()` handler. Both buttons stay in
  `right_layout` so the column layout is unchanged.
- Hold both `QPushButton` instances on `self` (e.g., `self._btn_apply_rois`
  and `self._btn_apply_current_phasor`) so the enable/disable gate (U2's
  R8 piece) can flip them.
- Implement `_on_apply_current_phasor_as_mask()`:
  1. Compute `visible = self._compute_visible_valid_2d()`. If `visible is
     None` (no phasor data) return immediately — this must precede any
     `.astype` call. The disabled-when-empty gate normally prevents reach,
     but the early-return is the load-bearing safety net.
  2. Build `binary = visible.astype(np.uint8)`.
  3. Read the existing-mask list from session metadata, **not** via a
     direct store handle (PhasorPlotWindow does not own a `DatasetStore`
     reference; it has `self._get_repo` and `self._session`). Use
     `existing = list(self._session.dataset.metadata.get("mask_names", []))`
     when `self._session.dataset is not None`, else `existing = []`.
     Compute the default name: `phasor_<channel>_<N>` where channel is
     `self._session.active_channel` (when truthy) and `N` is the smallest
     positive integer such that the resulting name is not in `existing`.
     When `active_channel` is falsy, use `phasor_<N>` (no `unknown`
     placeholder). Helper: `_default_phasor_mask_name()`.
  4. Run the prompt-and-validate loop (`while True:`):
     - `name, ok = QInputDialog.getText(self, "Save Phasor as Mask", "Mask name:", text=current_default)`
       — `current_default` starts at the computed default and is updated
       below on retry.
     - If `not ok`: return (cancel).
     - If `name.strip() == ""`: keep `current_default` unchanged and loop
       (prompt re-opens with the original computed default, not the
       blank string the user just submitted).
     - If `name in existing`: show
       `QMessageBox.warning(self, "Name in use", f"A mask named '{name}' already exists. Please enter a different name below.")`,
       set `current_default = name`, and loop.
     - Else `break`.
  5. If `int(binary.sum()) == 0`, show
     `QMessageBox.question(self, "Empty mask", "No pixels match your current filters. Save this empty mask anyway?", Yes|No)`.
     If `No`, return (cancel). The copy avoids "zero pixels" — a
     researcher's mental model is regions/cells, not pixels.
  6. Emit `self.phasor_mask_applied.emit((name, binary))`.
- Implement the disabled-when-empty gate via a single helper, not
  per-site toggles:
  - Define `_refresh_apply_buttons_enabled(self)` that reads
    `self._g_map is not None` and calls `setEnabled` on both
    `self._btn_apply_rois` and `self._btn_apply_current_phasor`.
  - Call the helper from every site that mutates `self._g_map`. Enumerate
    during implementation by grepping `self._g_map = ` across
    `phasor_plot.py`; expected sites include `set_phasor_data`,
    `_on_dataset_changed`, and any explicit clear-data path. Do **not**
    duplicate the toggle inline at each site — call the helper.
  - Initial state at construction: disabled (data is loaded after init).
  - Set tooltips on both buttons at construction (Qt shows tooltips on
    disabled widgets):
    - `self._btn_apply_rois.setToolTip("Save one mask per drawn ROI (filters applied). Disabled until phasor data is loaded.")`
    - `self._btn_apply_current_phasor.setToolTip("Save the current filter intersection as a single mask. Drawn ROIs are not included. Disabled until phasor data is loaded.")`
- Add the new entry to `docs/audits/gui-element-classification.yaml`:
  ```yaml
  - id: phasor_plot.apply_current_phasor_as_mask_button
    class: Creator
    path: src/percell4/interfaces/gui/peer_views/phasor_plot.py
    widget_type: QPushButton
    handler: _on_apply_current_phasor_as_mask
    reads: [active_channel, filter_ids, active_mask]
    writes: []
    notes: |
      Emits phasor_mask_applied(name, binary). Launcher subscriber
      writes /masks/<name> and sets session.active_mask. Same Creator
      contract as apply_rois_as_masks_button.
  ```

**Execution note:** Implement the visibility-predicate test first (the
parallel of `test_apply_equals_napari_preview`) before wiring the dialog
chain — same posture as the existing test; protects against the
ignored-filters bug class documented in the
`phasor-apply-visible-as-mask-ignored-filters-2026-05-03` learning.

**Patterns to follow:**
- `_on_apply_mask` at `phasor_plot.py:1756-1781` for signal-emission shape.
- `QInputDialog.getText(...)` at `data_panel.py:394-398` for the modal
  name prompt convention.
- Existing structural-equality test
  `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py::test_apply_equals_napari_preview`
  for the new structural-equality test.

**Test scenarios:**
- Happy path — Filters AND'd correctly: with intensity threshold,
  reference circle, `filter_ids`, and an active mask all set, click the
  button and accept the default name. The emitted mask equals
  `_compute_visible_valid_2d().astype(uint8)` pixel-for-pixel
  (structural-equality regression guard).
- Happy path — Default name template: with `session.active_channel ==
  "NADH"` and no existing masks, the dialog opens prefilled with
  `phasor_NADH_1`. With an existing `phasor_NADH_1`, prefilled with
  `phasor_NADH_2`.
- Edge case — Filters only, no ROIs: when ROIs exist on the window but
  the new button is clicked, the mask is identical to the case where the
  ROIs are not drawn. ROIs do not contribute.
- Edge case — Filter respect (parallel to existing `test_apply_respects_*`
  suite, one test per filter):
  - Pixels below `_intensity_threshold` are 0.
  - Pixels outside `_ref_circle_*` are 0.
  - Pixels not in `session.filter_ids` (when non-empty) are 0.
  - Pixels outside the active mask are 0.
  - Pixels inside `_cleared_mask` are 0.
- Edge case — `active_channel` is None or empty string: default name
  falls back to `phasor_<N>` (no `unknown` segment).
- Error path — Cancel on the name prompt: `phasor_mask_applied` is never
  emitted, `store.list_masks()` does not gain a new entry, and
  `session.active_mask` is unchanged.
- Error path — Empty name then OK: re-prompt opens with the default
  pre-filled; one user Cancel ends the flow with no signal.
- Error path — Name collision: typing an existing mask name yields a
  `QMessageBox.warning`; the prompt re-opens with the typed name
  pre-filled; on Cancel from the second prompt, no signal is emitted.
- Error path — Empty-mask confirmation: when the AND of filters yields
  zero pixels, a confirmation dialog appears. "No" → no signal. "Yes"
  → signal emits with an all-zero binary.
- Error path — Disabled when no data: at construction time and after a
  hypothetical clear-data path, both `Apply ROIs as Masks` and
  `Apply Current Phasor as Mask` are disabled; after `set_phasor_data`
  with non-None G/S, both are enabled.
- Edge case — Enable/disable transition (both buttons toggle together):
  construct the window with no data and assert both buttons are
  disabled; call `set_phasor_data(g, s)` with valid arrays and assert
  both buttons become enabled in the same call (not just one); drive
  any clear-data path and assert both return to disabled. Guards
  against a regression that flips one button's gate but leaves the
  sibling stale.
- Integration — Signal payload shape: the emitted tuple is
  `(str, np.ndarray)` with `dtype == np.uint8`, `ndim == 2`, and shape
  matching `self._g_map.shape`.

**Verification:**
- The new test file (above) passes end-to-end.
- Manual: open the phasor window with no data → both apply buttons are
  disabled. Load phasor data → both enable. Click `Apply Current Phasor
  as Mask` → name dialog with `phasor_<channel>_1` prefilled. OK → no
  visible action yet (signal-only — the launcher subscriber lands in U3).

---

- U3. **Wire the launcher subscriber that persists the new mask and auto-selects it**

**Goal:** In `main_window.py`, subscribe to the new
`phasor_mask_applied` signal, write to HDF5 via `store.write_mask`,
refresh the session's mask list, and set `session.active_mask` to the new
name. Mirrors the existing `_on_phasor_mask_applied` subscriber for the
per-ROI flow.

**Requirements:** R5 (and ties off R2, R7's "Yes" branch, R9's clean-cancel
behavior at the launcher boundary).

**Dependencies:** U2 (the signal must exist).

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Modify (conditional): `src/percell4/store.py` — add a minimal
  `set_mask_attrs(name: str, attrs: dict)` surface if no equivalent
  exists. Confirm during implementation; if `DatasetStore` already has
  a generic attribute writer, use that instead.
- Test: `tests/test_gui_workflows/test_phasor_apply_current_phasor_as_mask.py`
  (extend the U2 test file with launcher-integration scenarios that use
  a real `DatasetStore` fixture and assert HDF5 + session state, plus
  attribute-persistence and status-bar assertions)

**Approach:**
- Where the launcher today connects `phasor_window.mask_applied` to
  `self._on_phasor_mask_applied` (around `main_window.py:591` per the
  research map), add a parallel connection:
  `phasor_window.phasor_mask_applied.connect(self._on_phasor_current_mask_applied)`.
- Implement `_on_phasor_current_mask_applied(payload)`:
  1. Unpack `(name, binary)`.
  2. Resolve the store the same way the existing per-ROI subscriber does
     (`main_window.py:1112`):
     `store = getattr(self, "_current_store", None)`. If `store is None`,
     return — the dataset has been cleared and there is nothing to write
     to. (Mirrors the per-ROI `_on_phasor_mask_applied` guard.)
  3. `store.write_mask(name, binary)`.
  4. If a viewer window exists and is alive, add the mask as a napari
     layer so the user gets immediate visual feedback — mirrors
     `main_window.py:1120-1122`:
     ```python
     viewer_win = self._windows.get("viewer")
     if viewer_win is not None and viewer_win._is_alive():
         viewer_win.add_mask(binary, name=name)
     ```
     Without this step the new mask saves silently and only appears after
     a session refresh, breaking parity with the per-ROI flow.
  5. Persist filter-state attributes on `/masks/<name>` for provenance.
     Capture from the emitting phasor window (passed through the
     payload as a small dict, or read by reaching back through
     `self._windows.get("phasor_plot")` — implementer's choice during
     U3). Attribute keys: `phasor_intensity_threshold` (float),
     `phasor_ref_circle_center` (length-2 array or `None`),
     `phasor_ref_circle_radius` (float or `None`),
     `phasor_active_mask_at_capture` (str or empty),
     `phasor_cleared_pixel_count` (int),
     `phasor_active_channel` (str), `phasor_capture_iso8601` (str).
     If `DatasetStore` does not yet have an `set_mask_attrs(name, dict)`
     surface, add a minimal one in U3 next to `write_mask` (single-call
     attrs write inside the same `h5py.File` open block). This is the
     cheapest provenance fix; without it `phasor_NADH_3` and
     `phasor_NADH_4` are indistinguishable to the researcher reopening
     the dataset weeks later.
  6. `self.data_model.session.refresh_resource_lists(mask_names=store.list_masks())`.
  7. `self.data_model.set_active_mask(name)`.
  8. Surface the auto-select in the launcher's status bar:
     `self._status_bar.showMessage(f"Saved {name} (now active — next save will be filtered against this mask)", timeout=5000)`
     (or the project's existing status-message helper if there is one).
     This makes the snapshot→active transition visible before the
     researcher clicks the button a second time.
- Do not collision-check here — the prompt loop on the phasor window
  side already guarantees `name` is not in the metadata mask list at
  emission time. Re-checking here would race; relying on the upstream
  guarantee matches the existing `_on_phasor_mask_applied` shape.
- The existing per-ROI subscriber `_on_phasor_mask_applied` is left
  unchanged.

**Patterns to follow:**
- `_on_phasor_mask_applied` at `main_window.py:1092-1131` for the exact
  three-call shape (write → refresh list → set active).

**Test scenarios:**
- Integration — Happy path persistence: a phasor window driven by a real
  `DatasetStore` fixture emits `phasor_mask_applied(("phasor_NADH_1", binary))`.
  After the event loop drains, `/masks/phasor_NADH_1` exists in HDF5
  with the same pixel content; `store.list_masks()` includes it;
  `session.active_mask == "phasor_NADH_1"`; a single
  `state_changed` is observed with `mask=True` (and `mask_list=True`).
- Integration — Napari layer parity: with a stub viewer window present,
  emitting `phasor_mask_applied(...)` results in a `viewer_win.add_mask`
  call with the same `(binary, name)` payload — matches the per-ROI
  flow's visual feedback at `main_window.py:1120-1122`.
- Integration — No store / no viewer: emitting when `_current_store is
  None` returns silently with no exception, no HDF5 write, no
  `set_active_mask` call. Same with no viewer attached — write proceeds
  but the layer step is skipped.
- Integration — Two consecutive saves: emitting twice with names
  `phasor_NADH_1` then `phasor_NADH_2` results in both masks present
  in HDF5 and `session.active_mask == "phasor_NADH_2"`.
- Integration — Filter-state attributes: after a save, `/masks/<name>`
  carries HDF5 attributes for `phasor_intensity_threshold`,
  `phasor_active_channel`, `phasor_capture_iso8601`, and the
  ref-circle / active-mask / cleared-pixel-count fields. Two saves at
  different threshold values yield distinguishable attribute values.
- Integration — Status-bar message: after a save, the launcher's
  status bar shows the auto-select notification within the test's
  assertion window (the message can be observed via the launcher's
  status-message helper or by spying on `showMessage` calls).
- Integration — ViewerWindow refresh: a stub `ViewerWindow` connected to
  `state_changed` receives the mask-changed event after the write (no
  stale-cache regression — guards against the
  `in-session-hdf5-staleness-multi-vector-2026-04-30` bug class).
- Edge case — Cancel never reaches the subscriber: when the phasor
  window's name dialog is cancelled (signal not emitted), the launcher
  subscriber is not invoked, no HDF5 entry is created, `active_mask` is
  unchanged. (This is implicitly covered by U2's cancel test but
  worth asserting end-to-end.)

**Verification:**
- Test file above passes.
- Manual: open the phasor window, set filters, click the new button,
  type a name, OK. The mask appears in the data panel's mask selector,
  becomes the active mask, and downstream views (cell table, viewer
  layers) update without restart.

---

## System-Wide Impact

- **Interaction graph:** New signal `PhasorPlotWindow.phasor_mask_applied`
  → new launcher slot `_on_phasor_current_mask_applied` → existing
  `DatasetStore.write_mask` → existing `Session.refresh_resource_lists`
  + `Session.set_active_mask` → existing `CellDataModel.state_changed` →
  every existing `state_changed` subscriber (ViewerWindow, data panel
  mask selector, the phasor window itself, etc.).
- **Error propagation:** `store.write_mask` failures (disk full, HDF5
  permission) propagate as exceptions out of the launcher slot. Today's
  per-ROI subscriber lets these bubble; the new subscriber follows suit
  for parity. No swallowing.
- **State lifecycle risks:** None new — the four-step write-then-refresh
  shape is the exact pattern the existing per-ROI subscriber uses, so the
  cache invalidation chain documented in
  `in-session-hdf5-staleness-multi-vector-2026-04-30` is already proven
  correct for that path.
- **API surface parity:** Both apply buttons share the same Creator
  contract (write resource → auto-select). `gui-element-classification.yaml`
  is updated to keep the audit aligned. The disabled-when-empty gate is
  applied to both buttons identically.
- **Integration coverage:** U3's integration tests exercise the full
  signal → store → session → state_changed chain end-to-end; unit-only
  coverage on U2 would not catch the launcher binding regressions.
- **Unchanged invariants:** The existing `mask_applied` signal, its
  `(roi_name, binary, color)` payload shape, the launcher's
  `_on_phasor_mask_applied` slot, and the per-ROI test suite are
  explicitly unchanged. The new flow is additive.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| New button recomputes the visibility predicate inline and drifts from `_compute_visible_valid_2d()` (the bug class documented in `phasor-apply-visible-as-mask-ignored-filters-2026-05-03`). | U2 mandates calling the existing helper, and the structural-equality test is the regression guard. |
| Race between collision check on the phasor side and write on the launcher side (e.g., if a hypothetical second writer existed). | Single-process, single-store assumption holds today. If multi-process writers ever appear, collision handling moves into `store.write_mask`. Out of scope here; noted in deferred questions if needed later. |
| Renaming the classification entry id breaks an audit script or external doc that grepped the old id. | The id `phasor_plot.apply_visible_as_mask_button` appears only in `gui-element-classification.yaml` (per research map). After rename, repo-wide grep for the old id should be empty — verify in U1's verification step. |
| Tests in `test_phasor_apply_visible_as_mask.py` that find the button by its label will break on rename. | U1 enumerates and updates those assertions explicitly. |
| User saves an empty mask after confirmation and is later confused why downstream views are blank. | Acceptable — the confirmation message is explicit and researcher-readable ("No pixels match your current filters"). Future enhancement could add a status-bar note "Saved empty mask: phasor_X_1"; out of scope. |
| Auto-select replaces the in-flight active mask, creating a self-referential trap on consecutive saves. | U3 surfaces a status-bar message at save time so the researcher sees the active-mask change before the next click. A "stash without selecting" variant is deferred. |
| Two `phasor_<channel>_<N>` masks captured minutes apart with different filter parameters look identical at recall time. | U3 persists filter-state attributes on `/masks/<name>` (intensity threshold, ref circle, active-mask-at-capture, cleared-pixel count, channel, ISO timestamp). |
| In-process race between phasor-side collision check and launcher-side write (another Creator lands same name in the gap). | Single-process and in-process Creators rarely contend on the same auto-incremented name; deferred-to-implementation note documents the policy and the optional re-check at write time. |

---

## Documentation / Operational Notes

- After landing all three units, append a one-line entry to
  `docs/audits/session-mutation-graph.md` if it currently lists
  apply-mask edges (so the new edge is recorded). If not present there,
  the classification yaml change is sufficient.
- No migration, no rollout flag, no monitoring impact. Single-process
  desktop app.
- Consider a brief `docs/solutions/` entry only if the prompt-loop +
  collision-then-overwrite pattern proves reusable elsewhere. Not
  required for this plan.

---

## Sources & References

- Existing button + handler: `src/percell4/interfaces/gui/peer_views/phasor_plot.py:589-591, 1756-1781`
- Visibility predicate: `src/percell4/interfaces/gui/peer_views/phasor_plot.py:1292-1338, 1404-1425`
- Launcher subscriber: `src/percell4/interfaces/gui/main_window.py:1092-1131`
- Store write: `src/percell4/store.py:251-264`
- Modal name dialog convention: `src/percell4/interfaces/gui/task_panels/data_panel.py:394-398, 455-458`
- GUI classification: `docs/audits/gui-element-classification.yaml`
- Existing test suite: `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py`
- Single-source-of-truth bug: `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`
- Cache-invalidation playbook: `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
- Action/Creator/Selector contract: `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
