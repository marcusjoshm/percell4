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
    def __init__(self, native_shape=None) -> None:
        self.labels: dict[str, np.ndarray] = {}
        self.masks: dict[str, np.ndarray] = {}
        self.label_attrs: dict[str, dict] = {}
        self._native_shape = native_shape

    def write_labels(self, handle, name, data, attrs=None) -> None:
        self.labels[name] = data
        if attrs:
            self.label_attrs[name] = dict(attrs)

    def list_labels(self, handle):
        return list(self.labels.keys())

    def list_masks(self, handle):
        return list(self.masks.keys())

    def read_metadata(self, handle):
        return (
            {"native_shape": self._native_shape}
            if self._native_shape is not None
            else {}
        )


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


# ── view_bin handling (U12) ───────────────────────────────────────


class TestFinalizeViewBin:
    def test_default_view_bin_one_no_upsample_no_attr(
        self, session, edge_and_interior_masks
    ):
        """At view_bin=1, behavior is byte-identical to pre-U12 finalize:
        no upsample, no created_at_bin attr."""
        repo = FakeRepo()
        uc = SegmentCells(repo, session)

        result = uc.finalize(
            edge_and_interior_masks, min_area=0, remove_edge_cells=False
        )

        assert result.seg_name in repo.labels
        assert repo.labels[result.seg_name].shape == (20, 20)
        assert result.seg_name not in repo.label_attrs

    def test_view_bin_3_upsamples_to_native(self, session):
        """A 4x4 labels result at view_bin=3 NN-upsamples to native 12x12."""
        repo = FakeRepo(native_shape=(12, 12))
        uc = SegmentCells(repo, session)

        binned_labels = np.array(
            [
                [1, 1, 0, 0],
                [1, 1, 0, 0],
                [0, 0, 2, 2],
                [0, 0, 2, 2],
            ],
            dtype=np.int32,
        )

        result = uc.finalize(
            binned_labels, min_area=0, remove_edge_cells=False, view_bin=3
        )

        stored = repo.labels[result.seg_name]
        assert stored.shape == (12, 12)
        # Top-left 6x6 should all be label 1 after NN upsample.
        assert (stored[0:6, 0:6] == 1).all()

    def test_view_bin_gt_one_stamps_created_at_bin_attr(self, session):
        """At view_bin > 1, the stored layer carries created_at_bin=k."""
        repo = FakeRepo(native_shape=(12, 12))
        uc = SegmentCells(repo, session)

        binned = np.array(
            [[1, 1], [1, 1]], dtype=np.int32,
        )
        result = uc.finalize(
            binned, min_area=0, remove_edge_cells=False, view_bin=3
        )

        assert repo.label_attrs[result.seg_name]["created_at_bin"] == 3

    def test_view_bin_gt_one_names_with_bin_suffix(self, session):
        """Auto-derived default name carries _bin<k> when view_bin > 1."""
        repo = FakeRepo(native_shape=(12, 12))
        uc = SegmentCells(repo, session)

        binned = np.array([[1, 1], [1, 1]], dtype=np.int32)
        result = uc.finalize(
            binned, min_area=0, remove_edge_cells=False, view_bin=3
        )

        assert result.seg_name.endswith("_bin3")

    def test_view_bin_gt_one_explicit_name_used_verbatim(self, session):
        """Caller's explicit name wins over auto-suffix (the GUI already
        seeded the prompt with bin_suffix at U5)."""
        repo = FakeRepo(native_shape=(12, 12))
        uc = SegmentCells(repo, session)

        binned = np.array([[1, 1], [1, 1]], dtype=np.int32)
        result = uc.finalize(
            binned,
            min_area=0,
            remove_edge_cells=False,
            view_bin=3,
            name="user_chosen_name",
        )

        assert result.seg_name == "user_chosen_name"
        assert repo.label_attrs[result.seg_name]["created_at_bin"] == 3

    def test_view_bin_gt_one_no_native_shape_raises(self, session):
        """Cannot upsample without knowing the target shape."""
        from percell4.domain.errors import NoDatasetError

        repo = FakeRepo(native_shape=None)
        uc = SegmentCells(repo, session)

        binned = np.array([[1, 1], [1, 1]], dtype=np.int32)
        with pytest.raises(NoDatasetError, match="native_shape"):
            uc.finalize(
                binned, min_area=0, remove_edge_cells=False, view_bin=3
            )
