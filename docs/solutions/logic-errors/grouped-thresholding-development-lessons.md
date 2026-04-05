---
title: "Grouped Thresholding: Development Lessons from Interactive QC Workflow"
category: logic-errors
tags:
  - pyqtgraph
  - napari
  - scipy
  - numpy
  - qt-widgets
  - shapes-layer
  - metrics
  - dock-widgets
module: measure + gui (metrics.py, grouper.py, grouped_seg_panel.py, threshold_qc.py)
symptom: "Multiple issues during grouped thresholding development: pyqtgraph histogram crash with stepMode, napari ROI events firing mid-draw, metric functions breaking on float images, size-dependent ratio metric, dock widget UX problems, naming confusion with cell segmentation"
root_cause: "pyqtgraph stepMode array length contract, napari shapes layer event timing, np.bincount integer-only constraint, sum vs mean for size-invariant ratios, dock widget lifecycle limitations, domain terminology ambiguity"
---

# Grouped Thresholding: Development Lessons

Seven issues encountered and solved during development of the grouped thresholding feature (expression-level cell grouping + per-group interactive thresholding).

## 1. pyqtgraph stepMode Histogram Crash

**Symptom:** `Exception: len(X) must be len(Y)+1 since stepMode=True` when plotting histograms.

**Root cause:** `stepMode="center"` requires X with length N+1 (bin edges) and Y with length N (counts). `np.histogram` returns `(counts, edges)` where `edges` already has length N+1, but it's common to mistakenly pass `edges[:-1]` (the bin left-edges) making X the same length as Y.

**Fix:** Pass the full `edges` array, or avoid stepMode entirely by using `BarGraphItem`:

```python
counts, edges = np.histogram(data, bins=50)

# WRONG: edges[:-1] has same length as counts
plot.plot(edges[:-1], counts, stepMode="center")

# RIGHT: full edges array has len(counts) + 1
plot.plot(edges, counts, stepMode="center")

# ALSO RIGHT: BarGraphItem avoids the issue entirely
bar_centers = (edges[:-1] + edges[1:]) / 2
bar = pg.BarGraphItem(x=bar_centers, height=counts, width=bar_width)
```

**Prevention:** `np.histogram` already returns the exact pair stepMode wants. The bug is introduced by computing midpoints. Pass raw outputs directly.

---

## 2. napari Shapes Layer ROI Events Fire Mid-Draw

**Symptom:** Threshold preview recomputed on every mouse movement while dragging a rectangle ROI, causing lag and flickering.

**Root cause:** `layer.events.data` and `layer.events.set_data` both fire continuously during mouse drag in napari's `add_rectangle` mode. There is no built-in "shape completed" event.

**Fix:** Track shape count and gate on the layer's mode. Only process when the count changes (shape completed) or when not in an add mode (editing existing shapes):

```python
def _on_roi_data_changed(self, event=None):
    current_count = len(self._roi_layer.data)
    mode = str(getattr(self._roi_layer, "mode", ""))
    is_adding = "add" in mode
    count_changed = current_count != self._last_roi_count
    if is_adding and not count_changed:
        return  # mid-draw, ignore
    self._last_roi_count = current_count
    QTimer.singleShot(50, self._update_preview)  # debounce
```

**Prevention:** Never react to shapes layer `data` events directly for expensive operations. Gate on shape count changes or use an explicit "Apply" button.

---

## 3. sg_ratio Size Dependency (Sums vs Means)

**Symptom:** `sg_ratio` correlated strongly with cell area -- large cells produced smaller ratios regardless of actual contrast.

**Root cause:** Using `sum(signal_pixels) / sum(ground_pixels)`. Sums scale with pixel count, so the metric is cell-size-dependent.

**Fix:** Use means instead of sums:

```python
# WRONG: size-dependent
signal = float(pixels[pixels >= p95].sum())
ground = float(pixels[pixels <= p50].sum())
return signal / ground

# RIGHT: size-invariant
signal = pixels[pixels >= p95]
ground = pixels[pixels <= p50]
return float(signal.mean() / ground.mean())
```

