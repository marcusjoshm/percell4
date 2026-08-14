# PerCell4 Architecture Brainstorm

**Date:** 2026-03-25
**Status:** Draft
**Author:** Lee Lab + Claude

---

## What We're Building

A single-cell FLIM microscopy analysis desktop application. The core value: every measurement, threshold, mask, and analysis result is parsed into per-cell data defined by segmentation boundaries. The architecture is multi-window — each functional unit (viewer, plots, plugins) is its own independent window, freely positionable across multiple monitors. Windows communicate through a shared data model and Qt signals.

---

## Key Decisions

### 1. Application Shell: Multi-Window Qt Application

A single Qt process (`QApplication`) with multiple independent top-level windows. Each functional unit is its own window, freely movable and resizable across multiple monitors. Not a napari plugin — we own the event loop and all windows.

**Windows:**
- **Launcher/Hub** — the main control window. Project browser, open/close other windows, menus, status. Closing the hub closes the app.
- **Napari Viewer** — its own independent window for image display, label overlays, and manual editing.
- **Data Plot** — pyqtgraph scatter/histogram window for per-cell metrics.
- **Phasor Plot** — pyqtgraph phasor window with ROI tools and mask feedback.
- **Plugin/Workflow Manager** — browse and run plugins, manage analysis steps.

All windows are independent `QWidget`/`QMainWindow` instances sharing the same `QApplication` event loop. The hub can open/close any window; closing a secondary window doesn't affect others.

**Why:** Dock widgets inside a single window get cluttered fast — especially with napari widgets accumulating. Separate windows can be spread across multiple monitors, sized independently, and arranged to match the user's workflow.

### 2. Storage: One HDF5 File Per Dataset

Each dataset is a self-contained `.h5` file. Datasets can have any combination of dimensions — the format must support all of these simultaneously:

**Possible dimensions:** tiles (S), channels (C), timepoints (T), z-slices (Z), spatial (H, W), TCSPC bins (FLIM time bins per pixel). Not every dataset has every dimension, but the .h5 structure accommodates any combination.

**HDF5 internal layout (three processing stages + analysis outputs):**

*Processing stages (each preserved):*
- `/tiles/` — Group containing individual tile arrays before stitching, with position metadata as attributes. Raw imported data.
- `/stitched` — The assembled composite image (post tile-stitching). Shape varies by data dimensions (e.g., (C, H, W), (C, T, Z, H, W)). Float32.
- `/intensity` — The final analysis-ready image. May be a Z-projection of `/stitched`, or identical to `/stitched` if no projection needed. **This is what all analysis tools read from.**

*FLIM data:*
- `/decay` — TCSPC FLIM data. Adds a TCSPC bin dimension (e.g., (C, H, W, TCSPC) or (C, T, Z, H, W, TCSPC)). Uint16. Extremely compressible.
- `/phasor/g`, `/phasor/s` — (H, W) float32 phasor coordinate maps

*Analysis outputs:*
- `/labels/<name>` — (H, W) int32 segmentation masks (e.g., `/labels/cellpose`, `/labels/manual`). Multiple can coexist.
- `/masks/<name>` — (H, W) uint8 binary masks (e.g., `/masks/otsu_ch1`, `/masks/adaptive_ch2`)
- `/measurements` — CSV string (pandas DataFrame)

*Metadata:*
- `/metadata/` — HDF5 attributes (source files, laser frequency, pixel size, time resolution, channel names, dimension labels, import params)

**Chunking strategy:** Align chunks with access patterns. Spatial chunks (256, 256) for 2D operations. Include full TCSPC axis in each chunk so reading a pixel's full decay is one chunk read. Compression: gzip level 4.

**Why HDF5 (not Zarr):** Self-contained single file, Fiji/MATLAB readable, supports arbitrary dimensionality natively, mature chunking/compression. No reason to switch — HDF5 handles all these dimensions.

### 3. Project Index: Flat CSV

A `project.csv` at the experiment root. Each row is one dataset. Columns include: `path`, `condition`, `replicate`, `notes`, `status`, and any user-defined columns. No hierarchy — group/filter with pandas as needed.

**Why:** No database, no schema migrations, no foreign keys, no cascading deletes. Loads as a DataFrame in one line. The rigid hierarchy (experiments -> conditions -> bio_reps -> fovs) caused problems in PerCell3.

**Important:** The term "FOV" is not used in PerCell4. Each .h5 file is a "dataset."

### 4. Communication: CellDataModel + Qt Signals

A `CellDataModel(QObject)` is the central hub:
- Holds a pandas DataFrame (one row per cell, columns evolve with analysis methods)
- Emits `data_updated` when measurements change
- Emits `selection_changed` when user selects/deselects cells
- All windows listen to these signals — they never talk to each other directly

**The DataFrame is ephemeral.** It exists only for interactive exploration of the currently loaded dataset (driving plots, table, selection highlighting). It is NOT persisted in memory across datasets. When a dataset is closed and another opened, measurements are recomputed fresh. Persistent measurements live in each .h5 file's `/measurements` dataset. Measurements can be exported but are not kept in the program.

**Multi-dataset reporting** (future batch mode) aggregates by reading from .h5 files and project.csv — not from in-memory DataFrames.

The exact DataFrame column structure is **deferred** — it will evolve as analysis methods are built.

### 5. Import Pipeline: TIFF -> HDF5 Conversion

Import is a one-time operation:
1. Scan directory for TIFFs, parse tokens (ch_, t_, s_, z_) to identify channels, timepoints, tiles, z-sections
2. Store individual tiles in `/tiles/` group with position metadata
3. Assemble: stitch tiles into composite image, combine channels, handle z-series
4. Store stitched result in `/stitched`
5. Z-projection (MIP or other method) if z-series — store projected result in `/intensity`. If no projection needed, `/intensity` is a copy/link of `/stitched`
6. Write to .h5 file, add row to project.csv

