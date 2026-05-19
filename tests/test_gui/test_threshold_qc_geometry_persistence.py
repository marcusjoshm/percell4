"""Regression test: ThresholdQCController's per-group QC window must
remember its last position.

Bug 2026-05-18: every group transition built a fresh QMainWindow via
``_build_qc_dock`` without calling ``saveGeometry`` / ``restoreGeometry``,
so the user moved the window for Group 1 and it re-centered for Group 2,
and again for Group 3, ad infinitum. Same after a fresh workflow run.

The codebase's other windows (SessionWindow, PhasorPlot, DataPlot,
CellTable, LauncherWindow, ViewerWindow) all use the canonical
``QSettings("LeeLabPerCell4", "PerCell4")`` + ``saveGeometry`` /
``restoreGeometry`` pattern with a ``"<window>/geometry"`` key. This
test pins the same pattern for ``threshold_qc/geometry``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from qtpy.QtCore import QCoreApplication, QSettings

from percell4.domain.measure.grouper import GroupingResult


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Sandbox QSettings so the geometry test doesn't bleed into the
    real preferences store (mirrors tests/test_gui_workflows/
    test_session_window.py's isolated_settings fixture)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    QCoreApplication.setOrganizationName("LeeLabPerCell4")
    QCoreApplication.setApplicationName("PerCell4")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    QSettings("LeeLabPerCell4", "PerCell4").clear()
    yield tmp_path
    QSettings("LeeLabPerCell4", "PerCell4").clear()


def _make_grouping_result() -> GroupingResult:
    """Two-group result — enough to exercise the per-group QC loop."""
    return GroupingResult(
        group_assignments=pd.Series(
            data=np.array([1, 2], dtype=int),
            index=pd.Index([1, 2], name="label"),
            name="group",
        ),
        n_groups=2,
        group_means=[1.0, 5.0],
    )


@pytest.fixture
def fake_viewer():
    class _Layers(list):
        def remove(self, item):
            super().remove(item)

    viewer = MagicMock()
    viewer.layers = _Layers()
    viewer.add_image = MagicMock()
    viewer.add_labels = MagicMock()
    viewer.add_shapes = MagicMock()

    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    viewer_win.show = MagicMock()
    viewer_win.add_mask = MagicMock()
    return viewer_win


def _make_controller(fake_viewer):
    """Spin up a ThresholdQCController with sigma=0 to skip smoothing."""
    from percell4.gui.threshold_qc import ThresholdQCController

    H, W = 16, 16
    image = np.ones((H, W), dtype=np.float32) * 10.0
    seg_labels = np.zeros((H, W), dtype=np.int32)
    seg_labels[2:4, 2:4] = 1
    seg_labels[10:12, 10:12] = 2

    data_model = MagicMock()
    data_model.df = pd.DataFrame()
    data_model.session = MagicMock()

    return ThresholdQCController(
        viewer_win=fake_viewer,
        data_model=data_model,
        store=None,
        grouping_result=_make_grouping_result(),
        channel_image=image,
        seg_labels=seg_labels,
        channel="ch0",
        metric="mean_intensity",
        sigma=0.0,
        mask_name="m",
    )


def test_geometry_persists_across_group_transitions(
    qtbot, isolated_settings, fake_viewer,
):
    """Move the QC window during Group 1, advance to Group 2 — the new
    window must come up at the same position, not re-centered."""
    controller = _make_controller(fake_viewer)
    controller._current_index = 0
    controller._current_group_mask = controller._seg_labels == 1
    controller._group_image_buffer = controller._channel_image.copy()

    # Build the first group's QC dock.
    controller._build_qc_dock(initial_value=0.0)
    win1 = controller._qc_window
    qtbot.addWidget(win1)
    qtbot.waitExposed(win1)

    # Move it somewhere distinctive.
    win1.setGeometry(150, 50, 420, 380)
    qtbot.wait(50)

    # Simulate transition to Group 2.
    controller._current_index = 1
    controller._current_group_mask = controller._seg_labels == 2
    controller._build_qc_dock(initial_value=0.0)
    win2 = controller._qc_window
    qtbot.addWidget(win2)
    qtbot.waitExposed(win2)

    assert win2 is not win1, "Each group builds a fresh QMainWindow"

    g = win2.geometry()
    # restoreGeometry can adjust by a small amount on some platforms;
    # mirror the loose-equality tolerance used by SessionWindow's
    # existing geometry test.
    assert abs(g.x() - 150) <= 10, (
        f"Group 2's QC window should open at the position Group 1's "
        f"was moved to (x=150), got x={g.x()}"
    )
    assert abs(g.y() - 50) <= 10
    assert abs(g.width() - 420) <= 10
    assert abs(g.height() - 380) <= 10


def test_geometry_persists_across_workflow_runs(
    qtbot, isolated_settings, fake_viewer,
):
    """End the workflow, start a new one — the QC window opens at the
    last position the user moved it to in the previous run."""
    controller_a = _make_controller(fake_viewer)
    controller_a._current_index = 0
    controller_a._current_group_mask = controller_a._seg_labels == 1
    controller_a._group_image_buffer = controller_a._channel_image.copy()

    controller_a._build_qc_dock(initial_value=0.0)
    win_a = controller_a._qc_window
    qtbot.addWidget(win_a)
    qtbot.waitExposed(win_a)

    win_a.setGeometry(200, 80, 450, 410)
    qtbot.wait(50)

    # End the workflow (closes the window, saves geometry).
    controller_a._remove_qc_dock()
    qtbot.wait(50)

    # Brand new controller — fresh ThresholdQCController instance.
    controller_b = _make_controller(fake_viewer)
    controller_b._current_index = 0
    controller_b._current_group_mask = controller_b._seg_labels == 1
    controller_b._group_image_buffer = controller_b._channel_image.copy()

    controller_b._build_qc_dock(initial_value=0.0)
    win_b = controller_b._qc_window
    qtbot.addWidget(win_b)
    qtbot.waitExposed(win_b)

    g = win_b.geometry()
    assert abs(g.x() - 200) <= 10
    assert abs(g.y() - 80) <= 10
    assert abs(g.width() - 450) <= 10
    assert abs(g.height() - 410) <= 10


def test_first_build_uses_default_when_no_saved_geometry(
    qtbot, isolated_settings, fake_viewer,
):
    """No prior geometry stored → window comes up with at least the
    declared minimum size (no crash, no zero-size, no negative coords).

    This is the pre-fix baseline behavior — must still hold after the
    fix to avoid regressing first-run users.
    """
    controller = _make_controller(fake_viewer)
    controller._current_index = 0
    controller._current_group_mask = controller._seg_labels == 1
    controller._group_image_buffer = controller._channel_image.copy()

    controller._build_qc_dock(initial_value=0.0)
    win = controller._qc_window
    qtbot.addWidget(win)
    qtbot.waitExposed(win)

    g = win.geometry()
    assert g.width() >= 350, "Falls back to the declared minimum width"
    assert g.height() >= 300, "Falls back to the declared minimum height"
