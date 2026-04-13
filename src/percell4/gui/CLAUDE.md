# src/percell4/gui/

Qt GUI for PerCell4. All windows share a single `CellDataModel` and react to
its `state_changed` signal.

## Windows

- `launcher.py` — `LauncherWindow`. The hub: sidebar of categories, stacked
  content area, status bar. Creates and manages every other window. Owns
  dataset loading, measurement runs, filter controls, export actions, and
  plugin launching. Large (~2.5k lines) — a deliberate god object for now.
  Structurally conforms to `percell4.workflows.host.WorkflowHost` — see the
  "Batch workflow host API" section near the bottom of the file for the
  public surface batch runners rely on (`set_workflow_locked`,
  `close_child_windows`, `restore_child_windows`, etc.).
- `viewer.py` — `ViewerWindow` wraps a `napari.Viewer`. Renders the
  selection + filter highlighting via `DirectLabelColormap` (single code
  path handling all four combinations of filter/selection state).
- `data_plot.py` — `DataPlotWindow`. pyqtgraph scatter plot with X/Y axis
  dropdowns, two-layer rendering (background + highlights), click/shift-drag
  selection.
- `cell_table.py` — `CellTableWindow`. `QTableView` backed by
  `PandasTableModel` + `FilterableProxyModel` for row-level filtering.
- `phasor_plot.py` — `PhasorPlotWindow`. 2D phasor histogram (pyqtgraph
  ImageItem) with draggable ellipse ROIs, debounced mask preview pushed
  into the napari viewer, and "Apply as Mask" commit action.

## Dialogs and panels

- `add_layer_dialog.py` — add existing layers from the HDF5 file to the
  current dataset (flat or per-dataset discovery).
- `import_dialog.py` — TIFF → HDF5 import wizard with token config.
- `compress_dialog.py` — batch TIFF dataset compression (multi-dataset
  discovery, progress).
- `export_images_dialog.py` — export TIFF layers from the current dataset.
- `segmentation_panel.py` — Cellpose run controls, inline in launcher.
- `grouped_seg_panel.py` + `threshold_qc.py` — grouped-segmentation flow:
  cluster cells by a metric, interactively QC thresholds per group.

## Infrastructure

- `theme.py` — centralized dark-theme constants (`BACKGROUND`, `TEXT`,
  `ACCENT`, etc.) and the global Fusion-style stylesheet. Every GUI file
  imports constants from here; no hardcoded hex colors elsewhere.
- `workers.py` — `QThread` workers for Cellpose and other long-running ops.

## Subpackages

- `workflows/` — Qt driver for batch workflows. `BaseWorkflowRunner`
  generator-driven state machine and the `PhaseRequest` / `PhaseResult` /
  `WorkflowEvent` dataclasses. The pure-Python core lives under
  `src/percell4/workflows/`; this subpackage is the Qt-dependent half.
