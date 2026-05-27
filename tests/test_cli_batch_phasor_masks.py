"""Tests for the percell4-batch-phasor-masks CLI entry point.

Exercises argparse, path resolution, up-front validation (channel
intersection, suffix sanity, collision), the seam between the CLI and
batch_fit_phasor_masks, and exit codes. The use case itself is
monkeypatched in most tests so the CLI shape is what's under test.
A couple of end-to-end tests run against real .h5 files to confirm
the integration works, plus the mandatory (CLI, GUI) parity test
required by the plan.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.interfaces.cli import batch_phasor_masks as cli


# ── Fixture builders ───────────────────────────────────────────────────


def _build_decay(
    shape: tuple[int, int] = (10, 10),
    *,
    total_intensity: float = 100.0,
    n_bins: int = 8,
) -> np.ndarray:
    h, w = shape
    return np.full((h, w, n_bins), total_intensity / n_bins, dtype=np.float32)


def _make_h5(
    path: Path,
    *,
    channels: list[str],
    shape: tuple[int, int] = (10, 10),
    with_phasor_for: list[str] | None = None,
    with_decay_for: list[str] | None = None,
    g_center: float = 0.5,
    s_center: float = 0.3,
    sigma: float = 0.01,
    seed: int = 0,
) -> Path:
    """Build a minimal .h5 with /decay and /phasor groups for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if with_phasor_for is None:
        with_phasor_for = list(channels)
    if with_decay_for is None:
        with_decay_for = list(channels)

    rng = np.random.default_rng(seed=seed)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels
        meta.attrs["flim_frequency_mhz"] = 80.0
        for ch in channels:
            meta.attrs[f"flim_cal_phase_{ch}"] = 0.0
            meta.attrs[f"flim_cal_mod_{ch}"] = 1.0

        decay_grp = f.create_group("decay")
        for ch in with_decay_for:
            decay_grp.create_dataset(ch, data=_build_decay(shape))

        if with_phasor_for:
            ph_grp = f.create_group("phasor")
            for ch in with_phasor_for:
                ch_grp = ph_grp.create_group(ch)
                g = rng.normal(g_center, sigma, size=shape).astype(np.float32)
                s = rng.normal(s_center, sigma, size=shape).astype(np.float32)
                ch_grp.create_dataset("g", data=g)
                ch_grp.create_dataset("s", data=s)
    return path


# ── Use case stub ──────────────────────────────────────────────────────


@pytest.fixture
def stub_use_case(monkeypatch):
    """Capture the args the CLI passes to batch_fit_phasor_masks."""
    calls: dict = {}

    def fake_run(
        paths,
        *,
        channels,
        t_fit,
        t_mask_a,
        t_mask_b,
        suffix_a,
        suffix_b,
        ensure_phasor=True,
        progress_callback=None,
        cancel_check=None,
    ):
        from percell4.application.use_cases.batch_compute_phasor import (
            BatchPhasorItemResult,
            BatchPhasorReport,
        )
        calls["paths"] = list(paths)
        calls["channels"] = list(channels)
        calls["t_fit"] = t_fit
        calls["t_mask_a"] = t_mask_a
        calls["t_mask_b"] = t_mask_b
        calls["suffix_a"] = suffix_a
        calls["suffix_b"] = suffix_b
        calls["ensure_phasor"] = ensure_phasor
        # Fire a fake "succeeded" item for each path.
        items = []
        for p in paths:
            item = BatchPhasorItemResult(
                h5_path=Path(p),
                status="succeeded",
                processed=tuple(channels),
            )
            items.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return BatchPhasorReport(items=tuple(items))

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake_run)
    return calls


# ── Argparse plumbing ──────────────────────────────────────────────────


