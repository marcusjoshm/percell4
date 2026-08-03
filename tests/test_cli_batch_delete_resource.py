"""Tests for the percell4-batch-delete CLI entry point."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.interfaces.cli import batch_delete_resource as cli


def _make_h5_with_channel(path: Path, channel: str = "ch0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = [channel]
        meta.attrs["flim_frequency_mhz"] = 80.0
        meta.attrs[f"flim_cal_phase_{channel}"] = 0.0
        meta.attrs[f"flim_cal_mod_{channel}"] = 1.0
        decay = f.create_group("decay")
        decay.create_dataset(channel, data=np.zeros((4, 4, 8), dtype=np.float32))
    return path


@pytest.fixture
def stub_use_case(monkeypatch):
    """Capture args the CLI passes to batch_delete_resource."""
    calls: dict = {}

    def fake_run(
        paths, *, kind, name=None, all_resources=False,
        dry_run=False, progress_callback=None,
    ):
        from percell4.application.use_cases.batch_rename_resource import (
            BatchOperationItemResult,
            BatchOperationReport,
        )
        calls["paths"] = list(paths)
        calls["kind"] = kind
        calls["name"] = name
        calls["all_resources"] = all_resources
        calls["dry_run"] = dry_run
        items = []
        for p in paths:
            processed = (name,) if name is not None else ("placeholder",)
            item = BatchOperationItemResult(
                h5_path=Path(p), status="succeeded", processed=processed,
            )
            items.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return BatchOperationReport(items=tuple(items))

    monkeypatch.setattr(cli, "batch_delete_resource", fake_run)
    return calls


# ── argparse plumbing ──────────────────────────────────────────────────


def test_cli_passes_args_through_to_use_case(tmp_path, stub_use_case):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")

    exit_code = cli.main([
        str(h5), "--kind", "channel", "--name", "ch0",
    ])

    assert exit_code == 0
    assert stub_use_case["kind"] == "channel"
    assert stub_use_case["name"] == "ch0"
    assert stub_use_case["dry_run"] is False


def test_cli_dry_run_flag_propagates(tmp_path, stub_use_case):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    cli.main([str(h5), "--kind", "mask", "--name", "thresh", "--dry-run"])
    assert stub_use_case["dry_run"] is True


def test_cli_directory_glob_expands_to_h5_files(tmp_path, stub_use_case):
    a = _make_h5_with_channel(tmp_path / "a.h5")
    b = _make_h5_with_channel(tmp_path / "b.h5")
    cli.main([str(tmp_path), "--kind", "channel", "--name", "ch0"])
    assert sorted(stub_use_case["paths"]) == sorted([a, b])


def test_cli_rejects_bogus_kind(tmp_path):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    with pytest.raises(SystemExit) as exc:
        cli.main([str(h5), "--kind", "bogus", "--name", "x"])
    assert exc.value.code != 0


def test_cli_requires_kind_and_name(tmp_path):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    with pytest.raises(SystemExit):
        cli.main([str(h5), "--name", "x"])
    with pytest.raises(SystemExit):
        cli.main([str(h5), "--kind", "channel"])


def test_cli_no_matched_paths_returns_one(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    exit_code = cli.main([str(empty_dir), "--kind", "channel", "--name", "ch0"])
    assert exit_code == 1


# ── Exit code semantics ────────────────────────────────────────────────


def test_cli_returns_zero_when_any_dataset_processed(tmp_path, monkeypatch):
    a = _make_h5_with_channel(tmp_path / "a.h5")
    b = _make_h5_with_channel(tmp_path / "b.h5")
    from percell4.application.use_cases.batch_rename_resource import (
        BatchOperationItemResult,
        BatchOperationReport,
    )

    def fake(paths, **kw):
        return BatchOperationReport(items=(
            BatchOperationItemResult(
                h5_path=Path(paths[0]), status="succeeded", processed=("ch0",)
            ),
            BatchOperationItemResult(
                h5_path=Path(paths[1]),
                status="skipped_no_changes",
                skipped={"ch0": "not found"},
            ),
        ))

    monkeypatch.setattr(cli, "batch_delete_resource", fake)
    exit_code = cli.main([str(a), str(b), "--kind", "channel", "--name", "ch0"])
    assert exit_code == 0


def test_cli_returns_one_when_no_progress(tmp_path, monkeypatch):
    a = _make_h5_with_channel(tmp_path / "a.h5")
    from percell4.application.use_cases.batch_rename_resource import (
        BatchOperationItemResult,
        BatchOperationReport,
    )

    def fake(paths, **kw):
        return BatchOperationReport(items=(
            BatchOperationItemResult(
                h5_path=Path(paths[0]),
                status="skipped_no_changes",
                skipped={"ch0": "not found"},
            ),
        ))

    monkeypatch.setattr(cli, "batch_delete_resource", fake)
    exit_code = cli.main([str(a), "--kind", "channel", "--name", "ch0"])
    assert exit_code == 1


# ── Output text ────────────────────────────────────────────────────────


def test_cli_help_lists_kinds_and_dry_run(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "channel" in out
    assert "mask" in out
    assert "segmentation" in out
    assert "--dry-run" in out


def test_cli_uses_deleted_verb_in_output(tmp_path, stub_use_case, capsys):
    """Output reads 'N deleted' (not 'N processed' or 'N renamed')."""
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    cli.main([str(h5), "--kind", "channel", "--name", "ch0"])
    out = capsys.readouterr().out
    assert "deleted" in out
    assert "[succeeded]" in out


# ── End-to-end (real use case) ─────────────────────────────────────────


def test_end_to_end_delete_against_real_h5(tmp_path):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5", channel="ch0")
    exit_code = cli.main([str(h5), "--kind", "channel", "--name", "ch0"])
    assert exit_code == 0
    with h5py.File(h5, "r") as f:
        assert "decay/ch0" not in f
        names = list(f["metadata"].attrs["channel_names"])
        assert names == []


# ── --all flag ─────────────────────────────────────────────────────────


def test_cli_all_flag_propagates(tmp_path, stub_use_case):
    """``--all`` reaches the use case as ``all_resources=True`` and
    ``name=None``."""
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")

    exit_code = cli.main([str(h5), "--kind", "mask", "--all"])

    assert exit_code == 0
    assert stub_use_case["kind"] == "mask"
    assert stub_use_case["name"] is None
    assert stub_use_case["all_resources"] is True


def test_cli_name_and_all_mutually_exclusive(tmp_path):
    """``--name`` and ``--all`` together → argparse rejects."""
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    with pytest.raises(SystemExit) as exc:
        cli.main([
            str(h5), "--kind", "mask", "--name", "thresh", "--all",
        ])
    assert exc.value.code != 0


def test_cli_requires_name_or_all(tmp_path):
    """Neither ``--name`` nor ``--all`` → argparse rejects."""
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    with pytest.raises(SystemExit) as exc:
        cli.main([str(h5), "--kind", "channel"])
    assert exc.value.code != 0


def test_cli_help_lists_all_flag(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "--all" in out


def test_cli_all_dry_run_flag_propagates(tmp_path, stub_use_case):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    cli.main([str(h5), "--kind", "segmentation", "--all", "--dry-run"])
    assert stub_use_case["all_resources"] is True
    assert stub_use_case["dry_run"] is True


def test_end_to_end_all_channels_against_real_h5(tmp_path):
    """End-to-end: 2 channels on disk, ``--all`` removes both."""
    h5 = tmp_path / "ds.h5"
    h5.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(h5, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = ["ch0", "ch1"]
        meta.attrs["flim_frequency_mhz"] = 80.0
        for ch in ("ch0", "ch1"):
            meta.attrs[f"flim_cal_phase_{ch}"] = 0.0
            meta.attrs[f"flim_cal_mod_{ch}"] = 1.0
        decay = f.create_group("decay")
        for ch in ("ch0", "ch1"):
            decay.create_dataset(ch, data=np.zeros((4, 4, 8), dtype=np.float32))

    exit_code = cli.main([str(h5), "--kind", "channel", "--all"])

    assert exit_code == 0
    with h5py.File(h5, "r") as f:
        assert list(f["metadata"].attrs["channel_names"]) == []
        assert "decay/ch0" not in f
        assert "decay/ch1" not in f
