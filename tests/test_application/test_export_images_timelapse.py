"""Per-timepoint image export (U19)."""

from __future__ import annotations

import numpy as np
import tifffile

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.application.use_cases.batch_export_images import _enumerate_channels
from percell4.application.use_cases.export_images import ExportImages, ExportRequest
from percell4.store import DatasetStore

# ── _enumerate_channels: strip the leading T axis ─────────────


def test_enumerate_strips_leading_t_thw():
    # (T,H,W) on a time-lapse dataset -> ONE channel (T is not a channel).
    assert _enumerate_channels((3, 8, 8), ["GFP"], n_timepoints=3) == [("GFP", 0)]


def test_enumerate_strips_leading_t_tchw():
    out = _enumerate_channels((3, 2, 8, 8), ["GFP", "DAPI"], n_timepoints=3)
    assert out == [("GFP", 0), ("DAPI", 1)]


def test_enumerate_chw_unchanged_single_t():
    # Non-time-lapse (C,H,W) still enumerates channels (backward compat).
    out = _enumerate_channels((3, 8, 8), ["A", "B", "C"], n_timepoints=1)
    assert out == [("A", 0), ("B", 1), ("C", 2)]


# ── ExportImages.execute: one TIFF per timepoint ──────────────


def test_export_timelapse_writes_per_t_tiffs(tmp_path):
    h5 = tmp_path / "movie.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    intensity = np.zeros((3, 2, 8, 8), dtype=np.uint16)
    for t in range(3):
        for c in range(2):
            intensity[t, c] = t * 10 + c
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})
    labels = np.zeros((3, 8, 8), dtype=np.int32)
    labels[1] = 5
    store.write_labels("cp", labels)
    store.write_mask("thr", np.ones((8, 8), dtype=np.uint8))  # 2D time-invariant

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    assert handle.metadata["n_timepoints"] == 3

    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out,
        dataset_name="movie",
        channels=[("GFP", 0), ("DAPI", 1)],
        labels=["cp"],
        masks=["thr"],
    )
    result = ExportImages(repo).execute(handle, req)

    # (2 channels + 1 label + 1 mask) * 3 timepoints = 12 files.
    assert result.exported_count == 12
    names = {p.name for p in out.glob("*.tif")}
    assert {"movie_GFP_t00.tif", "movie_GFP_t02.tif", "movie_DAPI_t01.tif"} <= names
    assert {"movie_cp_t00.tif", "movie_cp_t01.tif", "movie_thr_t02.tif"} <= names

    # Frame content: each file is the correct 2D frame.
    gfp_t2 = tifffile.imread(out / "movie_GFP_t02.tif")
    assert gfp_t2.shape == (8, 8) and np.all(gfp_t2 == 20)  # t=2, c=GFP(0)
    dapi_t1 = tifffile.imread(out / "movie_DAPI_t01.tif")
    assert np.all(dapi_t1 == 11)  # t=1, c=DAPI(1)
    cp_t1 = tifffile.imread(out / "movie_cp_t01.tif")
    assert cp_t1[0, 0] == 5
    # The 2D time-invariant mask broadcasts to every timepoint.
    thr_t0 = tifffile.imread(out / "movie_thr_t00.tif")
    assert thr_t0.shape == (8, 8) and thr_t0.all()


def test_export_single_timepoint_unchanged(tmp_path):
    """Single-timepoint export keeps the historical filenames (no _t suffix)."""
    h5 = tmp_path / "still.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    store.write_array(
        "intensity", np.zeros((2, 8, 8), dtype=np.uint16),
        attrs={"dims": ["C", "H", "W"]},
    )
    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)

    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out, dataset_name="still",
        channels=[("GFP", 0), ("DAPI", 1)], labels=[], masks=[],
    )
    result = ExportImages(repo).execute(handle, req)
    assert result.exported_count == 2
    names = {p.name for p in out.glob("*.tif")}
    assert names == {"still_GFP.tif", "still_DAPI.tif"}  # no _t suffix


def test_export_non_time_multichannel_chw(tmp_path):
    """(C,H,W) single-t multichannel: one file per channel, no _t suffix,
    each channel's pixels routed to the right file (proves data routing)."""
    h5 = tmp_path / "chw.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["A", "B", "C"]})
    intensity = np.zeros((3, 8, 8), dtype=np.uint16)
    for c in range(3):
        intensity[c] = 100 + c  # distinct constant per channel
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    seg = np.zeros((8, 8), dtype=np.int32)
    seg[0, 0] = 7  # 2D label (single-t)
    store.write_labels("seg", seg)
    store.write_mask("m", np.ones((8, 8), dtype=np.uint8))

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    assert handle.metadata["n_timepoints"] == 1  # dims has no leading T

    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out, dataset_name="chw",
        channels=[("A", 0), ("B", 1), ("C", 2)], labels=["seg"], masks=["m"],
    )
    result = ExportImages(repo).execute(handle, req)

    # 3 channels + 1 label + 1 mask = 5 files, none with a _t suffix.
    assert result.exported_count == 5
    names = {p.name for p in out.glob("*.tif")}
    assert names == {"chw_A.tif", "chw_B.tif", "chw_C.tif", "chw_seg.tif", "chw_m.tif"}
    assert not any("_t" in n for n in names)

    # Each channel's pixels land in its own file (routing by channel index).
    assert np.all(tifffile.imread(out / "chw_A.tif") == 100)
    assert np.all(tifffile.imread(out / "chw_B.tif") == 101)
    assert np.all(tifffile.imread(out / "chw_C.tif") == 102)
    assert tifffile.imread(out / "chw_seg.tif")[0, 0] == 7
    assert tifffile.imread(out / "chw_m.tif").all()


