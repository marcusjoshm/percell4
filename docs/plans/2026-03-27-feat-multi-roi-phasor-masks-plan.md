---
title: "feat: Multi-ROI Phasor Masks"
type: feat
date: 2026-03-27
---

# Multi-ROI Phasor Masks

## Enhancement Summary

**Deepened on:** 2026-03-27
**Sections enhanced:** All 5 phases + architecture
**Research agents used:** Python reviewer, Performance oracle, Code simplicity reviewer

### Key Improvements from Deepening
1. **Cache per-ROI boolean masks** — only recompute the dragged ROI during live preview, reducing 10-ROI drag cost from ~400ms to ~60ms at 4096x4096
2. **Single-pass `measure_cells_multi_roi()`** — call `find_objects()` + `regionprops()` once instead of N+2 times, meeting the "2x single-mask time" target
3. **Remove `outside_all` measurement scope** — YAGNI; derivable from whole-cell minus per-ROI
4. **Replace ROI library with simple file dialogs** — `QFileDialog` save/load instead of `~/.percell4/phasor_rois/` directory + dropdown menu
5. **Simplify JSON schema** — drop `description` field, derive `label` from array position on load
6. **Relax name validation** — unique + non-empty only; pandas handles any string as column name
7. **Use `np.bincount()` for status bar pixel counts** — one pass instead of N separate equality checks
8. **Initialize debounce timer in `__init__`** — not lazily via `hasattr`
9. **Add JSON validation on load** — `PhasorROI.from_dict()` classmethod with `ValueError` on bad data

### New Considerations Discovered
- `phasor_roi_to_mask()` creates 4-6 temporary float32 arrays per call — 384MB peak memory at 4096x4096; use in-place numpy ops to halve this
- `store.write_mask()` docstring says "binary (0/1)" but already supports uint8 0-255 — update docstring
- ROI delete should call `data_model.set_active_mask("")` to force re-application before measurement
- Only push new `DirectLabelColormap` to napari when ROI colors/visibility change, not on every drag (avoids GPU texture re-uploads)

---

## Overview

Expand the Phasor Plot from a single ROI ellipse to multiple named, colored ROI ellipses. Each ROI represents a distinct lifetime population. All visible ROIs combine into a single integer-labeled mask for measurement. ROI positions can be saved to and loaded from JSON files for reuse across datasets.

## Problem Statement / Motivation

FLIM phasor analysis often requires classifying pixels into multiple lifetime populations simultaneously (e.g., bound vs. free, donor vs. acceptor). The current single-ROI design forces serial workflows: draw ROI → apply mask → measure → draw different ROI → repeat. With multiple ROIs, the user defines all populations at once, producing a single labeled mask that the measurement pipeline processes in one pass.

## Brainstorm Reference

`docs/brainstorms/2026-03-27-multi-roi-phasor-masks-brainstorm.md`

## Research Findings

### Current Phasor ROI System (`phasor_plot.py`, 409 lines)
- Single `pg.RectROI` at `[0.25, 0.30]` size `[0.20, 0.15]` defines ellipse bounding box
- `pg.PlotCurveItem` draws the ellipse curve from parametric rotation math
- Angle controlled by `QSpinBox` (-90° to +90°)
- `_get_ellipse_params()` → center, radii, angle_rad
- `_on_roi_changed()` → live preview in napari (`_phasor_roi_preview` layer, cyan, 0.4 opacity)
- `_on_apply_mask()` → writes binary uint8 to `masks/phasor_roi` in HDF5, sets active mask
- Mask name hardcoded as `"phasor_roi"`

### Mask Computation (`flim/phasor.py`)
- `phasor_roi_to_mask(g_map, s_map, center, radii, angle_rad)` → `NDArray[np.bool_]`
- Standard rotated ellipse test: shift to center, inverse-rotate, `(dg/rx)² + (ds/ry)² <= 1.0`
- Creates 4-6 temporary float32 arrays of shape (H,W) — 384MB peak at 4096x4096

### HDF5 Mask Storage (`store.py`)
- `write_mask(name, array)` enforces uint8 dtype, stores at `/masks/<name>`
- Already supports 0-255 values at the code level — docstring says "binary" but code does `astype(np.uint8)`
- No changes needed to storage logic, just update docstring

