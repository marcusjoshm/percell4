"""Tests for the batch-export-phasor CLI entry point.

Exercises argparse, path resolution, the up-front writability probe,
the three-category progress printer, exit codes, and the seam between
the CLI and batch_export_phasor. End-to-end against real HDF5 files;
PNGs hit the real filesystem under tmp_path. No monkeypatching of the
use case.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from percell4.interfaces.cli import batch_export_phasor as cli
from percell4.store import DatasetStore

_SRC = str(Path(__file__).resolve().parents[1] / "src")


# ── Helpers ─────────────────────────────────────────────────────────────


def _gs(shape=(8, 8)):
    rng = np.random.default_rng(2)
    g = rng.uniform(0.1, 0.9, shape).astype(np.float32)
    s = rng.uniform(0.05, 0.5, shape).astype(np.float32)
    return g, s


def _make_h5(path: Path, *, channels=("ch0",), filtered=False) -> Path:
    store = DatasetStore(path)
    store.create(metadata={})
    for ch in channels:
        g, s = _gs()
        store.write_array(f"phasor/{ch}/g", g)
        store.write_array(f"phasor/{ch}/s", s)
        store.write_array(
            f"decay/{ch}", np.ones((8, 8, 16), np.uint16), is_decay=True
        )
        if filtered:
            store.write_array(f"phasor/{ch}/g_filtered", g * 0.9)
            store.write_array(f"phasor/{ch}/s_filtered", s * 0.9)
    return path


def _make_one_of_each_h5(path: Path) -> Path:
    """A dataset producing exactly one error, one skip, one empty."""
    store = DatasetStore(path)
    store.create(metadata={})
    g, s = _gs()
    # ch_err: decay/g shape mismatch -> error
    store.write_array("phasor/ch_err/g", g)
    store.write_array("phasor/ch_err/s", s)
    store.write_array(
        "decay/ch_err", np.ones((4, 4, 16), np.uint16), is_decay=True
    )
    # ch_skip: asymmetric filtered cache -> skipped (raw still written)
    store.write_array("phasor/ch_skip/g", g)
    store.write_array("phasor/ch_skip/s", s)
    store.write_array("phasor/ch_skip/g_filtered", g * 0.9)
    # ch_empty: all-NaN -> rendered empty
    nan = np.full((8, 8), np.nan, np.float32)
    store.write_array("phasor/ch_empty/g", nan)
    store.write_array("phasor/ch_empty/s", nan)
    return path


# ── Path resolution ─────────────────────────────────────────────────────


def test_resolve_paths_passes_through_h5_files(tmp_path: Path) -> None:
    a = _make_h5(tmp_path / "a.h5")
    b = _make_h5(tmp_path / "b.h5")
    assert cli._resolve_paths([str(a), str(b)]) == [a, b]


def test_resolve_paths_globs_directory(tmp_path: Path) -> None:
    d = tmp_path / "scratch"
    a = _make_h5(d / "a.h5")
    b = _make_h5(d / "b.h5")
    (d / "notes.txt").write_text("ignored")
    assert cli._resolve_paths([str(d)]) == [a, b]


def test_resolve_paths_mixes_files_and_dirs(tmp_path: Path) -> None:
    f = _make_h5(tmp_path / "explicit.h5")
    d = tmp_path / "scratch"
    g = _make_h5(d / "globbed.h5")
    assert cli._resolve_paths([str(f), str(d)]) == [f, g]


# ── Happy path + exit codes ─────────────────────────────────────────────


def test_main_happy_path_writes_pngs_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(tmp_path / "ds.h5", channels=("ch0", "ch1"), filtered=True)

    code = cli.main([str(h5), "--output-dir", str(out)])

    assert code == 0
    captured = capsys.readouterr()
    assert "[succeeded] ds.h5" in captured.out
    assert "4 files" in captured.out
    assert "Totals: 1 succeeded" in captured.out
    assert "4 files written" in captured.out
    assert "rendered empty: 0" in captured.out
    for name in (
        "ds_ch0_phasor.png",
        "ds_ch0_phasor_filtered.png",
        "ds_ch1_phasor.png",
        "ds_ch1_phasor_filtered.png",
    ):
        assert (out / name).exists()


def test_main_short_output_flag(tmp_path: Path) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(tmp_path / "ds.h5")
    assert cli.main([str(h5), "-o", str(out)]) == 0
    assert (out / "ds_ch0_phasor.png").exists()


def test_main_no_matches_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    code = cli.main([str(empty), "--output-dir", str(tmp_path / "o")])
    assert code == 1
    assert "no .h5 files matched" in capsys.readouterr().err


def test_main_no_phasor_skipped_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    h5 = tmp_path / "noph.h5"
    DatasetStore(h5).create(metadata={})
    code = cli.main([str(h5), "--output-dir", str(tmp_path / "out")])
    assert code == 1
    out = capsys.readouterr().out
    assert "[skipped_no_changes]" in out
    assert "0 files written" in out


def test_main_partial_progress_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    ok = _make_h5(tmp_path / "ok.h5")
    missing = tmp_path / "missing.h5"
    code = cli.main([str(missing), str(ok), "--output-dir", str(out)])
    assert code == 0
    text = capsys.readouterr().out
    assert "[failed] missing.h5" in text
    assert "[succeeded] ok.h5" in text
    assert text.find("missing.h5") < text.find("ok.h5")


# ── --output-dir required ──────────────────────────────────────────────


def test_main_missing_output_dir_errors(tmp_path: Path) -> None:
    h5 = _make_h5(tmp_path / "ds.h5")
    with pytest.raises(SystemExit):
        cli.main([str(h5)])


# ── Writability probe ───────────────────────────────────────────────────


def test_unwritable_output_dir_fails_fast(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Output dir under a regular file -> mkdir fails -> exit 1 before
    any dataset is processed (no [succeeded]/[failed] header printed)."""
    h5 = _make_h5(tmp_path / "ds.h5")
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    bad_out = blocker / "sub"  # mkdir under a file -> NotADirectoryError

    code = cli.main([str(h5), "--output-dir", str(bad_out)])

    assert code == 1
    cap = capsys.readouterr()
    assert "not writable" in cap.err
    assert "[succeeded]" not in cap.out
    assert "[failed]" not in cap.out


