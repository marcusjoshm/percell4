"""Shared test fixtures for PerCell4."""

from __future__ import annotations

import os
import struct

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

# Render to an offscreen buffer instead of the desktop. Without this, a run
# put real windows on screen and took focus — 13 modules call .show() directly
# and roughly as many more reach it through production code, e.g.
# MultiSelectController.show() does show(); raise_(); activateWindow().
#
# This works only because the tests that build a real napari.Viewer now live in
# tests_gui/. macOS offscreen provides no OpenGL context, so a viewer built
# under it segfaults (exit 139, no traceback) rather than failing — which is
# why an earlier attempt at this concluded offscreen was unusable and reverted
# it. The order matters: quarantine first, then offscreen.
#
# Expect "QOpenGLWidget is not supported on this platform." on stderr. It is
# benign for the widget tests that remain here; if it is followed by a crash,
# something GL-dependent has leaked back into tests/ and the autouse guard in
# this file will name it.
#
# setdefault, like the pins above: QT_QPA_PLATFORM=cocoa pytest still runs
# windowed, which is the way to watch a layout problem happen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt assumes 96 DPI under the offscreen platform while macOS lays out on a
# 72pt basis, so the same widget measures about 25% wider offscreen (average
# char width 10 vs 8, line height 20 vs 16). Several tests assert a pixel
# budget — "this form fits a standard window width" — and those assertions
# would otherwise mean something different from what the researcher sees.
#
# Pinning the font DPI restores the macOS basis. An environment variable
# rather than a fixture because there is no ordering hazard: it must be set
# before the QApplication is constructed, and pytest-qt builds that during
# fixture setup, after any autouse fixture we could write here.
os.environ.setdefault("QT_FONT_DPI", "72")

import tempfile  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

# ── No napari viewer under tests/ ───────────────────────────────────
#
# Every test here runs under QT_QPA_PLATFORM=offscreen, which provides no
# OpenGL context on macOS. A real ``napari.Viewer`` built under it does not
# fail — it segfaults, exit 139, with no traceback and no attribution. Tests
# that need one live in ``tests_gui/``.
#
# Grep cannot police that boundary. ``test_dilute_phase_workflow_sidebar.py``
# mentioned neither napari nor ViewerWindow, yet built a real
# ``LauncherWindow`` that owns a ``ViewerWindow``; a queued handler then read
# ``.viewer`` and constructed the canvas, sometimes during a *later* test's
# setup. It passed alone and segfaulted when run after another module. So the
# rule is enforced dynamically, at the constructor.
#
# Record *before* raising. That ordering is required, not defensive:
# ``segmentation_panel`` wraps the same access in ``try: ... except
# Exception: return``, which would swallow a raise-only guard at precisely the
# site worth catching and report a clean run. The fixture below inspects the
# record, so a swallowed raise still fails the test that caused it.

#: (nodeid, formatted stack) for every construction attempt this session.
VIEWER_CONSTRUCTIONS: list[tuple[str, str]] = []

_CURRENT_NODEID = "<no test running>"


def pytest_runtest_protocol(item, nextitem):  # noqa: ARG001
    """Track the executing test, so deferred builds are attributed somewhere."""
    global _CURRENT_NODEID
    _CURRENT_NODEID = item.nodeid
    return None  # carry on with the normal protocol