### Current Measurement (`measurer.py`)
- `measure_cells(image, labels, metrics, mask)` — `mask` is `NDArray[np.uint8] | None`
- Binary scoping: `mask_bool = mask_crop > 0` → computes `{metric}_mask_inside` and `{metric}_mask_outside`
- Each call runs `regionprops(labels)` + `find_objects(labels)` — full-image passes, ~200-400ms at 4096x4096

### Key Gotchas (from docs/solutions/)
- Disable SI prefix on phasor axes in `_build_ui()` AND after every `_refresh_histogram()`
- Use `setRect(QRectF)` for ImageItem positioning, NOT `setTransform`
- `_is_alive()` check before accessing napari viewer

---

## Proposed Solution

### Architecture: ROI Data Model

```python
from dataclasses import dataclass, field
from typing import Final

COLOR_CYCLE: Final[tuple[str, ...]] = (
    "#3498db", "#e74c3c", "#2ecc71", "#f39c12",
    "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
)

@dataclass
class PhasorROI:
    """Single phasor ROI definition."""
    name: str               # user-editable, e.g. "bound", "free"
    center: tuple[float, float]  # (g, s) in data coordinates
    radii: tuple[float, float]   # (rx, ry) half-widths
    angle_deg: float         # rotation in degrees
    label: int               # integer value in mask (1, 2, 3, ...)
    color: str               # hex color, e.g. "#3498db"
    visible: bool = True     # session-only state, not serialized

    @classmethod
    def from_dict(cls, d: dict, label: int, default_color: str) -> "PhasorROI":
        """Create from JSON dict with validation. Raises ValueError on bad data."""
        try:
            center = tuple(float(x) for x in d["center"])
            radii = tuple(float(x) for x in d["radii"])
            if len(center) != 2 or len(radii) != 2:
                raise ValueError("center and radii must be 2-element sequences")
            return cls(
                name=str(d["name"]),
                center=center,
                radii=radii,
                angle_deg=float(d.get("angle_deg", 0)),
                label=label,
                color=str(d.get("color", default_color)),
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"Invalid ROI data: {e}") from e

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict. Excludes label (positional) and visible (session)."""
        return {
            "name": self.name,
            "center": list(self.center),
            "radii": list(self.radii),
            "angle_deg": self.angle_deg,
            "color": self.color,
        }

@dataclass
class _ROIWidget:
    """GUI objects for one phasor ROI."""
    roi: pg.RectROI
    curve: pg.PlotCurveItem
    phasor_roi: PhasorROI
    cached_mask: NDArray[np.bool_] | None = None  # cached for performance
```

### Key Design Decisions

1. **Single labeled mask** — all visible ROIs combine into one uint8 array. Stored at `/masks/phasor_roi` (same path, now multi-label). Old binary masks are backward-compatible (value 1 = one ROI).

2. **Hidden = excluded from mask** — visibility toggle controls mask participation. Button reads "Apply Visible as Mask."

3. **Overlap: later-in-list wins** — ROIs lower in the list have higher priority. List order determines overlap resolution. JSON preserves this order.

4. **Labels renumber on delete** — removing an ROI renumbers remaining labels contiguously and invalidates the active mask (calls `data_model.set_active_mask("")`), forcing re-application before measurement.

5. **ROI name validation** — unique and non-empty only. Strip whitespace. Default names: `ROI_1`, `ROI_2`, etc.

6. **No harmonic stored in JSON** — ROI positions are just G/S coordinates. User manages which harmonic they apply to.

7. **Single-pass measurement** — new `measure_cells_multi_roi()` function calls `find_objects()` and `regionprops()` once, then computes per-ROI metrics within each cell's bounding box crop.

8. **Per-ROI mask caching** — each `_ROIWidget` caches its boolean mask. Only the dragged ROI's mask is recomputed during live preview. Cache invalidated on G/S map change, harmonic switch, or filtered/unfiltered toggle.

---

## Implementation Phases

### Phase 1: Multi-ROI Data Model + UI

**Goal:** Support adding, removing, naming, and toggling multiple ROI ellipses on the phasor plot.

**Files to modify:**

#### `src/percell4/gui/phasor_plot.py`

**Replace single ROI with ROI list:**

