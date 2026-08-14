# FLIM-FRET Analysis Workflow — Requirements

**Date:** 2026-05-25
**Status:** Requirements drafted, ready for planning
**Scope:** Standard (new workflow under the Workflows tab, multi-dataset, fixed scientific behavior)

## Problem

PerCell4 already computes lifetime images from FLIM data, builds intensity masks, and applies phasor-circle masks. What it cannot yet do is compare a **donor-only** sample to a **donor+acceptor** sample and produce a per-cell or per-field FRET efficiency. Today that means scientists pull lifetime images out of the app, mask and average in a separate tool, and compute FRET in a spreadsheet — the cross-dataset comparison sits entirely outside the app even though every input layer lives inside the `.h5` store.

The result is the first PerCell4 workflow that **fuses information across two datasets** rather than running embarrassingly-parallel per-dataset analysis.

## User goal

A microscopy researcher with a stack of `.h5` files — some donor-only, some donor+acceptor (DA) — clicks **FLIM-FRET analysis** under Workflows, builds pairs of (donor, DA) datasets, picks which mask / phasor / lifetime layers to use for each pair, optionally enables single-cell analysis, presses Start, and receives a CSV with FRET efficiency per pair (or per cell when single-cell is enabled).

## Decisions captured during brainstorm

These are settled and feed the plan directly.

1. **Donor reference is "pooled per-cell donor mean" when single-cell is enabled.**
   Segment the donor dataset, compute each donor cell's mean lifetime within (donor `_mask` ∩ donor `_phasor`), then arithmetic-mean those per-cell values into one reference number for the whole pair. Every DA cell row in that pair uses the same donor reference; only the DA value varies per row. (Standard whole-field mean in non-single-cell mode.)
2. **Pairing UI is a pair-table with dropdowns + per-pair Configure.**
   One row per pair: pair name, donor `.h5` dropdown, DA `.h5` dropdown, Configure button. Configure opens a sub-dialog with the layer pickers for that pair. Add/Remove pair buttons control the row count. Matches the Batch TCSPC pattern at `src/percell4/gui/batch_tcspc_dialog.py`.
3. **Output is a single combined CSV.**
   One row per pair in whole-field mode, one row per DA cell in single-cell mode, all pairs concatenated. Lands at `<output_folder>/flim_fret_run_<timestamp>/flim_fret_results.csv`.
4. **Single-cell is a global toggle for the whole run.**
   One checkbox at the top of the dialog. When on, every pair must have a valid segmentation on both donor and DA datasets before Start unlocks.

## Required behavior

### Entry point

- New button **FLIM-FRET analysis** in the Workflows sidebar panel of the launcher (`src/percell4/interfaces/gui/main_window.py:_create_workflows_panel`). Sits alongside the existing two workflow buttons.
- Clicking opens the FLIM-FRET setup dialog (new modal `QDialog`).

### Setup dialog

Top-level controls:
- **Single-cell analysis** checkbox (global, off by default).
- **Output parent folder** picker (QFileDialog, with QSettings persistence following the established pattern).
- **Pair table** with columns: `pair_name`, `donor dataset (▾)`, `DA dataset (▾)`, `Configure`. Dropdowns are populated from `.h5` files discovered in a user-picked source folder (same discovery pattern as Batch TCSPC).
- **Add pair** / **Remove pair** buttons.
- **Start** / **Cancel** buttons.

Configure sub-dialog (per pair):
- Two columns: **Donor** and **Donor+Acceptor**.
- For each side: dropdowns for **mask layer** (entries under `/masks/` whose name ends in `_mask`), **phasor layer** (entries under `/masks/` whose name ends in `_phasor`), **lifetime channel** (entries under `/intensity/` whose name ends in `_lifetime`).
- If the global single-cell toggle is on, also a **segmentation layer** dropdown for each side (entries from `store.list_labels()`).
- Each dropdown is required; OK is disabled until all required dropdowns have a value.

### Validation (dialog refuses Start until satisfied)

