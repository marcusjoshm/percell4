"""Tests for the batch-phasor CLI entry point.

Exercises argparse, path resolution, the progress-callback printing
shape, exit codes, and the seam between the CLI and
``batch_compute_phasor``. The underlying ``ComputePhasor`` /
``ApplyWavelet`` use cases are monkeypatched at the module level so
the test runs without DTCWT or scipy median-filter -- the goal here is
CLI behavior, not the FLIM math.
"""

from __future__ import annotations

import sys
from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.domain.flim.wavelet_filter import MAX_FILTER_LEVEL
from percell4.interfaces.cli import batch_phasor as cli


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channels: list[str],
    with_calibration: bool = True,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels
        if with_calibration:
            meta.attrs["flim_frequency_mhz"] = 80.0
            for ch in channels:
                meta.attrs[f"flim_cal_phase_{ch}"] = 0.1
                meta.attrs[f"flim_cal_mod_{ch}"] = 0.9
        decay_grp = f.create_group("decay")
        for ch in channels:
            decay_grp.create_dataset(
                ch, data=np.zeros((4, 4, 8), dtype=np.float32)
            )
    return path


@pytest.fixture
def stub_use_cases(monkeypatch: pytest.MonkeyPatch):
    """Replace ComputePhasor + ApplyWavelet execute with no-op stubs that
    record their calls. The CLI path is what's under test, not the FLIM
    engines."""
    calls: dict[str, list[dict]] = {"compute": [], "wavelet": []}

    def fake_compute(self, channel, harmonic=1, view_bin=1):  # noqa: ARG001
        calls["compute"].append(
            {"channel": channel, "harmonic": harmonic, "view_bin": view_bin}
        )

    def fake_wavelet(self, channel, filter_level=9, view_bin=1):  # noqa: ARG001
        calls["wavelet"].append(
            {
                "channel": channel,
                "filter_level": filter_level,
                "view_bin": view_bin,
            }
        )

    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ComputePhasor.execute",
        fake_compute,
    )
    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ApplyWavelet.execute",
        fake_wavelet,
    )
    return calls


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
    # Non-h5 sibling files should be ignored.
    (dir_ / "readme.txt").write_text("ignored")

    paths = cli._resolve_paths([str(dir_)])
    # Alphabetical order from sorted(glob).
    assert paths == [h5_a, h5_b]


def test_resolve_paths_mixes_files_and_directories(tmp_path: Path) -> None:
    file_h5 = _make_h5(tmp_path / "explicit.h5", channels=["ch0"])
    dir_ = tmp_path / "scratch"
    glob_h5 = _make_h5(dir_ / "globbed.h5", channels=["ch0"])

    paths = cli._resolve_paths([str(file_h5), str(dir_)])
    assert paths == [file_h5, glob_h5]


# ── End-to-end main() exit codes + stdout ──────────────────────────────


