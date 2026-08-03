---
title: "feat: Group datasets by identical mask set in the existing-mask picker"
type: feat
status: completed
date: 2026-07-20
deepened: 2026-07-20
---

# feat: Group datasets by identical mask set in the existing-mask picker

## Overview

The "Use existing masks (skip thresholding rounds)" section of the single-cell
thresholding workflow config dialog currently renders **one multi-select mask
list per dataset**. When many datasets share the same available `/masks` layers
(the common case — e.g. `Dcp2_mask`, `P-body_mask`, `P-body_mask2`, `adaptive`
on every dataset), the researcher must click the same masks in every row, which
is prohibitive at scale.

This change auto-groups datasets by their **set of available mask layers**:
datasets that offer an identical mask set collapse into a single shared picker,
so masks are selected once per group and applied to every member. Separate
groups appear only when the available masks differ. Each group shows a member
count with a collapsible list of member dataset names. When a few datasets in a
group need a different selection (same available masks, different experimental
condition), the user can split them into their own sub-group.

The reshape is confined to how the picker is *presented* and how selections are
*captured*. The downstream contract — `existing_mask_selections` as a per-dataset
`dict[str, list[str]]` consumed by `SingleCellThresholdingRunner` and validated
by `WorkflowConfig.__post_init__` — is preserved in shape, keys, and values: the
runner and `__post_init__` receive the identical per-dataset dict. (Key iteration
order changes from queue order to group-then-member order; both consumers are
order-insensitive — see Key Technical Decisions.)

---

## Problem Frame

Source: direct user request (no upstream `docs/brainstorms/*-requirements.md`
matched — the nearest, `2026-06-03-headless-grouped-thresholding-puncta-requirements.md`,
concerns grouped *thresholding*, not this mask-reuse picker).

The pain: `_refresh_mask_picker` (`src/percell4/gui/workflows/single_cell/config_dialog.py`)
builds a `QListWidget` per `_PendingDataset` inside a `QFormLayout`. A run over
N datasets that all expose the same 3–4 masks forces N × (per-mask clicks). The
screenshot the user provided shows eight visually-identical rows, each requiring
the same selection.

The user wants the interface to "lump datasets together that have identical mask
selections" and to "only see multiple entries when the masks do not match."
Interpreted against the code: group by each dataset's **available** mask set
(the list items the picker offers), pick once per group.

---

## Requirements Trace

- R1. Datasets with an identical set of available `/masks` layers are presented
  as a single shared mask picker; selecting masks once applies the selection to
  every member dataset in that group.
- R2. Datasets whose available mask sets differ appear as separate groups —
  multiple entries appear only when the masks do not match.
- R3. Each group shows a member count and, on demand, a collapsible list of the
  member dataset names (transparency about what will be measured).
- R4. A group's members share one selection by default, but the user can **split**
  a subset of a group's members into a new independent sub-group (same available
  masks, its own selection) so datasets that carry identical masks yet belong to
  different conditions can be measured differently. Split and merge-back are
  reversible. (Per user decision to add a minimal breakout; identical *available*
  masks do not imply identical *desired* selections.)
- R5. The `existing_mask_selections` output contract is preserved in shape, keys,
  and values: a per-dataset `dict[str, list[str]]` keyed by each dataset's
  disambiguated `display_name`, one entry per member, never an empty-list value.
  `WorkflowConfig`, its `__post_init__` validation, and `SingleCellThresholdingRunner`
  are unchanged. (Dict key order becomes group-then-member rather than queue order;
  both consumers are order-insensitive, and the CSV mask-union must stay
  order-independent — see Deferred to Implementation.)
- R6. Datasets with no available masks form their own non-selectable group and
  are never merged into a real group.
- R7. User mask selections survive picker refreshes (dataset add/remove) and the
  group's non-destructive on/off toggle.
- R8. Every new or changed interactive widget wires its user-edit signal and is
  covered by a signal-path regression test (the `qt-wire-user-edit-signals`
  convention), including one test that drives selection *through the signal* and
  asserts the fan-out.

**Success criteria:** With 8 datasets sharing one mask set, the picker shows a
single group; selecting 2 masks there yields `existing_mask_selections` with 8
entries (one per dataset), each `[mask_a, mask_b]`; a run measures those 2 masks
on all 8 datasets. Adding a 9th dataset with a different mask set adds exactly one
new group without disturbing the first group's selection. Splitting 2 of the 8
into their own sub-group and choosing a different mask there yields 6 entries with
the group selection and 2 with the split selection — still one entry per dataset.

