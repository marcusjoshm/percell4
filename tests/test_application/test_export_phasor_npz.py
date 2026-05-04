"""Tests for ExportPhasorNpz use case.

Atomic-write contract: tempfile.mkstemp + os.replace, with explicit
file object passed to np.savez (defeating the .npz auto-suffix
behavior).

Round-trip + schema invariants:
- Intensity is NOT in the .npz (decay-derived; importer reconstructs).
- All channels iterated via _read_fresh_metadata (NOT handle.metadata).
- Channels with no /phasor/<ch>/g are silently skipped.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.export_phasor_npz import (
    ExportPhasorNpz,
    ExportPhasorResult,
)
from percell4.domain.dataset import DatasetHandle
from percell4.domain.errors import NoDatasetError


class FakeRepo:
    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.disk_metadata: dict = {}

    def write_array(self, handle, path, data, attrs=None):
        self.arrays[path] = data

    def read_array(self, handle, path):
        if path not in self.arrays:
            raise KeyError(f"Array not found: {path}")
        return self.arrays[path]

    def read_metadata(self, handle):
        return dict(self.disk_metadata)


@pytest.fixture
def session(tmp_path):
    s = Session()
    handle = DatasetHandle(
        path=tmp_path / "Dish_2_TAOK2.h5",
        metadata={"channel_names": ["mTQ2", "CA-SiR"], "flim_frequency_mhz": 80.0},
    )
    s._dataset = handle
    return s


@pytest.fixture
def repo_full_cache():
    repo = FakeRepo()
    repo.disk_metadata = {
        "channel_names": ["mTQ2", "CA-SiR"],
        "flim_frequency_mhz": 80.0,
    }
    rng = np.random.default_rng(0)
    for ch in ("mTQ2", "CA-SiR"):
        repo.arrays[f"phasor/{ch}/g"] = rng.uniform(size=(8, 8)).astype(np.float32)
        repo.arrays[f"phasor/{ch}/s"] = rng.uniform(size=(8, 8)).astype(np.float32)
        repo.arrays[f"phasor/{ch}/g_filtered"] = rng.uniform(size=(8, 8)).astype(np.float32)
        repo.arrays[f"phasor/{ch}/s_filtered"] = rng.uniform(size=(8, 8)).astype(np.float32)
        repo.arrays[f"phasor/{ch}/lifetime_filtered"] = rng.uniform(size=(8, 8)).astype(np.float32)
        repo.arrays[f"decay/{ch}"] = rng.uniform(size=(8, 8, 16)).astype(np.float32)
    return repo


# ── Happy path ────────────────────────────────────────────────


def test_full_cache_writes_one_file_per_channel(tmp_path, session, repo_full_cache):
    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo_full_cache, session).execute(out)

    assert isinstance(result, ExportPhasorResult)
    assert len(result.exported) == 2
    assert len(result.skipped) == 0
    written = sorted(p.name for _, p in result.exported)
    assert written == ["Dish_2_TAOK2_CA-SiR_phasor.npz", "Dish_2_TAOK2_mTQ2_phasor.npz"]


def test_npz_carries_documented_keys(tmp_path, session, repo_full_cache):
    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo_full_cache, session).execute(out)
    _, path = result.exported[0]

    data = np.load(path, allow_pickle=True)
    keys = set(data.files)
    assert {"g", "s", "g_filtered", "s_filtered", "lifetime_filtered", "metadata"} <= keys
    # CRITICAL: intensity is NOT in the .npz (decay-derived; importer reconstructs)
    assert "intensity" not in keys


def test_metadata_round_trip(tmp_path, session, repo_full_cache):
    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo_full_cache, session).execute(out)
    _, path = next(p for p in result.exported if "mTQ2" in p[1].name)

    data = np.load(path, allow_pickle=True)
    meta = data["metadata"].item()
    assert meta["schema_version"] == 1
    assert meta["channel"] == "mTQ2"
    assert meta["flim_frequency_mhz"] == 80.0
    assert meta["source_dataset_stem"] == "Dish_2_TAOK2"


# ── np.savez suffix verification ──────────────────────────────


def test_filename_does_not_get_double_npz_suffix(tmp_path, session, repo_full_cache):
    """Regression guard: np.savez auto-appends .npz to a path argument.
    Using an explicit file object (open(path, 'wb')) defeats this.
    """
    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo_full_cache, session).execute(out)

    for _, path in result.exported:
        # MUST end in exactly .npz, NOT .npz.npz
        assert path.name.endswith("_phasor.npz")
        assert not path.name.endswith(".npz.npz")
        # File actually exists at the declared path
        assert path.exists()


# ── Skip behavior ─────────────────────────────────────────────


def test_channel_with_no_cache_skipped_silently(tmp_path, session):
    repo = FakeRepo()
    repo.disk_metadata = {"channel_names": ["mTQ2", "CA-SiR"]}
    # Only mTQ2 has cache
    rng = np.random.default_rng(0)
    repo.arrays["phasor/mTQ2/g"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/mTQ2/s"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["decay/mTQ2"] = rng.uniform(size=(4, 4, 8)).astype(np.float32)
    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo, session).execute(out)

    assert len(result.exported) == 1
    assert "CA-SiR" in result.skipped
    assert len(list(out.glob("*.npz"))) == 1


def test_empty_dataset_writes_no_files(tmp_path, session):
    repo = FakeRepo()
    repo.disk_metadata = {"channel_names": ["mTQ2", "CA-SiR"]}
    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo, session).execute(out)

    assert len(result.exported) == 0
    assert len(result.skipped) == 2
    assert len(list(out.glob("*.npz"))) == 0


def test_raw_only_cache_writes_minimal_file(tmp_path, session):
    """Raw cache without wavelet → file written with g, s, metadata only."""
    repo = FakeRepo()
    repo.disk_metadata = {"channel_names": ["mTQ2"]}
    rng = np.random.default_rng(0)
    repo.arrays["phasor/mTQ2/g"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/mTQ2/s"] = rng.uniform(size=(4, 4)).astype(np.float32)
    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo, session).execute(out)

    _, path = result.exported[0]
    data = np.load(path, allow_pickle=True)
    assert "g" in data.files
    assert "s" in data.files
    assert "metadata" in data.files
    assert "g_filtered" not in data.files
    assert "s_filtered" not in data.files


# ── Stale-metadata regression guard ───────────────────────────


def test_iterates_fresh_metadata_not_handle_snapshot(tmp_path, session, repo_full_cache):
    """If handle.metadata is stale (different from disk), use disk."""
    # Stale snapshot says only mTQ2; live metadata adds CA-SiR
    session.dataset.metadata["channel_names"] = ["mTQ2"]  # stale snapshot
    # repo.disk_metadata still has both

    out = tmp_path / "out"
    out.mkdir()

    result = ExportPhasorNpz(repo_full_cache, session).execute(out)

    # Both channels exported because we iterate from disk_metadata
    assert len(result.exported) == 2


# ── Atomic write failure cleanup ──────────────────────────────


def test_atomic_write_failure_leaves_no_tmp_file(tmp_path, session, repo_full_cache):
    """Monkeypatch np.savez to fail; assert no .tmp orphans."""
    out = tmp_path / "out"
    out.mkdir()

    with patch("numpy.savez", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError):
            ExportPhasorNpz(repo_full_cache, session).execute(out)

    tmp_files = list(out.glob("*.tmp"))
    assert tmp_files == [], f"Orphan tmp files left: {tmp_files}"


# ── No dataset ────────────────────────────────────────────────


def test_no_dataset_raises(tmp_path):
    repo = FakeRepo()
    s = Session()
    with pytest.raises(NoDatasetError):
        ExportPhasorNpz(repo, s).execute(tmp_path)


# ── No h5py imports ──────────────────────────────────────────


def test_no_h5py_imports_in_use_case():
    import percell4.application.use_cases.export_phasor_npz as mod
    src = Path(mod.__file__).read_text()
    assert "import h5py" not in src
    assert "from h5py" not in src
