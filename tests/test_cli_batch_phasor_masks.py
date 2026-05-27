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
        roi_sources=None,
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
        calls["roi_sources"] = (
            dict(roi_sources) if roi_sources is not None else None
        )
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


# ── --roi-source flag: happy paths ────────────────────────────────────


def test_roi_source_single_target_resolves(tmp_path, stub_use_case):
    """--roi-source target.h5=source.h5 → resolved keys + complete dict."""
    source = _make_h5(tmp_path / "source.h5", channels=["ch0"])
    target = _make_h5(tmp_path / "target.h5", channels=["ch0"])

    exit_code = cli.main([
        str(source), str(target),
        "--channels", "ch0",
        "--roi-source", f"{target}={source}",
    ])

    assert exit_code == 0
    captured = stub_use_case["roi_sources"]
    assert captured is not None
    # Keys are resolved paths.
    assert source.resolve() in captured
    assert target.resolve() in captured
    # Target maps to resolved source; source maps to None (self-fitting).
    assert captured[target.resolve()] == source.resolve()
    assert captured[source.resolve()] is None
    # Dict is complete: every positional path is present.
    assert set(captured.keys()) == {source.resolve(), target.resolve()}


def test_roi_source_two_targets_one_source(tmp_path, stub_use_case):
    """Two --roi-source flags pointing at the same source."""
    source = _make_h5(tmp_path / "source.h5", channels=["ch0"])
    t1 = _make_h5(tmp_path / "t1.h5", channels=["ch0"])
    t2 = _make_h5(tmp_path / "t2.h5", channels=["ch0"])

    exit_code = cli.main([
        str(source), str(t1), str(t2),
        "--channels", "ch0",
        "--roi-source", f"{t1}={source}",
        "--roi-source", f"{t2}={source}",
    ])

    assert exit_code == 0
    captured = stub_use_case["roi_sources"]
    assert captured[t1.resolve()] == source.resolve()
    assert captured[t2.resolve()] == source.resolve()
    assert captured[source.resolve()] is None


def test_roi_source_relative_paths_resolve(tmp_path, stub_use_case, monkeypatch):
    """Run from a directory containing .h5 files; positional args are basenames.

    Both halves of --roi-source resolve to absolute paths and validation passes.
    """
    source = _make_h5(tmp_path / "untreated_a.h5", channels=["ch0"])
    target = _make_h5(tmp_path / "AsTreated_a.h5", channels=["ch0"])

    monkeypatch.chdir(tmp_path)
    exit_code = cli.main([
        "untreated_a.h5", "AsTreated_a.h5",
        "--channels", "ch0",
        "--roi-source", "AsTreated_a.h5=untreated_a.h5",
    ])

    assert exit_code == 0
    captured = stub_use_case["roi_sources"]
    # Resolved absolute keys.
    assert target.resolve() in captured
    assert source.resolve() in captured
    assert captured[target.resolve()] == source.resolve()
    assert captured[source.resolve()] is None


# ── --roi-source flag: validation rejections ──────────────────────────