---

## Scope Boundaries

- **Minimal per-group breakout IS in scope** (U4). Identical *available* masks do
  not imply identical *desired* selections — datasets in one auto-group may belong
  to different conditions — so the user can split a subset of a group's members
  into an independent sub-group with its own selection. Deliberately minimal:
  split/merge-back of member subsets, not a free-form per-dataset editor.
  Same-signature datasets still default to one shared selection until explicitly
  split.
- **Newly added same-signature datasets join the default (unsplit) sub-group and
  inherit its current selection.** Adding datasets after selecting therefore
  auto-enrolls them into that selection — intended (it is the point of shared
  groups), and the visible member list ensures nothing is measured invisibly.
- **No "compatible-selection"/broadcast mode and no manual grouping.** Grouping
  is automatic and keyed on the available mask set, computed when the picker is
  built — not on the current selection, and not user-drawn. (Rejected
  alternatives; see Open Questions.)
- **No changes to the runner, the `WorkflowConfig`/`WorkflowDatasetEntry`
  dataclasses, or the per-dataset output contract.** This is a
  presentation/capture change only.
- **No changes to the Segmentation Selection picker** (`_build_segmentation_group`
  / `segmentation_overrides`). Only the mask picker is regrouped, even though the
  two mirror each other today.
- **No change to no-mask/`tiff_pending` behavior** other than grouping them into
  one non-selectable group instead of N per-dataset "No masks found" rows.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/gui/workflows/single_cell/config_dialog.py`
  - `_build_mask_selection_group` (checkable `QGroupBox`; `_mask_form`/
    `_mask_form_host` `QFormLayout`; `box.toggled → _on_mask_reuse_toggled`).
  - `_refresh_mask_picker` — snapshots prior selections keyed by `display_name`,
    rebuilds one `QListWidget` (`MultiSelection`, `maxHeight 90`) per dataset,
    wires `lst.itemSelectionChanged → _update_start_enabled`. Called from
    `_refresh_dataset_tree`.
  - `_dataset_masks(pd)` — `DatasetStore(pd.h5_path).list_masks()` for
    `H5_EXISTING`, else `[]`.
  - `existing_mask_selections` property — returns `{display_name: [selected]}`,
    omitting no-mask datasets and empty selections.
  - `_mask_lists: list[tuple[_PendingDataset, QListWidget, bool]]` — the field to
    be replaced; `has_masks` is tracked explicitly because a checkable
    `QGroupBox` disables *all* children (so `isEnabled()` can't distinguish "no
    masks here" from "mask-reuse mode off").
  - `_add_pending` — the disambiguation pass guaranteeing unique `display_name`
    (`stem`, `stem (2)`, …); `display_name` becomes `WorkflowDatasetEntry.name`
    and every downstream dict key.
  - `_update_start_enabled` (reads `existing_mask_selections`) and
    `_try_build_config` (reads the same property; builds CSV columns from the
    union of selected masks) — both remain correct as long as the property
    contract is preserved.
- Consumers to keep unaffected:
  - `src/percell4/gui/workflows/single_cell/runner.py` — `_measure_round_specs_for`
    reads `config.existing_mask_selections.get(entry.name, [])`; an omitted key
    → base metrics only, a present key → one measure-only round per mask.
  - `src/percell4/workflows/models.py` — `WorkflowConfig.use_existing_masks`,
    `existing_mask_selections`; `__post_init__` rejects unknown-dataset keys and
    empty-list values, and allows empty rounds only when a non-empty selection
    exists.
  - `src/percell4/store.py` — `DatasetStore.list_masks()` → `list_groups("masks")`;
    h5py default (alphabetical) key order, deterministic but canonicalize anyway.
- **No existing collapsible-widget precedent** exists in `src/` (grep for
  `QToolButton` / `setExpanded` / `addChild` is empty). The flat `_dataset_tree`
  `QTreeWidget` and the pervasive checkable `QGroupBox` (mask/particles/dilute)
  are the closest idioms. The collapsible member list is a new, self-contained
  micro-pattern.

### Institutional Learnings

- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — wire the
  user-edit signal at construction, adjacent to the widget. Tests using
  `setSelected`/`setCheckState` pass even when the wire is missing; add a
  signal-path regression test. Applies directly (R8).
- `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`
  (`canonical_source: src/percell4/gui/_dialog_utils.py`;
  `applies_to` includes `src/percell4/gui/workflows/**/config_dialog.py`) — keep
  the outer `wrap_in_scroll` and cap inner list heights. A compliance test
  (`tests/test_gui/test_dialog_helper_compliance.py`) AST-walks dialogs; don't
  break it. `cap_to_screen` no-ops without a screen (headless-safe).
- `docs/solutions/conventions/gui-panel-and-batch-workflow-method-parity-2026-07-13.md`
  (`related_components` lists this dialog) — a UI restructure must not silently
  change what the runner receives. Keep the emitted per-dataset contract identical
  (R5).
- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
  (Pattern 3) — on `QFormLayout` row rebuild, tear down old connections and
  capture *values not references*; avoid reference-capturing lambdas against
  widgets that a rebuild will destroy.
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  — keep a group's widget construction + accessor in one place (single update
  point for the widget → selection mapping).
- `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md` — the
  dialog-value-capture rule: `existing_mask_selections` is read live from widget
  state inside `_try_build_config` before `accept()`, so selection state must stay
  queryable at build time (do not defer selection into a destroyed sub-dialog).

### Project Constraints

- GUI/napari tests only run on CI (local mixed-Qt venv segfaults) — write the GUI
  tests but expect CI to validate them. A Qt-free pure helper (U1) is the part
  that runs locally.

---

## Key Technical Decisions

- **Group signature = `tuple(sorted(DatasetStore.list_masks()))`.** Canonical and
  order-insensitive, robust to any h5py key-ordering drift. Datasets with an equal
  signature collapse into one group. The empty signature `()` is the no-mask
  group.
- **Group ordering = first appearance of the signature in `_pending_datasets`
  order; member ordering = queue order — except the no-mask (empty-signature)
  group always renders last.** Deterministic, stable, and predictable as datasets
  are added/removed; pinning the non-selectable no-mask group to the bottom keeps
  a dead placeholder row from splitting the actionable groups (and matches the
  layout sketch).
- **Extract the partition as a pure, Qt-free helper** (`group_by_mask_signature`
  in a new `src/percell4/workflows/mask_grouping.py`) so it is unit-testable
  locally and owns the canonicalization + ordering invariants. It lives in the
  repo's Qt-free workflows-core (alongside `models.py` / `channels.py` /
  `csv_columns.py`), not under `gui/workflows/`, so its test lands in
  `tests/test_workflows/` — outside the `qtbot`-gated `tests/test_gui_workflows/`
  conftest — and therefore runs in the local venv. The dialog imports it and
  builds widgets from its output.
- **Replace `_mask_lists` with `_mask_groups: list[_MaskGroup]`.** Each `_MaskGroup`
  bundles its member `_PendingDataset`s, its signature, the single shared
  `QListWidget`, the `has_masks` flag, and the collapsible member widgets — one
  construction/accessor site (per the shared-widget learning).
- **`existing_mask_selections` fans out:** for each group with `has_masks` and a
  non-empty selected set, emit `{member.display_name: chosen}` for every member.
  This preserves the per-dataset contract — shape, keys, and values (R5). Empty
  selections are omitted.
- **Preserve selection across refresh keyed by signature** (not by `display_name`,
  not by index): snapshot `{signature: set(selected_masks)}` before rebuild,
  re-apply by mask name after. Indices shift and membership changes when datasets
  regroup; the signature is the stable key.
- **Collapsible member list via a checkable `QToolButton`** (arrow indicator,
  `ToolButtonTextBesideIcon`) whose `toggled` signal drives a member-names
  `QLabel.setVisible` (direct slot connection — no reference-capturing lambda).
  Multi-member groups default collapsed; a **singleton** group shows its sole
  member's name inline in the header (a collapse toggle would save no space and
  hide the only identity). Cap **both** the shared list height and the expanded
  member-names label (scroll/elide past a threshold) so a 30-member group can't
  produce an unbounded wall of text; keep `wrap_in_scroll` intact.
- **Expand/collapse state is intentionally not preserved across a picker refresh**
  (R7 covers *selections* only). Rebuilding rows re-creates each `QToolButton` in
  its default state; re-expanding after a dataset add is accepted friction, not a
  bug — persisting it is out of scope.
- **Breakout via member sub-selection (U4).** The expanded member area is a
  selectable list (checkable member `display_name`s) plus a "Split selected into
  new group" button; splitting creates a sibling `_MaskGroup` (same signature,
  chosen members, its own list widget seeded from the parent's current selection),
  which carries a "Merge back" affordance. Breakout partitions are stored per
  signature keyed by member-`display_name` sets and reconciled on refresh (new
  same-signature datasets fall into the default/unsplit sub-group). The fan-out is
  untouched — it already iterates every `_MaskGroup`, so each sub-group emits its
  members with its own selection and every dataset still maps to exactly one entry.

---

## Open Questions

### Resolved During Planning

- Grouping rule → **identical available mask set** (exact-set). (User.)
- Per-dataset override within a group → **minimal breakout** (U4): members share
  one selection by default, but a subset can be split into an independent
  sub-group. (User; re-confirmed after the auto-group rationale was corrected —
  identical available masks do not imply identical desired selections.)
- Group display → **collapsible header: member count + expandable member names**.
  (User.)

### Deferred to Implementation

- Exact `QToolButton` arrow/label styling and whether the header also echoes the
  mask-set summary text (the shared list already shows the masks, so likely not) —
  a layout detail the implementer settles against the live dialog; directional
  mockup in U2.
- Whether the no-mask group's placeholder reads "No masks available" vs the
  current "No masks found" wording — cosmetic; keep it non-selectable either way.
- Confirm the CSV mask-union in `_try_build_config` (built from the selections
  dict) is order-independent — sort it or build first-seen — since the grouped
  fan-out emits dict keys in group-then-member rather than queue order. Verify no
  existing test asserts `existing_mask_selections` key order or CSV column order.
- Member-name label overflow threshold (when to scroll vs. elide) and whether
  member names sort alphabetically or stay in queue order for scanability.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review,
> not implementation specification. The implementing agent should treat it as
> context, not code to reproduce.*

Data flow — the only new structural piece is the grouping between the pending
datasets and the widgets; the emitted contract is unchanged:

```
_pending_datasets (queue, unique display_names)
        │  (display_name, sorted-mask-signature) per dataset
        ▼