Remove the single `_roi`, `_ellipse_curve`, and `_angle_spin` instance variables. Replace with:

```python
def __init__(self, data_model, launcher=None):
    ...
    self._roi_widgets: list[_ROIWidget] = []
    self._selected_roi_index: int | None = None
    self._preview_timer = QTimer()
    self._preview_timer.setSingleShot(True)
    self._preview_timer.setInterval(100)
    self._preview_timer.timeout.connect(self._update_preview)
    self._total_valid_pixels: int = 0  # cached from set_phasor_data
    self._colormap_dirty: bool = True
```

**ROI list panel** — `QVBoxLayout` with a row per ROI (no drag-to-reorder for v1):

```
┌─ ROIs ──────────────────────┐
│ [Add] [Remove]              │
│                             │
│ ■ bound        [vis ☑]      │
│ ■ free         [vis ☑]      │
│ ■ background   [vis ☐]      │
│                             │
│ Selected ROI:               │
│ Name: [bound_____]          │
│ Angle: [-15 °]              │
│                             │
│ [Apply Visible as Mask]     │
└─────────────────────────────┘
```

Click a row to select that ROI — its handles become active and the angle spinbox controls it.

**Add ROI:**

```python
def _on_add_roi(self) -> None:
    n = len(self._roi_widgets)
    if n >= 10:
        self.statusBar().showMessage("Maximum 10 ROIs", 3000)
        return
    color = COLOR_CYCLE[n % len(COLOR_CYCLE)]
    phasor_roi = PhasorROI(
        name=f"ROI_{n + 1}",
        center=(0.35 + n * 0.05, 0.35),
        radii=(0.10, 0.08),
        angle_deg=0,
        label=n + 1,
        color=color,
    )
    self._create_roi_widget(phasor_roi)
    self._colormap_dirty = True
    self._refresh_roi_list()
```

**Remove ROI — invalidate active mask:**

```python
def _on_remove_roi(self) -> None:
    if self._selected_roi_index is None:
        return
    widget = self._roi_widgets.pop(self._selected_roi_index)
    self._plot.removeItem(widget.roi)
    self._plot.removeItem(widget.curve)
    # Renumber remaining labels
    for i, w in enumerate(self._roi_widgets):
        w.phasor_roi.label = i + 1
        w.cached_mask = None  # invalidate caches (labels changed)
    self._selected_roi_index = None
    self._colormap_dirty = True
    # Invalidate active mask to prevent stale measurements
    self.data_model.set_active_mask("")
    self._refresh_roi_list()
    self._preview_timer.start()
```

**Each ROI's `sigRegionChangeFinished`** connects with its index:

```python
def _on_roi_moved(self, index: int) -> None:
    """Recompute only the changed ROI's mask, then debounced preview."""
    widget = self._roi_widgets[index]
    # Sync PhasorROI data from the RectROI position/size
    pos = widget.roi.pos()
    size = widget.roi.size()
    widget.phasor_roi.center = (pos.x() + abs(size.x()) / 2,
                                 pos.y() + abs(size.y()) / 2)
    widget.phasor_roi.radii = (abs(size.x()) / 2, abs(size.y()) / 2)
    # Redraw ellipse curve
    self._update_ellipse_curve(widget)
    # Invalidate only this ROI's cached mask
    widget.cached_mask = None
    self._preview_timer.start()
```

**Verify:** Add 3 ROIs. Each appears with a different color. Select one → its handles are highlighted, angle spinbox controls it. Remove middle ROI → remaining renumber to 1, 2. Toggle visibility → hidden ROI dims.

---

### Phase 2: Combined Mask Generation + Live Preview

**Goal:** Combine all visible ROIs into a single labeled mask. Cached per-ROI masks for performance.

**Files to modify:**

#### `src/percell4/gui/phasor_plot.py`

**Combined mask with per-ROI caching:**

