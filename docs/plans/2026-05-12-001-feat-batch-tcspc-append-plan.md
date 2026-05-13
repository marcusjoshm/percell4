---
title: "feat: Batch TCSPC (.bin) append to existing datasets"
type: feat
status: completed
date: 2026-05-12
origin: docs/brainstorms/2026-05-12-batch-tcspc-append-requirements.md
---

# feat: Batch TCSPC (.bin) append to existing datasets

## Overview

Adds a new **Batch TCSPC Append** dialog that loops the existing single-dataset `add_decay_to_dataset` use case across N pre-existing `.h5` files. The new code is a thin orchestration layer: a long-format calibration CSV parser, a pure-Python batch orchestrator, a Qt dialog, and a launcher entry. No changes to the underlying decay-write engine.

The implementation mirrors the existing **batch compress** pattern in `src/percell4/interfaces/gui/main_window.py:_run_batch_compress` (GUI-thread loop driven by a modal `QProgressDialog`, cancellation via `progress.wasCanceled()`) rather than introducing a new `QThread` worker — this aligns with the codebase convention and keeps the new surface area small.

---

## Problem Frame

Today, adding `.bin` files to an existing dataset goes through `AddLayerDialog` → `add_decay_to_dataset` one dataset at a time. A typical FLIM experiment produces 3–12 dishes per session, each exported as its own subfolder of `.bin` files. Repeating the single-dataset dialog 12 times — re-entering the same tile/orientation settings, pasting per-channel calibration values — is slow and error-prone (especially the per-channel `(phase, modulation)` pairs).

Within a session the structure is uniform: same channel layout, same tile grid and orientation across every dish. The only thing that genuinely varies dish-to-dish is calibration. The batch feature holds everything uniform constant once and varies calibration via a single per-batch CSV. (See origin: `docs/brainstorms/2026-05-12-batch-tcspc-append-requirements.md`.)

---

## Requirements Trace

Carried verbatim from the origin doc — see `docs/brainstorms/2026-05-12-batch-tcspc-append-requirements.md` for full text. Brief restatement, used here to anchor unit-level coverage.

- **R1.** Explicit multi-select of target `.h5` datasets (current project + ad-hoc fallback).
- **R2.** Group discovery from a parent root: each immediate subfolder = one group, `.bin` collected recursively.
- **R3.** Manual dataset ↔ group pairing with name-similarity Auto-pair default; group uniqueness enforced.
- **R4.** Long-format calibration CSV (`dataset,channel,frequency_mhz,phase,modulation`); row-numbered validation.
- **R5.** Single global tile/orientation config for the batch (no per-dataset variants).
- **R6.** Conflict policy radio: Skip existing (default) or Overwrite all.
- **R7.** Validate-then-Run: Run is disabled until pre-flight passes; any edit re-disables Run.
- **R8.** Per-dataset progress + summary report. **Deviation from origin:** the worker-thread model in origin R8 (origin doc line 142: *"Each dataset's append runs in a worker thread; the UI stays responsive."*) is replaced by the codebase's established GUI-thread `QProgressDialog` loop (see Key Technical Decisions); the user-facing promise — sequential execution, per-dataset progress, mid-batch cancel between datasets — is preserved.
- **R9.** Calibration written to `/metadata` *before* each dataset's append (so partial failure still leaves consistent calibration record).
- **R10.** Continue past per-dataset failures; full batch only aborts on user Cancel.

---

## Scope Boundaries

- Per-dataset tile geometry / rotation / token regex — out of scope. Heterogeneous batches use separate runs.
- Parallel per-dataset execution — out of scope. Sequential matches compress's convention and disk profile.
- Batch phasor compute after append — out of scope. The existing phasor flow can be run on the same datasets afterward.
- Auto-generating the CSV from microscope metadata — out of scope. User produces CSV manually.
- Per-cell undo or rollback — out of scope. Append is forward-only.
- Editing `/intensity` during the append — never. Rotation/flip touch `/decay` only (already enforced by `add_decay_to_dataset`).
- Writing `session.active_*` / `filter_ids` / `selection` from this dialog — never. Per `CLAUDE.md` GUI state ownership, the dialog is an Action.

### Deferred to Follow-Up Work

- **Wiring `ProjectIndex` into `LauncherWindow`.** Today the launcher has no concept of "the current project" — the only `ProjectIndex` construction is in `adapters/importer.py:516`. The dialog's `get_project_index` callable is plumbed through with a `None` default, so the project-aware dataset auto-population path will light up the day the launcher exposes one. Out of scope for this feature; tracked as a future follow-up.

---

## Context & Research

### Relevant Code and Patterns

