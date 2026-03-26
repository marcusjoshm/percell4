---
title: "feat: PerCell4 Core Architecture and Framework"
type: feat
date: 2026-03-25
---

# PerCell4 Core Architecture and Framework

## Enhancement Summary

**Deepened on:** 2026-03-25
**Sections enhanced:** All 12 phases + cross-cutting concerns
**Research agents used:** Python reviewer, Architecture strategist, Performance oracle, Code simplicity reviewer, Data integrity guardian, Best practices researcher, Learnings researcher, Context7 (h5py, pyqtgraph)

### Key Improvements
1. **Fix decay chunking** — (64, 64, N_bins) not (256, 256, N_bins). Current spec produces 128MB chunks that cripple FLIM performance.
2. **Use 2D histogram for phasor plot** — `ImageItem` + `histogram2d` instead of `ScatterPlotItem`. Renders in <50ms regardless of pixel count.
3. **Add session-based HDF5 reads** — Keep per-operation open/close for writes (crash-safe), but `with store.open_read()` for interactive sessions (set `rdcc_nbytes=64MB` chunk cache).
4. **Extract orchestration from viewer** — Viewer should emit intents, a controller coordinates store writes + measurement recomputation. Prevents viewer from becoming a god object.
5. **Simplify: defer plugin system, combine measurement phases** — Remove Phase 11, fold thresholding/particle analysis into Phase 5/7.
6. **Atomic writes for project.csv** — Write to temp file, then `os.replace()`. Never write in place.
7. **Cellpose version-compat adapter** — Use `getattr()` fallback for model class instantiation. Pin `cellpose>=3.0,<5.0`.
8. **Store dimension metadata** — Every HDF5 array gets a `dims` attribute (e.g., `["C", "H", "W"]`) to prevent axis-order misinterpretation.
9. **Streaming Z-projection** — Never load all Z-slices into memory; accumulate in-place with `np.maximum(result, slice, out=result)`.

### New Considerations Discovered
- HDF5 SWMR mode is NOT needed — adds complexity without benefit for a desktop app
- `shuffle=True` filter should be enabled alongside gzip — improves compression ratio at no speed cost
- `lzf` compression preferred over gzip for decay data (3-5x faster decompression for interactive use)
- napari viewer should be hosted via Python API (own window), not `_qt_viewer` embedding — simpler and less fragile
- Two-layer scatter rendering for selection sync — base layer (all points, cached) + highlight layer (selected only, fast redraws)

---

## Overview

Build the PerCell4 application framework from scratch: project scaffolding, multi-window Qt shell, HDF5 data store, import pipeline, segmentation, measurements, interactive data panels, thresholding, phasor analysis, CLI batch mode, and plugin system. Each phase produces something runnable and testable.

## Problem Statement / Motivation

PerCell3 suffered from architectural contradictions (4 schema versions), documentation bloat (100+ brainstorm docs), and over-engineering (SQLite + Zarr + DAG workflow + formal plugin ABCs for what is fundamentally a single-cell analysis tool). PerCell4 is a clean rebuild that keeps the proven domain logic but replaces the architecture with something simpler: HDF5 files, pandas DataFrames, Qt signals, and plain functions.

## Brainstorm Reference

`docs/brainstorms/2026-03-25-percell4-architecture-brainstorm.md` — all architecture decisions resolved.

---

## Target Project Structure

```
percell4/
├── src/percell4/
│   ├── __init__.py
│   ├── app.py              # QApplication + window management
│   ├── model.py            # CellDataModel (QObject + DataFrame + signals)
│   ├── store.py            # DatasetStore (HDF5 read/write)
│   ├── project.py          # ProjectIndex (project.csv management)
│   ├── io/
│   │   ├── __init__.py
│   │   ├── scanner.py      # FileScanner + token parsing
│   │   ├── models.py       # Dataclasses: TokenConfig, TileConfig, ScanResult
│   │   ├── assembler.py    # Tile stitching + dimension assembly
│   │   ├── readers.py      # TIFF/SDT/PTU format readers
│   │   └── importer.py     # Orchestrates scan -> assemble -> write HDF5
│   ├── segment/
│   │   ├── __init__.py
│   │   ├── cellpose.py     # Cellpose wrapper (plain function)
│   │   ├── postprocess.py  # Edge removal, small cell filter
│   │   └── roi_import.py   # ImageJ ROI .zip -> label array
│   ├── measure/
│   │   ├── __init__.py
│   │   ├── metrics.py      # NaN-safe metric functions
│   │   ├── measurer.py     # BBox-optimized per-cell measurement
│   │   ├── thresholding.py # Otsu, adaptive, triangle, li, manual
│   │   └── particle.py     # Particle analysis
│   ├── flim/
│   │   ├── __init__.py
│   │   ├── phasor.py        # Phasor G/S computation
│   │   └── wavelet_filter.py # DTCWT-based phasor denoising (from flimfret)
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── launcher.py     # Hub/launcher window
│   │   ├── viewer.py       # Napari viewer window wrapper
│   │   ├── data_plot.py    # pyqtgraph scatter/histogram window
│   │   ├── phasor_plot.py  # pyqtgraph phasor window + ROI
│   │   ├── cell_table.py   # QTableView window
│   │   └── workers.py      # QThread workers for long tasks
│   ├── cli/
│   │   ├── __init__.py
│   │   └── main.py         # Click CLI entry point
│   └── plugins/
│       └── __init__.py     # Auto-discovery logic
├── plugins/                # User plugin directory (outside package)
├── tests/
│   ├── conftest.py
│   ├── test_model.py
│   ├── test_store.py
│   ├── test_project.py
│   ├── test_io/
│   ├── test_segment/
│   ├── test_measure/
│   └── test_flim/
├── main.py                 # Dev entry point
├── pyproject.toml
├── .gitignore
└── CLAUDE.md
```

---

## Implementation Phases

### Phase 0: Project Scaffolding

**Goal:** Installable Python package with passing test suite.

**Create:**
- `pyproject.toml` — src layout, Python >=3.12, all deps with upper bounds (e.g., `napari>=0.5,<0.8`, `cellpose>=3.0,<5.0`), ruff (line-length 100), pytest config with `slow`/`gui` markers, optional deps: `gpu = ["cellpose[gpu]"]`, `dev = ["pytest", "pytest-qt", "ruff"]`. Entry points: `percell4 = "percell4.cli.main:cli"`, `percell4-gui = "percell4.app:main"`. Based on `/Users/leelab/percell3/pyproject.toml`.
- `.gitignore` — Python standard + `.venv/`, `*.h5`, `*.sdt`, `*.ptu`, `.DS_Store`
- `src/percell4/__init__.py` — `__version__ = "0.1.0"`
- `src/percell4/_types.py` — Shared type aliases: `LabelArray = NDArray[np.int32]`, `IntensityImage = NDArray[np.float32]`, and cross-module dataclasses like `DatasetMetadata`.
- `src/percell4/model.py` — `CellDataModel(QObject)` with `data_updated`/`selection_changed` signals, holds `pd.DataFrame`. Port from prototype `main.py:21-40`. The DataFrame is **ephemeral** — for interactive exploration only, not persisted across datasets. Contract: listeners get read-only view, never modify the DataFrame directly.
- `tests/conftest.py` — `tmp_h5` fixture, `sample_labels` fixture (synthetic 100x100 with 5 cells), `sample_image` fixture
- `tests/test_model.py` — Smoke tests for signal emission

