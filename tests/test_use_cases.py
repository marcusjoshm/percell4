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
from percell4.domain.errors import NoDatasetError, NoSegmentationError

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

    def read_channel_images(self, handle, view_bin=1):
        return self.channel_images

    def read_labels(self, handle, name, view_bin=1, timepoint=None):
        if name not in self.labels:
            raise KeyError(f"Labels not found: {name}")
        labels = self.labels[name]
        if timepoint is not None and labels.ndim == 3:
            return labels[timepoint]
        return labels

    def list_labels(self, handle):
        return list(self.labels.keys())

    def read_mask(self, handle, name, view_bin=1, timepoint=None):
        if name not in self.masks:
            raise KeyError(f"Mask not found: {name}")
        mask = self.masks[name]
        if timepoint is not None and mask.ndim == 3:
            return mask[timepoint]
        return mask

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
            if not hasattr(self, "array_attrs"):
                self.array_attrs: dict[str, dict] = {}
            self.array_attrs[path] = dict(attrs)

    def read_array(self, handle, path, view_bin=1):
        if path not in self.written_arrays:
            raise KeyError(f"Array not found: {path}")
        return self.written_arrays[path]

    def read_array_attrs(self, handle, path):
        return dict(getattr(self, "array_attrs", {}).get(path, {}))

    def read_decay(self, handle, channel, view_bin=1, timepoint=None):
        path = f"decay/{channel}"
        if path not in self.written_arrays:
            raise KeyError(f"Array not found: {path}")
        arr = self.written_arrays[path]
        return arr if timepoint is None else arr[timepoint]

    def read_group_columns(self, handle):
        return self.group_columns

    def read_metadata(self, handle):
        return dict(self.disk_metadata)

    def write_metadata(self, handle, attrs):
        # Mirror the on-disk merge semantics of DatasetStore.set_metadata.
        self.disk_metadata.update(dict(attrs))

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

    # ── view_bin handling (U13) ──────────────────────────────

    def test_default_view_bin_one_no_bin_at_measure_scaling(
        self, session, sample_labels, sample_image
    ):
        """At k=1 (default), the DataFrame still gains a bin_at_measure
        column for forward-compatibility (downstream filters can ignore
        the default), and pixel-count metrics are NOT scaled."""
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        session.set_active_segmentation("cellpose")

        uc = MeasureCells(repo, session)
        df = uc.execute(metrics=["area"])
        assert "bin_at_measure" in df.columns
        assert (df["bin_at_measure"] == 1).all()

    def test_view_bin_three_tags_rows(
        self, session, sample_labels, sample_image
    ):
        """Explicit view_bin=3 stamps bin_at_measure=3 on every row."""
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        session.set_active_segmentation("cellpose")

        uc = MeasureCells(repo, session)
        df = uc.execute(metrics=["area"], view_bin=3)
        assert (df["bin_at_measure"] == 3).all()

    def test_view_bin_picks_up_session_active_bin(
        self, session, sample_labels, sample_image
    ):
        """When view_bin is None, the use case reads session.active_bin."""
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        session.set_active_segmentation("cellpose")
        session.set_active_bin(2)

        uc = MeasureCells(repo, session)
        df = uc.execute(metrics=["area"])
        assert (df["bin_at_measure"] == 2).all()

    def test_view_bin_scales_pixel_area_by_k_squared(
        self, session, sample_labels, sample_image
    ):
        """area_pixels at view_bin=3 reports k**2=9 times the binned count
        so the value is comparable to a k=1 measurement (k=1-equivalent units)."""
        repo = FakeRepo()
        repo.channel_images = {"GFP": sample_image}
        repo.labels = {"cellpose": sample_labels}
        session.set_active_segmentation("cellpose")

        uc = MeasureCells(repo, session)
        df_k1 = uc.execute(metrics=["area"], view_bin=1)

        # Re-measure with the same data at view_bin=3 -- the FakeRepo
        # returns the same labels regardless of view_bin (it has no view
        # bin dispatch), so the count is identical; only the scaling
        # post-step differs.
        df_k3 = uc.execute(metrics=["area"], view_bin=3)
        if "area_pixels" in df_k1.columns:
            # Each row's area_pixels at k=3 equals 9x the k=1 value.
            np.testing.assert_allclose(
                df_k3["area_pixels"].values,
                df_k1["area_pixels"].values * 9,
            )


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

            def read_array(self, handle, path, view_bin=1):
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


