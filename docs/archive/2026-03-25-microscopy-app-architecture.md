> **SUPERSEDED — 2026-04-10.** This is the original pre-rebuild architecture
> writeup (dated 2026-03-25). The canonical current-state description of the
> PerCell4 architecture lives in `CLAUDE.md` and the per-module `CLAUDE.md`
> files under `src/percell4/`. Kept here for historical context only.

# Microscopy Analysis App — Architecture Guide

## Overview

A standalone desktop application for single-cell FLIM microscopy analysis, built around:

- **Napari** — image viewer with interactive label layers
- **Cellpose** — automated single-cell segmentation
- **Phasor plots** — FLIM lifetime visualization with ROI-to-mask feedback
- **Data visualization** — per-cell metrics linked to viewer selection

---

## Architecture: Qt Application with Embedded Napari

Since napari is built on Qt (via `qtpy`), the cleanest architecture is a **single Qt process**
where napari's viewer is one widget among several in a `QMainWindow`. All panels share memory
and communicate through Qt signals/slots — no sockets, no IPC, no serialization overhead.

```
┌─────────────────────────────────────────────────────────┐
│  QMainWindow                                            │
│  ┌───────────────────────┬─────────────────────────────┐│
│  │                       │  QDockWidget: Data Plots    ││
│  │   Central Widget:     │  (pyqtgraph)                ││
│  │   napari.Viewer       │                             ││
│  │                       ├─────────────────────────────┤│
│  │   - Image layers      │  QDockWidget: Phasor Plot   ││
│  │   - Labels layers     │  (pyqtgraph + ROI tools)    ││
│  │   - Shapes layers     │                             ││
│  │                       ├─────────────────────────────┤│
│  │                       │  QDockWidget: Cell Table     ││
│  │                       │  (QTableView)               ││
│  └───────────────────────┴─────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │  Status Bar / Progress                              ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

### Why This Pattern (Not a Napari Plugin)

- Full control over window layout, menus, toolbar
- Can be packaged as a standalone `.exe` / `.app` with PyInstaller or `briefcase`
- You own the main event loop — easier to add background tasks
- Still uses napari's full API — you're just hosting it, not extending it

---

## Recommended Tech Stack

| Component              | Library                   | Why                                                    |
|------------------------|---------------------------|--------------------------------------------------------|
| GUI framework          | `qtpy` + `PyQt5`          | napari already depends on it; one event loop            |
| Image viewer           | `napari`                  | Best-in-class for nD scientific images                  |
| Segmentation           | `cellpose`                | Gold standard for cell segmentation                     |
| Plotting               | `pyqtgraph`               | Fast, Qt-native, GPU-accelerated; interactive ROIs      |
| Data model             | `pandas` + `scikit-image` | `regionprops_table` → DataFrame of per-cell metrics     |
| FLIM file I/O          | `sdtfile`, `ptufile`, `tifffile` | Covers Becker&Hickl, PicoQuant, OME-TIFF         |
| Phasor math            | `numpy` / `scipy.fft`    | FFT-based phasor calculation is ~10 lines of numpy      |
| Packaging              | `PyInstaller` or `briefcase` | Standalone installer for distribution               |

### Why `pyqtgraph` over `matplotlib`?

Matplotlib blocks the Qt event loop during rendering, which freezes the UI. `pyqtgraph` renders
in the same thread as Qt and can handle 100k+ scatter points at 60fps — critical for phasor plots
where each pixel is a data point. It also has built-in ROI classes with drag signals.

---

## Data Flow & Communication

The key insight: **everything flows through a shared data model and Qt signals.**

```
File loaded
    │
    ▼
