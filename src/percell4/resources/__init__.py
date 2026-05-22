"""Bundled static resources for PerCell4 (application icons, etc.)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def resource_path(name: str) -> Path:
    """Absolute path to a bundled resource file by name."""
    return Path(str(files(__package__).joinpath(name)))


def app_icon_path() -> Path:
    """Source image for the application icon.

    The PNG is loaded by Qt at runtime to set the Dock (macOS) / taskbar
    (Windows, Linux) icon. The platform-native ``.icns`` / ``.ico`` files in
    this package are consumed by PyInstaller when building standalone bundles.
    """
    return resource_path("percell4_logo.png")


def splash_image_path() -> Path:
    """Source image for the startup splash screen (the PerCell4 logo)."""
    return resource_path("percell4_logo.png")
