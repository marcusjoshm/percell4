"""Tests for percell4-batch-whole-field CLI."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.interfaces.cli import batch_whole_field as cli


def _make_h5(path: Path, *, native_shape: tuple[int, int] = (4, 4)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = native_shape
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = ["ch0"]
        meta.attrs["flim_frequency_mhz"] = 80.0
        meta.attrs["native_shape"] = list(native_shape)
        decay = f.create_group("decay")
        decay.create_dataset("ch0", data=np.zeros((h, w, 8), dtype=np.float32))
    return path


@pytest.fixture
def stub_use_case(monkeypatch):
    """Capture args the CLI passes to the use case."""
    calls: dict = {}

    def fake_run(paths, *, dry_run=False, progress_callback=None):
        from percell4.application.use_cases.batch_rename_resource import (
            BatchOperationItemResult,
            BatchOperationReport,
        )
        calls["paths"] = list(paths)
        calls["dry_run"] = dry_run
        items = []
        for p in paths:
            item = BatchOperationItemResult(
                h5_path=Path(p),
                status="succeeded",
                processed=("whole_field",),
            )
            items.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return BatchOperationReport(items=tuple(items))

    monkeypatch.setattr(
        cli, "batch_create_whole_field_segmentation", fake_run,
    )
    return calls


# ── argparse plumbing ──────────────────────────────────────────────────


def test_cli_passes_paths_to_use_case(tmp_path, stub_use_case):
    h5 = _make_h5(tmp_path / "ds.h5")

    exit_code = cli.main([str(h5)])

    assert exit_code == 0
    assert stub_use_case["paths"] == [h5]
    assert stub_use_case["dry_run"] is False


def test_cli_dry_run_flag_propagates(tmp_path, stub_use_case):
    h5 = _make_h5(tmp_path / "ds.h5")
    cli.main([str(h5), "--dry-run"])
    assert stub_use_case["dry_run"] is True


def test_cli_directory_glob_expands_to_h5_files(tmp_path, stub_use_case):
    a = _make_h5(tmp_path / "a.h5")
    b = _make_h5(tmp_path / "b.h5")
    cli.main([str(tmp_path)])
    assert sorted(stub_use_case["paths"]) == sorted([a, b])


def test_cli_no_matched_paths_returns_one(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    exit_code = cli.main([str(empty_dir)])
    assert exit_code == 1


def test_cli_requires_paths():
    with pytest.raises(SystemExit):
        cli.main([])


# ── Exit code semantics ────────────────────────────────────────────────


def test_cli_returns_zero_when_any_dataset_processed(tmp_path, monkeypatch):
    """Mixed: one processed, one failed → exit 0."""
    a = _make_h5(tmp_path / "a.h5")
    b = _make_h5(tmp_path / "b.h5")
    from percell4.application.use_cases.batch_rename_resource import (
        BatchOperationItemResult,
        BatchOperationReport,
    )

    def fake(paths, **kw):
        return BatchOperationReport(items=(
            BatchOperationItemResult(
                h5_path=Path(paths[0]),
                status="succeeded",
                processed=("whole_field",),
            ),
            BatchOperationItemResult(
                h5_path=Path(paths[1]),
                status="failed",
                error="something broke",
            ),
        ))

    monkeypatch.setattr(cli, "batch_create_whole_field_segmentation", fake)
    exit_code = cli.main([str(a), str(b)])
    assert exit_code == 0


def test_cli_returns_one_when_no_progress(tmp_path, monkeypatch):
    """All failed → exit 1."""
    a = _make_h5(tmp_path / "a.h5")
    from percell4.application.use_cases.batch_rename_resource import (
        BatchOperationItemResult,
        BatchOperationReport,
    )

    def fake(paths, **kw):
        return BatchOperationReport(items=(
            BatchOperationItemResult(
                h5_path=Path(paths[0]),
                status="failed",
                error="no shape",
            ),
        ))

    monkeypatch.setattr(cli, "batch_create_whole_field_segmentation", fake)
    exit_code = cli.main([str(a)])
    assert exit_code == 1


# ── Help text ──────────────────────────────────────────────────────────


def test_cli_help_lists_dry_run_and_whole_field(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "whole_field" in out


def test_cli_uses_created_verb_in_output(tmp_path, stub_use_case, capsys):
    """Output reads 'N created' (per-line verb for this CLI)."""
    h5 = _make_h5(tmp_path / "ds.h5")
    cli.main([str(h5)])
    out = capsys.readouterr().out
    assert "created" in out
    assert "[succeeded]" in out


# ── End-to-end (real use case) ─────────────────────────────────────────


def test_end_to_end_against_real_h5(tmp_path):
    """Real use case path: writes /labels/whole_field of correct shape
    and dtype with every pixel = 1."""
    h5 = _make_h5(tmp_path / "ds.h5", native_shape=(6, 8))

    exit_code = cli.main([str(h5)])

    assert exit_code == 0
    with h5py.File(h5, "r") as f:
        assert "labels/whole_field" in f
        arr = f["labels/whole_field"][:]
        assert arr.shape == (6, 8)
        assert arr.dtype == np.int32
        assert (arr == 1).all()


def test_end_to_end_dry_run_does_not_write(tmp_path):
    h5 = _make_h5(tmp_path / "ds.h5")
    exit_code = cli.main([str(h5), "--dry-run"])
    assert exit_code == 0
    with h5py.File(h5, "r") as f:
        assert "labels/whole_field" not in f
