"""Persistent store for expert-only configuration.

Backs the launcher's Advanced panel. Holds one setting today -- the Cellpose
device override -- and is shaped so later advanced settings can join it
without re-deciding the mechanism.

**Why not QSettings.** ``percell4.gui.settings.app_settings`` is the
established preference store, but it imports ``qtpy``, and the batch CLI has
to read the override from a terminal run with no GUI toolkit loaded. A plain
JSON file under the user's config directory is readable by both surfaces,
which is what makes the override apply to a headless run rather than only to
one launched from the Batch Tools window.

**Nothing here may raise.** This is an opt-in convenience file; a missing,
malformed, unreadable, or hand-mangled one falls back to defaults. An unused
feature must never be able to break the default path.

**The path is resolved in exactly one function.** ``config_path()`` is the
only place that knows where the file lives, so the test suite can redirect
every caller at once. ``tests/test_config/test_advanced_settings_isolation_compliance.py``
fails the build if a second call site appears -- the same structural guard,
and the same reasoning, as the QSettings factory.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Directory name used under the platform's user-config location. Deliberately
#: not the QSettings org/app pair -- that lives in the Qt-bound settings module
#: and importing it here would defeat the point of this file.
_APP_DIR_NAME = "PerCell4"

#: The settings file's basename. Public so tests can assert against it without
#: re-spelling the literal and tripping the compliance guard.
CONFIG_FILENAME = "advanced_settings.json"

#: Redirect hook. ``None`` means "use the real per-user location".
#:
#: Consulted on every call rather than monkeypatched onto the functions,
#: because a call site may bind either way -- ``from ... import
#: save_advanced_settings`` captures the function object at import time, so
#: patching a module attribute would miss it. That exact binding-style gap is
#: what let three test modules believe they were sandboxed while writing to a
#: researcher's real preferences.
_redirect: Callable[[], Path] | None = None


@dataclass(frozen=True, slots=True)
class AdvancedSettings:
    """Expert-only configuration. Every field defaults to "behave as before"."""

    #: Explicit torch device for Cellpose (``xpu``, ``cuda:1``). ``None`` means
    #: auto-detect, which is what every unconfigured install does.
    cellpose_device: str | None = None

    def __post_init__(self) -> None:
        # A cleared text field arrives as "" or "   ". Both mean auto; storing
        # either verbatim would make every later run probe a device named
        # empty-string and report a fallback nobody asked for.
        if self.cellpose_device is not None:
            cleaned = self.cellpose_device.strip()
            object.__setattr__(self, "cellpose_device", cleaned or None)


def _default_config_dir() -> Path:
    """Locate the platform's user-config directory for this application."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
    return root / _APP_DIR_NAME


def config_path() -> Path:
    """Return the settings file's location.

    The single source of truth for where advanced settings live. Redirected
    wholesale by :func:`redirect_to` so the test suite never touches the real
    file.
    """
    if _redirect is not None:
        return _redirect() / CONFIG_FILENAME
    return _default_config_dir() / CONFIG_FILENAME


def redirect_to(directory: Path | str) -> None:
    """Point every subsequent :func:`config_path` call at ``directory``."""
    root = Path(directory)

    def _factory() -> Path:
        return root

    global _redirect
    _redirect = _factory


def clear_redirect() -> None:
    """Restore the real per-user location. Inverse of :func:`redirect_to`."""
    global _redirect
    _redirect = None


def _read_raw() -> dict[str, Any]:
    """Read the file as a plain dict, or return empty on any problem."""
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Missing is the overwhelmingly common case and not worth a log line;
        # anything else is worth knowing about without being fatal.
        if not isinstance(exc, FileNotFoundError):
            logger.warning("advanced settings unreadable at %s: %s", path, exc)
        return {}

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("advanced settings are not valid JSON at %s: %s", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "advanced settings at %s are %s, expected an object; ignoring.",
            path,
            type(data).__name__,
        )
        return {}
    return data


def load_advanced_settings() -> AdvancedSettings:
    """Read the stored settings, falling back to defaults on any problem."""
    raw = _read_raw()

    device = raw.get("cellpose_device")
    if device is not None and not isinstance(device, str):
        logger.warning(
            "advanced settings: cellpose_device is %s, expected a string; ignoring.",
            type(device).__name__,
        )
        device = None

    return AdvancedSettings(cellpose_device=device)


def save_advanced_settings(settings: AdvancedSettings) -> None:
    """Write ``settings``, preserving any keys this build does not know about.

    The merge matters for forward compatibility: a newer build may store
    settings this one has never heard of, and loading then saving on the older
    build must not silently delete them.
    """
    path = config_path()
    raw = _read_raw()
    raw["cellpose_device"] = settings.cellpose_device

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        # A read-only home directory should degrade to "setting doesn't stick",
        # not take down the panel that wrote it.
        logger.warning("could not write advanced settings to %s: %s", path, exc)


def load_cellpose_device() -> str | None:
    """Convenience reader for the one setting the segmentation path needs."""
    return load_advanced_settings().cellpose_device
