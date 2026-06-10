# Adaptive Local Clipping — Core Implementation

Full source snippets for the four core implementation files behind the
**Adaptive Local Clipping** GUI module.

Data flow: `AdaptiveClipSettingsWidget` (config) → `AdaptiveClipPanel._on_run()`
→ `run_adaptive_detection()` worker → `domain/measure/adaptive_clip.py`
(detect/estimate) → `AcceptPunctaMask` use case → mask written to store &
auto-selected.

| File | Role |
| --- | --- |
| `src/percell4/domain/measure/adaptive_clip.py` | Pure-domain algorithm (detect / Otsu / auto-window / size resolve) |
| `src/percell4/gui/_adaptive_clip_settings.py` | Settings model + reusable Qt form |
| `src/percell4/gui/adaptive_clip_panel.py` | Creator panel + worker body |
| `src/percell4/application/use_cases/accept_puncta_mask.py` | Persistence use case |

---

## 1. `src/percell4/domain/measure/adaptive_clip.py`

The algorithm. Pure domain (numpy / scipy / skimage only).

```python
"""Whole-frame adaptive local-clipping helpers + auto-window estimation.

Pure domain (``numpy`` / ``scipy`` / ``skimage`` only — no Qt, napari, h5py, or
store). These back the interactive **Adaptive Local Clipping** GUI module:

* :func:`detect_adaptive_whole_frame` runs the validated ``adaptive`` detector
  over a whole frame by handing :func:`detect_two_pass` a single full-frame
  "group" (an all-``True`` mask) — same computation that produced the gallery
  masks, just without per-cell-group isolation.
* :func:`otsu_first_pass` + :func:`estimate_adaptive_window` implement the
  auto-window calibration: the local window is sized to the granules in the
  image from the mean particle size of an Otsu first-pass mask.
* :func:`resolve_min_area_px` converts the GUI particle-size filter (px² or µm²)
  into an integer pixel-area threshold, mirroring
  :func:`percell4.workflows.phases._resolve_min_area_px`.

``settings`` is duck-typed (a :class:`~percell4.workflows.models.PunctaDetectorSettings`)
exactly as :func:`detect_two_pass` does, so the pure domain layer never imports
the workflows layer at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from percell4.domain.measure.puncta_pipeline import detect_two_pass
from percell4.domain.measure.thresholding import apply_gaussian_smoothing

if TYPE_CHECKING:  # type-only; no runtime import of the workflows layer
    from percell4.workflows.models import PunctaDetectorSettings

# Auto-window calibration. window ~= FACTOR * mean granule diameter, where the
# mean equivalent diameter (2*sqrt(area/pi)) is taken from the Otsu first-pass
# mask. FACTOR is the single tunable constant — calibrate so small (As+Noco)
# granules land near window 15 and matured (WT 90min-wash) granules near 50.
AUTO_WINDOW_FACTOR = 2.0
AUTO_WINDOW_MIN = 11
AUTO_WINDOW_MAX = 151
# Otsu-mask components smaller than this (px area) are treated as noise and
# excluded from the mean-diameter estimate.
AUTO_WINDOW_NOISE_FLOOR_PX = 3


def _make_odd(n: int) -> int:
    """Nearest odd integer >= ``n`` (forces the local window to be odd)."""
    return int(n) | 1


def detect_adaptive_whole_frame(
    image: np.ndarray,
    gaussian_sigma: float | None,
    settings: PunctaDetectorSettings,
) -> np.ndarray:
    """Run the ``adaptive`` detector over the whole frame (one sigma).

    Smooths ``image`` (``gaussian_sigma``), then runs :func:`detect_two_pass`
    with a single full-frame group (all-``True`` mask). Returns the detector's
    ``{0, 1}`` ``uint8`` mask; the size filter is applied inside
    :func:`detect_two_pass` via ``settings.min_spot_px``.
    """
    img = np.asarray(image, dtype=np.float32)
    smoothed = apply_gaussian_smoothing(img, gaussian_sigma)
    group = np.ones(smoothed.shape, dtype=bool)
    return detect_two_pass(smoothed, group, settings)


def otsu_first_pass(smoothed: np.ndarray) -> np.ndarray:
    """Boolean whole-frame Otsu mask of an already-smoothed image.

    Guards the degenerate constant/empty case (``threshold_otsu`` raises on a
    single intensity level): returns an all-``False`` mask instead.
    """
    from skimage.filters import threshold_otsu as sk_threshold_otsu

    sm = np.asarray(smoothed, dtype=np.float32)
    finite = sm[np.isfinite(sm)]
    if finite.size == 0 or float(finite.min()) == float(finite.max()):
        return np.zeros(sm.shape, dtype=bool)
    thr = float(sk_threshold_otsu(finite))
    return sm > thr


def estimate_adaptive_window(
    otsu_mask: np.ndarray,
    *,
    factor: float = AUTO_WINDOW_FACTOR,
    lo: int = AUTO_WINDOW_MIN,
    hi: int = AUTO_WINDOW_MAX,
    noise_floor_px: int = AUTO_WINDOW_NOISE_FLOOR_PX,
) -> int:
    """Estimate the adaptive window from the mean granule size of an Otsu mask.

    ``window = clamp(make_odd(round(factor * mean_equiv_diameter)), lo, hi)``,
    where the mean equivalent diameter (``2*sqrt(area/pi)``) is computed over the
    Otsu mask's connected components with area ``>= noise_floor_px``. An empty
    mask (or one with only sub-floor specks) returns ``make_odd(lo)``.
    """
    from skimage import measure

    mask = np.asarray(otsu_mask) > 0
    if not mask.any():
        return _make_odd(lo)
    labels = measure.label(mask)
    areas = np.array([p.area for p in measure.regionprops(labels)], dtype=float)
    areas = areas[areas >= noise_floor_px]
    if areas.size == 0:
        return _make_odd(lo)
    diameters = 2.0 * np.sqrt(areas / np.pi)
    window = int(round(factor * float(diameters.mean())))
    window = max(lo, min(hi, window))
    return _make_odd(window)


def resolve_min_area_px(value: float, unit: str, pixel_size_um: float | None) -> int:
    """Convert a particle-size filter value+unit into an integer pixel area.

    ``unit`` is ``"px"`` (area in pixels) or ``"um2"`` (area in µm²). The µm²
    option requires a positive ``pixel_size_um`` or raises :class:`ValueError`
    (no silent default — mirrors the workflow phase behavior).
    """
    v = float(value)
    if unit == "px":
        return int(round(v))
    if unit == "um2":
        if not pixel_size_um or float(pixel_size_um) <= 0:
            raise ValueError(
                "µm² particle-size filter requires a known pixel size; switch "
                "the unit to px² or re-import the dataset with TIFF resolution "
                "metadata."
            )
        return int(round(v / (float(pixel_size_um) ** 2)))
    raise ValueError(f"unknown size-filter unit: {unit!r}")
```

