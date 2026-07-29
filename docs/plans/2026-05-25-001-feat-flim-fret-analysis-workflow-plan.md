---
title: "feat: FLIM-FRET analysis workflow"
type: feat
status: completed
date: 2026-05-25
origin: docs/brainstorms/2026-05-25-flim-fret-analysis-requirements.md
---

# feat: FLIM-FRET analysis workflow

## Overview

Add a new **FLIM-FRET analysis** button to the Workflows sidebar. Clicking it opens a modal setup dialog where the user discovers `.h5` datasets in a folder, builds donor / donor+acceptor (DA) pairs in a pair table, configures per-pair mask / phasor / lifetime layers (and segmentations when single-cell mode is on), and runs the analysis. The workflow is the first in PerCell4 that fuses information across two datasets: per pair, it computes the mean lifetime within `(donor_mask ∩ donor_phasor)` and `(da_mask ∩ da_phasor)`, optionally separated by cell labels, then emits one combined CSV containing the donor reference, DA mean, and FRET efficiency per row.

The implementation reuses every available scaffold — frozen-dataclass configs (`workflows/models.py`), atomic write helpers (`workflows/artifacts.py:write_atomic`, `create_run_folder`), the run-log JSONL pattern (`workflows/run_log.py`), the pair-table dialog pattern from `gui/batch_tcspc_dialog.py`, the main-thread + QProgressDialog driver pattern, and the dialog helpers (`gui/_dialog_utils.py:wrap_in_scroll` + `cap_to_screen`) enforced by `tests/test_gui/test_dialog_helper_compliance.py`. The orchestrator stays Qt-free so it can be tested with `pytest` alone.

---

## Problem Frame

PerCell4 already computes phasor maps, lifetime channels, and masks per dataset, but FLIM-FRET comparison — donor-only vs donor+acceptor — has to be done in spreadsheets outside the app. Researchers want a one-click workflow that pairs datasets, pre-screens for required layers, computes mean lifetimes within the intersection of two masks, optionally separates by cells, and writes a single CSV with `fret_efficiency = 1 − (DA mean / donor mean)`. See origin: `docs/brainstorms/2026-05-25-flim-fret-analysis-requirements.md`.

---

## Requirements Trace

- R1. New **FLIM-FRET analysis** button in the Workflows sidebar of the launcher (origin §"Entry point").
- R2. Modal setup dialog with: global single-cell toggle, source folder picker, output parent folder picker, pair table (pair_name + donor `.h5` + DA `.h5` + Configure), Add/Remove pair, Start/Cancel.
- R3. Source-folder discovery pre-screens `.h5` files by suffix-based layer presence: dataset qualifies only if it has at least one `/masks/<name>` ending in `_mask`, at least one `/masks/<name>` ending in `_phasor`, and at least one `/intensity` channel name ending in `_lifetime`. Single-cell mode additionally requires at least one `/labels/<name>`.
- R4. Per-pair Configure sub-dialog exposes dropdowns for donor mask, donor phasor, donor lifetime, DA mask, DA phasor, DA lifetime, and (when single-cell) donor segmentation + DA segmentation. Dropdowns are unfiltered (show ALL `/masks/` entries, ALL `/intensity` channels, ALL `/labels/` entries) but each carries helper text guiding the user to pick a `_mask` / `_phasor` / `_lifetime` / segmentation layer.
- R5. Start is disabled until: every pair has both datasets selected, donor ≠ DA within a pair, every Configure dropdown has a value, every pair name is unique and non-empty, and single-cell mode (when on) has segmentations on both sides of every pair.
- R6. Pure-Python orchestrator processes pairs sequentially. Per pair: build effective mask = `(mask > 0) & (phasor > 0)`, then in single-cell mode segment both sides, compute per-cell means, pool donor cell means into one reference, emit per-DA-cell rows; in whole-field mode emit one row per pair using whole-field means on each side.
- R7. Single combined output CSV at `<output>/flim_fret_run_<UTC-timestamp>_<uuid>/flim_fret_results.csv`, written via `write_atomic`. Columns in fixed order: `pair_name, donor_dataset, da_dataset, cell_id, donor_mean_lifetime, da_mean_lifetime, fret_efficiency, n_pixels_donor, n_pixels_da, n_cells_donor_reference, n_da_cells_skipped`. Empty string (not `NaN`) for blank identity cells (e.g., `cell_id` in whole-field mode); `n_da_cells_skipped` is per pair and repeats across that pair's rows in single-cell mode, blank in whole-field mode.
- R8. Run log at `<run_folder>/run_log.jsonl` via `workflows/run_log.py:RunLog`, recording per-pair status, skipped cells (with `n_da_cells_skipped` count), missing layers, and cancellation.
- R9. Cancellation between pairs (not within a pair). All completed pairs are included in the single end-of-run atomic CSV write; cancelled pairs appear in the run log with `status="cancelled"` but emit no CSV rows.
- R10. Workflow is an **Action** per `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`: never mutates `session.active_*`, never adds layers to napari, only reads from disk and writes a CSV.

---

## Scope Boundaries

- **Not included:** plotting FRET efficiency in-app (CSV is the output); per-pixel FRET maps stored back into `.h5`; persisting FRET pair configurations across sessions; per-pair single-cell toggle; clamping negative or > 1 FRET values; per-pair CSV files; auto-pairing from filename conventions; cross-dataset image registration.
- **Not included:** non-FLIM FRET methods (ratiometric intensity FRET, acceptor photobleaching); reference donor lifetimes pulled from a library config.
- **Not included (data shape):** time-lapse datasets where `/intensity` is `(T, C, H, W)`. The orchestrator operates on single-timepoint data only. Time-lapse datasets are rejected at pre-screening with reason `"time-lapse /intensity unsupported"`; per-pair runtime revalidation re-checks the same predicate so a dataset re-imported as time-lapse between dialog accept and run start is captured as `status="error"` and the pair is skipped.

### Deferred to Follow-Up Work

