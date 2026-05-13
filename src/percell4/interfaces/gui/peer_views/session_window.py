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
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QWidget,
)

from percell4.application.session import Event
from percell4.model import CellDataModel

_QSETTINGS_ORG = "LeeLabPerCell4"
_QSETTINGS_APP = "PerCell4"
_GEOMETRY_KEY = "session_window/geometry"
_NO_DATASET_TEXT = "(no dataset)"
_DEFAULT_WIDTH = 720
_DEFAULT_HEIGHT = 80


class SessionWindow(QMainWindow):
    """Canonical Selector window for the three session active fields.

    Always-on-top is hardcoded; there is no toggle. The window is meant
    to be visible at all times so the user can pick what they're working
    on without tab-navigating away from whatever else they're doing.
    """

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self._session = data_model.session
        self._loading = False

        self.setWindowTitle("PerCell4 — Session")
        # Always-on-top is a permanent property of this window. Set the
        # flag here, before the window is ever shown, so no flag-change
        # side effects can hide it later.
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._build_ui()
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

        row.addStretch()

    # ── Combo population ────────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._refresh_dataset_header()
        self._refresh_channel_combo()
        self._refresh_mask_combo()
        self._refresh_seg_combo()

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
