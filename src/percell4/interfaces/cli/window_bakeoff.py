"""CLI adapter: bake off auto-window-size finders against the SG-mask oracle.

Headless front-end for :mod:`percell4.workflows.window_bakeoff` (plan U7). For
each dataset that carries an ``/masks/SG_mask`` ground truth, the IoU-argmax
window over ``--window-grid`` is the target; every finder's ``auto_window`` is
scored by ``|auto - ideal|`` plus its own mask IoU/recall. ``k`` is pinned for
the whole run and recorded.

Usage:
    percell4-window-bakeoff DS.h5 --channel G3BP1 --k 3.0 \\
        --window-grid 15 31 51 71 91 111 131 --out report.json
    percell4-window-bakeoff A.h5 B.h5 --channel G3BP1 --cp-name cp_mask \\
        --finders otsu-mean granule-size --holdout B --c 4.5

Exit codes:
    0 -- at least one labeled field was scored
    1 -- no labeled field found (no SG_mask anywhere) or a load error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _report_to_dict(report) -> dict:
    return {
        "k": report.k,
        "window_grid": list(report.window_grid),
        "ranking": [{"method": m, "mean_window_error": err} for m, err in report.ranking],
        "missing_label_fields": report.missing_label_fields,
        "oracles": {
            name: {
                "ideal_window": o.ideal_window,
                "windows": list(o.windows),
                "iou_curve": [round(x, 4) for x in o.iou_curve],
                "recall_curve": [round(x, 4) for x in o.recall_curve],
            }
            for name, o in report.oracles.items()
        },
        "scores": [
            {
                "field": s.field,
                "method": s.method,
                "error": s.error,
                **(
                    {}
                    if s.score is None
                    else {
                        "auto_window": s.score.auto_window,
                        "ideal_window": s.score.ideal_window,
                        "window_error": s.score.window_error,
                        "iou": round(s.score.iou, 4),
                        "recall": round(s.score.recall, 4),
                        "in_sample": s.score.in_sample,
                    }
                ),
            }
            for s in report.scores
        ],
    }


def _print_table(report) -> None:
    print(f"\nWindow bake-off  (k={report.k}, grid={list(report.window_grid)})")
    if report.missing_label_fields:
        print(f"  no SG_mask (skipped): {', '.join(report.missing_label_fields)}")
    for name, o in report.oracles.items():
        flat = (max(o.iou_curve) - (sum(o.iou_curve) / len(o.iou_curve))) if o.iou_curve else 0.0
        note = "  [IoU peak flat — check boundary]" if o.iou_curve and flat < 0.02 else ""
        print(f"  oracle[{name}]: ideal_window={o.ideal_window}  max_iou={max(o.iou_curve):.3f}{note}")
    print("\n  rank  finder           mean|err|")
    for i, (m, err) in enumerate(report.ranking, 1):
        print(f"  {i:>4}  {m:<15}  {err:>8.1f}")
    in_sample_any = any(s.score is not None and s.score.in_sample for s in report.scores)
    if in_sample_any:
        print("\n  NOTE: in-sample scores (calibration field == scoring field) are NOT validation.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="percell4-window-bakeoff",
        description="Score auto-window-size finders against the SG-mask oracle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("datasets", nargs="+", help="Dataset .h5 file(s).")
    parser.add_argument("--channel", required=True, help="Channel name (e.g. G3BP1).")
    parser.add_argument(
        "--finders", nargs="+", default=None,
        help="Finder names to bake off (default: all registered).",
    )
    parser.add_argument(
        "--window-grid", nargs="+", type=int,
        default=[15, 31, 51, 71, 91, 111, 131],
        help="Window grid for the oracle sweep (forced odd).",
    )
    parser.add_argument("--k", type=float, default=3.0, help="Pinned detector k (default 3.0).")
    parser.add_argument("--gaussian-sigma", type=float, default=1.0, help="Pre-smooth sigma.")
    parser.add_argument("--sg-mask-name", default="SG_mask", help="Ground-truth mask name.")
    parser.add_argument("--cp-name", default=None, help="Cell-labels name for in-cell restriction.")
    parser.add_argument(
        "--holdout", nargs="*", default=None,
        help="Field names held out for validation (non-holdout scores are flagged in-sample).",
    )
    parser.add_argument("--c", type=float, default=None, help="granule-size multiplier override.")
    parser.add_argument("--out", default=None, help="Write the full report as JSON to this path.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    # Deferred heavy imports (after --help / arg errors).
    from percell4.domain.measure.window_finder_names import WINDOW_FINDER_NAMES
    from percell4.store import DatasetStore
    from percell4.workflows.models import PunctaDetectorSettings
    from percell4.workflows.window_bakeoff import load_bakeoff_field, run_bakeoff

    finders = list(args.finders) if args.finders else list(WINDOW_FINDER_NAMES)
    unknown = [f for f in finders if f not in WINDOW_FINDER_NAMES]
    if unknown:
        print(f"error: unknown finder(s) {unknown}; known: {list(WINDOW_FINDER_NAMES)}", file=sys.stderr)
        return 1

    settings = PunctaDetectorSettings(
        detector_name="adaptive",
        seed_detector_name="otsu",
        background_estimator_name="mad",
        detector_params={"window_px": args.window_grid[0], "k": args.k},
        min_spot_px=3,
        spot_scale_prior=(1.0, 4.0),
    )

    fields = []
    for ds in args.datasets:
        try:
            store = DatasetStore(Path(ds))
            with store.open_read():
                fields.append(
                    load_bakeoff_field(
                        store, args.channel,
                        sg_mask_name=args.sg_mask_name,
                        cp_name=args.cp_name,
                        gaussian_sigma=args.gaussian_sigma,
                    )
                )
        except Exception as e:  # noqa: BLE001 — surface a load error, do not traceback
            print(f"error loading {ds}: {e}", file=sys.stderr)
            return 1

    finder_params = {"granule-size": {"c": args.c}} if args.c is not None else None
    report = run_bakeoff(
        fields, finders, settings, args.window_grid,
        finder_params=finder_params, holdout_field_names=args.holdout,
    )

    if not report.oracles:
        print("error: no labeled field (no SG_mask in any dataset)", file=sys.stderr)
        return 1

    _print_table(report)
    if args.out:
        Path(args.out).write_text(json.dumps(_report_to_dict(report), indent=2))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
