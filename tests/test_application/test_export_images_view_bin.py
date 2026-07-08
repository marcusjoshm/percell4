"""U1 tests: ExportImages.execute must forward view_bin to every
repository read.

The dataset-wide spatial binning feature stores arrays at native
resolution and applies binning as a view-time lens (view_bin=k via
the repository read methods). The export path used to hardcode the
default view_bin=1 — these tests pin the new behavior where
``ExportRequest`` carries the bin and ``execute`` forwards it to all
three layer-type reads (intensity / labels / masks).

R7 zero-regression invariant: default view_bin=1 must behave
bit-for-bit like today.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import tifffile

from percell4.application.use_cases.export_images import (
    ExportImages,
    ExportRequest,
)
from percell4.domain.dataset import DatasetHandle

# ── Mock-repo unit tests (forwarding contract) ──────────────────────


def _make_handle(tmp_path: Path) -> DatasetHandle:
    """Minimal handle — the use case only passes it through to the repo."""
    path = tmp_path / "ds.h5"
    path.touch()
    return DatasetHandle(path=path, metadata={"channel_names": ["ch0"]})


def test_default_view_bin_is_one_when_not_specified(tmp_path):
    """ExportRequest(view_bin=...) is optional; omitting it leaves
    the default at 1. R7 zero-regression."""
    req = ExportRequest(
        output_folder=tmp_path / "out",
        dataset_name="ds",
        channels=[("ch0", 0)],
        labels=[],
        masks=[],
    )
    assert req.view_bin == 1


def test_explicit_view_bin_is_carried_on_request(tmp_path):
    """ExportRequest accepts view_bin as a constructor kwarg."""
    req = ExportRequest(
        output_folder=tmp_path / "out",
        dataset_name="ds",
        channels=[("ch0", 0)],
        labels=[],
        masks=[],
        view_bin=4,
    )
    assert req.view_bin == 4


def test_execute_forwards_view_bin_to_read_array(tmp_path):
    """ExportImages.execute must pass view_bin to read_array for
    intensity. The repo mock records the kwarg."""
    repo = MagicMock()
    repo.read_array.return_value = np.zeros((2, 16, 16), dtype=np.uint16)

    uc = ExportImages(repo)
    handle = _make_handle(tmp_path)
    req = ExportRequest(
        output_folder=tmp_path / "out",
        dataset_name="ds",
        channels=[("ch0", 0), ("ch1", 1)],
        labels=[],
        masks=[],
        view_bin=2,
    )

    uc.execute(handle, req)

    repo.read_array.assert_called_once()
    args, kwargs = repo.read_array.call_args
    # view_bin can land in either kwargs or as the third positional;
    # accept either.
    forwarded = kwargs.get("view_bin")
    if forwarded is None and len(args) >= 3:
        forwarded = args[2]
    assert forwarded == 2


def test_execute_forwards_view_bin_to_read_labels(tmp_path):
    """labels reads honor view_bin too."""
    repo = MagicMock()
    repo.read_labels.return_value = np.zeros((16, 16), dtype=np.int32)

    uc = ExportImages(repo)
    handle = _make_handle(tmp_path)
    req = ExportRequest(
        output_folder=tmp_path / "out",
        dataset_name="ds",
        channels=[],
        labels=["seg"],
        masks=[],
        view_bin=3,
    )

    uc.execute(handle, req)

    repo.read_labels.assert_called_once()
    args, kwargs = repo.read_labels.call_args
    forwarded = kwargs.get("view_bin")
    if forwarded is None and len(args) >= 3:
        forwarded = args[2]
    assert forwarded == 3


def test_execute_forwards_view_bin_to_read_mask(tmp_path):
    """mask reads honor view_bin too."""
    repo = MagicMock()
    repo.read_mask.return_value = np.zeros((16, 16), dtype=np.uint8)

    uc = ExportImages(repo)
    handle = _make_handle(tmp_path)
    req = ExportRequest(
        output_folder=tmp_path / "out",
        dataset_name="ds",
        channels=[],
        labels=[],
        masks=["threshold"],
        view_bin=4,
    )

    uc.execute(handle, req)

    repo.read_mask.assert_called_once()
    args, kwargs = repo.read_mask.call_args
    forwarded = kwargs.get("view_bin")
    if forwarded is None and len(args) >= 3:
        forwarded = args[2]
    assert forwarded == 4


def test_default_view_bin_one_propagates_to_repo_reads(tmp_path):
    """When view_bin is left at default, the repo reads still receive
    view_bin=1 explicitly — guards against the use case silently
    omitting the kwarg (which would let the repo's own default mask
    a missing forward)."""
    repo = MagicMock()
    repo.read_array.return_value = np.zeros((2, 8, 8), dtype=np.uint16)
    repo.read_labels.return_value = np.zeros((8, 8), dtype=np.int32)
    repo.read_mask.return_value = np.zeros((8, 8), dtype=np.uint8)

    uc = ExportImages(repo)
    handle = _make_handle(tmp_path)
    req = ExportRequest(
        output_folder=tmp_path / "out",
        dataset_name="ds",
        channels=[("ch0", 0)],
        labels=["seg"],
        masks=["mask"],
    )

    uc.execute(handle, req)

    for mock_call in (
        repo.read_array.call_args,
        repo.read_labels.call_args,
        repo.read_mask.call_args,
    ):
        args, kwargs = mock_call
        forwarded = kwargs.get("view_bin")
        if forwarded is None and len(args) >= 3:
            forwarded = args[2]
        assert forwarded == 1


# ── Repo-backed integration test (real binning math + on-disk TIFF) ──


def _make_real_h5(path: Path, intensity_shape=(2, 32, 32)) -> Path:
    """Real .h5 via DatasetStore so the binning paths are exercised
    end-to-end. Intensity stamped per channel to make per-file output
    distinguishable."""
    from percell4.store import DatasetStore

    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch0", "ch1"]})
    arr = np.zeros(intensity_shape, dtype=np.uint16)
    for i in range(arr.shape[0]):
        arr[i, ...] = (i + 1) * 100
    store.write_array("intensity", arr)
    store.write_labels("seg", np.ones(intensity_shape[-2:], dtype=np.int32))
    store.write_mask("threshold", np.ones(intensity_shape[-2:], dtype=np.uint8))
    return path


def test_integration_view_bin_two_produces_half_shape_tiffs(tmp_path):
    """End-to-end: real Hdf5DatasetRepository over tmp_path; view_bin=2
    on a (2, 32, 32) intensity produces TIFFs of shape (16, 16)."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5_path = _make_real_h5(tmp_path / "ds.h5", intensity_shape=(2, 32, 32))
    handle = DatasetHandle(path=h5_path, metadata={"channel_names": ["ch0", "ch1"]})

    repo = Hdf5DatasetRepository()
    uc = ExportImages(repo)
    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out,
        dataset_name="ds",
        channels=[("ch0", 0), ("ch1", 1)],
        labels=["seg"],
        masks=["threshold"],
        view_bin=2,
    )

    result = uc.execute(handle, req)
    assert result.exported_count == 4

    for name in ("ch0", "ch1", "seg", "threshold"):
        out_path = out / f"ds_{name}.tif"
        assert out_path.exists()
        data = tifffile.imread(out_path)
        assert data.shape == (16, 16), (
            f"{name} TIFF shape should be (16, 16) at view_bin=2, got {data.shape}"
        )


