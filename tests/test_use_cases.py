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
        self.written_attrs: dict[str, dict] = {}
        self.group_attrs: dict[str, dict] = {}
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
        if attrs is not None:
            self.written_attrs[path] = dict(attrs)

    def read_array(self, handle, path):
        if path not in self.written_arrays:
            raise KeyError(f"Array not found: {path}")
        return self.written_arrays[path]

    def read_array_attrs(self, handle, path):
        if path not in self.written_attrs and path not in self.group_attrs:
            raise KeyError(f"Path not found: {path}")
        if path in self.group_attrs:
            return dict(self.group_attrs[path])
        return dict(self.written_attrs[path])

    def write_arrays(self, handle, items, group_attrs=None):
        for item in items:
            self.write_array(handle, item.path, item.array, attrs=item.attrs)
        if group_attrs:
            for path, attrs in group_attrs.items():
                self.group_attrs.setdefault(path, {}).update(attrs)

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


# ── ApplyWavelet ─────────────────────────────────────────────


@pytest.fixture
def wavelet_repo():
    """Repo pre-populated with a fake phasor channel so ApplyWavelet's
    inputs resolve without actually needing real data."""
    repo = FakeRepo()
    H = W = 64
    repo.written_arrays["phasor/ch0/g"] = np.full((H, W), 0.45)
    repo.written_arrays["phasor/ch0/s"] = np.full((H, W), 0.45)
    repo.written_arrays["intensity"] = np.full((H, W), 25.0)
    return repo


@pytest.fixture
def wavelet_session():
    """Session with a DatasetHandle carrying FLIM metadata."""
    s = Session()
    handle = DatasetHandle(
        path=Path("/tmp/test.h5"),
        metadata={"flim_frequency_mhz": 80.0},
    )
    s.set_dataset(handle)
    return s


