"""Tests for CellDataModel."""

from __future__ import annotations

import pandas as pd
import pytest

from percell4.model import CellDataModel


@pytest.fixture
def model(qtbot):
    """Create a CellDataModel instance."""
    m = CellDataModel()
    return m


def test_initial_state(model):
    """Model starts with empty DataFrame and no selection."""
    assert model.df.empty
    assert model.selected_ids == []


def test_set_measurements_emits_signal(model, qtbot):
    """set_measurements replaces the DataFrame and emits data_updated."""
    df = pd.DataFrame({"label": [1, 2, 3], "area": [100, 200, 300]})

    with qtbot.waitSignal(model.data_updated, timeout=1000):
        model.set_measurements(df)

    assert len(model.df) == 3
    assert list(model.df["label"]) == [1, 2, 3]


def test_set_selection_emits_signal(model, qtbot):
    """set_selection updates selected_ids and emits selection_changed."""
    with qtbot.waitSignal(model.selection_changed, timeout=1000):
        model.set_selection([2, 5])

    assert model.selected_ids == [2, 5]


def test_set_selection_empty(model, qtbot):
    """Setting empty selection (deselect all) works."""
    model.set_selection([1, 2])

    with qtbot.waitSignal(model.selection_changed, timeout=1000):
        model.set_selection([])

    assert model.selected_ids == []


def test_clear_resets_state(model, qtbot):
    """clear() empties DataFrame, clears selection, emits both signals."""
    df = pd.DataFrame({"label": [1, 2], "area": [100, 200]})
    model.set_measurements(df)
    model.set_selection([1])

    signals = []
    model.data_updated.connect(lambda: signals.append("data"))
    model.selection_changed.connect(lambda ids: signals.append("selection"))

    model.clear()

    assert model.df.empty
    assert model.selected_ids == []
    assert "data" in signals
    assert "selection" in signals


def test_df_is_read_only_reference(model):
    """The df property returns the same object, not a copy."""
    df = pd.DataFrame({"label": [1], "area": [100]})
    model.set_measurements(df)

    # Accessing .df twice should return the same object
    assert model.df is model.df


def test_set_selection_copies_input(model):
    """set_selection makes a copy of the input list."""
    ids = [1, 2, 3]
    model.set_selection(ids)
    ids.append(4)  # mutate original
    assert model.selected_ids == [1, 2, 3]  # model's copy unchanged