```python
def _compute_combined_mask(self) -> NDArray[np.uint8]:
    """Combine all visible ROIs into a single labeled uint8 mask.

    Uses cached per-ROI boolean masks. Only uncached ROIs are recomputed.
    Later ROIs in the list overwrite earlier ones at overlapping pixels.
    """
    g, s = self._get_active_gs_maps()
    mask = np.zeros(g.shape, dtype=np.uint8)
    for widget in self._roi_widgets:
        if not widget.phasor_roi.visible:
            continue
        if widget.cached_mask is None:
            roi = widget.phasor_roi
            angle_rad = np.deg2rad(roi.angle_deg)
            widget.cached_mask = phasor_roi_to_mask(
                g, s, roi.center, roi.radii, angle_rad
            )
        mask[widget.cached_mask] = widget.phasor_roi.label
    return mask

def _get_active_gs_maps(self) -> tuple[NDArray, NDArray]:
    """Return filtered or unfiltered G/S maps based on checkbox."""
    if self._filtered_check.isChecked() and self._g_filtered is not None:
        return self._g_filtered, self._s_filtered
    return self._g_map, self._s_map
```

**Invalidate all caches when G/S maps change:**

```python
def set_phasor_data(self, g_map, s_map, intensity=None,
                    g_unfiltered=None, s_unfiltered=None,
                    labels=None) -> None:
    ...
    self._total_valid_pixels = int(np.isfinite(g_map).sum())
    # Invalidate all ROI mask caches
    for widget in self._roi_widgets:
        widget.cached_mask = None
```

**Live preview with bincount status bar:**

```python
def _update_preview(self) -> None:
    """Push combined multi-label preview to napari."""
    viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
    if viewer_win is None or not viewer_win._is_alive():
        return
    mask = self._compute_combined_mask()

    # Build colormap only when dirty (add/remove/color change)
    if self._colormap_dirty:
        color_dict = {0: "transparent"}
        for widget in self._roi_widgets:
            if widget.phasor_roi.visible:
                color_dict[widget.phasor_roi.label] = widget.phasor_roi.color
        self._preview_colormap = DirectLabelColormap(color_map=color_dict)
        self._colormap_dirty = False

    # Update or create preview layer
    try:
        layer = viewer_win._viewer.layers["_phasor_roi_preview"]
        layer.data = mask
    except KeyError:
        viewer_win._viewer.add_labels(
            mask, name="_phasor_roi_preview",
            colormap=self._preview_colormap, opacity=0.4,
        )
    else:
        if hasattr(self, '_preview_colormap'):
            layer.colormap = self._preview_colormap

    # Status bar: pixel counts per ROI via bincount (single pass)
    max_label = max((w.phasor_roi.label for w in self._roi_widgets
                     if w.phasor_roi.visible), default=0)
    if max_label > 0:
        counts = np.bincount(mask.ravel(), minlength=max_label + 1)
        total = self._total_valid_pixels or 1
        parts = []
        for widget in self._roi_widgets:
            if widget.phasor_roi.visible:
                lbl = widget.phasor_roi.label
                pct = counts[lbl] / total * 100
                parts.append(f"{widget.phasor_roi.name}: {counts[lbl]} ({pct:.1f}%)")
        self.statusBar().showMessage(" | ".join(parts))
```

**Verify:** Add 2 ROIs. Move one → napari preview shows multi-colored mask. Overlap shows later ROI's color. Toggle visibility → hidden ROI disappears from preview. Status bar shows per-ROI pixel counts.

---

### Phase 3: Apply Mask + HDF5 Storage

**Goal:** "Apply Visible as Mask" writes the combined mask to HDF5 and sets as active mask.

**Files to modify:**

#### `src/percell4/gui/phasor_plot.py`

```python
def _on_apply_mask(self) -> None:
    mask = self._compute_combined_mask()
    if mask.max() == 0:
        self.statusBar().showMessage("No visible ROIs to apply", 3000)
        return

    viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
    if viewer_win and viewer_win._is_alive():
        try:
            viewer_win._viewer.layers.remove("_phasor_roi_preview")
        except ValueError:
            pass
        # Add final mask with ROI colors
        color_dict = {0: "transparent"}
        for widget in self._roi_widgets:
            if widget.phasor_roi.visible:
                color_dict[widget.phasor_roi.label] = widget.phasor_roi.color
        viewer_win.add_mask(mask, name="phasor_roi", color_dict=color_dict)

    store = self._launcher._store if self._launcher else None
    if store:
        store.write_mask("phasor_roi", mask)

    self.data_model.set_active_mask("phasor_roi")
    self.statusBar().showMessage("Multi-ROI mask applied", 3000)
```

