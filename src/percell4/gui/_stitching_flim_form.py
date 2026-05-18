"""Shared TCSPC stitching / orientation / raw-``.bin`` geometry form.

Single source of truth for the widget set every TCSPC append flow uses to
collect ``TileConfig`` + rotation/flip + the raw-binary ``FlimConfig``
fields (``bin_x``, ``bin_y``, ``bin_t``, ``bin_dtype``, ``bin_dim_order``,
``bin_header_bytes``). The widget construction is ported verbatim from
``AddLayerDialog`` (single-dataset TCSPC tab) so the two flows stay
visually and behaviorally identical.

Out of scope here:

* The ``flim_freq`` widget — the single-dataset dialog uses it as the
  calibration source, while ``BatchTCSPCDialog`` reads frequency per
  dataset from the calibration CSV. Each caller owns its own frequency.
* Per-channel ``(phase, modulation)`` calibration widgets — same
  rationale as frequency: differs across the two flows.

Future work: migrate ``AddLayerDialog`` to consume this widget so both
dialogs share a single construction site. Tracked as follow-up after
PR #9.
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

from percell4.domain.io.models import FlimConfig, TileConfig


class StitchingFlimForm(QWidget):
    """Stitching grid + rotation/flip + raw ``.bin`` geometry form.

    Emits :attr:`changed` whenever any control's value changes so callers
    can invalidate downstream state (e.g. a Run button that requires
    re-validation).
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_change_signals()

    # ──────────────────────────────────────────────────────────────
    # Construction — every value below mirrors AddLayerDialog's TCSPC
    # tab (lines 845-975 at time of extraction). Keep them in lockstep.
    # ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # ── Stitching grid (Rows × Cols, Pattern, Start) ──
        stitch_row = QHBoxLayout()
        stitch_row.addWidget(QLabel("Rows:"))
        self.stitch_rows = QSpinBox()
        self.stitch_rows.setRange(1, 100)
        self.stitch_rows.setValue(1)
        stitch_row.addWidget(self.stitch_rows)
        stitch_row.addWidget(QLabel("Cols:"))
        self.stitch_cols = QSpinBox()
        self.stitch_cols.setRange(1, 100)
        self.stitch_cols.setValue(1)
        stitch_row.addWidget(self.stitch_cols)
        stitch_row.addWidget(QLabel("Pattern:"))
        self.stitch_type = QComboBox()
        self.stitch_type.addItems(
            ["row_by_row", "column_by_column", "snake_by_row", "snake_by_column"]
        )
        stitch_row.addWidget(self.stitch_type)
        stitch_row.addWidget(QLabel("Start:"))
        self.stitch_order = QComboBox()
        self.stitch_order.addItems(
            [
                "right_down", "right_up", "left_down", "left_up",
                "top_left", "top_right", "bottom_left", "bottom_right",
            ]
        )
        stitch_row.addWidget(self.stitch_order)
        stitch_row.addStretch()
        outer.addLayout(stitch_row)

        # ── Rotation + Flip (applies to /decay only; T-axis untouched) ──
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

        # NOTE: the per-import "Spatial bin factor" spinner previously here
        # was removed when spatial binning became a dataset-wide concept
        # (see docs/plans/2026-05-18-001-feat-dataset-wide-spatial-binning-plan.md U6).
        # Native resolution is now locked at compress time via
        # CompressConfig.creation_bin; post-creation imports must already
        # match the dataset's native_shape, which the use case validates
        # and surfaces as a LayerSizeMismatchError.

        # ── FLIM .bin Parameters (raw binary geometry) ──
        # Checkable: when unchecked, callers should treat the FLIM config
        # as defaulted (every numeric field zero, dtype/dim_order empty —
        # ``add_decay_to_dataset`` then falls back to 512/512/132/
        # uint16/YXT/0 internally, which matches the single-dataset flow).
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
        """Wire every control's edit signal to :attr:`changed`."""
        for spin in (
            self.stitch_rows,
            self.stitch_cols,
            self.bin_x,
            self.bin_y,
            self.bin_t,
            self.bin_header,
        ):
            spin.valueChanged.connect(self.changed.emit)
        for combo in (
            self.stitch_type,
            self.stitch_order,
            self.rotation_combo,
            self.flip_combo,
            self.bin_dtype,
            self.bin_dim_order,
        ):
            combo.currentIndexChanged.connect(self.changed.emit)
        self.flim_group.toggled.connect(lambda _checked: self.changed.emit())

    # ──────────────────────────────────────────────────────────────
    # Accessors — same call sites the single-dataset dialog uses, so
    # callers can switch between widget sources without rewriting reads.
    # ──────────────────────────────────────────────────────────────

    def tile_config(self) -> TileConfig:
        return TileConfig(
            grid_rows=self.stitch_rows.value(),
            grid_cols=self.stitch_cols.value(),
            grid_type=self.stitch_type.currentText(),
            order=self.stitch_order.currentText(),
        )

    def rotation_k(self) -> int:
        return int(self.rotation_combo.currentData() or 0)

    def flip_axis(self) -> int | None:
        """``None`` = no flip; ``0`` = vertical (np.flipud); ``1`` = horizontal (np.fliplr)."""
        data = self.flip_combo.currentData()
        if data is None or int(data) < 0:
            return None
        return int(data)

    def flim_config(self, *, frequency_mhz: float = 80.0) -> FlimConfig:
        """Build the ``FlimConfig`` from the raw-``.bin`` fields.

        ``frequency_mhz`` is supplied by the caller — this widget does not
        own laser-frequency UI (the batch flow reads it from the CSV; the
        single-dataset flow has its own ``flim_freq`` spinbox alongside
        this widget).

        When the FLIM group is unchecked, returns ``FlimConfig()`` defaults
        so ``add_decay_to_dataset`` falls back to the built-in geometry
        (matches the single-dataset flow's behavior).
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
