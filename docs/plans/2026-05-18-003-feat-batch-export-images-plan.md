---
title: "feat: Batch export dataset layers to TIFF via CLI"
type: feat
status: active
date: 2026-05-18
---

# feat: Batch export dataset layers to TIFF via CLI

## Overview

Add a CLI entry point that exports every intensity channel, segmentation
label, and mask from a list of `.h5` datasets to a target directory as
`.tif` files. Reuses the existing `ExportImages` use case unchanged;
this plan adds a thin batch orchestrator + an argparse-driven CLI on
top, matching the structure of `batch_phasor` (shipped 2026-05-18) and
`run_pipeline.py`.

Per the clarifications: scope is **intensity + labels + masks**
(no phasor/lifetime/decay), output is **flat with dataset-prefixed
filenames**, conflicts **overwrite silently**, and **every channel** is
exported (no `--channels` filter).

---

## Problem Frame

After a batch FLIM or segmentation run produces a directory of `.h5`
files, the user often needs to hand the per-channel TIFFs (or label
images) to an external tool — ImageJ, a colleague who doesn't use
PerCell4, a snakemake pipeline, etc. Doing this through the GUI's
`ExportImagesDialog` requires opening every dataset, picking the
layers, and clicking Export, one at a time. The batch CLI removes that
friction: queue a directory of `.h5` files, get a folder of TIFFs.

This is the third batch CLI in the codebase, joining `batch_phasor`
(compute phasor + apply wavelet across N datasets) and `run_pipeline`
(headless single-dataset analysis). All three share the same
argparse + per-item-classification + final-totals shape.

---

## Requirements Trace

- R1. For each input `.h5`, export every channel under `/intensity`
  (multichannel 3D arrays expand to one TIFF per channel), every
  segmentation under `/labels/*`, and every mask under `/masks/*`.
- R2. Output filenames use the pattern `<dataset_stem>_<layer_name>.tif`
  written **flat** into the target directory (no per-dataset
  subfolders).
- R3. Existing `.tif` files in the target directory are overwritten
  silently. The CLI does not introduce a `--skip-existing` or
  `--no-overwrite` flag in this iteration.
- R4. Phasor arrays, lifetime arrays, and decay arrays are **not**
  exported in this iteration. The existing `ExportImages` use case
  doesn't support them, and the user explicitly scoped them out.
- R5. Per-dataset failures isolate — a missing or unreadable `.h5`
  fails its own item without aborting the batch.
- R6. Produce a per-dataset summary (status + count of files written)
  to stdout and a totals line at the end.
- R7. Programmatic API (`batch_export_images(...) -> BatchExportReport`)
  alongside the CLI so future GUI batch work can call it directly.
- R8. Exit code: `0` if any file was written, `1` if every dataset
  failed.

---

## Scope Boundaries

- No GUI dialog. Invocation is CLI-only. A future `BatchExportDialog`
  mirroring the existing `ExportImagesDialog` can be added in a
  follow-up if usage demand justifies it.
- No phasor / lifetime / wavelet array export. Out of scope for this
  iteration; the user explicitly chose to keep scope to
  intensity + labels + masks.
- No decay array export. `/decay/<ch>` is 3D `(H, W, T)` and exporting
  it as a 2D TIFF requires a projection or T-slice decision that
  belongs in a separate feature.
- No subfolder-per-dataset layout. Output is flat; the
  `dataset_stem_<layer>.tif` convention disambiguates.