**Verify:**
```bash
git init && git add -A && git commit -m "Initial scaffolding"
pip install -e ".[dev]"
pytest tests/test_model.py -v
```

---

### Phase 1: Multi-Window Framework

**Goal:** Launch app, see hub window, open/close napari viewer window.

**Key design: CellDataModel is ephemeral.** The DataFrame is computed on the fly for whatever dataset is loaded in the viewer. It is NOT persisted in memory across datasets. When the user closes a dataset and opens another, measurements are simply recomputed. Persistent measurements live in each .h5 file — the in-memory DataFrame is only for interactive exploration (plots, table, selection). Measurements can be exported but are not kept in the program.

**Multi-dataset reporting** (future batch mode) will aggregate by reading from .h5 files and project.csv — not from in-memory DataFrames.

**Create:**
- `src/percell4/app.py` — `main()`: creates QApplication, CellDataModel, LauncherWindow, enters event loop
- `src/percell4/gui/launcher.py` — `LauncherWindow(QMainWindow)` — see detailed UI spec below
- `src/percell4/gui/viewer.py` — `ViewerWindow(QWidget)` — see detailed UI spec below
- `src/percell4/gui/data_plot.py` — `DataPlotWindow(QWidget)` — see Phase 6 detail
- `src/percell4/gui/phasor_plot.py` — `PhasorPlotWindow(QWidget)` — see Phase 8 detail
- `src/percell4/gui/cell_table.py` — `CellTableWindow(QWidget)` — see Phase 6 detail
- `src/percell4/gui/workers.py` — Generic `Worker(QThread)` with `finished(object)`, `progress(str)`, `error(str)` signals + `request_abort()` for cancellation

### Window Management

**Key pattern:** Launcher holds `{"viewer": ViewerWindow | None, "data_plot": DataPlotWindow | None, ...}`. Opening existing window does `show()/raise_()/activateWindow()`. Closing a secondary window **hides** it (not destroys) — preserves state, signal connections, and window position for re-open. All windows receive CellDataModel at construction. **Do NOT port the dock widget pattern from `main.py` — each panel is its own top-level window.**

**Window geometry persistence:** Save/restore window positions across sessions via `QSettings("LeeLabPerCell4", "PerCell4")`. Each window saves geometry in `closeEvent`, restores in `__init__`.

**Orchestration:** The viewer and launcher should NOT contain business logic. They emit intents ("user requested segmentation with params X"). A controller (method cluster on launcher, or separate lightweight object) coordinates: run worker -> write to store -> recompute measurements -> update model. This prevents any single window from becoming a god object.

**Error propagation:** Worker errors surface as status bar messages or modal dialogs, never silently vanish. The generic Worker's `error(str)` signal must be connected to visible user feedback.

---

### Launcher Window — Detailed UI Spec

**Layout:** Sidebar + content area pattern (like PerCell3 CLI menus, but graphical).

```
┌──────────────────┬────────────────────────────────────────┐
│                  │                                        │
│  [Viewer]        │  Sub-menu content area                 │
│  [Analysis]      │                                        │
│  [FLIM]          │  Shows options/controls for the        │
│  [Scripts]       │  selected sidebar category.            │
│  [Workflows]     │                                        │
│  [Data]          │  Simple operations run inline here.    │
│                  │  Complex features open their own       │
│                  │  window.                               │
│                  │                                        │
├──────────────────┴────────────────────────────────────────┤
│  Status bar: current project, loaded dataset, progress    │
└───────────────────────────────────────────────────────────┘
```

**File menu (QMenuBar):** New Project, Open Project, Recent Projects, Import Dataset, Quit

**Sidebar categories and their sub-options:**

#### 1. Viewer
- **Open Viewer** — launches/raises the napari ViewerWindow
- **Load Dataset** — file dialog to select .h5 file, loads into viewer
- **Close Dataset** — clears viewer, clears CellDataModel

#### 2. Analysis
- **Segmentation** — inline panel with:
  - Method selector: Cellpose / Import ROIs / Manual (opens viewer for drawing)
  - Cellpose params: model type dropdown, diameter spinbox, GPU checkbox
  - "Run" button → Worker → writes labels to .h5 → updates viewer
  - Post-processing: edge removal checkbox + margin, min area spinbox
- **Thresholding** — inline panel with:
  - Channel selector dropdown
  - Method: Otsu / Triangle / Li / Adaptive / Manual
  - Manual value spinbox (shown only for manual method)
  - Gaussian sigma spinbox (optional smoothing)
  - "Preview" button → shows mask overlay in viewer
  - "Apply" button → writes mask to .h5, triggers masked measurements
- **Measurements** — inline panel with:
  - Channel checkboxes (which channels to measure)
  - Metric checkboxes (mean, max, min, integrated, std, median, area)
  - Active segmentation dropdown (which label set to use)
  - "Measure" button → computes → populates CellDataModel
  - "Open Data Plot" / "Open Cell Table" buttons → launches those windows
- **Particle Analysis** — inline panel with:
  - Threshold mask selector
  - Min particle area spinbox
  - "Analyze" button → computes → adds particle columns to DataFrame
- *[Placeholder: Image Calculator]* — future
- *[Placeholder: Background Subtraction]* — future

#### 3. FLIM
- **Phasor Analysis** — inline panel with:
  - Harmonic selector (1, 2, 3)
  - "Compute Phasor" button → chunked computation → writes G/S to .h5
  - "Open Phasor Plot" button → launches PhasorPlotWindow
  - Calibration section: reference G/S inputs, frequency MHz input
- **Wavelet Filter** — inline panel with:
  - Filter level spinbox (default 9)
  - "Apply Filter" button → runs DTCWT denoising → writes filtered G/S
  - Status: shows filtered vs unfiltered toggle in viewer
- **Lifetime Map** — inline panel with:
  - "Compute Lifetime" button → tau from filtered phasor
  - Colormap selector for lifetime display
- *[Placeholder: FRET Analysis]* — future
- *[Placeholder: Multi-Harmonic]* — future

#### 4. Scripts
- *[Placeholder]* — future macro/Python script system
- "Run Script..." file dialog to select and execute a .py file
- Script output displayed in content area

#### 5. Workflows
- *[Placeholder]* — future workflow chaining system
- Pre-built workflows (e.g., "Standard Analysis": import → segment → measure → export)
- Custom workflow builder

#### 6. Data
- **Export Measurements** — inline: format selector (CSV), path selector, "Export" button
- **Project Browser** — inline: table showing datasets from project.csv with condition, status columns
- **Dataset Info** — inline: metadata from current .h5 (dimensions, channels, cell count)
- *[Placeholder: Prism Export]* — future
- *[Placeholder: Batch Export]* — future

