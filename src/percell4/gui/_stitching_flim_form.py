"""Shared TCSPC stitching / orientation / raw-``.bin`` geometry form.

Single source of truth for the widget set every TCSPC append flow uses to
collect ``TileConfig`` + rotation/flip + the raw-binary ``FlimConfig``
fields.

As of the canonical-stitching-form refactor this class is a **composite**:
the rotate/flip pair and the raw-``.bin`` geometry group live in
``_flim_bin_form.py`` (:class:`RotateFlipForm`, :class:`FlimBinParamsForm`),
and are embedded here. The public surface — every widget attribute and every
accessor — is unchanged, so callers and tests do not care that the internals
moved.

The split exists so the stitching controls can be embedded on surfaces with no
TCSPC concerns. This class remains until those surfaces are migrated onto the
canonical ``StitchingForm``.

Out of scope here:

* The ``flim_freq`` widget — the single-dataset dialog uses it as the
  calibration source, while ``BatchTCSPCDialog`` reads frequency per
  dataset from the calibration CSV. Each caller owns its own frequency.
* Per-channel ``(phase, modulation)`` calibration widgets — same
  rationale as frequency: differs across the two flows.
"""

from __future__ import annotations

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

from percell4.domain.io.models import FlimConfig, TileConfig
from percell4.gui._flim_bin_form import FlimBinParamsForm, RotateFlipForm


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
    # tab. Keep them in lockstep.
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
        # Value rides in itemData, never the display text or the index — the
        # PR #9 drift precedent, and what lets the label change later without
        # breaking TileConfig construction. Label == value for now.
        for _gt in ("row_by_row", "column_by_column", "snake_by_row", "snake_by_column"):
            self.stitch_type.addItem(_gt, _gt)
        stitch_row.addWidget(self.stitch_type)
        stitch_row.addWidget(QLabel("Start:"))
        self.stitch_order = QComboBox()
        for _o in (
            "right_down", "right_up", "left_down", "left_up",
            "top_left", "top_right", "bottom_left", "bottom_right",
        ):
            self.stitch_order.addItem(_o, _o)
        stitch_row.addWidget(self.stitch_order)
        stitch_row.addStretch()
        outer.addLayout(stitch_row)

        # ── Overlap-aware registration (phase-correlation) ──
        # Overlap is stored as a FRACTION in TileConfig; the spinbox shows
        # a percentage. Register opts into the phase-correlation path
        # (gated at the importer on register ∧ overlap>0 ∧ grid>1×1).
        # Reference channel is identified by NAME (stable), not index —
        # populated by ``set_reference_channels`` from the caller's
        # discovered channel list, and editable so a caller without a
        # channel list at config time can still type a name.
        reg_row = QHBoxLayout()
        reg_row.addWidget(QLabel("Overlap:"))
        self.overlap_spin = QDoubleSpinBox()
        self.overlap_spin.setRange(0.0, 99.0)
        self.overlap_spin.setSuffix("%")
        self.overlap_spin.setValue(0.0)
        reg_row.addWidget(self.overlap_spin)
        self.register_check = QCheckBox(
            "Register overlapping tiles (phase correlation)"
        )
        reg_row.addWidget(self.register_check)
        reg_row.addWidget(QLabel("Reference channel:"))
        self.reference_combo = QComboBox()
        self.reference_combo.setEditable(True)
        reg_row.addWidget(self.reference_combo)
        reg_row.addStretch()
        outer.addLayout(reg_row)

        # ── Rotation + Flip (applies to /decay only; T-axis untouched) ──
        self._rotate_flip = RotateFlipForm()
        outer.addWidget(self._rotate_flip)
        # Re-exposed so callers and tests keep reaching the widgets directly.
        self.rotation_combo = self._rotate_flip.rotation_combo
        self.flip_combo = self._rotate_flip.flip_combo

        # NOTE: the per-import "Spatial bin factor" spinner previously here
        # was removed when spatial binning became a dataset-wide concept
        # (see docs/plans/2026-05-18-001-feat-dataset-wide-spatial-binning-plan.md U6).
        # Native resolution is now locked at compress time via
        # CompressConfig.creation_bin; post-creation imports must already
        # match the dataset's native_shape, which the use case validates
        # and surfaces as a LayerSizeMismatchError.

        # ── FLIM .bin Parameters (raw binary geometry) ──
        self._flim_bin = FlimBinParamsForm()
        outer.addWidget(self._flim_bin)
        self.flim_group = self._flim_bin.flim_group
        self.bin_x = self._flim_bin.bin_x
        self.bin_y = self._flim_bin.bin_y
        self.bin_t = self._flim_bin.bin_t
        self.bin_dtype = self._flim_bin.bin_dtype
        self.bin_dim_order = self._flim_bin.bin_dim_order
        self.bin_header = self._flim_bin.bin_header

    def _connect_change_signals(self) -> None:
        """Wire every control's edit signal to :attr:`changed`."""
        for spin in (self.stitch_rows, self.stitch_cols):
            # ``valueChanged(int)`` carries an arg; discard it so the 0-arg
            # ``changed`` Signal never receives the value (strict in PySide6).
            spin.valueChanged.connect(lambda _v: self.changed.emit())
        # ``valueChanged(float)`` carries an arg; the existing int spins
        # above rely on Qt swallowing it, but the editable-combo text
        # signal below is strict under PySide6 — wrap both in arg-discarding
        # lambdas so ``changed`` (a 0-arg Signal) never receives a value.
        self.overlap_spin.valueChanged.connect(lambda _v: self.changed.emit())
        for combo in (self.stitch_type, self.stitch_order, self.reference_combo):
            # ``currentIndexChanged(int)`` carries an arg; discard it so the
            # 0-arg ``changed`` Signal never receives the index (strict under
            # PySide6 when the combo is actually driven).
            combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        # The reference combo is editable — free-text edits also count.
        self.reference_combo.editTextChanged.connect(
            lambda _text: self.changed.emit()
        )
        self.register_check.toggled.connect(lambda _checked: self.changed.emit())
        # Re-emit the embedded widgets' edits as our own so a caller wiring
        # only ``StitchingFlimForm.changed`` still sees every edit exactly once.
        self._rotate_flip.changed.connect(self.changed.emit)
        self._flim_bin.changed.connect(self.changed.emit)

    def set_reference_channels(self, names: list[str]) -> None:
        """Populate the reference-channel combo from discovered channels.

        Each name is carried verbatim as the item's ``itemData`` (not an
        enum position) so reads round-trip the name, not an index — the
        PR #9 drift precedent. Preserves the current text when possible so
        a re-discovery does not silently drop a user's pick.
        """
        current = self.reference_combo.currentText()
        self.reference_combo.blockSignals(True)
        self.reference_combo.clear()
        for name in names:
            self.reference_combo.addItem(name, name)
        if current:
            idx = self.reference_combo.findText(current)
            if idx >= 0:
                self.reference_combo.setCurrentIndex(idx)
            else:
                self.reference_combo.setCurrentText(current)
        self.reference_combo.blockSignals(False)

    # ──────────────────────────────────────────────────────────────
    # Accessors — same call sites the single-dataset dialog uses, so
    # callers can switch between widget sources without rewriting reads.
    # ──────────────────────────────────────────────────────────────

    def tile_config(self) -> TileConfig:
        ref = self.reference_combo.currentText().strip()
        return TileConfig(
            grid_rows=self.stitch_rows.value(),
            grid_cols=self.stitch_cols.value(),
            grid_type=self.stitch_type.currentData(),
            order=self.stitch_order.currentData(),
            # Spinbox shows a percentage; TileConfig stores a fraction.
            overlap=self.overlap_spin.value() / 100.0,
            register=self.register_check.isChecked(),
            reference_channel=ref or None,
        )

    def rotation_k(self) -> int:
        return self._rotate_flip.rotation_k()

    def flip_axis(self) -> int | None:
        """``None`` = no flip; ``0`` = vertical (np.flipud); ``1`` = horizontal (np.fliplr)."""
        return self._rotate_flip.flip_axis()

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
        return self._flim_bin.flim_config(frequency_mhz=frequency_mhz)
