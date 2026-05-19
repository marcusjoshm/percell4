"""Export Images dialog for saving dataset layers as TIFF files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

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
    QWidget,
)

from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll

if TYPE_CHECKING:
    from percell4.application.session import Session

logger = logging.getLogger(__name__)


class ExportImagesDialog(QDialog):
    """Dialog for selecting which layers to export as TIFF images.

    Shows grouped checkboxes for channels, segmentations, and masks.
    User picks an output folder and clicks Export.
    """

    def __init__(
        self,
        parent,
        store,
        session: "Session | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Images")
        self.setMinimumWidth(450)
        self.resize(500, 500)
        cap_to_screen(self)

        self._store = store
        self._session = session
        self._channel_checks: list[tuple[QCheckBox, str, int]] = []
        self._label_checks: list[tuple[QCheckBox, str]] = []
        self._mask_checks: list[tuple[QCheckBox, str]] = []

        # Subscription handle for Event.ACTIVE_BIN_CHANGED; cleared
        # in closeEvent / accept / reject so the dialog never leaks
        # a callback past its lifetime.
        self._bin_unsubscribe: callable | None = None

        self._build_ui()
        self._subscribe_session_events()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        content = QWidget()
        layout = QVBoxLayout(content)
        outer.addWidget(wrap_in_scroll(content))

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

        # View-bin opt-in. The label includes the live session bin so
        # the user knows at a glance what k they'd be exporting at.
        # Disabled when session.active_bin == 1 (no effect) or when
        # no session was supplied (e.g. older callers).
        self._view_bin_check = QCheckBox(self._compose_bin_label())
        self._view_bin_check.setChecked(False)
        self._refresh_view_bin_widget()
        layout.addWidget(self._view_bin_check)

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

    # ── View-bin opt-in helpers ──

    def _current_active_bin(self) -> int:
        """Read session.active_bin live; default 1 when no session."""
        if self._session is None:
            return 1
        try:
            return int(self._session.active_bin)
        except Exception:
            return 1

    def _compose_bin_label(self) -> str:
        """Checkbox label reflects the current view bin so the user
        sees at-a-glance what they'd be exporting at."""
        k = self._current_active_bin()
        if k <= 1:
            return "Apply current view bin (k=1) to exports — no effect at native"
        return f"Apply current view bin (k={k}) to exports"

    def _refresh_view_bin_widget(self) -> None:
        """Update label + enabled state from session.active_bin.
        Disabled when k==1 (the option is a no-op there)."""
        if not hasattr(self, "_view_bin_check"):
            return
        k = self._current_active_bin()
        self._view_bin_check.setText(self._compose_bin_label())
        self._view_bin_check.setEnabled(k > 1)
        if k <= 1:
            # If the bin drops back to 1 while the dialog is open
            # with the box checked, auto-uncheck so the resolved
            # value reflects the disabled state.
            self._view_bin_check.setChecked(False)

    def _subscribe_session_events(self) -> None:
        """Wire ACTIVE_BIN_CHANGED so the label updates live if the
        user changes the SessionWindow spinbox while the dialog is
        open. Stores the unsubscribe handle for teardown."""
        if self._session is None:
            return
        try:
            from percell4.application.session import Event

            self._bin_unsubscribe = self._session.subscribe(
                Event.ACTIVE_BIN_CHANGED, self._refresh_view_bin_widget,
            )
        except Exception:
            logger.exception(
                "export dialog: failed to subscribe to ACTIVE_BIN_CHANGED"
            )

    def _unsubscribe_session_events(self) -> None:
        """Idempotent teardown — safe to call from multiple
        accept/reject/closeEvent paths."""
        if self._bin_unsubscribe is None:
            return
        try:
            self._bin_unsubscribe()
        except Exception:
            logger.exception(
                "export dialog: failed to unsubscribe from session"
            )
        finally:
            self._bin_unsubscribe = None

    def accept(self) -> None:
        self._unsubscribe_session_events()
        super().accept()

    def reject(self) -> None:
        self._unsubscribe_session_events()
        super().reject()

    def closeEvent(self, event) -> None:
        self._unsubscribe_session_events()
        super().closeEvent(event)

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

    def selected_view_bin(self) -> int:
        """Return the bin value the user opted in to, or 1 when the
        checkbox is unchecked / disabled / no session was supplied.

        Reads ``session.active_bin`` LIVE (not at dialog construct
        time) so the export uses whatever value is current at Export
        click. Matches the established "active_bin is live" semantic
        per phasor-view-bin-not-forwarded-from-gui-callers.
        """
        if not hasattr(self, "_view_bin_check"):
            return 1
        if not self._view_bin_check.isChecked():
            return 1
        return self._current_active_bin()
