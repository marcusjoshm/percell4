"""CLI adapter: set, append to, or clear the description on .h5 datasets.

Headless front-end for
:func:`percell4.application.use_cases.batch_set_description.batch_set_description`.

Usage:
    python -m percell4.interfaces.cli.batch_describe dish_1.h5 dish_2.h5 \\
        --set "HeLa p14, fixed 4% PFA 15min"
    python -m percell4.interfaces.cli.batch_describe /scratch/dishes/ \\
        --append "2h 10uM drug at 37C" --dry-run
    python -m percell4.interfaces.cli.batch_describe dish_3.h5 --clear

For each input .h5 (or every .h5 in a directory argument), the CLI applies
one description operation. Exactly one of ``--set`` / ``--append`` /
``--clear`` is required, so a run can never write without naming its verb.
``--clear`` on a dataset that has no description is reported as skipped;
an unreadable file is a per-dataset error and the batch continues.

Exit codes:
    0 -- at least one dataset was written
    1 -- every dataset was skipped or failed (no progress made)
    2 -- argparse / validation failure (no I/O performed)

Programmatic use:
    from percell4.interfaces.cli.batch_describe import main
    exit_code = main(["dish_1.h5", "--set", "HeLa p14"])
"""

from __future__ import annotations

import argparse
import logging
import sys

from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
)
from percell4.application.use_cases.batch_set_description import (
    batch_set_description,
)
from percell4.interfaces.cli._batch_report import (
    print_item_status,
    resolve_paths,
)

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch-describe",
        description=(
            "Set, append to, or clear the free-text experiment description "
            "on one or more .h5 datasets.\n\n"
            "The description is stored inside the .h5 itself, so it travels "
            "with the file. Read it back with percell4-inspect, or in the "
            "Data tab of the PerCell4 launcher.\n\n"
            "Exactly one verb is required. --set replaces whatever is there; "
            "--append adds the new text below the existing text, separated "
            "by a blank line; --clear removes the description. Setting or "
            "appending empty text clears instead of storing a blank "
            "placeholder.\n\n"
            "Datasets with no description to clear are reported as skipped, "
            "not as failures. Unreadable files are per-dataset errors; the "
            "batch continues to the next file.\n\n"
            "Recommendation: close any open PerCell4 GUI session against "
            "the target files before running. The batch CLI writes to the "
            "same .h5 files the GUI reads."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-describe dish_1.h5 dish_2.h5 \\\n"
            "      --set 'HeLa p14, fixed 4% PFA 15min'\n"
            "  percell4-batch-describe /scratch/experiment_7/ \\\n"
            "      --append '2h 10uM drug at 37C, 5% CO2'\n"
            "  percell4-batch-describe /scratch/experiment_7/ \\\n"
            "      --append 'shared prep notes' --dry-run\n"
            "  percell4-batch-describe dish_3.h5 --clear\n"
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
    verb_group = parser.add_mutually_exclusive_group(required=True)
    verb_group.add_argument(
        "--set",
        dest="set_text",
        metavar="TEXT",
        help="Replace each dataset's description with TEXT.",
    )
    verb_group.add_argument(
        "--append",
        dest="append_text",
        metavar="TEXT",
        help=(
            "Add TEXT below each dataset's existing description, "
            "separated by a blank line."
        ),
    )
    verb_group.add_argument(
        "--clear",
        action="store_true",
        help="Remove each dataset's description entirely.",
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
            "Suppress per-dataset detail lines. The per-dataset summary "
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

    if args.clear:
        verb, text = "clear", None
    elif args.set_text is not None:
        verb, text = "set", args.set_text
    else:
        verb, text = "append", args.append_text

    # Empty text is a clear, not a write -- so a run that would otherwise
    # store a blank placeholder is rejected before any file is opened.
    if verb != "clear" and not (text or "").strip():
        parser.error(f"--{verb} requires non-empty text (use --clear to remove)")

    paths = resolve_paths(args.paths)
    if not paths:
        print(
            "error: no .h5 files matched the given paths", file=sys.stderr,
        )
        return 1

    def cb(item: BatchOperationItemResult) -> None:
        print_item_status(item, quiet=args.quiet, verb="described")

    report = batch_set_description(
        paths,
        verb=verb,
        text=text,
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
