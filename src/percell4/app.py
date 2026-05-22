"""PerCell4 application entry point.

Creates the QApplication, CellDataModel, and LauncherWindow.
"""

from __future__ import annotations

import sys

import percell4._compat  # noqa: F401 — NumPy 2.0 shims for dtcwt

from qtpy.QtCore import Qt
from qtpy.QtGui import QIcon, QPixmap
from qtpy.QtWidgets import QApplication, QSplashScreen


def _make_splash() -> QSplashScreen:
    """Build the startup splash screen showing the PerCell4 logo.

    Shown immediately, before the heavy GUI/scientific imports below, so the
    user gets visual feedback while the app loads (à la FIJI/ImageJ).
    """
    from percell4.resources import splash_image_path

    pixmap = QPixmap(str(splash_image_path()))
    # Scale the square logo down to a sensible on-screen size.
    pixmap = pixmap.scaled(
        400,
        400,
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint)
    splash.setWindowFlag(Qt.FramelessWindowHint, True)
    return splash


def main() -> None:
    """Launch the PerCell4 GUI application."""
    app = QApplication.instance() or QApplication(sys.argv)

    # Application icon: Dock (macOS), taskbar (Windows, Linux). Set as the
    # QApplication default so every top-level window inherits it.
    from percell4.resources import app_icon_path

    app.setWindowIcon(QIcon(str(app_icon_path())))

    # Show the splash before importing the (slow) GUI + scientific stack so it
    # paints immediately. processEvents() forces the first paint.
    splash = _make_splash()
    splash.show()
    app.processEvents()

    from percell4.gui.theme import apply_theme

    apply_theme(app)

    from percell4.application.session import Session
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    session = Session()
    data_model = CellDataModel(session)
    launcher = LauncherWindow(data_model)
    launcher.show()

    # Dismiss the splash once the launcher is up; finish() keeps the splash on
    # top until the target window is ready to be shown.
    splash.finish(launcher)

    # Windows-only: warn if the MSVC Redistributable is too old for PyTorch.
    # Silently passes on macOS/Linux.
    from percell4.workflows.diagnostics import check_msvc_redist_version

    is_current, version = check_msvc_redist_version()
    if not is_current:
        from percell4.gui.torch_error import show_msvc_redist_warning

        show_msvc_redist_warning(launcher, version)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
