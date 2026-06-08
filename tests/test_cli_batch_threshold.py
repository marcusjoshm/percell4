"""Tests for the percell4-batch-threshold CLI (headless grouped thresholding)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from percell4.interfaces.cli import batch_threshold as cli
from percell4.store import DatasetStore


def _make_dataset(path: Path, *, with_labels: bool = True, size: int = 100,
                  n_cells: int = 12) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "RFP"]})
    intensity = np.zeros((2, size, size), dtype=np.float32)
    labels = np.zeros((size, size), dtype=np.int32)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        # Two intensity populations within each cell so grouping/threshold
        # has something to split.
        intensity[0, row : row + 6, col : col + 6] = 40
        intensity[0, row + 1 : row + 4, col + 1 : col + 4] = 200
        intensity[1, row : row + 6, col : col + 6] = 80
        labels[row : row + 6, col : col + 6] = i + 1
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    if with_labels:
        store.write_labels("cellpose", labels)


def test_batch_threshold_writes_binary_mask(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--algorithm", "kmeans",
                   "--kmeans-n-clusters", "2"])
    assert rc == 0
    store = DatasetStore(p)
    assert "GFP_hi" in store.list_masks()
    mask = store.read_mask("GFP_hi")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    # /groups table written too.
    assert "GFP_hi" in store.list_groups("groups")
    # Hand-off line points the user at the measure CLI.
    assert "percell4-batch-measure" in capsys.readouterr().out


def test_batch_threshold_missing_labels_skips(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p, with_labels=False)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "r1"])
    assert rc == 1
    assert "no usable segmentation" in capsys.readouterr().err


def test_batch_threshold_overwrite_guard(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    # First run writes the mask.
    assert cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                     "--segmentation", "cellpose", "--kmeans-n-clusters", "2"]) == 0
    before = DatasetStore(p).read_mask("GFP_hi").copy()

    # Second run without --overwrite must refuse and leave the mask intact.
    rc = cli.main([str(p), "--channel", "RFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--kmeans-n-clusters", "2"])
    assert rc == 1
    assert "already exists" in capsys.readouterr().err
    after = DatasetStore(p).read_mask("GFP_hi")
    assert np.array_equal(before, after)  # untouched

    # With --overwrite it is replaced (run succeeds).
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--kmeans-n-clusters", "2",
                   "--overwrite"])
    assert rc == 0


def test_batch_threshold_invalid_round_name(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "1bad",
                   "--segmentation", "cellpose"])
    assert rc == 1
    assert "invalid round configuration" in capsys.readouterr().err


def test_batch_threshold_partial_success_exit_zero(tmp_path, capsys):
    good = tmp_path / "good.h5"
    bad = tmp_path / "bad.h5"
    _make_dataset(good)
    _make_dataset(bad, with_labels=False)
    rc = cli.main([str(good), str(bad), "--channel", "GFP",
                   "--round-name", "GFP_hi", "--segmentation", "cellpose",
                   "--kmeans-n-clusters", "2"])
    assert rc == 0  # one succeeded
    assert "GFP_hi" in DatasetStore(good).list_masks()
    assert "GFP_hi" not in DatasetStore(bad).list_masks()


def test_batch_threshold_iterative_otsu_writes_binary_mask(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "SG_iter",
        "--segmentation", "cellpose", "--strategy", "iterative-otsu",
        "--iterative-scope", "per-cell", "--gaussian-sigma", "0",
        "--stop-param", "bg-floor.k=2.0",
    ])
    assert rc == 0
    store = DatasetStore(p)
    mask = store.read_mask("SG_iter")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    assert mask.sum() > 0
    out = capsys.readouterr().out
    assert "iterative-otsu" in out  # [ok] line names the strategy


def test_batch_threshold_iterative_unknown_stop_criterion_exits_1(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "SG_iter",
        "--segmentation", "cellpose", "--strategy", "iterative-otsu",
        "--stop-criteria", "not-a-real-criterion",
    ])
    assert rc == 1
    assert "invalid round configuration" in capsys.readouterr().err
    assert "SG_iter" not in DatasetStore(p).list_masks()


def test_batch_threshold_iterative_malformed_stop_param_exits_1(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "SG_iter",
        "--segmentation", "cellpose", "--strategy", "iterative-otsu",
        "--stop-param", "bg-floor-k-no-equals",
    ])
    assert rc == 1
    assert "invalid round configuration" in capsys.readouterr().err


def test_batch_threshold_grouped_otsu_default_unchanged(tmp_path):
    # Default strategy still produces the legacy grouped-otsu round (no iterative).
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--kmeans-n-clusters", "2"])
    assert rc == 0
    assert "GFP_hi" in DatasetStore(p).list_masks()


def test_batch_threshold_import_is_qt_free():
    qt_before = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    from percell4.interfaces.cli import batch_threshold  # noqa: F401
    qt_after = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    assert not (qt_after - qt_before)