- **Batch loop template** — `src/percell4/interfaces/gui/main_window.py:_run_batch_compress` (lines 720–795). Modal `QProgressDialog`, GUI-thread loop, per-iteration `wasCanceled()` check, summary via status bar + `QMessageBox.warning` on failures. This is the exact shape the new dialog's Run flow will follow.
- **Dialog form template** — `src/percell4/gui/compress_dialog.py:_build_ui`. Single scrolled `QVBoxLayout` with `QGroupBox` sections, pinned button row outside the scroll area, frozen `CompressConfig` property materializing widget state on Accept.
- **Decay write engine** — `src/percell4/application/use_cases/add_decay_to_dataset.py:add_decay_to_dataset` and its `AppendReport` dataclass. Reused verbatim per call; no signature changes.
- **Existing TCSPC append call site** — `src/percell4/gui/add_layer_dialog.py:1479-1490` and the post-run reporting in `_tcspc_show_report` (1676-1707). Mirror parameter construction and `AppendReport` consumption.
- **FLIM calibration on disk** — flat `/metadata` attrs: `flim_cal_phase_<channel>`, `flim_cal_mod_<channel>`, `flim_frequency_mhz`. Written by `add_layer_dialog.py:_tcspc_persist_flim_metadata` (1639-1674), read by `compute_phasor.py:_read_fresh_metadata`. Use the same key naming.
- **Dialog scroll/cap helpers** — `src/percell4/gui/_dialog_utils.py:wrap_in_scroll`, `cap_to_screen`. AST-enforced by `tests/test_gui/test_dialog_helper_compliance.py` — mandatory for any new tall dialog.
- **Launcher dialog registration** — `LauncherWindow._on_add_layer_to_dataset` (`src/percell4/interfaces/gui/main_window.py:804-816`) and `_create_io_panel` callback injection (lines 233-246). New entry follows the same shape.
- **ProjectIndex** — `src/percell4/project.py`, public API `load() -> DataFrame`, `reconcile(project_dir) -> {orphan_files, missing_files}`. Currently consumed only by `adapters/importer.py:514-516`. This dialog is the first GUI consumer; treat the DataFrame's `path` column as the dataset list.
- **DatasetStore.set_metadata** — `src/percell4/store.py:330-336`. Flat key-value writes to `/metadata.attrs`. No nested groups.
- **FlimConfig** — `src/percell4/domain/io/models.py:202-213`. In-memory `channel_calibrations: tuple[tuple[float, float], ...]` is positional (aligned to channel order). Use it only as an in-memory carrier; on-disk shape is the flat keys above.

### Institutional Learnings

- **Decay-write-path canonical source** — `docs/solutions/architecture-patterns/decay-write-path.md`. Reuse `add_decay_to_dataset`; do not introduce a parallel decay writer. The dead code at `add_decay_to_dataset.py:407-471` (`_read_and_stitch_decay`) is not to be revived.
- **Batch-compress development lessons** — `docs/solutions/logic-errors/batch-compress-development-lessons.md`. Three documented occurrences of "matcher/discovery refactor silently collapses per-input scope," producing N identical outputs. The triple `(h5_path, source_dir, calibration)` must be carried through the loop end-to-end — never re-derive any field from a shared parent inside the orchestrator.
- **Multi-vector HDF5 staleness** — `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`. After `set_metadata(attrs)` on the currently-loaded session dataset, also mutate `Session.dataset.metadata.update(attrs)` in-place to defeat the h5py library-level cache. Use `DatasetRepository.read_metadata(handle)` port, not `handle.metadata.get(...)`, anywhere the orchestrator (or anything it triggers) re-reads calibration.
- **Channel-deletion permanence** — `docs/solutions/architecture-patterns/channel-deletion-permanence.md` (Step 5 frozen-handle/mutable-dict sync rule). Same rule applies here.
- **GUI Action contract exhaustiveness** — `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`. The dialog as a whole is an Action — it writes new resources but per-dataset, not into the *current* session. Embedded field-level widgets are Action; only the terminal Run button is Creator (writes `/decay/<ch>`). Update `docs/audits/gui-element-classification.yaml` per the existing `add_layer.tcspc_tab.*` template.
- **Atomic-write contract** — `docs/solutions/architecture-patterns/atomic-write-contract.md`. `set_metadata`/`append_decay_layers` are in-place mutations with per-channel `flush+fsync`. The batch is NOT a transaction; the aggregated report must show partial state truthfully (`{succeeded, failed, skipped, not_yet_run}`).
- **FLIM cross-layer alignment** — `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`. Order is load-bearing: write `/metadata` calibration **before** `add_decay_to_dataset`, so any later `compute_phasor` reads correct values.
- **Wrap tall Qt dialogs in scroll** — `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`. Mandatory `wrap_in_scroll` + `cap_to_screen`; AST test will fail CI otherwise.
- **Callback injection for new dialog** — `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`. Inject `get_project_index`, `repo_factory(h5_path)`, `show_status` as callables — never pass `launcher=self`.

---

## Key Technical Decisions

