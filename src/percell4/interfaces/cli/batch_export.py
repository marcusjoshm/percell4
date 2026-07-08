"""CLI adapter: batch export of dataset layers as TIFF files.

Headless front-end for
:func:`percell4.application.use_cases.batch_export_images`.

Usage:
    python -m percell4.interfaces.cli.batch_export dish_1.h5 dish_2.h5 \\
        --output-dir /tmp/exports
    python -m percell4.interfaces.cli.batch_export /scratch/dishes/ \\
        --output-dir ~/exports/2026-05-18 --quiet

For each .h5 file (or every .h5 in a directory argument), writes
``<output-dir>/<h5_stem>_<layer>.tif`` for every intensity channel,
segmentation label, and mask in the dataset. Existing files at those
paths are overwritten silently.

Exit codes:
    0 -- at least one TIFF was written across the batch
    1 -- no files were written (every dataset failed or was skipped)

Programmatic use:
    from percell4.interfaces.cli.batch_export import main
    exit_code = main(["dish_1.h5", "--output-dir", "/tmp/exports"])
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import percell4._compat  # noqa: F401 — NumPy 2.0 shims
from percell4.application.use_cases.batch_export_images import (
    BatchExportItemResult,
    batch_export_images,
)

logger = logging.getLogger(__name__)


def _positive_int(raw: str) -> int:
    """argparse type=... validator for --view-bin. Rejects N < 1."""
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"expected an integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"must be >= 1, got {value}"
        )
    return value


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


def _format_item_line(item: BatchExportItemResult) -> str:
    """One-line summary for a single dataset result."""
    return (
        f"[{item.status}] {item.h5_path.name} -- "
        f"{item.files_written} files"
    )


def _print_item_status(
    item: BatchExportItemResult, *, quiet: bool = False
) -> None:
    """Print one dataset's result to stdout."""
    print(_format_item_line(item))
    if quiet:
        return
    if item.error:
        print(f"    error: {item.error}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch-export",
        description=(
            "Batch-export dataset layers as TIFF files across one or "
            "more .h5 datasets.\n\n"
            "For each input .h5, writes one TIFF per intensity channel, "
            "per /labels/<name>, and per /masks/<name> into the target "
            "directory. Filenames follow the pattern "
            "<h5_stem>_<layer>.tif and use a flat layout (no per-dataset "
            "subfolders). Existing files at those paths are overwritten "
            "silently -- point --output-dir at a fresh directory if you "
            "want to preserve prior runs.\n\n"
            "Out of scope for this CLI: phasor, lifetime, and decay "
            "arrays are NOT exported. Use the phasor-npz export or a "
            "future iteration for those."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-export dish_1.h5 dish_2.h5 --output-dir /tmp/exports\n"
            "  percell4-batch-export /scratch/dishes/ --output-dir ~/exports/\n"
            "  percell4-batch-export *.h5 --output-dir out/ --quiet\n"
            "  percell4-batch-export *.h5 --output-dir out/ --view-bin 4\n"
            "\n"
            "View-bin: --view-bin N applies the same sum/majority-vote\n"
            "downsampling the GUI uses for view_bin=N, producing TIFFs\n"
            "at the binned resolution. Default 1 (native). Filenames\n"
            "are unchanged regardless of bin -- track the value\n"
            "yourself if you mix outputs from different runs.\n"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help=(
            "One or more .h5 files, or directories containing .h5 files. "
            "Directories are globbed non-recursively (*.h5)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        required=True,
        help=(
            "Target directory for the .tif outputs. Created if missing. "
            "Existing files with matching names are overwritten."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-dataset error detail lines. Per-dataset "
            "status headers and final totals always print."
        ),
    )
    parser.add_argument(
        "--view-bin",
        type=_positive_int,
        default=1,
        metavar="N",
        help=(
            "Bin factor applied to every layer at read time. Default 1 "
            "(native resolution -- the established export contract). "
            "Values > 1 produce downsampled TIFFs using the same lens "
            "the GUI applies for view_bin=N (sum_bin_2d for intensity, "
            "mode_labels for /labels, majority_vote_mask for /masks). "
            "Output filenames are unchanged regardless of bin."
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
            "error: no .h5 files matched the given paths", file=sys.stderr,
        )
        return 1

    def cb(item: BatchExportItemResult) -> None:
        _print_item_status(item, quiet=args.quiet)

    report = batch_export_images(
        paths,
        output_dir=args.output_dir,
        view_bin=args.view_bin,
        progress_callback=cb,
    )

    print(
        f"\nTotals: {report.total_succeeded} succeeded, "
        f"{report.total_skipped} skipped, "
        f"{report.total_failed} failed "
        f"-- {report.total_files_written} files written"
    )

    return 0 if report.total_files_written > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
