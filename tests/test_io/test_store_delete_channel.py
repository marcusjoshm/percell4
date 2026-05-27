"""Tests for DatasetStore.delete_channel.

Canonical channel-delete method that sweeps every per-channel surface
in one call: ``/decay/<name>``, ``/phasor/<name>/{g,s,...}``, removal
from ``channel_names`` metadata, and the per-channel FLIM calibration
attrs ``flim_cal_phase_<name>`` / ``flim_cal_mod_<name>``.

The method is the symmetric pair to ``rename_channel``. The seg-QC
recovery work already inlines this logic in the FastAPI ``/delete``
endpoint (on the ``feat/tauri-react-ui`` branch); this method
canonicalizes it so the batch CLI in U3 and the backend endpoint can
share one implementation.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.store import DatasetStore


def _make_h5(path: Path, *, channels: list[str], with_phasor: list[str] | None = None) -> None:
    """Create a minimal .h5 with /decay/<ch>, metadata.channel_names,
    flim calibration attrs, and optional /phasor/<ch>/g."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels
        meta.attrs["flim_frequency_mhz"] = 80.0
        for ch in channels:
            meta.attrs[f"flim_cal_phase_{ch}"] = 0.1
            meta.attrs[f"flim_cal_mod_{ch}"] = 0.9
        decay = f.create_group("decay")
        for ch in channels:
            decay.create_dataset(ch, data=np.zeros((4, 4, 8), dtype=np.float32))
        if with_phasor:
            phasor = f.create_group("phasor")
            for ch in with_phasor:
                g = phasor.create_group(ch)
                g.create_dataset("g", data=np.zeros((4, 4), dtype=np.float32))
                g.create_dataset("s", data=np.zeros((4, 4), dtype=np.float32))


def test_delete_channel_removes_decay(tmp_path):
    """Happy path: /decay/<name> is gone after delete."""
    p = tmp_path / "ds.h5"
    _make_h5(p, channels=["ch0"])
    store = DatasetStore(p)

    deleted = store.delete_channel("ch0")

    assert deleted is True
    with h5py.File(p, "r") as f:
        assert "decay/ch0" not in f


def test_delete_channel_removes_phasor(tmp_path):
    """/phasor/<name>/* is also removed."""
    p = tmp_path / "ds.h5"
    _make_h5(p, channels=["ch0"], with_phasor=["ch0"])
    store = DatasetStore(p)

    deleted = store.delete_channel("ch0")

    assert deleted is True
    with h5py.File(p, "r") as f:
        assert "decay/ch0" not in f
        assert "phasor/ch0" not in f


def test_delete_channel_prunes_channel_names(tmp_path):
    """channel_names metadata loses the deleted entry."""
    p = tmp_path / "ds.h5"
    _make_h5(p, channels=["ch0", "ch1"])
    store = DatasetStore(p)

    store.delete_channel("ch0")

    with h5py.File(p, "r") as f:
        names = list(f["metadata"].attrs["channel_names"])
        assert names == ["ch1"]


def test_delete_channel_drops_flim_calibration_attrs(tmp_path):
    """flim_cal_phase_<name> and flim_cal_mod_<name> are both gone."""
    p = tmp_path / "ds.h5"
    _make_h5(p, channels=["ch0"])
    store = DatasetStore(p)

    store.delete_channel("ch0")

    with h5py.File(p, "r") as f:
        attrs = dict(f["metadata"].attrs)
        assert "flim_cal_phase_ch0" not in attrs
        assert "flim_cal_mod_ch0" not in attrs
        # The dataset-wide attr is untouched.
        assert attrs["flim_frequency_mhz"] == pytest.approx(80.0)


def test_delete_channel_returns_false_when_absent(tmp_path):
    """delete_channel returns False if the channel doesn't exist anywhere."""
    p = tmp_path / "ds.h5"
    _make_h5(p, channels=["ch0"])
    store = DatasetStore(p)

    deleted = store.delete_channel("nope")

    assert deleted is False
    # No state mutated.
    with h5py.File(p, "r") as f:
        assert "decay/ch0" in f
        names = list(f["metadata"].attrs["channel_names"])
        assert names == ["ch0"]
        assert "flim_cal_phase_ch0" in dict(f["metadata"].attrs)


def test_delete_channel_preserves_unrelated_channels(tmp_path):
    """Other channels are untouched after deleting one."""
    p = tmp_path / "ds.h5"
    _make_h5(p, channels=["ch0", "ch1"], with_phasor=["ch0", "ch1"])
    store = DatasetStore(p)

    store.delete_channel("ch0")

    with h5py.File(p, "r") as f:
        assert "decay/ch1" in f
        assert "phasor/ch1/g" in f
        assert "phasor/ch1/s" in f
        names = list(f["metadata"].attrs["channel_names"])
        assert names == ["ch1"]
        attrs = dict(f["metadata"].attrs)
        assert "flim_cal_phase_ch1" in attrs
        assert "flim_cal_mod_ch1" in attrs


def test_delete_channel_only_phasor_no_decay(tmp_path):
    """Channel with /phasor/<name> but no /decay/<name>: still deletes
    the phasor side and returns True. Defensive against orphaned phasor
    data that could exist if a manual edit removed decay but left phasor."""
    p = tmp_path / "ds.h5"
    _make_h5(p, channels=["ch0"], with_phasor=["ch0"])
    # Manually drop /decay/ch0 to simulate the orphan state.
    with h5py.File(p, "a") as f:
        del f["decay/ch0"]

    store = DatasetStore(p)
    deleted = store.delete_channel("ch0")

    assert deleted is True
    with h5py.File(p, "r") as f:
        assert "phasor/ch0" not in f


def test_delete_channel_no_metadata_group(tmp_path):
    """delete_channel handles datasets that have decay but no metadata
    group (legacy or partial files). Should still return True if decay
    was deleted."""
    p = tmp_path / "ds.h5"
    with h5py.File(p, "w") as f:
        decay = f.create_group("decay")
        decay.create_dataset("ch0", data=np.zeros((4, 4, 8), dtype=np.float32))

    store = DatasetStore(p)
    deleted = store.delete_channel("ch0")

    assert deleted is True
    with h5py.File(p, "r") as f:
        assert "decay/ch0" not in f
