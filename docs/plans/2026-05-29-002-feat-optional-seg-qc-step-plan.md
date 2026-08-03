---
title: "feat: Optional segmentation-QC step for pre-segmented datasets in the single-cell workflow"
type: feat
status: completed
date: 2026-05-29
---

> **Implementation note (2026-05-29):** Shipped on branch
> `feat/optional-seg-qc-step` in 3 commits (U1 config field + serialization,
> U3 runner gating, U2 dialog checkbox). Two deviations from the plan as
> written, both validated by the feasibility review:
> 1. **No `_resuming` guard.** The repo has no resume-from-disk code path
>    (`config_from_dict` has only test callers), so the planned resume guard
>    referenced a constructor param that does not exist. It was dropped; the
>    runner reads `self._config.run_seg_qc_on_existing` directly. The
>    "Resume behavior" question is therefore moot.
> 2. **Fixed a pre-existing failing test.** `test_interactive_runner.py`'s
>    fixtures are pre-segmented with `cellpose_qc` labels, so the U13 auto-skip
>    had been silently skipping their seg-QC, leaving
>    `test_interactive_runner_yields_seg_qc_and_threshold_qc_requests` failing
>    on `main`. The default-True flag restores seg-QC for those datasets,
>    flipping that test green for the right reason. The two
>    `test_runner_autoskip_segmentation.py` tests that encoded the old
>    "pre-segmented always skips seg-QC" behavior were updated.
> Implemented in the `_collect_phases` generator-drain harness in
> `tests/test_gui_workflows/test_runner_autoskip_segmentation.py` (lighter than
> the end-to-end qtbot runner). Full workflows + gui_workflows suite: 148 passed.

# feat: Optional segmentation-QC step for pre-segmented datasets

## Overview

Add a checkbox to the single-cell thresholding workflow's config dialog —
**"Run segmentation QC on already-segmented datasets"** — that lets the user run
the existing interactive segmentation-QC step on datasets that arrive already
segmented (e.g. by the `percell4-batch` CLI). When checked (the default), each
pre-segmented dataset opens its selected segmentation layer in the seg-QC editor
before group thresholding. When unchecked, pre-segmented datasets proceed
straight to group thresholding — today's behavior.

The change is a single boolean threaded `dialog → WorkflowConfig → runner`, plus
a small restructure of the runner's existing "auto-skip Cellpose + seg-QC for
pre-segmented datasets" branch so it can optionally yield a seg-QC phase instead
of skipping unconditionally. No new QC controller, no change to the seg-QC editor
itself, no change to fresh-Cellpose datasets.

---

## Problem Frame

`percell4-batch` (`src/percell4/interfaces/cli/batch_process.py`) segments
datasets headlessly and writes a `/labels/<seg_name>` layer into each `.h5`. When
those pre-segmented `.h5` files are then fed into the single-cell thresholding
workflow, the runner **auto-detects the existing segmentation and skips both
Cellpose and the interactive seg-QC step** (`runner.py:259-269` —
`if existing is not None: self._effective_seg[entry.name] = existing; continue`).
The batch-produced labels therefore go straight into group thresholding
un-reviewed, with no in-workflow opportunity to delete spurious cells, redraw
misses, or recover an empty field.

Datasets segmented *inside* the workflow by Cellpose already get an interactive
seg-QC pass (`runner.py:290-309`, gated by `interactive_qc`). The gap is only the
pre-segmented path. This plan closes it with an opt-out toggle: QC the existing
segmentation by default; skip straight to thresholding when the user trusts it.

This is a direct feature request (no upstream `ce-brainstorm` doc). Requirements
below are derived from the request and the two planning decisions confirmed with
the user.

---

## Requirements Trace

- R1. The config dialog gains a single checkbox, **"Run segmentation QC on
  already-segmented datasets"**, defaulting to **checked**.
- R2. When the box is checked, every dataset that uses a **pre-existing**
  segmentation — whether auto-detected (`_detect_existing_segmentation`) or
  explicitly chosen via the Segmentation Selection combos
  (`segmentation_overrides`) — runs the interactive seg-QC step on that selected
  layer before its group-thresholding rounds.
