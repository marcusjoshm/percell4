# src/percell4/

The `percell4` Python package.

## Top-level files

- `app.py` — GUI entry point. Creates the `QApplication`, applies the theme,
  instantiates one `CellDataModel`, and shows a `LauncherWindow`.
- `model.py` — `CellDataModel`, the shared state hub. Holds the measurements
  DataFrame, selection, filter, active segmentation name, and active mask
  name. Emits one signal, `state_changed`, carrying a `StateChange`
  descriptor. Every window connects to this signal; windows never talk to
  each other directly.
- `store.py` — `DatasetStore`, the HDF5 read/write interface for a single
  `.h5` dataset file. Writes are per-operation (crash-safe); reads can use
  a session context for efficiency. Chooses chunk shape + compression
  (`gzip` for images, `lzf` for TCSPC decay stacks).
- `project.py` — `ProjectIndex`, a thin atomic-write wrapper around a flat
  `project.csv` file that indexes all `.h5` datasets in a project.

## Subpackages

- `gui/` — Qt + napari + pyqtgraph windows and dialogs
- `io/` — TIFF discovery, scanning, assembly, and import → HDF5
- `measure/` — per-cell metrics, multi-ROI measurement, grouping, particles
- `segment/` — Cellpose wrapper, postprocessing filters, ROI import
- `flim/` — phasor computation and DTCWT wavelet filtering
- `cli/` — command-line entry points
- `plugins/` — plugin scaffolding (currently empty)