group_by_mask_signature()            ← pure, Qt-free (U1)
        │  ordered list of {signature, [member display_names]}
        ▼
_refresh_mask_picker() builds one shared QListWidget per group   (U2)
        │  user selects masks once per group
        ▼
existing_mask_selections property  ── fans each group's selection ──▶
        {display_name: [masks]}  (one entry PER member, no empties)   (U2, contract == R5)
        │
        ▼
_try_build_config → WorkflowConfig(existing_mask_selections=…)  ← UNCHANGED
        │
        ▼
SingleCellThresholdingRunner._measure_round_specs_for(entry)   ← UNCHANGED
```

Fan-out sketch (directional):

```
def existing_mask_selections(self):
    out = {}
    for group in self._mask_groups:
        if not group.has_masks:
            continue
        chosen = [it.text() for it in group.list_widget selected items]
        if not chosen:
            continue                      # omit — never emit an empty list
        for pd in group.members:
            out[pd.display_name] = list(chosen)   # fan out to every member
    return out
```

Layout sketch (collapsed default; one row per group):

```
☑ Use existing masks (skip thresholding rounds)
   Datasets with the same available masks are grouped. Pick masks once per
   group; the Threshold Rounds section is hidden while this is on.

   ▸ 6 datasets
       [x] Dcp2_mask   [ ] P-body_mask   [x] P-body_mask2   [ ] adaptive
   ▸ 2 datasets
       [x] Dcp2_mask   [ ] P-body_mask
   ▸ 3 datasets — no masks available        (non-selectable)

   (expanding ▸ reveals the member dataset names above that group's list;
    selecting a subset there + "Split selected into new group" spins off a
    same-signature sibling group with its own selection — U4)
