"""Decay-orientation and raw-``.bin`` geometry forms.

The two non-stitching halves of the former ``StitchingFlimForm``, split out so
the stitching widget can be purely about tile layout and embedded on surfaces
that have no TCSPC concerns at all.

* :class:`RotateFlipForm` — rotate/flip applied to the already-stitched decay
  array (whole-image, ``/decay``-only; the T axis is untouched).
* :class:`FlimBinParamsForm` — the raw binary histogram geometry a ``.bin``
  export needs (``bin_x``, ``bin_y``, ``bin_t``, ``bin_dtype``,
  ``bin_dim_order``, ``bin_header_bytes``).

Widget construction, item order, and ``itemData`` carriers are ported verbatim
from ``_stitching_flim_form.py`` — the values below are pinned by
``tests/test_gui/test_batch_tcspc_dialog.py`` and are the canonical lists every
TCSPC surface must show. Keep them in lockstep.

Out of scope here, as before: ``flim_freq`` and the per-channel
``(phase, modulation)`` calibration widgets. Each caller owns its own frequency
(the batch flow reads it from the calibration CSV; the single-dataset flow has
its own spinbox).
"""

from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.io.models import FlimConfig


class RotateFlipForm(QWidget):
    """Rotation + flip applied to the stitched decay array.

    Emits :attr:`changed` on any edit so callers can invalidate downstream
    state (e.g. a Run button that requires re-validation).
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_change_signals()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotate stitched array:"))
        self.rotation_combo = QComboBox()
        self.rotation_combo.addItem("None", 0)
        self.rotation_combo.addItem("90° CCW", 1)
        self.rotation_combo.addItem("180°", 2)
        self.rotation_combo.addItem("90° CW", 3)
        rot_row.addWidget(self.rotation_combo)
        rot_row.addWidget(QLabel("Flip:"))
        self.flip_combo = QComboBox()
        # ``-1`` = no flip; ``0`` = vertical (top↔bottom, np.flipud);
        # ``1`` = horizontal (left↔right, np.fliplr).
        self.flip_combo.addItem("None", -1)
        self.flip_combo.addItem("Vertical (top ↔ bottom)", 0)
        self.flip_combo.addItem("Horizontal (left ↔ right)", 1)
        rot_row.addWidget(self.flip_combo)
        rot_row.addStretch()
        outer.addLayout(rot_row)

    def _connect_change_signals(self) -> None:
        for combo in (self.rotation_combo, self.flip_combo):
            # ``currentIndexChanged(int)`` carries an arg; discard it so the
            # 0-arg ``changed`` Signal never receives the index (strict under
            # PySide6 when the combo is actually driven).
            combo.currentIndexChanged.connect(lambda _i: self.changed.emit())

    def rotation_k(self) -> int:
        return int(self.rotation_combo.currentData() or 0)

    def flip_axis(self) -> int | None:
        """``None`` = no flip; ``0`` = vertical (np.flipud); ``1`` = horizontal (np.fliplr)."""
        data = self.flip_combo.currentData()
        if data is None or int(data) < 0:
            return None
        return int(data)


class FlimBinParamsForm(QWidget):
    """Raw binary TCSPC histogram (``.bin``) geometry.

    Checkable: when unchecked, :meth:`flim_config` returns defaults so
    ``add_decay_to_dataset`` falls back to its built-in
    512/512/132/uint16/YXT/0 geometry — matching the single-dataset flow.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_change_signals()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.flim_group = QGroupBox("FLIM .bin Parameters")
        self.flim_group.setCheckable(True)
        self.flim_group.setChecked(False)
        self.flim_group.setToolTip(
            "Parameters for raw binary TCSPC histogram (.bin) files.\n"
            "Leave unchecked to use built-in defaults; check and override "
            "when your .bin export uses a non-default dtype or header."
        )
        flim_layout = QFormLayout(self.flim_group)

        self.bin_x = QSpinBox()
        self.bin_x.setRange(1, 10000)
        self.bin_x.setValue(512)
        flim_layout.addRow("X dimension:", self.bin_x)

        self.bin_y = QSpinBox()
        self.bin_y.setRange(1, 10000)
        self.bin_y.setValue(512)
        flim_layout.addRow("Y dimension:", self.bin_y)

        self.bin_t = QSpinBox()
        self.bin_t.setRange(1, 4096)
        self.bin_t.setValue(132)
        flim_layout.addRow("Time bins:", self.bin_t)

        self.bin_dtype = QComboBox()
        self.bin_dtype.addItems(["uint32", "uint16", "float32", "uint8"])
        flim_layout.addRow("Data type:", self.bin_dtype)

        self.bin_dim_order = QComboBox()
        self.bin_dim_order.addItems(["YXT", "XYT", "TYX"])
        flim_layout.addRow("Dimension order:", self.bin_dim_order)

        self.bin_header = QSpinBox()
        self.bin_header.setRange(0, 10000)
        self.bin_header.setValue(0)
        self.bin_header.setSpecialValueText("Auto-detect")
        flim_layout.addRow("Header bytes:", self.bin_header)

        outer.addWidget(self.flim_group)

    def _connect_change_signals(self) -> None:
        for spin in (self.bin_x, self.bin_y, self.bin_t, self.bin_header):
            # ``valueChanged(int)`` carries an arg; discard it so the 0-arg
            # ``changed`` Signal never receives the value (strict in PySide6).
            spin.valueChanged.connect(lambda _v: self.changed.emit())
        for combo in (self.bin_dtype, self.bin_dim_order):
            combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.flim_group.toggled.connect(lambda _checked: self.changed.emit())

    def flim_config(self, *, frequency_mhz: float = 80.0) -> FlimConfig:
        """Build the ``FlimConfig`` from the raw-``.bin`` fields.

        ``frequency_mhz`` is supplied by the caller — this widget does not own
        laser-frequency UI (the batch flow reads it from the CSV; the
        single-dataset flow has its own ``flim_freq`` spinbox alongside this
        widget).
        """
        if not self.flim_group.isChecked():
            return FlimConfig(frequency_mhz=frequency_mhz)
        return FlimConfig(
            frequency_mhz=frequency_mhz,
            bin_x=self.bin_x.value(),
            bin_y=self.bin_y.value(),
            bin_t=self.bin_t.value(),
            bin_dtype=self.bin_dtype.currentText(),
            bin_dim_order=self.bin_dim_order.currentText(),
            bin_header_bytes=self.bin_header.value(),
        )
