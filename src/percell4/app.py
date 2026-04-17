"""PerCell4 application entry point.

Creates the QApplication, CellDataModel, and LauncherWindow.
"""

from __future__ import annotations

import sys

from qtpy.QtWidgets import QApplication


def main() -> None:
    """Launch the PerCell4 GUI application."""
    app = QApplication.instance() or QApplication(sys.argv)

    from percell4.gui.theme import apply_theme

    apply_theme(app)

    from percell4.application.session import Session
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    session = Session()
    data_model = CellDataModel(session)
    launcher = LauncherWindow(data_model)
    launcher.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