- **GUI-thread loop with `QProgressDialog`, not a `QThread` worker.** Mirrors `_run_batch_compress`. `Worker(QThread)` exists in `gui/workers.py` but the codebase reserves it for single-shot heavy ops (Cellpose). Adding a new worker class with cooperative cancellation would be the first of its kind in batch I/O paths and is not justified by the workload — the existing pattern handles compress over similarly-sized data without a worker. The origin doc's R8 reference to a worker thread is deliberately superseded here.
- **CSV in `domain/io/`, not `adapters/`.** Pure-Python parser, no `csv.DictReader` magic on undeclared columns, no auto-coercion. Returns a frozen `BatchCalibration` dataclass. Errors raise typed exceptions from `domain/errors.py` carrying row numbers.
- **Calibration on disk: reuse the existing flat-key schema.** `flim_cal_phase_<ch>`, `flim_cal_mod_<ch>`, `flim_frequency_mhz` — same keys `add_layer_dialog.py:_tcspc_persist_flim_metadata` writes today and `compute_phasor.py` reads. The CSV-derived `BatchCalibration` is unpacked into these keys at write time; we do **not** invent a nested schema.
- **Triple-typed per-dataset state.** The orchestrator iterates over a `BatchAppendItem` dataclass that carries `(h5_path, source_dir, calibration)` together. No shared parent path is re-derived inside the loop; this is the explicit guard against the Bug-3 echo from the batch-compress learnings.
- **Pre-flight validation is pure-Python, callable without Qt.** `validate_batch_inputs(items, conflict_policy, ...)` lives in the use case module and returns a `BatchValidationReport`. The dialog calls it from the GUI but tests exercise it directly.
- **Session in-place metadata sync handled by the dialog, not the use case.** The use case stays Qt-free and Session-unaware; the dialog post-processes each successful write by checking whether `session.dataset.path == item.h5_path` and updating `session.dataset.metadata` in place. Keeps the use case testable in isolation while honoring the h5py library-cache learning.
- **Aggregated report is data, not text.** Orchestrator returns `BatchAppendReport(items=[(item, status, report_or_none, error_or_none), ...])`. The dialog formats it for display and clipboard. The Copy-to-clipboard plain-text rendering lives in the dialog, not the use case.
- **No sidecar log file.** Per-dataset provenance is already recorded inside each `.h5` by `add_decay_to_dataset`. The clipboard report is the only externalized artifact. (Revisit if real-world usage demands a `batch_run.log`.)

---

## Open Questions

### Resolved During Planning

- **Worker vs. GUI-thread loop:** GUI-thread loop. See Key Technical Decisions.
- **Calibration on-disk shape:** flat per-channel keys, reusing the existing single-dataset schema.
- **Where validation lives:** pure-Python function in the use case module, callable from the dialog and tests independently.
- **`session.dataset.metadata` sync:** dialog-side, not orchestrator-side. Keeps the orchestrator Qt/Session-free.
- **CSV `frequency_mhz` policy:** allowed to vary across datasets; required to be consistent across channels within a dataset. (Carried from origin R4.)

### Deferred to Implementation

- **Auto-pair similarity threshold and tie-break behavior** (fuzz ratio cutoff, what counts as ambiguous). Pure tuning; pick a default that pairs the example LAS X layout cleanly and refine if real CSVs reveal mismatches.
- **Dry-run mode** — whether `add_decay_to_dataset` needs a `dry_run` flag for "calibration-only" runs. Deferred; users can verify with one real dataset first.
- **`batch_run.log` sidecar** — only if usage reveals clipboard isn't enough.
- **Hoisting `frequency_mhz` out of the CSV** if real workflows always use one rep rate per session — deferred until evidence appears.

---

## Implementation Units

