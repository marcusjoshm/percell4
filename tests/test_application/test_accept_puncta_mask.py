"""Tests for the AcceptPunctaMask use case (U2).

A Qt-free Creator that writes a puncta mask to /masks/<name> and auto-selects
it. Mirrors application/use_cases/accept_threshold.py: store-write before session
mutation; refresh resource lists before set_active_mask. Unlike AcceptDiluteMask
(bool-only), this coerces the detector's {0,1} uint8 output at the boundary.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import h5py
import numpy as np
import pytest

from percell4.application.session import Event, Session
from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask
from percell4.domain.dataset import DatasetHandle


class FakeRepo:
    """Minimal in-memory DatasetRepository for AcceptPunctaMask tests."""

    def __init__(self) -> None:
        self.masks: dict[str, np.ndarray] = {}

    def write_mask(self, handle, name, data, attrs=None):  # noqa: ARG002
        self.masks[name] = data

    def list_masks(self, handle):  # noqa: ARG002
        return sorted(self.masks.keys())


@pytest.fixture
def handle() -> DatasetHandle:
    return DatasetHandle(path=Path("/tmp/test.h5"), metadata={})


@pytest.fixture
def session(handle: DatasetHandle) -> Session:
    s = Session()
    s.set_dataset(handle)
    return s


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


def test_execute_writes_uint8_mask_and_sets_active(session, repo):
    events: list[Event] = []
    session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append(Event.ACTIVE_MASK_CHANGED))

    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:5, 2:5] = 1

    uc = AcceptPunctaMask(repo=repo, session=session)
    result = uc.execute(mask, "SG_adaptive")

    assert "SG_adaptive" in repo.masks
    stored = repo.masks["SG_adaptive"]
    assert stored.dtype == np.uint8
    assert set(np.unique(stored)).issubset({0, 1})
    np.testing.assert_array_equal(stored, mask)
    assert session.active_mask == "SG_adaptive"
    assert events == [Event.ACTIVE_MASK_CHANGED]
    assert result.mask_name == "SG_adaptive"
    assert result.n_positive == 9
    assert result.n_total == 64


def test_select_false_writes_and_refreshes_but_does_not_set_active(session, repo):
    """select=False writes the mask + refreshes the inventory but skips
    set_active_mask — used when saving several masks with exactly one active."""
    events: list[Event] = []
    session.subscribe(
        Event.ACTIVE_MASK_CHANGED, lambda: events.append(Event.ACTIVE_MASK_CHANGED)
    )
    uc = AcceptPunctaMask(repo=repo, session=session)
    result = uc.execute(np.ones((4, 4), dtype=np.uint8), "pop_low", select=False)

    assert "pop_low" in repo.masks           # written
    assert session.active_mask is None        # NOT selected
    assert events == []                       # no ACTIVE_MASK_CHANGED fired
    assert result.mask_name == "pop_low"


def test_two_masks_one_active_via_select_flag(session, repo):
    """Writing two masks with exactly one selected fires ACTIVE_MASK_CHANGED once
    and leaves the selected one active (the metric-segmenter low/high save)."""
    events: list[Event] = []
    session.subscribe(
        Event.ACTIVE_MASK_CHANGED, lambda: events.append(Event.ACTIVE_MASK_CHANGED)
    )
    uc = AcceptPunctaMask(repo=repo, session=session)
    uc.execute(np.ones((4, 4), dtype=np.uint8), "x_low", select=False)
    uc.execute(np.ones((4, 4), dtype=np.uint8), "x_high", select=True)

    assert {"x_low", "x_high"} <= set(repo.masks)
    assert session.active_mask == "x_high"
    assert events == [Event.ACTIVE_MASK_CHANGED]  # exactly one


def test_coerces_non_binary_input_to_0_1_uint8(session, repo):
    """A non-{0,1} array (e.g. labels / bool) is coerced to {0,1} uint8 at write."""
    labels = np.zeros((6, 6), dtype=np.int32)
    labels[1:3, 1:3] = 7  # >0 -> foreground
    uc = AcceptPunctaMask(repo=repo, session=session)
    uc.execute(labels, "m")
    stored = repo.masks["m"]
    assert stored.dtype == np.uint8
    assert set(np.unique(stored)).issubset({0, 1})
    assert int(stored.sum()) == 4


def test_refresh_before_set_active_mask(session):
    repo = MagicMock()
    repo.list_masks.return_value = ["SG_adaptive"]
    sess = MagicMock()
    sess.dataset = session.dataset

    uc = AcceptPunctaMask(repo=repo, session=sess)
    uc.execute(np.ones((4, 4), dtype=np.uint8), "SG_adaptive")

    refresh_idx = set_idx = None
    for i, c in enumerate(sess.mock_calls):
        if c == call.refresh_resource_lists(mask_names=["SG_adaptive"]):
            refresh_idx = i
        elif c == call.set_active_mask("SG_adaptive"):
            set_idx = i
    assert refresh_idx is not None and set_idx is not None
    assert refresh_idx < set_idx
    assert repo.write_mask.called
    assert repo.write_mask.call_args[0][2].dtype == np.uint8


def test_empty_name_raises_and_skips_write(session):
    repo = MagicMock()
    uc = AcceptPunctaMask(repo=repo, session=session)
    with pytest.raises(ValueError):
        uc.execute(np.ones((4, 4), dtype=np.uint8), "")
    assert repo.write_mask.call_count == 0


def test_no_dataset_raises(repo):
    from percell4.domain.errors import NoDatasetError

    session = Session()  # no dataset set
    uc = AcceptPunctaMask(repo=repo, session=session)
    with pytest.raises(NoDatasetError):
        uc.execute(np.ones((4, 4), dtype=np.uint8), "m")


def test_integration_real_repo_and_session(tmp_path):
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5 = tmp_path / "ds.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    handle = DatasetHandle(path=h5, metadata={})
    session = Session()
    session.set_dataset(handle)

    fire = {"n": 0}
    session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: fire.__setitem__("n", fire["n"] + 1))

    repo = Hdf5DatasetRepository()
    uc = AcceptPunctaMask(repo=repo, session=session)

    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[1:4, 1:4] = 1
    uc.execute(mask, "SG_adaptive")

    assert fire["n"] == 1
    assert session.active_mask == "SG_adaptive"
    with h5py.File(h5, "r") as f:
        assert "masks/SG_adaptive" in f
        on_disk = f["masks/SG_adaptive"][...]
        assert on_disk.dtype == np.uint8
        np.testing.assert_array_equal(on_disk, mask)
