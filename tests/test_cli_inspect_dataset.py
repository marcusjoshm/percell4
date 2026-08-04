"""Tests for the percell4-inspect CLI (dataset metadata + layer inventory)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

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


def test_inspect_prints_description(tmp_path, capsys):
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    DatasetStore(p).set_description("HeLa p14, fixed 4% PFA 15min")
    assert cli.main([str(p)]) == 0
    assert "HeLa p14, fixed 4% PFA 15min" in capsys.readouterr().out


def test_inspect_prints_multiline_description_in_full(tmp_path, capsys):
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    DatasetStore(p).set_description("line one\nline two\nline three")
    assert cli.main([str(p)]) == 0
    out = capsys.readouterr().out
    for line in ("line one", "line two", "line three"):
        assert line in out


def test_inspect_json_includes_description(tmp_path, capsys):
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    DatasetStore(p).set_description("HeLa p14")
    assert cli.main([str(p), "--json"]) == 0
    rec = json.loads(capsys.readouterr().out)[0]
    assert rec["metadata"]["description"] == "HeLa p14"


def test_inspect_dataset_without_description(tmp_path, capsys):
    """Absent description renders the placeholder and reports null in JSON."""
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    assert cli.main([str(p)]) == 0
    assert "Description: —" in capsys.readouterr().out
    assert cli.main([str(p), "--json"]) == 0
    rec = json.loads(capsys.readouterr().out)[0]
    assert rec["metadata"]["description"] is None


def _folder_with_descriptions(tmp_path) -> None:
    """Twelve datasets, three of which mention PFA in mixed case."""
    described = {
        "dish_01.h5": "HeLa, 4% PFA 15min",
        "dish_02.h5": "HeLa, methanol fixed",
        "dish_05.h5": "U2OS, pfa fixed",
        "dish_09.h5": "COS-7, Pfa then permeabilized",
    }
    for i in range(1, 13):
        name = f"dish_{i:02d}.h5"
        p = tmp_path / name
        _make_dataset(p)
        if name in described:
            DatasetStore(p).set_description(described[name])


def test_inspect_grep_reports_only_matching_datasets(tmp_path, capsys):
    _folder_with_descriptions(tmp_path)
    assert cli.main([str(tmp_path), "--grep", "pfa"]) == 0
    out = capsys.readouterr().out
    for name in ("dish_01.h5", "dish_05.h5", "dish_09.h5"):
        assert name in out
    for name in ("dish_02.h5", "dish_03.h5", "dish_12.h5"):
        assert name not in out


def test_inspect_grep_filters_json_output_too(tmp_path, capsys):
    _folder_with_descriptions(tmp_path)
    assert cli.main([str(tmp_path), "--grep", "PFA", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 3


def test_inspect_grep_is_case_insensitive_substring(tmp_path, capsys):
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    DatasetStore(p).set_description("HeLa p14, fixed 4% PFA 15min")
    assert cli.main([str(p), "--grep", "FIXED 4%"]) == 0
    assert "ds.h5" in capsys.readouterr().out


def test_inspect_grep_excludes_datasets_without_a_description(tmp_path, capsys):
    p = tmp_path / "ds.h5"
    _make_dataset(p)
    assert cli.main([str(p), "--grep", "anything"]) == 1


def test_inspect_grep_matching_nothing_exits_1(tmp_path, capsys):
    _folder_with_descriptions(tmp_path)
    assert cli.main([str(tmp_path), "--grep", "cryo-EM"]) == 1
    assert capsys.readouterr().out.strip() == ""


def test_inspect_import_is_qt_free():
    """Importing the inspect CLI does not pull in Qt/napari."""
    qt_before = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    from percell4.interfaces.cli import inspect_dataset  # noqa: F401
    qt_after = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    assert not (qt_after - qt_before)
