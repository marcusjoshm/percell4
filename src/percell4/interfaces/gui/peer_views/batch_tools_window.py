"""Dedicated Batch Tools window — a hide-on-close host for the batch console.

Mirrors the peer-view windows (``data_plot``, ``cell_table``): a top-level
``QMainWindow`` whose X button hides it rather than destroying it, so the hosted
``BatchConsolePanel`` — and any in-flight batch subprocess — survive across
hides, and window geometry persists via ``QSettings``. The launcher registers
this window in its ``_windows`` registry and reaches it through the same lazy
factory used for the viewer and the peer views.

Unlike the peer views, this window subscribes to nothing: the hosted panel reads
session-derived values (the open ``.h5`` path) on demand through injected
getters, so there is no Session/``CellDataModel`` subscription to tear down on
hide or rebuild on show — the deaf-after-reopen trap does not apply here.
"""

from __future__ import annotations

from collections.abc import Callable

from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QMainWindow, QWidget

from percell4.interfaces.gui.task_panels.batch_console_panel import BatchConsolePanel


class BatchToolsWindow(QMainWindow):
    """Top-level window hosting the Batch Tools console; hides on close."""

    def __init__(
        self,
        *,
        get_open_h5_path: Callable[[], str | None] = lambda: None,
        reload_open_dataset: Callable[[], None] = lambda: None,
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("PerCell4 — Batch Tools")
        self.resize(950, 680)

        self._panel = BatchConsolePanel(
            get_open_h5_path=get_open_h5_path,
            reload_open_dataset=reload_open_dataset,
            show_status=show_status,
        )
        self.setCentralWidget(self._panel)

        self._restore_geometry()

    @property
    def panel(self) -> BatchConsolePanel:
        """The hosted console panel (created in ``__init__``)."""
        return self._panel

    def closeEvent(self, event) -> None:
        """Hide (do not destroy) so state, subprocess, and geometry persist."""
        self._save_geometry()
        self.hide()
        event.ignore()

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "batch_tools/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("batch_tools/geometry")
        if geom:
            self.restoreGeometry(geom)