┌──────────┐   intensity image    ┌──────────────┐
│ IO Layer │ ───────────────────▶ │ napari viewer │
│          │   FLIM decay cube    │  (image layer)│
└──────────┘         │            └──────┬───────┘
                     │                   │ user clicks
                     ▼                   │ "Run Cellpose"
              ┌─────────────┐            │
              │ Phasor Calc │            ▼
              │ (G, S maps) │    ┌──────────────┐
              └──────┬──────┘    │   Cellpose    │
                     │           │ (background   │
                     ▼           │  QThread)     │
              ┌─────────────┐    └──────┬───────┘
              │ Phasor Plot │           │ label mask
              │ (pyqtgraph) │           ▼
              └──────┬──────┘    ┌──────────────┐
                     │           │ napari viewer │
              ROI drawn          │ (labels layer)│
                     │           └──────┬───────┘
                     ▼                  │
              ┌─────────────┐           │ selection
              │ ROI → Mask  │           │ changes
              │ (new label  │           ▼
              │  layer)     │    ┌──────────────┐      ┌──────────────┐
              └─────────────┘    │  CellDataModel│ ──▶ │ Data Plots   │
                                 │  (pandas DF)  │      │ (pyqtgraph)  │
                                 └──────────────┘      └──────────────┘
```

### The CellDataModel (Central Hub)

This is the most important design decision. Create a single class that:
1. Holds a `pandas.DataFrame` with one row per cell (label ID, area, mean intensity,
   mean G, mean S, lifetime, circularity, etc.)
2. Emits Qt signals when data changes (`data_updated`, `selection_changed`)
3. All panels listen to these signals — they never talk to each other directly

```python
from qtpy.QtCore import QObject, Signal
import pandas as pd

class CellDataModel(QObject):
    """Central data store — all panels read from / write to this."""
    
    data_updated = Signal()           # emitted when measurements change
    selection_changed = Signal(list)   # emitted with list of selected label IDs
    
    def __init__(self):
        super().__init__()
        self.df = pd.DataFrame()       # per-cell measurements
        self._selected_ids = []
    
    def set_measurements(self, df: pd.DataFrame):
        """Called after segmentation + regionprops."""
        self.df = df
        self.data_updated.emit()
    
    def set_selection(self, label_ids: list):
        """Called when user clicks labels in napari or rows in table."""
        self._selected_ids = label_ids
        self.selection_changed.emit(label_ids)
    
    @property
    def selected_data(self) -> pd.DataFrame:
        return self.df[self.df['label'].isin(self._selected_ids)]
```

---

## Project Structure

```
microscopy_app/
├── main.py                  # Entry point — creates QApplication + MainWindow
├── app/
│   ├── __init__.py
│   ├── main_window.py       # QMainWindow: assembles all panels
│   ├── data_model.py        # CellDataModel (pandas DataFrame + signals)
│   │
│   ├── io/
│   │   ├── __init__.py
│   │   ├── readers.py        # File readers: .sdt, .ptu, .tiff, etc.
│   │   └── formats.py        # Format detection / metadata extraction
│   │
│   ├── panels/
│   │   ├── __init__.py
│   │   ├── viewer_panel.py   # Napari viewer wrapper + event wiring
│   │   ├── phasor_panel.py   # Phasor plot (pyqtgraph) + ROI logic
│   │   ├── data_panel.py     # Data visualization (scatter, histogram, etc.)
│   │   └── cell_table.py     # QTableView for per-cell measurements
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── segmentation.py   # Cellpose wrapper (runs in QThread)
│   │   ├── phasor.py         # G/S calculation from FLIM decay data
│   │   ├── measurements.py   # regionprops + custom FLIM metrics
│   │   └── roi_tools.py      # Phasor ROI → spatial mask conversion
│   │
│   └── workers/
│       ├── __init__.py
│       └── threaded.py       # Generic QThread worker for long tasks
│
├── tests/
├── requirements.txt
└── pyproject.toml
```

---

## Key Implementation Patterns

### 1. Hosting Napari in Your Own QMainWindow

```python
# main_window.py
import napari
from qtpy.QtWidgets import QMainWindow, QDockWidget
from qtpy.QtCore import Qt

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FLIM Cell Analyzer")
        
        # Create the shared data model
        self.data_model = CellDataModel()
        
        # Create napari viewer (headless — we embed its widget)
        self.viewer = napari.Viewer(show=False)
        self.setCentralWidget(self.viewer.window._qt_window)
        # NOTE: you can also do:
        #   self.setCentralWidget(self.viewer.window._qt_viewer)
        # to get JUST the canvas without napari's own menus/toolbars
        
        # Add dock panels
        self.phasor_panel = PhasorPanel(self.data_model)
        self.data_panel = DataPanel(self.data_model)
        self.cell_table = CellTablePanel(self.data_model)
        
        self._add_dock(self.phasor_panel, "Phasor Plot", Qt.RightDockWidgetArea)
        self._add_dock(self.data_panel, "Data Plots", Qt.RightDockWidgetArea)
        self._add_dock(self.cell_table, "Cell Table", Qt.BottomDockWidgetArea)
        
        # Wire napari events to data model
        self._connect_events()
    
    def _add_dock(self, widget, title, area):
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
    
    def _connect_events(self):
        # When user clicks a label in napari → update selection
        @self.viewer.layers.events.inserted.connect
        def on_layer_added(event):
            layer = event.value
            if isinstance(layer, napari.layers.Labels):
                layer.events.selected_label.connect(
                    lambda e: self.data_model.set_selection([e.value])
                )