def test_roi_source_malformed_value_rejected(tmp_path, capsys):
    """--roi-source foo (no '=') → exit 2, stderr names the value."""
    h5 = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    exit_code = cli.main([
        str(h5),
        "--channels", "ch0",
        "--roi-source", "foo",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--roi-source" in err
    assert "foo" in err


def test_roi_source_empty_target_rejected(tmp_path, capsys):
    """--roi-source =source.h5 → exit 2."""
    source = _make_h5(tmp_path / "source.h5", channels=["ch0"])
    exit_code = cli.main([
        str(source),
        "--channels", "ch0",
        "--roi-source", f"={source}",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--roi-source" in err


def test_roi_source_empty_source_rejected(tmp_path, capsys):
    """--roi-source target.h5= → exit 2."""
    target = _make_h5(tmp_path / "target.h5", channels=["ch0"])
    exit_code = cli.main([
        str(target),
        "--channels", "ch0",
        "--roi-source", f"{target}=",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--roi-source" in err


def test_roi_source_target_not_in_paths_rejected(tmp_path, capsys):
    """TARGET not in positional paths → exit 2, message names the missing path."""
    source = _make_h5(tmp_path / "source.h5", channels=["ch0"])
    missing = tmp_path / "missing.h5"
    exit_code = cli.main([
        str(source),
        "--channels", "ch0",
        "--roi-source", f"{missing}={source}",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "missing.h5" in err


def test_roi_source_source_not_in_paths_rejected(tmp_path, capsys):
    """SOURCE not in positional paths → exit 2, message names the missing path."""
    target = _make_h5(tmp_path / "target.h5", channels=["ch0"])
    missing = tmp_path / "missing.h5"
    exit_code = cli.main([
        str(target),
        "--channels", "ch0",
        "--roi-source", f"{target}={missing}",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "missing.h5" in err


def test_roi_source_chain_rejected(tmp_path, capsys):
    """Chain: a→b and b→c → exit 2, message mentions b is both source and target."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    c = _make_h5(tmp_path / "c.h5", channels=["ch0"])
    exit_code = cli.main([
        str(a), str(b), str(c),
        "--channels", "ch0",
        "--roi-source", f"{a}={b}",
        "--roi-source", f"{b}={c}",
    ])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "chain" in err.lower()
    assert "b.h5" in err


def test_roi_source_self_reference_normalized(tmp_path, stub_use_case):
    """--roi-source a.h5=a.h5 → no error, equivalent to omitting the flag."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    exit_code = cli.main([
        str(a),
        "--channels", "ch0",
        "--roi-source", f"{a}={a}",
    ])
    assert exit_code == 0
    captured = stub_use_case["roi_sources"]
    # Self-reference is normalized — a.h5 maps to None, not to itself.
    assert captured[a.resolve()] is None


# ── Dry-run output for --roi-source ───────────────────────────────────


def test_dry_run_prints_source_tags(tmp_path, monkeypatch, capsys):
    """--dry-run --roi-source target=source prints both tagged lines."""
    source = _make_h5(tmp_path / "source.h5", channels=["ch0"])
    target = _make_h5(tmp_path / "target.h5", channels=["ch0"])

    called = {"n": 0}

    def fake_run(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("dry-run must not call the use case")

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake_run)

    exit_code = cli.main([
        str(source), str(target),
        "--channels", "ch0",
        "--roi-source", f"{target}={source}",
        "--dry-run",
    ])

    assert exit_code == 0
    assert called["n"] == 0
    out = capsys.readouterr().out
    # source.h5 is self-fitting; target.h5 uses source.h5.
    assert "source.h5" in out
    assert "[source: self]" in out
    assert f"[source: {source.name}]" in out
    # target.h5 line appears in the plan.
    assert "target.h5" in out


# ── Real-run stdout tagging ───────────────────────────────────────────


def test_real_run_stdout_has_source_tags(tmp_path, monkeypatch, capsys):
    """Each per-item stdout line carries its [source: ...] tag."""
    source = _make_h5(tmp_path / "source.h5", channels=["ch0"])
    target = _make_h5(tmp_path / "target.h5", channels=["ch0"])

    from percell4.application.use_cases.batch_compute_phasor import (
        BatchPhasorItemResult,
        BatchPhasorReport,
    )

    def fake_run(paths, *, progress_callback=None, **kw):
        items = []
        for p in paths:
            item = BatchPhasorItemResult(
                h5_path=Path(p),
                status="succeeded",
                processed=("ch0",),
            )
            items.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return BatchPhasorReport(items=tuple(items))

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake_run)

    exit_code = cli.main([
        str(source), str(target),
        "--channels", "ch0",
        "--roi-source", f"{target}={source}",
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    # Source line: self; target line: source.h5.
    assert "[source: self]" in out
    assert f"[source: {source.name}]" in out


def test_final_summary_line(tmp_path, monkeypatch, capsys):
    """End-of-run summary contains 'N self-fitted, N used shared ROI.'"""
    source = _make_h5(tmp_path / "source.h5", channels=["ch0"])
    t1 = _make_h5(tmp_path / "t1.h5", channels=["ch0"])
    t2 = _make_h5(tmp_path / "t2.h5", channels=["ch0"])

    from percell4.application.use_cases.batch_compute_phasor import (
        BatchPhasorItemResult,
        BatchPhasorReport,
    )

    def fake_run(paths, *, progress_callback=None, **kw):
        items = tuple(
            BatchPhasorItemResult(
                h5_path=Path(p),
                status="succeeded",
                processed=("ch0",),
            )
            for p in paths
        )
        for item in items:
            if progress_callback is not None:
                progress_callback(item)
        return BatchPhasorReport(items=items)

    monkeypatch.setattr(cli, "batch_fit_phasor_masks", fake_run)

    exit_code = cli.main([
        str(source), str(t1), str(t2),
        "--channels", "ch0",
        "--roi-source", f"{t1}={source}",
        "--roi-source", f"{t2}={source}",
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "1 self-fitted, 2 used shared ROI." in out


# ── (CLI, GUI) parity with --roi-source ────────────────────────────────


def test_cli_gui_parity_with_roi_source(tmp_path):
    """(CLI, GUI) parity for the one-source-one-target case.

    Drive the same operation through the CLI and directly through
    ``batch_fit_phasor_masks`` (which is what the dialog does at
    ``_on_start_clicked``). Assert the on-disk masks are byte-equal.
    """
    from percell4.application.use_cases.batch_fit_phasor_masks import (
        batch_fit_phasor_masks,
    )

    # Two pairs of fixtures (same content per pair).
    src_cli = _make_h5(tmp_path / "src_cli.h5", channels=["ch0"], seed=42)
    tgt_cli = _make_h5(tmp_path / "tgt_cli.h5", channels=["ch0"], seed=99)
    src_gui = _make_h5(tmp_path / "src_gui.h5", channels=["ch0"], seed=42)
    tgt_gui = _make_h5(tmp_path / "tgt_gui.h5", channels=["ch0"], seed=99)

    # CLI run.
    exit_code = cli.main([
        str(src_cli), str(tgt_cli),
        "--channels", "ch0",
        "--t-fit", "10.0",
        "--t-mask-a", "0.0",
        "--t-mask-b", "5.0",
        "--suffix-a", "_phasor_1",
        "--suffix-b", "_phasor_5",
        "--roi-source", f"{tgt_cli}={src_cli}",
    ])
    assert exit_code == 0

    # Direct ("GUI") run — same kwargs the dialog builds in _on_start_clicked.
    roi_sources_gui = {
        src_gui.resolve(): None,
        tgt_gui.resolve(): src_gui.resolve(),
    }
    report = batch_fit_phasor_masks(
        [src_gui, tgt_gui],
        channels=["ch0"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_5",
        ensure_phasor=True,
        roi_sources=roi_sources_gui,
    )
    statuses = {it.h5_path.resolve(): it.status for it in report.items}
    assert statuses[src_gui.resolve()] == "succeeded"
    assert statuses[tgt_gui.resolve()] == "succeeded"

    # Byte-for-byte parity on every produced mask.
    with h5py.File(src_cli, "r") as fc, h5py.File(src_gui, "r") as fg:
        for name in ("ch0_phasor_1", "ch0_phasor_5"):
            assert np.array_equal(
                fc[f"masks/{name}"][()],
                fg[f"masks/{name}"][()],
            ), f"CLI/GUI parity violation on src/{name}"
    with h5py.File(tgt_cli, "r") as fc, h5py.File(tgt_gui, "r") as fg:
        for name in ("ch0_phasor_1", "ch0_phasor_5"):
            assert np.array_equal(
                fc[f"masks/{name}"][()],
                fg[f"masks/{name}"][()],
            ), f"CLI/GUI parity violation on tgt/{name}"


# ── _batch_report.format_item_line signature regression ────────────────


def test_batch_report_format_item_line_signature_unchanged():
    """Snapshot test: the shared helper signature must not change.

    U4 deliberately wraps `_batch_report.print_item_status` locally
    instead of modifying the shared helper. If a future regression adds
    a kwarg like ``extra_suffix=`` to ``format_item_line``, this test
    catches it.
    """
    import inspect

    from percell4.interfaces.cli._batch_report import (
        format_item_line,
        print_item_status,
    )

    sig = inspect.signature(format_item_line)
    params = list(sig.parameters.values())
    # exactly 2 params: item (positional), verb (kw-only with default "processed").
    assert [p.name for p in params] == ["item", "verb"]
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind == inspect.Parameter.KEYWORD_ONLY
    assert params[1].default == "processed"

    # print_item_status: item (positional), quiet (kw-only, default False),
    # verb (kw-only, default "processed").
    sig2 = inspect.signature(print_item_status)
    params2 = list(sig2.parameters.values())
    assert [p.name for p in params2] == ["item", "quiet", "verb"]
    assert params2[1].kind == inspect.Parameter.KEYWORD_ONLY
    assert params2[1].default is False
    assert params2[2].kind == inspect.Parameter.KEYWORD_ONLY
    assert params2[2].default == "processed"