def test_export_single_channel_timelapse_thw(tmp_path):
    """(T,H,W) single-channel time-lapse: one file per timepoint carrying the
    single channel's name + _t{NN}, each frame routed to the right file."""
    h5 = tmp_path / "thw.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP"]})
    intensity = np.zeros((3, 8, 8), dtype=np.uint16)
    for t in range(3):
        intensity[t] = t * 7  # distinct constant per frame
    store.write_array("intensity", intensity, attrs={"dims": ["T", "H", "W"]})

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    assert handle.metadata["n_timepoints"] == 3

    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out, dataset_name="thw",
        channels=[("GFP", 0)], labels=[], masks=[],
    )
    result = ExportImages(repo).execute(handle, req)

    # One file per timepoint, channel name + _t suffix.
    assert result.exported_count == 3
    names = {p.name for p in out.glob("*.tif")}
    assert names == {"thw_GFP_t00.tif", "thw_GFP_t01.tif", "thw_GFP_t02.tif"}

    # Each timepoint's frame routes to the matching _t file.
    assert np.all(tifffile.imread(out / "thw_GFP_t00.tif") == 0)
    assert np.all(tifffile.imread(out / "thw_GFP_t01.tif") == 7)
    assert np.all(tifffile.imread(out / "thw_GFP_t02.tif") == 14)


def test_export_2d_mask_broadcasts_thw_labels_slice(tmp_path):
    """Over a (T,C,H,W) dataset: a 2D mask broadcasts the SAME plane to every
    timepoint's file, while a (T,H,W) labels resource slices per frame."""
    h5 = tmp_path / "mix.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    intensity = np.zeros((3, 2, 8, 8), dtype=np.uint16)
    for t in range(3):
        for c in range(2):
            intensity[t, c] = t * 10 + c
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})

    # 2D time-invariant mask with a NON-uniform pattern (broadcasts as-is).
    gate = np.zeros((8, 8), dtype=np.uint8)
    gate[0, 0] = 1
    gate[5, 6] = 1
    store.write_mask("gate", gate)

    # (T,H,W) labels: a distinct constant plane per frame (slices per frame).
    seg = np.zeros((3, 8, 8), dtype=np.int32)
    for t in range(3):
        seg[t] = t + 1
    store.write_labels("seg", seg)

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    assert handle.metadata["n_timepoints"] == 3

    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out, dataset_name="mix",
        channels=[("GFP", 0), ("DAPI", 1)], labels=["seg"], masks=["gate"],
    )
    result = ExportImages(repo).execute(handle, req)

    # (2 channels + 1 label + 1 mask) * 3 timepoints = 12 files.
    assert result.exported_count == 12
    names = {p.name for p in out.glob("*.tif")}
    assert {"mix_gate_t00.tif", "mix_gate_t01.tif", "mix_gate_t02.tif"} <= names
    assert {"mix_seg_t00.tif", "mix_seg_t01.tif", "mix_seg_t02.tif"} <= names

    # Channel routing across (channel, timepoint).
    assert np.all(tifffile.imread(out / "mix_GFP_t02.tif") == 20)
    assert np.all(tifffile.imread(out / "mix_DAPI_t01.tif") == 11)

    # The 2D mask broadcasts the IDENTICAL plane to every timepoint.
    gate_planes = [
        tifffile.imread(out / f"mix_gate_t0{t}.tif") for t in range(3)
    ]
    for plane in gate_planes:
        assert np.array_equal(plane, gate)
    assert np.array_equal(gate_planes[0], gate_planes[1])
    assert np.array_equal(gate_planes[1], gate_planes[2])

    # The (T,H,W) labels resource slices a DIFFERENT plane per frame.
    assert np.all(tifffile.imread(out / "mix_seg_t00.tif") == 1)
    assert np.all(tifffile.imread(out / "mix_seg_t01.tif") == 2)
    assert np.all(tifffile.imread(out / "mix_seg_t02.tif") == 3)


def test_export_single_t_single_channel_historical(tmp_path):
    """Single-t single-channel keeps the historical filename: no _t suffix,
    no channel duplication — exactly one file."""
    h5 = tmp_path / "one.h5"
    store = DatasetStore(h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.full((8, 8), 42, dtype=np.uint16),
        attrs={"dims": ["H", "W"]},
    )
    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    assert handle.metadata["n_timepoints"] == 1

    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out, dataset_name="one",
        channels=[("GFP", 0)], labels=[], masks=[],
    )
    result = ExportImages(repo).execute(handle, req)

    assert result.exported_count == 1
    names = {p.name for p in out.glob("*.tif")}
    assert names == {"one_GFP.tif"}  # no _t, no duplication
    assert np.all(tifffile.imread(out / "one_GFP.tif") == 42)