---

### Viewer Window — Detailed UI Spec

**Layout:** Napari viewer (central) + layer management side panel (right)

```
┌───────────────────────────────────┬──────────────────────┐
│                                   │ Layer Manager         │
│   napari.Viewer                   │                      │
│   (_qt_viewer embedded)           │ Active Segmentation: │
│                                   │ [dropdown ▾]         │
│   - Image layers (channels)      │                      │
│   - Labels layers (segmentation) │ Active Mask:          │
│   - Mask overlays                │ [dropdown ▾]         │
│                                   │                      │
│                                   │ Layers:              │
│                                   │ ☑ DAPI (blue)       │
│                                   │ ☑ GFP (green)       │
│                                   │ ☑ Cellpose labels   │
│                                   │ ☐ Otsu mask         │
│                                   │ ☐ Phasor ROI mask   │
│                                   │                      │
│                                   │ [Save Labels]        │
│                                   │ [Save Mask]          │
└───────────────────────────────────┴──────────────────────┘
```

**Features:**
- **napari viewer** as central widget (`_qt_viewer` embedded, or napari managing its own window)
- **Layer manager side panel** (QDockWidget within the ViewerWindow, NOT a separate top-level window):
  - **Active segmentation dropdown:** selects which label set drives measurements and CellDataModel. Lists all `/labels/<name>` from the .h5.
  - **Active mask dropdown:** selects which binary mask is used for masked measurements. Lists all `/masks/<name>` from the .h5.
  - **Layer visibility checkboxes:** toggle image channels, label overlays, mask overlays
  - **Save Labels / Save Mask buttons:** writes current napari edits back to .h5
- **Selection sync:** clicking a label in napari → `CellDataModel.set_selection()` → highlights in data plot + cell table. Clicking background (label 0) → clears selection.
- **Channel colormaps:** auto-detect from channel names (DAPI→blue, GFP→green, RFP→red, etc.) — carry forward from PerCell3 `percell3/segment/viewer/_viewer.py`

---

### Data Plot Window — Detailed UI Spec

```
┌──────────────────────────────────────────┐
│  X: [area ▾]    Y: [DAPI_mean ▾]        │
├──────────────────────────────────────────┤
│                                          │
│   ● ● ●    ●                             │
│     ●  ●●●   ●    ● = cell (cyan)       │
│   ●   ●●●●●   ●   ◉ = selected (red)   │
│     ●  ●●●  ●                            │
│   ●    ●  ●●   ●                         │
│          ●    ●                           │
│                                          │
├──────────────────────────────────────────┤
│  Selected: 3 cells | Total: 842 cells    │
└──────────────────────────────────────────┘
```

**Features:** (detailed in Phase 6 above)
- Two-layer scatter (base + highlight)
- X/Y column dropdown selectors (populated from DataFrame numeric columns)
- Click point → select cell → sync to viewer + table
- Shift+drag rectangle → multi-select
- Status bar showing selection count and total cells

---

### Phasor Plot Window — Detailed UI Spec

```
┌──────────────────────────────────────────┐
│  Harmonic: [1 ▾]  [Filtered ☑]          │
├──────────────────────────────────────────┤
│                                          │
│        ╭─── universal semicircle         │
│       ╱  ░░░                             │
│      ╱ ░░█████░░  ← density histogram   │
│     ╱ ░██████████░                       │
│    │ ░████████████░  ╭──╮ ← ROI ellipse │
│    │  ░███████████░  │  │               │
│     ╲  ░░█████░░    ╰──╯               │
│      ╲    ░░░                            │
│       ╲                                  │
│  G ───────────────────────────────── →   │
├──────────────────────────────────────────┤
│  ROI: 23% of pixels | [Apply as Mask]    │
└──────────────────────────────────────────┘
```

**Features:** (detailed in Phase 8 above)
- 2D histogram density image (log-scale, viridis colormap)
- Universal semicircle overlay (dashed white line)
- EllipseROI for selecting lifetime populations
- Harmonic selector dropdown
- Filtered/unfiltered toggle checkbox
- "Apply as Mask" button → writes phasor ROI mask to .h5 and viewer
- Status bar showing ROI statistics (fraction of pixels inside)
- Optional: overlay selected cells as scatter points on top of histogram

---

### Cell Table Window — Detailed UI Spec

```
┌──────────────────────────────────────────────────────────┐
│  Filter: [condition ▾] = [all ▾]   [Export CSV]          │
├────┬──────┬──────────┬───────────┬───────────┬───────────┤
│ ID │ Area │ DAPI_mean│ GFP_mean  │ g_mean    │ s_mean    │
├────┼──────┼──────────┼───────────┼───────────┼───────────┤
│  1 │  342 │    1842  │     923   │   0.412   │   0.389   │
│  2 │  518 │    2104  │    1156   │   0.398   │   0.401   │
│ ►3 │  287 │    1563  │     834   │   0.425   │   0.375   │ ← selected
│  4 │  465 │    1891  │    1022   │   0.408   │   0.392   │
├────┴──────┴──────────┴───────────┴───────────┴───────────┤
│  Showing 842 cells | 3 selected                          │
└──────────────────────────────────────────────────────────┘
```

**Features:** (detailed in Phase 6 above)
- QTableView backed by PandasTableModel
- Column header click → sort (via QSortFilterProxyModel)
- Row click → select cell → sync to viewer + scatter
- Shift/Ctrl-click for multi-select
- Right-click context menu: Export Selection, Export All
- "Export CSV" button in toolbar
- Status bar showing total and selected cell counts

**Verify:** `python main.py` — hub appears with sidebar. Click "Viewer" → "Open Viewer" creates napari window. Click "Analysis" shows segmentation/threshold/measure sub-options. Close hub closes everything.

---

### Phase 2: HDF5 DatasetStore

**Goal:** Read/write all data groups in a .h5 file. Pure data layer, no GUI dependency.

**Create:**
- `src/percell4/store.py` — `DatasetStore` class:
  - Core API: `write_array(hdf5_path, array, attrs=None)`, `read_array(hdf5_path)`, `write_dataframe(hdf5_path, df)`, `read_dataframe(hdf5_path)`, `list_groups(prefix)`, `metadata` property
  - Convenience helpers: `write_labels(name, array)` (enforces int32), `write_mask(name, array)` (enforces uint8), `read_labels(name)`, `read_mask(name)`
  - **Writes:** Opens/closes per operation (crash-safe). Every write returns count. Validates dtype/shape.
  - **Reads:** Default per-operation. Also supports session mode: `with store.open_read() as s:` for interactive use — keeps file open with `rdcc_nbytes=64MB` chunk cache for fast repeated reads.
  - **Every HDF5 array gets a `dims` attribute** (e.g., `["C", "H", "W"]`) to prevent axis-order misinterpretation.
  - Chunking: **(64, 64, N_bins)** for decay (NOT 256x256 — that produces 128MB chunks), **(256, 256)** for spatial-only arrays.
  - Compression: `gzip(4) + shuffle=True` for spatial data. **`lzf`** for decay data (3-5x faster decompression for interactive use).
  - HDF5 crash safety: For large writes (import), use write-to-temp-then-`os.replace()` pattern.