---

## 2. `src/percell4/gui/_adaptive_clip_settings.py`

Settings model (`AdaptiveClipConfig`) + reusable Qt form.

```python
"""Settings widget for the Adaptive Local Clipping module.

A reusable form (window / k / Gaussian σ / particle-size filter + unit / auto
window) that snapshots into a frozen :class:`AdaptiveClipConfig`. Owns only
Action-shaped per-run knobs — channel / segmentation remain Session-owned
Selectors. Mirrors ``gui/_grouped_threshold_settings.py`` (frozen
``current_config()`` + aggregated ``config_changed`` signal).
"""

from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Unit dropdown labels -> internal codes used by resolve_min_area_px.
_UNIT_LABELS = ("px²", "µm²")
_UNIT_CODES = {"px²": "px", "µm²": "um2"}


@dataclass(frozen=True)
class AdaptiveClipConfig:
    """Immutable snapshot of the adaptive-clip settings widget.

    ``window_px`` is always odd (the local window must be odd). ``min_size_unit``
    is the internal code (``"px"`` / ``"um2"``). When ``auto_window`` is True the
    ``window_px`` value is ignored by the run (it is estimated from an Otsu
    first-pass).
    """

    window_px: int
    k: float
    gaussian_sigma: float
    min_size_value: float
    min_size_unit: str
    auto_window: bool


class AdaptiveClipSettingsWidget(QWidget):
    """Window / k / σ / particle-size / auto-window form as one reusable widget.

    Emits :attr:`config_changed` whenever any child widget's user-edit signal
    fires. Checking "Auto adaptive window size" disables the window spinbox (it
    is computed at run time); unchecking re-enables it.
    """

    config_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_change_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Auto window size ──
        self._auto = QCheckBox("Auto adaptive window size")
        self._auto.setToolTip(
            "Estimate the window from an Otsu first-pass (sizes it to the granules)."
        )
        self._auto.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self._auto)

        # ── Adaptive window (px) ──
        win_row = QHBoxLayout()
        win_row.addWidget(QLabel("Window (px):"))
        self._window = QSpinBox()
        self._window.setRange(3, 151)
        self._window.setSingleStep(2)  # keep odd via the arrows
        self._window.setValue(15)
        win_row.addWidget(self._window)
        layout.addLayout(win_row)

        # ── k (sigma multiplier) ──
        k_row = QHBoxLayout()
        k_row.addWidget(QLabel("k (σ multiplier):"))
        self._k = QDoubleSpinBox()
        self._k.setRange(0.0, 20.0)
        self._k.setSingleStep(0.25)
        self._k.setValue(2.25)
        k_row.addWidget(self._k)
        layout.addLayout(k_row)

        # ── Gaussian sigma ──
        sig_row = QHBoxLayout()
        sig_row.addWidget(QLabel("Gaussian σ:"))
        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.0, 20.0)
        self._sigma.setSingleStep(0.5)
        self._sigma.setValue(1.0)
        self._sigma.setSpecialValueText("None")
        sig_row.addWidget(self._sigma)
        layout.addLayout(sig_row)

        # ── Particle-size filter (value + unit) ──
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Min particle size:"))
        self._min_size = QDoubleSpinBox()
        self._min_size.setRange(0.0, 1_000_000.0)
        self._min_size.setDecimals(2)
        self._min_size.setValue(3.0)
        size_row.addWidget(self._min_size)
        self._unit = QComboBox()
        self._unit.addItems(list(_UNIT_LABELS))
        size_row.addWidget(self._unit)
        layout.addLayout(size_row)

    def _connect_change_signals(self) -> None:
        # Signal-to-signal forwarding: Qt drops the extra arg the child signals
        # carry (value / index / bool), so config_changed (0-arg) re-emits cleanly.
        self._window.valueChanged.connect(self.config_changed)
        self._k.valueChanged.connect(self.config_changed)
        self._sigma.valueChanged.connect(self.config_changed)
        self._min_size.valueChanged.connect(self.config_changed)
        self._unit.currentIndexChanged.connect(self.config_changed)
        self._auto.toggled.connect(self.config_changed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_auto_toggled(self, checked: bool) -> None:
        self._window.setEnabled(not checked)

    # ── Public API ────────────────────────────────────────────────

    def current_config(self) -> AdaptiveClipConfig:
        """Snapshot the live widget state into a frozen dataclass (odd window)."""
        return AdaptiveClipConfig(
            window_px=int(self._window.value()) | 1,  # force odd
            k=float(self._k.value()),
            gaussian_sigma=float(self._sigma.value()),
            min_size_value=float(self._min_size.value()),
            min_size_unit=_UNIT_CODES[self._unit.currentText()],
            auto_window=self._auto.isChecked(),
        )

    def set_window_value(self, window_px: int) -> None:
        """Display an (auto-estimated) window in the spinbox without re-running.

        Used to surface the auto-window result after a run. Setting it
        programmatically does not re-trigger estimation (estimation only runs on
        the panel's Run button), so there is no auto/manual feedback loop.
        """
        self._window.setValue(int(window_px) | 1)

    def set_enabled(self, enabled: bool) -> None:
        """Lock/unlock all widgets during a run (preserves the auto/window gate)."""
        for widget in (self._auto, self._k, self._sigma, self._min_size, self._unit):
            widget.setEnabled(enabled)
        # The window field also respects the auto checkbox when re-enabling.
        self._window.setEnabled(enabled and not self._auto.isChecked())
```