class TestApplyWavelet:
    """Dispatch + provenance tests for the wavelet use case.

    Monkeypatches the filter functions with sentinels so these tests
    run without dtcwt and prove the dispatch wiring, not the algorithm.
    """

    def _sentinel_result(self, tag: str):
        H = W = 64
        return {
            "G": np.full((H, W), 1.0 if tag == "boe" else 2.0, dtype=np.float32),
            "S": np.full((H, W), 3.0 if tag == "boe" else 4.0, dtype=np.float32),
            "T": np.full((H, W), 2.5, dtype=np.float32),
            "GU": np.zeros((H, W), dtype=np.float32),
            "SU": np.zeros((H, W), dtype=np.float32),
            "TU": np.zeros((H, W), dtype=np.float32),
            "filter_level": 9,
        }

    def test_boe_dispatch_invokes_boe_module(
        self, wavelet_repo, wavelet_session, monkeypatch,
    ):
        """algorithm='boe_2021' → boe.denoise_phasor_boe is called."""
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        called = {"module": None}

        def fake_boe(*a, **kw):
            called["module"] = "boe"
            return self._sentinel_result("boe")

        def fake_jcb(*a, **kw):
            called["module"] = "jcb"
            return self._sentinel_result("jcb")

        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.boe.denoise_phasor_boe", fake_boe)
        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.jcb.denoise_phasor_jcb", fake_jcb)

        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        result = uc.execute(channel="ch0", algorithm="boe_2021")

        assert called["module"] == "boe"
        assert result.algorithm == "boe_2021"
        # Written G is the BOE sentinel (all-1 array).
        assert np.allclose(wavelet_repo.written_arrays[
            "phasor/ch0/g_filtered"], 1.0)

    def test_jcb_dispatch_invokes_jcb_module(
        self, wavelet_repo, wavelet_session, monkeypatch,
    ):
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        called = {"module": None}
        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.boe.denoise_phasor_boe",
            lambda *a, **kw: (called.__setitem__("module", "boe"),
                               self._sentinel_result("boe"))[1],
        )
        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.jcb.denoise_phasor_jcb",
            lambda *a, **kw: (called.__setitem__("module", "jcb"),
                               self._sentinel_result("jcb"))[1],
        )

        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        result = uc.execute(channel="ch0", algorithm="jcb_2025")

        assert called["module"] == "jcb"
        assert result.algorithm == "jcb_2025"
        # Written G is the JCB sentinel (all-2 array).
        assert np.allclose(wavelet_repo.written_arrays[
            "phasor/ch0/g_filtered"], 2.0)

    def test_default_algorithm_is_boe(
        self, wavelet_repo, wavelet_session, monkeypatch,
    ):
        """No algorithm kwarg → uses boe_2021."""
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        called = {"module": None}
        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.boe.denoise_phasor_boe",
            lambda *a, **kw: (called.__setitem__("module", "boe"),
                               self._sentinel_result("boe"))[1],
        )

        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        uc.execute(channel="ch0")

        assert called["module"] == "boe"

    def test_unknown_algorithm_raises_valueerror(
        self, wavelet_repo, wavelet_session,
    ):
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        with pytest.raises(ValueError, match="Unknown wavelet algorithm"):
            uc.execute(channel="ch0", algorithm="bogus")

    def test_writes_full_provenance_attrs(
        self, wavelet_repo, wavelet_session, monkeypatch,
    ):
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet
        from percell4 import store_schema

        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.boe.denoise_phasor_boe",
            lambda *a, **kw: self._sentinel_result("boe"),
        )

        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        uc.execute(channel="ch0", algorithm="boe_2021", filter_level=9)

        attrs = wavelet_repo.written_attrs["phasor/ch0/g_filtered"]
        expected_keys = {
            "dims", "channel", "filter_level",
            "algorithm", "biort", "qshift", "n_local_window",
            "sigma_g_estimator", "shrinkage",
            "dtcwt_version", "percell4_version",
        }
        assert set(attrs) >= expected_keys, (
            f"missing provenance attrs: {expected_keys - set(attrs)}"
        )
        assert attrs["algorithm"] == "boe_2021"
        assert attrs["biort"] == "legall"
        assert attrs["qshift"] == "qshift_a"
        assert attrs["n_local_window"] == 3
        assert attrs["sigma_g_estimator"] == "mad_level1_pm45"
        assert attrs["shrinkage"] == "bishrink_full"
        assert attrs["filter_level"] == 9

    def test_lifetime_filtered_carries_omega(
        self, wavelet_repo, wavelet_session, monkeypatch,
    ):
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.boe.denoise_phasor_boe",
            lambda *a, **kw: self._sentinel_result("boe"),
        )

        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        uc.execute(channel="ch0", algorithm="boe_2021")

        attrs = wavelet_repo.written_attrs[
            "phasor/ch0/lifetime_filtered"]
        assert "omega_rad_per_ns" in attrs
        assert attrs["omega_rad_per_ns"] == pytest.approx(
            2 * np.pi * 80.0
        )

    def test_three_datasets_agree_on_algorithm(
        self, wavelet_repo, wavelet_session, monkeypatch,
    ):
        """All three filtered datasets carry the same algorithm attr."""
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.boe.denoise_phasor_boe",
            lambda *a, **kw: self._sentinel_result("boe"),
        )
        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        uc.execute(channel="ch0", algorithm="boe_2021")

        for path in ("phasor/ch0/g_filtered",
                     "phasor/ch0/s_filtered",
                     "phasor/ch0/lifetime_filtered"):
            assert wavelet_repo.written_attrs[path]["algorithm"] == "boe_2021"

    def test_filter_status_sentinel_set_on_group(
        self, wavelet_repo, wavelet_session, monkeypatch,
    ):
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet
        from percell4 import store_schema

        monkeypatch.setattr(
            "percell4.domain.flim.wavelet.boe.denoise_phasor_boe",
            lambda *a, **kw: self._sentinel_result("boe"),
        )

        uc = ApplyWavelet(wavelet_repo, wavelet_session)
        uc.execute(channel="ch0", algorithm="boe_2021")

        group_attrs = wavelet_repo.group_attrs.get("phasor/ch0", {})
        assert group_attrs.get(store_schema.FILTER_STATUS_ATTR) == (
            store_schema.FILTER_STATUS_COMPLETE
        )

    def test_missing_algorithm_attr_defaults_to_jcb_2025(self):
        """Backward compat: datasets written before this change lack
        the algorithm attr; readers must treat them as jcb_2025."""
        from percell4.store_schema import read_wavelet_algorithm

        # No attr → legacy.
        assert read_wavelet_algorithm({}) == "jcb_2025"
        # Explicit attr wins.
        assert read_wavelet_algorithm({"algorithm": "boe_2021"}) == "boe_2021"

    def test_no_dataset_raises(self):
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        repo = FakeRepo()
        uc = ApplyWavelet(repo, Session())
        with pytest.raises(NoDatasetError):
            uc.execute(channel="ch0")

    def test_no_phasor_raises_value_error(self, wavelet_session):
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        repo = FakeRepo()  # empty, no phasor arrays
        uc = ApplyWavelet(repo, wavelet_session)
        with pytest.raises(ValueError, match="No phasor data"):
            uc.execute(channel="ch0")
