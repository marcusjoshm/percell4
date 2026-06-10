"""Tests for LoadCachedPhasor use case.

Read-only path that hydrates the phasor window from /phasor/<ch>/g,s
(plus optional g_filtered/s_filtered and decay-derived intensity).
Trusts the upstream invalidation chain — these tests do not exercise
crash-mid-write cleanup, only the asymmetric-cache defensive path.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.load_cached_phasor import (
    CachedPhasorResult,
    LoadCachedPhasor,
)
from percell4.domain.dataset import DatasetHandle
from percell4.domain.errors import NoCachedPhasorError, NoDatasetError


class FakeRepo:
    """Minimal in-memory repo for the LoadCachedPhasor read path."""

    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.attrs: dict[str, dict] = {}
        self.disk_metadata: dict = {}

    def write_array(self, handle, path, data, attrs=None):
        self.arrays[path] = data
        if attrs:
            self.attrs[path] = dict(attrs)

    def read_array(self, handle, path, view_bin=1):
        if path not in self.arrays:
            raise KeyError(f"Array not found: {path}")
        return self.arrays[path]

    def read_array_attrs(self, handle, path):
        return dict(self.attrs.get(path, {}))

    def read_metadata(self, handle):
        return dict(self.disk_metadata)


@pytest.fixture
def session():
    s = Session()
    s.set_dataset(DatasetHandle(path=Path("/tmp/test.h5")))
    return s


@pytest.fixture
def repo():
    return FakeRepo()


def _seed_full_cache(repo: FakeRepo, channel: str = "ch0", shape=(8, 8)):
    """Seed a full bundle: g, s, g_filtered, s_filtered, decay."""
    rng = np.random.default_rng(42)
    g = rng.uniform(0.1, 0.9, size=shape).astype(np.float32)
    s = rng.uniform(0.05, 0.5, size=shape).astype(np.float32)
    gf = rng.uniform(0.1, 0.9, size=shape).astype(np.float32)
    sf = rng.uniform(0.05, 0.5, size=shape).astype(np.float32)
    decay = rng.uniform(1.0, 100.0, size=(*shape, 16)).astype(np.float32)
    repo.arrays[f"phasor/{channel}/g"] = g
    repo.arrays[f"phasor/{channel}/s"] = s
    repo.arrays[f"phasor/{channel}/g_filtered"] = gf
    repo.arrays[f"phasor/{channel}/s_filtered"] = sf
    repo.attrs[f"phasor/{channel}/g_filtered"] = {"filter_level": 9}
    repo.arrays[f"decay/{channel}"] = decay
    return g, s, gf, sf, decay


# ── Happy path ────────────────────────────────────────────────


def test_full_cache_returns_all_fields(session, repo):
    g, s, gf, sf, decay = _seed_full_cache(repo)
    uc = LoadCachedPhasor(repo, session)

    result = uc.execute("ch0")

    assert isinstance(result, CachedPhasorResult)
    assert result.channel == "ch0"
    np.testing.assert_array_equal(result.g_map, g)
    np.testing.assert_array_equal(result.s_map, s)
    np.testing.assert_array_equal(result.g_filtered, gf)
    np.testing.assert_array_equal(result.s_filtered, sf)
    # Intensity is decay.sum(axis=-1)
    np.testing.assert_array_equal(result.intensity, decay.sum(axis=-1).astype(np.float32))
    # All arrays are float32
    assert result.g_map.dtype == np.float32
    assert result.s_map.dtype == np.float32
    assert result.intensity.dtype == np.float32


def test_raw_only_cache_returns_none_for_filtered(session, repo):
    """When wavelet has not been applied, g_filtered/s_filtered are None."""
    rng = np.random.default_rng(0)
    repo.arrays["phasor/ch0/g"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/ch0/s"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["decay/ch0"] = rng.uniform(size=(4, 4, 8)).astype(np.float32)

    result = LoadCachedPhasor(repo, session).execute("ch0")

    assert result.g_filtered is None
    assert result.s_filtered is None
    assert result.intensity is not None  # decay still present


def test_no_decay_returns_none_intensity(session, repo):
    """When /decay/<ch> is missing, intensity is None but the rest loads."""
    rng = np.random.default_rng(0)
    repo.arrays["phasor/ch0/g"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/ch0/s"] = rng.uniform(size=(4, 4)).astype(np.float32)

    result = LoadCachedPhasor(repo, session).execute("ch0")

    assert result.g_map is not None
    assert result.s_map is not None
    assert result.intensity is None
    assert result.g_filtered is None


# ── Cached filter level (wavelet recompute signal) ────────────


def test_cached_filter_level_surfaced(session, repo):
    """The DTCWT level stamped on g_filtered is read back so the panel can
    detect a level change and recompute."""
    _seed_full_cache(repo)  # stamps filter_level=9
    result = LoadCachedPhasor(repo, session).execute("ch0")
    assert result.cached_filter_level == 9


def test_cached_filter_level_none_without_attr(session, repo):
    """Filtered cache present but no filter_level attr → None (so the caller
    treats the level as unknown and recomputes)."""
    _seed_full_cache(repo)
    repo.attrs.pop("phasor/ch0/g_filtered", None)
    result = LoadCachedPhasor(repo, session).execute("ch0")
    assert result.cached_filter_level is None


def test_cached_filter_level_none_when_no_filtered_cache(session, repo):
    """Raw-only cache (no wavelet) → cached_filter_level is None."""
    rng = np.random.default_rng(0)
    repo.arrays["phasor/ch0/g"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/ch0/s"] = rng.uniform(size=(4, 4)).astype(np.float32)
    result = LoadCachedPhasor(repo, session).execute("ch0")
    assert result.cached_filter_level is None


def test_cached_filter_level_none_when_repo_lacks_attr_reader(session):
    """A repo without read_array_attrs (older fake) → None, no crash."""

    class NoAttrRepo:
        def __init__(self):
            self.arrays: dict[str, np.ndarray] = {}

        def read_array(self, handle, path, view_bin=1):
            if path not in self.arrays:
                raise KeyError(path)
            return self.arrays[path]

    repo = NoAttrRepo()
    z = np.zeros((4, 4), dtype=np.float32)
    repo.arrays["phasor/ch0/g"] = z
    repo.arrays["phasor/ch0/s"] = z
    repo.arrays["phasor/ch0/g_filtered"] = z
    repo.arrays["phasor/ch0/s_filtered"] = z
    result = LoadCachedPhasor(repo, session).execute("ch0")
    assert result.cached_filter_level is None
    assert result.g_filtered is not None  # cache itself still loads


# ── Error paths ───────────────────────────────────────────────


def test_no_dataset_raises(repo):
    s = Session()  # no dataset set
    uc = LoadCachedPhasor(repo, s)
    with pytest.raises(NoDatasetError):
        uc.execute("ch0")


def test_no_cache_raises_no_cached_phasor_error(session, repo):
    """Empty repo → NoCachedPhasorError carrying the channel name."""
    uc = LoadCachedPhasor(repo, session)
    with pytest.raises(NoCachedPhasorError) as exc_info:
        uc.execute("ch0")
    assert "ch0" in str(exc_info.value)


def test_asymmetric_cache_g_without_s_raises_no_cached(session, repo, caplog):
    """Defensive: g present but s missing → treat as no cache, log warning."""
    repo.arrays["phasor/ch0/g"] = np.zeros((4, 4), dtype=np.float32)
    # No s

    with pytest.raises(NoCachedPhasorError):
        LoadCachedPhasor(repo, session).execute("ch0")

    # The warning should have fired
    assert any(
        "Asymmetric phasor cache" in rec.message for rec in caplog.records
    )


# ── Filtered asymmetry ────────────────────────────────────────


def test_asymmetric_filtered_cache_treated_as_no_filtered(session, repo, caplog):
    """g_filtered without s_filtered → both returned None, warning logged."""
    rng = np.random.default_rng(0)
    repo.arrays["phasor/ch0/g"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/ch0/s"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/ch0/g_filtered"] = rng.uniform(size=(4, 4)).astype(np.float32)
    # No s_filtered

    result = LoadCachedPhasor(repo, session).execute("ch0")

    assert result.g_filtered is None
    assert result.s_filtered is None
    # The raw cache still loads cleanly
    assert result.g_map is not None
    assert result.s_map is not None
    assert any(
        "Asymmetric wavelet cache" in rec.message for rec in caplog.records
    )


# ── Dtype + shape contract ────────────────────────────────────


def test_intensity_shape_matches_g_map(session, repo):
    _seed_full_cache(repo)
    result = LoadCachedPhasor(repo, session).execute("ch0")
    assert result.intensity.shape == result.g_map.shape


def test_no_h5py_imports_in_use_case():
    """The use case must go through the repo port, not h5py directly."""
    import percell4.application.use_cases.load_cached_phasor as mod
    src = Path(mod.__file__).read_text()
    assert "import h5py" not in src
    assert "from h5py" not in src


# ── view_bin forwarding (post-U14 caller-wiring fix) ─────────


class _RecordingRepo:
    """Repo that records every (path, view_bin) read so a test can
    assert the use case forwards view_bin to every store read."""

    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.reads: list[tuple[str, int]] = []

    def read_array(self, handle, path, view_bin=1):
        self.reads.append((path, view_bin))
        if path not in self.arrays:
            raise KeyError(path)
        return self.arrays[path]


def test_load_cached_phasor_forwards_view_bin_to_every_read(session):
    """Each of the 5 reads (g, s, g_filtered, s_filtered, decay/<ch>)
    receives the caller's view_bin so the store dispatch downsamples
    them at the active bin. Pre-fix this missed every read."""
    repo = _RecordingRepo()
    repo.arrays["phasor/ch0/g"] = np.zeros((8, 8), dtype=np.float32)
    repo.arrays["phasor/ch0/s"] = np.zeros((8, 8), dtype=np.float32)
    repo.arrays["phasor/ch0/g_filtered"] = np.zeros((8, 8), dtype=np.float32)
    repo.arrays["phasor/ch0/s_filtered"] = np.zeros((8, 8), dtype=np.float32)
    repo.arrays["decay/ch0"] = np.zeros((8, 8, 4), dtype=np.float32)

    LoadCachedPhasor(repo, session).execute("ch0", view_bin=3)

    # Every read must have view_bin == 3.
    assert repo.reads, "no reads recorded"
    for path, vb in repo.reads:
        assert vb == 3, f"read of {path} used view_bin={vb}, expected 3"
    # Every expected path was read.
    paths = {p for p, _ in repo.reads}
    assert "phasor/ch0/g" in paths
    assert "phasor/ch0/s" in paths
    assert "phasor/ch0/g_filtered" in paths
    assert "phasor/ch0/s_filtered" in paths
    assert "decay/ch0" in paths


def test_load_cached_phasor_default_view_bin_is_one(session):
    """Backward-compat: omitting view_bin reads at k=1."""
    repo = _RecordingRepo()
    repo.arrays["phasor/ch0/g"] = np.zeros((8, 8), dtype=np.float32)
    repo.arrays["phasor/ch0/s"] = np.zeros((8, 8), dtype=np.float32)

    LoadCachedPhasor(repo, session).execute("ch0")
    assert all(vb == 1 for _, vb in repo.reads)