**Prevention:** Any ratio-based metric comparing pixel populations must use means (or medians), never sums, to be cell-size-invariant.

---

## 4. mode_intensity Breaks on Float Images

**Symptom:** `ValueError` or silent wrong results when `mode_intensity` is called on float-valued images (post-smoothing, normalized data).

**Root cause:** `np.bincount(pixels.astype(int))` requires non-negative integers. Float images either crash or truncate values, and negative values raise `ValueError`.

**Fix:** Use `scipy.stats.mode` which handles any numeric type:

```python
# WRONG: integer-only
counts = np.bincount(pixels.astype(int).ravel())
return float(np.argmax(counts))

# RIGHT: handles float and negative values
from scipy.stats import mode
result = mode(pixels, keepdims=False)
return float(result.mode)
```

**Prevention:** `np.bincount` is only valid for non-negative integer arrays. For general-purpose mode computation, use `scipy.stats.mode`.

---

## 5. Dock Widgets vs Separate Windows for QC Workflows

**Symptom:** napari dock widgets consumed significant viewer canvas space, making it difficult to inspect images alongside the QC controls and histogram.

**Root cause:** napari dock widgets share the viewer window's layout. Each dock widget reduces the available image viewport, and they cannot be freely repositioned on multi-monitor setups.

**Fix:** Use standalone `QMainWindow` instances:

```python
from qtpy.QtWidgets import QMainWindow

win = QMainWindow()
win.setWindowTitle("Threshold QC")
win.setCentralWidget(widget)
win.show()
self._qc_window = win  # track for cleanup
```

**Prevention:** For multi-step QC workflows with controls and plots, use separate `QMainWindow` instances. Reserve napari dock widgets for simple view-specific controls (layer toggles, display sliders) tightly coupled to the viewer.

---

## 6. Layer Visibility During QC

**Symptom:** Existing segmentation overlays, channel images, and other layers cluttered the viewer during per-group thresholding, making it hard to evaluate the threshold preview.

**Fix:** Hide all non-QC layers on entry, restore on cleanup:

```python
# Hide on entry:
qc_layer_names = {"_group_image", "_group_threshold_preview", "_group_roi"}
for layer in viewer.layers:
    if layer.name not in qc_layer_names:
        self._hidden_layers[layer.name] = layer.visible
        layer.visible = False

# Restore on cleanup:
for layer in viewer.layers:
    if layer.name in self._hidden_layers:
        layer.visible = self._hidden_layers[layer.name]
self._hidden_layers = {}
```

**Prevention:** Any QC workflow that takes over the viewer should save/restore layer visibility using a dict mapping layer name to original visibility state.

---

## 7. Naming: "Grouped Segmentation" vs "Grouped Thresholding"

**Symptom:** Users and developers confused "grouped segmentation" with Cellpose cell segmentation. The feature creates binary intensity masks, not instance segmentation.

**Fix:** Renamed to "grouped thresholding" throughout. The existing single-image thresholding was renamed to "Whole Field Thresholding" and both live under the Analysis tab. Docstrings explicitly disclaim the confusion:

```python
"""Interactive threshold QC controller for grouped thresholding.

NOT cell segmentation — this creates binary masks via intensity
thresholding, with cells grouped by expression level for polyclonal data.
"""
```

**Prevention:** Never reuse a domain term for a different operation. In microscopy: "segmentation" = instance labeling (unique ID per cell), "thresholding" = binary classification by intensity cutoff. If a name could be confused with an existing concept, pick a different name.

---

## Related Documentation

- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` -- mask/segmentation classification confusion
- `docs/solutions/ui-bugs/percell4-phasor-plot-axis-desync.md` -- pyqtgraph SI prefix and auto-range issues
- `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md` -- NumPy 2.x silent failures with Python sets
- `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md` -- napari event timing and Qt widget lifecycle
- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` -- signal coalescing and ROI patterns
- `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md` -- thread safety and mutable state