#### `src/percell4/gui/viewer.py`

**Extend `add_mask()` — `color_dict` takes precedence over kwargs colormap:**

```python
def add_mask(
    self,
    data: NDArray[np.uint8],
    name: str,
    color_dict: dict[int, str] | None = None,
    **kwargs: Any,
) -> None:
    if color_dict is None:
        color_dict = {0: "transparent", 1: "yellow"}
    colormap = DirectLabelColormap(color_map=color_dict)
    kwargs.pop("colormap", None)  # color_dict takes precedence
    # ... existing layer creation logic with colormap ...
```

#### `src/percell4/store.py`

**Update `write_mask` docstring** — no code change needed:

```python
def write_mask(self, name: str, array: NDArray) -> int:
    """Write a mask (binary or multi-label) to /masks/<name>.

    Enforces uint8 dtype. Values 0-255 supported:
    - Binary: 0=outside, 1=inside
    - Multi-label: 0=outside, 1..N=ROI labels
    """
```

**Verify:** Apply mask with 3 visible ROIs → napari shows multi-colored mask. HDF5 contains uint8 values 0, 1, 2, 3. Active mask set to "phasor_roi". Old single-ROI code paths still work.

---

### Phase 4: Save/Load ROI Files

**Goal:** Save ROI positions to JSON files. Load via file dialog.

**Files to modify:**

#### `src/percell4/gui/phasor_plot.py`

**Simplified JSON schema (no description, no label — derive from position):**

```json
{
  "rois": [
    {"name": "donor", "center": [0.4, 0.32], "radii": [0.10, 0.08], "angle_deg": 15, "color": "#3498db"},
    {"name": "acceptor", "center": [0.62, 0.41], "radii": [0.12, 0.06], "angle_deg": -5, "color": "#e74c3c"}
  ]
}
```

**Save/Load via file dialogs:**

```python
def _on_save_rois(self) -> None:
    if not self._roi_widgets:
        self.statusBar().showMessage("No ROIs to save", 3000)
        return
    path, _ = QFileDialog.getSaveFileName(
        self, "Save ROIs", "", "JSON Files (*.json)"
    )
    if not path:
        return
    data = {"rois": [w.phasor_roi.to_dict() for w in self._roi_widgets]}
    Path(path).write_text(json.dumps(data, indent=2))
    self.statusBar().showMessage(f"Saved {len(self._roi_widgets)} ROIs", 3000)

def _on_load_rois(self) -> None:
    path, _ = QFileDialog.getOpenFileName(
        self, "Load ROIs", "", "JSON Files (*.json)"
    )
    if not path:
        return
    try:
        data = json.loads(Path(path).read_text())
        rois_data = data["rois"]
        if not isinstance(rois_data, list):
            raise ValueError("'rois' must be a list")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        QMessageBox.warning(self, "Load Error", f"Invalid ROI file:\n{e}")
        return

    # Clear existing ROIs
    for widget in self._roi_widgets:
        self._plot.removeItem(widget.roi)
        self._plot.removeItem(widget.curve)
    self._roi_widgets.clear()

    # Create from JSON — labels derived from position
    for i, roi_data in enumerate(rois_data):
        try:
            phasor_roi = PhasorROI.from_dict(
                roi_data,
                label=i + 1,
                default_color=COLOR_CYCLE[i % len(COLOR_CYCLE)],
            )
        except ValueError as e:
            QMessageBox.warning(self, "Load Error", f"ROI {i}: {e}")
            continue
        self._create_roi_widget(phasor_roi)

    self._colormap_dirty = True
    self._refresh_roi_list()
    self._preview_timer.start()
    self.statusBar().showMessage(f"Loaded {len(self._roi_widgets)} ROIs", 3000)
```

**Toolbar buttons:**

```python
save_btn = QPushButton("Save ROIs...")
save_btn.clicked.connect(self._on_save_rois)

load_btn = QPushButton("Load ROIs...")
load_btn.clicked.connect(self._on_load_rois)
```

**Verify:** Add 3 ROIs, position them. Save → JSON created. Close phasor plot. Reopen, Load → ROIs restored at saved positions. Invalid JSON shows error dialog.

---

### Phase 5: Measurement Integration

