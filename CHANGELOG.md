# Changelog

Notable user-facing changes to PerCell4, newest first. PerCell4 is pre-release
(`0.1.0`, no tagged releases yet), so entries are grouped by the **month they
were implemented** rather than by version. Dates are drawn from the repository's
commit history and the dated plans in [`docs/plans/`](docs/plans/); see the
[Features](README.md#features) section of the README for the current capability
list.

## Unreleased

### Changed

- **The workflow config dialog's Thresholding Rounds editor is now a card list.**
  Each round is a full-width card showing only the fields for its selected method
  (Grouped Otsu, or Adaptive Local Thresholding), with per-card move/remove
  controls — replacing the wide multi-method table where every method's columns
  showed at once. The **Adaptive σ-clipping (single-window)** method is no longer
  offered in the dialog; it remains available in the batch CLI
  (`percell4-batch-threshold`), and saved configs that use it still run.

### Added

- **Cellpose 4.2 models are now selectable.** The Segment tab, workflow dialog,
  and batch CLIs (`--cellpose-model`) offer the Cellpose 4.x built-ins —
  `cpsam_v2` (new default; improved CellposeSAM, fewer spurious masks in
  low-contrast regions), `cpsam` (original, for reproducing prior runs),
  `cpdino`, and `cpdino-vitb` (DINOv3 backbones). The 4.x wrapper now forwards
  the chosen model via `CellposeModel(pretrained_model=...)` (previously the
  model name was dropped on 4.x).

### Changed

- Default segmentation model is now **`cpsam_v2`** (was `cpsam`) — re-running
  Cellpose yields improved, slightly different masks.
- Minimum Cellpose is now **4.2** (`cellpose>=4.2,<5`); the legacy 3.x code path
  and the `cyto3`/`cyto2`/`cyto`/`nuclei` model names are removed. **Run
  `pip install -U cellpose` (or reinstall the package) to pull 4.2.x.** A saved
  workflow config referencing a removed model falls back to `cpsam_v2`.

## 2026-06 — Overlap-aware stitching, adaptive clipping, CNR

### Added

- **Overlap-aware mosaic tile stitching.** Phase-correlation registration on the
  tile **overlap region** (matching the Fiji/ImageJ Grid-Collection approach),
  solved once on a reference channel and reused for every channel and the FLIM
  decay stream. Includes a grid-prior band constraint, ImageJ-style outlier
  rejection, a nominal-overlap **grid fallback** when a channel is too low-contrast
  to register, and **None / Linear Blending** overlap fusion (None is the
  measurement-correct default and is forced for FLIM datasets). (2026-06-24 – 06-25)
- **Adaptive Local Clipping (ALC) puncta detection.** GUI module for per-cell
  band-pass + z-score puncta detection, with auto-window methods and a two-pass
  auto-extraction mode that sizes the window to the smallest particle. (module
  2026-06-05; auto-extraction 2026-06-22 – 06-24)
- **CNR subpopulation classification** (discover / guided / forced) plus an
  **interactive CNR histogram segmenter** for splitting a feature mask into
  populations. (2026-06-23)
- **Time-lapse parity** for ALC auto-extraction and CNR classification — per-frame
  across the GUI panel, the workflow, and `percell4-batch-threshold`. (2026-06-25)
- **Iterative Otsu thresholding**, existing-mask reuse, and headless puncta
  thresholding via `percell4-batch-threshold --strategy {adaptive-clip,auto-extract}`.
  (2026-06-08 – 06-24)
- **Segment-tab Cellpose settings parity** so interactive and headless
  (`percell4-batch-cellpose-laptrack`) runs share the same controls. (2026-06-03)

### Changed

- Faster load times for large datasets. (2026-06-06)

### Fixed

- Channels renamed at import (Manual mode) are now valid stitch registration
  references. (2026-06-25)

## 2026-05 — Time-lapse tracking, phasor masks, batch CLIs

### Added

- **Time-lapse tracking + lineage** (powered by laptrack): import `_tN` series as a
  single multi-timepoint dataset, segment every frame, track each cell with one ID
  across time, link dividing cells parent → daughter, and view trajectories in a
  napari Tracks layer. (2026-05-21)
- **Workflow tracking + `percell4-batch-cellpose-laptrack`** headless
  compress → segment → track pipeline for overnight batch runs. (2026-05-22)
- **Phasor analysis depth:** GMM phasor segmentation, phasor cache + `.npz` I/O,
  "apply current phasor as mask", clear-within-ROI, FLIM filter options, and a
  FLIM-FRET analysis workflow. (2026-05-03 – 05-25)
- **Phasor-masks workflow** (single-cluster GMM ellipse → dual-threshold masks) with
  a shared ROI across a treatment cohort, plus the `percell4-batch-phasor-masks`
  CLI. (2026-05-27)
- **Dataset-wide spatial binning**, binned-TIFF export, batch phasor/wavelet
  compute, and batch image export. (2026-05-18 – 05-19)
- **Dilute-phase mask generation** — adaptive per-dataset round loop layered on
  grouped thresholding for phase-separated biology. (2026-05-18)
- **Session selection window** and channel override for Cellpose / FLIM. (2026-05-12 – 05-13)
- **`percell4-batch-rename` / `percell4-batch-delete`** resource CLIs; segmentation-QC
  recovery options. (2026-05-26)
- **Whole-field multichannel analyses** and per-particle multichannel CSV. (2026-05-28 – 05-29)
- **Pixel-size visibility + TIFF-metadata roundtrip**; README revamp with the
  step-by-step workflow protocol. (2026-05-21)
- **Windows-via-WSL** install path. (2026-05-14)

## 2026-04 — Workflows, batch compress, hexagonal architecture

### Added

- **Single-cell thresholding workflow:** `BaseWorkflowRunner` generator-driven state
  machine with interactive QC and pause/resume via `run_state.json`. (2026-04-10 – 04-11)
- **Grouped segmentation:** cluster cells by a metric → per-group autothresholding →
  threshold QC → `/masks/<round>`, multiple rounds per workflow. (2026-04-03 – 04-04)
- **Batch TIFF compression** (`CompressDialog`, Auto/Manual modes, channel → layer
  assignment), the **Add Layer to Dataset** dialog, and the **Export Images** dialog.
  (2026-04-04 – 04-05)
- **TCSPC append + cross-format token matching** — append `.bin` decay onto an
  existing dataset by matching tokens. (2026-04-29)
- **napari multi-label selection** tool. (2026-04-17)
- **Phasor UX:** active-mask histogram filter, Save Phasor PNG, `nipy_spectral`
  density colormap. (2026-04-30)

### Changed

- _Internal:_ hexagonal-architecture refactor (domain / application / adapters /
  ports seams; CLI adapter validates the seam). (2026-04-16)

### Fixed

- Structured worker errors with an actionable torch error dialog, and a startup
  warning when the Windows MSVC redistributable is too old. (2026-04-17)

## 2026-03 — Foundation

### Added

- Project scaffolding and the **HDF5 `DatasetStore` + `ProjectIndex`** (one `.h5`
  per experiment). (2026-03-25 – 03-26)
- **TIFF import pipeline** (scanner, assembler, readers, importer) with tile
  stitching and Z-projection. (2026-03-26)
- **Cellpose segmentation** + ROI import + interactive label cleanup (edge removal,
  min-area filter). (2026-03-26)
- **Per-cell measurements** — per-channel metrics over bbox-optimized masked scopes.
  (2026-03-26)
- **FLIM phasor** computation + wavelet filter + phasor-plot window; TCSPC
  `.sdt` / `.bin` import with per-channel calibration. (2026-03-26)
- **Multi-window GUI** (launcher, napari viewer, data plot, cell table) on a single
  `CellDataModel`; cross-window selection and a cell filter shared across windows;
  single-pass multi-ROI measurement. (2026-03-26 – 03-27)
