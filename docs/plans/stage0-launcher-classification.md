# Launcher Method Classification

Classification of all `LauncherWindow` methods by target hexagonal layer.
Generated during Stage 0 of the hex architecture refactor.

## Summary

| Layer | Count | Description |
|-------|-------|-------------|
| **interfaces** | 44 | Qt chrome, panels, dialogs, UI sync, window management |
| **application** | 14 | Orchestration that becomes use cases |
| **adapters** | 5 | Napari/store bridge code |
| **domain** | 2 | Pure computation (filter, merge) |

## Full Classification

### interfaces/gui/ — Qt chrome, panels, window management (44 methods)

These stay in `interfaces/gui/` as MainWindow or task panel methods.

| Method | Lines | Notes |
|--------|-------|-------|
| `__init__` | 45–101 | Window setup, styling, menu/sidebar creation |
| `_create_menu_bar` | 105–118 | File menu with Open Project, Quit |
| `_create_central_widget` | 122–192 | Sidebar + stacked panels |
| `_on_sidebar_click` | 194–198 | Panel switching |
| `_create_io_panel` | 202–251 | I/O panel layout |
| `_create_viewer_panel` | 253–267 | Viewer panel layout |
| `_create_segment_panel` | 269–275 | Delegates to SegmentationPanel |
| `_create_analysis_panel` | 277–461 | Analysis panel layout (largest panel) |
| `_create_flim_panel` | 463–524 | FLIM panel layout |
| `_create_scripts_panel` | 526–540 | Scripts panel layout |
| `_create_workflows_panel` | 542–565 | Workflows panel layout |
| `_create_data_panel` | 742–847 | Data panel layout (dropdowns, info) |
| `_wrap_in_scroll` | 851–866 | Static helper: scroll wrapper |
| `_section_label` | 868–877 | Static helper: styled label |
| `_placeholder` | 879–885 | Static helper: "coming soon" label |
| `_get_or_create_window` | 889–910 | Window factory registry |
| `_show_window` | 942–962 | Show/raise window, auto-populate viewer |
| `_on_open_project` | 966–970 | File dialog for project folder |
| `_on_import_dataset` | 972–995 | Opens CompressDialog, extracts config |
| `_on_load_dataset` | 1084–1089 | File dialog → delegates to `_load_h5_into_viewer` |
| `_on_add_layer_to_dataset` | 1091–1103 | Opens AddLayerDialog |
| `_update_data_tab_from_store` | 1197–1238 | Refreshes Data tab info + dropdowns |
| `_update_active_channel_label` | 1256–1273 | Updates channel labels across panels |
| `_on_state_changed` | 1377–1386 | Unified model event dispatcher |
| `_on_clear_selection` | 1431–1433 | Delegates to `model.set_selection([])` |
| `_on_filter_to_selection` | 1435–1441 | Delegates to `model.set_filter` |
| `_on_clear_filter` | 1443–1445 | Delegates to `model.set_filter(None)` |
| `_on_filter_state_changed` | 1447–1460 | Updates filter status UI |
| `_update_thresh_stats` | 1622–1634 | Updates threshold result label |
| `_show_metric_config_dialog` | 1684–1714 | Metric selection dialog |
| `_load_selected_metrics` | 1716–1726 | QSettings read |
| `_save_selected_metrics` | 1728–1732 | QSettings write |
| `_on_export_particle_csv` | 1975–1989 | File dialog → CSV write |
| `_on_run_script` | 2307–2312 | Placeholder |
| `_refresh_management_combos` | 2316–2342 | Refreshes layer management dropdowns |
| `_on_delete_channel` | 2444–2467 | Removes layer from viewer only |
| `_on_active_seg_combo_changed` | 2471–2473 | Delegates to model |
| `_on_active_mask_combo_changed` | 2475–2479 | Delegates to model |
| `_on_model_active_seg_changed` | 2481–2490 | Syncs combo from model (with signal blocking) |
| `_on_model_active_mask_changed` | 2492–2501 | Syncs combo from model (with signal blocking) |
| `_refresh_dataset_info` | 2503–2524 | Refreshes info label from store |
| `_refresh_active_combos` | 2526–2556 | Refreshes active layer dropdowns (signal blocking) |
| `_on_export_csv` | 2558–2567 | File dialog → CSV write |
| `_on_export_images` | 2569–2632 | Opens ExportImagesDialog → writes TIFFs |

