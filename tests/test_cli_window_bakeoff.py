"""Tests for the percell4-window-bakeoff CLI (plan U7)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from skimage.draw import disk

from percell4.interfaces.cli import window_bakeoff as cli
from percell4.store import DatasetStore


def _disk_mask(centers, radius, shape=(160, 160)):
    m = np.zeros(shape, dtype=np.uint8)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        m[rr, cc] = 1
    return m


def _make_store(path: Path, *, with_sg: bool = True) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["G3BP1", "DNA"]})
    rng = np.random.default_rng(0)
    centers = [(80, 80), (80, 40), (40, 110)]
    g3 = 50.0 + rng.normal(0.0, 2.0, size=(160, 160)).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), 8, shape=(160, 160))
        g3[rr, cc] = 220.0
    store.write_array(
        "intensity", np.stack([g3, np.zeros_like(g3)], 0), attrs={"dims": ["C", "H", "W"]}
    )
    labels = np.zeros((160, 160), dtype=np.int32)
    labels[20:140, 20:140] = 1
    store.write_labels("cp_mask", labels)
    if with_sg:
        store.write_mask("SG_mask", _disk_mask(centers, 8))


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_unknown_finder_returns_1(tmp_path, capsys):
    p = tmp_path / "DS.h5"
    _make_store(p)
    rc = cli.main([str(p), "--channel", "G3BP1", "--finders", "not-a-finder"])
    assert rc == 1
    assert "unknown finder" in capsys.readouterr().err


def test_runs_and_writes_json(tmp_path, capsys):
    p = tmp_path / "DS.h5"
    _make_store(p)
    out = tmp_path / "report.json"
    rc = cli.main([
        str(p), "--channel", "G3BP1", "--cp-name", "cp_mask",
        "--finders", "otsu-mean", "granule-size",
        "--window-grid", "15", "31", "51", "--k", "3.0",
        "--out", str(out),
    ])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "Window bake-off" in printed
    assert "mean|err|" in printed

    report = json.loads(out.read_text())
    assert report["k"] == 3.0
    assert report["window_grid"] == [15, 31, 51]
    assert {r["method"] for r in report["ranking"]} == {"otsu-mean", "granule-size"}
    assert "DS" in report["oracles"]
    assert report["oracles"]["DS"]["ideal_window"] in [15, 31, 51]
    # in-sample scores are flagged
    assert any(s.get("in_sample") is True for s in report["scores"])


def test_no_sg_mask_returns_1(tmp_path, capsys):
    p = tmp_path / "DS_nosg.h5"
    _make_store(p, with_sg=False)
    rc = cli.main([str(p), "--channel", "G3BP1", "--finders", "otsu-mean"])
    assert rc == 1
    assert "no labeled field" in capsys.readouterr().err


def test_load_error_returns_1(tmp_path, capsys):
    rc = cli.main([str(tmp_path / "missing.h5"), "--channel", "G3BP1", "--finders", "otsu-mean"])
    assert rc == 1
    assert "error loading" in capsys.readouterr().err
