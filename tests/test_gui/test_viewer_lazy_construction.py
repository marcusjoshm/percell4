"""Reading launcher state must not build a napari viewer as a side effect.

``ViewerWindow.viewer`` constructs the viewer on access. That is right for
"show the user something now", and wrong for "does a viewer exist?" — yet the
codebase asked the second question through the first, in guards shaped like::

    if viewer_win is None or viewer_win.viewer is None:
        return

which can never be true, and builds a full napari window plus an OpenGL canvas
in order to discover that. Loading a dataset therefore spawned the viewer even
when the researcher never opened it.

It also made the test suite unable to go headless. Modules with no textual
reference to napari — ``test_dilute_phase_workflow_sidebar.py`` builds a real
``LauncherWindow``, which owns a ``ViewerWindow`` — ended up constructing a
canvas through a queued handler, sometimes during a *later* test's setup.
Under ``QT_QPA_PLATFORM=offscreen``, which has no GL context on macOS, that is
a segfault rather than a failure. The audit in ``docs/audits/gl-dependent-tests.md``
counted 14 such constructions in that one module; after this change it makes
none.

:attr:`ViewerWindow.existing_viewer` is the non-constructing accessor.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.model import CellDataModel


@pytest.fixture
def viewer_window(qtbot):  # noqa: ARG001 — qtbot ensures a QApplication exists
    """A real ``ViewerWindow`` whose napari viewer has not been built."""
    from percell4.gui.viewer import ViewerWindow

    session = Session()
    win = ViewerWindow(CellDataModel(session))
    yield win


def test_existing_viewer_is_none_before_anything_touches_it(viewer_window):
    """The whole point: asking does not create."""
    assert viewer_window.existing_viewer is None


def test_existing_viewer_returns_the_same_object_once_built(monkeypatch, viewer_window):
    """After a viewer exists, both accessors agree.

    The real constructor needs a GL context, so stand one in rather than
    building it — this asserts the accessor contract, not napari.
    """
    sentinel = SimpleNamespace(name="fake-napari-viewer")

    def _fake_ensure():
        viewer_window._viewer = sentinel

    monkeypatch.setattr(viewer_window, "_ensure_viewer", _fake_ensure)

    assert viewer_window.existing_viewer is None
    assert viewer_window.viewer is sentinel
    assert viewer_window.existing_viewer is sentinel


def test_dataset_load_does_not_construct_a_viewer(qtbot, tmp_path, monkeypatch):
    """A ``data`` state change must not build the viewer.

    This is the regression that made the suite un-headless, asserted directly:
    ``SegmentationPanel._wire_paint_autosave`` and ``_sync_diameter_circle``
    both run on every ``data`` change and both used to read the constructing
    property.
    """
    from percell4.gui.segmentation_panel import SegmentationPanel
    from percell4.gui.viewer import ViewerWindow
    from percell4.store import DatasetStore

    path = tmp_path / "exp.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch0"]})
    store.write_array("intensity", np.zeros((8, 8), dtype=np.float32))

    session = Session()
    data_model = CellDataModel(session)

    viewer_win = ViewerWindow(data_model)

    constructed: list[str] = []
    original_ensure = viewer_win._ensure_viewer

    def _recording_ensure():
        constructed.append("built")
        return original_ensure()

    monkeypatch.setattr(viewer_win, "_ensure_viewer", _recording_ensure)

    launcher = MagicMock()
    launcher._windows = {"viewer": viewer_win}
    launcher._current_store = store

    panel = SegmentationPanel(data_model, launcher=launcher)
    qtbot.addWidget(panel)

    session.set_dataset(
        DatasetHandle(path=path, metadata={"channel_names": ["ch0"]})
    )
    qtbot.wait(50)  # let the deferred one-tick handlers run

    assert not constructed, (
        "loading a dataset built a napari viewer; the panel is asking "
        "'does a viewer exist?' through the constructing property again"
    )
    assert viewer_win.existing_viewer is None