For each pair:
- Both selected datasets exist and open.
- Each dataset has at least one `_mask` layer, at least one `_phasor` layer, and at least one `_lifetime` channel — otherwise the dataset cannot be picked for that side (dropdown row is dimmed or filtered out, with a tooltip explaining the missing layer category).
- All required layer dropdowns in Configure have a value.
- If single-cell is on, both datasets in the pair have at least one segmentation layer and both segmentation dropdowns have a value.
- `pair_name` is non-empty and unique across the table.

### Analysis (per pair)

Given the selected `_mask`, `_phasor`, `_lifetime` layers on each side (and segmentations when single-cell):

1. **Build the effective mask** on each side: `effective = _mask AND _phasor` (boolean intersection at view-bin 1).
2. **Whole-field mode** (single-cell unchecked):
   - `donor_mean_lifetime` = arithmetic mean of donor `_lifetime` pixel values where donor `effective` is true.
   - `da_mean_lifetime` = arithmetic mean of DA `_lifetime` pixel values where DA `effective` is true.
   - `fret_efficiency` = `1 - (da_mean_lifetime / donor_mean_lifetime)`.
   - Emit one row per pair.
3. **Single-cell mode** (toggle on):
   - On the donor side, for each donor cell label `c`: compute mean of donor `_lifetime` pixels where `(donor_effective AND donor_segmentation == c)`. Collect those per-cell means; donor cells with zero valid pixels are excluded from the reference.
   - `donor_mean_lifetime` (one number for the pair) = arithmetic mean over the surviving per-cell donor means.
   - On the DA side, for each DA cell label `c`: compute `da_mean_lifetime_c` = mean of DA `_lifetime` pixels where `(da_effective AND da_segmentation == c)`. Cells with zero valid pixels emit a row with NaN for `da_mean_lifetime` and `fret_efficiency` (counted as skipped).
   - `fret_efficiency_c` = `1 - (da_mean_lifetime_c / donor_mean_lifetime)`.
   - Emit one row per DA cell.

### Output CSV

Columns:

| Column | Meaning |
|---|---|
| `pair_name` | User-supplied pair name from the table. |
| `donor_dataset` | Filename of the donor `.h5`. |
| `da_dataset` | Filename of the DA `.h5`. |
| `cell_id` | DA cell label (single-cell mode); blank in whole-field mode. |
| `donor_mean_lifetime` | Pooled per-cell donor mean (single-cell) or whole-field donor mean (whole-field). Same value repeats across all rows of a pair. |
| `da_mean_lifetime` | Whole-field DA mean (whole-field mode) or per-cell DA mean (single-cell mode). NaN if no valid pixels. |
| `fret_efficiency` | `1 - (da_mean_lifetime / donor_mean_lifetime)`. NaN if either input is NaN or donor mean is non-positive. |
| `n_pixels_donor` | Pixel count contributing to the donor mean for this row's pair. |
| `n_pixels_da` | Pixel count contributing to `da_mean_lifetime` for this row (per-cell in single-cell mode, whole-field in whole-field mode). |
| `n_cells_donor_reference` | Number of donor cells contributing to the donor reference (single-cell mode only; blank in whole-field). |

CSV lands at `<output_folder>/flim_fret_run_<YYYYMMDD-HHMMSS>/flim_fret_results.csv`.

### Run-time behavior

- Dialog returns a frozen config dataclass on accept (analogous to `WorkflowConfig`).
- Workflow runner consumes the config and processes pairs sequentially.
- Progress UI: `QProgressDialog` style (one tick per pair), matching the Batch TCSPC pattern — no per-pair interactive QC is needed, so `BaseWorkflowRunner` (which is built for interactive phases) is heavier than necessary here.
- Each pair load → compute → release, never holding two datasets in memory beyond what one pair requires.
- A run log (text file in the run folder) records the config, per-pair status, skipped cells, and any errors.

## Scope boundaries

**In scope (this brainstorm):**
- New Workflows-tab button, modal setup dialog, pair table, per-pair Configure sub-dialog.
- Whole-field and single-cell modes (global toggle).
- One combined CSV + run log.
- Validation of layer availability and dropdown completeness.

