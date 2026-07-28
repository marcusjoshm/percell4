"""The single construction site for the application's ``QSettings`` store.

Every window that remembers its geometry, and every dialog that remembers a
choice between runs, persists through here. Before this module the
organisation/application pair was spelled out at 35 call sites across 15
files — as bare literals in some, as per-module ``_QSETTINGS_ORG`` /
``_QSETTINGS_APP`` constants in others — which left the test suite with no
single place to redirect.

That mattered more than duplication usually does. On macOS the two-argument
``QSettings(org, app)`` constructor resolves to the live CFPreferences domain
``com.LeeLabPerCell4.PerCell4``, and *neither* ``setDefaultFormat`` nor
``setPath`` redirects it — both are ignored for the native format. Three test
modules carried an ``isolated_settings`` fixture built on exactly that pair
and were therefore reading, writing, and ``clear()``-ing the researcher's real
saved window layout while believing they were sandboxed. Running the suite
rearranged the desktop.

Call ``app_settings()`` rather than constructing ``QSettings`` directly;
``tests/test_gui/test_settings_isolation_compliance.py`` fails the build if
anything else does.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from qtpy.QtCore import QSettings

#: Organisation and application names identifying the preference store.
#: Changing either orphans every saved window position, so treat them as
#: fixed.
APP_ORG = "LeeLabPerCell4"
APP_NAME = "PerCell4"

#: Redirect hook. ``None`` means "use the real per-user store".
#:
#: The redirect lives inside the factory rather than being monkeypatched onto
#: it because call sites may bind the function either way — ``from ... import
#: app_settings`` captures the object at import time, so patching the module
#: attribute would miss them. Consulting a module global on every call works
#: for both import styles, which matters here: an isolation mechanism that
#: fails silently for *some* call sites is what caused the bug this module
#: exists to fix.
_redirect: Callable[[], QSettings] | None = None


def app_settings() -> QSettings:
    """Return the application's ``QSettings`` store.

    A fresh handle each call, which is how ``QSettings`` is meant to be used —
    handles are cheap and all of them address the same backing store, so a
    value written through one is visible to the next.
    """
    if _redirect is not None:
        return _redirect()
    return QSettings(APP_ORG, APP_NAME)


def redirect_to(directory: Path | str) -> None:
    """Point every subsequent :func:`app_settings` call at ``directory``.

    Used by the test suite to keep preferences inside ``tmp_path``. The
    four-argument constructor is not a stylistic choice — it is the only form
    macOS honours for a non-native store, which is precisely what the older
    ``setDefaultFormat`` + ``setPath`` approach got wrong.
    """
    root = str(directory)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, root)

    def _factory() -> QSettings:
        return QSettings(QSettings.IniFormat, QSettings.UserScope, APP_ORG, APP_NAME)

    global _redirect
    _redirect = _factory


def clear_redirect() -> None:
    """Restore the real per-user store. Inverse of :func:`redirect_to`."""
    global _redirect
    _redirect = None
