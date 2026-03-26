"""PerCell4 application entry point.

Creates the QApplication, CellDataModel, and LauncherWindow.
"""

from __future__ import annotations

import sys

from qtpy.QtWidgets import QApplication


def main() -> None:
    """Launch the PerCell4 GUI application."""
    app = QApplication.instance() or QApplication(sys.argv)

    from percell4.gui.launcher import LauncherWindow
    from percell4.model import CellDataModel

    data_model = CellDataModel()
    launcher = LauncherWindow(data_model)
    launcher.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
