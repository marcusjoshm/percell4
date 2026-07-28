"""Regression test: ThresholdQCController's per-group QC window must
remember its last position.

Bug 2026-05-18: every group transition built a fresh QMainWindow via
``_build_qc_dock`` without calling ``saveGeometry`` / ``restoreGeometry``,
so the user moved the window for Group 1 and it re-centered for Group 2,
and again for Group 3, ad infinitum. Same after a fresh workflow run.

The codebase's other windows (SessionWindow, PhasorPlot, DataPlot,
CellTable, LauncherWindow, ViewerWindow) all use the canonical
``app_settings()`` + ``saveGeometry`` /
``restoreGeometry`` pattern with a ``"<window>/geometry"`` key. This
test pins the same pattern for ``threshold_qc/geometry``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from percell4.domain.measure.grouper import GroupingResult
from percell4.gui.settings import app_settings


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
    viewer_win.existing_viewer = viewer
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
    qtbot, fake_viewer,
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

    # Move it somewhere distinctive. Capture what the window manager actually
    # granted rather than what we asked for: a decorating WM (macOS adds a
    # ~16px title bar) shifts the frame, so comparing Group 2 against the
    # requested numbers tests the WM, not the persistence behaviour.
    win1.setGeometry(150, 50, 420, 380)
    qtbot.wait(50)
    expected = win1.geometry()

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
    assert abs(g.x() - expected.x()) <= 10, (
        f"Group 2's QC window should open where Group 1's was moved to "
        f"(x={expected.x()}), got x={g.x()}"
    )
    assert abs(g.y() - expected.y()) <= 10, (
        f"expected y≈{expected.y()}, got {g.y()}"
    )
    assert abs(g.width() - expected.width()) <= 10
    assert abs(g.height() - expected.height()) <= 10


def test_geometry_persists_across_workflow_runs(
    qtbot, fake_viewer,
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
    qtbot, fake_viewer,
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


# ── Group Preview window geometry persistence ───────────────────────


def test_preview_window_geometry_persists_across_workflow_runs(
    qtbot, fake_viewer,
):
    """Same QSettings persistence for the Group Preview window. The
    preview opens once per workflow run (before per-group QC begins),
    so the relevant scenario is run-to-run, not group-to-group."""
    # First run: open the preview, move it, close it.
    controller_a = _make_controller(fake_viewer)
    controller_a._build_preview_dock()
    win_a = controller_a._preview_window
    qtbot.addWidget(win_a)
    qtbot.waitExposed(win_a)

    win_a.setGeometry(180, 70, 540, 470)
    qtbot.wait(50)
    controller_a._close_preview_window()
    qtbot.wait(50)

    # Second run: brand-new controller — the preview opens at the
    # remembered position.
    controller_b = _make_controller(fake_viewer)
    controller_b._build_preview_dock()
    win_b = controller_b._preview_window
    qtbot.addWidget(win_b)
    qtbot.waitExposed(win_b)

    g = win_b.geometry()
    assert abs(g.x() - 180) <= 10, (
        f"Preview window should re-open at the remembered x=180, "
        f"got x={g.x()}"
    )
    assert abs(g.y() - 70) <= 10
    assert abs(g.width() - 540) <= 10
    assert abs(g.height() - 470) <= 10


def test_preview_first_build_uses_default_when_no_saved_geometry(
    qtbot, fake_viewer,
):
    """First-ever preview build with no saved geometry → falls back
    to the declared minimum size (500x450)."""
    controller = _make_controller(fake_viewer)
    controller._build_preview_dock()
    win = controller._preview_window
    qtbot.addWidget(win)
    qtbot.waitExposed(win)

    g = win.geometry()
    assert g.width() >= 500
    assert g.height() >= 450


def test_qc_and_preview_use_distinct_settings_keys(
    qtbot, fake_viewer,
):
    """The two windows must persist under different keys so moving
    one doesn't reposition the other on the next open. Verifies the
    contract via QSettings.allKeys() rather than guessing internals."""
    controller = _make_controller(fake_viewer)

    # Open + move + close the preview window first.
    controller._build_preview_dock()
    pw = controller._preview_window
    qtbot.addWidget(pw)
    qtbot.waitExposed(pw)
    pw.setGeometry(100, 50, 510, 460)
    qtbot.wait(50)
    controller._close_preview_window()

    # Open + move + close the QC window with a clearly different
    # geometry.
    controller._current_index = 0
    controller._current_group_mask = controller._seg_labels == 1
    controller._group_image_buffer = controller._channel_image.copy()
    controller._build_qc_dock(initial_value=0.0)
    qw = controller._qc_window
    qtbot.addWidget(qw)
    qtbot.waitExposed(qw)
    qw.setGeometry(400, 200, 360, 320)
    qtbot.wait(50)
    controller._remove_qc_dock()

    # Both keys should exist; they must be distinct.
    qs = app_settings()
    keys = set(qs.allKeys())
    assert "threshold_qc/geometry" in keys
    assert "threshold_qc_preview/geometry" in keys

    # Sanity: the QSettings values for the two keys are NOT equal
    # (different geometries went in).
    qc_geom = qs.value("threshold_qc/geometry")
    preview_geom = qs.value("threshold_qc_preview/geometry")
    assert qc_geom != preview_geom
