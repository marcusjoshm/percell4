"""Tests for the percell4-window-k-sweep CLI (headless window×k sweep)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from skimage.draw import disk

from percell4.interfaces.cli import window_k_sweep as cli
from percell4.store import DatasetStore


def _make_dataset(
    path: Path,
    *,
    channel="Channel",
    seg="Cellpose",
    shape=(120, 120),
    pixel_size_um=0.1,
    with_seg=True,
    centers=None,
):
    centers = centers or [(60, 60), (60, 40), (40, 80)]
    rng = np.random.default_rng(0)
    img = (50.0 + rng.normal(0, 2.0, size=shape)).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), 7, shape=shape)
        img[rr, cc] = 220.0
    store = DatasetStore(path)
    store.create(metadata={"channel_names": [channel], "pixel_size_um": pixel_size_um})
    store.write_array("intensity", img[None], attrs={"dims": ["C", "H", "W"]})
    if with_seg:
        labels = np.zeros(shape, dtype=np.int32)
        labels[15:105, 15:105] = 1
        store.write_labels(seg, labels)


def test_cli_happy_path_two_datasets(tmp_path, capsys):
    p1, p2 = tmp_path / "Test1.h5", tmp_path / "Test2.h5"
    _make_dataset(p1)
    _make_dataset(p2)
    out = tmp_path / "manifests"
    rc = cli.main(
        [str(p1), str(p2), "--windows", "15", "31", "--ks", "2.0", "3.0", "--out", str(out)]
    )
    assert rc == 0
    txt = capsys.readouterr().out
    assert "Test1" in txt and "Test2" in txt
    assert "Cross-dataset" in txt
    # Manifest sidecar per dataset.
    j1 = json.loads((out / "Test1.sweep.json").read_text())
    assert len(j1["masks"]) == 4
    assert {m["name"] for m in j1["masks"]} == {
        "sweep_w015_k20", "sweep_w015_k30", "sweep_w031_k20", "sweep_w031_k30"
    }
    # Masks written into each .h5.
    assert "sweep_w031_k30" in DatasetStore(p1).list_masks()
    assert "sweep_w015_k20" in DatasetStore(p2).list_masks()


def test_cli_default_grid(tmp_path):
    p = tmp_path / "Test1.h5"
    _make_dataset(p)
    rc = cli.main([str(p)])
    assert rc == 0
    masks = [m for m in DatasetStore(p).list_masks() if m.startswith("sweep_")]
    assert len(masks) == 30  # 6 windows × 5 k


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    p = tmp_path / "Test1.h5"
    _make_dataset(p)
    out = tmp_path / "m"
    rc = cli.main([str(p), "--windows", "15", "31", "--ks", "2.0", "--dry-run", "--out", str(out)])
    assert rc == 0
    assert DatasetStore(p).list_masks() == []  # no masks
    assert not (out / "Test1.sweep.json").exists()  # no sidecar mutation in dry-run
    assert "sweep_w015_k20" in capsys.readouterr().out  # intended names shown


def test_cli_clear_preserves_other_masks(tmp_path):
    p = tmp_path / "Test1.h5"
    _make_dataset(p)
    store = DatasetStore(p)
    store.write_mask("keep", np.ones((120, 120), dtype=np.uint8))
    # Wider grid first.
    cli.main([str(p), "--windows", "15", "31", "51", "--ks", "2.0"])
    # Smaller grid with --clear removes the stale sweep masks, keeps "keep".
    cli.main([str(p), "--windows", "15", "--ks", "2.0", "--clear"])
    remaining = set(DatasetStore(p).list_masks())
    assert "keep" in remaining
    assert {m for m in remaining if m.startswith("sweep_")} == {"sweep_w015_k20"}


def test_cli_failure_isolation_and_exit_codes(tmp_path, capsys):
    good, bad = tmp_path / "Good.h5", tmp_path / "Bad.h5"
    _make_dataset(good)
    _make_dataset(bad, with_seg=False)  # missing Cellpose segmentation
    rc = cli.main([str(good), str(bad), "--windows", "15", "--ks", "2.0"])
    assert rc == 0  # one good dataset → success overall
    txt = capsys.readouterr().out
    assert "Bad" in txt  # the failure is reported
    assert "sweep_w015_k20" in DatasetStore(good).list_masks()

    # Every dataset failing → non-zero.
    rc_all_bad = cli.main([str(bad), "--windows", "15", "--ks", "2.0"])
    assert rc_all_bad == 1


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_cli_unknown_noise_estimator_errors_cleanly(tmp_path, capsys):
    p = tmp_path / "Test1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--noise-estimator", "bogus", "--windows", "15", "--ks", "2.0"])
    assert rc != 0
    assert DatasetStore(p).list_masks() == []  # nothing written
    captured = capsys.readouterr()  # capture once — a second call returns empty
    assert "bogus" in (captured.err + captured.out)
