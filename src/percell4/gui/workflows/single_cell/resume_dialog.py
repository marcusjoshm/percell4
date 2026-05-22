"""Per-dataset segmentation picker for the resume-from-segmented entry (U12).

A small modal dialog: given each pre-segmented dataset's available
``/labels`` resources, let the user choose which segmentation the workflow
should use per dataset, defaulting to the tracked layer when present. The
chosen picks seed ``SingleCellThresholdingRunner(segmentation_overrides=...)``.
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


class ResumeSegmentationDialog(QDialog):
    """Choose the segmentation to use per dataset (tracked-preferred default).

    ``per_dataset_labels`` maps dataset name → its available ``/labels``
    names. After ``exec()`` returns ``Accepted``, :attr:`picks` holds the
    chosen ``{dataset_name: segmentation_name}`` mapping.
    """

    def __init__(self, per_dataset_labels: dict[str, list[str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resume from segmented datasets")
        self._combos: dict[str, QComboBox] = {}

        defaults = default_segmentation_picks(per_dataset_labels)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Choose the segmentation to use for each dataset:")
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