```

### 2. Running Cellpose Without Freezing the UI

```python
# workers/threaded.py
from qtpy.QtCore import QThread, Signal

class SegmentationWorker(QThread):
    finished = Signal(object)  # emits the label mask (numpy array)
    progress = Signal(str)     # status messages
    
    def __init__(self, image, model_type='cyto3', diameter=None):
        super().__init__()
        self.image = image
        self.model_type = model_type
        self.diameter = diameter
    
    def run(self):
        from cellpose import models
        self.progress.emit("Loading Cellpose model...")
        model = models.Cellpose(model_type=self.model_type, gpu=True)
        
        self.progress.emit("Running segmentation...")
        masks, flows, styles, diams = model.eval(
            self.image,
            diameter=self.diameter,
            channels=[0, 0]  # adjust for your data
        )
        self.finished.emit(masks)


# In your main window or viewer panel:
def run_segmentation(self):
    image = self.viewer.layers['Image'].data
    self.worker = SegmentationWorker(image)
    self.worker.progress.connect(self.statusBar().showMessage)
    self.worker.finished.connect(self.on_segmentation_done)
    self.worker.start()

def on_segmentation_done(self, masks):
    self.viewer.add_labels(masks, name='Cellpose Segmentation')
    # Compute per-cell measurements
    from skimage.measure import regionprops_table
    props = regionprops_table(masks, self.current_image,
                              properties=['label', 'area', 'mean_intensity',
                                          'centroid', 'eccentricity'])
    self.data_model.set_measurements(pd.DataFrame(props))
```

### 3. Phasor Plot with ROI → Mask Feedback

```python
# panels/phasor_panel.py
import pyqtgraph as pg
import numpy as np
from qtpy.QtWidgets import QWidget, QVBoxLayout

class PhasorPanel(QWidget):
    def __init__(self, data_model):
        super().__init__()
        self.data_model = data_model
        
        layout = QVBoxLayout(self)
        self.plot_widget = pg.PlotWidget(title="Phasor Plot")
        self.plot_widget.setAspectLocked(True)
        layout.addWidget(self.plot_widget)
        
        # Scatter plot for phasor points
        self.scatter = pg.ScatterPlotItem(size=2, pen=None, brush='w')
        self.plot_widget.addItem(self.scatter)
        
        # Draw universal semicircle
        theta = np.linspace(0, np.pi, 100)
        self.plot_widget.plot(0.5 + 0.5*np.cos(theta),
                              0.5*np.sin(theta), pen='y')
        
        # Add interactive ROI (ellipse by default — good for phasor)
        self.roi = pg.EllipseROI([0.2, 0.1], [0.2, 0.2], pen='r')
        self.plot_widget.addItem(self.roi)
        self.roi.sigRegionChangeFinished.connect(self.on_roi_changed)
        
        # Store G, S coordinate maps (set when FLIM data is loaded)
        self.g_map = None  # shape: (H, W)
        self.s_map = None  # shape: (H, W)
    
    def set_phasor_data(self, g_map, s_map):
        """Called when FLIM data is loaded and phasor is computed."""
        self.g_map = g_map
        self.s_map = s_map
        self.scatter.setData(g_map.ravel(), s_map.ravel())
    
    def on_roi_changed(self):
        """Convert phasor ROI to a spatial mask and add as napari layer."""
        if self.g_map is None:
            return
        
        # Get ROI bounds in phasor coordinates
        roi_shape = self.roi.mapToItem(self.scatter, self.roi.shape())
        
        # For ellipse: check which pixels fall inside
        # (simplified — real implementation would use the ROI's transform)
        state = self.roi.getState()
        cx = state['pos'][0] + state['size'][0] / 2
        cy = state['pos'][1] + state['size'][1] / 2
        rx = state['size'][0] / 2
        ry = state['size'][1] / 2
        
        mask = (((self.g_map - cx) / rx)**2 +
                ((self.s_map - cy) / ry)**2) <= 1.0
        
        # Emit this mask back to the viewer (via signal or direct reference)
        # The main window would do:
        #   self.viewer.add_labels(mask.astype(int), name='Phasor ROI')