def pytest_configure(config):  # noqa: ARG001
    """Make ``napari.Viewer()`` a recorded failure rather than a segfault."""
    import napari

    original = napari.Viewer.__init__

    def _guarded_init(self, *args, **kwargs):
        VIEWER_CONSTRUCTIONS.append(
            (_CURRENT_NODEID, "".join(traceback.format_stack(limit=25)))
        )
        raise RuntimeError(
            f"napari.Viewer constructed during {_CURRENT_NODEID}. Tests under "
            "tests/ run headless and cannot build a viewer; move this test to "
            "tests_gui/, or ask whether a viewer exists with "
            "ViewerWindow.existing_viewer instead of .viewer."
        )
        return original(self, *args, **kwargs)  # noqa: W0101 — documents intent

    napari.Viewer.__init__ = _guarded_init


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Summarise offenders, so one run gives the whole relocation list."""
    if not VIEWER_CONSTRUCTIONS:
        return
    by_module: dict[str, int] = {}
    for nodeid, _stack in VIEWER_CONSTRUCTIONS:
        module = nodeid.split("::")[0]
        by_module[module] = by_module.get(module, 0) + 1
    out = Path(
        os.environ.get("PERCELL4_GL_AUDIT_OUT")
        or Path(tempfile.gettempdir()) / "percell4_gl_audit.txt"
    )
    lines = [f"{count:4d}  {module}" for module, count in sorted(by_module.items())]
    out.write_text(
        f"napari.Viewer constructions: {len(VIEWER_CONSTRUCTIONS)} "
        f"across {len(by_module)} modules\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(
        f"\n[gl-guard] {len(VIEWER_CONSTRUCTIONS)} napari.Viewer construction(s) "
        f"across {len(by_module)} module(s); these belong in tests_gui/ -> {out}"
    )


@pytest.fixture(autouse=True)
def _forbid_napari_viewer():
    """Fail the test that built a viewer, even if it swallowed the exception."""
    before = len(VIEWER_CONSTRUCTIONS)
    yield
    new = VIEWER_CONSTRUCTIONS[before:]
    if new:
        raise AssertionError(
            f"{len(new)} napari.Viewer construction(s) during this test. "
            "tests/ runs headless (no GL context); move this module to "
            "tests_gui/. Most recent stack:\n" + new[-1][1]
        )


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
def _sandbox_advanced_settings(tmp_path_factory):
    """Point every ``config_path()`` call at a throw-away directory.

    The advanced-settings store is a second persistence surface with the same
    failure mode as the QSettings one above: unredirected, a test that saves a
    device override rewrites the researcher's real configuration, and nothing
    announces it. Autouse rather than opt-in for exactly the reason the
    QSettings sandbox is — the tests that most need isolation are the ones
    that never think to ask for it.

    A fresh directory per test so a load-after-save assertion sees only what
    the current test wrote.
    """
    from percell4.config import advanced

    advanced.redirect_to(tmp_path_factory.mktemp("advanced_settings"))
    yield
    advanced.clear_redirect()


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
    _schedule_pyqtgraph_window_deletion(app)
    for _ in range(3):
        app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def _schedule_pyqtgraph_window_deletion(app) -> None:
    """Put closed-but-alive pyqtgraph windows on the deferred-delete queue.

    ``qtbot.addWidget`` closes a widget at teardown but never destroys it, so a
    window holding a pyqtgraph ``PlotWidget`` — the CNR/metric segmenter, the
    threshold-QC and phasor views — survives its test with a live
    ``GraphicsView`` on it. A later test spins the event loop, that stale view
    is painted, its ``AxisItem`` has since been collected, and the process
    segfaults inside ``GraphicsView.paintEvent``. The crash lands on whichever
    test is running at the time, which is why it presented as an unrelated
    flake in ``tests/test_gui/test_metric_segmenter_panel.py``.

    Nothing else puts these windows on the queue, so the drain below has
    nothing to collect unless they are scheduled here first. Widgets already
    destroyed on the C++ side raise on attribute access; they are what this
    is cleaning up after, so skip them.
    """
    try:
        from pyqtgraph.widgets.GraphicsView import GraphicsView
    except Exception:  # noqa: BLE001 — pyqtgraph absent; nothing to clean
        return
    for widget in list(app.topLevelWidgets()):
        try:
            if isinstance(widget, GraphicsView) or widget.findChildren(GraphicsView):
                widget.close()
                widget.deleteLater()
        except RuntimeError:
            continue


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


@pytest.fixture
def lif_header_bytes():
    """Factory wrapping header XML in a valid ``.lif`` container prefix.

    The reference ``.lif`` is 78 MB and cannot be checked in, so every
    ``.lif`` test synthesises the header it needs. Returns bytes shaped like
    a real file's first block: ``0x70`` marker, remaining-byte count, ``0x2A``
    separator, UTF-16 character count, then the XML as UTF-16LE.

    The byte count at offset 4 covers the separator and the character count
    as well as the XML — five bytes more than the payload — which is the
    trap ``read_lif_header`` has to avoid. Overridable so tests can build
    deliberately malformed containers.
    """

    def build(
        xml: str,
        *,
        marker: int = 0x70,
        separator: int = 0x2A,
        nchars: int | None = None,
        truncate: int = 0,
    ) -> bytes:
        payload = xml.encode("utf-16-le")
        declared = len(xml) if nchars is None else nchars
        head = struct.pack("<ii", marker, 1 + 4 + len(payload))
        head += bytes([separator]) + struct.pack("<i", declared)
        blob = head + payload
        return blob[: len(blob) - truncate] if truncate else blob

    return build
