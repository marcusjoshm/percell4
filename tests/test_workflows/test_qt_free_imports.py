"""Structural guarantee: the workflows subpackage must stay Qt-agnostic.

If anyone adds a `from qtpy import ...` or `import napari` to
``src/percell4/workflows/*``, these tests fail loudly. The batch runner's
unit tests depend on being able to import the workflow core without a
running ``QApplication``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FORBIDDEN_MODULES = ("qtpy", "PyQt5", "PyQt6", "PySide2", "PySide6", "napari")


def test_workflows_package_imports_without_qt():
    """Importing percell4.workflows must not pull in any GUI library."""
    # Start fresh so prior test files don't pollute sys.modules.
    for mod in list(sys.modules):
        if mod.startswith("percell4.workflows"):
            del sys.modules[mod]

    import percell4.workflows  # noqa: F401

    # We may have inherited GUI imports from earlier test modules in the
    # pytest session — so check the individual workflow module *__dict__*s,
    # not sys.modules at large.
    import percell4.workflows as pkg
    import percell4.workflows.artifacts  # noqa: F401
    import percell4.workflows.channels  # noqa: F401
    import percell4.workflows.diagnostics  # noqa: F401
    import percell4.workflows.failures  # noqa: F401
    import percell4.workflows.host  # noqa: F401
    import percell4.workflows.models  # noqa: F401
    import percell4.workflows.run_log  # noqa: F401
    for mod_name in (
        "artifacts", "channels", "diagnostics", "failures", "host", "models", "run_log",
    ):
        mod = getattr(pkg, mod_name, None) or sys.modules[f"percell4.workflows.{mod_name}"]
        offenders = [
            bad for bad in _FORBIDDEN_MODULES if bad in mod.__dict__
        ]
        assert not offenders, (
            f"percell4.workflows.{mod_name} has forbidden symbols: {offenders}"
        )


def test_no_qt_imports_in_workflows_source():
    """Grep the source files directly for forbidden imports."""
    here = Path(__file__).resolve()
    src = here.parents[2] / "src" / "percell4" / "workflows"
    assert src.is_dir(), f"expected {src} to be a directory"

    offenders: list[str] = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for bad in _FORBIDDEN_MODULES:
            # Match `import qtpy`, `from qtpy`, etc.
            patterns = (
                f"import {bad}",
                f"from {bad}",
            )
            for p in patterns:
                if p in text:
                    offenders.append(f"{py.name}: {p}")

    if offenders:
        pytest.fail(
            "workflows/ subpackage must be Qt-agnostic, found:\n  "
            + "\n  ".join(offenders)
        )
