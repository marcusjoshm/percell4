"""FLIM/phasor time-lapse discoverability guards + phasor-GMM fix (U20)."""

from __future__ import annotations

import numpy as np

from percell4.store import DatasetStore

# ── Decay-import guard ────────────────────────────────────────


def test_add_decay_timelapse_no_longer_rejected_outright(tmp_path):
    """The blanket U20 "Time-lapse FLIM is not yet supported" rejection is gone:
    time-lapse decay append now proceeds per-timepoint. Full per-frame success +
    completeness coverage lives in
    tests/test_add_decay_to_dataset.py::test_add_decay_timelapse_*."""
    from percell4.application.use_cases.add_decay_to_dataset import (
        add_decay_to_dataset,
    )
    from percell4.domain.io.cross_format import ZeroPadOffsetRule
    from percell4.domain.io.models import FlimConfig, TileConfig, TokenConfig

    h5 = tmp_path / "movie.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.zeros((3, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    src = tmp_path / "bins"
    src.mkdir()
    (src / "decay_ch00.bin").write_bytes(b"\x00" * 16)  # a .bin to get past the scan

    report = add_decay_to_dataset(
        h5, src, TokenConfig(), TileConfig(), FlimConfig(), ZeroPadOffsetRule(0, 0)
    )
    # No blanket time-lapse rejection any more (this .bin is simply unmatched).
    assert "decay" not in report.errors
    assert not any(
        "not yet supported" in m.lower() for m in report.errors.values()
    )


# ── FLIM-FRET discovery: dims-attr time-lapse detection ───────


def test_intensity_is_time_lapse_detects_thw_via_dims(tmp_path):
    from percell4.application.use_cases.flim_fret_discovery import (
        _intensity_is_time_lapse,
    )

    h5 = tmp_path / "movie.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP"]})
    # A single-channel (T,H,W) movie is only 3D -> the bare ndim>=4 check would
    # miss it; the dims attr ('T' leading) is the authoritative discriminator.
    store.write_array(
        "intensity", np.zeros((3, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    assert _intensity_is_time_lapse(h5) is True


def test_intensity_is_not_time_lapse_for_chw(tmp_path):
    from percell4.application.use_cases.flim_fret_discovery import (
        _intensity_is_time_lapse,
    )

    h5 = tmp_path / "still.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    store.write_array(
        "intensity", np.zeros((2, 8, 8), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    assert _intensity_is_time_lapse(h5) is False


# ── Phasor-GMM crash fix on a (T,H,W) segmentation ────────────


def test_run_phasor_gmm_timelapse_seg_no_crash(tmp_path):
    """A (T,H,W) active segmentation with a cell filter no longer broadcasts
    a T*H*W labels ravel against the 2D phasor (the IndexError/ValueError);
    labels are read at the active timepoint."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.application.session import Session
    from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM

    h5 = tmp_path / "movie.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.zeros((3, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    # 2D phasor g/s (no acquisition-T axis) with a gradient (non-degenerate cov).
    g = np.linspace(0.4, 0.6, 64).reshape(8, 8).astype(np.float32)
    s = np.linspace(0.2, 0.4, 64).reshape(8, 8).astype(np.float32)
    store.write_array("phasor/GFP/g", g, attrs={"dims": ["H", "W"]})
    store.write_array("phasor/GFP/s", s, attrs={"dims": ["H", "W"]})
    store.write_array("decay/GFP", np.ones((8, 8, 16), dtype=np.float32), is_decay=True)
    # A (T,H,W) segmentation with a cell -> the crash trigger.
    labels = np.zeros((3, 8, 8), dtype=np.int32)
    labels[:, 2:6, 2:6] = 1
    store.write_labels("cp", labels)

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    assert handle.metadata["n_timepoints"] == 3
    session = Session()
    session.set_dataset(handle)
    session.set_active_segmentation("cp")
    session.set_filter(frozenset({1}))  # engage the cell filter (the crash path)

    result = RunPhasorGMM(repo, session).execute(
        channel="GFP",
        shape="ellipse",
        n_components=1,
        criterion=None,
        use_filtered_gs=False,
        mask_filter_active=False,
    )
    assert result.chosen_n == 1


def test_phasor_masks_dialog_imports():
    import percell4.gui.phasor_masks_dialog  # noqa: F401
