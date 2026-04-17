"""Tests for application use cases.

All tests use fakes for ports — no Qt, no napari, no HDF5.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.application.session import Event, Session
from percell4.application.use_cases.accept_threshold import AcceptThreshold
from percell4.application.use_cases.close_dataset import CloseDataset
from percell4.application.use_cases.measure_cells import MeasureCells
from percell4.domain.dataset import DatasetHandle


# ── Fakes ────────────────────────────────────────────────────


class FakeViewer:
    def __init__(self):
        self.cleared = 0
        self.shown = []

    def show_dataset(self, view):
        self.shown.append(view)

    def clear(self):
        self.cleared += 1

    def close(self):
        pass


class FakeRepo:
    """In-memory DatasetRepository for testing use cases."""

    def __init__(self):
        self.channel_images: dict[str, np.ndarray] = {}
        self.labels: dict[str, np.ndarray] = {}
        self.masks: dict[str, np.ndarray] = {}
        self.written_measurements: pd.DataFrame | None = None
        self.written_masks: dict[str, np.ndarray] = {}
        self.written_arrays: dict[str, np.ndarray] = {}
        self.group_columns: pd.DataFrame | None = None

    def open(self, path):
        return DatasetHandle(path=path)

    def build_view(self, handle):
        pass

    def read_channel_images(self, handle):
        return self.channel_images

    def read_labels(self, handle, name):
        if name not in self.labels:
            raise KeyError(f"Labels not found: {name}")
        return self.labels[name]

    def list_labels(self, handle):
        return list(self.labels.keys())

    def read_mask(self, handle, name):
        if name not in self.masks:
            raise KeyError(f"Mask not found: {name}")
        return self.masks[name]

    def write_mask(self, handle, name, data):
        self.written_masks[name] = data

    def list_masks(self, handle):
        return list(self.masks.keys())

    def write_measurements(self, handle, df):
        self.written_measurements = df

    def read_measurements(self, handle):
        return self.written_measurements

    def write_array(self, handle, path, data, attrs=None):
        self.written_arrays[path] = data

    def read_array(self, handle, path):
        if path not in self.written_arrays:
            raise KeyError(f"Array not found: {path}")
        return self.written_arrays[path]

    def read_group_columns(self, handle):
        return self.group_columns


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def session():
    s = Session()
    s.set_dataset(DatasetHandle(path=Path("/tmp/test.h5")))
    return s


@pytest.fixture
def sample_labels():
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[5:15, 5:15] = 1
    labels[5:15, 25:35] = 2
    labels[25:35, 5:15] = 3
    return labels


@pytest.fixture
def sample_image():
    image = np.full((50, 50), 10.0, dtype=np.float32)
    image[5:15, 5:15] = 100.0
    image[5:15, 25:35] = 200.0
    image[25:35, 5:15] = 150.0
    return image


# ── CloseDataset ─────────────────────────────────────────────


class TestCloseDataset:
    def test_clears_session_and_viewer(self, session):
        viewer = FakeViewer()
        uc = CloseDataset(viewer, session)
        uc.execute()

        assert session.dataset is None
        assert viewer.cleared == 1


# ── MeasureCells ─────────────────────────────────────────────


class TestMeasureCells:
    def test_happy_path(self, session, sample_labels, sample_image):
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        session.set_active_segmentation("cellpose")

        uc = MeasureCells(repo, session)
        df = uc.execute(metrics=["mean_intensity", "area"])

        assert len(df) == 3
        assert "label" in df.columns
        assert "area" in df.columns
        assert "GFP_mean_intensity" in df.columns
        # Written to store
        assert repo.written_measurements is not None
        assert len(repo.written_measurements) == 3
        # Session updated
        assert len(session.df) == 3

    def test_no_dataset_raises(self):
        session = Session()
        repo = FakeRepo()
        uc = MeasureCells(repo, session)

        with pytest.raises(ValueError, match="No dataset loaded"):
            uc.execute(metrics=["area"])

    def test_no_segmentation_raises(self, session, sample_image):
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        uc = MeasureCells(repo, session)

        with pytest.raises(ValueError, match="No active segmentation"):
            uc.execute(metrics=["area"])

    def test_with_mask(self, session, sample_labels, sample_image):
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        mask = np.ones_like(sample_labels, dtype=np.uint8)
        repo.masks = {"threshold": mask}
        session.set_active_segmentation("cellpose")
        session.set_active_mask("threshold")

        uc = MeasureCells(repo, session)
        df = uc.execute(metrics=["mean_intensity"])
        assert len(df) == 3

    def test_with_filter(self, session, sample_labels, sample_image):
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        session.set_active_segmentation("cellpose")
        session.set_filter(frozenset({1, 2}))

        uc = MeasureCells(repo, session)
        df = uc.execute(metrics=["area"])
        # Only cells 1 and 2 measured (cell 3 filtered out)
        assert set(df["label"].tolist()) == {1, 2}

    def test_store_before_session(self, session, sample_labels, sample_image):
        """Measurements are written to store BEFORE session is updated."""
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        session.set_active_segmentation("cellpose")

        order = []
        original_write = repo.write_measurements

        def tracking_write(handle, df):
            order.append("store")
            original_write(handle, df)

        repo.write_measurements = tracking_write
        session.subscribe(Event.MEASUREMENTS_UPDATED, lambda: order.append("session"))

        uc = MeasureCells(repo, session)
        uc.execute(metrics=["area"])

        assert order == ["store", "session"]


# ── AcceptThreshold ──────────────────────────────────────────


class TestAcceptThreshold:
    def test_happy_path(self, session):
        repo = FakeRepo()
        viewer = FakeViewer()
        image = np.array([[100, 200], [50, 150]], dtype=np.float32)

        uc = AcceptThreshold(repo, viewer, session)
        result = uc.execute(image, threshold_value=125.0, method="otsu", channel_name="GFP")

        assert result.mask_name == "otsu_GFP"
        assert result.n_positive == 2  # 200 and 150 > 125
        assert result.n_total == 4
        # Written to store
        assert "otsu_GFP" in repo.written_masks
        np.testing.assert_array_equal(
            repo.written_masks["otsu_GFP"],
            np.array([[0, 1], [0, 1]], dtype=np.uint8),
        )
        # Session updated
        assert session.active_mask == "otsu_GFP"

    def test_no_dataset_raises(self):
        session = Session()
        repo = FakeRepo()
        viewer = FakeViewer()
        uc = AcceptThreshold(repo, viewer, session)

        with pytest.raises(ValueError, match="No dataset loaded"):
            uc.execute(np.zeros((2, 2)), 0.5, "otsu", "GFP")