def test_creates_missing_nested_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nested" / "phasors"
    h5 = _make_h5(tmp_path / "ds.h5")
    assert cli.main([str(h5), "--output-dir", str(out)]) == 0
    assert out.is_dir()


# ── Three-category printer + --quiet ────────────────────────────────────


def test_non_quiet_prints_all_three_categories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    h5 = _make_one_of_each_h5(tmp_path / "mix.h5")

    cli.main([str(h5), "--output-dir", str(out)])

    text = capsys.readouterr().out
    assert "ch_err error:" in text
    assert "ch_skip_filtered skipped:" in text
    assert "rendered empty (no valid phasor pixels)" in text
    assert "rendered empty: 1" in text  # Totals line count


def test_quiet_suppresses_all_three_categories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    h5 = _make_one_of_each_h5(tmp_path / "mix.h5")

    cli.main([str(h5), "--output-dir", str(out), "--quiet"])

    text = capsys.readouterr().out
    assert "[" in text and "Totals:" in text  # header + totals remain
    assert "error:" not in text
    assert "skipped:" not in text
    assert "rendered empty (no valid" not in text
    # Totals line still carries the aggregate empty count.
    assert "rendered empty: 1" in text


# ── --help ──────────────────────────────────────────────────────────────


def test_help_documents_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Batch-export cached phasors as PNG" in out
    assert "_phasor.png" in out
    assert "_phasor_filtered.png" in out
    assert "overwritten silently" in out
    assert "Examples:" in out


# ── Seam: CLI imports without Qt / napari ──────────────────────────────


def test_cli_import_does_not_load_qt_or_napari() -> None:
    code = (
        "import sys; "
        "import percell4.interfaces.cli.batch_export_phasor; "
        "assert 'PyQt5' not in sys.modules, 'PyQt5 leaked'; "
        "assert 'qtpy' not in sys.modules, 'qtpy leaked'; "
        "assert 'napari' not in sys.modules, 'napari leaked'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": _SRC},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
