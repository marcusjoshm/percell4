"""CLI adapter: rename one (kind, old_name) → new_name across .h5 datasets.

Headless front-end for
:func:`percell4.application.use_cases.batch_rename_resource.batch_rename_resource`.

Usage:
    python -m percell4.interfaces.cli.batch_rename_resource dish_1.h5 dish_2.h5 \\
        --kind channel --from-name mScar --to-name mScarlet
    python -m percell4.interfaces.cli.batch_rename_resource /scratch/dishes/ \\
        --kind segmentation --from-name cellpose_qc --to-name cp_mask --dry-run

For each input .h5 (or every .h5 in a directory argument), the CLI
applies one rename. Datasets that don't have the source name are
reported as skipped; datasets where the target name already exists
are reported as per-dataset errors and the batch continues. ``--dry-run``
classifies exactly as a live run would but does not mutate any file.

Exit codes:
    0 -- at least one dataset was successfully renamed
    1 -- every dataset was skipped or failed (no progress made)

Programmatic use:
    from percell4.interfaces.cli.batch_rename_resource import main
    exit_code = main([
        "dish_1.h5", "--kind", "mask",
        "--from-name", "thresh_old", "--to-name", "thresh_new",
    ])
"""

from __future__ import annotations

import argparse
import logging
import sys

from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    VALID_KINDS,
    batch_rename_resource,
)
from percell4.interfaces.cli._batch_report import (
    print_item_status,
    resolve_paths,
)

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch-rename",
        description=(
            "Batch-rename a single channel, mask, or segmentation "
            "across one or more .h5 datasets.\n\n"
            "For each input .h5, renames (kind, old_name) → new_name "
            "in that file. Datasets that don't have the source name "
            "are reported as skipped, not as failures. Datasets where "
            "the target name already exists are reported as per-dataset "
            "errors; the batch continues to the next file.\n\n"
            "Channel renames go through DatasetStore.rename_channel, "
            "which moves /decay/<name>, /phasor/<name>, and updates "
            "/metadata.channel_names plus the per-channel FLIM "
            "calibration attrs together. Masks and segmentations go "
            "through DatasetStore.rename_item against /masks/<name> "
            "and /labels/<name> respectively.\n\n"
            "Recommendation: close any open PerCell4 GUI session "
            "against the target files before running. The batch CLI "
            "writes to the same .h5 files the GUI reads."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-rename dish_1.h5 dish_2.h5 \\\n"
            "      --kind channel --from-name mScar --to-name mScarlet\n"
            "  percell4-batch-rename /scratch/dishes/ \\\n"
            "      --kind mask --from-name thresh_old --to-name thresh_new\n"
            "  percell4-batch-rename *.h5 \\\n"
            "      --kind segmentation --from-name cellpose_qc \\\n"
            "      --to-name cp_mask --dry-run\n"
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
        "--kind",
        choices=list(VALID_KINDS),
        required=True,
        help="Resource kind to rename.",
    )
    parser.add_argument(
        "--from-name",
        required=True,
        help="Current name of the resource in each .h5.",
    )
    parser.add_argument(
        "--to-name",
        required=True,
        help="New name to rename the resource to.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Classify each dataset as succeeded / skipped / failed "
            "exactly as a live run would, but do not mutate any file."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-resource skip / error detail lines. The "
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

    paths = resolve_paths(args.paths)
    if not paths:
        print(
            "error: no .h5 files matched the given paths", file=sys.stderr,
        )
        return 1

    def cb(item: BatchOperationItemResult) -> None:
        print_item_status(item, quiet=args.quiet, verb="renamed")

    report = batch_rename_resource(
        paths,
        kind=args.kind,
        old_name=args.from_name,
        new_name=args.to_name,
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
