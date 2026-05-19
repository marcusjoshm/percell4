"""Tests for the AcceptDiluteMask use case (U5).

A Qt-free Creator that writes the final dilute-phase mask to /masks/<name>
and auto-selects it on the session. Mirrors the
``application/use_cases/accept_threshold.py`` pattern: store-write before
session mutation; refresh resource lists before set_active_mask.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import h5py
import numpy as np
import pytest

from percell4.application.session import Event, Session
from percell4.application.use_cases.accept_dilute_mask import AcceptDiluteMask
from percell4.domain.dataset import DatasetHandle


# ── Fakes ────────────────────────────────────────────────────


class FakeRepo:
    """Minimal in-memory DatasetRepository for AcceptDiluteMask tests."""

    def __init__(self) -> None:
        self.masks: dict[str, np.ndarray] = {}
        self.write_calls: list[tuple[str, np.ndarray]] = []

    def write_mask(self, handle, name, data, attrs=None):  # noqa: ARG002
        self.write_calls.append((name, data))
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


# ── Happy path: dispatch and persistence (covers AE-1) ───────


def test_execute_writes_uint8_mask_and_sets_active(session, repo, handle):
    """Bool mask is coerced to uint8; session.active_mask flips to the new name."""
    events: list[Event] = []
    session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append(Event.ACTIVE_MASK_CHANGED))

    mask = np.zeros((8, 8), dtype=bool)
    mask[2:5, 2:5] = True

    uc = AcceptDiluteMask(repo=repo, session=session)
    uc.execute(handle, "dilute_v1", mask)

    # On-disk shape: stored under "dilute_v1", uint8 dtype
    assert "dilute_v1" in repo.masks
    stored = repo.masks["dilute_v1"]
    assert stored.dtype == np.uint8
    assert stored.shape == (8, 8)
    np.testing.assert_array_equal(stored, mask.astype(np.uint8))

    # Session state updated
    assert session.active_mask == "dilute_v1"
    assert events == [Event.ACTIVE_MASK_CHANGED]


# ── Happy path: call order ───────────────────────────────────


def test_refresh_before_set_active_mask(handle):
    """refresh_resource_lists must precede set_active_mask so subscribers re-list
    the mask inventory before observing the new active value."""
    repo = MagicMock()
    repo.list_masks.return_value = ["dilute_v1"]

    session = MagicMock()

    uc = AcceptDiluteMask(repo=repo, session=session)
    mask = np.ones((4, 4), dtype=bool)
    uc.execute(handle, "dilute_v1", mask)

    # Build observed call sequence on (repo, session)
    expected = [
        ("repo.write_mask", call(handle, "dilute_v1", repo.write_mask.call_args[0][2])),
        ("repo.list_masks", call(handle)),
        ("session.refresh_resource_lists", call(mask_names=["dilute_v1"])),
        ("session.set_active_mask", call("dilute_v1")),
    ]
    # The simplest assertion: refresh_resource_lists invoked before set_active_mask
    refresh_idx = None
    set_idx = None
    for i, c in enumerate(session.mock_calls):
        if c == call.refresh_resource_lists(mask_names=["dilute_v1"]):
            refresh_idx = i
        elif c == call.set_active_mask("dilute_v1"):
            set_idx = i
    assert refresh_idx is not None and set_idx is not None
    assert refresh_idx < set_idx
    # And store.write_mask must run before either session mutation
    assert repo.write_mask.called
    # Sanity on the uint8 coercion at the write boundary
    written = repo.write_mask.call_args[0][2]
    assert written.dtype == np.uint8


# ── Edge cases: input validation ─────────────────────────────


def test_non_2d_input_raises_and_skips_store_write(session, handle):
    """1D or 3D input raises ValueError; store.write_mask is not invoked."""
    repo = MagicMock()
    uc = AcceptDiluteMask(repo=repo, session=session)

    bad_1d = np.ones((16,), dtype=bool)
    with pytest.raises(ValueError):
        uc.execute(handle, "bad", bad_1d)
    assert repo.write_mask.call_count == 0

    bad_3d = np.ones((2, 4, 4), dtype=bool)
    with pytest.raises(ValueError):
        uc.execute(handle, "bad", bad_3d)
    assert repo.write_mask.call_count == 0


def test_non_boolean_input_raises_and_skips_store_write(session, handle):
    """int32 (or any non-bool) input raises ValueError; no store write."""
    repo = MagicMock()
    uc = AcceptDiluteMask(repo=repo, session=session)

    bad_int = np.ones((8, 8), dtype=np.int32)
    with pytest.raises(ValueError):
        uc.execute(handle, "bad", bad_int)
    assert repo.write_mask.call_count == 0

    bad_uint8 = np.ones((8, 8), dtype=np.uint8)
    with pytest.raises(ValueError):
        uc.execute(handle, "bad", bad_uint8)
    assert repo.write_mask.call_count == 0


# ── Integration: real Hdf5DatasetRepository over tmp_path ────


def test_integration_real_repo_and_session(tmp_path):
    """End-to-end: write through a real Hdf5DatasetRepository and Session.

    Subscribers to ACTIVE_MASK_CHANGED fire exactly once, the on-disk mask
    is readable via h5py directly, and session.active_mask reflects the
    new mask.
    """
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5 = tmp_path / "ds.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    handle = DatasetHandle(path=h5, metadata={})
    session = Session()
    session.set_dataset(handle)

    fire_count = {"n": 0}
    session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: fire_count.__setitem__("n", fire_count["n"] + 1))

    repo = Hdf5DatasetRepository()
    uc = AcceptDiluteMask(repo=repo, session=session)

    mask = np.zeros((10, 10), dtype=bool)
    mask[1:4, 1:4] = True
    mask[6:9, 6:9] = True

    uc.execute(handle, "dilute_v1", mask)

    assert fire_count["n"] == 1
    assert session.active_mask == "dilute_v1"

    with h5py.File(h5, "r") as f:
        assert "masks/dilute_v1" in f
        on_disk = f["masks/dilute_v1"][...]
        assert on_disk.dtype == np.uint8
        assert on_disk.shape == (10, 10)
        np.testing.assert_array_equal(on_disk, mask.astype(np.uint8))
