"""CLI adapter: race puncta-detection methods against ground truth and lock one.

Headless front-end for the dev-time validation harness
:mod:`percell4.workflows.puncta_validation` (plan U5).

Usage:
    percell4-batch-validate-puncta DS1.h5 --gt-dir labels/ --channel GFP
    percell4-batch-validate-puncta DS1.h5 --gt-dir labels/ --channel GFP \\
        --tier-b-mask old_qc --detectors log dog --k 2.0 2.5 3.0 --out locked.json

Inputs:
  - one dataset ``.h5`` whose ``/labels/cellpose_qc`` + ``/intensity`` feed the
    detection path, and
  - a directory of Tier-A napari-point CSVs (``y, x`` columns, optional
    ``field``) — the exhaustive recall ceiling and the only source of precision.

Optionally a Tier-B approved ``/masks/<name>`` supplies a recall floor (scored at
the same tolerance as the candidates).

The CLI prints the ranked table and the lock decision, and (when a method
qualifies) writes the locked ``PunctaDetectorSettings`` as JSON.

Exit codes:
    0 -- a method qualified and was locked
    1 -- no method cleared the bar (keep interactive QC) or a load error
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _settings_to_dict(settings) -> dict:
    """Serialize the locked PunctaDetectorSettings to a JSON-friendly dict."""
    return {
        "detector_name": settings.detector_name,
        "seed_detector_name": settings.seed_detector_name,
        "background_estimator_name": settings.background_estimator_name,
        "detector_params": dict(settings.detector_params),
        "seed_params": dict(settings.seed_params),
        "min_spot_px": settings.min_spot_px,
        "max_spot_px": settings.max_spot_px,
        "spot_scale_prior": (
            list(settings.spot_scale_prior)
            if settings.spot_scale_prior is not None
            else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="percell4-batch-validate-puncta",
        description=(
            "Race puncta-detection methods against hybrid ground truth and "
            "lock a qualifying winner."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-validate-puncta DS1.h5 --gt-dir labels/ "
            "--channel GFP\n"
            "  percell4-batch-validate-puncta DS1.h5 --gt-dir labels/ "
            "--channel GFP --tier-b-mask old_qc --out locked.json\n"
        ),
    )
    parser.add_argument("dataset", help="Path to the dataset .h5 file.")
    parser.add_argument(
        "--gt-dir",
        required=True,
        help="Directory of Tier-A napari-point CSVs (y,x columns).",
    )
    parser.add_argument(
        "--channel",
        required=True,
        help="Channel name to detect on (must exist in /metadata.channel_names).",
    )
    parser.add_argument(
        "--seg-name",
        default="cellpose_qc",
        help="Segmentation label set name (default: cellpose_qc).",
    )
    parser.add_argument(
        "--field-name",
        default=None,
        help=(
            "Tier-A field name this dataset corresponds to "
            "(default: the dataset file stem)."
        ),
    )
    parser.add_argument(
        "--tier-b-mask",
        default=None,
        help="Existing approved /masks/<name> to use as the recall floor.",
    )
    parser.add_argument(
        "--detectors",
        nargs="+",
        default=["log"],
        help="Detector names to sweep (default: log).",
    )
    parser.add_argument(
        "--backgrounds",
        nargs="+",
        default=["gaussian-peak"],
        help="Background estimator names to sweep (default: gaussian-peak).",
    )
    parser.add_argument(
        "--k",
        nargs="+",
        type=float,
        default=[2.5],
        help="k (sigma multiplier) values to sweep (default: 2.5).",
    )
    parser.add_argument(
        "--tol",
        nargs="+",
        type=float,
        default=[4.0],
        help="Matching tolerance (px) values to sweep (default: 4.0).",
    )
    parser.add_argument(
        "--scale-min",
        type=float,
        default=1.0,
        help="min_sigma of the scale range (default: 1.0).",
    )
    parser.add_argument(
        "--scale-max",
        type=float,
        default=4.0,
        help="max_sigma of the scale range (default: 4.0).",
    )
    parser.add_argument(
        "--min-spot-px",
        type=int,
        default=2,
        help="Minimum spot area in pixels (default: 2).",
    )
    parser.add_argument(
        "--precision-floor",
        type=float,
        default=0.9,
        help="Minimum Tier-A precision to lock (default: 0.9).",
    )
    parser.add_argument(
        "--beta",
        type=float,
        default=2.0,
        help="F-beta beta (recall weight; default: 2.0).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the locked PunctaDetectorSettings JSON here when locked.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable DEBUG logging."
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Imports deferred so --help stays fast and Qt-free import order is obvious.
    from percell4.store import DatasetStore
    from percell4.workflows.models import ThresholdAlgorithm, ThresholdingRound
    from percell4.workflows.puncta_validation import (
        GridPoint,
        load_fields_from_store,
        load_tier_a_csv,
        load_tier_b_recall,
        run_validation,
    )

    gt_dir = Path(args.gt_dir)
    csv_paths = sorted(gt_dir.glob("*.csv"))
    if not csv_paths:
        print(f"error: no *.csv found in {gt_dir}", file=sys.stderr)
        return 1
    gt_by_field = load_tier_a_csv(csv_paths)

    store = DatasetStore(args.dataset)
    field_name = args.field_name or Path(args.dataset).stem

    # A round just to drive grouping + the channel reads (puncta added per grid).
    base_round = ThresholdingRound(
        name="validate",
        channel=args.channel,
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=1.0,
    )
    try:
        fields = load_fields_from_store(
            store, base_round, field_name=field_name, seg_name=args.seg_name
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tier_b_recall = None
    if args.tier_b_mask:
        tol0 = args.tol[0]
        tier_b_recall = load_tier_b_recall(
            store, args.tier_b_mask, gt_by_field, tol0, field_name=field_name
        )
        print(f"Tier-B recall floor ({args.tier_b_mask} @ tol={tol0}): {tier_b_recall:.3f}")

    grid = [
        GridPoint(
            detector_name=det,
            background_estimator_name=bg,
            k=k,
            tol=tol,
            scale_range=(args.scale_min, args.scale_max),
            min_spot_px=args.min_spot_px,
        )
        for det, bg, k, tol in itertools.product(
            args.detectors, args.backgrounds, args.k, args.tol
        )
    ]

    report = run_validation(
        fields,
        gt_by_field,
        grid,
        tier_b_recall=tier_b_recall,
        beta=args.beta,
        precision_floor=args.precision_floor,
    )

    # Ranked table.
    print(
        f"\n{'detector':<14}{'background':<16}{'k':>5}{'tol':>6}"
        f"{'recall':>9}{'precision':>11}{'f_beta':>9}{'stable':>8}"
    )
    print("-" * 78)
    for row in report.ranked:
        print(
            f"{row['detector_name']:<14}{row['background_estimator_name']:<16}"
            f"{row['k']:>5.2f}{row['tol']:>6.1f}"
            f"{row['recall']:>9.3f}{row['precision']:>11.3f}"
            f"{row['f_beta']:>9.3f}{str(row['stable']):>8}"
        )

    print(f"\nDecision: {report.reason}")

    if report.locked_settings is not None:
        if args.out:
            out_path = Path(args.out)
            out_path.write_text(
                json.dumps(_settings_to_dict(report.locked_settings), indent=2),
                encoding="utf-8",
            )
            print(f"Locked settings written to {out_path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
