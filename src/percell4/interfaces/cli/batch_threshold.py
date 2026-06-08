"""CLI: headless grouped thresholding across datasets (masks only).

Computes one grouped-threshold round per invocation and writes
``/masks/<round>`` + ``/groups/<round>`` back into each .h5. Requires each
dataset to already carry a segmentation (``/labels/<name>``); this tool
does not segment. It does not measure or export — run
``percell4-batch-measure`` afterwards for CSVs.

Usage:
    percell4-batch-threshold dish_1.h5 dish_2.h5 --channel GFP \\
        --round-name GFP_bright --algorithm kmeans --kmeans-n-clusters 3
    percell4-batch-threshold /scratch/dishes/ --channel RFP \\
        --round-name RFP_pos --algorithm gmm --gmm-criterion bic --overwrite

Exit codes:
    0 -- at least one dataset got a mask written
    1 -- every dataset was skipped or failed

Programmatic use:
    from percell4.interfaces.cli.batch_threshold import main
    exit_code = main(["dish_1.h5", "--channel", "GFP", "--round-name", "r1"])
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from percell4.interfaces.cli._batch_report import resolve_paths

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="percell4-batch-threshold",
        description=(
            "Run one grouped-threshold round across datasets, writing "
            "/masks/<round> + /groups/<round> into each .h5. Requires existing "
            "/labels. Does not measure or export — use percell4-batch-measure next."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "datasets",
        nargs="+",
        help="One or more .h5 files, or directories (every *.h5 within, non-recursive).",
    )
    grp = parser.add_argument_group("Thresholding round")
    grp.add_argument("--round-name", required=True, help="Mask/group name to write (the round name).")
    grp.add_argument("--channel", required=True, help="Channel name to threshold.")
    grp.add_argument(
        "--metric",
        default="mean_intensity",
        help="Per-cell metric used to group cells before thresholding (default mean_intensity).",
    )
    grp.add_argument(
        "--algorithm",
        choices=("gmm", "kmeans"),
        default="kmeans",
        help="Grouping algorithm (default kmeans).",
    )
    grp.add_argument(
        "--gmm-criterion",
        choices=("bic", "silhouette"),
        default="bic",
        help="GMM component-count criterion (default bic).",
    )
    grp.add_argument("--gmm-max-components", type=int, default=4, help="Max GMM components (default 4).")
    grp.add_argument("--kmeans-n-clusters", type=int, default=3, help="K-means cluster count (default 3).")
    grp.add_argument("--gaussian-sigma", type=float, default=1.0, help="Pre-threshold Gaussian sigma (default 1.0).")
    parser.add_argument(
        "--segmentation",
        default=None,
        help="Existing /labels layer to group against. Auto-picked per dataset if omitted.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing /masks/<round> instead of erroring.",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging.")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    # Defer heavy imports so --help stays fast and Qt-free.
    from percell4.store import DatasetStore
    from percell4.workflows.models import (
        GmmCriterion,
        ThresholdAlgorithm,
        ThresholdingRound,
    )
    from percell4.workflows.phases import (
        apply_threshold_headless,
        pick_existing_segmentation,
        threshold_compute_one,
    )

    try:
        round_spec = ThresholdingRound(
            name=args.round_name,
            channel=args.channel,
            metric=args.metric,
            algorithm=ThresholdAlgorithm(args.algorithm),
            gmm_criterion=GmmCriterion(args.gmm_criterion),
            gmm_max_components=args.gmm_max_components,
            kmeans_n_clusters=args.kmeans_n_clusters,
            gaussian_sigma=args.gaussian_sigma,
        )
    except ValueError as e:
        print(f"[error] invalid round configuration: {e}", file=sys.stderr)
        return 1

    paths = resolve_paths(args.datasets)
    if not paths:
        print("No .h5 datasets found in the given paths.", file=sys.stderr)
        return 1

    n_ok = 0
    n_total = len(paths)
    written_for: list[str] = []
    for idx, path in enumerate(paths):
        name = path.name
        try:
            store = DatasetStore(path)
            labels = store.list_labels()
        except Exception as e:
            print(f"[{idx + 1}/{n_total}] [error] {name}: cannot open: {e}", file=sys.stderr)
            continue

        seg = args.segmentation or pick_existing_segmentation(labels)
        if seg is None or seg not in labels:
            print(
                f"[{idx + 1}/{n_total}] [error] {name}: no usable segmentation "
                f"(need /labels{' /' + args.segmentation if args.segmentation else ''}); skipping.",
                file=sys.stderr,
            )
            continue

        if store.array_exists(f"masks/{args.round_name}") and not args.overwrite:
            print(
                f"[{idx + 1}/{n_total}] [error] {name}: /masks/{args.round_name} already "
                f"exists; pass --overwrite to replace it. Skipping.",
                file=sys.stderr,
            )
            continue

        grouping, failure, msg = threshold_compute_one(store, round_spec, seg_name=seg)
        if failure is not None or grouping is None:
            print(f"[{idx + 1}/{n_total}] [error] {name}: grouping failed: {msg}", file=sys.stderr)
            continue
        failure, msg = apply_threshold_headless(store, round_spec, grouping, seg_name=seg)
        if failure is not None:
            print(f"[{idx + 1}/{n_total}] [error] {name}: threshold apply failed: {msg}", file=sys.stderr)
            continue
        n_ok += 1
        written_for.append(name)
        print(f"[{idx + 1}/{n_total}] [ok] {name}: wrote /masks/{args.round_name}")

    print(f"\n{n_ok}/{n_total} datasets thresholded.")
    if n_ok:
        ds_args = " ".join(str(p) for p in args.datasets)
        print(
            "Next: measure + export with\n"
            f"  percell4-batch-measure {ds_args} "
            f"--segmentation {args.segmentation or '<seg>'} --mask {args.round_name} --output <dir>"
        )
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