- No conflict-resolution flag (skip / error). Overwrites are silent.
- No per-channel filter (`--channels`). Every channel is exported.
- No parallelism across datasets. Sequential processing keeps the
  report deterministic; tifffile writes are fast enough that
  sequential time is not a bottleneck.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/application/use_cases/export_images.py` — `ExportImages`
  use case that already handles the per-dataset TIFF write. The batch
  orchestrator builds an `ExportRequest` per dataset and delegates.
  Reuse unchanged.
- `src/percell4/application/use_cases/batch_compute_phasor.py` — pattern
  source for the batch orchestrator: per-item iteration, per-item
  result classification, progress callback, structured report.
- `src/percell4/interfaces/cli/batch_phasor.py` — pattern source for
  the CLI: argparse with directory-glob path resolution, per-dataset
  stdout summary, final totals line, exit-code-on-progress convention.
- `src/percell4/store.py` — `DatasetStore.list_labels()`,
  `list_masks()`, and `metadata.get("channel_names", [])` provide the
  inputs the batch needs to build the `ExportRequest`.
- `src/percell4/adapters/hdf5_store.py` — `Hdf5DatasetRepository.open`
  + `read_array` chain the `ExportImages` use case uses.

### Institutional Learnings

- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`
  — caller-wiring discipline. Doesn't apply directly (TIFF export has
  no `view_bin` axis), but the lesson "use case accepts kwargs, callers
  must pass them" generalizes to all kwarg additions in this codebase.
  No new kwargs needed for this feature.
- No HDF5 staleness vectors apply — the batch reads disk fresh per
  dataset and writes derived TIFFs to a separate location. No caches
  to invalidate.

### External References

None. Internal CLI on top of an internal use case; `tifffile` is
already a dependency.

---

## Key Technical Decisions

- **Reuse `ExportImages.execute` unchanged.** It already implements the
  per-dataset TIFF write with the exact filename pattern the user
  wants. The batch is an orchestrator that builds the request per
  dataset and delegates.
- **Auto-build `ExportRequest` from metadata + store listing.** Per
  dataset: channels from `metadata["channel_names"]` enumerated with
  their indices; labels from `store.list_labels()`; masks from
  `store.list_masks()`. No user override surface in this iteration.
- **Flat output directory with `<stem>_<layer>.tif` filenames.**
  Matches `ExportImages`'s existing convention. The dataset_stem is
  `h5_path.stem` (the filename without `.h5`).
- **Per-dataset failures isolate, never abort the batch.** Same
  contract as `batch_compute_phasor`: per-item exceptions land in the
  result's `error` field; the loop continues.
- **Sequential, single-process.** No worker pool, no parallelism.
  Consistent with `batch_phasor`'s posture; TIFF writes are
  I/O-bound and parallel writes would only help on fast NVMe and
  complicate error attribution.
- **No `--skip-existing` flag.** The user explicitly chose silent
  overwrite. If resume-on-interruption becomes a real need, add the
  flag in a follow-up.

---

## Open Questions

### Resolved During Planning

- **What "all layers" means:** intensity channels + labels + masks.
  Phasor / lifetime / decay explicitly excluded.
- **Output filename pattern:** `<h5_stem>_<layer_name>.tif`, mirroring
  `ExportImages`'s existing single-dataset convention. Already pinned
  by the reused use case.
- **Channel naming:** when `metadata["channel_names"]` is absent or
  shorter than the intensity array's channel count, fall back to
  `f"ch{i}"` for the missing slots, mirroring
  `Hdf5DatasetRepository.read_channel_images`.
- **What happens if a `.h5` has no intensity layer at all:** the
  per-dataset result is `"skipped_no_changes"` with reason
  "no intensity, labels, or masks", same shape as
  `batch_compute_phasor`'s empty-decay case.
- **Exit code rule:** `0` if any file was written across the batch,
  `1` otherwise. Mirrors `batch_phasor`'s "any progress" rule.

### Deferred to Implementation

- **2D vs 3D intensity edge case in `ExportImages`:** the existing use
  case handles `intensity.ndim == 2` (single channel, exports whole
  array) and `intensity.ndim == 3` (multi-channel, indexes the slice).
  The batch's `ExportRequest.channels` list of `(name, idx)` tuples
  needs to be built correctly for both shapes — at implementation
  time, mirror what `Hdf5DatasetRepository.read_channel_images` does.
- **Whether to add a `--verbose` flag.** `batch_phasor` has one; this
  CLI's output is simpler, so it may not be necessary. Decide based
  on whether `tifffile`'s INFO logs are useful to surface.

---

## Implementation Units

- U1. **`batch_export_images` use case**

**Goal:** Loop N datasets, build an "everything" `ExportRequest` per
dataset, delegate to `ExportImages.execute`, collect per-dataset
results into a structured report.

**Requirements:** R1, R2, R5, R6, R7

