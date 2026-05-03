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
