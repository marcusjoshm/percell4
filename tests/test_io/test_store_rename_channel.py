"""Tests for DatasetStore.rename_channel.

Symmetric pair to ``delete_channel`` (see ``test_store_delete_channel.py``).
Two behaviors beyond the plain happy path are pinned here:

1. **Unnamed ``/intensity`` slices are renameable.** A dataset can carry more
   ``/intensity`` slices than ``channel_names`` entries; those slices display
   as ``ch<N>`` placeholders. Renaming one promotes it into ``channel_names``
   rather than doing nothing.
2. **A rename that matches nothing raises.** ``rename_channel`` used to be a
   silent no-op for every surface it could not find, so the Data tab reported
   "Renamed channel 'ch1' → 'ER'" for a rename that never touched the file and
   reverted on reload.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.store import DatasetStore


def _make_h5(
    path: Path,
    *,
    channels: list[str],
    n_intensity_channels: int | None = None,
    with_phasor: bool = False,
) -> None:
    """Minimal .h5 with /decay/<ch>, /metadata, FLIM cal attrs, /intensity.

    ``n_intensity_channels`` defaults to ``len(channels)``; pass a larger
    value to build the orphan-slice case (more slices than names).
    """
    n_slices = len(channels) if n_intensity_channels is None else n_intensity_channels
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels
        meta.attrs["n_channels"] = n_slices
        meta.attrs["n_timepoints"] = 1
        for ch in channels:
            meta.attrs[f"flim_cal_phase_{ch}"] = 0.1
            meta.attrs[f"flim_cal_mod_{ch}"] = 0.9
            meta.attrs[f"flim_cal_phase_{ch}_h2"] = 0.2
        decay = f.create_group("decay")
        for ch in channels:
            decay.create_dataset(ch, data=np.zeros((4, 4, 8), dtype=np.float32))
        if with_phasor:
            for ch in channels:
                f.create_dataset(f"phasor/{ch}/g", data=np.zeros((4, 4), np.float32))
        ds = f.create_dataset(
            "intensity", data=np.zeros((n_slices, 4, 4), dtype=np.float32)
        )
        ds.attrs["dims"] = ["C", "H", "W"]


# ── Named-channel rename (pre-existing behavior) ──────────────


def test_renames_every_per_channel_surface(tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["GFP", "RFP"], with_phasor=True)

    DatasetStore(p).rename_channel("GFP", "ER")

    with h5py.File(p, "r") as f:
        attrs = f["metadata"].attrs
        assert list(attrs["channel_names"]) == ["ER", "RFP"]
        assert "decay/ER" in f and "decay/GFP" not in f
        assert "phasor/ER/g" in f and "phasor/GFP" not in f
        assert attrs["flim_cal_phase_ER"] == pytest.approx(0.1)
        assert attrs["flim_cal_mod_ER"] == pytest.approx(0.9)
        assert attrs["flim_cal_phase_ER_h2"] == pytest.approx(0.2)
        assert "flim_cal_phase_GFP" not in attrs


def test_same_name_is_a_noop(tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["GFP"])
    DatasetStore(p).rename_channel("GFP", "GFP")
    assert DatasetStore(p).metadata["channel_names"] == ["GFP"]


def test_collision_on_decay_path_raises(tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["GFP", "RFP"])
    with pytest.raises(ValueError, match="already exists"):
        DatasetStore(p).rename_channel("GFP", "RFP")
    # Nothing mutated before the raise.
    assert DatasetStore(p).metadata["channel_names"] == ["GFP", "RFP"]


def test_collision_leaves_no_half_applied_rename(tmp_path):
    """A rename spans four surfaces with no HDF5 transaction, so every
    collision must be detected before the first move. Here the name collides
    but ``/decay/RFP`` does not exist — a move-then-validate order would
    rename the decay group and only then raise."""
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["GFP", "RFP"], with_phasor=True)
    with h5py.File(p, "a") as f:
        del f["decay/RFP"]  # name taken, decay path free

    with pytest.raises(ValueError, match="already exists"):
        DatasetStore(p).rename_channel("GFP", "RFP")

    with h5py.File(p, "r") as f:
        assert "decay/GFP" in f, "decay group moved before the collision raised"
        assert list(f["metadata"].attrs["channel_names"]) == ["GFP", "RFP"]
        assert "flim_cal_phase_GFP" in f["metadata"].attrs


# ── Unnamed /intensity slice (the reported bug) ───────────────


def test_renaming_unnamed_intensity_slice_promotes_it(tmp_path):
    """/intensity has 2 slices but channel_names has 1 -> slice 1 shows as
    'ch1'. Renaming it must write the name to disk, not silently no-op."""
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["G3BP1"], n_intensity_channels=2)

    DatasetStore(p).rename_channel("ch1", "ER")

    meta = DatasetStore(p).metadata
    assert meta["channel_names"] == ["G3BP1", "ER"]
    assert int(meta["n_channels"]) == 2


def test_promotion_pads_intervening_slots_with_placeholders(tmp_path):
    """Naming slice 2 of a 3-slice array must keep slice 1 addressable —
    channel_names is positional, so the gap gets its placeholder name."""
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["G3BP1"], n_intensity_channels=3)

    DatasetStore(p).rename_channel("ch2", "ER")

    assert DatasetStore(p).metadata["channel_names"] == ["G3BP1", "ch1", "ER"]


def test_promotion_makes_the_dataset_dims_consistent(tmp_path):
    """The mismatch that produced the placeholder also fails the open-time
    dims check; naming the slice resolves both."""
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["G3BP1"], n_intensity_channels=2)
    from percell4.store import DimsConsistencyError

    with pytest.raises(DimsConsistencyError):
        DatasetStore(p).check_intensity_dims_consistency()

    DatasetStore(p).rename_channel("ch1", "ER")

    DatasetStore(p).check_intensity_dims_consistency()  # no raise


def test_promotion_rejects_a_name_already_in_use(tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["G3BP1"], n_intensity_channels=2)
    with pytest.raises(ValueError, match="already exists"):
        DatasetStore(p).rename_channel("ch1", "G3BP1")


def test_placeholder_past_the_channel_axis_raises(tmp_path):
    """'ch5' on a 2-slice array names no real channel — refuse rather than
    grow channel_names past /intensity."""
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["G3BP1"], n_intensity_channels=2)
    with pytest.raises(ValueError, match="not found"):
        DatasetStore(p).rename_channel("ch5", "ER")
    assert DatasetStore(p).metadata["channel_names"] == ["G3BP1"]


# ── No-match is an error, never a silent success ──────────────


def test_unknown_channel_raises(tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["GFP"])
    with pytest.raises(ValueError, match="not found"):
        DatasetStore(p).rename_channel("nope", "ER")


def test_decay_only_channel_still_renames(tmp_path):
    """A channel present as /decay/<ch> but absent from channel_names is a
    real surface — renaming it must succeed, not trip the no-match guard."""
    p = tmp_path / "a.h5"
    _make_h5(p, channels=["GFP"])
    with h5py.File(p, "a") as f:
        f["decay"].create_dataset("orphan", data=np.zeros((4, 4, 8), np.float32))

    DatasetStore(p).rename_channel("orphan", "ER")

    with h5py.File(p, "r") as f:
        assert "decay/ER" in f and "decay/orphan" not in f
