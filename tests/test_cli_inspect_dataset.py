"""Tests for the percell4-inspect CLI (dataset metadata + layer inventory)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from percell4.interfaces.cli import inspect_dataset as cli
from percell4.store import DatasetStore


def _make_dataset(path: Path) -> None:
    store = DatasetStore(path)
    store.create(metadata={
        "channel_names": ["GFP", "RFP"],
        "pixel_size_um": 0.325,
        "source": "unit-test",
    })
    store.write_array(
        "intensity",
        np.zeros((3, 2, 32, 48), dtype=np.float32),
        attrs={"dims": ["T", "C", "H", "W"]},
    )
    store.write_labels("cellpose", np.zeros((3, 32, 48), dtype=np.int32))
    store.write_mask("pbody", np.zeros((3, 32, 48), dtype=np.uint8))


def test_inspect_human_output(tmp_path, capsys):
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    rc = cli.main([str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ds.h5" in out
    assert "32×48 px" in out          # resolution
    assert "0.325 µm/px" in out       # pixel size
    assert "GFP, RFP" in out          # channels
    assert "pbody" in out             # mask listed
    assert "cellpose" in out          # segmentation listed
    assert "float32" in out           # intensity dtype
    assert "uint8" in out             # mask dtype


def test_inspect_json_output(tmp_path, capsys):
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--json"])
    assert rc == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 1
    rec = records[0]
    assert rec["metadata"]["channel_names"] == ["GFP", "RFP"]
    assert rec["metadata"]["n_timepoints"] == 3
    assert rec["intensity"]["dtype"] == "float32"
    assert [m["name"] for m in rec["masks"]] == ["pbody"]
    assert [s["name"] for s in rec["segmentations"]] == ["cellpose"]


def test_inspect_classifies_mask_shadowing_label_name(tmp_path, capsys):
    """A name present under both /labels and /masks is reported as a mask,
    never double-counted as a segmentation."""
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    # Add a /labels entry whose name collides with a mask name.
    store = DatasetStore(p)
    store.write_labels("pbody", np.zeros((3, 32, 48), dtype=np.int32))
    rc = cli.main([str(p), "--json"])
    assert rc == 0
    rec = json.loads(capsys.readouterr().out)[0]
    seg_names = [s["name"] for s in rec["segmentations"]]
    assert "pbody" not in seg_names          # shadowed → not a segmentation
    assert "cellpose" in seg_names
    assert "pbody" in [m["name"] for m in rec["masks"]]


def test_inspect_missing_optional_metadata(tmp_path, capsys):
    p = tmp_path / "bare.h5"
    store = DatasetStore(p)
    store.create(metadata={})  # no channels, no pixel size
    store.write_array(
        "intensity",
        np.zeros((2, 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    rc = cli.main([str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "—" in out  # placeholders for absent fields, no crash


def test_inspect_corrupt_file_continues(tmp_path, capsys):
    good = tmp_path / "good.h5"
    _make_dataset(good)
    bad = tmp_path / "bad.h5"
    bad.write_bytes(b"not an hdf5 file at all")
    rc = cli.main([str(bad), str(good)])
    # One good, one bad → still exit 0 (partial success).
    assert rc == 0
    captured = capsys.readouterr()
    assert "bad.h5" in captured.err   # error went to stderr
    assert "good.h5" in captured.out  # good one still inspected


def test_inspect_all_fail_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.h5"
    bad.write_bytes(b"garbage")
    rc = cli.main([str(bad)])
    assert rc == 1


def test_inspect_does_not_decode_arrays(tmp_path, monkeypatch):
    """Inspecting must not call read_array (large-file-load regression guard)."""
    p = tmp_path / "ds.h5"
    _make_dataset(p)

    def _boom(self, *a, **k):
        raise AssertionError("inspect must not decode arrays via read_array")

    monkeypatch.setattr(DatasetStore, "read_array", _boom)
    assert cli.main([str(p), "--json"]) == 0


def test_inspect_import_is_qt_free():
    """Importing the inspect CLI does not pull in Qt/napari."""
    qt_before = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    from percell4.interfaces.cli import inspect_dataset  # noqa: F401
    qt_after = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    assert not (qt_after - qt_before)