- U1. **Calibration CSV parser**

  **Goal:** Pure-Python parser/validator for the long-format calibration CSV. Returns a typed, frozen `BatchCalibration` keyed by `(dataset_stem, channel_name)`.

  **Requirements:** R4.

  **Dependencies:** None.

  **Files:**
  - Create: `src/percell4/domain/io/calibration_csv.py`
  - Modify: `src/percell4/domain/errors.py` (add `CalibrationCSVError` with `row` / `column` fields)
  - Test: `tests/test_domain/test_calibration_csv.py`

  **Approach:**
  - Use `csv.DictReader` with required-column whitelist: `dataset`, `channel`, `frequency_mhz`, `phase`, `modulation`. Extra columns silently ignored.
  - Validate per-row, accumulate row-numbered errors, raise a single `CalibrationCSVError` with all errors when the parse completes (don't bail on first error — the user wants to fix everything at once).
  - Frozen dataclass: `BatchCalibration` wrapping `dict[str, dict[str, ChannelCalibration]]` where `ChannelCalibration = (frequency_mhz, phase, modulation)`.
  - Helper `validate_frequency_consistency(BatchCalibration) -> list[str]` returns per-dataset error messages when `frequency_mhz` differs across channels.

  **Patterns to follow:**
  - Frozen dataclasses in `src/percell4/domain/io/models.py`.
  - Typed error pattern in `src/percell4/domain/errors.py`.

  **Test scenarios:**
  - Happy path: 9-row CSV (3 datasets × 3 channels) → `BatchCalibration` with expected nested structure.
  - Happy path with extra columns: ignored without error.
  - Edge case: empty CSV (no data rows after header) → empty `BatchCalibration`, no error.
  - Edge case: duplicate `(dataset, channel)` rows → single `CalibrationCSVError` listing the conflicting row numbers.
  - Edge case: dataset names with commas/quotes (CSV escaping) round-trip correctly.
  - Error path: missing required column (`channel` missing) → error names the missing column.
  - Error path: non-numeric `phase` value → error names row, column, offending value.
  - Error path: `frequency_mhz` differs across two channels of the same dataset → `validate_frequency_consistency` returns one error message naming the dataset and the conflicting values.
  - Error path: header-only file (no data rows) → empty result, no error (validation lives downstream).

  **Verification:**
  - Parser accepts the example CSV in the origin doc verbatim and produces the expected nested mapping.
  - All error modes surface the row number with the failing value.

---

- U2. **Batch orchestrator use case**

  **Goal:** Pure-Python orchestrator. Loops over a list of `BatchAppendItem` triples. For each: writes calibration to `/metadata`, then calls `add_decay_to_dataset`. Aggregates per-dataset `AppendReport`s into a `BatchAppendReport` carrying truthful partial state.

  **Requirements:** R5, R6, R8, R9, R10.

  **Dependencies:** U1 (consumes `BatchCalibration` from the parser).

  **Files:**
  - Create: `src/percell4/application/use_cases/batch_add_decay.py`
  - Test: `tests/test_application/test_batch_add_decay.py`

  **Approach:**
  - Define `BatchAppendItem` (frozen dataclass): `h5_path: Path, source_dir: Path, calibration: dict[str, ChannelCalibration]`. `source_dir` is **the per-dataset group folder** (the immediate subfolder of the user-picked source root that contains this dataset's `.bin` files), not the source root itself. The dialog constructs items from `(selected_dataset, paired_group_folder, BatchCalibration[dataset_stem])`.
  - Define `BatchAppendReport` (frozen dataclass): `items: tuple[BatchItemResult, ...]` where `BatchItemResult` carries `item`, `status` (one of `succeeded | failed | skipped_no_changes | cancelled | not_run`), `append_report` (the `AppendReport` from the use case, or `None`), `error` (`str | None`).
  - Public entry point:
    ```
    batch_add_decay(
        items: list[BatchAppendItem],
        *,
        token_config: TokenConfig,
        tile_config: TileConfig,
        flim_config: FlimConfig,
        cross_format_rule: CrossFormatRule,
        rotate_k: int = 0,
        flip_axis: int | None = None,
        force: bool = False,
        progress_callback: Callable[[BatchAppendItem, BatchItemResult], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BatchAppendReport
    ```
  - Per-item flow:
    1. If `cancel_check and cancel_check()`: mark this item and all remaining as `cancelled`, return.
    2. Open `DatasetStore(item.h5_path)`. Build the flat calibration attr dict from `item.calibration` aligned to `store.metadata["channel_names"]`. Call `store.set_metadata({"flim_frequency_mhz": ..., f"flim_cal_phase_{ch}": ..., f"flim_cal_mod_{ch}": ..., ...})`. Calibration is written even if the decay step is later skipped or fails — order is load-bearing per `flim-phasor-cross-layer-alignment-2026-04-29.md`.
    3. Build per-item `intensity_channels: list[IntensityChannel]` by reading `store.metadata["channel_names"]` and `store.metadata.get("channel_base_stems", [])`, and applying `_extract_channel_token` (from `add_decay_to_dataset`) to each name. This is **per-item, not shared across the batch** — channels are owned by each `.h5`, and datasets with semantic channel names (`CA-SiR`, `mNG`, `mTQ2`) produce empty digit-suffix tokens, so the per-item construction is what makes the explicit `intensity_channels=` path on `add_decay_to_dataset` work (see `add_decay_to_dataset.py:96-101, 127-135`).
    4. Call `add_decay_to_dataset(h5_path=item.h5_path, source_dir=item.source_dir, token_config=…, tile_config=…, flim_config=…, cross_format_rule=…, rotate_k=…, flip_axis=…, force=…, intensity_channels=intensity_channels, progress_callback=None)`. The use case's per-channel `progress_callback` is intentionally not threaded through — the orchestrator's progress granularity is per-item, and per-channel chatter would not improve UX. If real runs reveal users want intra-dataset progress, adapt by capturing the string into the latest `BatchItemResult.in_progress_label` field in a follow-up.
    5. Translate the returned `AppendReport` into a `BatchItemResult`. `succeeded` if `report.written` non-empty; `skipped_no_changes` if every channel landed in `report.errors` as "already exists" under `force=False`; `failed` otherwise.
    6. `progress_callback(item, BatchItemResult)` after each item.
  - Outer try/except per item: any exception not caught by `add_decay_to_dataset` (e.g., bad `.h5` path, permission error) lands as `failed` with `error=str(exc)`. The loop continues.
  - **Validation entry point:**
    ```
    validate_batch_inputs(
        items: list[BatchAppendItem],
        *,
        channel_names_per_item: dict[Path, list[str]],   # h5_path -> channel names from each store
        force: bool,
        existing_decay_per_item: dict[Path, set[str]],   # h5_path -> set of channel names already in /decay
    ) -> BatchValidationReport
    ```
    Returns a frozen `BatchValidationReport` carrying: `pairing_errors: list[str]`, `csv_coverage_errors: list[str]`, `frequency_consistency_errors: list[str]`, `decay_collision_warnings: list[str]`, `is_passing: bool` (true iff all error lists empty). Pure function — no I/O. The dialog calls it after building `items`, `channel_names_per_item`, and `existing_decay_per_item` from per-`.h5` reads.

  **Patterns to follow:**
  - `add_decay_to_dataset` use case shape: pure-Python, no Qt, returns a report dataclass with per-channel errors instead of raising.
  - Order-of-operations rule from FLIM cross-layer alignment: calibration `/metadata` write **before** the decay write.

  **Test scenarios:**
  - Covers AE-equivalent acceptance criterion 8 (each `.h5` ends with `/decay/ch1-3` and `/metadata` matching CSV):
    Happy path: 2 items with distinct calibrations → both `.h5` files end with the correct per-channel `/metadata` attrs (assert `flim_cal_phase_ch1` differs as the CSV specifies), both `/decay/<ch>` written.
  - Happy path: 2 items with **semantic channel names** (`mNG`, `mTQ2`) → orchestrator constructs per-item `IntensityChannel` records, passes them via `intensity_channels=…` to `add_decay_to_dataset`, both items land with non-empty `report.written`. (Guards against F5: the digit-suffix token would otherwise be empty and yield zero bindings.)
  - Happy path: explicit ordering — verify `set_metadata` happens *before* `add_decay_to_dataset` by spying on call sequence.
  - Edge case: empty `items` list → empty `BatchAppendReport`, no error.
  - Edge case: `cancel_check` returns True after item 1 finishes → item 1 status `succeeded`, items 2..N status `cancelled`, no exception raised.
  - Edge case: `force=False` and every channel already has `/decay` → status `skipped_no_changes`; `/metadata` calibration is still written (calibration update is independent of decay collision).
  - Edge case: `force=True` → all channels rewritten regardless of pre-existence.
  - Error path: item 2's `.h5` path doesn't exist → item 2 status `failed`, items 1 and 3 unaffected, batch continues.
  - Error path: item 2's `source_dir` has zero `.bin` files → `add_decay_to_dataset` returns an `AppendReport` with `errors["scan"]` populated; orchestrator surfaces this as status `failed` with the scan error string.
  - Integration: `validate_batch_inputs` flags a missing `(item, channel)` calibration row with a specific message naming the item and channel.
  - Integration: `validate_batch_inputs` flags two items sharing the same `source_dir` as a uniqueness violation.

  **Verification:**
  - Running the orchestrator on a 3-item fixture produces 3 distinct `/metadata` records with the expected per-channel `flim_cal_*` values.
  - A mid-batch failure leaves earlier items committed and surfaces truthful partial state in the returned report.

---

- U3. **Batch TCSPC dialog**

  **Goal:** New Qt dialog implementing the UI from the origin doc: dataset multi-select, source-root group discovery, manual pairing, CSV upload, single global tile/orientation config, validate-then-run, per-dataset progress, summary view.

  **Requirements:** R1, R2, R3, R4 (UI surface), R5, R6, R7, R8 (UI surface), R10 (UI surface).

  **Dependencies:** U1, U2.

  **Files:**
  - Create: `src/percell4/gui/batch_tcspc_dialog.py`
  - Test: `tests/test_gui/test_batch_tcspc_dialog.py`

  **Approach:**
  - `class BatchTCSPCDialog(QDialog)`. Constructor injects callables, never `launcher=self`:
    ```
    BatchTCSPCDialog(
        parent,
        *,
        session: Session | None,
        show_status: Callable[[str], None],
        get_project_index: Callable[[], ProjectIndex | None] = lambda: None,
        orchestrator: Callable[..., BatchAppendReport] = batch_add_decay,
        validator: Callable[..., BatchValidationReport] = validate_batch_inputs,
        csv_parser: Callable[[Path], BatchCalibration] = parse_calibration_csv,
    )
    ```
    The `Callable` defaults make the dialog unit-testable with stubs. `get_project_index` defaults to `lambda: None` — see Section 1 below. Per-`.h5` reads use `DatasetStore(path)` directly rather than a `repo_factory` indirection (the dialog operates on N `.h5` files, a factory adds no value over the constructor).
  - **Layout:** outermost `QVBoxLayout` → `wrap_in_scroll(content_widget)` → button row pinned outside the scroll. Call `cap_to_screen(self)` after `setLayout`. Section structure mirrors compress_dialog: one `QGroupBox` per numbered section in the origin UI mock.
  - **Section 1 (Datasets):** `QTableWidget` with columns: checkbox, filename, channels, decay-status. **Primary entry path is the `Add datasets…` button** (`QFileDialog.getOpenFileNames`, `*.h5` filter). If `get_project_index()` returns a non-`None` `ProjectIndex`, the table is also auto-populated from `index.load()['path']` with each row pre-checked. The launcher does not currently expose a `ProjectIndex` — verified by grep, zero hits for `project_index` / `ProjectIndex` in `interfaces/gui/main_window.py` or `app.py`; the only construction is in `adapters/importer.py:516`. **Until a "current project" concept lands in the launcher (separate, deferred work), the dataset table starts empty and the user populates it via `Add datasets…`.** The `get_project_index` callable is wired in now so the project-aware path lights up automatically the day the launcher exposes one. Disabled rows (all decay already present) start unchecked but remain checkable for overwrite scenarios.
  - **Section 2 (Source root):** `QLineEdit` + Browse; on change, scan immediate subfolders and populate a discovered-groups label (`Discovered groups: N`). Recursive `.bin` count per group is cached via `Path.rglob("*.bin")`.
  - **Section 3 (Pairing):** `QTableWidget` keyed by checked datasets. Each row: dataset name (read-only) + a `QComboBox` populated with `["— select —", "— skip —", *group_names]`. `Auto-pair` button runs `difflib.SequenceMatcher` ratio; sets dropdown to best match when ratio ≥ 0.6; leaves `— select —` otherwise. Uniqueness invariant: when one combo's value changes, any other combo holding the same group is reset to `— select —`.
  - **Section 4 (CSV):** Browse button → calls `csv_parser`. On error, show `QMessageBox.critical` with the multi-row error text. On success, populate a calibration-preview column on each pairing row.
  - **Section 5 (Stitching & orientation):** lift the relevant widgets from `add_layer_dialog.py`'s TCSPC tab — `TileConfig` grid rows/cols/type/order, `rotate_k` combo, `flip_axis` combo, plus an Advanced twisty for `TokenConfig` regex and raw `bin_*` geometry defaults. **Do not duplicate the widget code**; extract a shared `StitchingFormWidget` if the lift is non-trivial (see Approach note below). Default values match `add_layer_dialog.py`'s defaults.
  - **Section 6 (Conflict policy):** `QButtonGroup` with two `QRadioButton`s — Skip existing (default) / Overwrite all. Maps to `force` boolean passed to the orchestrator.
  - **Buttons:** `[Validate] [Run] [Close]`. Run is disabled at init and after any state change; only `_on_validate_succeeded` enables it. Any signal connected to "settings changed" (combo change, table edit, CSV reload, conflict-policy change) calls `_invalidate_run`.
  - **Validate path:** assembles a `list[BatchAppendItem]` plus channel-names-per-item (read from each store's metadata), calls `validator(...)`, shows the result in a `QPlainTextEdit` panel inside Section 7. Sets `self._validated = True` iff no errors.
  - **Run path:** builds the items list, opens a modal `QProgressDialog(self, "Running batch TCSPC append…", "Cancel", 0, n)`, sets `Qt.WindowModal`. Defines a `cancel_check = lambda: progress.wasCanceled()` and a `progress_callback(item, result)` that updates the label + step. Calls `orchestrator(items, ...)` on the GUI thread. After the loop returns, swaps the form widget for a summary widget showing the `BatchAppendReport` rendered as a table plus a Copy-to-clipboard button. Close button returns to the launcher.
  - **Session sync (post each successful item):** in the dialog's `progress_callback`, if `self._session and self._session.dataset and self._session.dataset.path == item.h5_path and result.status == "succeeded"`: build the same calibration dict and call `self._session.dataset.metadata.update(...)`. Keeps the orchestrator Session-free while honoring the h5py cache-staleness learning.
  - **Shared stitching widget (decision):** Lift the TCSPC stitch/orientation widgets out of `add_layer_dialog.py` into a new `src/percell4/gui/_stitching_form.py` only if doing so cleanly removes >50 lines of duplication. Otherwise, copy the widget construction code — extraction is a refactor risk we don't want to bundle with a new feature. Default to **copy**; **if the implementer measures >50 lines of duplication after the copy, open a follow-up issue tagged `follow-up-refactor` titled "Extract TCSPC stitching widget" and link it in the PR description.** This keeps the deferral owned rather than lost.

  **Patterns to follow:**
  - `src/percell4/gui/compress_dialog.py:_build_ui` for section layout and the "frozen config property" approach.
  - `src/percell4/interfaces/gui/main_window.py:_run_batch_compress` for the `QProgressDialog`-driven loop.
  - `src/percell4/gui/add_layer_dialog.py:_tcspc_show_report` for partial-success messaging conventions.
  - `wrap_in_scroll` + `cap_to_screen` per `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`.

  **Test scenarios:**
  - Happy path (Qt headless via `pytest-qt`): construct dialog with mocked `get_project_index` returning 3 datasets; verify all 3 appear checked in the table.
  - Happy path: load a valid CSV via the mocked parser; verify calibration column populates on every paired row.
  - Edge case: clicking Auto-pair on 3 datasets with exact-name subfolders fills all 3 combos; ratios below threshold remain `— select —`.
  - Edge case: changing combo A to group X clears combo B if B previously held X.
  - Edge case: every "settings changed" event (combo change, CSV reload, conflict-policy toggle, source-root change) disables Run.
  - Error path: Validate with a missing pairing → pre-flight panel shows the dataset name; Run stays disabled.
  - Error path: Validate succeeds; user edits one combo; Run re-disables until Validate is re-run.
  - Integration: clicking Run with a mocked orchestrator that emits 3 progress callbacks updates the label/step correctly and shows the summary widget on completion.
  - Integration: user clicks Cancel inside the progress dialog after item 1 → `cancel_check` returns True; items 2/3 land as `cancelled` in the summary.
  - Integration: covers AE-equivalent acceptance 12 — pre-flight reports a specific "missing calibration for (Dish 2, ch3)" message when the CSV is short one row.
  - Integration: dialog wrap-in-scroll AST test (`tests/test_gui/test_dialog_helper_compliance.py`) passes for the new file.

  **Verification:**
  - Dialog opens, all 6 sections render inside the scroll area, dialog height capped to screen on a small monitor.
  - Validate gating works: Run is impossible to click until pre-flight passes, and re-disables on every settings change.
  - A full end-to-end run with a mocked orchestrator surfaces the per-dataset progress + summary correctly.

---

- U4. **Launcher entry + IO panel button**

  **Goal:** Add a `Batch TCSPC Append` action to the IO panel that opens `BatchTCSPCDialog`.

  **Requirements:** R1 (UI surface — launcher entry).

  **Dependencies:** U3.

  **Files:**
  - Modify: `src/percell4/interfaces/gui/main_window.py` (add `_on_batch_tcspc_append`; pass into `_create_io_panel`)
  - Modify: `src/percell4/interfaces/gui/task_panels/io_panel.py` (accept new `on_batch_tcspc` callback; add the button)
  - Test: `tests/test_gui/test_main_window_wiring.py` (extend existing wiring test if present; otherwise create the file)

  **Approach:**
  - `_on_batch_tcspc_append(self)` lazy-imports `BatchTCSPCDialog`, instantiates with injected callables: `session=self._data_model.session`, `show_status=self.statusBar().showMessage`. `get_project_index` is **omitted** — defaults to `lambda: None` per U3. Calls `exec_()`, then `deleteLater()`. Mirrors `_on_add_layer_to_dataset`.
  - **No new `_project_index` field on `LauncherWindow`.** The "current project" concept is genuinely absent from the launcher today (verified: zero hits for `project_index` / `ProjectIndex` in `main_window.py` or `app.py`; the sole `ProjectIndex` construction is `adapters/importer.py:516`). Wiring one in is a separate piece of work, out of scope here. The dialog's `Add datasets…` path covers the user's flow without it.
  - `_create_io_panel` grows one more callback kwarg: `on_batch_tcspc=self._on_batch_tcspc_append`.
  - `IoPanel` constructor accepts the new callback and adds a button labeled `Batch TCSPC Append` to the existing action group (placement: directly under the existing `Add Layer to Dataset` button to keep TCSPC-related actions grouped).

  **Patterns to follow:**
  - `_on_add_layer_to_dataset` and the surrounding wiring in `main_window.py:233-246, 804-816`.
  - Existing button construction in `io_panel.py`.

  **Test scenarios:**
  - Happy path: button exists on IO panel after `LauncherWindow` initialization (Qt headless).
  - Integration: clicking the button calls the injected handler (verify via spy on the callback).

  **Verification:**
  - The button appears in the IO panel under `Add Layer to Dataset` and opens the new dialog when clicked.

---

- U5. **GUI element classification audit update**

  **Goal:** Record the new dialog and its widgets in `docs/audits/gui-element-classification.yaml` per the project's audit conventions.

  **Requirements:** Project rule from `CLAUDE.md` (GUI state ownership / `docs/audits/` living artifacts).

  **Dependencies:** U3, U4.

  **Files:**
  - Modify: `docs/audits/gui-element-classification.yaml`
  - Modify: `docs/audits/session-mutation-graph.md` (note the dialog is Action; writes no session slots)

  **Approach:**
  - Increment `total_widgets` and `counts` (Action / Creator buckets) at the file header.
  - Add `io_panel.batch_tcspc_append_button` row: `class: Action`, handler `_on_batch_tcspc_append`, notes `"opens BatchTCSPCDialog (Creator) from the launcher"`. Mirrors `io_panel.add_layer_button` (lines 535-544).
  - Add `batch_tcspc.*` widget rows following the `add_layer.tcspc_tab.*` template — every interactive widget classified individually:
    - Dataset table, Add datasets button, source-root edit + Browse, group discovery label, pairing combos, Auto-pair button, CSV browse, stitching widgets, conflict-policy radios, Validate button → all `Action`.
    - **Run button** → `Creator` (writes `/decay/<ch>` and `/metadata` per dataset). `notes` field calls out which HDF5 paths it writes and that auto-select side does **not** apply (datasets aren't loaded into the active session).
  - Add a note in `session-mutation-graph.md` stating this dialog has no session-mutation edges.

  **Patterns to follow:**
  - Existing `add_layer.tcspc_tab.*` entries.
  - Existing `io_panel.add_layer_button` Action-with-Creator-prose-note pattern.

  **Test scenarios:**
  - Test expectation: none — pure documentation/audit metadata update. The YAML's existing schema-validation test (if present) will catch malformed entries; no behavioral test is required.

  **Verification:**
  - YAML parses cleanly; new widget IDs are unique; `total_widgets` matches actual row count.

---

## System-Wide Impact

- **Interaction graph:** New launcher → `IoPanel` button → `_on_batch_tcspc_append` → `BatchTCSPCDialog`. Dialog → `validate_batch_inputs` (sync) → `batch_add_decay` (sync, GUI thread, modal `QProgressDialog`) → per-item `DatasetStore.set_metadata` + `add_decay_to_dataset` → per-item `Session.dataset.metadata.update` (only when active session matches). No new signal wiring on `CellDataModel` or napari.
- **Error propagation:** Per-item exceptions caught in the orchestrator; per-channel errors handled by the existing `add_decay_to_dataset.AppendReport.errors`. CSV parser raises a single `CalibrationCSVError` carrying all row-numbered failures. Validation report is data, not exceptions.
- **State lifecycle risks:** Partial-batch state is real (atomic-write does not apply — see learnings). The summary report must enumerate `{succeeded, failed, skipped_no_changes, cancelled, not_run}` so the user sees truthful state. Retrying a partial batch is straightforward: re-run with the same CSV/pairing; previously-succeeded datasets land as `skipped_no_changes` under `force=False`.
- **API surface parity:** No public-API change to `add_decay_to_dataset`, `DatasetStore`, `compute_phasor`, or `ProjectIndex`. The calibration CSV and `BatchAppendReport` are new public surfaces of `domain/io/` and `application/use_cases/` respectively.
- **Integration coverage:** Two integration scenarios that mocks alone can't prove —
  1. After a successful batch run on the currently-loaded session dataset, a subsequent `compute_phasor.execute(channel)` reads fresh calibration (not the stale snapshot). Covered by an end-to-end test that runs the orchestrator on a temp `.h5`, then runs `compute_phasor` and asserts the calibration was applied.
  2. After Cancel mid-batch, the cancelled `.h5` files have no `/decay` or `/metadata` mutations. Covered by inspecting cancelled datasets in a 3-item test with `cancel_check` triggering after item 1.
- **Unchanged invariants:** `add_decay_to_dataset` signature, `DatasetStore` API, `Session` state model, `CellDataModel.state_changed` signal, napari adapter, `ProjectIndex` schema. The new flow only writes `/decay/<ch>` and `/metadata.attrs` — same surfaces the single-dataset flow already touches.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Per-input-scope collapse (Bug-3 echo from batch-compress learnings) — orchestrator silently shares one `source_dir`/`calibration` across all items. | Carry `BatchAppendItem` as a typed triple end-to-end; never re-derive any field inside the loop. Unit test U2 explicitly asserts "2 distinct calibrations → 2 distinct `/metadata` records." |
| h5py library-level metadata cache returns stale values after `set_metadata` when the same `.h5` is also the active session. | Dialog-side `Session.dataset.metadata.update` after each successful item, gated on path match. Anywhere downstream re-reads calibration, route through `DatasetRepository.read_metadata(handle)` — never `handle.metadata.get(...)`. |
| GUI-thread loop freezes the dialog for the duration of one dataset's append (10–30s with large tile counts). | Accepted — matches compress's UX. `QProgressDialog.setValue(...)` between iterations implicitly drives Qt's event loop, so Cancel registers cleanly *between* datasets (verified by reading `_run_batch_compress` lines 720-795 — it relies on `setValue` for event processing, not an explicit `processEvents` call). *Within* a single `add_decay_to_dataset` call the dialog is frozen; that trade-off is identical to compress. Future migration to a worker is a separate decision. |
| Tall dialog overflows on small monitors. | Mandatory `wrap_in_scroll` + `cap_to_screen`. Compliance enforced by `tests/test_gui/test_dialog_helper_compliance.py`. |
| Pairing combo uniqueness invariant breaks when the user changes a combo programmatically. | Single `_on_pairing_changed` slot owns the invariant; gate it with a `_pairing_signal_silent` flag during programmatic mutations (Auto-pair, "clear conflicting"). |
| First GUI consumer of `ProjectIndex` reveals an API mismatch. | Treat `ProjectIndex.load()` DataFrame's `path` column as authoritative. Fallback to `reconcile(project_dir)['orphan_files']` only behind an explicit "discover untracked" toggle (deferred unless real-world need surfaces). |
| Audit-driven retrieval hook (`scripts/claude_code_hooks/check_learnings_retrieval.py`) warns on edits to T1 files; the warning shouldn't block. | Already warn-only per `CLAUDE.md` R15/R16. Verify it doesn't surface false positives on the new files; if it does, update `docs/audits/canonical-sources-matrix.yaml` to mark the new files as canonical sources themselves. |

---

## Documentation / Operational Notes

- After landing, capture two `/ce-compound` entries:
  1. **The QThread-vs-GUI-thread loop decision** with the rationale and the compress template reference (institutional learnings currently lack this).
  2. **Pairing combo uniqueness invariant** as a UI pattern (manual N-to-N pairing with uniqueness is also new in this codebase).
- Update `docs/audits/canonical-sources-matrix.yaml` to register `batch_add_decay.py` and `calibration_csv.py` as canonical sources for batch-decay-append and FLIM-calibration-CSV-parsing respectively, so future work routes through them.
- No user-facing docs/help text in this codebase; the dialog labels are the documentation surface.
- No migration, rollout, or feature flag — additive feature behind a new launcher button.

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-12-batch-tcspc-append-requirements.md`
- **Reused engine:** `src/percell4/application/use_cases/add_decay_to_dataset.py`
- **Template for batch loop:** `src/percell4/interfaces/gui/main_window.py:_run_batch_compress` (lines 720-795)
- **Template for dialog form:** `src/percell4/gui/compress_dialog.py`
- **Template for TCSPC widget set:** `src/percell4/gui/add_layer_dialog.py` (TCSPC tab, lines 1420-1707)
- **Institutional learnings consulted:**
  - `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  - `docs/solutions/logic-errors/batch-compress-development-lessons.md`
  - `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`
  - `docs/solutions/architecture-patterns/decay-write-path.md`
  - `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  - `docs/solutions/architecture-patterns/atomic-write-contract.md`
  - `docs/solutions/architecture-patterns/channel-deletion-permanence.md`
  - `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`
  - `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`
- **Audit artifacts touched:** `docs/audits/gui-element-classification.yaml`, `docs/audits/session-mutation-graph.md`, `docs/audits/canonical-sources-matrix.yaml`
