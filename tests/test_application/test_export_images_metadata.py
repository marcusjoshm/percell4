"""ExportImages must thread pixel_size_um through to TIFF writes.

Companion to ``test_export_images_view_bin.py`` (which covers the
``view_bin`` forward). The two together pin the contract:
``ExportRequest(view_bin=k, pixel_size_um=p)`` → output TIFFs carry
resolution tags decoding back to ``p × k``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.adapters.readers import read_tiff_metadata
from percell4.application.use_cases.export_images import (
    ExportImages,
    ExportRequest,
)
from percell4.domain.dataset import DatasetHandle


def _make_real_h5(
    path: Path, pixel_size_um: float | None = 0.12034,
    intensity_shape: tuple[int, int, int] = (2, 16, 16),
) -> Path:
    """Stand up a real .h5 with optional pixel_size_um in /metadata."""
    from percell4.store import DatasetStore

    store = DatasetStore(path)
    meta: dict = {"channel_names": ["ch0", "ch1"]}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)
    arr = np.zeros(intensity_shape, dtype=np.uint16)
    for i in range(arr.shape[0]):
        arr[i, ...] = (i + 1) * 100
    store.write_array("intensity", arr)
    store.write_labels("seg", np.ones(intensity_shape[-2:], dtype=np.int32))
    store.write_mask("threshold", np.ones(intensity_shape[-2:], dtype=np.uint8))
    return path


def test_channels_carry_pixel_size_at_native_bin(tmp_path):
    """A single-dataset export at view_bin=1 round-trips pixel_size_um."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5 = _make_real_h5(tmp_path / "ds.h5", pixel_size_um=0.12034)
    handle = DatasetHandle(path=h5, metadata={"channel_names": ["ch0", "ch1"]})

    repo = Hdf5DatasetRepository()
    out = tmp_path / "out"
    ExportImages(repo).execute(
        handle,
        ExportRequest(
            output_folder=out,
            dataset_name="ds",
            channels=[("ch0", 0)],
            labels=["seg"],
            masks=["threshold"],
            view_bin=1,
            pixel_size_um=0.12034,
        ),
    )

    for name in ("ch0", "seg", "threshold"):
        meta = read_tiff_metadata(out / f"ds_{name}.tif")
        assert meta["pixel_size_um"] == pytest.approx(0.12034, abs=1e-4)


def test_view_bin_two_emits_scaled_resolution(tmp_path):
    """view_bin=2 → exported file describes its own 2× coarser sampling."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5 = _make_real_h5(tmp_path / "ds.h5", pixel_size_um=0.12034)
    handle = DatasetHandle(path=h5, metadata={"channel_names": ["ch0", "ch1"]})

    repo = Hdf5DatasetRepository()
    out = tmp_path / "out"
    ExportImages(repo).execute(
        handle,
        ExportRequest(
            output_folder=out,
            dataset_name="ds",
            channels=[("ch0", 0)],
            labels=["seg"],
            masks=["threshold"],
            view_bin=2,
            pixel_size_um=0.12034,
        ),
    )

    for name in ("ch0", "seg", "threshold"):
        meta = read_tiff_metadata(out / f"ds_{name}.tif")
        assert meta["pixel_size_um"] == pytest.approx(0.24068, abs=1e-4)


def test_missing_pixel_size_omits_resolution_tags(tmp_path):
    """Legacy dataset path: no pixel_size_um → exports skip resolution tags."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5 = _make_real_h5(tmp_path / "ds.h5", pixel_size_um=None)
    handle = DatasetHandle(path=h5, metadata={"channel_names": ["ch0", "ch1"]})

    repo = Hdf5DatasetRepository()
    out = tmp_path / "out"
    ExportImages(repo).execute(
        handle,
        ExportRequest(
            output_folder=out,
            dataset_name="ds",
            channels=[("ch0", 0)],
            labels=[],
            masks=[],
            view_bin=1,
            pixel_size_um=None,
        ),
    )

    meta = read_tiff_metadata(out / "ds_ch0.tif")
    assert "pixel_size_um" not in meta


# ── Batch orchestrator reads pixel_size_um per dataset ────────────────


def test_batch_export_reads_pixel_size_per_dataset(tmp_path):
    """batch_export_images resolves pixel_size_um from each dataset's
    own /metadata, so heterogeneous datasets don't cross-contaminate."""
    from percell4.application.use_cases.batch_export_images import (
        batch_export_images,
    )

    h5_a = _make_real_h5(tmp_path / "a.h5", pixel_size_um=0.1)
    h5_b = _make_real_h5(tmp_path / "b.h5", pixel_size_um=0.5)

    out = tmp_path / "out"
    report = batch_export_images([h5_a, h5_b], output_dir=out, view_bin=1)
    assert report.total_succeeded == 2

    meta_a = read_tiff_metadata(out / "a_ch0.tif")
    meta_b = read_tiff_metadata(out / "b_ch0.tif")
    assert meta_a["pixel_size_um"] == pytest.approx(0.1, abs=1e-4)
    assert meta_b["pixel_size_um"] == pytest.approx(0.5, abs=1e-4)