**Dependencies:** None — reuses `ExportImages` unchanged.

**Files:**
- Create: `src/percell4/application/use_cases/batch_export_images.py`
- Test: `tests/test_application/test_batch_export_images.py`

**Approach:**
- Public function: `batch_export_images(h5_paths: list[Path], *,
  output_dir: Path, overwrite: bool = True,
  progress_callback=None) -> BatchExportReport`.
- `BatchExportReport` dataclass: `items: tuple[BatchExportItemResult, ...]`,
  `total_succeeded: int` property, `total_failed: int` property,
  `total_skipped: int` property, `total_files_written: int` property.
- `BatchExportItemResult` dataclass: `h5_path: Path`, `status: str`
  ("succeeded" | "skipped_no_changes" | "failed"),
  `files_written: int`, `error: str | None`.
- Per-dataset loop:
  1. `Hdf5DatasetRepository().open(h5_path)` → `handle`.
  2. Enumerate channels:
     `intensity.shape[0]` for the 3D case (channel_names padded with
     `f"ch{i}"` for missing slots); for the 2D case, single channel
     named from `channel_names[0]` or `"Intensity"`.
  3. Enumerate `store.list_labels()` and `store.list_masks()`.
  4. Build `ExportRequest(output_folder=output_dir,
     dataset_name=h5_path.stem, channels=[...], labels=[...],
     masks=[...])`.
  5. If `channels and labels and masks` are all empty →
     `"skipped_no_changes"`.
  6. Else `ExportImages(repo).execute(handle, request)` →
     `ExportResult.exported_count` → `"succeeded"`.
  7. Catch per-dataset exceptions; classify as `"failed"` with the
     error message.
- `progress_callback(item)` fired once per dataset after
  classification.
- The `overwrite` parameter exists for forward compatibility but is
  currently unused inside the function (tifffile.imwrite always
  overwrites). Document that and keep the kwarg for the CLI surface.

**Patterns to follow:**
- `src/percell4/application/use_cases/batch_compute_phasor.py` — same
  item / result / report shape; same per-dataset isolation; same
  callback contract.
- `src/percell4/application/use_cases/export_images.py` — the
  single-dataset use case being orchestrated. Filename pattern lives
  there; don't duplicate it.

**Test scenarios:**
- Happy path (multichannel): a `.h5` with `intensity (2, 4, 4)`,
  `/labels/seg`, and `/masks/threshold` → the resulting TIFF set
  includes `stem_ch0.tif`, `stem_ch1.tif`, `stem_seg.tif`,
  `stem_threshold.tif`. `files_written == 4`, status `"succeeded"`.
- Happy path (single-channel 2D intensity): `intensity (4, 4)` only,
  no labels or masks → one `stem_<channel>.tif`, status
  `"succeeded"`.
- Channel naming fallback: `intensity (3, 4, 4)` with
  `channel_names = ["A", "B"]` (one missing) → files
  `stem_A.tif`, `stem_B.tif`, `stem_ch2.tif`.
- Empty dataset: a `.h5` with `/metadata` only, no intensity / labels
  / masks → status `"skipped_no_changes"`, `files_written == 0`,
  no files in the output directory.
- Multiple datasets: two `.h5` files → two items in the report, both
  succeeded, total_files_written sums correctly.
- Dataset-level error: nonexistent `.h5` path → status `"failed"`,
  `error` populated, other datasets continue.
- Progress callback fires once per dataset in input order.
- Output directory created if it doesn't exist (delegated to
  `ExportImages.execute`'s `mkdir(parents=True, exist_ok=True)`).
- Overwrite: pre-existing `stem_ch0.tif` in the output directory is
  silently replaced; no error, no skip.
- Empty input list returns an empty report (no error).

**Verification:**
- `tests/test_application/test_batch_export_images.py` passes.
- A two-dataset two-channel scratch directory exports the expected
  TIFF set when wired up via U2.

---

- U2. **CLI entry point `batch_export.py`**

**Goal:** Wire `batch_export_images` to an argparse-driven CLI that
the user invokes from a terminal.

**Requirements:** R1, R2, R3, R5, R6, R7, R8

