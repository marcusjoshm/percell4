"""CLI adapter: batch export of cached phasors as PNG files.

Headless front-end for
:func:`percell4.application.use_cases.batch_export_phasor`.

Usage:
    python -m percell4.interfaces.cli.batch_export_phasor dish_1.h5 \\
        dish_2.h5 --output-dir /tmp/phasors
    python -m percell4.interfaces.cli.batch_export_phasor \\
        /scratch/dishes/ --output-dir ~/phasors/2026-05-18 --quiet

For each .h5 file (or every .h5 in a directory argument), writes
``<output-dir>/<h5_stem>_<ch>_phasor.png`` for every channel under
``/phasor/<ch>``, plus ``<h5_stem>_<ch>_phasor_filtered.png`` for every
channel that additionally has both ``g_filtered`` and ``s_filtered``.
Flat layout (no per-dataset subfolders). Existing files at those paths
are overwritten silently.

This CLI does NOT compute phasors. Channels with no ``/phasor/<ch>/g``
are reported as skipped -- run ``batch_phasor`` first.

Exit codes:
    0 -- at least one PNG was written across the batch
    1 -- no files were written (every dataset failed/empty), or the
         output directory is not writable

Programmatic use:
    from percell4.interfaces.cli.batch_export_phasor import main
    exit_code = main(["dish_1.h5", "--output-dir", "/tmp/phasors"])
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import percell4._compat  # noqa: F401 — NumPy 2.0 shims
from percell4.application.use_cases.batch_export_phasor import (
    BatchPhasorExportItemResult,
    batch_export_phasor,
)

logger = logging.getLogger(__name__)


def _resolve_paths(args: list[str]) -> list[Path]:
    """Expand positional arguments into a flat list of .h5 file paths.

    Each argument is either a file path or a directory; if a directory,
    every ``*.h5`` immediately under it is included (non-recursive,
    alphabetical). File args land in argv order.
    """
    paths: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.h5")))
        else:
            paths.append(p)
    return paths


def _check_output_dir_writable(output_dir: Path) -> str | None:
    """Create ``output_dir`` and verify it is writable.

    Returns an error message string on failure, or None on success.
    Environmental write failures (missing perms, read-only mount, full
    disk) are not per-channel data faults -- failing fast here avoids
    absorbing the same failure into every channel's error map.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".batch_export_phasor_write_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return f"output directory not writable: {output_dir} ({exc})"
    return None


def _format_item_line(item: BatchPhasorExportItemResult) -> str:
    """One-line summary for a single dataset result."""
    return (
        f"[{item.status}] {item.h5_path.name} -- "
        f"{item.files_written} files, "
        f"{len(item.skipped)} skipped, {len(item.errors)} errors, "
        f"{len(item.rendered_empty)} empty"
    )


def _print_item_status(
    item: BatchPhasorExportItemResult, *, quiet: bool = False
) -> None:
    """Print one dataset's result to stdout.

    Three indented per-channel categories print under the header
    (suppressed by ``--quiet``): errors, skips, and empty renders.
    """
    print(_format_item_line(item))
    if quiet:
        return
    if item.error:
        print(f"    error: {item.error}")
    for key, msg in item.errors.items():
        print(f"    {key} error: {msg}")
    for key, reason in item.skipped.items():
        print(f"    {key} skipped: {reason}")
    for name in item.rendered_empty:
        print(f"    {name} rendered empty (no valid phasor pixels)")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch-export-phasor",
        description=(
            "Batch-export cached phasors as PNG files across one or "
            "more .h5 datasets.\n\n"
            "For each input .h5, writes one raw PNG per channel under "
            "/phasor/<ch> (<h5_stem>_<ch>_phasor.png) and one filtered "
            "PNG per channel that also has g_filtered + s_filtered "
            "(<h5_stem>_<ch>_phasor_filtered.png). Each PNG mirrors the "
            "GUI phasor window: intensity-weighted 2D histogram, "
            "universal semicircle overlay, labeled G/S axes. Filenames "
            "use a flat layout (no per-dataset subfolders). Existing "
            "files at those paths are overwritten silently -- point "
            "--output-dir at a fresh directory to preserve prior "
            "runs.\n\n"
            "Out of scope: this CLI does NOT compute phasors. Channels "
            "with no /phasor/<ch>/g are reported as skipped -- run "
            "percell4-batch-phasor first."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-export-phasor dish_1.h5 dish_2.h5 "
            "--output-dir /tmp/phasors\n"
            "  percell4-batch-export-phasor /scratch/dishes/ "
            "--output-dir ~/phasors/\n"
            "  percell4-batch-export-phasor *.h5 --output-dir out/ "
            "--quiet\n"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help=(
            "One or more .h5 files, or directories containing .h5 "
            "files. Directories are globbed non-recursively (*.h5)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        required=True,
        help=(
            "Target directory for the .png outputs. Created if missing. "
            "Existing files with matching names are overwritten."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-channel error / skip / empty detail lines. "
            "Per-dataset status headers and final totals always print."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    paths = _resolve_paths(args.paths)
    if not paths:
        print(
            "error: no .h5 files matched the given paths",
            file=sys.stderr,
        )
        return 1

    # Fail fast on an unwritable output dir -- before processing any
    # dataset -- rather than absorbing the same environmental failure
    # into every channel's error map.
    probe_err = _check_output_dir_writable(args.output_dir)
    if probe_err is not None:
        print(f"error: {probe_err}", file=sys.stderr)
        return 1

    def cb(item: BatchPhasorExportItemResult) -> None:
        _print_item_status(item, quiet=args.quiet)

    report = batch_export_phasor(
        paths,
        output_dir=args.output_dir,
        progress_callback=cb,
    )

    print(
        f"\nTotals: {report.total_succeeded} succeeded, "
        f"{report.total_skipped} skipped, "
        f"{report.total_failed} failed "
        f"-- {report.total_files_written} files written, "
        f"rendered empty: {report.total_rendered_empty}"
    )

    return 0 if report.total_files_written > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
