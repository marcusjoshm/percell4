"""Tests for segmentation post-processing filters."""

from __future__ import annotations

import numpy as np

from percell4.domain.segmentation.postprocess import (
    filter_edge_cells,
    filter_small_cells,
    relabel_sequential,
)


def _make_labels():
    """Create a 50x50 label array with 4 cells, 2 touching edges."""
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[0:10, 5:15] = 1   # touches top edge
    labels[20:30, 20:30] = 2  # interior, area=100
    labels[15:18, 15:18] = 3  # interior, area=9 (small)
    labels[40:50, 35:45] = 4  # touches bottom edge
    return labels


# ── filter_edge_cells ─────────────────────────────────────────


def test_filter_edge_removes_border_cells():
    """Cells touching the border are removed."""
    labels = _make_labels()
    result, count = filter_edge_cells(labels)
    assert count == 2  # cells 1 and 4
    assert 1 not in result
    assert 4 not in result
    assert 2 in result
    assert 3 in result


def test_filter_edge_with_margin():
    """Edge margin extends the border region."""
    labels = np.zeros((20, 20), dtype=np.int32)
    labels[2:5, 2:5] = 1  # 2 pixels from edge
    labels[10:15, 10:15] = 2  # interior

    result, count = filter_edge_cells(labels, edge_margin=2)
    assert count == 1  # cell 1 within margin
    assert 1 not in result
    assert 2 in result


def test_filter_edge_no_border_cells():
    """No cells touch the border → nothing removed."""
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[10:20, 10:20] = 1
    result, count = filter_edge_cells(labels)
    assert count == 0
    np.testing.assert_array_equal(result, labels)


def test_filter_edge_does_not_mutate_input():
    """Input array is not modified."""
    labels = _make_labels()
    original = labels.copy()
    filter_edge_cells(labels)
    np.testing.assert_array_equal(labels, original)


# ── filter_small_cells ────────────────────────────────────────


def test_filter_small_removes_tiny_cells():
    """Cells below min_area are removed."""
    labels = _make_labels()
    result, count = filter_small_cells(labels, min_area=50)
    assert count == 1  # cell 3 (area=9)
    assert 3 not in result
    assert 2 in result  # area=100, kept


def test_filter_small_keeps_all_above_threshold():
    """All cells above threshold are kept."""
    labels = _make_labels()
    result, count = filter_small_cells(labels, min_area=5)
    assert count == 0


def test_filter_small_does_not_mutate_input():
    """Input array is not modified."""
    labels = _make_labels()
    original = labels.copy()
    filter_small_cells(labels, min_area=50)
    np.testing.assert_array_equal(labels, original)


# ── relabel_sequential ────────────────────────────────────────


def test_relabel_fills_gaps():
    """Labels [1, 3, 7] → [1, 2, 3]."""
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[0:3, 0:3] = 1
    labels[4:7, 4:7] = 3
    labels[7:10, 7:10] = 7

    result = relabel_sequential(labels)
    unique = np.unique(result)
    assert set(unique) == {0, 1, 2, 3}


def test_relabel_already_sequential():
    """Already sequential labels are unchanged."""
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[0:3, 0:3] = 1
    labels[4:7, 4:7] = 2
    labels[7:10, 7:10] = 3

    result = relabel_sequential(labels)
    np.testing.assert_array_equal(result, labels)


def test_relabel_empty():
    """All-zero labels (no cells) returned as-is."""
    labels = np.zeros((10, 10), dtype=np.int32)
    result = relabel_sequential(labels)
    np.testing.assert_array_equal(result, labels)


def test_relabel_does_not_mutate_input():
    """Input array is not modified."""
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[0:5, 0:5] = 5
    original = labels.copy()
    relabel_sequential(labels)
    np.testing.assert_array_equal(labels, original)


# ── Composition ───────────────────────────────────────────────


def test_filter_then_relabel():
    """Edge filter → small filter → relabel produces sequential IDs."""
    labels = _make_labels()  # cells 1(edge), 2(big), 3(small), 4(edge)

    filtered, _ = filter_edge_cells(labels)
    filtered, _ = filter_small_cells(filtered, min_area=50)
    result = relabel_sequential(filtered)

    # Only cell 2 survives → relabeled to 1
    unique = np.unique(result)
    assert set(unique) == {0, 1}


def test_multi_item_filter():
    """Test with N>=2 items to catch last-item-only bugs."""
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[0:5, 0:5] = 1    # edge
    labels[10:15, 10:15] = 2  # interior, area=25
    labels[20:22, 20:22] = 3  # interior, area=4 (small)
    labels[30:40, 30:40] = 4  # interior, area=100
    labels[45:50, 45:50] = 5  # edge

    filtered, edge_count = filter_edge_cells(labels)
    assert edge_count == 2

    filtered, small_count = filter_small_cells(filtered, min_area=10)
    assert small_count == 1

    result = relabel_sequential(filtered)
    unique = sorted(np.unique(result))
    assert unique == [0, 1, 2]  # cells 2 and 4 survive, relabeled to 1 and 2