def test_integration_view_bin_one_matches_native(tmp_path):
    """R7 zero-regression: view_bin=1 produces the same on-disk TIFFs
    as the existing behavior (native shape)."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5_path = _make_real_h5(tmp_path / "ds.h5", intensity_shape=(2, 32, 32))
    handle = DatasetHandle(path=h5_path, metadata={"channel_names": ["ch0", "ch1"]})

    repo = Hdf5DatasetRepository()
    uc = ExportImages(repo)
    out = tmp_path / "out"
    req = ExportRequest(
        output_folder=out,
        dataset_name="ds",
        channels=[("ch0", 0), ("ch1", 1)],
        labels=["seg"],
        masks=["threshold"],
        view_bin=1,
    )

    uc.execute(handle, req)
    for name in ("ch0", "ch1", "seg", "threshold"):
        data = tifffile.imread(out / f"ds_{name}.tif")
        assert data.shape == (32, 32)


def test_integration_view_bin_four_on_32_produces_8x8(tmp_path):
    """Edge case from plan: view_bin=4 on 32x32 intensity → 8x8 TIFFs."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository

    h5_path = _make_real_h5(tmp_path / "ds.h5", intensity_shape=(2, 32, 32))
    handle = DatasetHandle(path=h5_path, metadata={"channel_names": ["ch0", "ch1"]})

    repo = Hdf5DatasetRepository()
    uc = ExportImages(repo)
    out = tmp_path / "out"
    uc.execute(
        handle,
        ExportRequest(
            output_folder=out,
            dataset_name="ds",
            channels=[("ch0", 0)],
            labels=[],
            masks=[],
            view_bin=4,
        ),
    )

    data = tifffile.imread(out / "ds_ch0.tif")
    assert data.shape == (8, 8)
