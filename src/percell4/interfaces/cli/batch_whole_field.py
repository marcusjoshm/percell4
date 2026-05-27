"""CLI adapter: create a whole-field segmentation across .h5 datasets.

Headless front-end for
:func:`percell4.application.use_cases.batch_create_whole_field_segmentation.batch_create_whole_field_segmentation`.

Usage:
    python -m percell4.interfaces.cli.batch_whole_field dish_1.h5 dish_2.h5
    python -m percell4.interfaces.cli.batch_whole_field /scratch/dishes/ --dry-run

For each input .h5 (or every .h5 in a directory argument), the CLI
writes ``/labels/whole_field`` — a 2D int32 array shaped to the
dataset's native (H, W) with every pixel = 1. Useful as a baseline
gating layer for whole-field measurements or as a default
segmentation before per-cell Cellpose runs.

Pre-existing ``/labels/whole_field`` is silently overwritten. The
shape is inferred from ``/metadata.native_shape`` when present;
otherwise from the first ``/decay/<channel>`` group. An empty
dataset with neither metadata nor decay can't be shaped and is
reported as failed. ``--dry-run`` classifies exactly as a live run
would but does not mutate any file.

Recommendation: close any open PerCell4 GUI session against the
target files before running. The batch CLI writes to the same .h5
files the GUI reads.

Exit codes:
    0 -- at least one dataset got a whole_field segmentation
    1 -- every dataset failed (no progress made)
"""

from __future__ import annotations

import argparse
import logging
import sys

from percell4.application.use_cases.batch_create_whole_field_segmentation import (
    batch_create_whole_field_segmentation,
)
from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
)
from percell4.interfaces.cli._batch_report import (
    print_item_status,
    resolve_paths,
)

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="percell4-batch-whole-field",
        description=(
            "Create /labels/whole_field (every pixel = 1) in each input "
            ".h5 dataset."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-whole-field dish_1.h5 dish_2.h5\n"
            "  percell4-batch-whole-field /scratch/dishes/ --dry-run\n"
            "\n"
            "Notes:\n"
            "  - Shape is taken from /metadata.native_shape, falling\n"
            "    back to the first /decay/<channel> if absent.\n"
            "  - Pre-existing /labels/whole_field is silently\n"
            "    overwritten.\n"
            "  - Close any open PerCell4 GUI session against the target\n"
            "    files before running.\n"
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
        "--dry-run",
        action="store_true",
        help=(
            "Classify each dataset as succeeded / failed exactly as a "
            "live run would, but do not mutate any file."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-resource detail lines. The per-dataset summary "
            "line and final totals always print."
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

    paths = resolve_paths(args.paths)
    if not paths:
        print(
            "error: no .h5 files matched the given paths", file=sys.stderr,
        )
        return 1

    def cb(item: BatchOperationItemResult) -> None:
        print_item_status(item, quiet=args.quiet, verb="created")

    report = batch_create_whole_field_segmentation(
        paths,
        dry_run=args.dry_run,
        progress_callback=cb,
    )

    print(
        f"\nTotals: {report.total_succeeded} succeeded, "
        f"{report.total_failed} failed, "
        f"{report.total_skipped} skipped"
    )

    any_progress = any(item.processed for item in report.items)
    return 0 if any_progress else 1


if __name__ == "__main__":
    sys.exit(main())
