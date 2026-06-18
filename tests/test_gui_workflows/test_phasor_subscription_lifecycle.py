"""Regression tests for the phasor plot staying live with session state.

Covers two root causes of "the phasor only updates on in-window actions":

* Bug B — ``closeEvent`` tore down Session subscriptions and ``showEvent``
  never rebuilt them, so a reopened window went deaf to FILTER_CHANGED etc.
* Bug A — ``_try_auto_load_cached`` fed ``labels=None``, so the
  cell-selection filter was a silent no-op until the user recomputed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.load_cached_phasor import CachedPhasorResult
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    sess = Session()
    sess._dataset = DatasetHandle(path=tmp_path / "fake.h5", metadata={})
    sess._active_channel = "ch1"
    return sess


def _maps_with_labels():
    g = np.full((8, 8), 0.5, dtype=np.float32)
    s = np.full((8, 8), 0.3, dtype=np.float32)
    labels = np.zeros((8, 8), dtype=np.int32)
    labels[:4, :] = 1  # 32 px
    labels[4:, :] = 2  # 32 px
    return g, s, labels


# ── Bug B: subscription survives close → reopen ───────────────


def test_filter_refreshes_while_subscribed(qtbot, session_with_dataset):
    win = PhasorPlotWindow(session_with_dataset)
    qtbot.addWidget(win)
    g, s, labels = _maps_with_labels()
    win.set_phasor_data(g, s, labels=labels)
    assert "64" in win._status.currentMessage()

    session_with_dataset.set_filter(frozenset({1}))
    qtbot.wait(300)
    assert "32" in win._status.currentMessage(), win._status.currentMessage()


def test_filter_still_refreshes_after_close_reopen(qtbot, session_with_dataset):
    win = PhasorPlotWindow(session_with_dataset)
    qtbot.addWidget(win)
    g, s, labels = _maps_with_labels()
    win.set_phasor_data(g, s, labels=labels)

    win.close()   # closeEvent unsubscribes + clears _unsubs
    win.show()    # showEvent must re-subscribe
    qtbot.wait(50)
    assert win._unsubs, "subscriptions were not re-established on show"

    session_with_dataset.set_filter(frozenset({1}))
    qtbot.wait(300)
    assert "32" in win._status.currentMessage(), win._status.currentMessage()


def test_reopen_resyncs_filter_set_while_hidden(qtbot, session_with_dataset):
    """A filter set while the window was closed is reflected on reopen."""
    win = PhasorPlotWindow(session_with_dataset)
    qtbot.addWidget(win)
    g, s, labels = _maps_with_labels()
    win.set_phasor_data(g, s, labels=labels)

    win.close()
    session_with_dataset.set_filter(frozenset({1}))  # missed while hidden
    win.show()
    qtbot.wait(300)
    assert "32" in win._status.currentMessage(), win._status.currentMessage()


# ── Bug A: auto-load supplies labels so the cell filter engages live ──


def test_auto_load_populates_labels_so_filter_works(qtbot, session_with_dataset):
    g, s, labels = _maps_with_labels()
    repo = MagicMock()
    result = CachedPhasorResult(
        g_map=g, s_map=s, g_filtered=None, s_filtered=None, intensity=None, channel="ch1",
    )

    win = PhasorPlotWindow(
        session_with_dataset,
        get_repo=lambda: repo,
        get_seg_labels=lambda: labels,
    )
    qtbot.addWidget(win)

    with patch(
        "percell4.application.use_cases.load_cached_phasor.LoadCachedPhasor"
    ) as MockUC:
        MockUC.return_value.execute.return_value = result
        win._try_auto_load_cached()

    assert win._labels_flat is not None, "auto-load did not populate labels"

    session_with_dataset.set_filter(frozenset({1}))
    qtbot.wait(300)
    assert "32" in win._status.currentMessage(), win._status.currentMessage()


def test_auto_load_shape_mismatch_falls_back_to_none(qtbot, session_with_dataset):
    """Misaligned labels (e.g. a bin mismatch) degrade safely to None."""
    g, s, _ = _maps_with_labels()
    repo = MagicMock()
    result = CachedPhasorResult(
        g_map=g, s_map=s, g_filtered=None, s_filtered=None, intensity=None, channel="ch1",
    )
    wrong_shape_labels = np.ones((4, 4), dtype=np.int32)

    win = PhasorPlotWindow(
        session_with_dataset,
        get_repo=lambda: repo,
        get_seg_labels=lambda: wrong_shape_labels,
    )
    qtbot.addWidget(win)

    with patch(
        "percell4.application.use_cases.load_cached_phasor.LoadCachedPhasor"
    ) as MockUC:
        MockUC.return_value.execute.return_value = result
        win._try_auto_load_cached()

    assert win._labels_flat is None
