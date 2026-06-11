"""Tests for the percell4-per-cell-sweep CLI."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from skimage.draw import disk

from percell4.interfaces.cli import per_cell_sweep as cli
from percell4.store import DatasetStore


def _make_dataset(path: Path, *, with_seg=True, shape=(140, 140)):
    rng = np.random.default_rng(0)
    img = (50.0 + rng.normal(0, 2.0, size=shape)).astype(np.float32)
    labels = np.zeros(shape, dtype=np.int32)
    labels[20:120, 10:60] = 1
    labels[20:120, 80:130] = 2
    for cy, cx in [(60, 35), (70, 105)]:
        rr, cc = disk((cy, cx), 6, shape=shape)
        img[rr, cc] = 220.0
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["Channel"], "pixel_size_um": 0.1})
    store.write_array("intensity", img[None], attrs={"dims": ["C", "H", "W"]})
    if with_seg:
        store.write_labels("Cellpose", labels)


def test_cli_renders_sheets_and_template(tmp_path, capsys):
    p = tmp_path / "Test1.h5"
    _make_dataset(p)
    out = tmp_path / "sheets"
    rc = cli.main([str(p), "--out", str(out), "--windows", "15", "31", "--ks", "2.0", "3.0"])
    assert rc == 0
    ds_dir = out / "Test1"
    assert (ds_dir / "cell001_contactsheet.png").exists()
    assert (ds_dir / "cell002_contactsheet.png").exists()
    assert (ds_dir / "cells.csv").exists()
    assert (ds_dir / "labels.csv").exists()
    assert "Test1" in capsys.readouterr().out


def test_cli_failure_isolation_and_exit_codes(tmp_path, capsys):
    good, bad = tmp_path / "Good.h5", tmp_path / "Bad.h5"
    _make_dataset(good)
    _make_dataset(bad, with_seg=False)
    out = tmp_path / "sheets"
    rc = cli.main([str(good), str(bad), "--out", str(out), "--windows", "15", "--ks", "2.0"])
    assert rc == 0  # one good → success overall
    err = capsys.readouterr().err
    assert "Bad" in err  # failure reported to stderr
    assert (out / "Good" / "cell001_contactsheet.png").exists()

    rc_all_bad = cli.main([str(bad), "--out", str(out), "--windows", "15", "--ks", "2.0"])
    assert rc_all_bad == 1


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_cli_unknown_noise_estimator_errors_cleanly(tmp_path, capsys):
    p = tmp_path / "Test1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--out", str(tmp_path / "s"), "--noise-estimator", "bogus"])
    assert rc != 0
    assert "bogus" in capsys.readouterr().err
