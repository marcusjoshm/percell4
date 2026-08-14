"""Codebase invariant: the advanced-settings file is located in one place.

This mirrors ``tests/test_gui/test_settings_isolation_compliance.py``, and for
the same reason. That test exists because seven modules addressed the QSettings
store directly, three of them believed they were sandboxed, and the suite
overwrote the researcher's real saved window layout for months before anyone
noticed.

The advanced-settings store is a second persistence surface with the same
failure mode and none of the accumulated scar tissue. A module that builds the
path itself -- reading ``XDG_CONFIG_HOME`` or joining the filename by hand --
escapes the suite-wide redirect in ``tests/conftest.py`` and writes to the
user's real configuration. The damage is quiet: a researcher's device override
changes and nothing announces it.

So the path is resolved in exactly one function, and this test forbids a
second.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

_SCANNED = [
    _REPO / "src" / "percell4",
    _REPO / "tests",
    _REPO / "tests_gui",
]

#: The one module allowed to locate the file, plus this test, whose prose and
#: self-check literals necessarily spell out the forbidden shapes.
_CANONICAL = _REPO / "src" / "percell4" / "config" / "advanced.py"
_EXEMPT = {_CANONICAL, Path(__file__).resolve()}

#: The settings filename written as a literal. Everything else must reach it
#: through ``config_path()``.
_FILENAME_LITERAL = re.compile(r"""['"]advanced_settings\.json['"]""")

#: Hand-rolled user-config-directory resolution. Any of these outside the
#: canonical module means someone is building a second path.
_CONFIG_DIR_LOOKUP = re.compile(
    r"""XDG_CONFIG_HOME|Application\s+Support|['"]APPDATA['"]"""
)


def _py_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCANNED:
        if root.is_dir():
            files.extend(
                p for p in root.rglob("*.py") if "__pycache__" not in p.parts
            )
    return files


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for path in _py_files():
        if path in _EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(_REPO)}:{lineno}: {line.strip()}")
    return hits


def test_settings_filename_appears_only_in_the_config_module():
    offenders = _offenders(_FILENAME_LITERAL)
    assert not offenders, (
        "The advanced-settings filename belongs only in "
        "percell4/config/advanced.py; call config_path() instead.\n"
        + "\n".join(offenders)
    )


def test_user_config_directory_is_resolved_only_in_the_config_module():
    offenders = _offenders(_CONFIG_DIR_LOOKUP)
    assert not offenders, (
        "Resolve the user config directory through "
        "percell4.config.advanced.config_path() rather than rebuilding it.\n"
        + "\n".join(offenders)
    )


def test_guard_patterns_still_match_what_they_claim():
    """Self-check: a typo in a pattern above would pass the whole file while
    leaving the invariant unenforced."""
    assert _FILENAME_LITERAL.search('p = d / "advanced_settings.json"')
    assert _FILENAME_LITERAL.search("open('advanced_settings.json')")
    assert _CONFIG_DIR_LOOKUP.search('os.environ.get("XDG_CONFIG_HOME")')
    assert _CONFIG_DIR_LOOKUP.search('Path.home() / "Library" / "Application Support"')
    assert _CONFIG_DIR_LOOKUP.search('os.environ["APPDATA"]')

    # Shapes that must NOT trip the guard.
    assert not _FILENAME_LITERAL.search("from percell4.config.advanced import config_path")
    assert not _CONFIG_DIR_LOOKUP.search("settings = load_advanced_settings()")
