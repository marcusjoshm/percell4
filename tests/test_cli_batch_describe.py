"""Tests for the percell4-batch-describe CLI entry point.

Exercises argparse (the required verb group), path resolution, the seam
between the CLI and batch_set_description, and exit codes. The use case is
monkeypatched in most tests so the CLI shape is what's under test; a couple
of end-to-end tests run against real .h5 files.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.interfaces.cli import batch_describe as cli
from percell4.store import DatasetStore


def _make_dataset(path: Path, description: str | None = None) -> DatasetStore:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = DatasetStore(path)
    store.create(metadata={"source": "test"})
    store.write_array(
        "intensity",
        np.zeros((8, 8), dtype=np.float32),
        attrs={"dims": ["H", "W"]},
    )
    if description is not None:
        store.set_description(description)
    return store


@pytest.fixture
def stub_use_case(monkeypatch):
    """Capture the args the CLI passes to batch_set_description."""
    calls: dict = {}

    def fake_run(paths, *, verb, text=None, dry_run=False, progress_callback=None):
        from percell4.application.use_cases.batch_rename_resource import (
            BatchOperationItemResult,
            BatchOperationReport,
        )
        calls["paths"] = list(paths)
        calls["verb"] = verb
        calls["text"] = text
        calls["dry_run"] = dry_run
        items = []
        for p in paths:
            item = BatchOperationItemResult(
                h5_path=Path(p), status="succeeded", processed=("description",),
            )
            items.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return BatchOperationReport(items=tuple(items))

    monkeypatch.setattr(cli, "batch_set_description", fake_run)
    return calls


# ── Verb group ────────────────────────────────────────────────


def test_no_verb_exits_without_touching_files(tmp_path, capsys):
    """AE6: text without a verb refuses to run and writes nothing."""
    p = tmp_path / "dish.h5"
    store = _make_dataset(p, "original")
    with pytest.raises(SystemExit) as exc:
        cli.main([str(p)])
    assert exc.value.code == 2
    assert store.description == "original"


def test_two_verbs_at_once_rejected(tmp_path):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    with pytest.raises(SystemExit) as exc:
        cli.main([str(p), "--set", "a", "--append", "b"])
    assert exc.value.code == 2


def test_clear_takes_no_text(tmp_path):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    with pytest.raises(SystemExit) as exc:
        cli.main([str(p), "--clear", "extra-positional-becomes-a-path"])
    # The stray token is parsed as another path, not as --clear's argument,
    # so this fails on the unreadable file rather than on argparse.
    assert exc.value.code != 0


@pytest.mark.parametrize("flag", ["--set", "--append"])
def test_blank_text_is_rejected_before_any_file_opens(tmp_path, flag):
    """Empty text would store a blank placeholder; --clear is the way."""
    p = tmp_path / "dish.h5"
    store = _make_dataset(p, "original")
    with pytest.raises(SystemExit) as exc:
        cli.main([str(p), flag, "   "])
    assert exc.value.code == 2
    assert store.description == "original"


# ── Path resolution ───────────────────────────────────────────


def test_directory_argument_expands_to_h5_files(tmp_path, stub_use_case):
    for name in ("a.h5", "b.h5"):
        _make_dataset(tmp_path / name)
    (tmp_path / "notes.txt").write_text("ignore me")
    rc = cli.main([str(tmp_path), "--set", "notes"])
    assert rc == 0
    assert [p.name for p in stub_use_case["paths"]] == ["a.h5", "b.h5"]


def test_mixed_file_and_directory_args_resolve_in_order(tmp_path, stub_use_case):
    solo = tmp_path / "solo.h5"
    _make_dataset(solo)
    folder = tmp_path / "folder"
    for name in ("x.h5", "y.h5"):
        _make_dataset(folder / name)
    rc = cli.main([str(solo), str(folder), "--set", "notes"])
    assert rc == 0
    assert [p.name for p in stub_use_case["paths"]] == ["solo.h5", "x.h5", "y.h5"]


def test_no_matching_h5_exits_nonzero(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = cli.main([str(empty), "--set", "notes"])
    assert rc == 1
    assert "no .h5 files matched" in capsys.readouterr().err


# ── Seam: what the CLI forwards ───────────────────────────────


def test_forwards_set_verb_and_text(tmp_path, stub_use_case):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    cli.main([str(p), "--set", "HeLa p14"])
    assert stub_use_case["verb"] == "set"
    assert stub_use_case["text"] == "HeLa p14"
    assert stub_use_case["dry_run"] is False


def test_forwards_append_verb_and_text(tmp_path, stub_use_case):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    cli.main([str(p), "--append", "2h drug"])
    assert stub_use_case["verb"] == "append"
    assert stub_use_case["text"] == "2h drug"


def test_forwards_clear_verb_with_no_text(tmp_path, stub_use_case):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    cli.main([str(p), "--clear"])
    assert stub_use_case["verb"] == "clear"
    assert stub_use_case["text"] is None


def test_forwards_dry_run(tmp_path, stub_use_case):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    cli.main([str(p), "--set", "notes", "--dry-run"])
    assert stub_use_case["dry_run"] is True


# ── Exit codes ────────────────────────────────────────────────


def test_exit_zero_when_a_dataset_progressed(tmp_path):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    assert cli.main([str(p), "--set", "notes"]) == 0


def test_exit_one_when_every_dataset_skipped(tmp_path):
    """Clearing a folder with no descriptions makes no progress."""
    for name in ("a.h5", "b.h5"):
        _make_dataset(tmp_path / name)
    assert cli.main([str(tmp_path), "--clear"]) == 1


def test_exit_one_when_every_dataset_failed(tmp_path):
    bad = tmp_path / "bad.h5"
    bad.write_text("not an hdf5 file")
    assert cli.main([str(bad), "--set", "notes"]) == 1


# ── End to end ────────────────────────────────────────────────


def test_set_append_clear_cycle_end_to_end(tmp_path, capsys):
    p = tmp_path / "dish.h5"
    store = _make_dataset(p)

    assert cli.main([str(p), "--set", "HeLa p14, 4% PFA"]) == 0
    assert store.description == "HeLa p14, 4% PFA"

    assert cli.main([str(p), "--append", "2h 10uM drug"]) == 0
    assert store.description == "HeLa p14, 4% PFA\n\n2h 10uM drug"

    assert cli.main([str(p), "--clear"]) == 0
    assert store.description is None


def test_dry_run_end_to_end_leaves_files_untouched(tmp_path):
    p = tmp_path / "dish.h5"
    store = _make_dataset(p, "original")
    before = p.read_bytes()
    assert cli.main([str(p), "--set", "replacement", "--dry-run"]) == 0
    assert p.read_bytes() == before
    assert store.description == "original"


def test_shared_experiment_text_appended_across_a_folder(tmp_path):
    """The workflow the feature exists for: one prep note, many dishes."""
    stores = {}
    for i, name in enumerate(("dish_1.h5", "dish_2.h5", "dish_3.h5")):
        stores[name] = _make_dataset(tmp_path / name, f"dish {i + 1}")
    assert cli.main([str(tmp_path), "--append", "4% PFA 15min"]) == 0
    for name, store in stores.items():
        assert store.description.endswith("\n\n4% PFA 15min"), name


def test_per_dataset_status_lines_printed(tmp_path, capsys):
    _make_dataset(tmp_path / "good.h5")
    (tmp_path / "bad.h5").write_text("not an hdf5 file")
    cli.main([str(tmp_path), "--set", "notes"])
    out = capsys.readouterr().out
    assert "good.h5" in out
    assert "bad.h5" in out
    assert "1 described" in out
    assert "Totals: 1 succeeded, 1 failed, 0 skipped" in out
