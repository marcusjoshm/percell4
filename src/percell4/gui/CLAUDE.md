# src/percell4/gui/

Qt + napari support for PerCell4. The launcher and standalone peer-view
windows live under `interfaces/gui/`; this subpackage holds the napari
viewer adapter, dialogs, segmentation panels, and the Qt driver for batch
workflows. All components share a single `CellDataModel` and react to its
`state_changed` signal.

## Viewer

- `viewer.py` — `ViewerWindow` wraps `napari.Viewer`. Renders the
  selection + filter highlighting via `DirectLabelColormap` (single code
  path handling all four combinations of filter/selection state). Hosts
  the session → napari one-way push (`_on_state_changed` extends to
  `change.mask` / `change.segmentation` via `_find_layer_by_name_and_type`,
  guarded by `_is_originator`). Owns the `multi_select_requested` Qt signal
  and the process-wide `Labels.bind_key("M")` registration plus per-Labels-
  layer-instance binding (because napari's `action_manager` re-binds
  `napari:new_label` at every layer add).

## Dialogs

- `add_layer_dialog.py` — add existing layers from the HDF5 file to the
  current dataset (flat or per-dataset discovery; TIFF / batch / TCSPC /
  ROI / cellpose tabs).
- `import_dialog.py` — TIFF → HDF5 import wizard with token config.
- `compress_dialog.py` — batch TIFF dataset compression (multi-dataset
  discovery, progress).
- `export_images_dialog.py` — export TIFF layers from the current dataset.

## Inline panels

- `segmentation_panel.py` — Cellpose run controls and manual-edit UX.
  Creator path: `Create Empty Labels` writes `"manual"` segmentation and
  auto-selects.
- `grouped_seg_panel.py` + `threshold_qc.py` — grouped-segmentation flow:
  cluster cells by a metric, interactively QC thresholds per group.
  Threshold-QC accept is a Creator: writes the grouped-thresh mask and
  auto-selects.
- `multi_select.py` — modal multi-label selection tool
  (`MultiLabelSelectController`, `StagingBuffer`, dock-window-scoped
  Ctrl+Return / Ctrl+Enter / Esc). The keystroke that *opens* the tool
  is `M`, bound on `Labels` keymaps from `viewer.py`.

## Infrastructure

- `theme.py` — centralized dark-theme constants (`BACKGROUND`, `TEXT`,
  `ACCENT`, etc.) and the global Fusion-style stylesheet. Every GUI file
  imports constants from here; no hardcoded hex colors elsewhere.
- `workers.py` — `QThread` workers for Cellpose and other long-running ops.
- `tcspc_tab_state.py` — TCSPC import tab state.
- `_dialog_utils.py`, `torch_error.py` — small shared utilities.

## Subpackages

- `workflows/` — Qt driver for batch workflows. `BaseWorkflowRunner`
  generator-driven state machine and the `PhaseRequest` / `PhaseResult` /
  `WorkflowEvent` dataclasses. The pure-Python core lives under
  `src/percell4/workflows/`; this subpackage is the Qt-dependent half.