- `src/percell4/project.py` — `ProjectIndex`: thin wrapper around `pd.read_csv()`/`to_csv()` with atomic writes (temp file + `os.replace()`). Include `status` column (`importing`, `complete`, `error`). Add `reconcile()` method that scans for orphan .h5 files not in CSV.

**Verify:** `pytest tests/test_store.py tests/test_project.py -v`

**Reuse from PerCell3:** HDF5 patterns from `percell3/core/zarr_io.py` (adapted from Zarr to h5py)

---

### Phase 3: Import Pipeline

**Goal:** Scan TIFF directory, parse tokens, assemble tiles, write .h5.

**Create:**
- `src/percell4/io/models.py` — Frozen dataclasses: `TokenConfig`, `TileConfig`, `DiscoveredFile`, `ScanResult`. Adapted from `/Users/leelab/percell3/src/percell3/io/models.py`
- `src/percell4/io/scanner.py` — `FileScanner`: walk dir, match TIFFs, parse tokens (ch_, t_, s_, z_) via regex. Adapted from `/Users/leelab/percell3/src/percell3/io/scanner.py`
- `src/percell4/io/readers.py` — `read_tiff()`, `read_sdt()`, `read_flim_bin()`, `read_tiff_metadata()`
- `src/percell4/io/assembler.py` — `assemble_tiles()`, `assemble_channels()`, `project_z(method="mip")`
- `src/percell4/io/importer.py` — `import_dataset(source_dir, output_h5, token_config, tile_config, project_csv) -> int`. Takes `progress_callback` for GUI integration.

**Key design:** Scanner and assembler have zero dependency on HDF5 — they work with numpy arrays only. Importer orchestrates everything.

### Research Insights (Phase 3)

**From PerCell3 learnings:**
- **Streaming Z-projection** — never `np.stack()` all slices then reduce. Accumulate in-place: `np.maximum(result, read_tiff(path), out=result)` for MIP. Saves N x image_size memory.
- **Integer overflow** — always `dtype=np.int64` for integer sums during Z-projection.
- **TokenConfig validation** — `__post_init__()` must validate regex patterns (length <200, valid compilation via `re.compile()`).
- **Explicit error for unknown methods** — never use bare `else:` for enum-like params. `raise ValueError(f"Unknown method: {method!r}")`.
- **Test with real microscope filenames** — e.g., `30min_Recovery_+_VCPi_Merged_ch00_z00.tif` breaks narrow heuristics.
- **Write HDF5 first, then update project.csv** — orphan .h5 files are harmless; orphan CSV rows are confusing.

**Memory-bounded tile assembly:** Write tiles directly into a pre-allocated output array (or HDF5 dataset via region-write) rather than building intermediate arrays. Prevents 2x memory spike during import.

**Verify:** `pytest tests/test_io/ -v` — integration test: fake TIFFs -> import -> verify .h5 contents

**Reuse from PerCell3:** Token parsing from `percell3/io/scanner.py`, tile stitching grid types from `percell3/io/engine.py`

### .bin TCSPC Reader (from bin_reader — carry forward)

**Source:** `/Users/leelab/bin_reader/flim_bin_reader.py`, `/Users/leelab/bin_reader/bin_to_tif.py`

Reads pre-aggregated TCSPC histogram data from raw .bin files (Becker & Hickl, PicoQuant, or generic exports). The .bin format is unstructured binary — user must specify dimensions and dtype.

**Adapt as:** `read_flim_bin()` in `src/percell4/io/readers.py`:
```
def read_flim_bin(
    filepath: str,
    x_dim: int = 512, y_dim: int = 512, t_dim: int = 132,
    dtype: str = "uint16",
    byte_order: str = "little",
    dim_order: str = "YXT",
    header_bytes: int = 0,
) -> dict
    """Returns {'decay': ndarray (H, W, T), 'intensity': ndarray (H, W), 'metadata': dict}"""
```

**Key logic from bin_reader:**
- Load flat array: `np.fromfile(filepath, dtype=np_dtype)`
- Validate element count: `len(data) == x_dim * y_dim * t_dim`
- Reshape with configurable dimension order: `data.reshape(shape)` then `np.transpose()` to canonical (T, Y, X) or (H, W, T)
- Auto-detect header: if file_size exceeds expected by <1000 bytes, skip the excess
- Intensity = `data.sum(axis=-1)` (sum over time bins)
- Includes dimension order diagnostic tool for unknown formats (try all 6 permutations of 3 dims)

**For PerCell4 import pipeline:** .bin files bypass the token-based TIFF scanner. They enter the pipeline directly as a single dataset. The importer detects .bin extension and calls `read_flim_bin()` instead of the TIFF scanner. The resulting (H, W, T) array is written to `/decay` in the .h5 file, with intensity written to `/intensity`.

**Metadata note:** .bin files contain NO metadata. Laser frequency, time resolution, and pixel size must be provided by the user at import time (or from a companion config file).

---

### Phase 4: Segmentation

**Goal:** Cellpose, manual labels, ROI import — all produce (H,W) int32 label arrays.

**Create:**
- `src/percell4/segment/cellpose.py` — `run_cellpose(image, model_type="cyto3", diameter=None, gpu=False) -> LabelArray`. Lazy-imports cellpose. **Use `getattr()` fallback for version-compatible instantiation:** `_Model = getattr(models, "CellposeModel", None) or getattr(models, "Cellpose")` (Cellpose 4.0 broke the API — this pattern handles both v3 and v4).
- `src/percell4/segment/postprocess.py` — `filter_edge_cells()`, `filter_small_cells()`, `relabel_sequential()`. All return `(new_labels, removed_count)`.
- `src/percell4/segment/roi_import.py` — `import_imagej_rois(zip_path, shape)`, `import_cellpose_seg(seg_path)`
- GUI integration in `viewer.py` — "Run Cellpose" button, Worker wraps `run_cellpose`, writes to store, adds napari Labels layer

**Key design:** Segmentation functions are pure: image in, labels out. No store/GUI coupling.

**Verify:** `pytest tests/test_segment/ -v -m "not slow"`

**Reuse from PerCell3:** Post-processing from `percell3/segment/_engine.py`, ROI import from `percell3/segment/`

---

### Phase 5: Basic Measurements

**Goal:** Per-cell metrics from labels + image -> DataFrame -> CellDataModel.

**Create:**
- `src/percell4/measure/metrics.py` — 7 NaN-safe metric functions + `BUILTIN_METRICS` dict. Direct port from `/Users/leelab/percell3/src/percell3/measure/metrics.py`. Each metric follows the signature `(image_crop: NDArray, cell_mask: NDArray[bool]) -> float` using `np.nan*` functions (nanmean, nanmax, nanmin, nansum, nanstd, nanmedian). The `area` metric counts True pixels in the mask. All return `float` explicitly.
- `src/percell4/measure/measurer.py` — Main measurement function:

