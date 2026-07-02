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
