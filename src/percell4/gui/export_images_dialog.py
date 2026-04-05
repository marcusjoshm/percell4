"""Export Images dialog for saving dataset layers as TIFF files."""

from __future__ import annotations

from pathlib import Path

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class ExportImagesDialog(QDialog):
    """Dialog for selecting which layers to export as TIFF images.

    Shows grouped checkboxes for channels, segmentations, and masks.
    User picks an output folder and clicks Export.
    """

    def __init__(self, parent, store) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Images")
        self.setMinimumWidth(450)
        self.resize(500, 500)

        self._store = store
        self._channel_checks: list[tuple[QCheckBox, str, int]] = []
        self._label_checks: list[tuple[QCheckBox, str]] = []
        self._mask_checks: list[tuple[QCheckBox, str]] = []

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Output folder
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Output folder:"))
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("Select output folder...")
        self._folder_edit.setReadOnly(True)
        folder_row.addWidget(self._folder_edit, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse)
        folder_row.addWidget(btn_browse)
        layout.addLayout(folder_row)

        # Read available layers from store
        with self._store.open_read() as s:
            meta = s.metadata
            channel_names = list(meta.get("channel_names", []))
            try:
                intensity = s.read_array("intensity")
                n_channels = intensity.shape[0] if intensity.ndim == 3 else 1
            except KeyError:
                n_channels = 0
            label_names = s.list_labels()
            mask_names = s.list_masks()

        # Channels group
        if n_channels > 0:
            ch_group = QGroupBox("Channels")
            ch_layout = QVBoxLayout(ch_group)
            ch_all = QCheckBox("Select All")
            ch_all.setChecked(True)
            ch_all.toggled.connect(lambda c: self._toggle_group(self._channel_checks, c))
            ch_layout.addWidget(ch_all)
            for i in range(n_channels):
                name = channel_names[i] if i < len(channel_names) else f"ch{i}"
                cb = QCheckBox(name)
                cb.setChecked(True)
                self._channel_checks.append((cb, name, i))
                ch_layout.addWidget(cb)
            layout.addWidget(ch_group)

        # Segmentations group
        if label_names:
            seg_group = QGroupBox("Segmentations")
            seg_layout = QVBoxLayout(seg_group)
            seg_all = QCheckBox("Select All")
            seg_all.setChecked(True)
            seg_all.toggled.connect(lambda c: self._toggle_group(self._label_checks, c))
            seg_layout.addWidget(seg_all)
            for name in label_names:
                cb = QCheckBox(name)
                cb.setChecked(True)
                self._label_checks.append((cb, name))
                seg_layout.addWidget(cb)
            layout.addWidget(seg_group)

        # Masks group
        if mask_names:
            mask_group = QGroupBox("Masks")
            mask_layout = QVBoxLayout(mask_group)
            mask_all = QCheckBox("Select All")
            mask_all.setChecked(True)
            mask_all.toggled.connect(lambda c: self._toggle_group(self._mask_checks, c))
            mask_layout.addWidget(mask_all)
            for name in mask_names:
                cb = QCheckBox(name)
                cb.setChecked(True)
                self._mask_checks.append((cb, name))
                mask_layout.addWidget(cb)
            layout.addWidget(mask_group)

        # Format (extensible — TIFF only for now)
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        self._format_combo.addItems(["TIFF"])
        fmt_row.addWidget(self._format_combo)
        fmt_row.addStretch()
        layout.addLayout(fmt_row)

        layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_export = QPushButton("Export")
        self._btn_export.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_export)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            self._folder_edit.setText(path)

    def _toggle_group(self, checks, checked: bool) -> None:
        for item in checks:
            item[0].setChecked(checked)

    # ── Results ──

    @property
    def output_folder(self) -> Path | None:
        text = self._folder_edit.text().strip()
        return Path(text) if text else None

    @property
    def selected_channels(self) -> list[tuple[str, int]]:
        """Return list of (name, index) for checked channels."""
        return [
            (name, idx)
            for cb, name, idx in self._channel_checks
            if cb.isChecked()
        ]

    @property
    def selected_labels(self) -> list[str]:
        return [name for cb, name in self._label_checks if cb.isChecked()]

    @property
    def selected_masks(self) -> list[str]:
        return [name for cb, name in self._mask_checks if cb.isChecked()]

    @property
    def export_format(self) -> str:
        return self._format_combo.currentText()