---

## 3. `src/percell4/gui/adaptive_clip_panel.py`

The Creator panel + the pure worker body (`run_adaptive_detection`).

```python
"""Adaptive Local Clipping panel — interactive whole-frame `adaptive` detection.

Exposes the production `adaptive` puncta detector in the Analysis tab. A
**Creator**: the Run button detects puncta on the active channel (optionally
auto-sizing the local window from an Otsu first-pass), writes a new
``/masks/<name>`` layer, auto-selects it, and shows it in napari. Heavy compute
runs in a :class:`~percell4.gui.workers.Worker`. Reads ``session.active_channel``
directly; the Run button writes only ``active_mask`` (via ``AcceptPunctaMask``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from percell4.gui._adaptive_clip_settings import AdaptiveClipSettingsWidget
from percell4.gui._resource_name_prompt import prompt_for_resource_name
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)


def run_adaptive_detection(image, gaussian_sigma, settings, auto_window):
    """Worker body: optionally Otsu-estimate the window, then detect.

    Returns ``(mask uint8, window_used int)``. When ``auto_window`` is True the
    window is estimated from an Otsu first-pass (mean granule size) and the
    settings are rebuilt with it; otherwise the settings' window is used as-is.
    Pure (no Qt) so it is unit-testable and worker-safe.
    """
    from percell4.domain.measure.adaptive_clip import (
        detect_adaptive_whole_frame,
        estimate_adaptive_window,
        otsu_first_pass,
    )
    from percell4.domain.measure.thresholding import apply_gaussian_smoothing
    from percell4.workflows.models import PunctaDetectorSettings

    window_used = int(dict(settings.detector_params).get("window_px", 15))
    if auto_window:
        smoothed = apply_gaussian_smoothing(np.asarray(image, dtype=np.float32), gaussian_sigma)
        window_used = estimate_adaptive_window(otsu_first_pass(smoothed))
        params = dict(settings.detector_params)
        params["window_px"] = window_used
        settings = PunctaDetectorSettings(
            detector_name=settings.detector_name,
            seed_detector_name=settings.seed_detector_name,
            background_estimator_name=settings.background_estimator_name,
            detector_params=params,
            seed_params=settings.seed_params,
            min_spot_px=settings.min_spot_px,
            max_spot_px=settings.max_spot_px,
            spot_scale_prior=settings.spot_scale_prior,
        )
    mask = detect_adaptive_whole_frame(image, gaussian_sigma, settings)
    return mask, window_used


class AdaptiveClipPanel(QWidget):
    """Interactive Adaptive Local Clipping panel (Creator)."""

    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_repo: Callable[[], Any | None] = lambda: None,
        get_store: Callable[[], Any | None] = lambda: None,
        get_viewer_window: Callable[[], Any | None] = lambda: None,
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._get_repo = get_repo
        self._get_store = get_store
        self._get_viewer_window = get_viewer_window
        self._show_status_cb = show_status
        self._worker = None
        self._pending_name: str | None = None
        self._pending_auto = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        self._settings = AdaptiveClipSettingsWidget(self)
        layout.addWidget(self._settings)

        from percell4.gui import theme

        self._run_btn = QPushButton("Run Adaptive Clipping")
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN}; color: white;"
            f" padding: 8px; font-weight: bold; border-radius: 4px; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
        )
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        self._status = QLabel("Ready")
        self._status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-style: italic;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        layout.addStretch()

    # ── helpers ──────────────────────────────────────────────────

    def _show_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._show_status_cb(msg)

    # ── run (Creator) ────────────────────────────────────────────

    def _on_run(self) -> None:
        viewer_win = self._get_viewer_window()
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return
        store = self._get_store()
        if store is None:
            self._show_status("No dataset loaded")
            return

        channel = self.data_model.session.active_channel
        if not channel:
            self._show_status("Select a channel in the Session window first")
            return

        image = None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image" and layer.name == channel:
                image = np.asarray(layer.data)
                break
        if image is None:
            self._show_status(f"Channel '{channel}' not found in viewer")
            return
        if image.ndim == 3:  # time-lapse: detect on the currently-displayed frame
            t = int(viewer_win.viewer.dims.current_step[0])
            image = image[t]

        config = self._settings.current_config()

        # Resolve the particle-size filter to a px area (µm² needs calibration).
        from percell4.domain.measure.adaptive_clip import resolve_min_area_px

        pixel_size_um = None
        try:
            pixel_size_um = store.metadata.get("pixel_size_um")
        except Exception:
            pixel_size_um = None
        try:
            min_spot_px = resolve_min_area_px(
                config.min_size_value, config.min_size_unit, pixel_size_um
            )
        except ValueError as e:
            self._show_status(str(e))
            return

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Adaptive Clipping Mask",
            label="Mask name:",
            default="adaptive",
            existing_names=existing,
        )
        if mask_name is None:
            return

        from percell4.workflows.models import PunctaDetectorSettings

        settings = PunctaDetectorSettings(
            detector_name="adaptive",
            seed_detector_name="otsu",
            background_estimator_name="gaussian-peak",
            detector_params={"window_px": config.window_px, "k": config.k},
            min_spot_px=max(1, int(min_spot_px)),
            spot_scale_prior=(1.0, 4.0),
        )

        self._pending_name = mask_name
        self._pending_auto = config.auto_window
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        self._show_status(
            "Detecting (auto window)..." if config.auto_window else "Detecting..."
        )

        from percell4.gui.workers import Worker

        self._worker = Worker(
            run_adaptive_detection, image, config.gaussian_sigma, settings, config.auto_window
        )
        self._worker.finished.connect(self._on_detect_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _on_detect_error(self, err) -> None:
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)
        self._show_status(f"Detection error: {err.exc_type}: {err.message}")

    def _on_detect_done(self, result) -> None:
        mask, window_used = result
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)

        name = self._pending_name or "adaptive"
        try:
            from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask

            repo = self._get_repo()
            uc = AcceptPunctaMask(repo, self.data_model.session)
            res = uc.execute(mask, name)
        except Exception as e:  # noqa: BLE001 — surface any persist failure to the user
            self._show_status(f"Failed to save mask: {e}")
            return

        viewer_win = self._get_viewer_window()
        if viewer_win is not None:
            viewer_win.add_mask(np.asarray(mask, dtype=np.uint8), name=name)

        if self._pending_auto:
            self._settings.set_window_value(window_used)

        win_note = f" (auto window {window_used})" if self._pending_auto else ""
        self._show_status(f"Saved '{name}': {res.n_positive:,} px{win_note}")
```