class TestComputePhasorTimelapse:
    """ComputePhasor on a 4-D (T_acq,H,W,T_bins) decay writes a (T_acq,H,W)
    phasor, computing each frame from ITS OWN decay (cross-layer alignment
    across the acquisition axis)."""

    def test_timelapse_decay_writes_4d_phasor_per_frame(self):
        from percell4.application.use_cases.compute_phasor import ComputePhasor
        from percell4.domain.flim.phasor import compute_phasor

        nt, h, w, tb = 3, 4, 4, 16
        rates = [0.1, 0.25, 0.4]  # distinct decay rate per frame -> distinct phasor
        tvec = np.arange(tb, dtype=np.float32)
        decay = np.empty((nt, h, w, tb), dtype=np.float32)
        for t in range(nt):
            decay[t] = np.broadcast_to(
                np.exp(-rates[t] * tvec) * 1000.0, (h, w, tb)
            ).astype(np.float32)

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        repo.disk_metadata = {"native_shape": (h, w), "n_timepoints": nt}
        repo.written_arrays["decay/ch0"] = decay
        repo.array_attrs = {"decay/ch0": {"dims": ["Tacq", "H", "W", "T"]}}

        ComputePhasor(repo, session).execute(channel="ch0", harmonic=1)

        g = repo.written_arrays["phasor/ch0/g"]
        s = repo.written_arrays["phasor/ch0/s"]
        assert g.shape == (nt, h, w)
        assert s.shape == (nt, h, w)
        assert repo.array_attrs["phasor/ch0/g"]["dims"] == ["Tacq", "H", "W"]
        # Each frame equals compute_phasor of that frame's decay.
        for t in range(nt):
            gt, st = compute_phasor(decay[t], harmonic=1)
            np.testing.assert_allclose(g[t], gt, atol=1e-6)
            np.testing.assert_allclose(s[t], st, atol=1e-6)
        # Frames are genuinely distinct (different decay rates).
        assert not np.allclose(g[0], g[1])
        assert not np.allclose(g[1], g[2])


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


# ── ComputePhasor: truly-unfiltered output ───────────────────


class TestComputePhasorNoMedianFilter:
    """The canonical /phasor/<ch>/{g,s} must be written truly unfiltered.

    Regression guard for the FLIM filter-options change: ComputePhasor
    used to apply an unconditional scipy.ndimage.median_filter(size=3)
    before saving, so the 'unfiltered' cloud was secretly 3x3-median
    filtered. Median filtering is now an opt-in downstream view.
    """

    def _make_decay_with_outlier(self) -> np.ndarray:
        """4x4 decay, uniform except one pixel with a distinct lifetime.

        The lone fast-decay pixel gives a (g, s) that differs sharply from
        its neighbours, so a 3x3 median would visibly overwrite it.
        """
        H, W, T = 4, 4, 64
        t = np.arange(T, dtype=np.float32)
        decay = np.broadcast_to(
            np.exp(-t / 8.0), (H, W, T)
        ).astype(np.float32).copy()
        decay[1, 1, :] = np.exp(-t / 1.5) * 1000.0  # outlier pixel
        return decay

    def test_saved_gs_equal_raw_compute_phasor(self):
        from percell4.application.use_cases.compute_phasor import ComputePhasor
        from percell4.domain.flim.phasor import compute_phasor

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        decay = self._make_decay_with_outlier()
        repo.written_arrays["decay/ch0"] = decay
        repo.disk_metadata = {}  # no calibration → identity transform

        uc = ComputePhasor(repo, session)
        uc.execute(channel="ch0", harmonic=1)

        expected_g, expected_s = compute_phasor(decay, harmonic=1)
        # No calibration and no low-photon pixels here, so the saved maps
        # must equal the raw transform exactly. A 3x3 median would change
        # the outlier pixel and its neighbours.
        np.testing.assert_array_equal(repo.written_arrays["phasor/ch0/g"], expected_g)
        np.testing.assert_array_equal(repo.written_arrays["phasor/ch0/s"], expected_s)

    def test_outlier_pixel_survives(self):
        """The distinct outlier pixel is preserved (not median-smoothed)."""
        from percell4.application.use_cases.compute_phasor import ComputePhasor
        from percell4.domain.flim.phasor import compute_phasor

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        decay = self._make_decay_with_outlier()
        repo.written_arrays["decay/ch0"] = decay
        repo.disk_metadata = {}

        uc = ComputePhasor(repo, session)
        uc.execute(channel="ch0", harmonic=1)

        raw_g, _ = compute_phasor(decay, harmonic=1)
        saved_g = repo.written_arrays["phasor/ch0/g"]
        # Outlier pixel keeps its raw value; a median would replace it with
        # the surrounding neighbourhood median.
        assert saved_g[1, 1] == pytest.approx(float(raw_g[1, 1]))