```
def measure_cells(
    image: NDArray,           # (H, W) single channel
    labels: NDArray[np.int32], # (H, W) label array
    metrics: list[str] | None = None,  # None = all builtins
    mask: NDArray[np.uint8] | None = None,  # binary threshold mask
) -> pd.DataFrame
```

### Research Insights (Phase 5) — from PerCell3 code analysis

**BBox optimization (critical for performance):**
1. Run `skimage.measure.regionprops(labels)` once to extract bounding boxes for all cells
2. For each cell: crop labels and image to bbox, create binary mask `cell_mask = label_crop == label_val`
3. Compute all metrics on the crop, not the full image
4. PerCell3 stores bbox as columns (bbox_x, bbox_y, bbox_w, bbox_h) — reusable across channels

**Multi-channel measurement:**
- Call `measure_cells()` once per channel, each returns a DataFrame
- Prefix column names with channel: `{channel}_{metric}` (e.g., `DAPI_mean_intensity`)
- Merge all channel DataFrames on the `label` column
- Core cell properties (label, centroid, bbox, area) come from the first call only

**Masked measurement scopes (from PerCell3):**
- When `mask` is provided, three measurement scopes:
  - `whole_cell`: metrics on all pixels in cell (ignore mask)
  - `mask_inside`: metrics on pixels where `cell_mask & (mask > 0)`
  - `mask_outside`: metrics on pixels where `cell_mask & ~(mask > 0)`
- PerCell3 normalizes uint8 masks (0/255) to boolean. PerCell4 should store masks as uint8 (0/1) to avoid this conversion.
- If scoped_mask is empty (no pixels), set value to 0.0 (not NaN) — per PerCell3 convention.

**Output DataFrame columns:**
- `label` (int32) — cell ID from label array
- `centroid_y`, `centroid_x` (float) — from regionprops
- `bbox_y`, `bbox_x`, `bbox_h`, `bbox_w` (int) — for reuse in future crops
- `area` (float) — pixel count
- One column per (channel, metric) pair: `{channel}_{metric}`

**Performance strategy (from best-practices research):**
- Use `scipy.ndimage.find_objects(labels)` once — returns list of `(slice_row, slice_col)` bounding boxes in a single C-level pass. Reuse across all channels.
- For simple metrics (mean, sum, std) without NaN, `scipy.ndimage.mean/sum_labels` is fastest (single C-level pass, no Python loop). For a 10K x 10K image: ~1 second.
- For NaN-safe or custom metrics, use the `find_objects` loop with `np.nanmean(image[sl][mask])`.
- **Never copy the full image.** `image[sl]` returns a view. Only `image[sl][mask]` creates a copy (cell-sized ~KB).
- Pre-compute `find_objects` once, store slices, reuse across channels and measurement passes.

**Particle analysis (carried from PerCell3 + CellProfiler patterns):**
- Global `scipy.ndimage.label(particle_mask)` for connected components
- Per cell: `np.unique(particle_labels[sl][cell_mask])` for particles overlapping the cell
- Morphometrics via `skimage.measure.regionprops` with `intensity_image` per particle
- 11 summary metrics per cell: particle_count, total/mean/max area, coverage_fraction, intensity stats
- Cells with 0 particles get zero values (not NaN)

**Edge cases:**
- **Empty label arrays (0 cells):** Return empty DataFrame with correct column dtypes
- **Background label (0):** Skip — never measure label 0
- **Labels not sequential:** Call `relabel_sequential()` during postprocess (Phase 4), BEFORE any measurements. This renumbers labels to [1..N] to avoid memory waste in `find_objects()`. Cell IDs in the label array and DataFrame always match because both derive from the same (already relabeled) array. **Never relabel after measuring** — that would break the ID linkage. If labels are edited manually, re-measure from scratch (the ephemeral DataFrame design handles this naturally).
- **NaN in image:** All metrics are NaN-safe via `np.nan*` functions
- **Large "background" label:** Filter out abnormally large labels (> 50% of image area) with a warning

**Verify:** `pytest tests/test_measure/ -v` — test with synthetic image (100x100, 5 known blobs of known intensity), verify exact metric values.

---

### Phase 6: Data Panels + Selection Linking

**Goal:** Scatter plot, cell table, bidirectional selection sync across all windows.

**Create:**
- `src/percell4/gui/data_plot.py` — `DataPlotWindow(QWidget)`:
  - pyqtgraph `PlotWidget` with two `ScatterPlotItem` layers (base + highlight)
  - Two `QComboBox` dropdowns for X and Y axis column selection (populated from DataFrame numeric columns)
  - Listens to `CellDataModel.data_updated` to refresh base scatter
  - Listens to `CellDataModel.selection_changed` to update highlight layer only
  - `sigClicked` on base scatter -> extract label ID from point `data` field -> `CellDataModel.set_selection([label_id])`

- `src/percell4/gui/cell_table.py` — `CellTableWindow(QWidget)`:
  - `QTableView` backed by a custom `PandasTableModel(QAbstractTableModel)` wrapping the DataFrame
  - `PandasTableModel` implements `rowCount()`, `columnCount()`, `data()`, `headerData()` — reads directly from `CellDataModel.df` (no copy)
  - Row click -> `CellDataModel.set_selection([label_id])`
  - `selection_changed` signal -> scroll to and highlight corresponding row
  - Right-click context menu: "Export Selection to CSV" and "Export All to CSV"
  - Column header click enables sorting via `QSortFilterProxyModel`

### Research Insights (Phase 6)

**Two-layer scatter rendering (critical for performance):**
```
base_scatter = ScatterPlotItem(size=2, pen=None, brush=(0, 255, 255, 80), pxMode=True)
highlight_scatter = ScatterPlotItem(size=10, pen=mkPen('r', width=2), brush=None, pxMode=True)
```
- Base layer: all points, uniform style, set once via `setData(x, y, data=label_ids)`. Rarely changes.
- Highlight layer: only selected points. On `selection_changed`, clear and re-set with just the selected cells. Fast redraws (1-100 points vs 100K).
- Use `data` parameter on `addPoints()` to associate each point with its cell label ID. On `sigClicked`, retrieve `point.data()` to get the label.

**Scatter-to-napari reverse selection:**
- When scatter point is clicked, call `CellDataModel.set_selection([label_id])`
- Napari viewer listens to `selection_changed` and sets `labels_layer.selected_label = label_id`
- Visual feedback in napari: `selected_label` changes the paint tool's active label. For stronger visual highlighting, consider temporarily modifying the label colormap for the selected cell (e.g., brighter color).