**Deferred for later (out of this requirements doc, fair game for follow-ups):**
- Plotting of FRET efficiency in the app (histogram, per-cell scatter, overlay on the segmentation). For now, the CSV is the output.
- Storing FRET maps (per-pixel efficiency images) back into the `.h5` store under a new `/fret/` group.
- Persisting FRET pair configurations across sessions for re-running.
- Per-pair single-cell toggle.
- Negative FRET clamping (current behavior: report `fret_efficiency` as-is, including negative or > 1 values; downstream tooling decides what to do).
- Multi-CSV outputs (per-pair files alongside the combined CSV).
- Auto-pairing from filename conventions.
- Cross-dataset alignment / registration: this workflow does not assume donor and DA images share a coordinate system — the math is mean-based.

**Outside this feature's identity:**
- Generic FRET methods other than lifetime-ratio (e.g., ratiometric intensity FRET, acceptor photobleaching) — the math here is specifically FLIM-based lifetime-ratio FRET.
- Reference-sample lifetimes pulled from a global config or library — donor reference is always recomputed per pair from the matched donor dataset.

## Dependencies and assumptions

- `_mask`, `_phasor`, and `_lifetime` are the project's existing layer-name suffix conventions. Verified against `src/percell4/application/use_cases/compute_lifetime.py:27` (lifetime channel naming `{src}_{source}_lifetime`) and `src/percell4/store.py` (masks under `/masks/`). The workflow trusts the suffix — no inspection of layer attributes.
- Lifetime channels are stored as float images under `/intensity/<name>` and read by `store.read_channel(name, view_bin=1)`.
- Masks under `/masks/<name>` are read by `store.read_mask(name, view_bin=1)`.
- Segmentation layers under `/labels/<name>` are read by `store.read_labels(name, view_bin=1)`.
- Donor and DA images for a pair may have different dimensions — the math only uses summary statistics, so no resampling is required.
- The session model holds one dataset at a time, but the workflow runner loads each dataset transiently and does not push them into the session — consistent with how Batch TCSPC operates.

## Acceptance criteria

A reasonable implementation is "done" when:

1. A **FLIM-FRET analysis** button is visible in the Workflows sidebar of the launcher.
2. Clicking it opens the setup dialog with the pair table, single-cell toggle, output folder picker, and Start/Cancel.
3. Dropdowns in the pair table list only `.h5` files that contain at least one `_mask`, one `_phasor`, and one `_lifetime` layer.
4. The per-pair Configure sub-dialog exposes the right layer dropdowns for each side, including segmentation when single-cell is on.
5. Start is disabled until every required selection is made and every pair name is unique.
6. Running with N pairs produces one combined CSV at `<output_folder>/flim_fret_run_<timestamp>/flim_fret_results.csv` with the columns listed above.
7. In single-cell mode with N pairs, the CSV has one row per DA cell across all pairs, with the pair's pooled donor mean repeated across that pair's rows.
8. In whole-field mode with N pairs, the CSV has exactly N rows (one per pair) and the `cell_id` column is blank.
9. A run log file records configuration, per-pair status, skipped cells, and any errors.
10. Cancelling the run mid-flight stops cleanly without leaving a partially-written CSV (write to a temp file, atomic rename at the end).

## Open questions for planning

These do not block the requirements but should be resolved during planning:

- **Discovery folder vs explicit add:** Should the pair-table dropdowns be populated from a single user-chosen source folder (Batch TCSPC style), or from a multi-select "Add datasets" file dialog? Either fits the table model.
- **Layer dropdown order:** Sorted alphabetically, or with the most recently created layer first?
- **Pair name auto-suggestion:** Auto-fill `pair_name` based on a common prefix between donor and DA filenames, or always require explicit user input?
- **Skipped-cell threshold:** Should the workflow expose a "minimum pixels per cell" filter, or just skip cells with zero valid pixels (current default)?

## References

- Existing multi-dataset pattern: `src/percell4/gui/batch_tcspc_dialog.py`.
- Existing workflow runner (interactive variant): `src/percell4/gui/workflows/base_runner.py`.
- Existing single-cell workflow config pattern: `src/percell4/gui/workflows/single_cell/config_dialog.py`.
- Workflows-tab entry point: `src/percell4/interfaces/gui/main_window.py:_create_workflows_panel`.
- Layer naming source of truth: `src/percell4/application/use_cases/compute_lifetime.py`, `src/percell4/store.py`.
