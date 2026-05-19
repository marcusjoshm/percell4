"""Regression test: DilutePhaseMaskPanel must remember its last
position across workflow runs.

The launcher shows the panel as a top-level window with a default
size of 520x700. Without geometry persistence, every open re-centers
the window — the same painful pattern that already bit the Threshold
QC and Group Preview windows. Mirror the QSettings("LeeLabPerCell4",
"PerCell4") + saveGeometry / restoreGeometry pattern under the key
``dilute_phase_panel/geometry`` so the panel opens where the user
last left it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from qtpy.QtCore import Qt, QCoreApplication, QSettings


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Sandbox QSettings so the test doesn't bleed into user prefs.
    Same fixture shape as test_threshold_qc_geometry_persistence.py
    and test_session_window.py."""
    monkeypatch.setenv("HOME", str(tmp_path))
    QCoreApplication.setOrganizationName("LeeLabPerCell4")
    QCoreApplication.setApplicationName("PerCell4")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    QSettings("LeeLabPerCell4", "PerCell4").clear()
    yield tmp_path
    QSettings("LeeLabPerCell4", "PerCell4").clear()


def _make_panel(qtbot, tmp_path):
    """Spin up a DilutePhaseMaskPanel with a minimal but real
    Session + DatasetStore so validation passes; the panel itself
    is the surface under test."""
    from percell4.application.session import Session
    from percell4.domain.dataset import DatasetHandle
    from percell4.gui.workflows.dilute_phase.panel import (
        DilutePhaseMaskPanel,
    )
    from percell4.model import CellDataModel
    from percell4.store import DatasetStore

    path = tmp_path / "exp.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch0"]})
    store.write_array("intensity", np.zeros((8, 8), dtype=np.float32))

    session = Session()
    handle = DatasetHandle(path=path, metadata={"channel_names": ["ch0"]})
    session.set_dataset(handle)

    data_model = CellDataModel(session)

    viewer_win = MagicMock()
    viewer_win.viewer = MagicMock()
    viewer_win.viewer.layers = []

    def _get_active_seg_labels():
        return np.zeros((8, 8), dtype=np.int32)

    panel = DilutePhaseMaskPanel(
        parent=None,
        store=store,
        data_model=data_model,
        session=session,
        viewer_win=viewer_win,
        get_active_seg_labels=_get_active_seg_labels,
        handle=handle,
    )
    qtbot.addWidget(panel)
    # Mirror the launcher's window-promotion setup.
    panel.setWindowFlag(Qt.Window, True)
    panel.setWindowTitle("Dilute Phase Mask Generation")
    panel.resize(520, 700)
    return panel


def test_panel_geometry_persists_across_workflow_runs(
    qtbot, isolated_settings, tmp_path,
):
    """Move the panel, close it, reopen a fresh panel — the new
    panel comes up at the remembered position."""
    panel_a = _make_panel(qtbot, tmp_path)
    panel_a.show()
    qtbot.waitExposed(panel_a)

    panel_a.setGeometry(220, 90, 600, 760)
    qtbot.wait(50)
    panel_a.close()
    qtbot.wait(50)

    panel_b = _make_panel(qtbot, tmp_path)
    panel_b.show()
    qtbot.waitExposed(panel_b)

    g = panel_b.geometry()
    assert abs(g.x() - 220) <= 10, (
        f"Dilute panel should re-open at remembered x=220, got x={g.x()}"
    )
    assert abs(g.y() - 90) <= 10
    assert abs(g.width() - 600) <= 10
    assert abs(g.height() - 760) <= 10


def test_panel_first_open_uses_launcher_default_when_no_saved_geometry(
    qtbot, isolated_settings, tmp_path,
):
    """No prior geometry → the launcher's resize(520, 700) baseline
    wins (no zero-size, no center-snap that ignores the launcher)."""
    panel = _make_panel(qtbot, tmp_path)
    panel.show()
    qtbot.waitExposed(panel)

    g = panel.geometry()
    # Loose equality — Qt may pad by a frame or 1-2 px on some platforms.
    assert abs(g.width() - 520) <= 20
    assert abs(g.height() - 700) <= 20


def test_panel_uses_distinct_settings_key_from_qc_windows(
    qtbot, isolated_settings, tmp_path,
):
    """The dilute panel's settings key must not collide with the
    threshold_qc/* keys — moving the QC window mustn't move the
    dilute panel on next open, and vice versa."""
    panel = _make_panel(qtbot, tmp_path)
    panel.show()
    qtbot.waitExposed(panel)
    panel.setGeometry(150, 60, 540, 720)
    qtbot.wait(50)
    panel.close()

    qs = QSettings("LeeLabPerCell4", "PerCell4")
    keys = set(qs.allKeys())
    assert "dilute_phase_panel/geometry" in keys
    # Must NOT have overwritten the threshold_qc keys (they don't
    # exist in this isolated test, but the key must be distinct).
    assert "threshold_qc/geometry" not in keys
    assert "threshold_qc_preview/geometry" not in keys