```

### 4. Phasor Calculation from FLIM Data

```python
# analysis/phasor.py
import numpy as np

def compute_phasor(decay_stack, harmonic=1):
    """
    Compute phasor G and S coordinates from FLIM decay data.
    
    Parameters
    ----------
    decay_stack : np.ndarray
        Shape (H, W, T) where T is the number of time bins.
    harmonic : int
        Harmonic number (1 = fundamental frequency).
    
    Returns
    -------
    g_map, s_map : np.ndarray
        Phasor coordinates, each shape (H, W).
    """
    n_bins = decay_stack.shape[-1]
    t = np.arange(n_bins)
    omega = 2 * np.pi * harmonic / n_bins
    
    # Total photon counts per pixel (for normalization)
    total = decay_stack.sum(axis=-1, keepdims=True)
    total = np.where(total == 0, 1, total)  # avoid division by zero
    
    # Phasor transform (discrete cosine/sine transform at the harmonic freq)
    g_map = (decay_stack * np.cos(omega * t)).sum(axis=-1) / total.squeeze()
    s_map = (decay_stack * np.sin(omega * t)).sum(axis=-1) / total.squeeze()
    
    return g_map, s_map
```

---

## FLIM File I/O — Multi-Format Strategy

Since you work with multiple formats, create a dispatcher:

```python
# io/readers.py
from pathlib import Path
import numpy as np

def load_flim(filepath: str):
    """
    Load FLIM data from any supported format.
    
    Returns
    -------
    dict with keys:
        'intensity' : np.ndarray (H, W) or (H, W, C) — intensity image
        'decay'     : np.ndarray (H, W, T) — raw decay histograms (if available)
        'metadata'  : dict — laser freq, time resolution, etc.
    """
    ext = Path(filepath).suffix.lower()
    
    if ext == '.sdt':
        return _load_sdt(filepath)
    elif ext == '.ptu':
        return _load_ptu(filepath)
    elif ext in ('.tif', '.tiff'):
        return _load_tiff(filepath)
    else:
        raise ValueError(f"Unsupported format: {ext}")

def _load_sdt(filepath):
    import sdtfile
    sdt = sdtfile.SdtFile(filepath)
    decay = sdt.data[0]  # shape: (H, W, T)
    intensity = decay.sum(axis=-1)
    return {
        'intensity': intensity,
        'decay': decay,
        'metadata': {'frequency': sdt.measure_info[0].laser_rep_rate}
    }

def _load_ptu(filepath):
    # ptufile or readPTU_FLIM or picoquant-ptu
    import ptufile
    ptu = ptufile.PtuFile(filepath)
    # Implementation depends on the specific library version
    # Generally: decode photons → bin into (H, W, T) histogram
    ...

def _load_tiff(filepath):
    import tifffile
    data = tifffile.imread(filepath)
    # Convention: last axis is time bins if ndim == 3
    if data.ndim == 3:
        return {
            'intensity': data.sum(axis=-1),
            'decay': data,
            'metadata': {}
        }
    else:
        return {
            'intensity': data,
            'decay': None,
            'metadata': {}
        }
