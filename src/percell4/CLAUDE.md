# src/percell4/

The `percell4` Python package.

## Top-level files

- `app.py` — GUI entry point. Creates the `QApplication`, applies the theme,
  instantiates a `Session` plus `CellDataModel`, and shows the
  `LauncherWindow` from `interfaces/gui/main_window.py`.
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

## State ownership

`Session` (in `application/session.py`) is the canonical owner of the five
selection fields: `active_channel`, `active_segmentation`, `active_mask`,
`filter_ids`, `selection`. Only Selectors and Creators (see root
`CLAUDE.md` → "GUI state ownership") write these. napari → session for
layer-list events is forbidden; session → napari is a one-way controlled
push driven by `ViewerWindow._on_state_changed`.

## Subpackages

- `interfaces/gui/` — hexagonal GUI tree. `main_window.py` hosts the
  `LauncherWindow` hub (sidebar + stacked task panels + status bar);
  `task_panels/` holds the per-category panels (`io_panel`, `data_panel`,
  `analysis_panel`, `flim_panel`); `peer_views/` holds the standalone
  windows (`data_plot`, `cell_table`, `phasor_plot`); `app.py` is an
  alternative composition-root entry point used during the hex migration.
- `interfaces/cli/` — CLI surface for the hex architecture.
- `gui/` — remaining Qt + napari support: `viewer.py` (`ViewerWindow`
  wrapping `napari.Viewer`), dialogs (import, add-layer, compress, export),
  `multi_select.py`, `segmentation_panel.py`, `grouped_seg_panel.py`,
  `threshold_qc.py`, `theme.py`, `workers.py`, and `workflows/` (Qt driver
  for batch workflows).
- `application/` — use cases and `Session`. The session emits its own
  signals; `CellDataModel` bridges them onto the legacy `state_changed`
  surface. `phasor_render.py` is a Qt-free headless phasor-plot PNG
  renderer (matplotlib `Figure` + `FigureCanvasAgg`, no `pyplot`/no
  global backend mutation) used by the `batch_export_phasor` use case.
- `domain/` — Qt-free domain types and I/O ports.
- `adapters/` — driven adapters (HDF5 store, napari viewer adapter).
- `ports/` — port protocols consumed by use cases.
- `io/` — TIFF discovery, scanning, assembly, and import → HDF5.
- `measure/` — per-cell metrics, multi-ROI measurement, grouping, particles.
- `segment/` — Cellpose wrapper, postprocessing filters, ROI import.
- `flim/` — phasor computation and DTCWT wavelet filtering.
- `workflows/` — Qt-agnostic building blocks for batch analysis workflows
  (config dataclasses, run-folder I/O, channel intersection, host protocol,
  run-log helper). The Qt driver lives under `gui/workflows/`.
- `cli/` — command-line entry points.
- `plugins/` — plugin scaffolding (currently empty).
