"""The canonical tile-stitching form.

One widget, used by every GUI surface that stitches tiles. Replaces four
independently-built control sets that had drifted apart.

Layout is a ``QGridLayout`` (label, field, label, field) inside a
``QGroupBox`` rather than the single wide ``QHBoxLayout`` the old surfaces
used — that row held eight widgets and overflowed its host dialog into a
horizontal scrollbar.

Presentation follows the Fiji *Grid/Collection Stitching* plugin: ``Type`` and
``Order``, with the Order options keyed to the selected Type. The labels are
Fiji's; the values handed to ``TileConfig`` are PerCell4's existing canonical
strings, carried in ``itemData``. See ``_stitch_order.py`` for why the two are
separable.

Per-surface variation is expressed with constructor flags rather than
subclasses, so there stays exactly one construction site.
"""

from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.io.models import TileConfig
from percell4.gui._stitch_order import (
    GRID_TYPE_LABELS,
    GRID_TYPES,
    normalize_order,
    order_labels_for,
)


class StitchingForm(QWidget):
    """Grid size + Type/Order, optionally registration and fusion.

    Emits :attr:`changed` on any edit so callers can invalidate downstream
    state (e.g. a Run button that requires re-validation).
    """

    changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        show_registration: bool = True,
        show_fusion: bool = False,
        title: str = "Tile Stitching",
    ) -> None:
        super().__init__(parent)
        self._show_registration = show_registration
        self._show_fusion = show_fusion
        # Hosts that already label the section with their own enable-checkbox
        # pass ``title=""`` so the words are not shown twice.
        self._title = title
        self._build_ui()
        self._connect_change_signals()
        # Seed the Order combo for the initial Type. Done after wiring so the
        # combo is populated, but signals are blocked inside so this does not
        # count as a user edit.
        self._repopulate_order(emit=False)

    # ── Construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.group = QGroupBox(self._title)
        if not self._title:
            self.group.setFlat(True)
        grid = QGridLayout(self.group)

        # Row 0: Grid size X | Type
        # X is the COLUMN count and Y is the ROW count — matching Fiji's own
        # grid_size_x / grid_size_y, and the assembler, where the canvas is
        # grid_rows·tile_h by grid_cols·tile_w. Swapping them transposes the
        # mosaic and, on the registered path, changes native_shape.
        grid.addWidget(QLabel("Grid size X:"), 0, 0)
        self.grid_x = QSpinBox()
        self.grid_x.setRange(1, 100)
        self.grid_x.setValue(1)
        self.grid_x.setToolTip("Number of tile columns (the horizontal extent of the grid).")
        grid.addWidget(self.grid_x, 0, 1)

        grid.addWidget(QLabel("Type:"), 0, 2)
        self.grid_type = QComboBox()
        for value in GRID_TYPES:
            self.grid_type.addItem(GRID_TYPE_LABELS[value], value)
        self.grid_type.setToolTip(
            "Acquisition scan pattern.\n"
            "row-by-row: fill a row, jump back, fill the next.\n"
            "snake-by-row: fill a row, then reverse along the next.\n"
            "The column variants do the same walking down columns first."
        )
        grid.addWidget(self.grid_type, 0, 3)

        # Row 1: Grid size Y | Order
        grid.addWidget(QLabel("Grid size Y:"), 1, 0)
        self.grid_y = QSpinBox()
        self.grid_y.setRange(1, 100)
        self.grid_y.setValue(1)
        self.grid_y.setToolTip("Number of tile rows (the vertical extent of the grid).")
        grid.addWidget(self.grid_y, 1, 1)

        grid.addWidget(QLabel("Order:"), 1, 2)
        self.order = QComboBox()
        self.order.setToolTip(
            "Which corner the first tile came from, and the travel directions.\n"
            "For a row Type the first word is the direction along a row and the\n"
            "second is how rows advance; for a column Type the first word runs\n"
            "down a column and the second is how columns advance.\n\n"
            "Note: this is not applied when 'Register overlapping tiles' is on —\n"
            "registration always seeds from the top-left corner."
        )
        grid.addWidget(self.order, 1, 3)

        # Row 2: Overlap | Register  (registration surfaces only)
        self.overlap_label = QLabel("Overlap:")
        grid.addWidget(self.overlap_label, 2, 0)
        # Overlap is stored as a FRACTION in TileConfig; the spinbox shows a
        # percentage. Register opts into the phase-correlation path, gated at
        # the importer on register ∧ overlap>0 ∧ grid>1×1.
        self.overlap = QDoubleSpinBox()
        self.overlap.setRange(0.0, 99.0)
        self.overlap.setSuffix("%")
        self.overlap.setValue(0.0)
        grid.addWidget(self.overlap, 2, 1)

        self.register_check = QCheckBox("Register overlapping tiles (phase correlation)")
        grid.addWidget(self.register_check, 2, 2, 1, 2)

        # Row 3: Reference | Fusion
        self.reference_label = QLabel("Reference:")
        grid.addWidget(self.reference_label, 3, 0)
        # Reference channel is identified by NAME (stable), not index.
        # Editable so a caller without a channel list at config time can type one.
        self.reference = QComboBox()
        self.reference.setEditable(True)
        grid.addWidget(self.reference, 3, 1)

        self.fusion_label = QLabel("Fusion:")
        grid.addWidget(self.fusion_label, 3, 2)
        self.fusion = QComboBox()
        self.fusion.addItem("None", "none")
        self.fusion.addItem("Linear Blending", "linear_blending")
        self.fusion.setToolTip(
            "How overlapping pixels combine.\n"
            "None: a single tile wins — the only measurement-correct choice, and\n"
            "forced for datasets carrying FLIM decay so /intensity and /decay\n"
            "resolve every overlap pixel to the same tile.\n"
            "Linear Blending: feathered blend; seamless for display, but it\n"
            "alters overlap intensities."
        )
        grid.addWidget(self.fusion, 3, 3)

        grid.setColumnStretch(4, 1)
        outer.addWidget(self.group)

        for widget in (self.overlap_label, self.overlap, self.register_check,
                       self.reference_label, self.reference):
            widget.setVisible(self._show_registration)
        for widget in (self.fusion_label, self.fusion):
            widget.setVisible(self._show_fusion)

    def _connect_change_signals(self) -> None:
        # Arg-discarding lambdas: ``changed`` is a 0-arg Signal and PySide6 is
        # strict about receiving an extra value.
        for spin in (self.grid_x, self.grid_y):
            spin.valueChanged.connect(lambda _v: self.changed.emit())
        self.overlap.valueChanged.connect(lambda _v: self.changed.emit())
        for combo in (self.order, self.reference, self.fusion):
            combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        # The reference combo is editable — free-text edits count too.
        self.reference.editTextChanged.connect(lambda _t: self.changed.emit())
        self.register_check.toggled.connect(lambda _c: self.changed.emit())
        # Type drives the Order repopulation, which emits ``changed`` itself
        # when the effective value moves — so it is wired separately and does
        # NOT also emit here, or a Type change would fire twice.
        self.grid_type.currentIndexChanged.connect(self._on_grid_type_changed)

    # ── Type → Order dependency ─────────────────────────────────────

    def _on_grid_type_changed(self, _index: int) -> None:
        # A Type change is itself a user edit even when the Order value is
        # unaffected, so always announce it; _repopulate_order stays silent and
        # this one emission covers both.
        self._repopulate_order(emit=False)
        self.changed.emit()

    def _repopulate_order(self, *, emit: bool) -> None:
        """Swap the Order label set to match the selected Type.

        The user's pick survives: values are Type-independent corners, so the
        previous value is re-selected under its new wording.
        """
        previous = self.order.currentData()
        grid_type = self.grid_type.currentData()

        self.order.blockSignals(True)
        self.order.clear()
        for label, value in order_labels_for(grid_type):
            self.order.addItem(label, value)
        idx = self.order.findData(previous) if previous is not None else -1
        self.order.setCurrentIndex(idx if idx >= 0 else 0)
        self.order.blockSignals(False)

        if emit and self.order.currentData() != previous:
            self.changed.emit()

    # ── Population ──────────────────────────────────────────────────

    def set_reference_channels(
        self, names: list[str], *, preserve: str = "text"
    ) -> None:
        """Populate the reference-channel combo from discovered channels.

        Each name is carried verbatim as ``itemData`` so reads round-trip the
        name, not an index.

        ``preserve`` decides what survives the repopulation. ``"text"`` keeps
        the same channel *name* — right when the list is re-discovered.
        ``"index"`` keeps the same *position*, which is what a rename needs:
        renaming ``ch00`` to ``ER`` must leave the reference pointing at that
        same channel rather than falling back to the first.
        """
        if preserve not in ("text", "index"):
            raise ValueError(f"preserve must be 'text' or 'index', got {preserve!r}")
        current_text = self.reference.currentText()
        current_index = self.reference.currentIndex()

        self.reference.blockSignals(True)
        self.reference.clear()
        for name in names:
            self.reference.addItem(name, name)

        if preserve == "index":
            if 0 <= current_index < self.reference.count():
                self.reference.setCurrentIndex(current_index)
        elif current_text:
            idx = self.reference.findText(current_text)
            if idx >= 0:
                self.reference.setCurrentIndex(idx)
            else:
                self.reference.setCurrentText(current_text)
        self.reference.blockSignals(False)

    def set_tile_config(self, config: TileConfig) -> None:
        """Seed every control from a ``TileConfig``.

        ``config.order`` may use either accepted vocabulary — persisted state
        carries both — so it is normalized onto its canonical corner first.
        """
        self.grid_x.setValue(config.grid_cols)
        self.grid_y.setValue(config.grid_rows)

        idx = self.grid_type.findData(config.grid_type)
        if idx >= 0:
            self.grid_type.setCurrentIndex(idx)

        try:
            corner = normalize_order(config.order)
        except ValueError:
            corner = None
        if corner is not None:
            idx = self.order.findData(corner)
            if idx >= 0:
                self.order.setCurrentIndex(idx)

        self.overlap.setValue(config.overlap * 100.0)
        self.register_check.setChecked(config.register)
        if config.reference_channel:
            self.reference.setCurrentText(config.reference_channel)
        idx = self.fusion.findData(config.fusion_method)
        if idx >= 0:
            self.fusion.setCurrentIndex(idx)

    # ── Accessors ───────────────────────────────────────────────────

    def tile_config(self) -> TileConfig:
        """Build the ``TileConfig`` this form describes.

        Callers decide what an unchecked "Tile Stitching" checkbox means —
        some surfaces pass ``None`` downstream, others a 1×1 config. That
        divergence is deliberate and is not resolved here.
        """
        ref = self.reference.currentText().strip()
        return TileConfig(
            grid_rows=self.grid_y.value(),
            grid_cols=self.grid_x.value(),
            grid_type=self.grid_type.currentData(),
            order=self.order.currentData(),
            # Spinbox shows a percentage; TileConfig stores a fraction.
            overlap=(self.overlap.value() / 100.0) if self._show_registration else 0.0,
            register=self.register_check.isChecked() if self._show_registration else False,
            reference_channel=(ref or None) if self._show_registration else None,
            fusion_method=self.fusion.currentData() if self._show_fusion else "none",
        )