### interfaces/gui/ — Workflow host API (9 methods)

Implements `WorkflowHost` protocol. Stays in `interfaces/gui/`.

| Method | Lines | Notes |
|--------|-------|-------|
| `_on_open_single_cell_workflow` | 567–674 | Config dialog → runner setup (mixed app+UI) |
| `_on_workflow_event` | 676–740 | Status bar + summary dialog |
| `is_workflow_locked` (property) | 2641–2643 | Read-only flag |
| `set_workflow_locked` | 2646–2667 | Enable/disable main UI |
| `show_workflow_status` | 2669–2674 | Statusbar update |
| `get_viewer_window` | 2676–2685 | Accessor for runner |
| `get_data_model` | 2687–2688 | Accessor for runner |
| `close_child_windows` | 2690–2724 | Close peer views during run |
| `restore_child_windows` | 2726–2745 | Reopen peer views after run |

### interfaces/gui/ — Lifecycle (3 methods)

| Method | Lines | Notes |
|--------|-------|-------|
| `closeEvent` | 2749–2782 | Cancel workflow + close windows + quit |
| `_save_geometry` | 2784–2787 | QSettings persist |
| `_restore_geometry` | 2789–2792 | QSettings restore |

### application/ — Use cases (14 methods)

These become use case classes in `application/use_cases/`.

| Method | Target Use Case | Lines | Notes |
|--------|----------------|-------|-------|
| `_load_h5_into_viewer` | `LoadDataset` | 1105–1133 | Open store → clear model → populate viewer |
| `_on_close_dataset` | `CloseDataset` | 1240–1254 | Clear viewer + model + store refs |
| `_run_batch_compress` | `BatchCompress` | 997–1082 | Progress dialog + import loop |
| `_on_threshold_preview` | `PreviewThreshold` | 1462–1566 | Compute threshold → preview in viewer |
| `_on_threshold_roi_changed` | (part of PreviewThreshold) | 1568–1620 | Recalculate from ROI region |
| `_on_threshold_accept` | `AcceptThreshold` | 1636–1682 | Save mask to store + viewer |
| `_on_phasor_mask_applied` | `ApplyPhasorMask` | 1407–1429 | Store mask → add to viewer → update model |
| `_on_measure_cells` | `MeasureCells` | 1765–1871 | Collect layers → measure → store → model |
| `_on_analyze_particles` | `AnalyzeParticles` | 1873–1973 | Collect layers → analyze → merge → store |
| `_on_compute_phasor` | `ComputePhasor` | 1991–2106 | Read decay → compute → calibrate → filter → store |
| `_on_apply_wavelet` | `ApplyWavelet` | 2108–2234 | Read phasor → denoise → store → update plot |
| `_on_compute_lifetime` | `ComputeLifetime` | 2236–2305 | Read phasor → lifetime → store → viewer |
| `_on_rename_layer` | `RenameLayer` | 2344–2377 | Rename in store + viewer + refresh |
| `_on_delete_layer` | `DeleteLayer` | 2379–2409 | Delete from store + viewer + refresh |
| `_on_rename_channel` | `RenameChannel` | 2411–2442 | Update metadata + viewer layer name |

### adapters/ — Napari/store bridge (5 methods)

These move into `adapters/napari_viewer.py`.

| Method | Lines | Notes |
|--------|-------|-------|
| `_wire_viewer_layer_selection` | 912–940 | Connect napari active-layer events |
| `_populate_viewer_from_store` | 1135–1195 | Read store → add layers to napari |
| `_sync_active_layers_from_viewer` | 1275–1318 | Napari active layer → model sync (metadata + store fallback) |
| `_on_phasor_preview` | 1390–1405 | Forward phasor mask to napari preview layer |
| `_get_active_seg_labels` | 1337–1366 | Read labels array from napari layer |

### domain/ — Pure computation (2 methods)

These move into `domain/`.

| Method | Target Location | Lines | Notes |
|--------|----------------|-------|-------|
| `_apply_cell_filter` | `domain/filtering.py` | 1320–1335 | Zero out non-filtered cells in labels array |
| `_merge_group_columns` | `domain/measurements.py` | 1734–1763 | Merge group columns into measurements DataFrame |
