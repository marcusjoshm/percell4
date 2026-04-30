"""Tests for application use cases.

All tests use fakes for ports — no Qt, no napari, no HDF5.
"""

from __future__ import annotations

from pathlib import Path

from percell4.domain.errors import NoDatasetError, NoSegmentationError

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
        # In-memory '/metadata' attrs that read_metadata() returns. Tests
        # can populate this AFTER set_dataset to simulate writes that
        # happened post-snapshot (e.g., TCSPC import writing flim_cal_*).
        self.disk_metadata: dict = {}

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

    def read_metadata(self, handle):
        return dict(self.disk_metadata)

    def delete_path(self, handle, path):
        return self.written_arrays.pop(path, None) is not None


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

        with pytest.raises(NoDatasetError, match="No dataset loaded"):
            uc.execute(metrics=["area"])

    def test_no_segmentation_raises(self, session, sample_image):
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        uc = MeasureCells(repo, session)

        with pytest.raises(NoSegmentationError, match="No active segmentation"):
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

        with pytest.raises(NoDatasetError, match="No dataset loaded"):
            uc.execute(np.zeros((2, 2)), 0.5, "otsu", "GFP")


# ── ComputePhasor: fresh-metadata-on-disk regression ────────


class TestComputePhasorFreshMetadata:
    """ComputePhasor must read /metadata fresh from disk for calibration.

    Regression for the in-session TCSPC import bug: handle.metadata is a
    snapshot taken when set_dataset was called. If TCSPC import writes
    flim_cal_phase_<ch> / flim_cal_mod_<ch> AFTER that snapshot, the
    snapshot has stale defaults and calibration is silently skipped —
    producing a wildly wrong phasor that "fixes itself" only after app
    restart (which takes a fresh snapshot).
    """

    def _make_decay(self) -> np.ndarray:
        """Synthetic 4x4 decay with one exponential per pixel."""
        H, W, T = 4, 4, 64
        t = np.arange(T, dtype=np.float32)
        # Simple decay: exp(-t/tau) with tau=8
        decay_curve = np.exp(-t / 8.0)
        decay = np.broadcast_to(
            decay_curve, (H, W, T)
        ).astype(np.float32).copy()
        return decay

    def test_fresh_metadata_calibration_is_applied(self):
        """Calibration written AFTER set_dataset must still apply."""
        from percell4.application.use_cases.compute_phasor import ComputePhasor

        session = Session()
        # Snapshot at set_dataset time has NO calibration:
        handle = DatasetHandle(path=Path("/tmp/x.h5"), metadata={})
        session.set_dataset(handle)

        repo = FakeRepo()
        repo.written_arrays["decay/ch0"] = self._make_decay()
        # Simulate post-snapshot TCSPC import writing calibration:
        repo.disk_metadata = {
            "flim_cal_phase_ch0": 0.5,
            "flim_cal_mod_ch0": 1.2,
            "flim_frequency_mhz": 80.0,
        }

        # Run with snapshot-only (defaults) for comparison
        uc_fresh = ComputePhasor(repo, session)
        result_with_fresh = uc_fresh.execute(channel="ch0", harmonic=1)

        # Now run a "stale snapshot" version: drop disk_metadata so
        # read_metadata returns {} and the result reflects no calibration.
        repo_stale = FakeRepo()
        repo_stale.written_arrays["decay/ch0"] = self._make_decay()
        repo_stale.disk_metadata = {}
        # Reset session so the second compute starts fresh
        session2 = Session()
        session2.set_dataset(DatasetHandle(path=Path("/tmp/y.h5"), metadata={}))
        uc_stale = ComputePhasor(repo_stale, session2)
        result_no_cal = uc_stale.execute(channel="ch0", harmonic=1)

        # Calibration with cal_phase=0.5, cal_mod=1.2 IS not the identity,
        # so the two results must differ. If the use case ignored the
        # fresh metadata (i.e., used handle.metadata snapshot only), they
        # would be identical — that's the regression.
        assert not np.allclose(result_with_fresh.g_map, result_no_cal.g_map)
        assert not np.allclose(result_with_fresh.s_map, result_no_cal.s_map)

    def test_falls_back_to_snapshot_when_repo_lacks_read_metadata(self):
        """Old test stubs without read_metadata still work via fallback."""
        from percell4.application.use_cases.compute_phasor import ComputePhasor

        # FakeRepo without read_metadata at all:
        class MinimalRepo:
            def __init__(self):
                self.written_arrays = {"decay/ch0": self._make_decay()}

            def _make_decay(self):
                return np.broadcast_to(
                    np.exp(-np.arange(64, dtype=np.float32) / 8.0),
                    (4, 4, 64),
                ).astype(np.float32).copy()

            def read_array(self, handle, path):
                if path not in self.written_arrays:
                    raise KeyError(path)
                return self.written_arrays[path]

            def write_array(self, handle, path, data, attrs=None):
                self.written_arrays[path] = data

        session = Session()
        # Snapshot has the calibration values directly
        handle = DatasetHandle(
            path=Path("/tmp/x.h5"),
            metadata={"flim_cal_phase_ch0": 0.5, "flim_cal_mod_ch0": 1.2},
        )
        session.set_dataset(handle)
        repo = MinimalRepo()
        uc = ComputePhasor(repo, session)
        # Should not raise; falls back to handle.metadata.
        result = uc.execute(channel="ch0", harmonic=1)
        assert result.g_map.shape == (4, 4)


