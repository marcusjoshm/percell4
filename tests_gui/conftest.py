"""Fixtures for the GL-dependent GUI suite.

This tree is deliberately outside ``tests/``. ``testpaths = ["tests"]`` in
``pyproject.toml`` means a bare ``pytest`` does not collect it, which is what
keeps the default run headless.

Three things below are duplicated from ``tests/conftest.py`` rather than
shared. That is intentional, not an oversight: the binding pins have to
execute before *any* ``qtpy`` import in whichever session is running, and a
root ``conftest.py`` is the only place that ordering is guaranteed. Sharing
them through an importable module would put the import itself before the
pins.
"""

from __future__ import annotations

import os

# See tests/conftest.py for the full reasoning. Briefly: a dev machine can
# have PyQt5, PyQt6 and PySide6 installed at once, qtpy picks whichever it
# finds first, and pyqtgraph ignores QT_API entirely and runs its own
# detection — two bindings in one process abort with SIGABRT the moment both
# try to own the event loop.
os.environ.setdefault("QT_API", "pyqt5")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import numpy as np  # noqa: E402
import pytest  # noqa: E402


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Refuse to run under a platform that cannot provide an OpenGL context.

    Every test here builds a real ``napari.Viewer`` and therefore a real
    vispy canvas. Under ``QT_QPA_PLATFORM=offscreen`` that segfaults — no
    error, no traceback, just exit 139 partway through.

    The realistic way to arrive here is collecting both trees at once
    (``pytest .``, ``pytest tests tests_gui``, or an IDE "run all tests"
    button). ``tests/conftest.py`` sets the offscreen platform process-wide
    at import, so a combined run would drag these tests under it. Failing
    collection with an explanation beats a segfault with none.
    """
    if not items:
        return
    platform = os.environ.get("QT_QPA_PLATFORM", "")
    if platform == "offscreen":
        raise pytest.UsageError(
            "tests_gui/ needs a real OpenGL context and segfaults under "
            "QT_QPA_PLATFORM=offscreen. This usually means tests/ and "
            "tests_gui/ were collected together — tests/conftest.py sets the "
            "offscreen platform for the whole process. Run them separately: "
            "`pytest` for the headless suite, `pytest tests_gui/` for this one."
        )


@pytest.fixture(autouse=True)
def _sandbox_app_settings(tmp_path_factory):
    """Point every ``app_settings()`` call at a throw-away store.

    Duplicated from ``tests/conftest.py`` because this suite needs it *more*,
    not less: these tests build real ``ViewerWindow`` and ``LauncherWindow``
    instances, whose ``_save_geometry()`` writes to the live macOS preference
    domain on close. Without the redirect, running this suite is the fastest
    way to destroy the researcher's saved window layout — the original bug.
    """
    from percell4.gui import settings

    settings.redirect_to(tmp_path_factory.mktemp("qsettings"))
    yield
    settings.clear_redirect()


@pytest.fixture(autouse=True)
def _flush_pending_qt_deletions():
    """Drain Qt's deferred-delete queue at the end of every test.

    ``deleteLater()`` only schedules destruction. A test that closes a napari
    viewer leaves a live canvas behind, and the paint or teardown that finally
    runs is delivered during a *later* test, so pytest-qt attributes the
    exception to whichever test happens to be running. That is why the failing
    set used to move between runs.
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
def sample_labels() -> np.ndarray:
    """Synthetic 100x100 label array with 5 cells.

    Duplicated from ``tests/conftest.py`` because that file is not an ancestor
    of this tree and so is never loaded here. Three relocated modules request
    it; keep the two definitions identical, since their assertions depend on
    the exact block positions.

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
