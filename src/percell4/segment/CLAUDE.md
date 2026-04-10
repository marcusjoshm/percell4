# src/percell4/segment/

Cell segmentation — Cellpose wrapper, postprocessing filters, and import
adapters for external segmentations.

## Modules

- `cellpose.py` — `run_cellpose()`. Lazy-imports `cellpose` to keep the
  startup cost off the main import path. Handles both Cellpose 3.x (the
  `Cellpose` class with `model_type='cyto3'`) and 4.x (`CellposeModel`
  with `cpsam` as the default). Pure function: image in, `int32` label
  array out.
- `postprocess.py` — label-array filters that never mutate their input:
  `filter_edge_cells()` removes cells touching the border, size filters
  drop cells outside a min/max area, and a relabel step compacts the IDs
  to `1..N`. Each returns `(new_labels, removed_count)`.
- `roi_import.py` — convert external segmentations to `int32` label
  arrays:
  - `import_imagej_rois()` — ImageJ ROI `.zip` files (via `roifile`)
  - `import_cellpose_seg()` — Cellpose `_seg.npy` files
