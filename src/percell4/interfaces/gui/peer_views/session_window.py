"""Always-visible Session window — canonical Selector site.

Wide, short, always-on-top top-level window that owns the three canonical
Selectors for ``session.active_channel``, ``session.active_segmentation``,
and ``session.active_mask``. Sibling of the Launcher, Phasor Window, and
Viewer. Designed to sit pinned at the top edge of the screen so the user
can pick what they're working on without tab-navigating to the Data tab.

The window mirrors the data-tab Selector wiring shape
(``currentTextChanged`` -> ``session.set_active_*``) and subscribes to
Session events so its combos always reflect Session truth.
"""

from __future__ import annotations

from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSpinBox,
    QWidget,
)

from percell4.application.session import Event
from percell4.model import CellDataModel

_QSETTINGS_ORG = "LeeLabPerCell4"
_QSETTINGS_APP = "PerCell4"
_GEOMETRY_KEY = "session_window/geometry"
_PIN_KEY = "session_window/pin_on_top"
_NO_DATASET_TEXT = "(no dataset)"
_DEFAULT_WIDTH = 720
_DEFAULT_HEIGHT = 80


class SessionWindow(QMainWindow):
    """Canonical Selector window for the three session active fields."""

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self._session = data_model.session
        self._loading = False

        self.setWindowTitle("PerCell4 — Session")
        self._build_ui()
        self._restore_pin_on_top()
        self._restore_geometry()

        self._unsubs = [
            self._session.subscribe(Event.DATASET_CHANGED, self._on_dataset_changed),
            self._session.subscribe(
                Event.ACTIVE_CHANNEL_CHANGED, self._on_active_channel_changed
            ),
            self._session.subscribe(
                Event.ACTIVE_MASK_CHANGED, self._on_active_mask_changed
            ),
            self._session.subscribe(
                Event.ACTIVE_SEGMENTATION_CHANGED,
                self._on_active_segmentation_changed,
            ),
            self._session.subscribe(
                Event.ACTIVE_BIN_CHANGED, self._on_active_bin_changed
            ),
            self._session.subscribe(
                Event.CHANNEL_LIST_CHANGED, self._refresh_channel_combo
            ),
            self._session.subscribe(
                Event.MASK_LIST_CHANGED, self._refresh_mask_combo
            ),
            self._session.subscribe(
                Event.SEGMENTATION_LIST_CHANGED, self._refresh_seg_combo
            ),
        ]

        self._refresh_all()

    # ── UI construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(12)

        # Dataset name header (left).
        self._dataset_label = QLabel(_NO_DATASET_TEXT)
        self._dataset_label.setStyleSheet("font-weight: 600;")
        row.addWidget(self._dataset_label)
        row.addSpacing(8)

        # Channel selector.
        row.addWidget(QLabel("Channel:"))
        self._channel_combo = QComboBox()
        self._channel_combo.setMinimumWidth(140)
        self._channel_combo.currentTextChanged.connect(self._on_channel_combo_changed)
        row.addWidget(self._channel_combo)

        # Mask selector.
        row.addSpacing(6)
        row.addWidget(QLabel("Mask:"))
        self._mask_combo = QComboBox()
        self._mask_combo.setMinimumWidth(140)
        self._mask_combo.currentTextChanged.connect(self._on_mask_combo_changed)
        row.addWidget(self._mask_combo)

        # Segmentation selector.
        row.addSpacing(6)
        row.addWidget(QLabel("Segmentation:"))
        self._seg_combo = QComboBox()
        self._seg_combo.setMinimumWidth(140)
        self._seg_combo.currentTextChanged.connect(self._on_seg_combo_changed)
        row.addWidget(self._seg_combo)

        # View-bin selector. The canonical (and only) Selector for
        # session.active_bin. DataPanel mirrors the value display but
        # never writes it (consolidate-canonical-state).
        row.addSpacing(6)
        row.addWidget(QLabel("View bin (k):"))
        self._bin_spin = QSpinBox()
        self._bin_spin.setRange(1, 16)
        self._bin_spin.setValue(1)
        self._bin_spin.setMinimumWidth(60)
        self._bin_spin.setToolTip(
            "Session-level view bin: every store read downsamples by "
            "k×k at this setting (sum for intensity/decay, mean for "
            "phasor, majority-vote for masks, mode for labels). "
            "Native (k=1) storage is unchanged. Resets to 1 on dataset switch."
        )
        self._bin_spin.valueChanged.connect(self._on_bin_spin_changed)
        row.addWidget(self._bin_spin)

        row.addStretch()

        # Always-on-top toggle (right). Controls Z-order — when checked,
        # the window stays above other PerCell4 windows even when they
        # have focus. Does NOT control screen position.
        self._pin_check = QCheckBox("Always on top")
        self._pin_check.setToolTip(
            "Keep this window above other PerCell4 windows even when they "
            "have focus. Does not move the window — drag it where you want."
        )
        self._pin_check.toggled.connect(self._on_pin_toggled)
        row.addWidget(self._pin_check)

    # ── Combo population ────────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._refresh_dataset_header()
        self._refresh_channel_combo()
        self._refresh_mask_combo()
        self._refresh_seg_combo()
        self._refresh_bin_spin()

    def _refresh_dataset_header(self) -> None:
        ds = self._session.dataset
        if ds is None:
            self._dataset_label.setText(_NO_DATASET_TEXT)
        else:
            self._dataset_label.setText(ds.path.stem)

    def _channel_names(self) -> list[str]:
        ds = self._session.dataset
        if ds is None:
            return []
        return list(ds.metadata.get("channel_names", []))

    def _mask_names(self) -> list[str]:
        ds = self._session.dataset
        if ds is None:
            return []
        return list(ds.metadata.get("mask_names", []))

    def _seg_names(self) -> list[str]:
        ds = self._session.dataset
        if ds is None:
            return []
        return list(ds.metadata.get("segmentation_names", []))

    def _populate_combo(
        self, combo: QComboBox, items: list[str], current: str | None
    ) -> None:
        """Repopulate a combo without firing currentTextChanged echoes."""
        self._loading = True
        try:
            combo.clear()
            for name in items:
                combo.addItem(name)
            if current and current in items:
                combo.setCurrentText(current)
            elif items:
                # No active selection but list is non-empty — show first
                # without writing back to Session.
                combo.setCurrentIndex(0)
        finally:
            self._loading = False

    def _refresh_channel_combo(self) -> None:
        self._populate_combo(
            self._channel_combo, self._channel_names(), self._session.active_channel
        )

    def _refresh_mask_combo(self) -> None:
        self._populate_combo(
            self._mask_combo, self._mask_names(), self._session.active_mask
        )

    def _refresh_seg_combo(self) -> None:
        self._populate_combo(
            self._seg_combo, self._seg_names(), self._session.active_segmentation
        )

    def _refresh_bin_spin(self) -> None:
        """Sync the SpinBox to ``session.active_bin`` without firing the
        valueChanged echo (which would re-write Session)."""
        self._loading = True
        try:
            self._bin_spin.setValue(self._session.active_bin)
        finally:
            self._loading = False

    # ── Session event handlers ──────────────────────────────────────

    def _on_dataset_changed(self) -> None:
        self._refresh_all()

    def _on_active_channel_changed(self) -> None:
        if self._loading:
            return
        active = self._session.active_channel or ""
        if self._channel_combo.currentText() != active:
            self._loading = True
            try:
                self._channel_combo.setCurrentText(active)
            finally:
                self._loading = False

    def _on_active_mask_changed(self) -> None:
        if self._loading:
            return
        active = self._session.active_mask or ""
        if self._mask_combo.currentText() != active:
            self._loading = True
            try:
                self._mask_combo.setCurrentText(active)
            finally:
                self._loading = False

    def _on_active_segmentation_changed(self) -> None:
        if self._loading:
            return
        active = self._session.active_segmentation or ""
        if self._seg_combo.currentText() != active:
            self._loading = True
            try:
                self._seg_combo.setCurrentText(active)
            finally:
                self._loading = False

    def _on_active_bin_changed(self) -> None:
        if self._loading:
            return
        if self._bin_spin.value() != self._session.active_bin:
            self._refresh_bin_spin()

    # ── Combo change → Session write ────────────────────────────────

    def _on_channel_combo_changed(self, text: str) -> None:
        if self._loading:
            return
        self._session.set_active_channel(text or None)

    def _on_mask_combo_changed(self, text: str) -> None:
        if self._loading:
            return
        # CellDataModel wrapper emits state_changed; use it for parity with data_panel.
        self.data_model.set_active_mask(text or None)

    def _on_seg_combo_changed(self, text: str) -> None:
        if self._loading:
            return
        self.data_model.set_active_segmentation(text or None)

    def _on_bin_spin_changed(self, value: int) -> None:
        if self._loading:
            return
        self._session.set_active_bin(int(value))

    # ── Pin-on-top ──────────────────────────────────────────────────

    def _restore_pin_on_top(self) -> None:
        qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        raw = qs.value(_PIN_KEY, True)
        # QSettings stores booleans as strings on some backends.
        if isinstance(raw, str):
            pinned = raw.lower() in ("true", "1", "yes")
        else:
            pinned = bool(raw)
        self._apply_pin_on_top(pinned)
        # Reflect in checkbox without firing the toggle slot back.
        was_blocked = self._pin_check.blockSignals(True)
        self._pin_check.setChecked(pinned)
        self._pin_check.blockSignals(was_blocked)

    def _apply_pin_on_top(self, pinned: bool) -> None:
        # ``setWindowFlag`` hides the widget as a side effect when called
        # on a visible window, so we capture visibility BEFORE the flag
        # change and re-show after. Reading ``isVisible()`` after the call
        # always returns False and would silently drop the window for the
        # user every time they toggle the pin.
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, pinned)
        if was_visible:
            self.show()
            if pinned:
                # macOS doesn't always re-stack the window on flag change.
                # Force it forward so the toggle has a visible effect.
                self.raise_()

    def _on_pin_toggled(self, pinned: bool) -> None:
        self._apply_pin_on_top(pinned)
        QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).setValue(_PIN_KEY, pinned)

    # ── Geometry persistence ────────────────────────────────────────

    def _save_geometry(self) -> None:
        QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).setValue(
            _GEOMETRY_KEY, self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(_GEOMETRY_KEY)
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(_DEFAULT_WIDTH, _DEFAULT_HEIGHT)

    # ── Lifecycle ───────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        for unsub in getattr(self, "_unsubs", []):
            try:
                unsub()
            except ValueError:
                pass
        self._save_geometry()
        super().closeEvent(event)
