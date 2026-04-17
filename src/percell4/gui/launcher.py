"""Backward-compatibility shim — LauncherWindow moved to interfaces/gui/main_window.py.

This file re-exports LauncherWindow so existing imports continue to work.
New code should import from percell4.interfaces.gui.main_window.
"""

from percell4.interfaces.gui.main_window import LauncherWindow

__all__ = ["LauncherWindow"]