```

Breakout does not change the data flow above: a split adds a sibling `_MaskGroup`
with the same signature and a member subset, and the fan-out already iterates
every `_MaskGroup`, so each (sub-)group contributes one dict entry per member.

---

## Implementation Units

- U1. **Pure mask-signature grouping helper**

**Goal:** A Qt-free function that partitions datasets into ordered groups keyed by
their canonical available-mask signature, owning the canonicalization and ordering
invariants so the dialog stays a thin widget-builder.

**Requirements:** R1, R2, R6

**Dependencies:** None

**Files:**
- Create: `src/percell4/workflows/mask_grouping.py`
- Test: `tests/test_workflows/test_mask_grouping.py`

**Approach:**
- `group_by_mask_signature(items)` where `items` is an ordered iterable of
  `(name, available_masks)`; it canonicalizes each `available_masks` to
  `tuple(sorted(set(...)))` internally, then groups by signature.
- Return an ordered list of small records (`MaskGroupPlan(signature, member_names)`),
  group order = first appearance of the signature, member order = input order.
- The empty signature `()` is a valid group (no-mask datasets); the helper returns
  it **last** (after all non-empty groups), so the dialog can render it as a
  trailing non-selectable row (U2) without a second sort.
- Import **no** Qt symbols so the module and its test run in the local venv.

**Execution note:** Implement test-first — the invariants (canonical order-
insensitivity, first-appearance group order, member order) are the whole value of
extracting this.

**Patterns to follow:**
- The Qt-free workflows-core modules `src/percell4/workflows/models.py`,
  `channels.py`, and `csv_columns.py` (module-level pure functions + small
  `slots`/frozen dataclasses; no Qt imports).

**Test scenarios:**
- Happy path: three datasets with the same masks → one group, three members in
  input order.
- Happy path: two distinct signatures → two groups ordered by first appearance.
- Edge case: masks differing only in order (`[a, b]` vs `[b, a]`) map to the same
  signature (order-insensitive canonicalization).
- Edge case: duplicate mask names within one dataset's list collapse (set-canonical).
- Edge case: datasets with empty mask lists group together under signature `()`,
  separate from any non-empty group, and that group is returned **last** even when
  a no-mask dataset appears first in the input order.
- Edge case: empty input → empty list.
- Edge case: single dataset → one group with one member.

**Verification:**
- The helper's tests pass locally (Qt-free); grouping is deterministic and
  order-insensitive.

---

- U2. **Grouped mask picker UI + per-dataset fan-out**

**Goal:** Render the existing-mask section as grouped shared pickers (one
multi-select list + collapsible member header per group), replacing the
per-dataset `_mask_lists`, while emitting the identical per-dataset
`existing_mask_selections` contract.

**Requirements:** R1, R2, R3, R4, R5, R6, R7, R8

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: `tests/test_gui_workflows/test_config_dialog_existing_masks.py`

**Approach:**
- Introduce a `_MaskGroup` record (module-local) holding: `members:
  list[_PendingDataset]`, `signature: tuple[str, ...]`, `list_widget: QListWidget`,
  `has_masks: bool`, and the collapsible member widgets. Replace the
  `_mask_lists` field with `_mask_groups: list[_MaskGroup]`.
- `_refresh_mask_picker`:
  1. Snapshot prior selections keyed by **signature**: `{sig: set(selected mask
     names)}` from the current `_mask_groups`.
  2. Compute `[(pd, tuple(sorted(set(self._dataset_masks(pd))))) for pd in
     self._pending_datasets]`, call `group_by_mask_signature`, and map each
     returned group's member names back to their `_PendingDataset`s.
  3. Clear the `QFormLayout` rows (which deletes old child widgets and their
     connections), rebuild `_mask_groups`. For each group build: a `QToolButton`
     (checkable, arrow) + a wrapped, height-capped member-names `QLabel` (hidden by
     default for multi-member groups, shown via
     `toolbtn.toggled.connect(label.setVisible)`; for a singleton group put the
     member name in the header and skip the toggle), and a single shared
     `QListWidget` (`MultiSelection`, capped height) populated with the signature's
     masks. Wire `list_widget.itemSelectionChanged → self._update_start_enabled`
     at construction. Re-apply the snapshot by mask name.
  4. No-mask group (`has_masks=False`, signature `()`): render it **last** with a
     header ("N datasets — no masks available") and a single non-selectable
     placeholder item; do not wire selection.
  5. All-no-mask case (every pending dataset lacks masks → the no-mask group is the
     only group): instead of a bare placeholder, show an inline explanatory line
     ("No masks available in the loaded datasets — add datasets that already
     contain `/masks`, or use Threshold Rounds instead"). Start stays gated by the
     require-a-selection rule, but the user is told why the section is empty.
- Rewrite the `existing_mask_selections` property to fan out (see Technical
  Design): iterate `_mask_groups`, skip `has_masks=False` and empty selections,
  and emit one `{display_name: list(chosen)}` entry per member. Keys stay equal to
  the disambiguated `display_name`s; never emit an empty list. Keep `_mask_groups`
  and the fan-out agnostic to *how* groups are formed, so U4's breakout can add
  same-signature sibling groups without touching this property.
- Leave `_on_mask_reuse_toggled`, `_update_start_enabled`, and `_try_build_config`
  untouched (they consume the property, which is contract-stable). Keep the outer
  `wrap_in_scroll`.
- Follow the rebuild-safety learning: connect the shared list to the bound method
  `self._update_start_enabled` (no lambda), and the toggle to the label's
  `setVisible` slot directly.

**Technical design:** *(directional)* one `QFormLayout` row per group whose field
widget is a small container `QWidget` stacking `[QToolButton header] [member-names
QLabel, hidden] [shared QListWidget]`; the label field left empty so the group
spans the row. Header text: the member's `display_name` for a singleton group,
else `f"{n} datasets"` (proper plural); the no-mask group appends
`" — no masks available"`.

**Patterns to follow:**
- The mirror structure of `_refresh_segmentation_picker` / `segmentation_overrides`
  (per-dataset → per-group is the only change).
- The `has_masks`-tracked-explicitly invariant (checkable `QGroupBox` disables all
  children).
- `qt-wire-user-edit-signals`; `dialog-scroll-when-tall`; the rebuild-safety and
  shared-widget learnings.

**Test scenarios:**
- Happy path (R1): two datasets added with the same mask set → exactly one
  selectable group; `_mask_groups` has one entry with two members.
- Happy path / fan-out (R5): selecting two masks in a shared group yields
  `existing_mask_selections == {ds1: [a, b], ds2: [a, b]}` — one entry per member,
  identical lists, keyed by `display_name`.
- Happy path (R2): two datasets with *different* mask sets → two groups; selecting
  in one does not change the other's selection.
- Edge case (R6): a dataset with no `/masks` groups into the non-selectable
  no-mask group; it never appears in `existing_mask_selections`; its placeholder
  item is not selectable.
- Edge case (R7, refresh): select masks in a group, add a third dataset with the
  same signature (`_add_h5_paths`), assert the group now has three members and the
  selection is preserved for all three.
- Edge case (R7, toggle): the non-destructive on/off toggle of the outer group
  preserves the shared selection (port `test_mask_selections_preserved_across_toggle`).
- Signal path (R8): drive `group.list_widget.item(i).setSelected(True)` (which
  fires `itemSelectionChanged`) and assert both `existing_mask_selections` reflects
  it for every member *and* `_start_btn.isEnabled()` becomes True — proving the
  wire, not just state.
- Interaction (R3): toggling a group's `QToolButton` shows/hides the member-names
  label (`label.isVisibleTo(dialog)` flips).
- Regression: `_try_build_config()` with a grouped selection produces a
  `WorkflowConfig` whose `existing_mask_selections` has one key per member and
  passes `__post_init__`; `_rounds_group_box.isVisibleTo(dialog)` is False while
  mask-reuse is on; requiring-a-selection still gates Start.
- Edge case (R6, all-no-mask): with every pending dataset lacking masks, the
  section shows the inline "no masks available" explanation, `existing_mask_selections`
  is empty, and Start stays disabled while mask-reuse is on.
- Edge case (rendering): the no-mask group renders after all selectable groups
  even when a no-mask dataset was added first.
- Edge case (R3, singleton): a group with one member shows that member's name in
  the header (no bare "1 dataset" count).
- Migration: update **all six** existing tests that destructure `_mask_lists` as
  `(pd, lw, has)` to the new `_mask_groups` shape (map each member `display_name`
  to its group's shared `list_widget`): `test_mask_picker_lists_masks_and_handles_empty`,
  `test_existing_mask_selections_property`, `test_build_config_existing_masks`,
  `test_start_enabled_in_mask_reuse_mode_without_rounds`,
  `test_mask_can_be_unselected_with_a_click`, `test_mask_selections_preserved_across_toggle`.

**Verification:**
- The full `tests/test_gui_workflows/test_config_dialog_existing_masks.py` suite
  passes on CI; `tests/test_gui_workflows/test_single_cell_runner.py` and
  `tests/test_workflows/test_models.py` pass **unchanged** (proving the contract
  held); the dialog-helper compliance test still passes.
- Manually (or on CI screenshot): 8 identical-mask datasets show one group;
  selecting masks once drives the whole run.

---

- U4. **Minimal per-group breakout (split members into an independent sub-group)**

**Goal:** Let the user split a subset of an auto-group's members into a new
same-signature sub-group with its own mask selection, so datasets that share
identical available masks but belong to different conditions can be measured
differently.

**Requirements:** R4, R5, R7, R8

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: `tests/test_gui_workflows/test_config_dialog_existing_masks.py`

**Approach:**
- Make each multi-member group's expanded member area a selectable list (checkable
  member `display_name`s) with a "Split selected into new group" button; a split
  sub-group shows a "Merge back" button that returns its members to the base group.
- Represent a sub-group as another `_MaskGroup` with the same signature, an
  explicit member subset, and its own list widget seeded from the parent group's
  current selection at split time.
- Maintain a breakout partition per signature — `{signature: [set(display_names),
  …]}`, default one set = all members — stored on the dialog. Reconcile inside
  `_refresh_mask_picker`: assign new same-signature datasets to the default
  (unsplit) sub-group, drop removed datasets, prune empty sub-groups. Persist
  across refresh keyed by member-name sets, alongside the per-sub-group selection
  snapshot (extend U2's signature-keyed snapshot to a (signature, member-set) key).
- Fan-out (`existing_mask_selections`) is unchanged — it already iterates every
  `_MaskGroup`; each sub-group emits its members with its own selection, so every
  dataset still maps to exactly one selection and the R5 contract holds.
- Wire the split/merge buttons and the member-selection list per
  `qt-wire-user-edit-signals`; apply rebuild-safety (no reference-capturing lambdas
  across refresh).

**Execution note:** Land after U2 so the base grouping (the main win) ships first;
breakout layers on without touching the fan-out.

**Patterns to follow:**
- The `_MaskGroup` construction/accessor site from U2 (one place builds a group's
  widgets).
- `MultiSelection` `QListWidget` semantics (one-click toggle) already used for the
  mask lists.

**Test scenarios:**
- Happy path (R4): a 3-member group; split 1 member out → two groups (2 + 1
  members), each with independent selections; fan-out emits 2 datasets with the
  group's selection and 1 with the split selection.
- Happy path (R5): after a split, `existing_mask_selections` still has exactly one
  entry per dataset (three total), no empty-list values, keys == `display_name`s;
  `_try_build_config` builds a valid `WorkflowConfig`.
- Merge-back: merging a split sub-group returns its members to the base group; the
  picker collapses to one group; fan-out reflects the base selection for all.
- Edge case (R7, refresh): split a group, then add a new same-signature dataset →
  it joins the default (unsplit) sub-group, not the split one; both sub-groups'
  selections are preserved.
- Edge case: splitting all-but-one, or splitting every member individually, still
  yields one entry per dataset and never an empty selection value.
- Signal path (R8): the "Split selected" button and the member-selection list are
  wired; a split driven through the signal updates `_mask_groups` and
  `existing_mask_selections`.

**Verification:**
- A mixed-condition run (all datasets share masks; two conditions split into two
  sub-groups) measures each condition's chosen mask on its own datasets; the suite
  passes on CI.

---

- U3. **Documentation update**

**Goal:** Keep the module docs describing what *is* — a grouped mask picker, not a
per-dataset one.

**Requirements:** R1, R3, R4

**Dependencies:** U2, U4

**Files:**
- Modify: `src/percell4/gui/workflows/CLAUDE.md`

**Approach:**
- Update the `config_dialog.py` description (the sentence: "The checkable 'Use
  existing masks (skip thresholding rounds)' group adds a **per-dataset**
  multi-select mask picker (mirrors the segmentation-override combos)…") to state
  that datasets sharing an identical available-mask set are grouped into a single
  shared picker with a collapsible member list, that a subset of a group can be
  split into its own sub-group with an independent selection, and that the emitted
  `existing_mask_selections` per-dataset contract is unchanged. Describe current
  state only — no history, per the repo's documentation rules.

**Patterns to follow:**
- The existing terse, present-tense style of `src/percell4/gui/workflows/CLAUDE.md`.

**Test scenarios:**
- Test expectation: none — documentation-only change.

**Verification:**
- The CLAUDE.md paragraph matches the shipped UI; no lingering "per-dataset" claim
  for the mask picker.

---

## System-Wide Impact

- **Interaction graph:** `_refresh_dataset_tree → _refresh_mask_picker` (rebuild);
  `existing_mask_selections` is read by `_update_start_enabled` and
  `_try_build_config`. Only the picker internals and the property change; both
  readers are contract-stable.
- **Error propagation:** unchanged — `_try_build_config` still filters selections
  to channel-intersection-kept datasets, warns on empty, and catches
  `WorkflowConfig.__post_init__` `ValueError`.
- **State lifecycle risks:** selection loss on refresh is the main hazard;
  mitigated by signature-keyed snapshot/restore (R7) with an explicit test. Stale
  signal connections on rebuild avoided by clearing form rows (widget deletion)
  and using bound-method/direct-slot connections (no reference-capturing lambdas).
- **API surface parity:** the internal per-dataset `existing_mask_selections`
  contract is the parity surface — same shape, keys, and values, so `runner.py`,
  `models.py`, and their tests need no change (dict key order shifts to
  group-then-member, but both consumers are order-insensitive).
- **Unchanged invariants:** `WorkflowConfig` / `WorkflowDatasetEntry`,
  `_measure_round_specs_for`, the Segmentation Selection picker, the Threshold
  Rounds table, and the CSV-column union computation all stay as-is.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Existing GUI tests destructure `_mask_lists`; renaming to `_mask_groups` breaks them | Update **all six** destructuring tests in lockstep within U2 (enumerated in U2 test scenarios); keep the property contract so runner/model tests stay untouched (proof the contract held) |
| Selection silently lost when datasets are added/removed or regrouped | Snapshot/restore keyed by mask **signature** (not index/display_name); dedicated preserve-across-refresh and preserve-across-toggle tests (R7) |
| A wired signal that tests bypass with programmatic setters | Add a signal-path test that drives `setSelected` and asserts both fan-out and Start-enable (R8) |
| New collapsible micro-pattern has no in-repo precedent | Keep it minimal (checkable `QToolButton` → `QLabel.setVisible`), self-contained per group; default collapsed; covered by an interaction test |
| Local GUI test segfault (mixed-Qt venv) | Per project memory, GUI tests are CI-gated; the Qt-free U1 helper carries the locally-runnable logic |
| Tall shared list inside an already-scrolled form | Cap the shared `QListWidget` height and keep the outer `wrap_in_scroll`; the dialog-helper compliance test guards the scroll wrapper |
| Breakout (U4) adds partition state that must survive a picker refresh (R7) | Store the partition per signature keyed by member-name sets; reconcile on rebuild (new same-signature datasets → default/unsplit sub-group, prune empties); test split-then-add and merge-back |
| Auto-group forces one selection on datasets with identical available masks but different desired masks (mixed-condition) — silent wrong measurement | Resolved by U4 breakout: split the divergent members into their own sub-group. Until split, the visible member list surfaces exactly which datasets share a selection |

---

## Documentation / Operational Notes

- After landing, consider a `/ce-compound` capture for two learnings the research
  surfaced as undocumented: (a) preserving Qt selections across widget rebuilds by
  a stable domain key, and (b) the headless-Qt-testing posture for this repo.
- No migration, rollout, or data concerns — this is a pre-run config dialog with
  dialog-local state only; it never mutates the live session or on-disk data.

---

## Sources & References

- User request + provided screenshot (single-cell thresholding workflow dialog).
- Code: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  (`_build_mask_selection_group`, `_refresh_mask_picker`, `existing_mask_selections`,
  `_add_pending`, `_try_build_config`, `_update_start_enabled`),
  `src/percell4/gui/workflows/single_cell/runner.py` (`_measure_round_specs_for`),
  `src/percell4/workflows/models.py` (`WorkflowConfig`, `WorkflowDatasetEntry`),
  `src/percell4/store.py` (`list_masks`).
- Tests: `tests/test_gui_workflows/test_config_dialog_existing_masks.py`,
  `tests/test_gui_workflows/test_single_cell_runner.py`,
  `tests/test_workflows/test_models.py`,
  `tests/test_gui/test_config_dialog_segmentation_picker.py`,
  `tests/test_gui/test_dialog_helper_compliance.py`.
- Learnings: `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`,
  `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`,
  `docs/solutions/conventions/gui-panel-and-batch-workflow-method-parity-2026-07-13.md`,
  `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`,
  `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`,
  `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md`.
