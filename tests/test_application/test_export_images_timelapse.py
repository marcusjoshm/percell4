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
