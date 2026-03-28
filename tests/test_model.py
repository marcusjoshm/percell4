"""Tests for CellDataModel."""

from __future__ import annotations

import pandas as pd
import pytest

from percell4.model import CellDataModel, StateChange


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


# ── state_changed signal tests ──────────────────────────────
#
# Each mutation must emit exactly one state_changed with the correct flags.
# These guard against the core regression: multi-signal emission.


def _capture_state_changes(model):
    """Connect a recorder to state_changed and return the list of captured changes."""
    changes = []
    model.state_changed.connect(lambda sc: changes.append(sc))
    return changes


def test_set_selection_emits_state_changed(model):
    """set_selection emits exactly one state_changed with selection=True."""
    changes = _capture_state_changes(model)
    model.set_selection([1, 2])

    assert len(changes) == 1
    sc = changes[0]
    assert sc.selection is True
    assert sc.data is False
    assert sc.filter is False
    assert sc.segmentation is False
    assert sc.mask is False


def test_set_filter_emits_state_changed(model):
    """set_filter emits exactly one state_changed with filter=True, selection=True."""
    changes = _capture_state_changes(model)
    model.set_filter([1, 2, 3])

    assert len(changes) == 1
    sc = changes[0]
    assert sc.filter is True
    assert sc.selection is True
    assert sc.data is False
    assert sc.segmentation is False
    assert sc.mask is False


def test_set_filter_clears_selection(model):
    """set_filter auto-clears selection."""
    model.set_selection([1, 2])
    assert model.selected_ids == [1, 2]

    model.set_filter([3, 4])

    assert model.selected_ids == []
    assert model.filtered_ids == {3, 4}


def test_set_filter_none_clears_filter(model):
    """set_filter(None) removes the filter."""
    model.set_filter([1, 2])
    assert model.is_filtered is True

    model.set_filter(None)

    assert model.is_filtered is False
    assert model.filtered_ids is None


def test_set_measurements_emits_state_changed(model):
    """set_measurements emits exactly one state_changed with data+filter+selection."""
    changes = _capture_state_changes(model)
    df = pd.DataFrame({"label": [1, 2], "area": [100, 200]})
    model.set_measurements(df)

    assert len(changes) == 1
    sc = changes[0]
    assert sc.data is True
    assert sc.filter is True
    assert sc.selection is True
    assert sc.segmentation is False
    assert sc.mask is False


def test_set_active_segmentation_emits_state_changed(model):
    """set_active_segmentation emits state_changed with segmentation=True."""
    changes = _capture_state_changes(model)
    model.set_active_segmentation("cellpose_labels")

    assert len(changes) == 1
    sc = changes[0]
    assert sc.segmentation is True
    assert sc.data is False


def test_set_active_segmentation_no_emit_when_unchanged(model):
    """set_active_segmentation does not emit if the name hasn't changed."""
    model.set_active_segmentation("seg")
    changes = _capture_state_changes(model)
    model.set_active_segmentation("seg")  # same name

    assert len(changes) == 0


def test_set_active_mask_emits_state_changed(model):
    """set_active_mask emits state_changed with mask=True."""
    changes = _capture_state_changes(model)
    model.set_active_mask("phasor_roi")

    assert len(changes) == 1
    sc = changes[0]
    assert sc.mask is True
    assert sc.data is False


def test_set_active_mask_no_emit_when_unchanged(model):
    """set_active_mask does not emit if the name hasn't changed."""
    model.set_active_mask("m")
    changes = _capture_state_changes(model)
    model.set_active_mask("m")  # same name

    assert len(changes) == 0


def test_clear_emits_state_changed(model):
    """clear() emits exactly one state_changed with all flags True."""
    df = pd.DataFrame({"label": [1], "area": [100]})
    model.set_measurements(df)
    model.set_selection([1])
    model.set_active_segmentation("seg")
    model.set_active_mask("mask")

    changes = _capture_state_changes(model)
    model.clear()

    assert len(changes) == 1
    sc = changes[0]
    assert sc.data is True
    assert sc.selection is True
    assert sc.filter is True
    assert sc.segmentation is True
    assert sc.mask is True


def test_state_changed_emits_before_legacy_signals(model):
    """state_changed fires BEFORE legacy signals (critical ordering invariant)."""
    order = []
    model.state_changed.connect(lambda sc: order.append("state_changed"))
    model.selection_changed.connect(lambda ids: order.append("selection_changed"))
    model.set_selection([1])

    assert order == ["state_changed", "selection_changed"]


def test_state_changed_emits_before_legacy_on_set_filter(model):
    """state_changed fires before filter_changed and selection_changed."""
    order = []
    model.state_changed.connect(lambda sc: order.append("state_changed"))
    model.filter_changed.connect(lambda: order.append("filter_changed"))
    model.selection_changed.connect(lambda ids: order.append("selection_changed"))
    model.set_filter([1, 2])

    assert order == ["state_changed", "filter_changed", "selection_changed"]


# ── filtered_df tests ────────────────────────────────────────


def test_filtered_df_returns_full_when_no_filter(model):
    """filtered_df returns full DataFrame when no filter is active."""
    df = pd.DataFrame({"label": [1, 2, 3], "area": [10, 20, 30]})
    model.set_measurements(df)

    assert len(model.filtered_df) == 3


def test_filtered_df_returns_subset_when_filtered(model):
    """filtered_df returns only rows matching the filter."""
    df = pd.DataFrame({"label": [1, 2, 3], "area": [10, 20, 30]})
    model.set_measurements(df)
    model.set_filter([1, 3])

    fdf = model.filtered_df
    assert len(fdf) == 2
    assert list(fdf["label"]) == [1, 3]


def test_filtered_df_cache_invalidated_by_set_filter(model):
    """filtered_df cache is invalidated when filter changes."""
    df = pd.DataFrame({"label": [1, 2, 3], "area": [10, 20, 30]})
    model.set_measurements(df)

    model.set_filter([1])
    fdf1 = model.filtered_df
    assert len(fdf1) == 1

    model.set_filter([1, 2])
    fdf2 = model.filtered_df
    assert len(fdf2) == 2
    assert fdf1 is not fdf2  # different cached object


def test_filtered_df_cache_invalidated_by_set_measurements(model):
    """set_measurements clears the filter and its cache."""
    df1 = pd.DataFrame({"label": [1, 2, 3], "area": [10, 20, 30]})
    model.set_measurements(df1)
    model.set_filter([1])

    df2 = pd.DataFrame({"label": [4, 5], "area": [40, 50]})
    model.set_measurements(df2)

    assert model.is_filtered is False
    assert len(model.filtered_df) == 2
