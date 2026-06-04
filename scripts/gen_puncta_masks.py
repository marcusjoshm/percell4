"""Generate /masks layers for adaptive-detector recipes, for visual comparison.

Computes the k-means intensity grouping once (it is detector-independent), then
applies one or more headless two-pass recipes, writing each as a
``/masks/<name>`` layer. Pair with ``scripts/compare_masks.py`` to eyeball them.

A recipe is ``name:window_px:k`` — the two knobs of the ``adaptive`` detector.
Smaller ``window_px`` starves extended structures (streaks / dilute patches)
while compact granules still clear the local background; larger ``k`` raises the
contrast floor uniformly (kills the faintest pickups — dilute first, then dim
granules). All other settings match the existing ``aw*`` masks exactly.

Usage:
    python scripts/gen_puncta_masks.py DS.h5 mNG --seg cp_mask \
        --recipe aw11_k225:11:2.25 aw11_k25:11:2.5 aw15_k30:15:3.0
"""

from __future__ import annotations

import argparse
import sys

from percell4.store import DatasetStore
from percell4.workflows.models import (
    PunctaDetectorSettings,
    ThresholdAlgorithm,
    ThresholdingRound,
)
from percell4.workflows.phases import apply_threshold_headless, threshold_compute_one


def _parse_recipe(spec: str) -> tuple[str, int, float]:
    try:
        name, win, k = spec.split(":")
        return name, int(win), float(k)
    except ValueError as exc:  # noqa: BLE001 - argparse surfaces the message
        raise argparse.ArgumentTypeError(
            f"recipe {spec!r} must be name:window_px:k (e.g. aw11_k225:11:2.25)"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write adaptive-detector /masks layers.")
    ap.add_argument("dataset")
    ap.add_argument("channel")
    ap.add_argument("--seg", default="cp_mask", help="/labels set defining the cells.")
    ap.add_argument(
        "--recipe",
        nargs="+",
        required=True,
        type=_parse_recipe,
        help="One or more name:window_px:k specs.",
    )
    ap.add_argument("--detector", default="adaptive")
    ap.add_argument("--bg", default="gaussian-peak")
    ap.add_argument("--seed", default="otsu")
    ap.add_argument("--min-spot-px", type=int, default=3)
    ap.add_argument("--n-clusters", type=int, default=3)
    ap.add_argument("--gaussian-sigma", type=float, default=1.0)
    ap.add_argument(
        "--scale-prior",
        default="1.0,4.0",
        help="min,max sigma scale prior.",
    )
    args = ap.parse_args(argv)

    lo, hi = (float(x) for x in args.scale_prior.split(","))
    store = DatasetStore(args.dataset)

    def build_round(name: str, win: int, k: float) -> ThresholdingRound:
        puncta = PunctaDetectorSettings(
            detector_name=args.detector,
            seed_detector_name=args.seed,
            background_estimator_name=args.bg,
            detector_params={"window_px": win, "k": k},
            seed_params={"k": 2.5},
            min_spot_px=args.min_spot_px,
            spot_scale_prior=(lo, hi),
        )
        return ThresholdingRound(
            name=name,
            channel=args.channel,
            metric="mean_intensity",
            algorithm=ThresholdAlgorithm.KMEANS,
            kmeans_n_clusters=args.n_clusters,
            gaussian_sigma=args.gaussian_sigma,
            puncta=puncta,
        )

    grouping = None
    for name, win, k in args.recipe:
        rnd = build_round(name, win, k)
        if grouping is None:
            grouping, fail, msg = threshold_compute_one(store, rnd, seg_name=args.seg)
            if fail is not None:
                print(f"grouping failed: {fail} {msg}", file=sys.stderr)
                return 1
        failure, msg2 = apply_threshold_headless(store, rnd, grouping, seg_name=args.seg)
        status = "OK" if failure is None else f"FAIL({failure})"
        print(f"{name:12s} win={win:>3d} k={k:<5g} -> {status}  {msg2}")

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
