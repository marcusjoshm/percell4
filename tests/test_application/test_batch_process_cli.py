"""batch_process CLI: arg parsing, spec building, exit codes (U11)."""

from __future__ import annotations

from pathlib import Path

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


def test_no_valid_sources_returns_one(tmp_path, captured):
    # A path that isn't a directory is skipped -> no specs -> exit 1.
    rc = cli.main([str(tmp_path / "missing"), "--output-dir", str(tmp_path / "o")])
    assert rc == 1
    assert "specs" not in captured  # use case never called


def test_missing_output_dir_is_argparse_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main([str(tmp_path)])
    assert exc.value.code == 2  # argparse usage error
