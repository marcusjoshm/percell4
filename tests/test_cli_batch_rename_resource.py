"""Tests for the percell4-batch-rename CLI entry point.

Exercises argparse, path resolution, the seam between the CLI and
batch_rename_resource, and exit codes. The use case itself is
monkeypatched in most tests so the CLI shape is what's under test.
A couple of end-to-end tests run against real .h5 files to confirm
the integration works.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.interfaces.cli import batch_rename_resource as cli


def _make_h5_with_channel(path: Path, channel: str = "mScar") -> Path:
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
    """Capture the args the CLI passes to batch_rename_resource."""
    calls: dict = {}

    def fake_run(paths, *, kind, old_name, new_name, dry_run=False, progress_callback=None):
        from percell4.application.use_cases.batch_rename_resource import (
            BatchOperationReport,
            BatchOperationItemResult,
        )
        calls["paths"] = list(paths)
        calls["kind"] = kind
        calls["old_name"] = old_name
        calls["new_name"] = new_name
        calls["dry_run"] = dry_run
        # Fire a fake "succeeded" item for each path so progress prints.
        items = []
        for p in paths:
            item = BatchOperationItemResult(
                h5_path=Path(p), status="succeeded", processed=(old_name,),
            )
            items.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return BatchOperationReport(items=tuple(items))

    monkeypatch.setattr(cli, "batch_rename_resource", fake_run)
    return calls


# ── argparse plumbing ──────────────────────────────────────────────────


def test_cli_passes_args_through_to_use_case(tmp_path, stub_use_case):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")

    exit_code = cli.main([
        str(h5),
        "--kind", "channel",
        "--from-name", "mScar",
        "--to-name", "mScarlet",
    ])

    assert exit_code == 0
    assert stub_use_case["kind"] == "channel"
    assert stub_use_case["old_name"] == "mScar"
    assert stub_use_case["new_name"] == "mScarlet"
    assert stub_use_case["dry_run"] is False
    assert stub_use_case["paths"] == [h5]


def test_cli_dry_run_flag_propagates(tmp_path, stub_use_case):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    cli.main([
        str(h5), "--kind", "mask",
        "--from-name", "old", "--to-name", "new",
        "--dry-run",
    ])
    assert stub_use_case["dry_run"] is True


def test_cli_directory_glob_expands_to_h5_files(tmp_path, stub_use_case):
    a = _make_h5_with_channel(tmp_path / "a.h5")
    b = _make_h5_with_channel(tmp_path / "b.h5")
    cli.main([
        str(tmp_path), "--kind", "channel",
        "--from-name", "mScar", "--to-name", "mScarlet",
    ])
    assert sorted(stub_use_case["paths"]) == sorted([a, b])


def test_cli_rejects_bogus_kind(tmp_path):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    with pytest.raises(SystemExit) as exc:
        cli.main([str(h5), "--kind", "bogus", "--from-name", "a", "--to-name", "b"])
    assert exc.value.code != 0


def test_cli_requires_kind_from_name_to_name(tmp_path):
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    # Missing --kind
    with pytest.raises(SystemExit):
        cli.main([str(h5), "--from-name", "a", "--to-name", "b"])
    # Missing --from-name
    with pytest.raises(SystemExit):
        cli.main([str(h5), "--kind", "channel", "--to-name", "b"])
    # Missing --to-name
    with pytest.raises(SystemExit):
        cli.main([str(h5), "--kind", "channel", "--from-name", "a"])


def test_cli_no_matched_paths_returns_one(tmp_path):
    # Directory with no .h5 files inside.
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    exit_code = cli.main([
        str(empty_dir), "--kind", "channel",
        "--from-name", "a", "--to-name", "b",
    ])
    assert exit_code == 1


# ── Exit code semantics ────────────────────────────────────────────────


def test_cli_returns_zero_when_any_dataset_processed(tmp_path, monkeypatch):
    """Mixed: one processed, one skipped → exit 0."""
    a = _make_h5_with_channel(tmp_path / "a.h5")
    b = _make_h5_with_channel(tmp_path / "b.h5")

    from percell4.application.use_cases.batch_rename_resource import (
        BatchOperationReport,
        BatchOperationItemResult,
    )

    def fake(paths, **kw):
        return BatchOperationReport(items=(
            BatchOperationItemResult(h5_path=Path(paths[0]), status="succeeded", processed=("ch0",)),
            BatchOperationItemResult(h5_path=Path(paths[1]), status="skipped_no_changes", skipped={"ch0": "not found"}),
        ))

    monkeypatch.setattr(cli, "batch_rename_resource", fake)
    exit_code = cli.main([str(a), str(b), "--kind", "channel", "--from-name", "ch0", "--to-name", "ch1"])
    assert exit_code == 0


def test_cli_returns_one_when_no_progress(tmp_path, monkeypatch):
    """All skipped → exit 1."""
    a = _make_h5_with_channel(tmp_path / "a.h5")
    from percell4.application.use_cases.batch_rename_resource import (
        BatchOperationReport,
        BatchOperationItemResult,
    )

    def fake(paths, **kw):
        return BatchOperationReport(items=(
            BatchOperationItemResult(h5_path=Path(paths[0]), status="skipped_no_changes", skipped={"ch0": "not found"}),
        ))

    monkeypatch.setattr(cli, "batch_rename_resource", fake)
    exit_code = cli.main([str(a), "--kind", "channel", "--from-name", "ch0", "--to-name", "ch1"])
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


def test_cli_uses_renamed_verb_in_output(tmp_path, stub_use_case, capsys):
    """Output should read 'N renamed' (not 'N processed') for this CLI."""
    h5 = _make_h5_with_channel(tmp_path / "ds.h5")
    cli.main([str(h5), "--kind", "channel", "--from-name", "mScar", "--to-name", "mScarlet"])
    out = capsys.readouterr().out
    assert "renamed" in out
    assert "[succeeded]" in out


# ── End-to-end (real use case) ─────────────────────────────────────────


def test_end_to_end_rename_against_real_h5(tmp_path):
    """Smoke test that the CLI + real use case + real DatasetStore.rename_channel
    all line up: one .h5, one rename, exit 0, channel actually renamed on disk."""
    h5 = _make_h5_with_channel(tmp_path / "ds.h5", channel="mScar")
    exit_code = cli.main([
        str(h5), "--kind", "channel",
        "--from-name", "mScar", "--to-name", "mScarlet",
    ])
    assert exit_code == 0
    with h5py.File(h5, "r") as f:
        assert "decay/mScarlet" in f
        assert "decay/mScar" not in f
        names = list(f["metadata"].attrs["channel_names"])
        assert names == ["mScarlet"]