- R3. When the box is unchecked, pre-segmented datasets skip seg-QC and proceed
  straight to group thresholding (exactly today's behavior).
- R4. Fresh-Cellpose datasets (no pre-existing segmentation) are **unaffected** —
  they continue to run seg-QC under the existing `interactive_qc` gate, regardless
  of the new checkbox.
- R5. The flag is a field on `WorkflowConfig`, serialized into `run_config.json`
  and round-tripped. An old `run_config.json` that predates the field loads with
  the default (checked / `True`).
- R6. The flag is a pure phase-gating decision: it must not mutate any of the five
  session selection fields, must not alter the measurement-persistence invariant
  (the runner keeps constructing QC controllers with
  `write_measurements_to_store=False`), and is a no-op in headless mode
  (`interactive_qc=False`), where no interactive phase is yielded.
- R7. Naming is precise: the control, the config field, and the label all say
  "segmentation QC" and never blur into the grouped-thresholding step they gate
  (per the seg-vs-threshold terminology split).

---

## Scope Boundaries

- **Not a master seg-QC toggle.** The checkbox governs only the pre-segmented
  (auto-skip) path. It does not gate fresh-Cellpose seg-QC, and it is not a way to
  turn off all interactive QC (that is the runner's separate `interactive_qc`
  switch, which stays untouched).
- **No change to the seg-QC editor.** `SegmentationQCController` and its
  read/edit/accept/write-back contract (`seg_qc.py`) are reused as-is. This plan
  only changes *whether* it is invoked for pre-segmented datasets.
- **No change to group thresholding, measurement, dilute, or export.**
- **No new "accepted" provenance marker.** Seg-QC's accept already overwrites
  `/labels/<seg_name>` in place; that existing behavior is the persistence model.
  Running QC on a batch-produced layer overwrites it with the reviewed version —
  the intended outcome.
- **No per-dataset QC toggle.** One run-wide checkbox, not a per-dataset choice in
  the Segmentation Selection table.
- **Non-2D pre-segmented layers skip seg-QC.** `SegmentationQCController` rejects
  any labels with `ndim != 2` (seg_qc.py:231). The yield is therefore gated on the
  chosen layer being **2D**, established by a single `store.labels_shape(existing)`
  call (which also serves as the existence check). A `(T, H, W)` stack is skipped;
  a **2D whole-field gate on a time-lapse dataset** (e.g. from
  `percell4-batch-whole-field`) is still QC-able and runs. *(Implementation
  refinement from the adversarial review: gate on label rank, not `n_timepoints` —
  more precise and matches the editor's actual constraint.)*

---

## Context & Research

### Relevant Code and Patterns

- **Config dataclass:** `src/percell4/workflows/models.py` — `WorkflowConfig`
  (frozen, lines 268-336). Existing fields include `edge_mode`,
  `dilute_settings`, `cellpose_segmentation_name`, `particle_settings`. The new
  `run_seg_qc_on_existing: bool = True` follows the shape of the existing
  `CellposeSettings.gpu` bool (a plain toggle, not an `Optional` settings block).
  No `__post_init__` validation is needed for a free boolean.
- **Serialization:** `src/percell4/workflows/artifacts.py` — `config_to_dict` /
  `config_from_dict` (~lines 201-224). `_from_dict` must default the new key to
  `True` when absent (Resume back-compat), mirroring how `edge_mode` /
  `dilute_settings` defaults were handled.
- **Config dialog:** `src/percell4/gui/workflows/single_cell/config_dialog.py` —
  `WorkflowConfigDialog`. The **Segmentation Selection group**
  (`_build_segmentation_group`, lines 565-585) is the natural home: its note
  already says "Datasets that already have a segmentation skip Cellpose and start
  at thresholding." The new checkbox extends exactly that statement. The literal
  `QCheckBox` pattern to mirror is `self._cp_gpu` (lines 448-450), read pull-style
  at build time. Config assembly is `_try_build_config`
  (lines 1696-1810); the `WorkflowConfig(...)` constructor call is at lines
  1794-1807 — add the new keyword there, mirroring `gpu=self._cp_gpu.isChecked()`
  (line 1740). The dialog is already wrapped in `wrap_in_scroll` (line 355).
- **Runner gating:** `src/percell4/gui/workflows/single_cell/runner.py` —
  `_phase_generator` (lines 226-435). The Phase-1/Phase-2 block (lines 257-309)
  is the only edit site. Today:
  - Pre-segmented datasets: `existing is not None` → set `_effective_seg` →
    `continue` (lines 264-269), skipping both segment and seg-QC.
  - Fresh datasets: yield segment (lines 271-288), then yield seg-QC if
    `interactive_qc` and segment didn't fail (lines 294-309).
  The seg-QC handler `_make_seg_qc_handler(entry, idx, len(active))`
  (lines 677-730) loads `self._seg_name_for(entry)` — which, for a pre-segmented
  dataset, is the chosen existing layer — so reusing it "just works" on the
  selected layer. `cfg` is bound at line 229, so the runner reads
  `cfg.run_seg_qc_on_existing` directly; **no new runner constructor kwarg is
  required** (unlike `interactive_qc` / `segmentation_overrides`, which are
  launcher-supplied).
- **Seg-QC editor (unchanged):** `src/percell4/gui/workflows/single_cell/seg_qc.py`
  — `SegmentationQCController`. Reads `/labels/<seg_name>` (line 229), writes the
  edited array back to the same `/labels/<seg_name>` on Accept (line 1293).
  Cancel writes nothing and unwinds the run (the existing `_wrapped_complete`
  treats a cancel message as a runner-level cancel, `runner.py:698-704`).
- **percell4-batch source of pre-segmented data:**
  `src/percell4/interfaces/cli/batch_process.py` — writes `/labels/<seg_name>`
  (default `cellpose_<n_cells>`, `--seg-name`). These are the `.h5` files the
  checkbox's QC runs on.

### Institutional Learnings (`ce-learnings-researcher`)

- **`qt-wire-user-edit-signals-2026-05-12.md`** (high) — a checkbox that changes
  downstream behavior is the "looks correct, passes a programmatic test, no-ops at
  runtime" trap. Here the checkbox is **read pull-style** at Start (like
  `_cp_gpu`), not used to drive live UI enable-state, so a `toggled` wire is not
  strictly required — but the test must exercise the value via
  `setChecked(...)` + `_try_build_config()` and assert the resulting
  `WorkflowConfig.run_seg_qc_on_existing`, not just inspect a widget. If the
  checkbox is later made to enable/disable other widgets, wire `toggled` at
  construction.
- **`gui-action-contract-exhaustiveness.md`** (pre_canonical) — the checkbox is
  config-value capture, an **Action**, not a Selector/Creator. It must not write
  any of the five session fields (`active_channel`, `active_segmentation`,
  `active_mask`, `filter_ids`, `selection`). Skipping/inserting seg-QC must not
  clear `active_segmentation` as a side effect.
- **`creator-contract-four-step-sequence-2026-05-18.md`** (canonical) — seg-QC's
  accept path is a Creator (it writes a labels resource). This plan does **not**
  change that path; it only changes whether the phase is yielded. No new write
  path is introduced, so the four-step contract is neither extended nor at risk
  here. (Verify the edit adds no off-label session write.)
- **`dialog-scroll-when-tall.md`** (canonical_clean) — keep the new checkbox
  inside the existing `wrap_in_scroll(content)` wrapper;
  `tests/test_gui/test_dialog_helper_compliance.py` AST-checks every
  `gui/**/*Dialog.py`.
- **`threshold-qc-measurements-write-owned-by-controller.md`** (tech-debt) — the
  runner's invariant: per-dataset `.h5` files hold images/labels/masks only; the
  measurement DataFrame lives only in the run folder. The skip/insert branch must
  not cause the runner to start writing measurements into `.h5` (it won't — seg-QC
  writes labels, not measurements).
- **`grouped-thresholding-development-lessons.md`** (item 7) — "segmentation"
  (instance labeling) and "thresholding" (binary intensity) are distinct domain
  terms; name the field/label precisely (R7).

### External References

- None. Entirely in-codebase, well-patterned change.

---

## Key Technical Decisions

- **Model as a plain `WorkflowConfig.run_seg_qc_on_existing: bool = True`**, not an
  `Optional` settings block — it is a single toggle with no associated parameters.
  Mirrors the existing `CellposeSettings.gpu` bool precedent.
- **Default `True` (checked).** The feature's reason for existing is that
  batch-segmented data "still needs a QC step," so QC-by-default matches intent; a
  user who trusts the segmentation unchecks it. Confirmed with the user. Serializer
  default is also `True` so the dataclass default and `config_from_dict` default
  agree.
- **Read from config in the runner, not via a new constructor kwarg.** `cfg` is
  already bound in `_phase_generator`; `interactive_qc` and
  `segmentation_overrides` are launcher-supplied for reasons that don't apply to a
  recipe-level toggle. Keeping it on the frozen `WorkflowConfig` means it is
  captured at Start and recorded in `run_config.json` for free (R5).
- **Restructure the `continue`, don't duplicate the yield.** Replace the
  unconditional `continue` at `runner.py:269` with: set `_effective_seg`; if
  `interactive_qc and cfg.run_seg_qc_on_existing`, yield a seg-QC `PhaseRequest`
  for the existing layer; then `continue` (skip the Cellpose segment yield
  regardless). Fresh-Cellpose datasets fall through to the unchanged
  segment-then-seg-QC path. This keeps the fresh path byte-for-byte unchanged
  (R4).
- **Reuse `_make_seg_qc_handler` unchanged.** It already resolves the layer via
  `_seg_name_for(entry)`, which returns the pre-existing layer for these datasets,
  so QC operates on the selected segmentation with no handler change.
- **Two guards before yielding pre-segmented seg-QC** (both fall through to today's
  skip-to-thresholding behavior when they fail, never crashing the run):
  1. `not self._is_timelapse(entry)` — the single-frame editor can't edit a
     `(T, H, W)` stack (BLOCKING fix from review). The runner already has
     `_is_timelapse` (used at `runner.py:982`).
  2. The resolved layer actually exists: `seg_name in DatasetStore(entry.h5_path)
     .list_labels()`. A stale `segmentation_overrides` entry naming a renamed/missing
     layer would otherwise hand a bad name to `SegmentationQCController.read_labels`,
     which raises inside `start()` outside the runner's per-phase failure-recording
     path. When the layer is missing, skip seg-QC; group thresholding then records
     the missing-layer failure exactly as it does today.
- **Checkbox lives in the Segmentation Selection group**, not Cellpose settings —
  it is conceptually "what to do with already-segmented datasets," which is that
  group's exact subject. Update the group's note to mention it.
- **Headless no-op.** The seg-QC yield stays gated by `interactive_qc`, so
  `interactive_qc=False` runs (tests, unattended) never yield seg-QC regardless of
  the flag. The flag never reuses or flips `interactive_qc` (R6).

---

## Open Questions

### Resolved During Planning

- *Scope — master toggle vs pre-segmented only?* → **Pre-segmented only.**
  Fresh-Cellpose seg-QC is unchanged.
- *Default state?* → **Checked** (`True`), including for old `run_config.json`
  files lacking the field.
- *Where does the checkbox live?* → The **Segmentation Selection** group.
- *Does the runner need a new constructor kwarg?* → **No.** Read
  `cfg.run_seg_qc_on_existing` in `_phase_generator`.
- *Does skipping seg-QC need a fallback artifact for thresholding?* → **No.**
  Group thresholding reads `/labels/<seg_name>`, which already exists from the
  segment phase / batch CLI; seg-QC only refines it in place.

### Deferred to Implementation

- Exact field name string (`run_seg_qc_on_existing` is the plan's choice; confirm
  no collision in `config_to_dict`/`config_from_dict`).
- Whether to assert the generator phase sequence by iterating `_phase_generator()`
  directly vs driving the full interactive runner with the existing
  `_FakeSegQCController`. Pick whichever is the cleaner test at implementation
  time (both are viable; see U3 test scenarios).
- Whether the Segmentation Selection group note copy needs design review — adjust
  wording at implementation time.

---

## Implementation Units

- U1. **`WorkflowConfig.run_seg_qc_on_existing` + serialization back-compat**

**Goal:** Add the boolean field to the frozen `WorkflowConfig` (default `True`) and
round-trip it through `run_config.json` with an absent-key default of `True`.

**Requirements:** R5, R7

**Dependencies:** None

**Files:**
- Modify: `src/percell4/workflows/models.py` (add `run_seg_qc_on_existing: bool = True`
  to `WorkflowConfig`; docstring line noting it gates seg-QC for pre-segmented
  datasets only)
- Modify: `src/percell4/workflows/artifacts.py` (`config_to_dict` writes the key;
  `config_from_dict` reads it with `.get("run_seg_qc_on_existing", True)`)
- Test: `tests/test_workflows/test_models.py`
- Test: `tests/test_workflows/test_artifacts.py`

**Approach:**
- Place the field next to other top-level workflow toggles; no `__post_init__`
  validation needed.
- In `config_to_dict`, add the key unconditionally. In `config_from_dict`, default
  to `True` when the key is absent so old run folders Resume unchanged in shape and
  pick up the checked default.

**Execution note:** Characterization-first — add the "old `run_config.json` lacks
the key → loads as `True`" round-trip test before writing the serializer change.

**Patterns to follow:**
- `CellposeSettings.gpu` (existing bool field).
- The `edge_mode` / `dilute_settings` serialization + absent-key default pattern
  already in `artifacts.py`.

**Test scenarios:**
- Happy path: `WorkflowConfig(..., run_seg_qc_on_existing=False)` constructs;
  field reads back `False`.
- Happy path: `WorkflowConfig` built without specifying the field defaults to
  `True`.
- Edge case: `True` and `False` each round-trip through
  `config_to_dict` → `config_from_dict` unchanged.
- Integration (R5): a config dict with **no** `run_seg_qc_on_existing` key passes
  through `config_from_dict` → resulting `WorkflowConfig.run_seg_qc_on_existing is
  True`, no exception. Re-serializing via `config_to_dict` now includes the key.

**Verification:**
- `pytest tests/test_workflows/test_models.py tests/test_workflows/test_artifacts.py -q`
  passes; an old (pre-field) `run_config.json` fixture loads with the field `True`.

---

- U2. **Config dialog: "Run segmentation QC on already-segmented datasets" checkbox**

**Goal:** Add the checkbox to the Segmentation Selection group (default checked),
read it in `_try_build_config`, and pass `run_seg_qc_on_existing` into the
`WorkflowConfig` constructor.

**Requirements:** R1, R7

**Dependencies:** U1 (the field must exist on `WorkflowConfig`)

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: `tests/test_gui_workflows/test_config_dialog.py`

**Approach:**
- In `_build_segmentation_group` (lines 565-585), add
  `self._run_seg_qc = QCheckBox("Run segmentation QC on already-segmented datasets")`,
  `setChecked(True)`, with a tooltip explaining: checked → review/edit each
  pre-segmented dataset's selected layer before thresholding; unchecked → use it
  as-is. Add it inside the group's layout (which is inside the scroll wrapper).
  Extend the group's existing note so the skip-vs-QC behavior is discoverable.
- In `_try_build_config` (constructor call at lines 1794-1807), add
  `run_seg_qc_on_existing=self._run_seg_qc.isChecked()`, mirroring
  `gpu=self._cp_gpu.isChecked()`.
- Read pull-style at Start (no live `toggled` wiring needed since it drives no
  other widget's enabled-state). If a future change makes it enable/disable other
  controls, wire `toggled` at construction then.

**Patterns to follow:**
- `self._cp_gpu` literal `QCheckBox` (build + pull-style read).
- The checkable-group reads in `_try_build_config` for particles/dilute (for how
  optional config is assembled).

**Test scenarios:**
- Happy path: dialog opens with the checkbox checked;
  `_try_build_config()` (with a minimal valid dataset/round set) yields a
  `WorkflowConfig` with `run_seg_qc_on_existing is True`.
- Happy path / value path (Qt learning): `self._run_seg_qc.setChecked(False)` then
  `_try_build_config()` → `WorkflowConfig.run_seg_qc_on_existing is False`. Assert
  via the built config, not by inspecting the widget alone.
- Edge case: the field is present in the built config even when no dataset is
  pre-segmented (the flag is inert but always captured).
- Integration: the file still passes
  `tests/test_gui/test_dialog_helper_compliance.py` (checkbox is inside the
  `wrap_in_scroll` content).

**Verification:**
- `pytest tests/test_gui_workflows/test_config_dialog.py tests/test_gui/test_dialog_helper_compliance.py -q`
  passes; opening the dialog shows the checkbox in the Segmentation Selection
  group, checked by default.

---

- U3. **Runner: optionally run seg-QC on pre-segmented datasets**

**Goal:** Restructure the runner's pre-segmented auto-skip branch so that, when
`interactive_qc` and `cfg.run_seg_qc_on_existing` are both true, it yields a
seg-QC `PhaseRequest` for the existing layer before continuing; otherwise it skips
to thresholding as today. Fresh-Cellpose datasets are untouched.

**Requirements:** R2, R3, R4, R6

**Dependencies:** U1 (reads `cfg.run_seg_qc_on_existing`)

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (the Phase-1/Phase-2
  block, lines 257-309)
- Test: `tests/test_gui_workflows/test_interactive_runner.py`
- Test (no-op guard): `tests/test_gui_workflows/test_single_cell_runner.py`

**Approach:**
- In the `existing is not None` branch (lines 264-269): after
  `self._effective_seg[entry.name] = existing`, yield an INTERACTIVE seg-QC
  `PhaseRequest` **only when all of**: `self._interactive_qc`,
  `cfg.run_seg_qc_on_existing`, `not self._is_timelapse(entry)`, and the resolved
  layer is present in `DatasetStore(entry.h5_path).list_labels()`. Then `continue`.
  When any guard fails (flag off, headless, time-lapse, or missing layer),
  `continue` immediately as today.
- The seg-QC `PhaseRequest` mirrors the fresh-path one:
  `phase_name="seg_qc", dataset_index=idx, dataset_total=len(active),
  dataset_name=entry.name, handler=self._make_seg_qc_handler(entry, idx, len(active))`.
- Leave the fresh-Cellpose segment + seg-QC path (lines 271-309) exactly as-is.
- Reuse `_make_seg_qc_handler` unchanged — it resolves `_seg_name_for(entry)` to
  the pre-existing layer.
- Add no session-field writes; the branch only yields a phase. Confirm via a grep
  that no `session.set_active_*` / filter / selection write is introduced.

**Technical design:** *(directional — not implementation spec)*

```
for idx, entry in enumerate(active):
    existing = self._effective_seg.get(entry.name) or self._detect_existing_segmentation(entry)
    if existing is not None:
        self._effective_seg[entry.name] = existing
        if (self._interactive_qc
                and cfg.run_seg_qc_on_existing
                and not self._is_timelapse(entry)          # single-frame editor only
                and existing in DatasetStore(entry.h5_path).list_labels()):  # layer present
            yield PhaseRequest(INTERACTIVE, "seg_qc", idx, len(active), entry.name,
                               handler=self._make_seg_qc_handler(entry, idx, len(active)))
        continue                      # skip Cellpose segment regardless
    # ... unchanged: yield segment, then (if interactive_qc) yield seg_qc ...
```

The four-way `and` is the whole behavioral change; the fresh-Cellpose `else` path
is byte-for-byte unchanged.

**Patterns to follow:**
- The existing fresh-path seg-QC yield (lines 302-309) — same `PhaseRequest`
  shape and handler factory.
- The dilute phase's `if cfg.<flag> and self._interactive_qc:` gating
  (lines 387-393) as the precedent for config-flag-gated phase yields.

**Test scenarios:**
- Happy path (R2): an interactive runner over a dataset with a pre-existing
  segmentation (seeded via `segmentation_overrides` or an h5 fixture carrying
  `/labels/<name>`), `run_seg_qc_on_existing=True` → a `seg_qc` phase is yielded
  for that dataset (assert the `_FakeSegQCController` was instantiated for it, or
  that `phase_name="seg_qc"` appears in the yielded sequence for that dataset),
  and Cellpose segment is **not** yielded for it.
- Happy path (R3): same setup, `run_seg_qc_on_existing=False` → **no** `seg_qc`
  phase for the pre-segmented dataset; the next yielded phase for it is
  `threshold_compute:<round>`.
- R4 (fresh path unchanged): a dataset with **no** pre-existing segmentation still
  yields `segment` then `seg_qc` regardless of `run_seg_qc_on_existing` (test both
  flag values → identical fresh-path sequence).
- R4 boundary (mix): a run with **one pre-segmented and one fresh** dataset, flag
  on → BOTH yield `seg_qc` (pre-segmented via the new branch, fresh via the
  unchanged path), and the fresh one also yields `segment`. Proves the restructured
  `continue` doesn't swallow the fresh path.
- R3 end-to-end: a run where **all** datasets are pre-segmented and the flag is
  **off** → zero `seg_qc` phases; the first phase yielded per dataset is
  `threshold_compute:<round>`.
- Edge case: a pre-segmented dataset whose chosen layer is reviewed and **accepted**
  in seg-QC → group thresholding subsequently reads the (possibly edited)
  `/labels/<seg_name>`; the run advances to `threshold_compute`.
- Edge case (cancel): cancelling seg-QC on a pre-segmented dataset propagates a
  runner-level cancel (same as the fresh-path seg-QC cancel behavior — this aborts
  the whole batch; see Risks).
- Edge case (time-lapse): a **pre-segmented time-lapse** dataset
  (`n_timepoints > 1`) yields **no** `seg_qc` phase even with the flag on — it
  proceeds to tracking/thresholding as today. (Build the fixture with
  `metadata.n_timepoints > 1` and a `(T, H, W)` labels layer.)
- Edge case (stale override): a `segmentation_overrides` entry naming a layer that
  is **not** in `list_labels()` yields **no** `seg_qc` phase (and does not raise);
  the run falls through to thresholding, which records the missing-layer failure as
  today.
- Edge case (resume idempotency): a pre-segmented dataset whose seg-QC already
  completed in a prior pass is **not** re-yielded for seg-QC on resume (verify
  against the runner's completed-phase / `datasets_without_failures` skip
  mechanism; add an assertion).
- R6 (headless no-op): with `interactive_qc=False` and
  `run_seg_qc_on_existing=True`, a pre-segmented dataset yields **no** `seg_qc`
  phase (headless never yields interactive phases). Covered in
  `test_single_cell_runner.py`.

**Verification:**
- `pytest tests/test_gui_workflows/test_interactive_runner.py
  tests/test_gui_workflows/test_single_cell_runner.py -q` passes.
- Manually: a pre-segmented `.h5` from `percell4-batch` opens the seg-QC editor on
  its selected layer when the box is checked, and goes straight to thresholding
  when unchecked.

---

## System-Wide Impact

- **Interaction graph:** the only behavioral fork is in `_phase_generator`'s
  pre-segmented branch. Fresh-Cellpose datasets, dilute, measure, export, and the
  seg-QC editor itself are unchanged.
- **Error propagation:** seg-QC failure/cancel on a pre-segmented dataset reuses
  the existing `_wrapped_complete` path (cancel → runner cancel; failure →
  recorded), identical to the fresh path.
- **State lifecycle:** seg-QC on a pre-segmented layer **overwrites
  `/labels/<seg_name>` in place** on accept — the same write the editor already
  performs. The batch-produced layer is replaced by the reviewed version, which is
  the intended effect. No new resource, no measurement write into `.h5` (R6).
- **API surface parity:** `WorkflowConfig` gains a field; `run_config.json` gains a
  key. Both are additive with a back-compat default (R5). No other workflow or CLI
  reads this field.
- **Integration coverage:** the load-bearing proof is the U3 runner test asserting
  the *yielded phase sequence* differs by flag for a pre-segmented dataset and is
  invariant for a fresh dataset — a generator-shape check, since the value travels
  dialog → config → generator branch.
- **Unchanged invariants:** fresh-Cellpose seg-QC; the `interactive_qc` switch
  semantics; group thresholding reading `/labels/<seg_name>`; the
  measurement-in-run-folder-only provenance rule; the five session selection
  fields.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Restructuring the `continue` accidentally changes the fresh-Cellpose path | U3 keeps the fresh branch untouched; a dedicated R4 test asserts the fresh sequence is identical for both flag values |
| Default `True` changes out-of-box behavior for pre-segmented datasets (now an interactive QC stop) | Intended per the feature's purpose and the user's explicit choice; documented; users uncheck to restore straight-to-thresholding |
| Resuming an *old* run (pre-field `run_config.json`) now defaults the flag to `True`, possibly inserting a seg-QC stop the original run didn't have for not-yet-processed pre-segmented datasets | Narrow window (only old runs resumed before their pre-segmented datasets reach thresholding). Accepted as consistent with the chosen default. If exact old-run resume fidelity is later required, default `config_from_dict` to `False` while keeping the dialog default `True` |
| Seg-QC on a pre-segmented dataset overwrites the batch-produced layer | This is the existing seg-QC accept contract and the desired outcome (reviewed labels feed thresholding); documented in Scope Boundaries |
| Checkbox no-ops at runtime (wired wrong) | Read pull-style like `_cp_gpu`; U2 test asserts the value via the built `WorkflowConfig`, not the widget alone |
| Checkbox added outside the scroll area, breaking `cap_to_screen` | Add inside the Segmentation Selection group (already inside `wrap_in_scroll`); `test_dialog_helper_compliance.py` enforces |
| Off-label session write introduced in the runner branch | The branch only yields a `PhaseRequest`; U3 verifies no `session.set_active_*` is added |
| **Pre-segmented time-lapse `(T,H,W)` layer crashes the single-frame seg-QC editor** (review BLOCKING) | New yield gated on `not self._is_timelapse(entry)`; pre-segmented time-lapse skips seg-QC as today; U3 time-lapse test |
| Stale `segmentation_overrides` names a missing layer → seg-QC opens on a non-existent layer and raises in `start()` | New yield gated on `existing in list_labels()`; missing → skip seg-QC, fall through to today's thresholding-records-failure path; U3 stale-override test |
| **Cancel during pre-segmented seg-QC aborts the entire batch** — a new, wider blast radius for the percell4-batch large-N case (these datasets never hit seg-QC before) | Pre-existing seg-QC cancel semantics, now reachable for pre-segmented batches. Documented: Accept is the "looks good, continue" gesture; Cancel aborts the run. A per-dataset "skip QC" affordance is a possible follow-up, out of scope here |
| Default `True` + resume re-opens seg-QC for already-QC'd pre-segmented datasets | U3 resume-idempotency test verifies completed seg-QC is not re-yielded; the launcher reconstructs `cfg` from `config_from_dict` on resume so the field is present on both fresh and resume paths |
| Seg-QC overwrites the pre-existing layer in place; a later fresh re-run QCs from the edited (not original batch) labels | Pre-existing seg-QC accept contract; documented so the in-place, cumulative-refinement behavior isn't surprising |

---

## Documentation / Operational Notes

- Update `src/percell4/gui/workflows/CLAUDE.md` (single_cell runner bullet) to note
  that pre-segmented datasets optionally run seg-QC, gated by
  `WorkflowConfig.run_seg_qc_on_existing`.
- No migration: `run_config.json` gains an additive key with a back-compat default;
  old run folders load unchanged in shape.

---

## Sources & References

- Config dataclass: `src/percell4/workflows/models.py` (`WorkflowConfig`, 268-336).
- Serialization: `src/percell4/workflows/artifacts.py`
  (`config_to_dict` / `config_from_dict`, ~201-224).
- Config dialog: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  (Segmentation Selection group 565-585; `_cp_gpu` 448-450; `_try_build_config`
  1696-1810; constructor call 1794-1807).
- Runner: `src/percell4/gui/workflows/single_cell/runner.py`
  (auto-skip branch 259-269; fresh seg-QC yield 290-309; `_make_seg_qc_handler`
  677-730; dilute gating precedent 387-393).
- Seg-QC editor (unchanged): `src/percell4/gui/workflows/single_cell/seg_qc.py`
  (read 229, write-back 1293).
- percell4-batch (pre-segmented source):
  `src/percell4/interfaces/cli/batch_process.py` (`--seg-name`, default
  `cellpose_<n_cells>`); console script `pyproject.toml:83`.
- Tests: `tests/test_workflows/test_models.py`,
  `tests/test_workflows/test_artifacts.py`,
  `tests/test_gui_workflows/test_config_dialog.py`,
  `tests/test_gui_workflows/test_interactive_runner.py`
  (`_FakeSegQCController` pattern), `tests/test_gui_workflows/test_single_cell_runner.py`.
- Learnings: `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`,
  `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`,
  `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`,
  `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`,
  `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`.
- Related prior work: `docs/plans/2026-05-20-001-feat-end-to-end-single-cell-workflow-plan.md`
  (config-field + serialization + runner-gating pattern this plan mirrors).
