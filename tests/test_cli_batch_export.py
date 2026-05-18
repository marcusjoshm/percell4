"""Tests for the batch-export CLI entry point.

Exercises argparse, path resolution, the progress-callback printing
shape, exit codes, and the seam between the CLI and
``batch_export_images``. The use case runs against real HDF5 files
written via DatasetStore + h5py; tifffile.imwrite hits the real
filesystem under tmp_path. No monkeypatching of the use case -- this
is the end-to-end CLI check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import h5py
import numpy as np
import pytest
import tifffile

from percell4.interfaces.cli import batch_export as cli
from percell4.store import DatasetStore


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channels: list[str] | None = None,
    intensity_shape: tuple[int, ...] | None = (2, 4, 4),
    labels: dict[str, np.ndarray] | None = None,
    masks: dict[str, np.ndarray] | None = None,
) -> Path:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": channels or []})
    if intensity_shape is not None:
        store.write_array(
            "intensity", np.zeros(intensity_shape, dtype=np.uint16),
        )
    for name, data in (labels or {}).items():
        store.write_labels(name, data)
    for name, data in (masks or {}).items():
        store.write_mask(name, data)
    return path


# ── Path resolution ─────────────────────────────────────────────────────


def test_resolve_paths_passes_through_h5_files(tmp_path: Path) -> None:
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    paths = cli._resolve_paths([str(h5_a), str(h5_b)])
    assert paths == [h5_a, h5_b]


def test_resolve_paths_globs_directory_arguments(tmp_path: Path) -> None:
    dir_ = tmp_path / "scratch"
    h5_a = _make_h5(dir_ / "a.h5", channels=["ch0"])
    h5_b = _make_h5(dir_ / "b.h5", channels=["ch0"])
    (dir_ / "readme.txt").write_text("ignored")

    paths = cli._resolve_paths([str(dir_)])
    assert paths == [h5_a, h5_b]


def test_resolve_paths_mixes_files_and_directories(tmp_path: Path) -> None:
    file_h5 = _make_h5(tmp_path / "explicit.h5", channels=["ch0"])
    dir_ = tmp_path / "scratch"
    glob_h5 = _make_h5(dir_ / "globbed.h5", channels=["ch0"])
    paths = cli._resolve_paths([str(file_h5), str(dir_)])
    assert paths == [file_h5, glob_h5]


# ── End-to-end main() exit codes + stdout ──────────────────────────────


def test_main_happy_path_writes_tiffs_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        labels={"seg": np.zeros((4, 4), dtype=np.int32)},
    )

    exit_code = cli.main([str(h5), "--output-dir", str(out)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[succeeded] ds.h5" in captured.out
    assert "3 files" in captured.out  # ch0 + ch1 + seg
    assert "Totals: 1 succeeded" in captured.out
    assert "3 files written" in captured.out

    # On-disk files match the expected pattern.
    assert (out / "ds_ch0.tif").exists()
    assert (out / "ds_ch1.tif").exists()
    assert (out / "ds_seg.tif").exists()


def test_main_short_output_dir_flag(tmp_path: Path) -> None:
    """-o is a recognized short form of --output-dir."""
    out = tmp_path / "out"
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])

    exit_code = cli.main([str(h5), "-o", str(out)])
    assert exit_code == 0
    assert any(out.glob("ds_*.tif"))


def test_main_no_matches_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty directory yields a stderr error and exit 1."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    exit_code = cli.main([str(empty_dir), "--output-dir", str(tmp_path / "out")])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "no .h5 files matched" in captured.err


def test_main_all_skipped_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dataset with no layers -> skipped_no_changes -> exit 1
    because no files were written."""
    out = tmp_path / "out"
    h5 = tmp_path / "empty.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    exit_code = cli.main([str(h5), "--output-dir", str(out)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[skipped_no_changes]" in captured.out
    assert "0 files written" in captured.out


def test_main_partial_progress_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One succeeded + one failed -> exit 0 because some files landed."""
    out = tmp_path / "out"
    h5_ok = _make_h5(tmp_path / "ok.h5", channels=["ch0"])
    h5_missing = tmp_path / "missing.h5"

    exit_code = cli.main([
        str(h5_missing), str(h5_ok), "--output-dir", str(out),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[failed] missing.h5" in captured.out
    assert "[succeeded] ok.h5" in captured.out


# ── --output-dir required ──────────────────────────────────────────────


def test_main_missing_output_dir_errors(tmp_path: Path) -> None:
    """--output-dir is required; argparse exits with SystemExit."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    with pytest.raises(SystemExit):
        cli.main([str(h5)])


# ── --quiet flag ───────────────────────────────────────────────────────


def test_main_quiet_suppresses_error_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--quiet suppresses indented error detail lines for failed items.
    Headers + totals still print."""
    out = tmp_path / "out"
    h5_missing = tmp_path / "missing.h5"

    cli.main([str(h5_missing), "--output-dir", str(out), "--quiet"])
    captured = capsys.readouterr()
    assert "[failed] missing.h5" in captured.out
    assert "Totals:" in captured.out
    # No indented 'error:' detail line.
    assert "    error:" not in captured.out


def test_main_non_quiet_prints_error_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    h5_missing = tmp_path / "missing.h5"

    cli.main([str(h5_missing), "--output-dir", str(out)])
    captured = capsys.readouterr()
    assert "    error:" in captured.out


# ── Multi-dataset ──────────────────────────────────────────────────────


def test_main_multi_dataset_processes_each(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0", "ch1"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0", "ch1"])

    exit_code = cli.main([str(h5_a), str(h5_b), "--output-dir", str(out)])

    assert exit_code == 0
    # 4 TIFFs total (2 channels x 2 datasets).
    written = sorted(p.name for p in out.iterdir())
    assert written == ["a_ch0.tif", "a_ch1.tif", "b_ch0.tif", "b_ch1.tif"]
    # Both dataset headers in input order.
    captured = capsys.readouterr()
    a_pos = captured.out.find("a.h5")
    b_pos = captured.out.find("b.h5")
    assert 0 <= a_pos < b_pos


# ── Output directory creation ──────────────────────────────────────────


def test_main_creates_missing_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nested" / "exports"
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])

    exit_code = cli.main([str(h5), "--output-dir", str(out)])
    assert exit_code == 0
    assert out.is_dir()


# ── --help ──────────────────────────────────────────────────────────────


def test_help_includes_description_and_examples(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """--help shows description, flat-layout rule, overwrite warning,
    and at least one usage example."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Batch-export dataset layers" in captured.out
    assert "<h5_stem>_<layer>.tif" in captured.out
    assert "overwritten silently" in captured.out
    assert "--output-dir" in captured.out
    assert "--quiet" in captured.out
    assert "Examples:" in captured.out


# ── Seam: CLI imports without Qt / napari ──────────────────────────────


def test_cli_module_imports_without_qt() -> None:
    """Importing the batch_export CLI must not pull in Qt or napari."""
    import importlib
    importlib.reload(cli)
    assert hasattr(cli, "main")
    assert hasattr(cli, "batch_export_images")