**Dependencies:** U1.

**Files:**
- Create: `src/percell4/interfaces/cli/batch_export.py`
- Test: `tests/test_cli_batch_export.py`

**Approach:**
- `main(argv: list[str] | None = None) -> int` entry point.
- Args:
  - Positional: one or more `.h5` paths (or directories globbed for
    `*.h5`). Same `_resolve_paths` shape as `batch_phasor`.
  - `--output-dir PATH` (required): target directory for the `.tif`
    files. Created if missing.
  - `--quiet` flag (suppresses per-dataset detail; final totals
    always print).
- Call `batch_export_images(paths, output_dir=..., progress_callback=
  _print_item_status)`.
- `_print_item_status(item)` prints one line per dataset:
  `[succeeded] dish_3.h5 -- 4 files`. Errors print on a second line
  indented.
- Totals line:
  `Totals: 2 succeeded, 0 skipped, 0 failed -- 8 files written`.
- Exit code: `0` if `report.total_files_written > 0`, else `1`.

**Patterns to follow:**
- `src/percell4/interfaces/cli/batch_phasor.py` — argparse setup,
  `_resolve_paths`, `_format_item_line`, `_print_item_status`,
  `main(argv=None)` signature for programmatic invocation, exit-code
  convention. Mirror this so the two batch CLIs feel like siblings.

**Test scenarios:**
- Happy path: invoke `main(["a.h5", "b.h5", "--output-dir", str(out)])`
  → exit 0, stdout has two `[succeeded]` lines and the totals line,
  `out/a_ch0.tif` etc. exist on disk.
- Directory glob: invoke with a directory containing `.h5` files →
  every file is processed.
- No matches: invoke with an empty / nonexistent path → exit 1,
  stderr error message.
- Missing `--output-dir`: argparse raises `SystemExit` with a clear
  error.
- All datasets skipped (no layers): exit 1 (no files written),
  totals line shows `0 files written`.
- `--quiet` flag: stdout contains only `[<status>] <name>` headers
  and the totals line; no indented detail.
- Exit code: a partial run with one succeeded + one failed → exit 0
  because some files were written; a run with only `failed` items →
  exit 1.
- `--help` shows the description, all flags, and at least one usage
  example.
- CLI module imports without Qt / napari (hex seam, same as
  `batch_phasor`).

**Verification:**
- `tests/test_cli_batch_export.py` passes.
- Manual: `python -m percell4.interfaces.cli.batch_export
  scratch/*.h5 --output-dir /tmp/exports` produces the expected
  flat directory of TIFFs.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Silent overwrite is destructive — a user pointing `--output-dir` at a populated directory could lose data. | Documented in `--help`. Recommend a fresh output directory per run. A future iteration can add `--no-overwrite` if real users hit this. |
| `tifffile.imwrite` doesn't enforce the output directory exists — it errors. | `ExportImages.execute` already does `output_folder.mkdir(parents=True, exist_ok=True)` before any write. Reuse delegates to that path. |
| Mixed-rank `/intensity` (some files 2D, some 3D in the same batch) could trip channel-naming logic. | Build the channel list using the same rank-aware logic `Hdf5DatasetRepository.read_channel_images` uses (which already handles 2D and 3D). Tests cover both ranks. |
| `tifffile` writes int32 labels as int32 TIFFs, which some external tools can't open. | Out of scope for this iteration. If users hit it, a `--label-dtype uint16` flag can land later. Current behavior matches `ExportImages.execute`. |

---

## Documentation / Operational Notes

- No README changes — feature is self-documenting via `--help`.
- No CLAUDE.md update needed — adds one file in
  `interfaces/cli/` and one use case in `application/use_cases/`,
  both in existing categories.

---

## Sources & References

- Pattern source (use case): `src/percell4/application/use_cases/batch_compute_phasor.py`
- Pattern source (CLI): `src/percell4/interfaces/cli/batch_phasor.py`
- Single-dataset operation: `src/percell4/application/use_cases/export_images.py`
- Prior brainstorm:
  `docs/brainstorms/2026-04-05-image-export-brainstorm.md` (the
  single-dataset image-export feature this batch composes)