**Multi-select patterns:**
- Scatter: use pyqtgraph `RectROI` or custom rubber-band selection. On ROI change, find all points inside, emit `set_selection(list_of_ids)`.
- Table: `QTableView.setSelectionMode(QAbstractItemView.ExtendedSelection)` for shift-click and ctrl-click multi-row select.
- Napari: `selected_label` is single-value. For multi-select, maintain the list in `CellDataModel._selected_ids` and highlight in scatter/table. Napari doesn't natively support multi-label highlighting.

**Deselection:** Clicking background (label 0) in napari should call `set_selection([])` (empty list, not `[0]`). Guard: `if label_id == 0: model.set_selection([])`.

**QTableView performance:**
- `QAbstractTableModel` is inherently lazy — Qt only calls `data()` for visible rows (~30 at a time). No lazy loading needed.
- Use `df.iat[row, col]` (fastest single-element access) or cache `df.values` as numpy array for `data()`.
- Format floats as `f"{value:.4g}"` for compact display. Show empty string for NaN.
- Use `QSortFilterProxyModel` wrapping the model for sort (click column header) and filter support. Set `sortRole=Qt.UserRole` to sort by raw values, not display strings.

**Rectangle selection in scatter:** Shift+drag draws a rubber-band rectangle. On release, find all scatter points within the rectangle bounds, extract their cell IDs from `point.data()`, emit `set_selection(list_of_ids)`.

**Verify:** Manual test — open all windows, segment + measure, click cells in any window, verify cross-highlighting.

---

### Phase 7: Thresholding, Masks, and Particle Analysis

**Goal:** Binary masks from threshold methods, masked measurements, and particle analysis within cells.

**Create:**
- `src/percell4/measure/thresholding.py` — Pure functions, adapted from `/Users/leelab/percell3/src/percell3/measure/thresholding.py`:

```
def threshold_otsu(image: NDArray) -> tuple[NDArray[np.uint8], float]
def threshold_adaptive(image: NDArray, block_size: int | None = None) -> tuple[NDArray[np.uint8], float]
def threshold_triangle(image: NDArray) -> tuple[NDArray[np.uint8], float]
def threshold_li(image: NDArray) -> tuple[NDArray[np.uint8], float]
def threshold_manual(image: NDArray, value: float) -> tuple[NDArray[np.uint8], float]
def apply_gaussian_smoothing(image: NDArray, sigma: float | None) -> NDArray
```

All return `(mask_uint8, threshold_value)`. Masks stored as uint8 (0/1 not 0/255). Start with otsu + manual — add others when requested.

- `src/percell4/measure/particle.py` — Particle analysis within cells, adapted from `/Users/leelab/percell3/src/percell3/measure/particle_analyzer.py`:

```
def analyze_particles(
    image: NDArray,
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> pd.DataFrame
```

Returns DataFrame with one row per cell, columns for 11 particle summary metrics: `particle_count`, `total_particle_area`, `mean_particle_area`, `max_particle_area`, `particle_coverage_fraction`, `mean_particle_mean_intensity`, etc.

### Research Insights (Phase 7) — from PerCell3 code analysis

**Threshold method selection guide:**
- **Otsu** — best for bimodal histograms (e.g., bright puncta on dark background). Most common starting point.
- **Triangle** — better for skewed histograms where one peak dominates.
- **Li** — iterative minimum cross-entropy. Good for low-contrast images.
- **Adaptive** — local thresholding via `skimage.filters.threshold_local()`. PerCell3 auto-calculates block_size: `max(15, (min(image.shape) // 10) | 1)` (ensures odd, >=15). Best for uneven illumination.
- **Manual** — user provides exact value. Essential for reproducibility.

**Gaussian smoothing before thresholding:** Optional preprocessing via `scipy.ndimage.gaussian_filter()`. Applied to float32 copy. Sigma=None or <=0 means no smoothing.

**How masks interact with per-cell measurements (from PerCell3):**
The masked measurement flow:
1. Threshold produces binary mask (H, W) uint8
2. For each cell, crop mask to cell's bbox: `mask_crop = mask[by:by+bh, bx:bx+bw]`
3. Compute scoped masks:
   - `inside = cell_mask & (mask_crop > 0)` — pixels in both cell AND threshold
   - `outside = cell_mask & ~(mask_crop > 0)` — pixels in cell but NOT threshold
4. Compute metrics on scoped pixels. If scope is empty, value = 0.0.
5. Add columns: `{channel}_{metric}_mask_inside`, `{channel}_{metric}_mask_outside`

**Particle analysis workflow (from PerCell3):**
1. For each cell: crop labels, mask, image to bbox
2. Create particle_mask: `threshold_bool[crop] & (label_crop == cell_label)`
3. Run `scipy.ndimage.label()` on particle_mask for connected components
4. Run `skimage.measure.regionprops()` with `intensity_image` for morphometrics per particle
5. Filter particles by `min_area`
6. Aggregate per-cell: count, total/mean/max area, coverage fraction, intensity stats
7. Cells with no particles get zero values (not NaN)

**Edge cases from PerCell3:**
- Adaptive block_size must be odd — use `| 1` bitwise OR
- Group thresholding normalizes stats to group area only (denominator = cell_mask pixels, not full image)
- Particle coordinate transform: local regionprops centroids + bbox offset = dataset-level coordinates
- Circularity computed manually: `4 * pi * area / perimeter**2`

**Verify:** `pytest tests/test_measure/test_thresholding.py tests/test_measure/test_particle.py -v`

---

### Phase 8: Phasor Analysis (FLIM)

**Goal:** Compute phasor G/S from TCSPC data, display phasor plot, ROI selection -> spatial mask, per-cell phasor metrics.

**Create:**
- `src/percell4/flim/phasor.py` — Core phasor computation functions:

```
def compute_phasor(
    decay_stack: NDArray,  # (H, W, T) or h5py.Dataset
    harmonic: int = 1,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]
    """Returns (g_map, s_map) each shape (H, W)."""

def compute_phasor_chunked(
    decay_dset: h5py.Dataset,  # HDF5 dataset, NOT loaded into memory
    harmonic: int = 1,
    chunk_size: int = 64,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]
    """Process decay data in spatial chunks from HDF5. Memory-bounded."""

def phasor_to_lifetime(
    g: NDArray, s: NDArray, frequency_mhz: float,
) -> NDArray[np.float32]
    """Phase lifetime: tau_phi = s / (2 * pi * f * g). Returns (H, W) in nanoseconds."""

def phasor_roi_to_mask(
    g_map: NDArray, s_map: NDArray,
    center: tuple[float, float], radii: tuple[float, float],
) -> NDArray[np.bool_]
    """Ellipse in phasor space -> spatial boolean mask. Vectorized numpy."""
```

- `src/percell4/gui/phasor_plot.py` — `PhasorPlotWindow(QWidget)`:

### Research Insights (Phase 8)

**Phasor math — direct cosine/sine transform (NOT FFT):**
The prototype `main.py` uses the correct approach: direct cosine/sine transform is more numerically stable than FFT for phasor computation and avoids the overhead of computing all harmonics when you only need one:
```
omega = 2 * pi * harmonic / n_bins
g = (decay * cos(omega * t)).sum(axis=-1) / total
s = (decay * sin(omega * t)).sum(axis=-1) / total
```
This is ~10 lines of numpy. FFT computes all harmonics (wasteful when you only need harmonic 1).

