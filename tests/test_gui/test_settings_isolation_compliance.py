"""Codebase invariant: ``QSettings`` is constructed in exactly one place.

Window geometry and remembered dialog choices persist to a per-user store. On
macOS that store is the live CFPreferences domain
``com.LeeLabPerCell4.PerCell4``, and the two-argument ``QSettings(org, app)``
constructor reaches it no matter what ``setDefaultFormat`` or ``setPath`` say —
both are ignored for the native format.

That is why three test modules could carry an ``isolated_settings`` fixture,
believe they were sandboxed, and still ``clear()`` the researcher's real saved
window layout. Running the suite rearranged their desktop.

The fix is structural rather than careful: one factory,
``percell4.gui.settings.app_settings``, which the test suite redirects once for
the whole session. A second construction site anywhere would silently escape
that redirect and reintroduce the bug, so this test forbids one.

Asserted by inspection, the same way ``test_qt_free_imports.py`` asserts the
hexagonal import boundary and ``test_signal_lifetime_compliance.py`` asserts
the signal-connection shapes.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "percell4"

#: The one file allowed to construct a ``QSettings``.
_CANONICAL = SRC / "gui" / "settings.py"

#: Any ``QSettings(...)`` construction. Deliberately matches the call form
#: only — bare ``QSettings`` in a type annotation, an ``isinstance`` check or
#: an import line is fine, since none of those open a store.
_QSETTINGS_CONSTRUCTION = re.compile(r"\bQSettings\s*\(")

#: A per-module copy of the org/app pair. These are what the call sites used
#: before the factory existed; leaving one behind means someone is still
#: addressing the store by name and is one edit away from constructing it.
_LOCAL_ORG_APP_CONST = re.compile(
    r"^\s*_?QSETTINGS_(?:ORG|APP)\s*=", re.MULTILINE
)


def _py_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for path in _py_files():
        if path == _CANONICAL:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                rel = path.relative_to(SRC.parents[1])
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


def test_qsettings_is_constructed_only_in_the_settings_module():
    """Every store must come from ``app_settings()``.

    A construction site outside the factory bypasses the test suite's
    session-wide redirect and writes to the researcher's real preferences.
    """
    offenders = _offenders(_QSETTINGS_CONSTRUCTION)
    assert not offenders, (
        "QSettings must be constructed only in percell4/gui/settings.py; "
        "call percell4.gui.settings.app_settings() instead.\n"
        + "\n".join(offenders)
    )


def test_no_module_level_org_app_constants_remain():
    """The org/app pair lives in ``settings.py`` and nowhere else."""
    offenders = _offenders(_LOCAL_ORG_APP_CONST)
    assert not offenders, (
        "Import APP_ORG / APP_NAME from percell4.gui.settings rather than "
        "redeclaring them.\n" + "\n".join(offenders)
    )


def test_guard_patterns_still_match_what_they_claim():
    """Self-check: the regexes above detect a synthetic offender.

    Without this, a typo in a pattern would silently pass the whole file and
    the invariant would be unenforced while looking green.
    """
    assert _QSETTINGS_CONSTRUCTION.search('qs = QSettings("Org", "App")')
    assert _QSETTINGS_CONSTRUCTION.search("QSettings(fmt, scope, org, app)")
    assert _LOCAL_ORG_APP_CONST.search('_QSETTINGS_ORG = "LeeLabPerCell4"')
    assert _LOCAL_ORG_APP_CONST.search('QSETTINGS_APP = "PerCell4"')

    # Shapes that must NOT trip the guard.
    assert not _QSETTINGS_CONSTRUCTION.search("from qtpy.QtCore import QSettings")
    assert not _QSETTINGS_CONSTRUCTION.search("def f() -> QSettings:")
    assert not _LOCAL_ORG_APP_CONST.search("    # _QSETTINGS_ORG = 'x' (retired)")