**Goal:** Extend measurements to compute per-ROI metrics with a single-pass approach.

### Research Insight: Single-Pass vs N-Call Design

Calling `measure_cells()` N+2 times (base + N ROIs + outside_all) runs `regionprops()` + `find_objects()` N+2 times — ~200-400ms each at 4096x4096. For 10 ROIs, that's 2.4-4.8 seconds just in overhead. A single-pass approach calls these once and computes per-ROI metrics within each cell's bounding box crop.

**Files to modify:**

#### `src/percell4/measure/measurer.py`

**Add single-pass multi-ROI measurement function:**

```python
def measure_cells_multi_roi(
    image: NDArray,
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    roi_names: dict[int, str],
    metrics: list[str] | None = None,
) -> pd.DataFrame:
    """Measure per-cell metrics for each ROI label in a multi-label mask.

    Single-pass: calls find_objects() and regionprops() once, then computes
    per-ROI metrics within each cell's bounding box crop.

    Args:
        roi_names: mapping from mask label value to ROI name string.
                   e.g. {1: "bound", 2: "free"}
    """
    metric_names = metrics or list(BUILTIN_METRICS.keys())
    slices = find_objects(labels)
    props = regionprops(labels)

    rows = []
    for prop in props:
        sl = slices[prop.label - 1]
        if sl is None:
            continue
        label_crop = labels[sl]
        image_crop = image[sl]
        mask_crop = mask[sl]
        cell_mask = label_crop == prop.label

        row: dict[str, Any] = {
            "label": prop.label,
            "centroid_y": prop.centroid[0],
            "centroid_x": prop.centroid[1],
            "bbox_y": prop.bbox[0],
            "bbox_x": prop.bbox[1],
            "bbox_h": prop.bbox[2] - prop.bbox[0],
            "bbox_w": prop.bbox[3] - prop.bbox[1],
        }

        # Whole-cell metrics (no mask)
        for name in metric_names:
            row[name] = BUILTIN_METRICS[name](image_crop, cell_mask)

        # Per-ROI metrics — computed on the small crop, fast
        for label_val, roi_name in roi_names.items():
            roi_cell = cell_mask & (mask_crop == label_val)
            n_pixels = int(roi_cell.sum())
            row[f"{roi_name}_area"] = n_pixels
            if n_pixels > 0:
                for name in metric_names:
                    if name != "area":
                        row[f"{roi_name}_{name}"] = BUILTIN_METRICS[name](
                            image_crop, roi_cell
                        )
            else:
                for name in metric_names:
                    if name != "area":
                        row[f"{roi_name}_{name}"] = 0.0

        rows.append(row)

    return pd.DataFrame(rows)
```

#### `src/percell4/gui/launcher.py`

**Modify `_on_measure_cells()`** to detect multi-label masks:

```python
def _on_measure_cells(self) -> None:
    ...
    mask = self._get_active_mask_array()
    if mask is not None and mask.max() > 1:
        roi_names = self._get_phasor_roi_names()
        df = measure_cells_multi_roi(image, labels, mask, roi_names, metrics)
    else:
        df = measure_cells(image, labels, metrics, mask)
    ...

def _get_phasor_roi_names(self) -> dict[int, str]:
    phasor_win = self._windows.get("phasor_plot")
    if phasor_win is None:
        # Fallback: generic names from mask values
        return {}
    return {
        w.phasor_roi.label: w.phasor_roi.name
        for w in phasor_win._roi_widgets
        if w.phasor_roi.visible
    }
```

**Verify:** Create 2 phasor ROIs named "bound" and "free". Apply mask. Measure cells. DataFrame contains: `bound_mean_intensity`, `bound_area`, `free_mean_intensity`, `free_area`, plus whole-cell metrics. Data Plot dropdowns show these new columns.

---

## Deferred to Follow-Up PRs

| Feature | Reason |
|---------|--------|
| **`outside_all` measurement scope** | Derivable from whole-cell minus per-ROI values. Add when requested. |
| **Drag-to-reorder ROI list** | Plain list is sufficient for v1. Overlap priority by list order still works. |
| **ROI library directory + dropdown menu** | File dialog save/load is sufficient. Library UX can be added if users accumulate many presets. |
| **User-editable color picker** | Auto-assigned colors from cycle are sufficient. |
| **In-place numpy ops in `phasor_roi_to_mask()`** | Performance optimization for 4096x4096. Profile first. |

