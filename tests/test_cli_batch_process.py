"""Tests for the batch cellpose+laptrack CLI's source resolution.

Focuses on ``_build_specs`` -- the seam that maps positional arguments
(``.h5`` files, directories of ``.h5`` files, or TIFF source directories)
onto ``DatasetSpec`` objects. No Cellpose or HDF5 content is needed: the
files are empty touch-targets, since ``_build_specs`` only inspects paths.
"""

from __future__ import annotations

from pathlib import Path

from percell4.interfaces.cli import batch_process as cli


def _touch(path: Path) -> Path:
    path.write_bytes(b"")
    return path


def test_dir_of_h5_no_output_dir_segments_in_place(tmp_path: Path) -> None:
    """A directory of .h5 files with no --output-dir yields one in-place
    spec per file (source == output), matching the rest of the CLI suite."""
    d = tmp_path / "datasets"
    d.mkdir()
    a = _touch(d / "dish_a.h5")
    b = _touch(d / "dish_b.h5")

    specs = cli._build_specs([d], None)

    assert len(specs) == 2
    by_src = {s.source_dir: s for s in specs}
    assert by_src[a].output_h5 == a
    assert by_src[b].output_h5 == b


def test_dir_of_h5_with_output_dir_copies(tmp_path: Path) -> None:
    """With --output-dir each .h5 in the directory is remapped to
    <output-dir>/<name>.h5 (copy-then-segment)."""
    d = tmp_path / "datasets"
    d.mkdir()
    _touch(d / "dish_a.h5")
    out = tmp_path / "out"

    specs = cli._build_specs([d], out)

    assert {s.output_h5 for s in specs} == {out / "dish_a.h5"}


def test_empty_dir_without_output_dir_is_skipped_as_tiff_source(
    tmp_path: Path,
) -> None:
    """A directory with no .h5 files is still treated as a TIFF source and
    requires --output-dir (unchanged legacy behavior)."""
    d = tmp_path / "tiff_src"
    d.mkdir()

    specs = cli._build_specs([d], None)

    assert specs == []


def test_tiff_source_dir_with_output_dir(tmp_path: Path) -> None:
    """A .h5-free directory with --output-dir imports to
    <output-dir>/<dirname>.h5."""
    d = tmp_path / "tiff_src"
    d.mkdir()
    out = tmp_path / "out"

    specs = cli._build_specs([d], out)

    assert len(specs) == 1
    assert specs[0].source_dir == d
    assert specs[0].output_h5 == out / "tiff_src.h5"


def test_mixed_file_and_dir_preserve_order(tmp_path: Path) -> None:
    """Explicit .h5 file args and directory globs both resolve; directory
    globs sort alphabetically within their argument position."""
    explicit = _touch(tmp_path / "explicit.h5")
    d = tmp_path / "datasets"
    d.mkdir()
    _touch(d / "b_second.h5")
    _touch(d / "a_first.h5")

    specs = cli._build_specs([explicit, d], None)

    assert [s.source_dir for s in specs] == [
        explicit,
        d / "a_first.h5",
        d / "b_second.h5",
    ]


# ── --device flag ────────────────────────────────────────────────────
#
# The device reaches Cellpose through batch_process_datasets ->
# SegmentCells.run_inference -> the Segmenter port. These assert the
# argument plumbing without running Cellpose.


def _intercept_run():
    """Capture the kwargs the CLI hands to the batch runner, then stop.

    Returns ``(captured, fake)``; patch ``fake`` over
    ``batch_process_datasets`` and read ``captured["kwargs"]`` afterwards.
    Raising SystemExit keeps the CLI from touching the empty .h5 files these
    tests use as path stand-ins.
    """
    captured: dict = {}

    def _fake_batch(specs, **kwargs):  # noqa: ARG001 - specs unused by design
        captured["kwargs"] = kwargs
        raise SystemExit(0)

    return captured, _fake_batch


def test_device_flag_defaults_to_none(tmp_path: Path, monkeypatch) -> None:
    """Unset means 'use whatever the Advanced panel stored', not 'cpu'.
    Defaulting to a concrete device here would silently override the
    stored setting for every headless run."""
    captured, fake = _intercept_run()
    monkeypatch.setattr(cli, "batch_process_datasets", fake)
    h5 = _touch(tmp_path / "a.h5")

    try:
        cli.main([str(h5)])
    except SystemExit:
        pass

    assert captured["kwargs"]["device"] is None


def test_device_flag_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    captured, fake = _intercept_run()
    monkeypatch.setattr(cli, "batch_process_datasets", fake)
    h5 = _touch(tmp_path / "a.h5")

    try:
        cli.main([str(h5), "--device", "cuda:1"])
    except SystemExit:
        pass

    assert captured["kwargs"]["device"] == "cuda:1"


def test_device_is_not_written_into_the_run_recipe(tmp_path: Path, monkeypatch) -> None:
    """The device is a property of the machine, not the experiment. Putting
    it in CellposeSettings would serialize one researcher's cuda:1 into
    run_config.json and carry it onto a colleague's single-GPU box."""
    from percell4.workflows.models import CellposeSettings

    captured, fake = _intercept_run()
    monkeypatch.setattr(cli, "batch_process_datasets", fake)
    h5 = _touch(tmp_path / "a.h5")

    try:
        cli.main([str(h5), "--device", "cuda:1"])
    except SystemExit:
        pass

    settings = captured["kwargs"]["settings"]
    assert isinstance(settings, CellposeSettings)
    assert not hasattr(settings, "device")
    assert "cuda:1" not in repr(settings)


def test_resolution_is_reported_on_stderr(tmp_path: Path, monkeypatch, capsys) -> None:
    """A fallback must be visible in a headless log. stderr, not stdout, so
    the run's parseable output is unchanged."""
    from percell4.adapters import torch_device

    monkeypatch.setattr(torch_device, "_probe_device", lambda spec: f"no {spec}")
    captured, fake = _intercept_run()
    monkeypatch.setattr(cli, "batch_process_datasets", fake)
    h5 = _touch(tmp_path / "a.h5")

    try:
        cli.main([str(h5), "--gpu"])
    except SystemExit:
        pass

    streams = capsys.readouterr()
    assert "CPU" in streams.err or "cpu" in streams.err
    assert "cpu" not in streams.out.lower()


def test_device_flag_appears_in_help(capsys) -> None:
    import pytest as _pytest

    with _pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "--device" in capsys.readouterr().out