def test_cli_passes_args_through_to_use_case(tmp_path, stub_use_case):
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])

    exit_code = cli.main([
        str(h5),
        "--channels", "ch0", "ch1",
        "--t-fit", "12.5",
        "--t-mask-a", "1.5",
        "--t-mask-b", "7.0",
        "--suffix-a", "_a",
        "--suffix-b", "_b",
    ])

    assert exit_code == 0
    assert stub_use_case["paths"] == [h5]
    assert stub_use_case["channels"] == ["ch0", "ch1"]
    assert stub_use_case["t_fit"] == 12.5
    assert stub_use_case["t_mask_a"] == 1.5
    assert stub_use_case["t_mask_b"] == 7.0
    assert stub_use_case["suffix_a"] == "_a"
    assert stub_use_case["suffix_b"] == "_b"
    assert stub_use_case["ensure_phasor"] is True


def test_cli_defaults_match_plan(tmp_path, stub_use_case):
    """Omitting flags yields 10.0 / 0.0 / 5.0 / _phasor_1 / _phasor_5."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    cli.main([str(h5), "--channels", "ch0"])
    assert stub_use_case["t_fit"] == 10.0
    assert stub_use_case["t_mask_a"] == 0.0
    assert stub_use_case["t_mask_b"] == 5.0
    assert stub_use_case["suffix_a"] == "_phasor_1"
    assert stub_use_case["suffix_b"] == "_phasor_5"


def test_cli_directory_glob_expands_to_h5_files(tmp_path, stub_use_case):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    cli.main([str(tmp_path), "--channels", "ch0"])
    assert sorted(stub_use_case["paths"]) == sorted([a, b])


def test_cli_directory_glob_is_non_recursive(tmp_path, stub_use_case):
    """A nested *.h5 must NOT be discovered."""
    top = _make_h5(tmp_path / "top.h5", channels=["ch0"])
    sub = tmp_path / "sub"
    sub.mkdir()
    _make_h5(sub / "nested.h5", channels=["ch0"])
    cli.main([str(tmp_path), "--channels", "ch0"])
    assert stub_use_case["paths"] == [top]


def test_cli_requires_paths_and_channels(tmp_path):
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    # Missing --channels
    with pytest.raises(SystemExit):
        cli.main([str(h5)])
    # Missing positional paths
    with pytest.raises(SystemExit):
        cli.main(["--channels", "ch0"])


def test_cli_no_matched_paths_returns_one(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    exit_code = cli.main([str(empty_dir), "--channels", "ch0"])
    assert exit_code == 1


# ── Validation: exit code 2 ────────────────────────────────────────────


def test_channel_intersection_rejection(tmp_path, capsys):
    """Two .h5; one is missing the requested channel → exit 2."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0", "ch1"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0"])  # missing ch1

    exit_code = cli.main([
        str(h5_a), str(h5_b),
        "--channels", "ch0", "ch1",
    ])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "ch1" in err
    assert h5_b.name in err


