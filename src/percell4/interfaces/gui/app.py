"""Composition root for the hex architecture GUI.

Creates all infrastructure, wires dependencies, and starts the app.
This is a SEPARATE entry point from the existing app.py — the old
launcher continues to work alongside this one during migration.
"""

from __future__ import annotations

import sys

from qtpy.QtWidgets import QApplication

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.adapters.napari_viewer import NapariViewerAdapter
from percell4.application.session import Session
from percell4.application.use_cases.load_dataset import LoadDataset
from percell4.interfaces.gui.main_window import HexMainWindow


def main() -> int:
    qt_app = QApplication.instance() or QApplication(sys.argv)

    from percell4.gui.theme import apply_theme
    apply_theme(qt_app)

    # --- infrastructure (driven adapters) ---
    repo = Hdf5DatasetRepository()

    # --- application ---
    session = Session()

    # --- napari viewer (reuses existing ViewerWindow) ---
    # Import here to keep napari out of the module-level scope.
    # The old CellDataModel is needed by ViewerWindow for now —
    # it will be replaced by Session subscriptions in Stage 3.
    from percell4.gui.viewer import ViewerWindow
    from percell4.model import CellDataModel

    # Temporary bridge: ViewerWindow still needs a CellDataModel.
    # This will go away when ViewerWindow is migrated to subscribe
    # to Session directly.
    _bridge_model = CellDataModel()
    viewer_window = ViewerWindow(_bridge_model)
    viewer = NapariViewerAdapter(viewer_window)

    # --- use cases ---
    load_dataset = LoadDataset(repo, viewer, session)

    # --- main window ---
    window = HexMainWindow(load_dataset=load_dataset)
    window.show()

    return qt_app.exec_()


if __name__ == "__main__":
    sys.exit(main())
