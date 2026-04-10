# src/percell4/measure/

Per-cell measurement, grouping, and particle analysis. All functions are
pure: arrays/DataFrames in, DataFrames out. No HDF5 or GUI coupling.

## Modules

- `metrics.py` — NaN-safe per-cell metric functions (mean, max, min,
  median, std, mode, integrated intensity, ...). Each takes
  `(image_crop, cell_mask)` and returns a single `float`. `BUILTIN_METRICS`
  is the dispatch dict used by `measurer`.
- `measurer.py` — `measure_multichannel()` and
  `measure_multichannel_multi_roi()`. BBox-optimized: uses
  `scipy.ndimage.find_objects` for O(1) bounding-box lookup per cell, then
  `regionprops` for morphology. Single-pass over all channels and ROIs.
  Returns a DataFrame keyed by cell `label` with `CORE_COLUMNS` + metric
  columns. Multi-ROI output adds `<metric>_<roi_name>` columns.
- `grouper.py` — `group_by_metric()`. Clusters cells by a metric value
  using GMM or K-means; returns a `GroupingResult` with 1-indexed group
  assignments ordered by ascending group mean.
- `thresholding.py` — per-cell thresholding helpers used by the grouped
  segmentation QC flow.
- `particle.py` — per-cell particle/puncta analysis using connected
  components inside each cell boundary. `analyze_particles()` returns one
  row per cell (counts + aggregate intensity); `analyze_particles_detail()`
  returns one row per particle.