def test_empty_suffix_a_rejection(tmp_path, capsys):
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    exit_code = cli.main([
        str(h5), "--channels", "ch0",
        "--suffix-a", "", "--suffix-b", "_b",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "suffix" in err.lower()


def test_empty_suffix_b_rejection(tmp_path, capsys):
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    exit_code = cli.main([
        str(h5), "--channels", "ch0",
        "--suffix-a", "_a", "--suffix-b", "",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "suffix" in err.lower()


def test_identical_suffixes_rejection(tmp_path, capsys):
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    exit_code = cli.main([
        str(h5), "--channels", "ch0",
        "--suffix-a", "_same", "--suffix-b", "_same",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "suffix" in err.lower()


def test_collision_rejection_with_default_suffixes(tmp_path, capsys):
    """Dataset already has channel 'mNG_phasor_1' → default suffix_a collides."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["mNG", "mNG_phasor_1"],
    )
    exit_code = cli.main([str(h5), "--channels", "mNG"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "mNG" in err
    assert "_phasor_1" in err


def test_collision_rejection_names_offending_triple(tmp_path, capsys):
    """Error message names dataset, channel, and suffix."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["mNG", "mNG_phasor_5"],  # collides with suffix_b
    )
    exit_code = cli.main([str(h5), "--channels", "mNG"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert h5.name in err
    assert "mNG" in err
    assert "_phasor_5" in err


def test_validation_runs_before_use_case(tmp_path, monkeypatch):
    """Validation failure must not invoke batch_fit_phasor_masks."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    called = {"n": 0}

    def fake_run(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("should not be called when validation fails")

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake_run)
    cli.main([
        str(h5), "--channels", "ch0",
        "--suffix-a", "_same", "--suffix-b", "_same",
    ])
    assert called["n"] == 0


# ── Dry-run ────────────────────────────────────────────────────────────


def test_dry_run_does_not_invoke_use_case(tmp_path, monkeypatch, capsys):
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    called = {"n": 0}

    def fake_run(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("dry-run must not call the use case")

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake_run)
    exit_code = cli.main([str(h5), "--channels", "ch0", "--dry-run"])
    assert exit_code == 0
    assert called["n"] == 0
    out = capsys.readouterr().out
    # Dry-run output mentions the planned shape.
    assert "1 datasets" in out or "1 dataset" in out
    assert "ch0" in out
    assert "t_fit" in out
    assert "_phasor_1" in out


def test_dry_run_still_validates(tmp_path, capsys):
    """--dry-run should still reject identical suffixes (exit 2)."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    exit_code = cli.main([
        str(h5), "--channels", "ch0", "--dry-run",
        "--suffix-a", "_x", "--suffix-b", "_x",
    ])
    assert exit_code == 2


# ── Exit codes ────────────────────────────────────────────────────────


def test_cli_returns_zero_when_any_dataset_processed(tmp_path, monkeypatch):
    """Mixed: one processed, one skipped → exit 0."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    b = _make_h5(tmp_path / "b.h5", channels=["ch0"])

    from percell4.application.use_cases.batch_compute_phasor import (
        BatchPhasorItemResult,
        BatchPhasorReport,
    )

    def fake(paths, **kw):
        return BatchPhasorReport(items=(
            BatchPhasorItemResult(
                h5_path=Path(paths[0]),
                status="succeeded",
                processed=("ch0",),
            ),
            BatchPhasorItemResult(
                h5_path=Path(paths[1]),
                status="skipped_no_changes",
                skipped={"ch0": "channel not present"},
            ),
        ))

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake)
    exit_code = cli.main([str(a), str(b), "--channels", "ch0"])
    assert exit_code == 0


def test_cli_returns_one_when_no_progress(tmp_path, monkeypatch):
    """All skipped → exit 1."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])

    from percell4.application.use_cases.batch_compute_phasor import (
        BatchPhasorItemResult,
        BatchPhasorReport,
    )

    def fake(paths, **kw):
        return BatchPhasorReport(items=(
            BatchPhasorItemResult(
                h5_path=Path(paths[0]),
                status="skipped_no_changes",
                skipped={"ch0": "channel not present"},
            ),
        ))

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake)
    exit_code = cli.main([str(a), "--channels", "ch0"])
    assert exit_code == 1


# ── Output text ────────────────────────────────────────────────────────


def test_cli_default_prints_one_line_per_dataset(tmp_path, stub_use_case, capsys):
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    cli.main([str(h5), "--channels", "ch0"])
    out = capsys.readouterr().out
    assert "[succeeded]" in out
    assert "processed" in out
    assert h5.name in out


def test_cli_quiet_suppresses_details(tmp_path, monkeypatch, capsys):
    """--quiet drops per-channel skip/error indented lines."""
    h5 = _make_h5(tmp_path / "a.h5", channels=["ch0"])

    from percell4.application.use_cases.batch_compute_phasor import (
        BatchPhasorItemResult,
        BatchPhasorReport,
    )

    def fake(paths, *, progress_callback=None, **kw):
        item = BatchPhasorItemResult(
            h5_path=Path(paths[0]),
            status="partial",
            processed=("ch0",),
            skipped={"chX": "channel not present"},
        )
        if progress_callback:
            progress_callback(item)
        return BatchPhasorReport(items=(item,))

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake)
    cli.main([str(h5), "--channels", "ch0", "--quiet"])
    out = capsys.readouterr().out
    # The per-dataset header still prints
    assert "[partial]" in out
    # But the per-channel skip detail is suppressed
    assert "chX skipped" not in out


def test_cli_verbose_runs_cleanly(tmp_path, stub_use_case):
    """--verbose should not error and should propagate normally."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    exit_code = cli.main([str(h5), "--channels", "ch0", "--verbose"])
    assert exit_code == 0


def test_cli_help_lists_required_flags(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "--channels" in out
    assert "--t-fit" in out
    assert "--t-mask-a" in out
    assert "--t-mask-b" in out
    assert "--suffix-a" in out
    assert "--suffix-b" in out
    assert "--dry-run" in out


# ── End-to-end (real use case) ─────────────────────────────────────────


def test_end_to_end_against_real_h5(tmp_path):
    """One real .h5 with channel + decay + phasor → CLI runs to completion,
    masks land on disk with correct names and shapes."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"], shape=(10, 10))

    exit_code = cli.main([
        str(h5),
        "--channels", "ch0",
        "--t-fit", "10.0",
        "--t-mask-a", "0.0",
        "--t-mask-b", "5.0",
        "--suffix-a", "_phasor_1",
        "--suffix-b", "_phasor_5",
    ])

    assert exit_code == 0
    with h5py.File(h5, "r") as f:
        assert "masks/ch0_phasor_1" in f
        assert "masks/ch0_phasor_5" in f
        assert f["masks/ch0_phasor_1"].shape == (10, 10)
        assert f["masks/ch0_phasor_5"].shape == (10, 10)


def test_cli_gui_parity(tmp_path):
    """(CLI, GUI) parity — required by the plan.

    Run the CLI against one fixture; separately, call
    ``batch_fit_phasor_masks`` directly with the same kwargs (which is
    exactly what ``PhasorMasksDialog`` does). Assert the on-disk mask
    arrays are byte-for-byte identical.
    """
    from percell4.application.use_cases.batch_fit_phasor_masks import (
        batch_fit_phasor_masks,
    )

    # Two fixtures: same content, one for the CLI run, one for the
    # direct ("GUI") run.
    h5_cli = _make_h5(tmp_path / "cli.h5", channels=["ch0"], seed=42)
    h5_gui = _make_h5(tmp_path / "gui.h5", channels=["ch0"], seed=42)

    # Run the CLI.
    exit_code = cli.main([
        str(h5_cli),
        "--channels", "ch0",
        "--t-fit", "10.0",
        "--t-mask-a", "0.0",
        "--t-mask-b", "5.0",
        "--suffix-a", "_phasor_1",
        "--suffix-b", "_phasor_5",
    ])
    assert exit_code == 0

    # Run the use case directly (the "GUI" path).
    report = batch_fit_phasor_masks(
        [h5_gui],
        channels=["ch0"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_5",
        ensure_phasor=True,
    )
    assert report.items[0].status == "succeeded"

    # Byte-for-byte parity on every produced mask.
    with h5py.File(h5_cli, "r") as fc, h5py.File(h5_gui, "r") as fg:
        for name in ("ch0_phasor_1", "ch0_phasor_5"):
            cli_arr = fc[f"masks/{name}"][()]
            gui_arr = fg[f"masks/{name}"][()]
            assert cli_arr.shape == gui_arr.shape
            assert cli_arr.dtype == gui_arr.dtype
            assert np.array_equal(cli_arr, gui_arr), (
                f"CLI/GUI parity violation on {name}"
            )