**Chunked computation (critical for large FLIM data):**
For a 10K x 10K x 256 decay stack (~50GB uncompressed):
1. Pre-allocate output: `g_map = np.empty((H, W), dtype=np.float32)`, `s_map = np.empty(...)`
2. Pre-compute trig vectors: `cos_vec = np.cos(omega * np.arange(n_bins))`, `sin_vec = np.sin(...)`
3. Iterate spatial chunks matching HDF5 layout (64x64): `chunk = decay_dset[y:y+64, x:x+64, :]`
4. Compute phasor on chunk: `g_chunk = (chunk * cos_vec).sum(axis=-1) / total_chunk`
5. Write into output: `g_map[y:y+64, x:x+64] = g_chunk`
Peak memory: one chunk (~2MB at 64x64x256 uint16) + output maps (~400MB each for 10K x 10K float32). Bounded regardless of input size.

**2D histogram phasor plot (critical for rendering):**
- `np.histogram2d(g_map.ravel(), s_map.ravel(), bins=512, range=[[-0.1, 1.1], [-0.1, 0.7]])` -> (512, 512) density image
- Render with `pyqtgraph.ImageItem`: `img.setImage(np.log1p(hist.T))` with viridis colormap
- Universal semicircle overlay: `theta = np.linspace(0, pi, 200)`, plot `(0.5 + 0.5*cos(theta), 0.5*sin(theta))` as a line
- `EllipseROI` works in the same coordinate space — user drags in (G, S) space
- Renders in <50ms regardless of pixel count (it's a fixed-size image, not N scatter points)
- Optional: overlay selected cells as small scatter points on top of histogram (small N, fast)

**Phasor ROI -> spatial mask (vectorized):**
```
def phasor_roi_to_mask(g_map, s_map, center, radii):
    cx, cy = center
    rx, ry = radii
    return ((g_map - cx) / rx)**2 + ((s_map - cy) / ry)**2 <= 1.0
```
This is a single vectorized numpy expression — instantaneous for any image size.

**Per-cell phasor metrics:**
For each cell (using bbox optimization):
- `g_mean = g_map[cell_mask].mean()` — average phasor G within cell
- `s_mean = s_map[cell_mask].mean()` — average phasor S within cell
- `phasor_spread = np.sqrt(g_map[cell_mask].var() + s_map[cell_mask].var())` — phasor variance
- `phasor_roi_fraction = phasor_roi_mask[cell_mask].mean()` — fraction of cell pixels inside the ROI
Add as columns: `g_mean`, `s_mean`, `phasor_spread`, `phasor_roi_fraction`

**Phasor calibration (future):**
IRF correction shifts all phasor points. Applied as: `g_corrected = g * cos(phi) + s * sin(phi)`, `s_corrected = -g * sin(phi) + s * cos(phi)` where phi is the IRF phase shift determined from a reference standard (e.g., fluorescein). Defer calibration UI but keep the math function available.

**Zero-photon pixels:** Where `total == 0`, set both G and S to NaN (preferred over 0 — NaN is excluded from per-cell averages via nanmean). The prototype uses `total = np.where(total == 0, 1, total)` to avoid division by zero, then mark zero-count pixels as NaN afterward.

**Sign convention warning:** The FLIM community is inconsistent about S sign. Convention: S is positive in upper half-plane for a decay with positive phase lag. When computing via FFT: `S = -imag(FFT[harmonic])`. When computing via direct sine transform: S is naturally positive. **Validate against phasorpy or a reference standard** — if the reference appears in the lower half-plane, flip the sign.

**Intensity-weighted per-cell phasor means (recommended):** Weight by photon count per pixel. Pixels with more photons have lower noise. This is equivalent to computing the phasor of the summed decay across the cell. Use `scipy.ndimage.sum` with label arrays for vectorized computation.

**Threshold before phasor (important):** Pixels with very few photons produce noisy, scattered phasors. Threshold on total intensity (DC component) to remove noisy pixels before computing cell-level phasor statistics. Triangle method recommended as default for this.

**phasorpy library:** Consider installing `phasorpy` (by Christoph Gohlke, author of tifffile) for validation and reference. Don't use it as the primary computation engine — roll your own for HDF5/pyqtgraph/label-array integration. But validate your output against theirs to catch sign convention issues.

**Multi-harmonic (future):** First harmonic is sufficient for most work (FRET, metabolic imaging). Second harmonic helps resolve multi-component mixtures. When computing via direct transform, pre-compute basis vectors for each harmonic (tiny: N_bins floats each).

### Wavelet Filtering (from flimfret — carry forward)

**Source:** `/Users/leelab/flimfret/src/python/modules/wavelet_filter.py` (671 lines)

DTCWT-based adaptive denoising of phasor data. Operates on **post-computed G/S maps** (not raw decay). This is critical for noisy FLIM data — reduces phasor scatter while preserving spatial structure.

**Create:** `src/percell4/flim/wavelet_filter.py` — adapted from flimfret as plain functions:

```
def denoise_phasor(
    g: NDArray, s: NDArray, intensity: NDArray,
    filter_level: int = 9,
    omega: float | None = None,
) -> dict[str, NDArray]
    """Returns dict with 'G', 'S', 'T' (filtered) and 'GU', 'SU', 'TU' (unfiltered)."""
```

**Algorithm (6 steps):**
1. Rescale to Fourier coefficients: `Freal = G * intensity`, `Fimag = S * intensity`
2. Anscombe transform on Freal, Fimag, intensity (variance stabilization for Poisson noise): `2 * sqrt(max(x + 3/8, 0))`
3. Forward DTCWT (`dtcwt.Transform2d`, biort='near_sym_a', qshift='qshift_a') at `filter_level` decomposition levels
4. Adaptive thresholding: estimate noise via MAD (median absolute deviation / 0.6745), compute local noise variance in 3x3 windows, apply Wiener-like shrinkage with multi-scale coefficient interaction
5. Inverse DTCWT + reverse Anscombe (sixth-order rational approximation)
6. Recover filtered phasor: `G_filtered = Freal_filtered / intensity_filtered`

**Dependency:** `dtcwt>=0.14.0` — add to pyproject.toml as optional: `flim = ["dtcwt>=0.14.0"]`

**HDF5 storage:** Write filtered results to `/phasor/g_filtered`, `/phasor/s_filtered` alongside unfiltered `/phasor/g`, `/phasor/s`. Store filter parameters as attributes.

**Parameters from flimfret defaults:**
- `filter_level = 9` (decomposition depth)
- `biort = 'near_sym_a'`, `qshift = 'qshift_a'` (wavelet basis)
- Reference G/S from calibration standard

**Performance note:** The current flimfret implementation has nested loops in `compute_phi_prime()` — suitable for images up to ~2048x2048. For larger images, consider vectorizing the coefficient update or processing in tiles.

**Verify:** `pytest tests/test_flim/test_wavelet.py -v` — test with synthetic noisy phasor data, verify filtering reduces scatter while preserving mean G/S values. Compare output against flimfret reference output on same input.

**Verify:** `pytest tests/test_flim/test_phasor.py -v` — test with synthetic exponential decay, verify G/S values lie on universal semicircle. Test zero-photon handling. Test chunked vs non-chunked produce identical results. Validate sign convention against phasorpy if installed.

---

### Phase 9: Additional Analysis + Particle Analysis

**Create:**
- `src/percell4/measure/particle.py` — `analyze_particles(image, labels, mask) -> pd.DataFrame`. Adapted from `/Users/leelab/percell3/src/percell3/measure/particle_analyzer.py`. This is just another measurement function — same pattern as Phase 5.

---

### Phase 10: CLI Batch Mode

**Create:**
- `src/percell4/cli/main.py` — Click CLI: `percell4 import`, `percell4 segment`, `percell4 measure`, `percell4 export`. Calls same processing functions as GUI. CLI passes `print` or `tqdm.update` as `progress_callback`.

**Note:** CLI must NOT import Qt/napari. Processing functions live in `io/`, `segment/`, `measure/`, `flim/` — all pure Python with no GUI deps. The `gui/` package is only imported by the GUI entry point.

---

### ~~Phase 11: Plugin System~~ — DEFERRED

**Rationale:** PerCell3's formal plugin system added complexity without value. The "plain functions" architecture already provides extensibility — just write a function and call it. Plugin auto-discovery and a manager window are framework code for a problem that may not exist yet. Add only when there are actual plugins to discover.

---

## Phase Dependency Graph

```
Phase 0 (scaffolding)
  │
Phase 1 (launcher + multi-window + model)
  │         \
Phase 2     │  (HDF5 store — no GUI dependency)
  │         │
Phase 3 (import — depends on store)
  │         │
Phase 4 (segment — pure functions, no store dependency)
  │         │
Phase 5 (measure — pure functions, no store dependency)
  │         │
Phase 6 (data panels — depends on model + GUI framework)
  │
Phase 7 (thresholding — extends measure)
  │
Phase 8 (phasor — depends on GUI framework)
  │
Phase 9 (additional analysis)
  │
Phase 10 (CLI — depends on all processing modules)
  │
Phase 11 (plugins)
```

Phases 2-5 core logic (scanner, assembler, segmentation functions, measurement functions) can be parallelized since they are pure functions with no interdependency.

---

## Critical Learnings to Enforce (from PerCell3 docs/solutions/ + agent reviews)

| Rule | Enforcement | Source |
|------|-------------|--------|
| All write functions return counts | Code review, test assertions | `layer-based-architecture-redesign-learnings.md` |
| No bare `except Exception:` | ruff rule, grep in CI | `viewer-module-code-review-findings.md` |
| No `store._private` access outside core | grep in CI: `store\._[a-z]` | `segment-module-private-api-encapsulation-fix.md` |
| Typed dataclasses at API boundaries | Code review, never pass raw dicts | `layer-based-architecture-redesign-learnings.md` |
| Test with real microscope filenames | Include real naming patterns in test fixtures | `import-flow-table-first-ui-and-heuristics.md` |
| `getattr()` fallback for Cellpose model class | Smoke test that instantiates adapter | `cellpose-4-0-api-breaking-change.md` |
| Streaming Z-projection (in-place accumulation) | Never `np.stack()` all slices | `io-module-p1-z-projection-fixes.md` |
| `dtype=np.int64` for integer sums | Prevents overflow on uint16 accumulation | `io-module-p1-z-projection-fixes.md` |
| Always use `is None`, never truthiness for optional params | Catches 0 and "" as valid values | `image-calculator-plugin-architecture.md` |
| Raise ValueError for unknown enum-like params | No bare `else:` as catch-all | `io-module-p1-z-projection-fixes.md` |
| Regex validation with length limits in TokenConfig | `__post_init__()` with `re.compile()` | `tile-scan-stitching-learnings.md` |
| Defer user questions until they have context to answer | Don't ask channel config at startup | `defer-questions-until-context.md` |
| Test multi-item (N>=2) behavior, not just single items | Last-item corruption is invisible with N=1 | `combined-mask-overwrites-last-group.md` |
| Store `dims` attribute on every HDF5 array | Prevents axis-order misinterpretation | Performance oracle recommendation |
| Atomic writes for project.csv | `os.replace()` from temp file | Data integrity guardian recommendation |

---

## PerCell3 Files to Carry Forward

| PerCell4 Module | Adapt From (PerCell3 path) |
|-----------------|---------------------------|
| `io/scanner.py` | `src/percell3/io/scanner.py` |
| `io/assembler.py` | `src/percell3/io/engine.py` (stitching logic) |
| `segment/postprocess.py` | `src/percell3/segment/_engine.py`, `label_processor.py` |
| `segment/roi_import.py` | `src/percell3/segment/` (ROI + _seg.npy) |
| `measure/metrics.py` | `src/percell3/measure/metrics.py` |
| `measure/measurer.py` | `src/percell3/measure/measurer.py` |
| `measure/thresholding.py` | `src/percell3/measure/thresholding.py` |
| `measure/particle.py` | `src/percell3/measure/particle_analyzer.py` |
| `io/readers.py` (read_flim_bin) | `bin_reader/flim_bin_reader.py`, `bin_reader/bin_to_tif.py` |
| `flim/wavelet_filter.py` | `flimfret/src/python/modules/wavelet_filter.py` |

---

## Acceptance Criteria

### Functional Requirements
- [ ] App launches with hub window, opens/closes viewer, data plot, phasor plot independently
- [ ] Import TIFF directory with ch_/t_/s_/z_ tokens -> .h5 file with tiles + stitched + intensity
- [ ] Run Cellpose segmentation in background thread without freezing UI
- [ ] Import ImageJ ROI .zip as label array
- [ ] Per-cell measurements populate DataFrame and display in scatter plot + cell table
- [ ] Click cell in napari -> highlights in scatter + table (and reverse)
- [ ] Apply Otsu threshold -> binary mask -> masked measurements per cell
- [ ] Compute phasor G/S from TCSPC data -> phasor plot with ROI -> spatial mask
- [ ] CLI batch import/segment/measure across multiple datasets
- [ ] Drop .py plugin in plugins/ folder -> available in GUI

### Non-Functional Requirements
- [ ] No bare `except Exception:` in codebase
- [ ] All write functions return counts
- [ ] Typed dataclasses at module boundaries
- [ ] Tests pass: `pytest -v -m "not slow and not gui"`

---

## Success Metrics

- App can load, segment, measure, and interactively explore a real microscopy dataset end-to-end
- Selection sync works bidirectionally across all open windows
- Batch CLI can process a folder of datasets unattended
- Architecture supports adding new analysis methods as plain functions without modifying framework code
