"""CLI adapter: delete one (kind, name) across .h5 datasets.

Headless front-end for
:func:`percell4.application.use_cases.batch_delete_resource.batch_delete_resource`.

Usage:
    python -m percell4.interfaces.cli.batch_delete_resource dish_1.h5 dish_2.h5 \\
        --kind segmentation --name cellpose_qc
    python -m percell4.interfaces.cli.batch_delete_resource /scratch/dishes/ \\
        --kind mask --name thresh_488 --dry-run

For each input .h5 (or every .h5 in a directory argument), the CLI
deletes one ``(kind, name)`` pair. Datasets that don't have the
resource are reported as skipped, not as failures. ``--dry-run``
classifies exactly as a live run would but does not mutate any file.

Channels go through DatasetStore.delete_channel, which removes
/decay/<name>, /phasor/<name>, the channel_names metadata entry, and
the per-channel FLIM calibration attrs together. Masks and
segmentations go through DatasetStore.delete_item against
/masks/<name> and /labels/<name> respectively.

Exit codes:
    0 -- at least one dataset was successfully deleted
    1 -- every dataset was skipped or failed (no progress made)

Programmatic use:
    from percell4.interfaces.cli.batch_delete_resource import main
    exit_code = main([
        "dish_1.h5", "--kind", "mask", "--name", "thresh_488",
    ])
"""

from __future__ import annotations

import argparse
import logging
import sys

from percell4.application.use_cases.batch_delete_resource import (
    batch_delete_resource,
)
from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    VALID_KINDS,
)
from percell4.interfaces.cli._batch_report import (
    print_item_status,
    resolve_paths,
)

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch-delete",
        description=(
            "Batch-delete a single channel, mask, or segmentation "
            "across one or more .h5 datasets.\n\n"
            "For each input .h5, deletes (kind, name) in that file. "
            "Datasets that don't have the resource are reported as "
            "skipped, not as failures.\n\n"
            "Channel deletes go through DatasetStore.delete_channel, "
            "which removes /decay/<name>, /phasor/<name>, the "
            "channel_names metadata entry, and the per-channel FLIM "
            "calibration attrs together. Masks and segmentations go "
            "through DatasetStore.delete_item against /masks/<name> "
            "and /labels/<name>.\n\n"
            "Recommendation: close any open PerCell4 GUI session "
            "against the target files before running. The batch CLI "
            "writes to the same .h5 files the GUI reads. Use "
            "--dry-run first on destructive operations to audit what "
            "would change."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-delete dish_1.h5 dish_2.h5 \\\n"
            "      --kind segmentation --name cellpose_qc\n"
            "  percell4-batch-delete /scratch/dishes/ \\\n"
            "      --kind mask --name thresh_488 --dry-run\n"
            "  percell4-batch-delete *.h5 \\\n"
            "      --kind channel --name DAPI --quiet\n"
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
        help="Resource kind to delete.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name of the resource to delete in each .h5.",
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
        print_item_status(item, quiet=args.quiet, verb="deleted")

    report = batch_delete_resource(
        paths,
        kind=args.kind,
        name=args.name,
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