# ── ComputePhasor: invalidate stale wavelet output ───────────


class TestComputePhasorInvalidatesWavelet:
    """Recomputing the phasor must drop stale derived layers.

    Regression for: after a TCSPC import, an early apply_wavelet run
    captured uncalibrated (g, s) into g_filtered. A later compute_phasor
    fixed /phasor/<ch>/g, /s but left g_filtered untouched — so toggling
    'Filtered' in the phasor plot showed a stale wavelet view that
    looked like an unmasked, broad distribution. The fix invalidates
    g_filtered, s_filtered, and lifetime_filtered whenever (g, s) is
    rewritten.
    """

    def test_recompute_drops_stale_filtered_layers(self):
        from percell4.application.use_cases.compute_phasor import ComputePhasor

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))

        repo = FakeRepo()
        # Synthetic decay
        T = 64
        decay = np.broadcast_to(
            np.exp(-np.arange(T, dtype=np.float32) / 8.0), (4, 4, T),
        ).astype(np.float32).copy()
        repo.written_arrays["decay/ch0"] = decay
        # Pre-populate stale derived layers (would happen after a prior
        # apply_wavelet / compute_lifetime against earlier g/s):
        repo.written_arrays["phasor/ch0/g_filtered"] = np.full((4, 4), 99.0)
        repo.written_arrays["phasor/ch0/s_filtered"] = np.full((4, 4), 99.0)
        repo.written_arrays["phasor/ch0/lifetime_filtered"] = np.full((4, 4), 99.0)

        uc = ComputePhasor(repo, session)
        uc.execute(channel="ch0", harmonic=1)

        # Stale derived layers must be gone
        assert "phasor/ch0/g_filtered" not in repo.written_arrays
        assert "phasor/ch0/s_filtered" not in repo.written_arrays
        assert "phasor/ch0/lifetime_filtered" not in repo.written_arrays
        # Fresh (g, s) must be present
        assert "phasor/ch0/g" in repo.written_arrays
        assert "phasor/ch0/s" in repo.written_arrays

    def test_recompute_succeeds_when_no_stale_layers_exist(self):
        """First compute on a clean dataset doesn't error on absent layers."""
        from percell4.application.use_cases.compute_phasor import ComputePhasor

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/y.h5"), metadata={}))
        repo = FakeRepo()
        T = 64
        decay = np.broadcast_to(
            np.exp(-np.arange(T, dtype=np.float32) / 8.0), (4, 4, T),
        ).astype(np.float32).copy()
        repo.written_arrays["decay/ch0"] = decay

        uc = ComputePhasor(repo, session)
        result = uc.execute(channel="ch0", harmonic=1)

        assert result.g_map.shape == (4, 4)
