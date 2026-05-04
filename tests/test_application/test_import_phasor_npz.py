"""Tests for ImportPhasorNpz use case.

Critical security guardrail: channel-name sanitization runs FIRST,
before any HDF5 access. Without this, a malicious .npz with
metadata['channel'] = '../segmentation/cellpose' could overwrite
arbitrary HDF5 groups via delete_path / write_array.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.export_phasor_npz import ExportPhasorNpz
from percell4.application.use_cases.import_phasor_npz import (
    ImportPhasorNpz,
    ImportPhasorResult,
)
from percell4.application.use_cases.load_cached_phasor import LoadCachedPhasor
from percell4.domain.dataset import DatasetHandle
from percell4.domain.errors import NoDatasetError
from percell4.store import LayerAlreadyExistsError


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

    def delete_path(self, handle, path):
        # Mimic group-delete semantics: clear all arrays under the prefix.
        prefix = path.rstrip("/") + "/"
        keys = [k for k in list(self.arrays) if k.startswith(prefix) or k == path]
        for k in keys:
            del self.arrays[k]
        return bool(keys)


@pytest.fixture
def session(tmp_path):
    s = Session()
    handle = DatasetHandle(
        path=tmp_path / "dataset.h5",
        metadata={"channel_names": ["mTQ2"], "flim_frequency_mhz": 80.0},
    )
    s._dataset = handle
    return s


@pytest.fixture
def repo():
    return FakeRepo()


def _make_npz(tmp_path: Path, name: str, **arrays) -> Path:
    """Helper: write an .npz with the given keys, return its path."""
    path = tmp_path / name
    np.savez(path, **arrays)
    # np.savez auto-appends .npz only if missing
    if not path.exists():
        path = path.with_suffix(path.suffix + ".npz")
    return path


def _full_bundle_npz(tmp_path: Path, name: str = "test.npz", channel: str = "mTQ2"):
    rng = np.random.default_rng(0)
    metadata = {
        "schema_version": 1,
        "channel": channel,
        "flim_frequency_mhz": 80.0,
        "source_dataset_stem": "src",
    }
    return _make_npz(
        tmp_path, name,
        g=rng.uniform(size=(8, 8)).astype(np.float32),
        s=rng.uniform(size=(8, 8)).astype(np.float32),
        g_filtered=rng.uniform(size=(8, 8)).astype(np.float32),
        s_filtered=rng.uniform(size=(8, 8)).astype(np.float32),
        lifetime_filtered=rng.uniform(size=(8, 8)).astype(np.float32),
        metadata=np.array(metadata, dtype=object),
    )


# ── Happy path ────────────────────────────────────────────────


def test_full_bundle_writes_all_arrays(tmp_path, session, repo):
    npz = _full_bundle_npz(tmp_path)
    result = ImportPhasorNpz(repo, session).execute(npz, "mTQ2")

    assert isinstance(result, ImportPhasorResult)
    assert result.target_channel == "mTQ2"
    assert result.wrote_filtered is True
    assert "phasor/mTQ2/g" in repo.arrays
    assert "phasor/mTQ2/s" in repo.arrays
    assert "phasor/mTQ2/g_filtered" in repo.arrays
    assert "phasor/mTQ2/s_filtered" in repo.arrays
    assert "phasor/mTQ2/lifetime_filtered" in repo.arrays


def test_raw_only_npz_writes_only_g_s(tmp_path, session, repo):
    rng = np.random.default_rng(0)
    npz = _make_npz(
        tmp_path, "raw.npz",
        g=rng.uniform(size=(4, 4)).astype(np.float32),
        s=rng.uniform(size=(4, 4)).astype(np.float32),
    )
    result = ImportPhasorNpz(repo, session).execute(npz, "mTQ2")

    assert result.wrote_filtered is False
    assert "phasor/mTQ2/g" in repo.arrays
    assert "phasor/mTQ2/s" in repo.arrays
    assert "phasor/mTQ2/g_filtered" not in repo.arrays


# ── Conflict resolution ───────────────────────────────────────


def test_conflict_force_false_raises(tmp_path, session, repo):
    rng = np.random.default_rng(0)
    repo.arrays["phasor/mTQ2/g"] = rng.uniform(size=(4, 4)).astype(np.float32)

    npz = _full_bundle_npz(tmp_path)
    with pytest.raises(LayerAlreadyExistsError):
        ImportPhasorNpz(repo, session).execute(npz, "mTQ2", force=False)


def test_conflict_force_true_overwrites(tmp_path, session, repo):
    rng = np.random.default_rng(0)
    # Pre-populate target with old data + an orphan we want cleared
    repo.arrays["phasor/mTQ2/g"] = rng.uniform(size=(4, 4)).astype(np.float32)
    repo.arrays["phasor/mTQ2/orphan"] = np.zeros((4, 4), dtype=np.float32)

    npz = _full_bundle_npz(tmp_path)
    result = ImportPhasorNpz(repo, session).execute(npz, "mTQ2", force=True)

    assert result.wrote_filtered is True
    # The orphan is cleared by delete_path before re-writing
    assert "phasor/mTQ2/orphan" not in repo.arrays
    # New g written, shape is (8, 8) not (4, 4)
    assert repo.arrays["phasor/mTQ2/g"].shape == (8, 8)


# ── Channel-name sanitization (security) ──────────────────────


def test_path_traversal_via_target_channel_rejected_before_hdf5(tmp_path, session, repo):
    """Regression guard against HDF5 path traversal exploit."""
    npz = _full_bundle_npz(tmp_path)
    pre_arrays = dict(repo.arrays)

    with pytest.raises(ValueError, match="must match"):
        ImportPhasorNpz(repo, session).execute(npz, "../segmentation/cellpose")

    # No HDF5 mutation occurred
    assert repo.arrays == pre_arrays


def test_null_byte_in_channel_rejected(tmp_path, session, repo):
    npz = _full_bundle_npz(tmp_path)
    with pytest.raises(ValueError, match="must match"):
        ImportPhasorNpz(repo, session).execute(npz, "ch\x00name")


def test_slash_in_channel_rejected(tmp_path, session, repo):
    npz = _full_bundle_npz(tmp_path)
    with pytest.raises(ValueError, match="must match"):
        ImportPhasorNpz(repo, session).execute(npz, "ch/name")


def test_too_long_channel_rejected(tmp_path, session, repo):
    npz = _full_bundle_npz(tmp_path)
    with pytest.raises(ValueError, match="must match"):
        ImportPhasorNpz(repo, session).execute(npz, "x" * 65)


def test_empty_channel_rejected(tmp_path, session, repo):
    npz = _full_bundle_npz(tmp_path)
    with pytest.raises(ValueError, match="must match"):
        ImportPhasorNpz(repo, session).execute(npz, "")


def test_legitimate_channel_names_accepted(tmp_path, session, repo):
    """Real channel names like CA-SiR, mTQ2_v2, ch.0 work."""
    for ch in ("CA-SiR", "mTQ2_v2", "ch.0", "GFP_488"):
        repo.arrays.clear()
        npz = _full_bundle_npz(tmp_path, name=f"{ch}.npz", channel=ch)
        result = ImportPhasorNpz(repo, session).execute(npz, ch)
        assert result.target_channel == ch


# ── Validation ────────────────────────────────────────────────


def test_missing_g_raises_value_error(tmp_path, session, repo):
    rng = np.random.default_rng(0)
    npz = _make_npz(
        tmp_path, "no_g.npz",
        s=rng.uniform(size=(4, 4)).astype(np.float32),
    )
    with pytest.raises(ValueError, match="missing required key 'g'"):
        ImportPhasorNpz(repo, session).execute(npz, "mTQ2")


def test_missing_s_raises_value_error(tmp_path, session, repo):
    rng = np.random.default_rng(0)
    npz = _make_npz(
        tmp_path, "no_s.npz",
        g=rng.uniform(size=(4, 4)).astype(np.float32),
    )
    with pytest.raises(ValueError, match="missing required key 's'"):
        ImportPhasorNpz(repo, session).execute(npz, "mTQ2")


def test_g_s_shape_mismatch_raises(tmp_path, session, repo):
    rng = np.random.default_rng(0)
    npz = _make_npz(
        tmp_path, "shape_mismatch.npz",
        g=rng.uniform(size=(4, 4)).astype(np.float32),
        s=rng.uniform(size=(8, 8)).astype(np.float32),
    )
    with pytest.raises(ValueError, match="shape"):
        ImportPhasorNpz(repo, session).execute(npz, "mTQ2")


def test_g_filtered_without_s_filtered_raises(tmp_path, session, repo):
    rng = np.random.default_rng(0)
    npz = _make_npz(
        tmp_path, "asym.npz",
        g=rng.uniform(size=(4, 4)).astype(np.float32),
        s=rng.uniform(size=(4, 4)).astype(np.float32),
        g_filtered=rng.uniform(size=(4, 4)).astype(np.float32),
        # No s_filtered
    )
    with pytest.raises(ValueError, match="filtered"):
        ImportPhasorNpz(repo, session).execute(npz, "mTQ2")


def test_extra_top_level_keys_rejected(tmp_path, session, repo):
    """Multi-channel single-.npz files (declared non-goal) carry extra keys."""
    rng = np.random.default_rng(0)
    npz = _make_npz(
        tmp_path, "multichannel.npz",
        g=rng.uniform(size=(4, 4)).astype(np.float32),
        s=rng.uniform(size=(4, 4)).astype(np.float32),
        g_ch0=rng.uniform(size=(4, 4)).astype(np.float32),  # extra
    )
    with pytest.raises(ValueError, match="unexpected keys"):
        ImportPhasorNpz(repo, session).execute(npz, "mTQ2")


def test_intensity_in_npz_rejected(tmp_path, session, repo):
    """intensity is intentionally NOT in the schema — extra-keys check rejects it."""
    rng = np.random.default_rng(0)
    npz = _make_npz(
        tmp_path, "with_intensity.npz",
        g=rng.uniform(size=(4, 4)).astype(np.float32),
        s=rng.uniform(size=(4, 4)).astype(np.float32),
        intensity=rng.uniform(size=(4, 4)).astype(np.float32),  # rejected
    )
    with pytest.raises(ValueError, match="unexpected keys"):
        ImportPhasorNpz(repo, session).execute(npz, "mTQ2")


# ── Round-trip ────────────────────────────────────────────────


def test_export_then_import_round_trip(tmp_path, session, repo):
    """End-to-end: export a channel via U4, delete cache, re-import via U6."""
    rng = np.random.default_rng(0)
    repo.disk_metadata = {"channel_names": ["mTQ2"], "flim_frequency_mhz": 80.0}
    repo.arrays[f"phasor/mTQ2/g"] = rng.uniform(size=(8, 8)).astype(np.float32)
    repo.arrays[f"phasor/mTQ2/s"] = rng.uniform(size=(8, 8)).astype(np.float32)
    repo.arrays[f"phasor/mTQ2/g_filtered"] = rng.uniform(size=(8, 8)).astype(np.float32)
    repo.arrays[f"phasor/mTQ2/s_filtered"] = rng.uniform(size=(8, 8)).astype(np.float32)
    repo.arrays[f"phasor/mTQ2/lifetime_filtered"] = rng.uniform(size=(8, 8)).astype(np.float32)
    repo.arrays[f"decay/mTQ2"] = rng.uniform(size=(8, 8, 16)).astype(np.float32)
    original_g = repo.arrays["phasor/mTQ2/g"].copy()

    out = tmp_path / "out"
    out.mkdir()

    # Export
    export_result = ExportPhasorNpz(repo, session).execute(out)
    _, exported_path = export_result.exported[0]

    # Delete cache
    for k in list(repo.arrays):
        if k.startswith("phasor/mTQ2/"):
            del repo.arrays[k]

    # Re-import
    ImportPhasorNpz(repo, session).execute(exported_path, "mTQ2", force=False)

    # Verify reconstituted
    np.testing.assert_array_equal(repo.arrays["phasor/mTQ2/g"], original_g)
    cached = LoadCachedPhasor(repo, session).execute("mTQ2")
    np.testing.assert_array_equal(cached.g_map, original_g)


# ── No dataset ────────────────────────────────────────────────


def test_no_dataset_raises(tmp_path, repo):
    s = Session()
    npz = _full_bundle_npz(tmp_path)
    with pytest.raises(NoDatasetError):
        ImportPhasorNpz(repo, s).execute(npz, "mTQ2")