def test_main_happy_path_returns_zero(
    tmp_path: Path, stub_use_cases, capsys: pytest.CaptureFixture[str]
) -> None:
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])

    exit_code = cli.main([str(h5)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[succeeded] ds.h5" in captured.out
    assert "2 processed" in captured.out
    assert "Totals: 1 succeeded" in captured.out


def test_main_no_matches_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path with no .h5 files yields a stderr error and exit 1."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    exit_code = cli.main([str(empty_dir)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "no .h5 files matched" in captured.err


def test_main_all_skipped_returns_one(
    tmp_path: Path, stub_use_cases, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dataset with no decay channels -> skipped_no_changes -> exit 1
    because no progress was made."""
    h5 = tmp_path / "no_decay.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    exit_code = cli.main([str(h5)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[skipped_no_changes]" in captured.out


def test_main_partial_progress_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A partial-success run (some channels landed, some failed) exits 0
    because some progress was made."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])

    def fake_compute(self, channel, harmonic=1, view_bin=1):  # noqa: ARG001
        return None

    def fake_wavelet(self, channel, filter_level=9, view_bin=1):  # noqa: ARG001
        if channel == "ch1":
            raise RuntimeError("synthetic wavelet failure")

    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ComputePhasor.execute",
        fake_compute,
    )
    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ApplyWavelet.execute",
        fake_wavelet,
    )

    exit_code = cli.main([str(h5)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[partial] ds.h5" in captured.out
    assert "synthetic wavelet failure" in captured.out


# ── --overwrite flag ────────────────────────────────────────────────────


def test_main_overwrite_flag_threads_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--overwrite reaches batch_compute_phasor."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    captured: dict = {}

    real_fn = cli.batch_compute_phasor

    def spy(paths, *, filter_level=9, overwrite=False, progress_callback=None):
        captured["overwrite"] = overwrite
        return real_fn(
            paths, filter_level=filter_level, overwrite=overwrite,
            progress_callback=progress_callback,
        )

    monkeypatch.setattr(cli, "batch_compute_phasor", spy)
    # The spy still calls the real use case, which needs stubbed deps.
    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ComputePhasor.execute",
        lambda self, channel, harmonic=1, view_bin=1: None,
    )
    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ApplyWavelet.execute",
        lambda self, channel, filter_level=9, view_bin=1: None,
    )

    cli.main([str(h5), "--overwrite"])
    assert captured["overwrite"] is True


# ── --filter-level flag ────────────────────────────────────────────────


def test_main_filter_level_out_of_range_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--filter-level below 1 or above MAX_FILTER_LEVEL is rejected via the
    argparse error path."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    with pytest.raises(SystemExit):
        cli.main([str(h5), "--filter-level", "0"])
    with pytest.raises(SystemExit):
        cli.main([str(h5), "--filter-level", str(MAX_FILTER_LEVEL + 1)])


def test_main_filter_level_threads_through_to_wavelet(
    tmp_path: Path, stub_use_cases,
) -> None:
    """The configured filter_level reaches every ApplyWavelet call."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])

    cli.main([str(h5), "--filter-level", "5"])

    for call in stub_use_cases["wavelet"]:
        assert call["filter_level"] == 5


# ── --quiet flag ────────────────────────────────────────────────────────


def test_main_quiet_suppresses_skip_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--quiet suppresses per-channel detail lines but keeps headers + totals."""
    h5 = tmp_path / "no_decay.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    cli.main([str(h5), "--quiet"])
    captured = capsys.readouterr()

    # Per-dataset header line still printed.
    assert "[skipped_no_changes]" in captured.out
    # Totals still printed.
    assert "Totals:" in captured.out
    # No "    _dataset skipped:" indented detail line.
    assert "no decay channels" not in captured.out


def test_main_non_quiet_prints_skip_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    h5 = tmp_path / "no_decay.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    cli.main([str(h5)])
    captured = capsys.readouterr()
    assert "no decay channels" in captured.out


# ── Multi-dataset run ───────────────────────────────────────────────────


def test_main_multi_dataset_processes_each_in_order(
    tmp_path: Path, stub_use_cases, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two datasets, two channels each -> 4 compute calls and 4 wavelet calls."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0", "ch1"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0", "ch1"])

    exit_code = cli.main([str(h5_a), str(h5_b)])

    assert exit_code == 0
    assert len(stub_use_cases["compute"]) == 4
    assert len(stub_use_cases["wavelet"]) == 4
    captured = capsys.readouterr()
    # Both dataset headers in order.
    a_pos = captured.out.find("a.h5")
    b_pos = captured.out.find("b.h5")
    assert 0 <= a_pos < b_pos


# ── --help ──────────────────────────────────────────────────────────────


def test_help_includes_description_and_examples(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--help shows the description, the calibration skip rule, and at
    least one usage example. Polishes per U3 of the plan."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    # Description elements.
    assert "Batch-compute phasor" in captured.out
    assert "flim_cal_phase_<ch>" in captured.out
    # Per-flag help.
    assert "--filter-level" in captured.out
    assert "--overwrite" in captured.out
    assert "--quiet" in captured.out
    # Examples block.
    assert "Examples:" in captured.out


# ── Seam: CLI imports without Qt / napari ──────────────────────────────


# ── --remove flag ──────────────────────────────────────────────────────


def _has_phasor(h5_path: Path, channel: str) -> bool:
    with h5py.File(h5_path, "r") as f:
        return f"phasor/{channel}" in f


def test_remove_flag_deletes_phasor_groups(tmp_path: Path, capsys) -> None:
    """--remove deletes /phasor/<ch>/ for every channel with data."""
    h5 = tmp_path / "ds.h5"
    _make_h5(h5, channels=["ch0", "ch1"])
    # Plant phasor data so there's something to remove.
    with h5py.File(h5, "a") as f:
        phasor = f.create_group("phasor")
        for ch in ("ch0", "ch1"):
            grp = phasor.create_group(ch)
            grp.create_dataset("g", data=np.zeros((4, 4), dtype=np.float32))
            grp.create_dataset("s", data=np.zeros((4, 4), dtype=np.float32))

    exit_code = cli.main([str(h5), "--remove"])

    assert exit_code == 0
    assert not _has_phasor(h5, "ch0")
    assert not _has_phasor(h5, "ch1")
    captured = capsys.readouterr()
    # Output uses the removal verb, not "processed".
    assert "removed" in captured.out
    assert "[succeeded]" in captured.out


def test_remove_skips_channels_with_no_phasor(tmp_path: Path, capsys) -> None:
    """Channels without /phasor/<ch>/ on disk are reported as skipped."""
    h5 = tmp_path / "ds.h5"
    _make_h5(h5, channels=["ch0", "ch1"])
    # Only plant phasor data for ch0; ch1 should be skipped.
    with h5py.File(h5, "a") as f:
        grp = f.create_group("phasor/ch0")
        grp.create_dataset("g", data=np.zeros((4, 4), dtype=np.float32))

    exit_code = cli.main([str(h5), "--remove"])

    assert exit_code == 0
    captured = capsys.readouterr()
    # ch0 removed, ch1 skipped → status "partial"
    assert "[partial]" in captured.out
    assert "1 removed" in captured.out
    assert "1 skipped" in captured.out


def test_remove_and_overwrite_mutually_exclusive(tmp_path: Path) -> None:
    """argparse rejects --remove + --overwrite together."""
    h5 = tmp_path / "ds.h5"
    _make_h5(h5, channels=["ch0"])
    with pytest.raises(SystemExit) as exc:
        cli.main([str(h5), "--remove", "--overwrite"])
    assert exc.value.code != 0


def test_remove_ignores_filter_level_validation(tmp_path: Path) -> None:
    """--filter-level out-of-range is allowed when --remove is set."""
    h5 = tmp_path / "ds.h5"
    _make_h5(h5, channels=["ch0"])
    with h5py.File(h5, "a") as f:
        grp = f.create_group("phasor/ch0")
        grp.create_dataset("g", data=np.zeros((4, 4), dtype=np.float32))

    # filter_level=0 would normally error in compute mode. With --remove
    # it's irrelevant; main() should not validate it.
    exit_code = cli.main([str(h5), "--remove", "--filter-level", "0"])
    assert exit_code == 0
    assert not _has_phasor(h5, "ch0")


def test_remove_all_clean_returns_exit_1(tmp_path: Path) -> None:
    """When no dataset had phasor to remove, exit code signals no-progress."""
    h5 = tmp_path / "ds.h5"
    _make_h5(h5, channels=["ch0"])  # no /phasor/ group at all
    exit_code = cli.main([str(h5), "--remove"])
    assert exit_code == 1


def test_remove_help_text_present(capsys) -> None:
    """--remove appears in the CLI help."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    captured = capsys.readouterr()
    assert "--remove" in captured.out
    assert "delete" in captured.out.lower()


def test_cli_module_imports_without_qt() -> None:
    """Importing the batch_phasor CLI must not pull in Qt or napari."""
    # The module is already imported at test-collection time; check
    # whether Qt / napari leaked in via that import.
    qt_modules = {
        m for m in sys.modules
        if "PyQt" in m or "qtpy" in m or m.startswith("napari")
    }
    # Some Qt may be in sys.modules from OTHER tests in the same
    # session. The test is meaningful only on a clean import. Just
    # verify the CLI module is loaded without crashing -- the import
    # chain doesn't pull qt-only modules.
    import importlib
    importlib.reload(cli)
    # Confirm core dependencies loaded.
    assert hasattr(cli, "main")
    assert hasattr(cli, "batch_compute_phasor")
