"""Per-dataset segmentation selector for already-segmented datasets (U12).

When datasets added to the workflow already carry segmentation (and tracking)
— e.g. produced overnight by the standalone ``percell4-batch`` CLI — the
workflow setup recognizes that and starts them at the grouped-threshold step
(cellpose and tracking are skipped). This small modal lets the user choose
*which* ``/labels`` resource each such dataset should use when more than one
exists, defaulting to the tracked layer. The chosen picks seed
``SingleCellThresholdingRunner(segmentation_overrides=...)``.

This is not a "resume" of a prior run — these datasets enter the workflow
fresh; they simply skip the phases whose work is already on disk.
"""

from __future__ import annotations

from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from percell4.workflows.phases import default_segmentation_picks


class SegmentationSelectDialog(QDialog):
    """Choose the segmentation to use per already-segmented dataset.

    ``per_dataset_labels`` maps dataset name → its available ``/labels``
    names. After ``exec()`` returns ``Accepted``, :attr:`picks` holds the
    chosen ``{dataset_name: segmentation_name}`` mapping (tracked-preferred
    defaults).
    """

    def __init__(self, per_dataset_labels: dict[str, list[str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select segmentation for pre-processed datasets")
        self._combos: dict[str, QComboBox] = {}

        defaults = default_segmentation_picks(per_dataset_labels)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "These datasets are already segmented. Choose the "
                "segmentation to use for each (the workflow will start at "
                "grouped thresholding):"
            )
        )
        form = QFormLayout()
        for name, labels in per_dataset_labels.items():
            combo = QComboBox()
            combo.addItems(list(labels))
            default = defaults.get(name)
            if default in labels:
                combo.setCurrentText(default)
            self._combos[name] = combo
            form.addRow(name, combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def picks(self) -> dict[str, str]:
        """The chosen ``{dataset_name: segmentation_name}`` mapping."""
        return {
            name: combo.currentText()
            for name, combo in self._combos.items()
            if combo.currentText()
        }