- Pair-name auto-suggestion based on filename prefix (origin Open Question #3): follow-up PR after baseline workflow ships.
- Minimum-pixels-per-cell QC threshold (origin Open Question #4): follow-up PR.
- Registering `compute_flim_fret` / `run_flim_fret` in `docs/audits/canonical-sources-matrix.yaml` once stable: separate housekeeping PR.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/gui/batch_tcspc_dialog.py` — pair-table pattern, QFileDialog discovery, QProgressDialog driver loop, cancellation via `progress.wasCanceled()`, summary view after run. The closest analog to this workflow.
- `src/percell4/gui/workflows/single_cell/config_dialog.py` — frozen-config-dataclass dialog conventions, QSettings persistence (`LeeLabPerCell4` / `PerCell4` org/app keys), validation-before-accept, `_warn(...)` for `ValueError` surfacing, sub-dialog launch via inline `QDialog(self)`.
- `src/percell4/gui/_dialog_utils.py` — `wrap_in_scroll(content)` + `cap_to_screen(self)`. Mandatory; enforced by `tests/test_gui/test_dialog_helper_compliance.py`.
- `src/percell4/application/use_cases/batch_add_decay.py` — pure-Python orchestrator shape: frozen input dataclasses, `progress_callback` + `cancel_check` kwargs, per-item `try/except` keeps batch going, returns a frozen aggregated report.
- `src/percell4/workflows/artifacts.py` — `write_atomic(path, writer_fn)` (tmp + fsync + os.replace + parent fsync) and `create_run_folder(output_parent, prefix="run")` (UTC ISO timestamp + shortuuid suffix).
- `src/percell4/workflows/run_log.py` — `RunLog(folder)` appends JSONL events with UTC timestamps.
- `src/percell4/workflows/models.py` — frozen-dataclass conventions, `__post_init__` validation, `StrEnum` for closed sets, `Path` for filesystem fields, JSON round-trip helpers.
- `src/percell4/store.py` — `DatasetStore` public API: `list_masks()`, `read_mask(name, view_bin=1)`, `list_labels()`, `read_labels(name, view_bin=1)`, `metadata["channel_names"]` (channel-name list for `/intensity`), `read_channel("intensity", channel_idx, view_bin=1)`, `read_array("intensity", view_bin=1)`. Within-dataset `native_shape` is locked at `view_bin=1`, so all selected layers in one dataset share dimensions by construction (`LayerSizeMismatchError` at write time).
- `src/percell4/interfaces/gui/main_window.py:329` (`_create_workflows_panel`) — entry point for the new button. `is_workflow_locked` reentrance guard pattern at `main_window.py:367-419`. `_run_batch_compress` at lines 859-945 — canonical main-thread + QProgressDialog driver loop without a `_active_workflow_runner` slot.
- `src/percell4/application/use_cases/compute_lifetime.py:27` — lifetime channel naming `f"{src}_{source}_lifetime"`. The only true suffix convention in the codebase today.

### Institutional Learnings

- **`docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`** — when reading multiple `/masks/`, `/intensity`, `/phasor/` layers from one dataset, mask ∩ phasor ∩ lifetime must come from the same `native_shape` envelope. Already guaranteed by `store.py:_validate_layer_shape` at `view_bin=1`, but worth asserting at the top of per-pair compute as defensive guard.
- **`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`** — workflows that iterate across `.h5` files in the same process must do fresh disk reads per dataset. The orchestrator opens each `.h5` per-pair via `DatasetStore(path).open_read()`; it does not reuse any `Session` state.
- **`docs/solutions/architecture-patterns/atomic-write-contract.md`** — use `write_atomic`, do not roll a third implementation. Tmp file in same parent dir as final path.
- **`docs/solutions/ui-bugs/dialog-scroll-when-tall.md`** — `wrap_in_scroll` + `cap_to_screen` mandatory for every `gui/*_dialog.py`, enforced by AST-walking CI test.
- **`docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`** — every UI element is exactly one of Selector / Creator / Action. The Workflows button and the dialog's Run button are Actions: they read session if needed and write a CSV; they never mutate `session.active_*` or add napari layers.
- **`docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`** — every interactive widget that participates in validation must have its `currentIndexChanged` / `toggled` / `editingFinished` / `textChanged` signal wired to the `_validate_and_update_run_button` slot at construction. Programmatic setters in tests bypass these signals.
- **`docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`** — string-match layer names against actual HDF5 keys, not GUI display text. Pre-flight resolution + validation before any compute.
- **`docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`** — when `FlimFretConfig` carries new fields, every call site that materializes the config and every use-case entry must forward them. Add an end-to-end test that runs dialog → config → orchestrator → CSV path and asserts the toggle's value reaches the output.
- **`docs/solutions/logic-errors/batch-compress-development-lessons.md`** — discovery produces scoped subsets; the per-pair worker must receive both explicit `.h5` paths and operate strictly on those. Never pass the parent folder and re-scan inside the worker.
- **`docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md`** — for I/O-bound batch ops on external drives, main-thread + QProgressDialog is the documented PerCell4 pattern, not QThread workers. Pull dialog values into local variables immediately after `exec_()` returns.
- **`docs/solutions/conventions/numpy-isin-fails-with-python-sets.md`** — `np.isin(labels, set_obj)` returns all-False under NumPy 2.x; convert to `np.array(list(set))` first if the orchestrator builds cell-pool masks this way.

### External References

None required. Local patterns cover every layer of this workflow.

---

## Key Technical Decisions

- **Pure-Python orchestrator in `application/use_cases/`.** Qt-free, returns a frozen report; the dialog drives progress, cancellation, and CSV writing. Mirrors `batch_add_decay.py`. Makes unit testing trivial.
- **Main-thread + QProgressDialog, no `BaseWorkflowRunner`.** No interactive QC phases, only sequential per-pair compute. The runner-host scaffolding (`gui/workflows/base_runner.py`) is built for generator-driven interactive workflows; reusing it would be over-fitting.
- **Pre-screening at dataset discovery, not at dropdown filter.** A dataset qualifies for selection only if it has at least one mask name ending in `_mask`, at least one mask name ending in `_phasor`, and at least one channel name ending in `_lifetime` (plus `/labels/` ≥ 1 when single-cell). Non-qualifying datasets are excluded from donor/DA dropdowns and surfaced in a "datasets excluded" warning summary with the reason per dataset. Inside the Configure sub-dialog, dropdowns show ALL relevant entries (unfiltered) with helper text indicating the suffix the workflow expects.
- **Mask intersection semantic on multi-label masks:** `(mask_a > 0) & (mask_b > 0)`. Mask arrays under `/masks/` are uint8 and may be multi-label ROI masks.
- **Background label exclusion:** when segmenting, `cell_ids = unique(labels) \ {0}`. Label 0 is the background convention used by Cellpose and elsewhere in the codebase.
- **Run folder reuses `create_run_folder` with prefix `flim_fret_run` and no auto-created subdirs.** Yields `<output_parent>/flim_fret_run_<UTC-ISO>_<uuid>/`. The current helper (`workflows/artifacts.py:create_run_folder`) accepts only `output_parent`, hardcodes `run_<ts>_<uuid>`, and unconditionally creates `per_dataset/` and `staging/` (relics of the single-cell workflow). This plan extends the signature to `create_run_folder(output_parent: Path, *, prefix: str = "run", create_subdirs: bool = True) -> Path` — defaults preserve existing behavior, so single-cell callers stay unchanged. FLIM-FRET passes `prefix="flim_fret_run"` and `create_subdirs=False`. Same atomic helper for the CSV.
- **Frozen dataclass invariants in `__post_init__`:** pair names are unique and non-empty; donor `.h5` ≠ DA `.h5` within a pair; in single-cell mode every pair has segmentation paths on both sides.
- **Same `.h5` file may be reused across pairs.** A donor reference dataset paired against multiple DA samples is legitimate. Only within-pair donor==DA is blocked.
- **CSV float format and NaN handling:** `to_csv(... index=False, float_format="%.6g", na_rep="", encoding="utf-8", lineterminator="\n")`. Identity columns blank as empty string. Negative or > 1 FRET values are reported as-is. The only division-guard is `donor_mean == 0` (or NaN): if the donor reference is exactly zero or undefined, `fret_efficiency = NaN`; otherwise compute `1 − (da / donor)` and emit whatever NumPy returns (including negative values when `da > donor` and including > 1 values when `da < 0`). This is symmetric with the stated "no clamping" policy.
- **Per-pair revalidation at run-time.** Even though dialog accept validates layer existence, the orchestrator re-opens each `.h5` and re-checks selected layers before computing — if a layer vanished between dialog accept and run, the pair logs `missing_layer:<name>` and the run continues with the next pair.
- **Donor reference fallback when pool is empty.** If `n_cells_donor_reference == 0` (single-cell) or `n_pixels_donor == 0` (whole-field), the pair emits its row(s) with NaN values and the run log records `donor_reference_empty`. Run continues.
- **Determinism in CSV row order:** rows are ordered by pair index (the order they appear in the pair table), then by ascending integer cell label within a pair (single-cell mode).
- **QSettings keys:** `flim_fret_workflow/output_parent`, `flim_fret_workflow/source_folder` under org `LeeLabPerCell4`, app `PerCell4`.

---

## Open Questions

### Resolved During Planning

- **How to populate mask / phasor dropdowns given missing suffix conventions?** Pre-screen datasets by suffix at discovery (R3); inside Configure show all entries unfiltered with helper text. Datasets without all required suffix-tagged layers are excluded from selection with a warning summary.
- **Run-folder timestamp format?** Reuse `create_run_folder(output_parent, prefix="flim_fret_run")` for UTC + uuid suffix; do not invent a second format.
- **Same `.h5` reused across pairs?** Allowed across pairs; forbidden within a pair (donor ≠ DA).
- **Cancellation mid-run?** Cancel between pairs; partial results from completed pairs are written via the atomic helper; in-flight pair marked `cancelled` in run log; run folder kept.
- **Background label exclusion?** Yes — `cell_ids = unique(labels) \ {0}`.
- **CSV row ordering in single-cell mode?** `(pair_index_in_table, cell_id_ascending)`.

### Deferred to Implementation

- **Exact widget layout inside the Configure sub-dialog** (single-column with section headers vs. two-column donor/DA side-by-side) — both meet the requirement; pick whichever fits screen height after `wrap_in_scroll` + `cap_to_screen`.
- **Helper-text wording near each dropdown** — the convention is "Select a `_mask` layer", "Select a `_phasor` layer", etc.; final phrasing chosen during implementation.
- **Whether the orchestrator opens each pair's two `.h5` files with `with store.open_read()` context managers or short-lived per-read opens** — both work; choose by profiling if needed.
- **Whether the dialog's pair-uniqueness check uses `Path.resolve()` or string-equality** — resolve is safer (handles symlinks and `..`); decide at implementation if `Path.samefile` is preferred.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
Launcher                  Dialog                            Orchestrator                  Filesystem
   |                        |                                    |                            |
   |-- click FLIM-FRET ----->|                                    |                            |
   |                        |-- pick source folder, single-cell --|                            |
   |                        |-- discover .h5 + pre-screen suffixes ----------------------------> (read /masks, /intensity, /labels)
   |                        |   (exclude unqualifying with reasons)                            |
   |                        |-- Add pair, Configure (sub-dialog) -|                            |
   |                        |   ...build pair table...           |                            |
   |                        |-- pick output parent ---------------|                            |
   |                        |-- Start (validates frozen config) --|                            |
   |                        |                                    |                            |
   |                        |-- QProgressDialog(0..N pairs)      |                            |
   |                        |   for pair in pairs:               |                            |
   |                        |     run_flim_fret_pair(pair) ----->|                            |
   |                        |                                    |--- re-validate layers ---->|
   |                        |                                    |--- read masks, phasor ----->|
   |                        |                                    |--- intersect, mean lifetime ->|
   |                        |                                    |    (single-cell: pool donor) |
   |                        |                                    |<-- per-pair rows -----------|
   |                        |     progress_callback(pair, result)|                            |
   |                        |     cancel_check() between pairs    |                            |
   |                        |                                    |                            |
   |                        |-- create_run_folder(...) ----------------------------------------> mkdir
   |                        |-- write_atomic(results.csv, df.to_csv) -------------------------> tmp + replace
   |                        |-- RunLog(...).log(...)                                          -> jsonl append
   |                        |-- show summary, close                                            |
```

Pre-screen rule (per dataset, evaluated at discovery and re-checked at run-time):

```
qualifies(store) :=
  any(n.endswith("_mask")    for n in store.list_masks())
  and any(n.endswith("_phasor")  for n in store.list_masks())
  and any(c.endswith("_lifetime") for c in store.metadata["channel_names"])
  and (not single_cell or len(store.list_labels()) >= 1)
```

Per-pair compute (whole-field mode):

```
donor_mask  = store_donor.read_mask(pair.donor_mask, view_bin=1)
donor_phas  = store_donor.read_mask(pair.donor_phasor, view_bin=1)
donor_eff   = (donor_mask > 0) & (donor_phas > 0)

donor_names = store_donor.metadata["channel_names"]
donor_idx   = donor_names.index(pair.donor_lifetime)   # ValueError → status="missing_layer"
donor_chan  = store_donor.read_channel("intensity", donor_idx, view_bin=1)

donor_mean  = mean(donor_chan[donor_eff])  if donor_eff.any() else NaN

(same for DA side)

fret_efficiency = NaN  if donor_mean == 0 or isnan(donor_mean) else 1 - (da_mean / donor_mean)
```

Per-pair compute (single-cell mode):

```
donor_labels = store_donor.read_labels(pair.donor_seg, view_bin=1)
donor_cell_means = []
for cid in unique(donor_labels) \ {0}:
    mask = donor_eff & (donor_labels == cid)
    if mask.any():
        donor_cell_means.append(mean(donor_chan[mask]))
donor_ref = mean(donor_cell_means) if donor_cell_means else NaN

(same per-cell loop on DA; emit one row per DA cell with donor_ref repeated)
```

---

## Implementation Units

- U1. **Frozen config and result dataclasses**

**Goal:** Define the data shape that flows from the dialog to the orchestrator and back, with self-validating invariants.

**Requirements:** R5, R7, R10.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/workflows/models.py`
- Test: `tests/test_workflows/test_flim_fret_models.py` (new)

**Approach:**
- Add `FlimFretPair(frozen)` with fields: `name: str`, `donor_h5: Path`, `da_h5: Path`, `donor_mask: str`, `donor_phasor: str`, `donor_lifetime: str`, `da_mask: str`, `da_phasor: str`, `da_lifetime: str`, `donor_segmentation: str | None`, `da_segmentation: str | None`.
- Add `FlimFretConfig(frozen)` with fields: `pairs: list[FlimFretPair]`, `single_cell: bool`, `output_parent: Path`.
- `FlimFretConfig.__post_init__` validates: `len(pairs) >= 1`; pair names non-empty, unique; for each pair `donor_h5.resolve() != da_h5.resolve()`; when `single_cell` is True every pair has non-None segmentation on both sides.
- Add `FlimFretPairResult(frozen)` with: `pair: FlimFretPair`, `status: str` (one of `"succeeded"`, `"cancelled"`, `"missing_layer"`, `"dataset_open_failed"`, `"donor_reference_empty"`, `"error"`), `reason: str | None`, `rows: list[dict]` (the CSV row payloads emitted by this pair), `n_pixels_donor: int`, `n_cells_donor_reference: int`, `n_da_cells_skipped: int`. Add `FlimFretReport(frozen)` with: `results: list[FlimFretPairResult]`, `run_folder: Path | None`.
- Use the existing `ValueError`-raising idiom so the dialog's `_warn` surfaces problems.

**Patterns to follow:**
- `src/percell4/workflows/models.py` for the dataclass + StrEnum + `__post_init__` conventions.

**Test scenarios:**
- Happy path: build a `FlimFretConfig` with two pairs (whole-field) and assert frozen-ness + field values round-trip.
- Edge case: empty `pairs` list raises `ValueError` with "at least one pair required".
- Edge case: two pairs with identical `name` raises `ValueError` naming the duplicate.
- Edge case: pair with `donor_h5` and `da_h5` resolving to the same path raises `ValueError`.
- Edge case: `single_cell=True` with any pair missing `donor_segmentation` or `da_segmentation` raises `ValueError`.
- Edge case: blank/whitespace-only pair `name` raises `ValueError`.

**Verification:**
- All invariants raise `ValueError` with messages naming the offending pair.
- Frozen dataclass rejects attribute assignment (covered by frozen semantics — sanity test).

---

- U2. **Dataset eligibility / pre-screening helper**

**Goal:** Encapsulate the suffix-based qualification rule and the layer enumeration helpers used by the dialog and orchestrator.

**Requirements:** R3, R4.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/application/use_cases/flim_fret_discovery.py`
- Test: `tests/test_application/test_flim_fret_discovery.py` (new)

**Approach:**
- `def discover_flim_fret_candidates(source_folder: Path, *, single_cell: bool) -> list[DatasetCandidate]` non-recursive `*.h5` + `*.hdf5` glob.
- `DatasetCandidate` (frozen): `path: Path`, `qualifies: bool`, `reasons: list[str]` (empty if qualifies; otherwise human-readable reasons like `"no /masks/<*>_mask"`, `"no /masks/<*>_phasor"`, `"no /intensity/*_lifetime channel"`, `"no /labels/* (required for single-cell)"`, `"time-lapse /intensity unsupported"`, `"open failed: <exc>"`).
- For each candidate, open via `DatasetStore(path)` inside a try/except; populate the booleans without raising. Determine time-lapse by reading `/intensity` shape: if `ndim == 4` it is `(T, C, H, W)` → reject.
- Provide a single helper for the dialog dropdowns: `list_lifetime_channel_names(store)` returns `[c for c in metadata["channel_names"] if c.endswith("_lifetime")]`. The mask and phasor dropdowns consume `store.list_masks()` directly — no wrapper, since neither filtering nor renaming is needed inside the Configure sub-dialog (per R4, dropdowns are unfiltered with helper text).
- Provide `validate_pair_layers(pair: FlimFretPair, single_cell: bool) -> list[str]` returning a list of missing-layer reasons, evaluated against the live `.h5` (called by the orchestrator at the top of per-pair compute). Checks mask names against `store.list_masks()`, segmentation names against `store.list_labels()`, lifetime channel names against `store.metadata["channel_names"]`, and re-validates that `/intensity` is not time-lapse.

**Patterns to follow:**
- `src/percell4/gui/workflows/single_cell/config_dialog.py:_read_h5_channels` for opening a store and tolerating errors.
- `src/percell4/application/use_cases/batch_add_decay.py:validate_batch_inputs` for the validation-as-pure-function shape.

**Test scenarios:**
- Happy path: a folder containing one qualifying `.h5` (has `foo_mask`, `bar_phasor`, `ch0_unfiltered_lifetime`) returns one candidate with `qualifies=True, reasons=[]`.
- Happy path: helper `list_lifetime_channel_names` returns only entries ending in `_lifetime` from `metadata["channel_names"]`.
- Edge case: dataset with `_mask` and `_lifetime` but no `_phasor` returns `qualifies=False`, reasons includes `"no /masks/<*>_phasor"`.
- Edge case: `single_cell=True` but dataset has empty `/labels/` returns `qualifies=False`, reasons includes the single-cell-specific message.
- Edge case: dataset whose `/intensity` is 4D (time-lapse) returns `qualifies=False`, reasons includes `"time-lapse /intensity unsupported"`.
- Edge case: `.h5` that raises on open (corrupted, missing `/metadata`) returns `qualifies=False, reasons=["open failed: <exc message>"]` and does not raise to the caller.
- Edge case: empty folder returns `[]`.
- Edge case: folder with `.h5.bak` or other non-target extensions ignored.
- Integration: `validate_pair_layers` against a fresh `DatasetStore` after a mask layer is deleted between dialog accept and run time returns `["missing donor mask 'foo_mask'"]`.
- Integration: `validate_pair_layers` when `pair.donor_lifetime` is not in `metadata["channel_names"]` returns `["missing donor lifetime channel 'ch0_unfiltered_lifetime'"]`.

**Verification:**
- Discovery never raises (errors are captured in `reasons`).
- Suffix matching is `str.endswith` and case-sensitive (matches the codebase's existing layer-name conventions).

---

- U3. **Pure-Python orchestrator `run_flim_fret`**

**Goal:** Qt-free orchestrator that processes pairs sequentially, returns per-pair results, supports progress callback and cancel check.

**Requirements:** R6, R7, R8, R9.

**Dependencies:** U1, U2.

**Files:**
- Create: `src/percell4/application/use_cases/run_flim_fret.py`
- Test: `tests/test_application/test_run_flim_fret.py` (new)

**Approach:**
- Top-level function: `def run_flim_fret(config: FlimFretConfig, *, progress_callback: Callable[[FlimFretPair, FlimFretPairResult], None] | None = None, cancel_check: Callable[[], bool] | None = None, run_log: RunLog | None = None) -> FlimFretReport`.
- For each pair (in order):
  1. If `cancel_check` returns True, append `status="cancelled"` results for remaining pairs (no compute) and break.
  2. Re-validate via `validate_pair_layers`; on failure, build result with `status="missing_layer"`, NaN values, log to `run_log`, call `progress_callback`, continue.
  3. Open donor and DA `DatasetStore` (lightweight, per-pair). Reject time-lapse `/intensity` (`ndim == 4`) here as `status="error"` with reason `"time-lapse /intensity unsupported"`.
  4. Resolve each side's lifetime channel name to a channel index via `metadata["channel_names"].index(name)`. A missing name yields `status="missing_layer"` with the offending name in the reason.
  5. Read donor `_mask`, `_phasor`, lifetime channel slice; build `donor_eff = (mask > 0) & (phasor > 0)`.
  6. If `config.single_cell`: read donor segmentation, compute per-cell donor means via `unique(labels) \ {0}`, pool into one donor reference.
  7. Otherwise: donor reference = `mean(donor_lifetime[donor_eff])` (NaN if mask is empty).
  8. Read DA side identically.
  9. Single-cell mode: iterate DA cells, emit row per cell with `donor_mean_lifetime` repeated, `da_mean_lifetime` per cell, `fret_efficiency` per cell. Whole-field mode: emit one row per pair.
  10. `fret_efficiency = NaN` when `donor_mean == 0` or `isnan(donor_mean)`; otherwise `1 - (da_mean / donor_mean)` reported as-is (no clamping).
  11. Track `n_da_cells_skipped` per pair: count of DA cell labels that had zero pixels in `da_eff` (single-cell mode only; blank/0 in whole-field mode).
  12. Append rows to a list inside the result; call `progress_callback(pair, result)`; log to `RunLog` with `n_da_cells_skipped` and `n_cells_donor_reference` in the per-pair finish event.
- Per-pair `try/except`: any unexpected exception captured into `FlimFretPairResult(status="error", ...)`; log with traceback via `logger.exception`; continue.
- Returns `FlimFretReport(results=..., run_folder=None)`. The dialog (not the orchestrator) creates the run folder and writes the CSV.
- Use `view_bin=1` everywhere; never look at `session.active_bin`.
- Defensive assertion at the top of per-pair compute: donor `mask.shape == phasor.shape == lifetime.shape`, and (in single-cell mode) `donor_labels.shape == donor_mask.shape` and `da_labels.shape == da_mask.shape`. Already enforced by `store.py` `_validate_layer_shape` at write time; assert anyway to catch hand-edited or import-bug `.h5` files. `AssertionError` is captured as `status="error"` with the shape mismatch in the reason.

**Execution note:** Test-first for the math. Build fixture `.h5` files with known per-pixel lifetimes, then assert exact CSV values against hand-computed expectations.

**Patterns to follow:**
- `src/percell4/application/use_cases/batch_add_decay.py` — orchestrator shape, progress + cancel kwargs, per-item exception isolation.
- `docs/solutions/conventions/numpy-isin-fails-with-python-sets.md` — if pooling uses `np.isin`, convert sets to lists or use array-typed input.

**Test scenarios:**
- Happy path (whole-field): one pair with handcrafted donor lifetime = 2.5 ns and DA lifetime = 2.0 ns across the masked region returns `fret_efficiency = 0.2` and one row.
- Happy path (single-cell): donor has two cells (means 2.5 and 2.4), DA has three cells (means 2.0, 2.1, 1.9). Donor reference = 2.45. Three DA rows with `fret_efficiency = 1 - 2.0/2.45`, `1 - 2.1/2.45`, `1 - 1.9/2.45`. Cells ordered by ascending integer label.
- Edge case: pair with empty mask intersection (no pixels survive `mask & phasor`) emits one row with NaN donor mean and NaN FRET in whole-field; in single-cell mode emits NaN-donor + NaN-FRET rows for every DA cell.
- Edge case: pair where every donor cell has empty effective intersection (donor reference pool empty) → `status="donor_reference_empty"`, NaN values, run continues to next pair.
- Edge case: DA cell with zero valid pixels emits a row with NaN `da_mean_lifetime` and NaN `fret_efficiency`, `n_pixels_da = 0`.
- Edge case: background label 0 is excluded from the cell loops on both sides.
- Edge case: multi-label mask values (e.g., 2, 3 as cell labels in a mask used as `_mask`) intersected with phasor mask via `> 0` boolean coercion — covered pixels match those with both masks non-zero.
- Edge case: negative `donor_mean_lifetime` from upstream noise → `fret_efficiency = NaN` for that pair.
- Error path: per-pair `DatasetStore` open raises `OSError` (file locked) → `status="dataset_open_failed"`, run continues.
- Error path: a layer named in the pair was deleted between dialog accept and run start → `validate_pair_layers` returns missing-layer reasons, status `"missing_layer"`, run continues.
- Error path: dataset's `/intensity` is 4D at run time (re-imported as time-lapse between dialog accept and Start) → `status="error"`, reason includes `"time-lapse /intensity unsupported"`, run continues.
- Error path: lifetime channel name not in `metadata["channel_names"]` at run time → `status="missing_layer"`, run continues.
- Error path: donor or DA `donor_labels.shape != donor_mask.shape` in single-cell mode → `AssertionError` captured as `status="error"`, run continues.
- Integration: single-cell pair with two DA cells where one has zero pixels in `da_eff` → emits two DA rows (one valid, one NaN), `n_da_cells_skipped == 1` on both rows of that pair.
- Integration: `cancel_check` returns True after pair 1 of 3 → pair 1 returns `succeeded`, pairs 2 and 3 return `cancelled` with no compute attempted; report contains all three results.
- Integration: `progress_callback` called exactly once per pair, in order, with the matching `FlimFretPairResult`.
- Integration: `RunLog` receives one `run_started` event, one event per pair start, one event per pair finish/error/cancel, one `run_finished` event. Verify by reading back the JSONL file.

**Verification:**
- Orchestrator is Qt-free (no `from PyQt5` / `qtpy` imports).
- Per-pair exceptions never abort the batch.
- Cancellation honored between pairs; in-flight compute uninterruptible (matches the documented PerCell4 pattern for I/O-bound batch ops).
- Run log records `donor_reference_empty`, `missing_layer:<name>`, `dataset_open_failed`, `cancelled`, `error`, `succeeded` per pair.

---

- U4. **Setup dialog with pair table and Configure sub-dialog**

**Goal:** Modal `QDialog` that drives source-folder discovery, builds the pair table, hosts the Configure sub-dialog per pair, validates, drives the QProgressDialog run loop, atomically writes the combined CSV.

**Requirements:** R1, R2, R3, R4, R5, R7, R8, R9, R10.

**Dependencies:** U1, U2, U3.

**Files:**
- Create: `src/percell4/gui/flim_fret_dialog.py`
- Modify: `src/percell4/workflows/artifacts.py` (extend `create_run_folder` with optional `prefix: str = "run"` and `create_subdirs: bool = True` kwargs; defaults preserve existing single-cell caller behavior)
- Test: `tests/test_gui/test_flim_fret_dialog.py` (new)
- Test: `tests/test_workflows/test_artifacts.py` (extend with cases for the new `prefix` and `create_subdirs` kwargs)

**Approach:**
- `FlimFretDialog(QDialog)` with:
  - Top: "Single-cell analysis" `QCheckBox`, "Source folder" picker (line edit + browse button), "Output parent folder" picker, both backed by `QSettings("LeeLabPerCell4", "PerCell4")` keys `flim_fret_workflow/source_folder` and `flim_fret_workflow/output_parent`.
  - Middle: pair `QTableWidget` with columns `pair_name | donor .h5 | DA .h5 | Configure`. Donor and DA columns are `QComboBox` cells listing only qualifying datasets discovered in the source folder. Configure column is a `QPushButton` per row.
  - Bottom: "Add pair" / "Remove pair" / "Start" / "Cancel" buttons.
  - Content widget wrapped in `wrap_in_scroll`; `cap_to_screen(self)`.
- Source-folder change handler calls `discover_flim_fret_candidates(source_folder, single_cell=self._is_single_cell())`. Refreshes donor/DA dropdown items. If any pair rows exist, surfaces a warn-and-confirm `QMessageBox` ("Changing source folder will clear N pair(s). Continue?"); on confirm, clears the table.
- Single-cell toggle handler re-runs discovery (because `/labels/` requirement changes) and marks each pair row needing reconfigure if it lacks segmentation picks. Previously-set non-segmentation picks are preserved; segmentation picks are retained across toggle so re-enabling restores them.
- "Add pair" appends a row with auto-generated empty `pair_name`, blank dropdowns; "Remove pair" removes the selected row and purges that pair's stored Configure state.
- Donor / DA `QComboBox` items carry the `Path` via `itemData(...)`. `currentIndexChanged` wired to validation; if the user picks a non-qualifying value (cannot happen with filtered dropdowns) or sets donor == DA, the row marks itself invalid with a tooltip.
- Configure button per row opens an inline `QDialog(self)` sub-dialog ("Configure pair: <pair_name>") with two columns (Donor / Donor+Acceptor) and per-column dropdowns:
  - Mask layer dropdown — populated from `store.list_masks()`. Helper text below: `"Select a layer whose name ends in '_mask'."`. Default selection: first entry ending in `_mask` (or first entry if none match).
  - Phasor layer dropdown — populated from `store.list_masks()` (same source). Helper text: `"Select a layer whose name ends in '_phasor'."`. Default selection: first entry ending in `_phasor`.
  - Lifetime channel dropdown — populated from `[c for c in store.metadata["channel_names"] if c.endswith("_lifetime")]`. Helper text: `"Select a derived lifetime channel."`.
  - When single-cell ON: segmentation dropdown per side — populated from `store.list_labels()`. Helper text: `"Select a segmentation layer."`.
  - OK / Cancel. OK enabled only when every required dropdown has a value. Cancel discards in-session changes.
  - Re-opening Configure pre-fills the prior accepted picks.
  - Sub-dialog content wrapped in `wrap_in_scroll`; `cap_to_screen(self)`.
- "Start" handler:
  1. Builds `FlimFretConfig` via `_try_build_config()`; on `ValueError` shows `QMessageBox.warning` (mirrors `single_cell/config_dialog.py:_warn`).
  2. Calls `create_run_folder(config.output_parent, prefix="flim_fret_run", create_subdirs=False)` (uses the extended kwargs added in Key Technical Decisions).
  3. Builds `RunLog(run_folder)`.
  4. Builds `QProgressDialog("Running FLIM-FRET…", "Cancel", 0, len(config.pairs), self)` with `Qt.WindowModal`, `setMinimumDuration(0)`.
  5. Calls `run_flim_fret(config, progress_callback=cb, cancel_check=lambda: progress.wasCanceled(), run_log=log)`.
  6. `progress_callback` increments value and sets label text `f"({n}/{N}) {pair.name} — {result.status}"`.
  7. Assembles a `pandas.DataFrame` from all per-pair rows. Columns in fixed order: `pair_name, donor_dataset, da_dataset, cell_id, donor_mean_lifetime, da_mean_lifetime, fret_efficiency, n_pixels_donor, n_pixels_da, n_cells_donor_reference`.
  8. Writes via `write_atomic(run_folder / "flim_fret_results.csv", lambda tmp: df.to_csv(tmp, index=False, float_format="%.6g", na_rep="", encoding="utf-8", lineterminator="\n"))`.
  9. Logs `run_finished` to `RunLog`.
  10. Shows a summary `QMessageBox` (counts of succeeded / cancelled / missing_layer / error / donor_reference_empty pairs, link to CSV path).
  11. `self.accept()`.
- Cancel mid-run: rows already produced are still written to the CSV (atomic helper); in-flight pair captured as `status="cancelled"` in the run log; summary reflects the partial outcome; run folder is kept.
- The dialog is an **Action**. It does not call `session.set_*`; it does not add napari layers.
- Every interactive widget (dropdowns, checkbox, line edits, table item edits) wires its user-edit signal to a `_validate_and_update_run_button` slot at construction (per `qt-wire-user-edit-signals`).

**Patterns to follow:**
- `src/percell4/gui/batch_tcspc_dialog.py` for pair-table + QProgressDialog + per-pair progress callback + cancellation.
- `src/percell4/gui/workflows/single_cell/config_dialog.py:_try_build_config`, `_warn`, `_save_output_setting`, `_capture_immediately` for the QSettings + freeze-on-accept sequence.
- `src/percell4/gui/_dialog_utils.py:wrap_in_scroll`, `cap_to_screen`.
- `src/percell4/interfaces/gui/main_window.py:_run_batch_compress` for the main-thread + QProgressDialog loop without `_active_workflow_runner` slot.

**Test scenarios:**
- Happy path (whole-field): qtbot constructs the dialog, monkeypatches `QFileDialog.getExistingDirectory` to return a fixture folder with two qualifying `.h5` files, programmatically adds one pair, opens Configure, picks `_mask` / `_phasor` / `_lifetime` for each side, programmatically clicks Start. Asserts a CSV file is produced at the expected location with exactly one row and computed-by-hand values.
- Happy path (single-cell): qtbot ticks single-cell toggle, fixture `.h5` files have `/labels/`, dialog adds a pair, opens Configure, picks segmentations + masks/phasor/lifetime, Start. CSV has one row per DA cell.
- Edge case: source folder with no `.h5` files shows the "(no datasets found)" empty state; donor/DA dropdowns are disabled.
- Edge case: source folder with one qualifying + one non-qualifying `.h5` shows the qualifying one in dropdowns and surfaces "1 dataset excluded: <name>: no /masks/<*>_phasor" in a status label.
- Edge case: user selects donor == DA in a pair — Start remains disabled, row shows tooltip "donor and DA must be different datasets".
- Edge case: user adds two pairs with identical `pair_name` — Start remains disabled.
- Edge case: user toggles single-cell ON after configuring a pair without segmentations — pair row gains a "(needs segmentation)" marker; Start disabled; toggling OFF restores Start; toggling ON again restores the prior segmentation picks.
- Edge case: user changes source folder after configuring two pairs — QMessageBox warn-and-confirm; on confirm, pair table is cleared.
- Edge case: user removes a pair row — that pair's Configure state is discarded.
- Edge case: user opens Configure, picks values, presses Cancel — prior accepted state preserved.
- Edge case: user opens Configure, accepts, opens again — dropdowns pre-fill prior picks.
- Edge case: user changes a donor `.h5` in a pair after Configure was accepted — Configure marked "(needs configure)" and Start disabled until reconfigured.
- Error path: output folder is read-only — Start surfaces `QMessageBox.warning` with the OSError from `create_run_folder`; dialog stays open.
- Error path: `.h5` file is locked by another reader — orchestrator returns `dataset_open_failed`, dialog still produces a CSV (for completed pairs) and shows that pair as failed in the summary.
- Error path: disk fills during CSV write — `write_atomic` re-raises and unlinks the tmp; dialog surfaces `QMessageBox.critical` with the path of the run log (which already contains pair events).
- Integration: progress callback is invoked once per pair with the matching status; cancellation between pairs writes a partial CSV containing only completed-pair rows.
- Integration: dialog helper compliance (`tests/test_gui/test_dialog_helper_compliance.py` AST walker) passes for the new file.
- Integration: dialog never mutates `session.active_*` (verify via spy on a mocked Session).
- Integration: every `QComboBox` and the single-cell `QCheckBox` has its user-edit signal connected at construction (introspect `receivers()` count or assert by spy).

**Verification:**
- Dialog passes the `wrap_in_scroll` + `cap_to_screen` AST check.
- CSV layout: header in fixed order; rows ordered by `(pair_index, cell_id_ascending)`; blank cells are empty strings.
- Cancellation produces a partial CSV plus a complete-enough run log.

---

- U5. **Launcher button wiring**

**Goal:** New **FLIM-FRET analysis** button in the Workflows sidebar that opens the dialog, respecting the launcher's reentrance guard.

**Requirements:** R1.

**Dependencies:** U4.

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Test: `tests/test_gui/test_workflows_panel_flim_fret_wiring.py` (new) — mirrors `tests/test_gui/test_io_panel_batch_tcspc_wiring.py`.

**Approach:**
- In `_create_workflows_panel` (around `main_window.py:329`) add a new `QPushButton("FLIM-FRET analysis")` after the existing two workflow buttons; connect `clicked` to `_on_open_flim_fret_workflow`.
- `_on_open_flim_fret_workflow(self)`:
  - If `self.is_workflow_locked`: surface a `QMessageBox.information` ("Another workflow is running") and return.
  - Construct `dialog = FlimFretDialog(parent=self)`; `dialog.exec_()`; `dialog.deleteLater()`.
  - No `_active_workflow_runner` slot needed (the QProgressDialog blocks within `_on_start`).
- The button is itself an Action; the handler reads no session state and writes no session state.

**Patterns to follow:**
- `_create_workflows_panel` button construction style (already present for Single-cell workflow and Dilute phase mask).
- `_on_open_single_cell_workflow` and `_on_batch_tcspc_append` for the dialog launch + reentrance guard pattern.

**Test scenarios:**
- Happy path: launcher renders the new button visible under the Workflows panel.
- Happy path: clicking the button (via qtbot) constructs a `FlimFretDialog` instance (assert via spy on the dialog class) and shows it modally.
- Edge case: `is_workflow_locked == True` — the handler returns without opening the dialog and shows an informational message (assert via spy on `QMessageBox.information`).
- Integration: the button is wired only once (no duplicate `clicked` signal connections after re-creating the panel).

**Verification:**
- New button appears in the Workflows sidebar alongside the existing two buttons.
- Reentrance guard honored.

---

## System-Wide Impact

- **Interaction graph:** New launcher button → modal dialog → orchestrator → atomic CSV write + JSONL run log. No new callbacks on the `CellDataModel`. No napari layer creation. No session state mutation.
- **Error propagation:** Per-pair errors are isolated in the orchestrator and surface as result statuses + run-log events. Dialog-level errors (output folder unwritable, disk full) surface as `QMessageBox.warning` / `critical`. Layer-name resolution happens twice (dialog accept and orchestrator pre-flight) to catch stale dropdown state.
- **State lifecycle risks:** Frozen config snapshots the pair table at Start; later changes to the source folder cannot affect the in-flight run. Partial CSVs are written via `write_atomic` only at end-of-run, so a process kill mid-run leaves no partial CSV (but the JSONL run log is durable per-event via `flush + fsync` — a recovery-from-run-log path is out of scope for this plan).
- **API surface parity:** No public API changes outside the new module. `DatasetStore`'s read-only API is exercised; no new write paths are added.
- **Integration coverage:** End-to-end test (dialog → orchestrator → CSV) verifies the full path and the single-cell-toggle wiring per the `phasor-view-bin-not-forwarded` learning.
- **Unchanged invariants:** The Selector / Creator / Action discipline holds — the new dialog is purely an Action. `session.active_*` mutation rules are not altered. `store.py` `native_shape` lock unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Users have existing datasets without `_mask` / `_phasor` suffixes on their masks → workflow shows everything as ineligible. Intentional contract per user direction ("Use the suffixes to pre-screen; block if not present"). The codebase does not yet auto-append these suffixes — phasor masks default to `phasor_<channel>_N` prefix, and segmentation/threshold masks are arbitrarily named. So existing datasets need their masks renamed before they enter the FLIM-FRET workflow. | Discovery surfaces a per-dataset "excluded because no `<…>_phasor` mask" reason so users know what to rename. The source-folder helper text in the dialog spells out the naming convention. A follow-up PR can update mask creators (`peer_views/phasor_plot.py`, `segmentation_panel.py`, etc.) to append the convention automatically, but that work is intentionally outside this plan's scope. |
| Long batch over external-drive `.h5` files freezes the UI between pairs. | Main-thread + `QProgressDialog` with per-pair callback is the documented PerCell4 pattern for I/O-bound batch ops (see `percell4-flim-phasor-troubleshooting.md` Item 2). Acceptable; document in the dialog tooltip. |
| `_validate_layer_shape` is enforced only at write — a hand-edited or imported-with-bug `.h5` could carry mis-shaped layers and the orchestrator would crash on the boolean intersection. | Defensive `assert` at the top of per-pair compute; on `AssertionError` capture as `status="error"` and continue with next pair. |
| Cells-with-empty-intersection in single-cell mode silently produce many NaN rows. | Run-log captures per-pair `n_da_cells_skipped` and `donor_reference_empty` events. Summary message after the run reports counts. |
| `numpy.isin` with Python sets returns all-False in NumPy 2.x. | If pooling uses `np.isin`, convert sets to arrays first or use the `(labels == cid)` per-cell loop (preferred — clearer and avoids the trap). |
| Dialog test helper compliance check fails when wrappers aren't applied. | Include `wrap_in_scroll` + `cap_to_screen` from the first commit; the AST walker (`tests/test_gui/test_dialog_helper_compliance.py`) guards future regressions. |
| Run log entries lost on disk-full. | `RunLog` uses `flush + fsync` per event; the only loss window is the in-flight event itself. Acceptable. |

---

## Documentation / Operational Notes

- After the workflow ships, update `docs/audits/canonical-sources-matrix.yaml` to add `applies_to` globs for `src/percell4/application/use_cases/run_flim_fret.py` and `src/percell4/gui/flim_fret_dialog.py` so `learnings_applicability.py` surfaces relevant docs on future edits.
- Add a brief usage note in the project README's "Workflows" section once the button ships.
- Consider capturing the "main-thread + QProgressDialog for sequential I/O-bound batch ops" rule as a separate `docs/solutions/architecture-patterns/` entry once this is the third reuse (along with `batch_tcspc_dialog.py` and `_run_batch_compress`).
- Per-module `CLAUDE.md` in `src/percell4/gui/` and `src/percell4/application/use_cases/` may need a one-line entry referencing the new files (current-state-only, per project doc rules).

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-25-flim-fret-analysis-requirements.md](../brainstorms/2026-05-25-flim-fret-analysis-requirements.md)
- Pair-table dialog reference: `src/percell4/gui/batch_tcspc_dialog.py`
- Workflow config dialog reference: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Atomic write + run folder helpers: `src/percell4/workflows/artifacts.py`
- Run log JSONL helper: `src/percell4/workflows/run_log.py`
- Frozen config conventions: `src/percell4/workflows/models.py`
- Pure-Python orchestrator reference: `src/percell4/application/use_cases/batch_add_decay.py`
- Storage API: `src/percell4/store.py`
- Lifetime channel naming source of truth: `src/percell4/application/use_cases/compute_lifetime.py`
- Launcher entry point: `src/percell4/interfaces/gui/main_window.py:_create_workflows_panel`
- Dialog helper enforcement: `src/percell4/gui/_dialog_utils.py`, `tests/test_gui/test_dialog_helper_compliance.py`