---

## 4. `src/percell4/application/use_cases/accept_puncta_mask.py`

Persistence use case (Creator contract: store-before-layer, refresh, select).

```python
"""Use case: persist a puncta mask and select it (Creator)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.ports.dataset_repository import DatasetRepository


@dataclass
class PunctaMaskResult:
    """Result of persisting a puncta mask."""

    mask_name: str
    n_positive: int
    n_total: int


class AcceptPunctaMask:
    """Persist a ``{0, 1}`` puncta mask and auto-select it.

    Owns Creator steps 1/3/4 (see ``docs/solutions/architecture-patterns/
    creator-contract-four-step-sequence-2026-05-18.md``): write the mask to the
    store first (store-before-layer), refresh the resource inventory, then set it
    as the active mask. The calling panel owns step 2 (``viewer.add_mask``).

    Takes only ``repo`` + ``session`` (no viewer port) so it stays Qt-free and
    unit-testable against a real ``DatasetStore``.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(self, mask: NDArray, name: str) -> PunctaMaskResult:
        """Coerce to ``{0, 1}`` uint8, persist, and select.

        Args:
            mask: The detected mask (any binary-coercible array).
            name: The HDF5 ``/masks/<name>`` layer name (caller-chosen).
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")
        if not name:
            raise ValueError("mask name must be non-empty")

        # Enforce the {0,1} uint8 contract at the store boundary.
        binary = (np.asarray(mask) > 0).astype(np.uint8)

        # Store-before-layer: write to HDF5 first.
        self._repo.write_mask(handle, name, binary)

        # Refresh inventory before auto-selecting so subscribers re-list the
        # mask combos before they look up the just-written name.
        self._session.refresh_resource_lists(
            mask_names=self._repo.list_masks(handle),
        )
        self._session.set_active_mask(name)

        return PunctaMaskResult(
            mask_name=name,
            n_positive=int(binary.sum()),
            n_total=binary.size,
        )
```