---

## Edge Cases and Mitigations

| Edge Case | Mitigation |
|-----------|-----------|
| No visible ROIs when "Apply" clicked | Show status bar message, do nothing |
| Two ROIs with same name | Enforce unique names; append `_2` if duplicate detected |
| Old binary `masks/phasor_roi` in existing .h5 | Works as-is: value 1 = one ROI. `measure_cells()` binary path handles it |
| Load ROIs onto dataset with different dimensions | ROIs are in G/S coordinate space, not pixel space — always valid |
| Delete ROI after applying mask | `set_active_mask("")` forces re-application before measurement |
| >10 ROIs | Soft limit of 10 with message. Hard limit 255 (uint8). |
| Large image (4096x4096) live preview | Per-ROI mask caching + 100ms debounce. Only recompute dragged ROI. |
| Invalid JSON file on load | `PhasorROI.from_dict()` validates; `QMessageBox` on failure |
| Overlap priority confusion | Later in list wins. Consistent: same rule always applies. |
| ROI extends beyond semicircle | Allowed — some analyses need ROIs outside the semicircle |
| Filtered/unfiltered toggle changes G/S maps | All cached masks invalidated in `_on_filtered_toggled()` |

## Acceptance Criteria

### Functional Requirements

- [ ] **Add multiple ROIs**: "Add ROI" creates new colored ellipse, up to 10
- [ ] **Remove ROI**: removes selected ROI, renumbers labels, invalidates active mask
- [ ] **Name ROIs**: editable name field, unique + non-empty enforced
- [ ] **Color per ROI**: auto-assigned from cycle, distinct on plot and in napari
- [ ] **Visibility toggle**: hidden ROIs excluded from mask and "Apply Visible"
- [ ] **Angle per ROI**: selected ROI's angle controlled by spinbox
- [ ] **Combined mask**: all visible ROIs produce single uint8 mask (0=outside, 1..N=labels)
- [ ] **Overlap resolution**: later ROIs in list overwrite earlier at overlapping pixels
- [ ] **Live preview**: napari shows multi-colored preview, debounced at 100ms
- [ ] **Apply Visible as Mask**: writes combined mask to HDF5, sets active mask
- [ ] **Save ROIs**: file dialog, writes JSON
- [ ] **Load ROIs**: file dialog, validates JSON, replaces current ROIs
- [ ] **Per-ROI measurement**: `{roi_name}_{metric}` columns in DataFrame for each ROI
- [ ] **Multi-label viewer colormap**: napari mask colors match phasor ROI colors
- [ ] **Backward compatible**: old binary `masks/phasor_roi` still works

### Non-Functional Requirements

- [ ] Live preview updates in <200ms for 4096x4096 with 10 ROIs (per-ROI caching)
- [ ] Measurement with multi-label mask completes within 2x single-mask time (single-pass)
- [ ] JSON save/load is <100ms
- [ ] No memory leaks from ROI widget cleanup

## Dependencies & Risks

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| pyqtgraph ROI handle overlap with multiple ROIs | Medium | Highlight only selected ROI's handles; dim others |
| Axis desync with many ROIs | Medium | Disable auto-range and SI prefix after every refresh |
| Wide DataFrame (N ROIs × M metrics × C channels) | Low | Expected for multi-population analysis; user selects columns in Data Plot |
| `phasor_roi_to_mask` memory at 4096x4096 | Medium | Defer in-place optimization; current peak ~384MB per ROI is acceptable for desktop |

## References

- `src/percell4/gui/phasor_plot.py` — current single-ROI system, full rewrite of ROI management
- `src/percell4/flim/phasor.py:phasor_roi_to_mask()` — ellipse mask computation, called once per ROI
- `src/percell4/measure/measurer.py` — `measure_cells()` unchanged; new `measure_cells_multi_roi()` single-pass
- `src/percell4/store.py:write_mask()` — already supports uint8 0-255, update docstring
- `src/percell4/gui/viewer.py:add_mask()` — extend with `color_dict` parameter
- `docs/solutions/ui-bugs/percell4-phasor-plot-axis-desync.md` — SI prefix + auto-range gotcha
