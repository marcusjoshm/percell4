"""Tests for the LoadDataset use case.

Tests use fakes for the repository and viewer — no Qt, no napari,
no HDF5. This is the pattern for all use case tests.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from percell4.application.session import Event, Session
from percell4.application.use_cases.load_dataset import LoadDataset
from percell4.domain.dataset import DatasetHandle, DatasetView


# ── Fakes ────────────────────────────────────────────────────


class FakeRepository:
    """In-memory DatasetRepository for testing."""

    def __init__(self, datasets: dict[Path, DatasetView] | None = None):
        self._datasets = datasets or {}
        self.opened: list[Path] = []

    def open(self, path: Path) -> DatasetHandle:
        if path not in self._datasets:
            raise FileNotFoundError(f"Dataset not found: {path}")
        self.opened.append(path)
        return DatasetHandle(path=path, metadata={"source": "fake"})

    def build_view(self, handle: DatasetHandle) -> DatasetView:
        return self._datasets[handle.path]


class FakeViewer:
    """In-memory ViewerPort for testing."""

    def __init__(self):
        self.shown: list[DatasetView] = []
        self.cleared: int = 0
        self.closed: bool = False

    def show_dataset(self, view: DatasetView) -> None:
        self.shown.append(view)

    def clear(self) -> None:
        self.cleared += 1

    def close(self) -> None:
        self.closed = True


# ── Test fixtures ────────────────────────────────────────────


def _sample_view() -> DatasetView:
    return DatasetView(
        channel_images={"GFP": np.zeros((10, 10), dtype=np.float32)},
        labels={"cellpose": np.zeros((10, 10), dtype=np.int32)},
        masks={},
    )


# ── Tests ────────────────────────────────────────────────────


class TestLoadDataset:
    """Verify the LoadDataset use case orchestration."""

    def test_happy_path(self):
        path = Path("/tmp/test.h5")
        view = _sample_view()
        repo = FakeRepository({path: view})
        viewer = FakeViewer()
        session = Session()

        uc = LoadDataset(repo, viewer, session)
        handle = uc.execute(path)

        # Repository was called
        assert repo.opened == [path]
        # Viewer received the view
        assert viewer.shown == [view]
        # Session was updated
        assert session.dataset is handle
        assert session.dataset.path == path

    def test_file_not_found_raises(self):
        repo = FakeRepository()
        viewer = FakeViewer()
        session = Session()

        uc = LoadDataset(repo, viewer, session)
        try:
            uc.execute(Path("/nonexistent.h5"))
            assert False, "Should have raised"
        except FileNotFoundError:
            pass

        # Session should not have been updated
        assert session.dataset is None
        # Viewer should not have been called
        assert viewer.shown == []

    def test_store_before_layer_invariant(self):
        """Session is set BEFORE viewer.show_dataset is called.

        This is the critical ordering invariant — viewer events during
        show_dataset may query the session for the active dataset.
        """
        path = Path("/tmp/test.h5")
        view = _sample_view()
        repo = FakeRepository({path: view})
        session = Session()

        # Track ordering: when did session get set vs viewer get called?
        order = []

        session.subscribe(Event.DATASET_CHANGED, lambda: order.append("session"))

        class OrderTrackingViewer:
            def show_dataset(self, v):
                order.append("viewer")

            def clear(self):
                pass

            def close(self):
                pass

        viewer = OrderTrackingViewer()
        uc = LoadDataset(repo, viewer, session)
        uc.execute(path)

        assert order == ["session", "viewer"]

    def test_loading_new_dataset_resets_selection(self):
        """Loading a dataset clears any previous selection/filter."""
        path = Path("/tmp/test.h5")
        view = _sample_view()
        repo = FakeRepository({path: view})
        viewer = FakeViewer()
        session = Session()

        # Set up some state
        session._selection = frozenset({1, 2, 3})
        session._active_segmentation = "old"

        uc = LoadDataset(repo, viewer, session)
        uc.execute(path)

        assert session.selection == frozenset()
        assert session.active_segmentation is None


class TestHdf5Repository:
    """Test the real Hdf5DatasetRepository against actual HDF5 files."""

    def test_open_and_build_view(self, tmp_h5, sample_image, sample_labels):
        """Repository can open an h5 file and build a DatasetView."""
        from percell4.adapters.hdf5_store import Hdf5DatasetRepository
        from percell4.store import DatasetStore

        # Create a real dataset
        store = DatasetStore(tmp_h5)
        store.create(metadata={"channel_names": ["GFP", "DAPI"]})

        # Write multi-channel intensity
        combined = np.stack([sample_image, sample_image * 0.5])
        store.write_array("intensity", combined)
        store.write_labels("cellpose", sample_labels)
        mask = (sample_labels > 0).astype(np.uint8)
        store.write_mask("threshold", mask)

        # Test through the port
        repo = Hdf5DatasetRepository()
        handle = repo.open(tmp_h5)

        assert handle.path == tmp_h5
        assert list(handle.metadata.get("channel_names")) == ["GFP", "DAPI"]

        view = repo.build_view(handle)

        assert "GFP" in view.channel_images
        assert "DAPI" in view.channel_images
        assert view.channel_images["GFP"].shape == (100, 100)
        assert "cellpose" in view.labels
        assert "threshold" in view.masks
        # Masks should not appear in labels
        assert "threshold" not in view.labels

    def test_open_nonexistent_raises(self, tmp_path):
        from percell4.adapters.hdf5_store import Hdf5DatasetRepository

        repo = Hdf5DatasetRepository()
        try:
            repo.open(tmp_path / "nonexistent.h5")
            assert False, "Should have raised"
        except FileNotFoundError:
            pass
