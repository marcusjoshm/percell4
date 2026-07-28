"""Shared test fixtures for PerCell4."""

from __future__ import annotations

import os

# Pin qtpy's binding before anything can import it. A dev machine often has
# PyQt5, PyQt6, and PySide6 installed at once (napari and its plugins pull
# different ones); without this qtpy picks whichever it finds first, so the
# suite passes, fails, or segfaults from run to run. CI installs only PyQt5,
# so pinning it here makes a local run reproduce CI rather than testing a
# configuration nobody ships. pytest-qt's own binding is pinned via the
# ``qt_api`` ini option in pyproject.toml — both are needed, they select
# independently.
#
# setdefault, not assignment: an explicit QT_API in the environment still wins,
# so it stays possible to check binding portability deliberately.
os.environ.setdefault("QT_API", "pyqt5")

# pyqtgraph does NOT honour QT_API — it runs its own binding detection and will
# happily import PyQt6 when PyQt6 is installed, even with QT_API=pyqt5. Two
# bindings loaded into one process abort with SIGABRT the moment both try to
# own the Qt event loop, which is what made a full local run die partway
# through tests/test_gui while the same directory passed when run alone.
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import tempfile  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

# ── GL-dependency audit ─────────────────────────────────────────────
#
# Set PERCELL4_GL_AUDIT=1 to find every test that ends up constructing a real
# ``napari.Viewer``. Those need an OpenGL context, which the macOS offscreen
# platform does not provide, so they cannot live under ``tests/``.
#
# Grep cannot find them. ``test_dilute_phase_workflow_sidebar.py`` mentions
# neither napari nor ViewerWindow, yet it builds a real ``LauncherWindow``
# that owns a ``ViewerWindow``; a queued ``_wire_paint_autosave`` then reads
# ``.viewer`` during a *later* test's setup and builds the canvas there. It
# passes alone and segfaults when paired with ``test_cnr_segmenter.py``.
#
# The audit records before it raises, and that ordering is required rather
# than defensive: ``segmentation_panel.py`` wraps the very same access in
# ``try: ... except Exception: return``, so a raise-only probe is swallowed
# at exactly the site it exists to find and reports a clean run.

#: Populated by the patched constructor: (nodeid, formatted stack).
GL_AUDIT_HITS: list[tuple[str, str]] = []

_CURRENT_NODEID = "<no test running>"


def pytest_runtest_protocol(item, nextitem):  # noqa: ARG001
    """Track which test is executing, for attribution of deferred builds."""
    global _CURRENT_NODEID
    _CURRENT_NODEID = item.nodeid
    return None  # carry on with the normal protocol


def pytest_configure(config):  # noqa: ARG001
    if os.environ.get("PERCELL4_GL_AUDIT") != "1":
        return

    import napari

    original = napari.Viewer.__init__

    def _audited_init(self, *args, **kwargs):
        GL_AUDIT_HITS.append(
            (_CURRENT_NODEID, "".join(traceback.format_stack(limit=25)))
        )
        raise RuntimeError(
            "PERCELL4_GL_AUDIT: napari.Viewer constructed during "
            f"{_CURRENT_NODEID}"
        )
        return original(self, *args, **kwargs)  # noqa: W0101 — documents intent

    napari.Viewer.__init__ = _audited_init


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    if os.environ.get("PERCELL4_GL_AUDIT") != "1":
        return
    out = Path(
        os.environ.get("PERCELL4_GL_AUDIT_OUT")
        or Path(tempfile.gettempdir()) / "percell4_gl_audit.txt"
    )
    seen: dict[str, int] = {}
    for nodeid, _stack in GL_AUDIT_HITS:
        module = nodeid.split("::")[0]
        seen[module] = seen.get(module, 0) + 1
    lines = [f"{count:4d}  {module}" for module, count in sorted(seen.items())]
    out.write_text(
        f"napari.Viewer constructions: {len(GL_AUDIT_HITS)} "
        f"across {len(seen)} modules\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f"\n[gl-audit] {len(GL_AUDIT_HITS)} hits across {len(seen)} modules -> {out}")


@pytest.fixture(autouse=True)
def _sandbox_app_settings(tmp_path_factory):
    """Point every ``app_settings()`` call at a throw-away store.

    Window geometry and remembered dialog choices persist through
    ``percell4.gui.settings.app_settings``. Unredirected, that is the live
    macOS preference domain ``com.LeeLabPerCell4.PerCell4`` — so a test run
    used to overwrite the researcher's saved window layout, and two geometry
    modules went further and ``clear()``-ed it outright. Their own
    ``isolated_settings`` fixtures were built on ``setDefaultFormat`` +
    ``setPath``, which macOS ignores for the native format; the sandbox
    silently did nothing while the ``clear()`` landed on the real store.

    A fresh directory per test, rather than one per session, so a dialog that
    reads a remembered value sees only what the current test wrote. That is
    what the various ``_clear_qsettings`` fixtures were hand-rolling, and it
    keeps default-value assertions from depending on execution order.
    """
    from percell4.gui import settings

    settings.redirect_to(tmp_path_factory.mktemp("qsettings"))
    yield
    settings.clear_redirect()


@pytest.fixture(autouse=True)
def _flush_pending_qt_deletions():
    """Drain Qt's deferred-delete queue at the end of every test.

    ``widget.deleteLater()`` only schedules destruction; the object survives
    until the event loop next processes ``DeferredDelete``. A test that closes
    a napari viewer therefore leaves a live canvas behind, and the paint or
    teardown that finally runs is delivered during a *later* test — pytest-qt
    attributes the resulting exception to whichever test happens to be running,
    which is why failures moved around and why Qt-free suites such as
    ``tests/test_measure`` collected teardown errors they had no part in.

    Draining here keeps each test's cleanup inside its own boundary. No-ops
    when the test never created a QApplication, so the Qt-free suites pay
    nothing.
    """
    yield
    try:
        from qtpy.QtCore import QEvent
        from qtpy.QtWidgets import QApplication
    except Exception:  # noqa: BLE001 — Qt not installed / not importable
        return
    app = QApplication.instance()
    if app is None:
        return
    for _ in range(3):
        app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.fixture
def tmp_h5(tmp_path: Path) -> Path:
    """Return a temporary .h5 file path (does not create the file)."""
    return tmp_path / "test_dataset.h5"


@pytest.fixture
def sample_labels() -> np.ndarray:
    """Synthetic 100x100 label array with 5 cells.

    Cell 1: 20x20 block at (10, 10)
    Cell 2: 20x20 block at (10, 60)
    Cell 3: 20x20 block at (50, 10)
    Cell 4: 20x20 block at (50, 60)
    Cell 5: 15x15 block at (70, 35)
    """
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[10:30, 10:30] = 1
    labels[10:30, 60:80] = 2
    labels[50:70, 10:30] = 3
    labels[50:70, 60:80] = 4
    labels[70:85, 35:50] = 5
    return labels


@pytest.fixture
def sample_image() -> np.ndarray:
    """Synthetic 100x100 intensity image with known values per cell region.

    Cell 1 region: intensity 100
    Cell 2 region: intensity 200
    Cell 3 region: intensity 150
    Cell 4 region: intensity 250
    Cell 5 region: intensity 175
    Background: intensity 10
    """
    image = np.full((100, 100), 10.0, dtype=np.float32)
    image[10:30, 10:30] = 100.0
    image[10:30, 60:80] = 200.0
    image[50:70, 10:30] = 150.0
    image[50:70, 60:80] = 250.0
    image[70:85, 35:50] = 175.0
    return image
