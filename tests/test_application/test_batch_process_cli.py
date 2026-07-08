"""batch_process CLI: arg parsing, spec building, exit codes (U11)."""

from __future__ import annotations

import pytest

import percell4.interfaces.cli.batch_process as cli
from percell4.application.use_cases.batch_process_datasets import (
    BatchProcessItemResult,
    BatchProcessReport,
)


@pytest.fixture
def captured(monkeypatch):
    """Replace the heavy use case with a stub that records its call."""
    seen = {}

    def _stub(specs, **kwargs):
        seen["specs"] = specs
        seen["kwargs"] = kwargs
        report = BatchProcessReport()
        for s in specs:
            report.items.append(BatchProcessItemResult(name=s.name, output_h5=s.output_h5))
        return report

    monkeypatch.setattr(cli, "batch_process_datasets", _stub)
    return seen


def test_happy_path_returns_zero(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    (tmp_path / "dishB").mkdir()
    out = tmp_path / "h5"

    rc = cli.main([str(tmp_path / "dishA"), str(tmp_path / "dishB"),
                   "--output-dir", str(out), "--seg-channel", "mNG"])

    assert rc == 0
    assert out.is_dir()  # output dir created
    # Specs built one per source dir, named after the dir.
    names = sorted(s.name for s in captured["specs"])
    assert names == ["dishA", "dishB"]
    assert captured["kwargs"]["seg_channel"] == "mNG"
    assert captured["kwargs"]["track"] is True


def test_no_track_flag_forwarded(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o"),
                   "--no-track"])
    assert rc == 0
    assert captured["kwargs"]["track"] is False


def test_channel_names_parsed_to_list_and_forwarded(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o"),
                   "--channel-names", "DAPI, GFP ,RFP", "--seg-name", "nuclei"])
    assert rc == 0
    # Comma-split, whitespace-stripped, empties dropped.
    assert captured["kwargs"]["channel_names"] == ["DAPI", "GFP", "RFP"]
    assert captured["kwargs"]["seg_name"] == "nuclei"


def test_channel_names_default_none_when_absent(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o")])
    assert rc == 0
    assert captured["kwargs"]["channel_names"] is None
    assert captured["kwargs"]["seg_name"] is None


def test_empty_channel_names_returns_one(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o"),
                   "--channel-names", " , "])
    assert rc == 1
    assert "specs" not in captured  # use case never called


def test_no_valid_sources_returns_one(tmp_path, captured):
    # A path that isn't a directory is skipped -> no specs -> exit 1.
    rc = cli.main([str(tmp_path / "missing"), "--output-dir", str(tmp_path / "o")])
    assert rc == 1
    assert "specs" not in captured  # use case never called


def test_tiff_dir_without_output_dir_returns_one(tmp_path, captured):
    # A TIFF source directory requires --output-dir; without it the source is
    # skipped -> no specs -> exit 1 (R6). Not an argparse error.
    (tmp_path / "dishA").mkdir()
    rc = cli.main([str(tmp_path / "dishA")])
    assert rc == 1
    assert "specs" not in captured  # use case never called


# --- U3: full Cellpose settings, edge options, .h5 sources ---

from percell4.workflows.models import CellposeSettings  # noqa: E402


def test_cellpose_settings_forwarded(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([
        str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o"),
        "--cellpose-model", "cpdino", "--cellpose-diameter", "120",
        "--flow-threshold", "0.7", "--cellprob-threshold", "-1.0",
        "--min-size", "22", "--saturation", "2.5", "--blur-sigma", "1.5",
        "--gpu",
    ])
    assert rc == 0
    s = captured["kwargs"]["settings"]
    assert isinstance(s, CellposeSettings)
    assert s.model == "cpdino"
    assert s.diameter == 120.0
    assert s.gpu is True
    assert s.flow_threshold == 0.7
    assert s.cellprob_threshold == -1.0
    assert s.min_size == 22
    assert s.saturation_pct == 2.5
    assert s.blur_sigma == 1.5


def test_defaults_match_gui(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o")])
    assert rc == 0
    s = captured["kwargs"]["settings"]
    assert s.model == "cpsam_v2"
    assert s.diameter == 30.0  # CellposeSettings() default
    assert s.flow_threshold == 0.4
    assert s.cellprob_threshold == 0.0
    assert s.min_size == 15
    assert s.saturation_pct == 1.0
    assert s.blur_sigma == 0.0
    assert captured["kwargs"]["remove_edge_cells"] is True
    assert captured["kwargs"]["edge_margin"] == 0


def test_no_remove_edge_cells_and_margin_forwarded(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([
        str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o"),
        "--no-remove-edge-cells", "--edge-margin", "5",
    ])
    assert rc == 0
    assert captured["kwargs"]["remove_edge_cells"] is False
    assert captured["kwargs"]["edge_margin"] == 5


def test_invalid_settings_returns_one(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([
        str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o"),
        "--saturation", "99",  # out of [0, 50]
    ])
    assert rc == 1
    assert "specs" not in captured  # use case never called


def test_h5_source_in_place_when_no_output_dir(tmp_path, captured):
    h5 = tmp_path / "dish.h5"
    h5.write_bytes(b"")  # just needs to be an existing .h5 file
    rc = cli.main([str(h5)])
    assert rc == 0
    spec = captured["specs"][0]
    assert spec.source_dir == h5
    assert spec.output_h5 == h5  # in place


def test_h5_source_copies_to_output_dir(tmp_path, captured):
    h5 = tmp_path / "dish.h5"
    h5.write_bytes(b"")
    out = tmp_path / "o"
    rc = cli.main([str(h5), "--output-dir", str(out)])
    assert rc == 0
    spec = captured["specs"][0]
    assert spec.source_dir == h5
    assert spec.output_h5 == out / "dish.h5"


def test_invalid_model_is_argparse_error(tmp_path):
    (tmp_path / "dishA").mkdir()
    with pytest.raises(SystemExit) as exc:
        cli.main([str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o"),
                  "--cellpose-model", "not-a-model"])
    assert exc.value.code == 2  # argparse choices rejection


# --- skip-segmentation (track-only) ---

def test_skip_segmentation_forwarded(tmp_path, captured):
    h5 = tmp_path / "movie.h5"
    h5.write_bytes(b"")
    rc = cli.main([str(h5), "--skip-segmentation", "--seg-name", "cellpose_42"])
    assert rc == 0
    assert captured["kwargs"]["skip_segmentation"] is True
    assert captured["kwargs"]["seg_name"] == "cellpose_42"


def test_skip_segmentation_without_seg_name_returns_one(tmp_path, captured):
    h5 = tmp_path / "movie.h5"
    h5.write_bytes(b"")
    rc = cli.main([str(h5), "--skip-segmentation"])
    assert rc == 1
    assert "specs" not in captured  # use case never called


def test_skip_segmentation_defaults_false(tmp_path, captured):
    (tmp_path / "dishA").mkdir()
    rc = cli.main([str(tmp_path / "dishA"), "--output-dir", str(tmp_path / "o")])
    assert rc == 0
    assert captured["kwargs"]["skip_segmentation"] is False


# --- verbose logging configuration ---

def test_configure_logging_verbose_lifts_dependency_loggers():
    import logging
    cli._configure_logging(verbose=True)
    assert logging.getLogger("cellpose").level == logging.INFO
    assert logging.getLogger("laptrack").level == logging.INFO


def test_configure_logging_quiet_silences_dependency_loggers():
    import logging
    cli._configure_logging(verbose=False)
    assert logging.getLogger("cellpose").level == logging.WARNING
    assert logging.getLogger("laptrack").level == logging.WARNING
