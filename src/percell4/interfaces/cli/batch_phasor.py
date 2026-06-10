"""CLI adapter: batch compute phasor + apply wavelet across .h5 datasets.

Headless front-end for
:func:`percell4.application.use_cases.batch_compute_phasor` (and its
inverse :func:`batch_remove_phasor`).

Usage:
    python -m percell4.interfaces.cli.batch_phasor dish_1.h5 dish_2.h5
    python -m percell4.interfaces.cli.batch_phasor /path/to/scratch/ \\
        --filter-level 5 --overwrite
    python -m percell4.interfaces.cli.batch_phasor /scratch/ --remove

For each .h5 file (or every .h5 in a directory argument), the CLI runs
ComputePhasor + ApplyWavelet on every channel under /decay/*, writing
/phasor/<ch>/g, /s, g_filtered, s_filtered, and lifetime_filtered.
Channels with an existing /phasor/<ch>/g are skipped unless --overwrite
is set. Channels missing calibration (flim_cal_phase_<ch>,
flim_cal_mod_<ch>, or flim_frequency_mhz) are skipped with a clear
report line.

The ``--remove`` flag flips the CLI into deletion mode: for each .h5,
the entire ``/phasor/<ch>/`` group is removed for every channel under
``/decay/*`` (sweeping g, s, g_filtered, s_filtered, lifetime_filtered
together). Channels that have no phasor on disk are reported as
skipped, not as failures. ``--remove`` is mutually exclusive with
``--overwrite``.

Exit codes:
    0 -- at least one channel was processed across the batch
    1 -- every dataset failed or was skipped (no progress made)

Programmatic use:
    from percell4.interfaces.cli.batch_phasor import main
    exit_code = main(["dish_1.h5", "--filter-level", "5"])
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import percell4._compat  # noqa: F401 — NumPy 2.0 shims for dtcwt

from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
    batch_compute_phasor,
    batch_remove_phasor,
)
from percell4.domain.flim.wavelet_filter import MAX_FILTER_LEVEL

logger = logging.getLogger(__name__)


def _resolve_paths(args: list[str]) -> list[Path]:
    """Expand positional arguments into a flat list of .h5 file paths.

    Each argument can be either an .h5 file path or a directory; if a
    directory, every ``*.h5`` immediately under it is included
    (non-recursive). Order is preserved: file args land in argv order,
    directory globs sort alphabetically.
    """
    paths: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.h5")))
        else:
            paths.append(p)
    return paths


def _format_item_line(
    item: BatchPhasorItemResult, *, mode: str = "compute"
) -> str:
    """One-line summary for a single dataset result.

    ``mode`` swaps the verb in the count summary so removal runs read as
    "N removed" instead of "N processed".
    """
    n_processed = len(item.processed)
    n_skipped = len(item.skipped)
    n_errors = len(item.errors)
    verb = "removed" if mode == "remove" else "processed"
    return (
        f"[{item.status}] {item.h5_path.name} -- "
        f"{n_processed} {verb}, {n_skipped} skipped, {n_errors} errors"
    )


def _print_item_status(
    item: BatchPhasorItemResult,
    *,
    quiet: bool = False,
    mode: str = "compute",
) -> None:
    """Print one dataset's result to stdout."""
    print(_format_item_line(item, mode=mode))
    if quiet:
        return
    # Indent per-channel skip + error details under the dataset header.
    if item.error:
        print(f"    error: {item.error}")
    for channel, reason in item.skipped.items():
        print(f"    {channel} skipped: {reason}")
    for channel, msg in item.errors.items():
        print(f"    {channel} error: {msg}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch-phasor",
        description=(
            "Batch-compute phasor and apply wavelet across one or more "
            ".h5 datasets.\n\n"
            "For each input .h5, processes every channel under "
            "/decay/*: writes /phasor/<ch>/g, /s, g_filtered, "
            "s_filtered, lifetime_filtered. Channels with an existing "
            "/phasor/<ch>/g are skipped unless --overwrite is set. "
            "Channels missing calibration "
            "(flim_cal_phase_<ch>, flim_cal_mod_<ch>, "
            "flim_frequency_mhz) are skipped with a clear report line."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-phasor dish_1.h5 dish_2.h5\n"
            "  percell4-batch-phasor /scratch/dishes/ --filter-level 5\n"
            "  percell4-batch-phasor *.h5 --overwrite --quiet\n"
            "  percell4-batch-phasor /scratch/dishes/ --remove\n"
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
        "--filter-level",
        type=int,
        default=9,
        help=f"Wavelet filter level (1..{MAX_FILTER_LEVEL}, default 9).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Recompute channels even when /phasor/<ch>/g already exists. "
            "Default: skip channels with existing phasor."
        ),
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help=(
            "Inverse mode: delete /phasor/<ch>/ (all of g, s, g_filtered, "
            "s_filtered, lifetime_filtered) for every channel in each "
            "dataset instead of computing. Mutually exclusive with "
            "--overwrite. --filter-level is ignored when --remove is set."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-channel skip / error detail lines. The "
            "per-dataset summary line and final totals always print."
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

    if args.remove and args.overwrite:
        parser.error("--remove and --overwrite are mutually exclusive")

    if not args.remove and not (1 <= args.filter_level <= MAX_FILTER_LEVEL):
        # --filter-level is irrelevant for --remove, so only validate it
        # for the compute path.
        parser.error(
            f"--filter-level must be in [1, {MAX_FILTER_LEVEL}], "
            f"got {args.filter_level}"
        )

    paths = _resolve_paths(args.paths)
    if not paths:
        print(
            "error: no .h5 files matched the given paths", file=sys.stderr,
        )
        return 1

    mode = "remove" if args.remove else "compute"

    def cb(item: BatchPhasorItemResult) -> None:
        _print_item_status(item, quiet=args.quiet, mode=mode)

    if args.remove:
        report = batch_remove_phasor(paths, progress_callback=cb)
    else:
        report = batch_compute_phasor(
            paths,
            filter_level=args.filter_level,
            overwrite=args.overwrite,
            progress_callback=cb,
        )

    print(
        f"\nTotals: {report.total_succeeded} succeeded, "
        f"{report.total_partial} partial, "
        f"{report.total_failed} failed, "
        f"{report.total_skipped} skipped"
    )

    # Exit 0 if any progress was made (some channel processed/removed
    # somewhere), else 1.
    any_progress = any(item.processed for item in report.items)
    return 0 if any_progress else 1


if __name__ == "__main__":
    sys.exit(main())
