"""Shared Cellpose-settings form — single source of truth for the eight
``CellposeSettings`` controls.

Both the single-cell workflow setup dialog
(``WorkflowConfigDialog._build_cellpose_group``) and the interactive Segment
tab (``SegmentationPanel``) need the same Model / Diameter / GPU / Flow /
Cellprob / Min-size / Saturation / Sigma controls. This widget owns that
construction once so the two surfaces cannot drift in items, defaults,
ranges, widget types, or tooltips.

The widget *is* a ``CellposeSettings`` editor: :meth:`__init__` seeds every
control from an ``initial`` value and :meth:`settings` reads them back into a
``CellposeSettings`` (letting the dataclass' ``__post_init__`` validate the
invariants). Surface-specific controls that the two callers do *not* share —
the segmentation-channel picker, layer-name field, and edge-mode/margin — stay
in each caller; they are not part of this form.

The construction below is ported verbatim from the workflow dialog's
``_build_cellpose_group`` (the seven original rows) plus the new ``Sigma`` row.
Keep them in lockstep.
"""

from __future__ import annotations

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QWidget,
)

from percell4.workflows.models import CellposeSettings

# Cellpose model identifiers, in display order. Single source of truth for
# both the workflow dialog and the Segment panel (config_dialog imports this).
CELLPOSE_MODELS = ("cpsam", "cyto3", "cyto2", "cyto", "nuclei")


class CellposeSettingsForm(QWidget):
    """Editor for the eight :class:`CellposeSettings` fields.

    No ``changed`` signal: both consumers read the widgets pull-style at
    run/accept time, so there is no live derived-state subscriber to notify.
    """

    def __init__(
        self,
        initial: CellposeSettings = CellposeSettings(),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._build_ui(initial)

    def _build_ui(self, initial: CellposeSettings) -> None:
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        self._model = QComboBox()
        self._model.addItems(CELLPOSE_MODELS)
        self._model.setCurrentText(initial.model)
        form.addRow("Model:", self._model)

        self._diameter = QDoubleSpinBox()
        self._diameter.setRange(0.0, 1000.0)
        self._diameter.setSingleStep(1.0)
        self._diameter.setValue(initial.diameter)
        self._diameter.setToolTip("0 = auto-detect")
        form.addRow("Diameter (px):", self._diameter)

        self._gpu = QCheckBox("Use GPU")
        self._gpu.setChecked(initial.gpu)
        form.addRow("", self._gpu)

        self._flow = QDoubleSpinBox()
        self._flow.setRange(0.0, 10.0)
        self._flow.setSingleStep(0.1)
        self._flow.setValue(initial.flow_threshold)
        form.addRow("Flow threshold:", self._flow)

        self._cellprob = QDoubleSpinBox()
        self._cellprob.setRange(-10.0, 10.0)
        self._cellprob.setSingleStep(0.1)
        self._cellprob.setValue(initial.cellprob_threshold)
        form.addRow("Cellprob threshold:", self._cellprob)

        self._min_size = QSpinBox()
        self._min_size.setRange(0, 100000)
        self._min_size.setValue(initial.min_size)
        form.addRow("Min cell size (px):", self._min_size)

        # ImageJ-style Enhance Contrast applied to the segmentation
        # channel before Cellpose runs. Same operation the seg-QC
        # Modify Channel group exposes interactively. 1.0% mirrors
        # the QC default; set to 0 to disable.
        self._saturation = QDoubleSpinBox()
        self._saturation.setRange(0.0, 50.0)
        self._saturation.setSingleStep(0.5)
        self._saturation.setDecimals(1)
        self._saturation.setValue(initial.saturation_pct)
        self._saturation.setSuffix(" %")
        self._saturation.setToolTip(
            "Saturation % applied as an ImageJ-style Enhance Contrast "
            "LUT to the segmentation channel before Cellpose runs. "
            "1% saturates the brightest 1% of pixels to dtype-max so "
            "Cellpose's percentile normalization isn't skewed by hot "
            "pixels or speck outliers. Set to 0 to disable. The "
            "on-disk /intensity is never modified."
        )
        form.addRow("Saturation:", self._saturation)

        # Gaussian blur applied to the segmentation channel after the
        # saturation LUT and before Cellpose runs. Smooths shot noise so
        # speckled channels segment as single cell bodies; set to 0 to
        # disable. Strictly a Cellpose-input preprocessor — the on-disk
        # /intensity is never modified.
        self._blur_sigma = QDoubleSpinBox()
        self._blur_sigma.setRange(0.0, 20.0)
        self._blur_sigma.setSingleStep(0.5)
        self._blur_sigma.setDecimals(1)
        self._blur_sigma.setValue(initial.blur_sigma)
        self._blur_sigma.setToolTip(
            "Gaussian blur sigma (standard deviation) applied to the "
            "segmentation channel after the saturation LUT and before "
            "Cellpose runs. Smooths shot noise so speckled channels segment "
            "as single cell bodies rather than fragmenting. Typical values "
            "are 0.5 to 3.0. Set to 0 to disable. The on-disk /intensity is "
            "never modified."
        )
        form.addRow("Blur (sigma):", self._blur_sigma)

    def settings(self) -> CellposeSettings:
        """Read the widgets into a validated :class:`CellposeSettings`."""
        return CellposeSettings(
            model=self._model.currentText(),
            diameter=self._diameter.value(),
            gpu=self._gpu.isChecked(),
            flow_threshold=self._flow.value(),
            cellprob_threshold=self._cellprob.value(),
            min_size=self._min_size.value(),
            saturation_pct=self._saturation.value(),
            blur_sigma=self._blur_sigma.value(),
        )