After import, the original TIFFs are no longer needed by the app. Individual tiles are preserved in the .h5 for reference or re-stitching.

**Carry forward from PerCell3:** The `FileScanner` token-parsing logic and tile stitching from `percell3/io/engine.py`.

### 6. Segmentation: Multiple Methods, Same Output

All segmentation methods produce the same output: an (H, W) int32 label array stored at `/labels/<name>` in the .h5 file. Methods:
- **Cellpose/Cellpose-SAM** — runs in QThread, label array added to .h5
- **Manual drawing** — napari label editing, saved back to .h5
- **ImageJ ROI import** — .zip file of ROI boundaries, converted to label array

No segmentation base class needed. Each method is just a function that returns a label array.

### 7. Analysis: Functions, Not Framework

Analysis tools are plain functions. A typical signature:

```
def analyze(image, labels, mask=None, **params) -> pd.DataFrame
```

Results are merged into the CellDataModel DataFrame. No AnalysisPlugin ABC, no registry ceremony. The app calls the function, gets a DataFrame back, merges it.

### 8. Plugin System: Simple Callable Convention

A plugin is a Python file in a `plugins/` folder. The app auto-discovers .py files and looks for a conventional entry point (e.g., a `run()` function or a module-level docstring for metadata). No base class, no registry, no entry_points.

**Why:** PerCell3's formal plugin system (ABCs, registry, entry_points) added complexity without proportional value. Start simple, add structure only if needed.

### 9. Dual Interface: GUI Interactive + CLI Batch

- **GUI:** Qt app for single-dataset interactive analysis. Load one dataset, segment, analyze, explore.
- **CLI:** Separate entry point for batch operations (import all, segment all, measure all across datasets).
- **Shared core:** Both interfaces call the same processing functions. The GUI wraps them in QThread; the CLI calls them directly.

### 10. Plotting: pyqtgraph

All plots use pyqtgraph in their own independent windows — Qt-native, GPU-accelerated, handles 100k+ points at 60fps. Critical for phasor plots where each pixel is a data point. Built-in ROI classes with drag signals for phasor-to-mask feedback.

**Not matplotlib** — it blocks the Qt event loop during rendering.

---

## What We're NOT Building (YAGNI)

- No database (SQLite, PostgreSQL, SQLAlchemy, Alembic)
- No DAG-based workflow engine (PerCell3's was over-engineered for this use case)
- No formal plugin ABCs or registry
- No experiment hierarchy (experiments -> conditions -> fovs)
- No OME-Zarr (HDF5 covers our needs)
- No web interface or multi-user support
- No migration from PerCell3 data

---

## Resolved Questions

1. **HDF5 scope:** One .h5 per dataset (not per experiment)
2. **Experiment index:** Flat CSV, no hierarchy, pandas-native
3. **Terminology:** "Dataset" not "FOV"
4. **Data dimensionality:** Any combination of S (tiles), C (channels), T (time), Z (depth), H, W (spatial), TCSPC (FLIM bins) — all can coexist in one dataset. Channels stacked with names as HDF5 attribute.
5. **Mask/label storage:** Same .h5 as image data
6. **Import model:** Convert TIFFs to .h5 upfront. Store individual tiles AND stitched result.
7. **Plugin system:** Simple callable convention, auto-discovered from folder
8. **Batch processing:** CLI for batch, GUI for interactive, shared processing functions
9. **DataFrame structure:** Deferred — evolves with analysis methods
10. **Multiple segmentations:** Supported simultaneously — multiple label layers can coexist
11. **Export formats:** CSV only for now
12. **HDF5 vs Zarr:** Staying with HDF5 — single file, Fiji/MATLAB compatible, arbitrary dimensionality

## Open Questions

None — all resolved.

---

## Build Order (Suggested)

1. **Core framework** — Launcher hub + multi-window framework + CellDataModel + signal wiring
2. **HDF5 store** — ExperimentStore class (read/write .h5 files)
3. **Import pipeline** — TIFF token parsing, assembly, .h5 creation, project.csv
4. **Segmentation** — Cellpose in QThread, label storage, napari overlay
5. **Basic measurements** — regionprops per cell, populate DataFrame
6. **Data panels** — pyqtgraph scatter, cell table, selection linking
7. **Thresholding/masks** — Otsu + other methods, mask storage, masked measurements
8. **Phasor analysis** — FLIM phasor calc, phasor panel, ROI-to-mask
9. **Additional analysis** — particle analysis, image math, custom scripts
10. **CLI batch mode** — batch import, segment, measure
11. **Plugins** — auto-discovery, callable convention

---

## Previous Versions Reference

Domain logic to carry forward (adapt to new architecture):
- **Import/token parsing:** `percell3/io/engine.py` (FileScanner, ImportEngine)
- **Tile stitching:** `percell3/io/engine.py` (grid types, snake/row-by-row)
- **Segmentation post-processing:** `percell3/segment/_engine.py` (edge removal, small cell filter)
- **Label processing:** `percell3/segment/label_processor.py` (regionprops extraction)
- **ROI import:** `percell3/segment/` (ImageJ .zip, Cellpose _seg.npy)
- **Measurements:** `percell3/measure/measurer.py` (bbox-optimized per-cell measurement)
- **Thresholding:** `percell3/measure/thresholding.py` (otsu, adaptive, triangle, li)
- **Particle analysis:** `percell3/measure/particle_analyzer.py`
- **Metrics:** `percell3/measure/metrics.py` (7 built-in NaN-safe metrics)
- **Napari viewer patterns:** `percell3/segment/viewer/` (layer loading, colormap detection)