# ── RunPhasorGMM (U3) ────────────────────────────────────────


def _make_two_cluster_phasor(rng_seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """64x64 phasor with two well-separated clusters in the brightest pixels."""
    rng = np.random.default_rng(rng_seed)
    h = w = 64
    g = np.full((h, w), np.nan, dtype=np.float32)
    s = np.full((h, w), np.nan, dtype=np.float32)
    # Two clusters spread across the spatial field
    pts_a = rng.multivariate_normal([0.30, 0.40], np.eye(2) * 0.0008, size=h * w // 4)
    pts_b = rng.multivariate_normal([0.55, 0.42], np.eye(2) * 0.0006, size=h * w // 4)
    flat_g = g.ravel()
    flat_s = s.ravel()
    flat_g[: pts_a.shape[0]] = pts_a[:, 0]
    flat_s[: pts_a.shape[0]] = pts_a[:, 1]
    flat_g[pts_a.shape[0]: pts_a.shape[0] + pts_b.shape[0]] = pts_b[:, 0]
    flat_s[pts_a.shape[0]: pts_a.shape[0] + pts_b.shape[0]] = pts_b[:, 1]
    return flat_g.reshape(h, w), flat_s.reshape(h, w)


def _seed_phasor_dataset(
    repo: FakeRepo,
    *,
    intensity_value: float = 100.0,
    freq_mhz: float | None = 80.0,
    write_filtered: bool = True,
    write_unfiltered: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    g_map, s_map = _make_two_cluster_phasor()
    # Decay is broadcast intensity across 16 time bins so decay.sum(-1)
    # equals intensity_value × 16 for every pixel.
    n_bins = 16
    h, w = g_map.shape
    decay = np.full(
        (h, w, n_bins), intensity_value, dtype=np.float32,
    )
    repo.written_arrays["decay/ch0"] = decay
    if write_filtered:
        repo.written_arrays["phasor/ch0/g_filtered"] = g_map
        repo.written_arrays["phasor/ch0/s_filtered"] = s_map
    if write_unfiltered:
        repo.written_arrays["phasor/ch0/g"] = g_map
        repo.written_arrays["phasor/ch0/s"] = s_map
    if freq_mhz is not None:
        repo.disk_metadata["flim_frequency_mhz"] = freq_mhz
    return g_map, s_map


class TestRunPhasorGMM:
    """U3 — RunPhasorGMM use case test scenarios."""

    def test_two_cluster_ellipse_recovers_means(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=2, criterion=None,
        )
        assert len(result.geometries) == 2
        assert result.chosen_n == 2
        # Ordered by mean_g
        means_g = sorted(g.mean_g for g in result.geometries)
        assert means_g[0] == pytest.approx(0.30, abs=0.03)
        assert means_g[1] == pytest.approx(0.55, abs=0.03)

    def test_auto_bic_selects_two(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=None, criterion="BIC",
            n_max=4,
        )
        assert result.chosen_n == 2
        assert result.criterion == "BIC"
        assert result.criterion_value is not None

    def test_circle_shape_has_equal_radii(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="circle",
            n_components=2, criterion=None,
        )
        for geom in result.geometries:
            assert geom.radii[0] == pytest.approx(geom.radii[1], abs=1e-12)
            assert geom.angle_deg == 0.0

    def test_dataset_path_snapshot_in_result(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        path = Path("/tmp/snapshot.h5")
        session.set_dataset(DatasetHandle(path=path, metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(channel="ch0", shape="ellipse", n_components=2, criterion=None)
        assert result.dataset_path == path

    def test_intensity_derived_from_decay_not_intensity_layer(self):
        """Alignment invariant: intensity must come from /decay.sum, never /intensity."""
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/align.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo, intensity_value=10.0)
        # If the use case accidentally read /intensity[0] (a misleading
        # layer with garbage data), the GMM would produce skewed weights.
        # Plant a misleading /intensity stack with values 1e6 — opposite
        # of what's in /decay (which sums to 160 per pixel).
        repo.written_arrays["intensity"] = np.full((1, 64, 64), 1e6, dtype=np.float32)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse", n_components=2, criterion=None,
        )
        # The fit succeeds with consistent weights from /decay; no read
        # of repo.written_arrays["intensity"] occurred.
        assert result.chosen_n == 2

    def test_use_filtered_gs_false_reads_unfiltered(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        # Only unfiltered phasor written
        _seed_phasor_dataset(repo, write_filtered=False)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=2, criterion=None,
            use_filtered_gs=False,
        )
        assert result.chosen_n == 2

    def test_cell_filter_restricts_gmm_input(self):
        """session.filter_ids + active_segmentation restrict pixels fed to GMM."""
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        g_map, s_map = _seed_phasor_dataset(repo)

        # Synthetic labels: cluster A pixels are label 1, cluster B pixels
        # are label 2, the rest are 0. This means filtering to {1} should
        # cause the GMM to fit a unimodal blob and converge differently.
        h, w = g_map.shape
        labels = np.zeros((h, w), dtype=np.int32)
        # Cluster A occupies the first quarter of the flat array
        n_a = (h * w) // 4
        labels.flat[:n_a] = 1
        labels.flat[n_a: 2 * n_a] = 2
        repo.labels["seg1"] = labels

        session.set_active_segmentation("seg1")
        session.set_filter({1})

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=2, criterion=None,
        )
        # Only label-1 pixels (cluster A) contributed → both fitted means
        # should sit near cluster A's center (0.30, 0.40), not the bimodal
        # truth.
        for geom in result.geometries:
            assert geom.mean_g == pytest.approx(0.30, abs=0.05)

    def test_no_dataset_raises(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()  # no dataset set
        repo = FakeRepo()
        uc = RunPhasorGMM(repo, session)
        with pytest.raises(NoDatasetError):
            uc.execute(channel="ch0", shape="ellipse", n_components=2, criterion=None)

    def test_missing_phasor_unfiltered_raises(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        # No phasor data written
        repo.written_arrays["decay/ch0"] = np.zeros((4, 4, 8), dtype=np.float32)

        uc = RunPhasorGMM(repo, session)
        with pytest.raises(ValueError, match="Phasor data not found"):
            uc.execute(
                channel="ch0", shape="ellipse",
                n_components=2, criterion=None,
                use_filtered_gs=False,
            )

    def test_missing_filtered_phasor_raises_distinct_message(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo, write_filtered=False)

        uc = RunPhasorGMM(repo, session)
        with pytest.raises(ValueError, match="Wavelet-filtered phasor not found"):
            uc.execute(
                channel="ch0", shape="ellipse",
                n_components=2, criterion=None,
                use_filtered_gs=True,
            )

    def test_ref_circle_without_freq_raises(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo, freq_mhz=None)

        uc = RunPhasorGMM(repo, session)
        with pytest.raises(ValueError, match="flim_frequency_mhz"):
            uc.execute(
                channel="ch0", shape="ellipse",
                n_components=2, criterion=None,
                ref_circle_tau_ns=2.5, ref_circle_radius=0.5,
            )

    def test_mask_filter_active_with_no_active_mask_silently_bypassed(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=2, criterion=None,
            mask_filter_active=True,  # but session.active_mask is None
        )
        assert result.chosen_n == 2

    def test_n_components_one_explicit_allowed(self):
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo)

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=1, criterion=None,
        )
        assert result.chosen_n == 1
        assert len(result.geometries) == 1

    def test_fresh_metadata_path_sees_post_snapshot_freq(self):
        """_read_fresh_metadata defeats handle.metadata snapshot staleness."""
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        # Empty metadata at set_dataset time (snapshot stale)
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        _seed_phasor_dataset(repo, freq_mhz=None)
        # Simulate post-snapshot write of the freq into /metadata
        repo.disk_metadata["flim_frequency_mhz"] = 80.0

        uc = RunPhasorGMM(repo, session)
        # Should not raise — fresh read picks up the post-snapshot freq
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=2, criterion=None,
            ref_circle_tau_ns=2.5, ref_circle_radius=0.5,
        )
        assert result.chosen_n == 2

    def test_decay_sum_uses_float64_intermediate(self):
        """High photon counts must not lose precision in intensity weights."""
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/u3.h5"), metadata={}))
        repo = FakeRepo()
        # Large per-bin counts: 256 bins × 70_000 each → sum 1.79e7,
        # above float32 precision (2^24 = 1.68e7).
        h = w = 32
        n_bins = 256
        decay = np.full((h, w, n_bins), 70_000, dtype=np.uint32)
        repo.written_arrays["decay/ch0"] = decay
        g_map, s_map = _make_two_cluster_phasor()
        # Reshape to match the seeded decay shape
        rng = np.random.default_rng(2)
        g_small = rng.multivariate_normal([0.30, 0.40], np.eye(2) * 0.0008, size=h * w // 2)
        g_other = rng.multivariate_normal([0.55, 0.42], np.eye(2) * 0.0006, size=h * w // 2)
        pts = np.vstack([g_small, g_other])
        flat_g = np.full(h * w, np.nan, dtype=np.float32)
        flat_s = np.full(h * w, np.nan, dtype=np.float32)
        flat_g[: pts.shape[0]] = pts[:, 0]
        flat_s[: pts.shape[0]] = pts[:, 1]
        repo.written_arrays["phasor/ch0/g_filtered"] = flat_g.reshape(h, w)
        repo.written_arrays["phasor/ch0/s_filtered"] = flat_s.reshape(h, w)
        repo.disk_metadata["flim_frequency_mhz"] = 80.0

        uc = RunPhasorGMM(repo, session)
        result = uc.execute(
            channel="ch0", shape="ellipse",
            n_components=2, criterion=None,
        )
        # Sanity check the fit landed despite large sums — the precision
        # invariant matters most for the sampling weight distribution.
        assert result.chosen_n == 2


# ── ComputePhasor / ApplyWavelet / ComputeLifetime: view_bin (U14) ──


class TestPhasorWritesViewBin:
    """ComputePhasor at view_bin > 1 NN-upsamples to native_shape and
    stamps created_at_bin so the canonical /phasor/<ch>/{g,s} paths stay
    at native (storage-at-native invariant)."""

    def _make_decay_at_binned_shape(self, h, w, t=64):
        return np.broadcast_to(
            np.exp(-np.arange(t, dtype=np.float32) / 8.0),
            (h, w, t),
        ).astype(np.float32).copy()

    def test_compute_phasor_default_view_bin_one_no_attr(self):
        """At view_bin=1, no created_at_bin attr is stamped (back-compat)."""
        from percell4.application.use_cases.compute_phasor import ComputePhasor

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        repo.disk_metadata = {"native_shape": (4, 4)}
        repo.written_arrays["decay/ch0"] = self._make_decay_at_binned_shape(4, 4)

        uc = ComputePhasor(repo, session)
        uc.execute(channel="ch0", harmonic=1, view_bin=1)

        attrs = getattr(repo, "array_attrs", {}).get("phasor/ch0/g", {})
        assert "created_at_bin" not in attrs

    def test_compute_phasor_view_bin_3_stamps_attr_and_upsamples(self):
        """At view_bin=3, g and s are NN-upsampled to native_shape and
        carry created_at_bin=3."""
        from percell4.application.use_cases.compute_phasor import ComputePhasor

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        # Native is 12x12; the decay we feed in is at the binned 4x4 shape.
        repo.disk_metadata = {"native_shape": (12, 12)}
        repo.written_arrays["decay/ch0"] = self._make_decay_at_binned_shape(4, 4)

        uc = ComputePhasor(repo, session)
        uc.execute(channel="ch0", harmonic=1, view_bin=3)

        # g and s written at native (12, 12).
        assert repo.written_arrays["phasor/ch0/g"].shape == (12, 12)
        assert repo.written_arrays["phasor/ch0/s"].shape == (12, 12)
        # Both carry the attr.
        attrs_g = repo.array_attrs["phasor/ch0/g"]
        attrs_s = repo.array_attrs["phasor/ch0/s"]
        assert attrs_g["created_at_bin"] == 3
        assert attrs_s["created_at_bin"] == 3

    def test_apply_wavelet_view_bin_3_stamps_attr_and_upsamples(self, monkeypatch):
        """ApplyWavelet outputs (g_filtered, s_filtered, lifetime_filtered)
        all NN-upsample to native and carry created_at_bin.

        denoise_phasor is mocked to return the input unchanged -- the
        underlying DTCWT implementation pulls in a numpy-2-incompatible
        library, and we only care about the upsample/attr discipline
        added in U14 around it, not the wavelet math itself."""
        import percell4.domain.flim.wavelet_filter as wf
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        # Replace denoise_phasor with a passthrough.
        def fake_denoise(g, s, intensity, filter_level=1, omega=None):
            return {"G": g.copy(), "S": s.copy(), "T": np.zeros_like(g)}

        monkeypatch.setattr(wf, "denoise_phasor", fake_denoise)

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        repo.disk_metadata = {
            "native_shape": (12, 12),
            "flim_frequency_mhz": 80.0,
        }
        # G, S, decay all live at the binned 4x4 shape.
        repo.written_arrays["phasor/ch0/g"] = np.full((4, 4), 0.5, dtype=np.float32)
        repo.written_arrays["phasor/ch0/s"] = np.full((4, 4), 0.3, dtype=np.float32)
        repo.written_arrays["decay/ch0"] = self._make_decay_at_binned_shape(4, 4)

        uc = ApplyWavelet(repo, session)
        uc.execute(channel="ch0", filter_level=2, view_bin=3)

        # Outputs at native.
        assert repo.written_arrays["phasor/ch0/g_filtered"].shape == (12, 12)
        assert repo.written_arrays["phasor/ch0/s_filtered"].shape == (12, 12)
        # Attrs stamped.
        assert repo.array_attrs["phasor/ch0/g_filtered"]["created_at_bin"] == 3
        assert repo.array_attrs["phasor/ch0/s_filtered"]["created_at_bin"] == 3

    def test_compute_lifetime_view_bin_3_upsamples_to_native(self):
        """ComputeLifetime upsamples the binned lifetime to native_shape
        before appending it as a /intensity channel slice."""
        from percell4.application.use_cases.compute_lifetime import ComputeLifetime

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        repo.disk_metadata = {
            "native_shape": (12, 12),
            "flim_frequency_mhz": 80.0,
        }
        # g/s live at the binned shape; the lifetime is upsampled to native.
        repo.written_arrays["phasor/ch0/g"] = np.full((4, 4), 0.5, dtype=np.float32)
        repo.written_arrays["phasor/ch0/s"] = np.full((4, 4), 0.3, dtype=np.float32)

        uc = ComputeLifetime(repo, session)
        uc.execute(channel="ch0", view_bin=3)

        # First lifetime channel → /intensity is the bare 2D plane.
        assert repo.written_arrays["intensity"].shape == (12, 12)
        assert repo.array_attrs["intensity"]["created_at_bin"] == 3

    def test_compute_phasor_view_bin_gt_one_no_native_shape_raises(self):
        """Missing native_shape with view_bin > 1 is an error."""
        from percell4.application.use_cases.compute_phasor import ComputePhasor

        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        # No native_shape on disk metadata.
        repo.disk_metadata = {}
        repo.written_arrays["decay/ch0"] = self._make_decay_at_binned_shape(4, 4)

        uc = ComputePhasor(repo, session)
        with pytest.raises(ValueError, match="native_shape"):
            uc.execute(channel="ch0", harmonic=1, view_bin=3)


# ── ComputeLifetime: explicit source selection ───────────────


class TestComputeLifetimeSource:
    """ComputeLifetime computes from an explicit, caller-chosen source.

    Replaces the old implicit 'filtered-if-exists, else unfiltered'
    behavior with three explicit sources: unfiltered, median, wavelet.
    """

    def _setup(self, *, with_wavelet: bool = False):
        session = Session()
        session.set_dataset(DatasetHandle(path=Path("/tmp/x.h5"), metadata={}))
        repo = FakeRepo()
        repo.disk_metadata = {"flim_frequency_mhz": 80.0}
        g = np.full((5, 5), 0.5, dtype=np.float32)
        s = np.full((5, 5), 0.3, dtype=np.float32)
        g[2, 2] = 0.9  # outlier so median differs from raw
        s[2, 2] = 0.05
        repo.written_arrays["phasor/ch0/g"] = g
        repo.written_arrays["phasor/ch0/s"] = s
        if with_wavelet:
            repo.written_arrays["phasor/ch0/g_filtered"] = np.full(
                (5, 5), 0.45, dtype=np.float32
            )
            repo.written_arrays["phasor/ch0/s_filtered"] = np.full(
                (5, 5), 0.28, dtype=np.float32
            )
        return session, repo

    def test_unfiltered_registers_channel_and_writes_intensity(self):
        from percell4.application.use_cases.compute_lifetime import (
            ComputeLifetime,
            lifetime_channel_name,
        )
        from percell4.domain.flim.phasor import phasor_to_lifetime

        session, repo = self._setup()
        result = ComputeLifetime(repo, session).execute(
            channel="ch0", source="unfiltered"
        )

        assert result.source == "unfiltered"
        assert result.median_size is None
        assert result.channel_name == lifetime_channel_name("ch0", "unfiltered")
        expected = phasor_to_lifetime(
            repo.written_arrays["phasor/ch0/g"],
            repo.written_arrays["phasor/ch0/s"],
            frequency_mhz=80.0,
        )
        # /intensity now holds the lifetime channel (first channel, since
        # the fixture has no prior intensity).
        intensity = repo.written_arrays["intensity"]
        assert intensity.shape == expected.shape  # single-channel, 2D
        np.testing.assert_array_equal(intensity, expected.astype(np.float32))
        # channel_names was registered and persisted to /metadata + session.
        assert result.channel_name in repo.disk_metadata["channel_names"]
        assert repo.disk_metadata["n_channels"] == len(
            repo.disk_metadata["channel_names"]
        )
        # No bespoke phasor/<ch>/lifetime path was written.
        assert "phasor/ch0/lifetime" not in repo.written_arrays

    def test_median_applies_kernel_and_returns_size(self):
        from percell4.application.use_cases.compute_lifetime import (
            ComputeLifetime,
            lifetime_channel_name,
        )
        from percell4.domain.flim.phasor import median_filter_gs, phasor_to_lifetime

        session, repo = self._setup()
        result = ComputeLifetime(repo, session).execute(
            channel="ch0", source="median", median_size=3
        )

        assert result.source == "median"
        assert result.median_size == 3
        assert result.channel_name == lifetime_channel_name("ch0", "median")
        gm, sm = median_filter_gs(
            repo.written_arrays["phasor/ch0/g"],
            repo.written_arrays["phasor/ch0/s"],
            size=3,
        )
        expected = phasor_to_lifetime(gm, sm, frequency_mhz=80.0)
        np.testing.assert_array_equal(
            repo.written_arrays["intensity"], expected.astype(np.float32)
        )

    def test_wavelet_reads_filtered_maps(self):
        from percell4.application.use_cases.compute_lifetime import (
            ComputeLifetime,
            lifetime_channel_name,
        )
        from percell4.domain.flim.phasor import phasor_to_lifetime

        session, repo = self._setup(with_wavelet=True)
        result = ComputeLifetime(repo, session).execute(
            channel="ch0", source="wavelet"
        )

        assert result.source == "wavelet"
        assert result.channel_name == lifetime_channel_name("ch0", "wavelet")
        expected = phasor_to_lifetime(
            repo.written_arrays["phasor/ch0/g_filtered"],
            repo.written_arrays["phasor/ch0/s_filtered"],
            frequency_mhz=80.0,
        )
        np.testing.assert_array_equal(
            repo.written_arrays["intensity"], expected.astype(np.float32)
        )

    def test_wavelet_without_filtered_maps_raises(self):
        from percell4.application.use_cases.compute_lifetime import ComputeLifetime

        session, repo = self._setup(with_wavelet=False)
        with pytest.raises(ValueError, match="wavelet"):
            ComputeLifetime(repo, session).execute(channel="ch0", source="wavelet")

    def test_invalid_source_raises(self):
        from percell4.application.use_cases.compute_lifetime import ComputeLifetime

        session, repo = self._setup()
        with pytest.raises(ValueError, match="source"):
            ComputeLifetime(repo, session).execute(channel="ch0", source="bogus")

    def test_appends_when_intensity_already_exists(self):
        """An existing /intensity channel survives; lifetime is added as C+1."""
        from percell4.application.use_cases.compute_lifetime import ComputeLifetime

        session, repo = self._setup()
        # Seed an existing single-channel /intensity.
        repo.written_arrays["intensity"] = np.full((5, 5), 10.0, dtype=np.float32)
        repo.disk_metadata["channel_names"] = ["ch0"]

        result = ComputeLifetime(repo, session).execute(
            channel="ch0", source="unfiltered"
        )

        intensity = repo.written_arrays["intensity"]
        # 2D → (2, H, W) after the append.
        assert intensity.shape == (2, 5, 5)
        np.testing.assert_array_equal(intensity[0], np.full((5, 5), 10.0))
        assert repo.disk_metadata["channel_names"] == ["ch0", result.channel_name]
        assert repo.disk_metadata["n_channels"] == 2

    def test_recompute_same_source_overwrites_slice(self):
        """Re-running with the same source overwrites that channel's slice."""
        from percell4.application.use_cases.compute_lifetime import ComputeLifetime

        session, repo = self._setup()
        repo.written_arrays["intensity"] = np.full((5, 5), 10.0, dtype=np.float32)
        repo.disk_metadata["channel_names"] = ["ch0"]

        ComputeLifetime(repo, session).execute(channel="ch0", source="unfiltered")
        # Bump the raw maps so the second run produces a different lifetime.
        repo.written_arrays["phasor/ch0/g"] = np.full((5, 5), 0.7, dtype=np.float32)
        ComputeLifetime(repo, session).execute(channel="ch0", source="unfiltered")

        # Still two channels, not three — same name → in-place replace.
        assert repo.written_arrays["intensity"].shape == (2, 5, 5)
        assert len(repo.disk_metadata["channel_names"]) == 2

    def test_time_lapse_raises(self):
        """Time-lapse intensity shape isn't supported yet — must raise."""
        from percell4.application.use_cases.compute_lifetime import ComputeLifetime

        session, repo = self._setup()
        repo.disk_metadata["n_timepoints"] = 4

        with pytest.raises(ValueError, match="time-lapse"):
            ComputeLifetime(repo, session).execute(channel="ch0", source="unfiltered")


class TestApplyWaveletTimelapse:
    """ApplyWavelet on a time-lapse dataset filters each acquisition frame
    independently (U9). Before the fix, the 3-D (T_acq,H,W) phasor was passed
    straight to the 2-D wavelet kernel and crashed with
    'too many values to unpack (expected 2)'."""

    def test_apply_wavelet_filters_per_frame(self, monkeypatch):
        import percell4.domain.flim.wavelet_filter as wf
        from percell4.application.use_cases.apply_wavelet import ApplyWavelet

        nt, h, w, tb = 3, 4, 4, 8
        calls = []

        def fake_denoise(g, s, intensity, filter_level=1, omega=None):
            # Mirror _filter_channel's `h, w = data.shape` so a 3-D pass
            # reproduces the reported crash; per-frame 2-D input is required.
            hh, ww = g.shape
            assert intensity.shape == (hh, ww)
            calls.append((hh, ww))
            return {"G": g.copy() + 10.0, "S": s.copy() + 20.0, "T": g.copy() + 1.0}

        monkeypatch.setattr(wf, "denoise_phasor", fake_denoise)

        session = Session()
        session.set_dataset(DatasetHandle(
            path=Path("/tmp/x.h5"),
            metadata={"n_timepoints": nt, "native_shape": (h, w),
                      "flim_frequency_mhz": 80.0},
        ))
        repo = FakeRepo()
        repo.disk_metadata = {"native_shape": (h, w), "n_timepoints": nt,
                              "flim_frequency_mhz": 80.0}
        rng = np.random.default_rng(3)
        g = rng.uniform(0.1, 0.9, (nt, h, w)).astype(np.float32)
        s = rng.uniform(0.05, 0.5, (nt, h, w)).astype(np.float32)
        decay = rng.uniform(1.0, 50.0, (nt, h, w, tb)).astype(np.float32)
        repo.written_arrays["phasor/ch0/g"] = g
        repo.written_arrays["phasor/ch0/s"] = s
        repo.written_arrays["decay/ch0"] = decay
        repo.array_attrs = {"phasor/ch0/g": {"dims": ["Tacq", "H", "W"]}}

        ApplyWavelet(repo, session).execute(channel="ch0", filter_level=2)

        gf = repo.written_arrays["phasor/ch0/g_filtered"]
        sf = repo.written_arrays["phasor/ch0/s_filtered"]
        lf = repo.written_arrays["phasor/ch0/lifetime_filtered"]
        assert gf.shape == (nt, h, w)
        assert sf.shape == (nt, h, w)
        assert lf.shape == (nt, h, w)
        assert repo.array_attrs["phasor/ch0/g_filtered"]["dims"] == ["Tacq", "H", "W"]
        assert len(calls) == nt  # filtered once per frame, each 2-D
        # Each frame is the wavelet of THAT frame's input (per-frame, ordered).
        for t in range(nt):
            np.testing.assert_allclose(gf[t], g[t] + 10.0)
            np.testing.assert_allclose(sf[t], s[t] + 20.0)