```

---

## Per-Cell Measurement Pipeline

```python
# analysis/measurements.py
import numpy as np
import pandas as pd
from skimage.measure import regionprops_table

def measure_cells(labels, intensity, g_map=None, s_map=None, decay=None):
    """
    Compute per-cell measurements and return as DataFrame.
    
    Each row = one cell. Columns include morphological and FLIM metrics.
    """
    # Standard morphological + intensity features
    props = regionprops_table(
        labels, intensity,
        properties=[
            'label', 'area', 'centroid',
            'mean_intensity', 'max_intensity', 'min_intensity',
            'eccentricity', 'solidity', 'perimeter',
        ]
    )
    df = pd.DataFrame(props)
    
    # Add FLIM phasor metrics per cell
    if g_map is not None and s_map is not None:
        g_means, s_means, lifetimes = [], [], []
        for label_id in df['label']:
            mask = labels == label_id
            g_mean = g_map[mask].mean()
            s_mean = s_map[mask].mean()
            g_means.append(g_mean)
            s_means.append(s_mean)
            
            # Apparent lifetime from phasor (phase lifetime)
            # tau_phi = s / (omega * g) where omega = 2*pi*f
            # You'll need the laser rep rate from metadata
        
        df['g_mean'] = g_means
        df['s_mean'] = s_means
    
    return df
```

---

## Getting Started — Learning Path

Since this is your first Qt project, here's a suggested build order. Each step is
independently testable, so you always have something that works:

### Phase 1: Minimal skeleton (Day 1-2)
1. Create `main.py` that opens a `QMainWindow` with an embedded napari viewer
2. Add a menu item: File → Open that loads a TIFF as a napari `Image` layer
3. Verify you can pan/zoom and see the image

### Phase 2: Segmentation (Day 3-5)
4. Add a "Run Cellpose" button (in a toolbar or dock widget)
5. Run Cellpose in a QThread, add result as a `Labels` layer
6. Compute `regionprops_table` and store in your `CellDataModel`

### Phase 3: Data panel (Day 5-7)
7. Add a `QDockWidget` with a `pyqtgraph` scatter plot
8. Wire it to `CellDataModel.data_updated` — plot area vs. mean intensity
9. Wire napari label selection → highlight the corresponding point

### Phase 4: Phasor plot (Week 2)
10. Implement `compute_phasor()` for your FLIM data
11. Add the phasor panel with scatter + universal semicircle
12. Add ROI → mask feedback loop

### Phase 5: Polish (Week 3+)
13. Add cell table panel
14. Add file format auto-detection
15. Add export (CSV of measurements, screenshot of plots)
16. Package with PyInstaller

---

## Dependencies (`requirements.txt`)

```
napari[all]>=0.5.0
cellpose>=3.0
pyqtgraph>=0.13
PyQt5>=5.15
qtpy
numpy
pandas
scikit-image
scipy
tifffile
sdtfile
# ptufile          # uncomment when needed
# readPTU-FLIM     # alternative PicoQuant reader
```

---

## Tips for a First-Time Qt Developer

1. **Start every panel as a standalone script.** Test your phasor plot in isolation
   before embedding it. `pyqtgraph.examples.run()` is a great playground.

2. **Use `qtpy` instead of importing `PyQt5` directly.** This lets napari handle
   the Qt binding choice (PyQt5 vs PySide2) and avoids conflicts.

3. **Never do heavy computation in the main thread.** If the UI freezes for >100ms,
   users will think it crashed. Use `QThread` for anything that takes >0.5 seconds.

4. **Signals and slots are your best friend.** Think of them as an event bus.
   Panel A emits a signal → Panel B's slot reacts. They don't need to import each other.

5. **`pyqtgraph` has amazing examples.** Run `python -m pyqtgraph.examples` to see
   scatter plots, ROIs, image views, and more — all with source code.

6. **napari's event system** (`layer.events.*`) is well-documented at
   https://napari.org/stable/howtos/connecting_events.html

7. **For packaging**, PyInstaller with `--onedir` mode is the most reliable for
   scientific Python apps. Expect to spend a day on the spec file.
