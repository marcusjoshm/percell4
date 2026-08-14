"""Verify DataPlot and CellTable invalidate their content caches on DATASET_CHANGED.

Both peer views subscribe to DATASET_CHANGED via _on_data_changed; the
handler reloads from session.df which becomes empty after set_dataset.
The scatter arrays clear and the table model takes an empty DataFrame.
"""

from __future__ import annotations

import pandas as pd
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.cell_table import CellTableWindow
from percell4.interfaces.gui.peer_views.data_plot import DataPlotWindow


@pytest.fixture
def session_with_data():
    session = Session()
    session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={
        "channel_names": ["mNG"], "mask_names": ["m1"],
    }))
    df = pd.DataFrame({
        "label": [1, 2, 3], "area": [10.0, 20.0, 30.0], "mean_intensity": [0.1, 0.2, 0.3],
    })
    session.set_measurements(df)
    return session


def test_data_plot_clears_on_dataset_switch(qtbot, session_with_data):
    win = DataPlotWindow(session_with_data)
    qtbot.addWidget(win)
    # Force a refresh so scatter is populated
    win._refresh_plot()
    # Verify there's data before the switch
    assert not session_with_data.df.empty

    # Switch to a new dataset
    session_with_data.set_dataset(DatasetHandle(path="/tmp/y.h5", metadata={
        "channel_names": ["CA-SiR"], "mask_names": ["m2"],
    }))
    qtbot.wait(20)

    # session.df is empty; DataPlot should reflect that
    assert session_with_data.df.empty
    # Status bar reflects the cleared state
    assert win._status.currentMessage() == "No data"


def test_cell_table_clears_on_dataset_switch(qtbot, session_with_data):
    win = CellTableWindow(session_with_data)
    qtbot.addWidget(win)
    # Verify table has data before the switch
    assert win._model.rowCount() == 3

    session_with_data.set_dataset(DatasetHandle(path="/tmp/y.h5", metadata={
        "channel_names": ["CA-SiR"],
    }))
    qtbot.wait(20)

    # Table should be empty after the switch (session.df is empty)
    assert win._model.rowCount() == 0
    assert win._status.currentMessage() == "No data"


def test_data_plot_clears_on_set_dataset_none(qtbot, session_with_data):
    win = DataPlotWindow(session_with_data)
    qtbot.addWidget(win)
    win._refresh_plot()

    session_with_data.set_dataset(None)
    qtbot.wait(20)

    assert session_with_data.df.empty
    assert win._status.currentMessage() == "No data"


def test_cell_table_clears_on_clear(qtbot, session_with_data):
    win = CellTableWindow(session_with_data)
    qtbot.addWidget(win)
    assert win._model.rowCount() == 3

    session_with_data.clear()
    qtbot.wait(20)

    assert win._model.rowCount() == 0
