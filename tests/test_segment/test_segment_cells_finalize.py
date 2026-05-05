"""Tests for SegmentCells.finalize edge-removal toggle."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.segment_cells import SegmentCells
from percell4.domain.dataset import DatasetHandle


class FakeRepo:
    def __init__(self) -> None:
        self.labels: dict[str, np.ndarray] = {}
        self.masks: dict[str, np.ndarray] = {}

    def write_labels(self, handle, name, data) -> None:
        self.labels[name] = data

    def list_labels(self, handle):
        return list(self.labels.keys())

    def list_masks(self, handle):
        return list(self.masks.keys())


@pytest.fixture
def session():
    s = Session()
    s.set_dataset(DatasetHandle(path=Path("/tmp/test.h5")))
    return s


@pytest.fixture
def edge_and_interior_masks():
    """Two cells: one touches the border, one does not."""
    masks = np.zeros((20, 20), dtype=np.int32)
    # Edge cell — touches top row
    masks[0:5, 0:5] = 1
    # Interior cell — well away from any border
    masks[8:14, 8:14] = 2
    return masks


class TestFinalizeEdgeRemoval:
    def test_default_removes_edge_cells(self, session, edge_and_interior_masks):
        repo = FakeRepo()
        uc = SegmentCells(repo, session)

        result = uc.finalize(edge_and_interior_masks, min_area=0)

        assert result.edge_removed == 1
        assert result.n_cells == 1  # only the interior cell survives
        # Edge cell pixels were zeroed out
        assert not np.any(result.labels[0:5, 0:5] != 0)

    def test_remove_edge_cells_false_keeps_edge_cells(
        self, session, edge_and_interior_masks
    ):
        repo = FakeRepo()
        uc = SegmentCells(repo, session)

        result = uc.finalize(
            edge_and_interior_masks, min_area=0, remove_edge_cells=False
        )

        assert result.edge_removed == 0
        assert result.n_cells == 2  # both cells survive
        # Edge cell pixels still present (relabeled but non-zero)
        assert np.any(result.labels[0:5, 0:5] != 0)

    def test_remove_edge_cells_false_does_not_mutate_input(
        self, session, edge_and_interior_masks
    ):
        original = edge_and_interior_masks.copy()
        repo = FakeRepo()
        uc = SegmentCells(repo, session)

        uc.finalize(edge_and_interior_masks, min_area=0, remove_edge_cells=False)

        assert np.array_equal(edge_and_interior_masks, original)
