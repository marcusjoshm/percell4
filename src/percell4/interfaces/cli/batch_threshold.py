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
    percell4-batch-threshold dish_1.h5 --channel RFP --round-name SG_iter \\
        --strategy iterative-otsu --iterative-scope per-cell \\
        --stop-criteria bg-floor,positive-fraction-high \\
        --stop-param bg-floor.k=2.5 --verbose

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

from percell4.interfaces.cli._batch_report import resolve_paths

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _parse_stop_param(spec: str) -> tuple[str, object]:
    """Parse a ``CRITERION.KEY=VALUE`` stop-param override into a ``(key, value)`` pair.

    The key keeps its dotted ``CRITERION.KEY`` form (validated against the known
    criteria by ``IterativeOtsuSettings``); the value is coerced to
    bool/int/float when possible, else kept as a string. Raises ``ValueError`` on
    a malformed spec so the caller can exit 1 with a clear message.
    """
    if "=" not in spec:
        raise ValueError(f"--stop-param must be CRITERION.KEY=VALUE, got {spec!r}")
    key, _, raw = spec.partition("=")
    key, raw = key.strip(), raw.strip()
    if not key or not raw:
        raise ValueError(f"--stop-param must be CRITERION.KEY=VALUE, got {spec!r}")
    low = raw.lower()
    if low in ("true", "false"):
        return key, low == "true"
    try:
        return key, int(raw)
    except ValueError:
        pass
    try:
        return key, float(raw)
    except ValueError:
        pass
    return key, raw


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
    grp.add_argument(
        "--round-name", required=True, help="Mask/group name to write (the round name)."
    )
    grp.add_argument("--channel", required=True, help="Channel name to threshold.")
    grp.add_argument(
        "--metric",
        default="mean_intensity",
        help="Per-cell metric to group cells before thresholding (default mean_intensity).",
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
    grp.add_argument(
        "--gmm-max-components", type=int, default=4, help="Max GMM components (default 4)."
    )
    grp.add_argument(
        "--kmeans-n-clusters", type=int, default=3, help="K-means cluster count (default 3)."
    )
    grp.add_argument(
        "--gaussian-sigma", type=float, default=1.0,
        help=(
            "Pre-threshold Gaussian sigma (default 1.0). For --strategy "
            "adaptive-clip or auto-extract this is the detector's per-cell "
            "presmooth sigma (px)."
        ),
    )
    grp.add_argument(
        "--strategy",
        choices=("grouped-otsu", "iterative-otsu", "adaptive-clip", "auto-extract"),
        default="grouped-otsu",
        help=(
            "Thresholding strategy. 'grouped-otsu' (default) is the legacy "
            "per-group Otsu. 'iterative-otsu' peels the brightest layer each "
            "round (see the iterative-otsu options group). 'adaptive-clip' runs "
            "the per-cell single-window adaptive sigma-clipping detector (see the "
            "adaptive-clip options group). 'auto-extract' runs the per-cell "
            "two-pass auto-extraction detector (see the auto-extract options "
            "group). Both per-cell strategies ignore --algorithm/--metric grouping."
        ),
    )
    ac = parser.add_argument_group(
        "Adaptive sigma clipping (only used when --strategy adaptive-clip)"
    )
    ac.add_argument(
        "--d-min-um", type=float, default=None,
        help=(
            "Smallest particle diameter to detect, as a value in --d-min-unit "
            "(REQUIRED for --strategy adaptive-clip). Sets the local-background "
            "window and size filter; the noise floor is a robust per-cell MAD."
        ),
    )
    ac.add_argument(
        "--d-min-unit", choices=("um", "px"), default="um",
        help=(
            "Unit for --d-min-um (default um). 'um' resolves via the dataset pixel "
            "size; 'px' is used directly — for datasets without a pixel size."
        ),
    )
    ac.add_argument(
        "--k", type=float, default=1.0,
        help="Adaptive-clip sigma multiplier (default 1.0; raise to be more conservative).",
    )
    ae = parser.add_argument_group(
        "Auto extraction (two-pass) (only used when --strategy auto-extract)"
    )
    ae.add_argument(
        "--smallest-particle-um", type=float, default=None,
        help=(
            "Smallest particle diameter, as a value in --smallest-particle-unit, to "
            "OVERRIDE auto-detection of the smallest particle. Omit to auto-detect "
            "it from the image. The largest particle is always measured (LoG)."
        ),
    )
    ae.add_argument(
        "--smallest-particle-unit", choices=("um", "px"), default="um",
        help=(
            "Unit for --smallest-particle-um (default um). 'um' resolves via the "
            "dataset pixel size; 'px' is used directly — for datasets without a "
            "pixel size."
        ),
    )
    cnr = parser.add_argument_group(
        "Guided CNR subpopulation classification (opt-in; ALC strategies only)"
    )
    cnr.add_argument(
        "--cnr-classify", action="store_true",
        help=(
            "After the feature mask is produced, split its foci by contrast-to-"
            "noise ratio at --cnr-threshold into <round>_low / <round>_high masks "
            "plus a per-focus CNR table at /classification/<round>. Guided mode "
            "only; valid only with --strategy adaptive-clip or auto-extract."
        ),
    )
    cnr.add_argument(
        "--cnr-threshold", type=float, default=None,
        help="Guided CNR split threshold (REQUIRED with --cnr-classify).",
    )
    itr = parser.add_argument_group(
        "Iterative Otsu (only used when --strategy iterative-otsu)"
    )
    itr.add_argument(
        "--iterative-scope",
        choices=("groups", "per-cell", "whole-field"),
        default="per-cell",
        help="Iteration unit (default per-cell). 'groups' reuses --algorithm grouping.",
    )
    itr.add_argument(
        "--dilation-radius", type=int, default=5,
        help="Guard-ring radius (px) removed around each captured layer (default 5).",
    )
    itr.add_argument(
        "--max-rounds", type=int, default=10,
        help="Hard cap on peel iterations per dataset (default 10).",
    )
    itr.add_argument(
        "--iterations", type=int, default=None,
        help=(
            "Run EXACTLY this many peel iterations per unit, ignoring the stop "
            "criteria (blocks --max-rounds / --stop-criteria / --stop-combine). "
            "Omit to use the criteria-driven mode."
        ),
    )
    itr.add_argument(
        "--stop-criteria",
        default="bg-floor,positive-fraction-high",
        help=(
            "Comma-separated stopping criteria (default "
            "'bg-floor,positive-fraction-high'). One of: bg-floor, separability, "
            "positive-fraction-high, min-positive, diminishing-returns, "
            "peak-prominence, min-area-components."
        ),
    )
    itr.add_argument(
        "--stop-combine",
        choices=("any", "all"),
        default="any",
        help="Stop a unit when ANY (default) or ALL active criteria fire.",
    )
    itr.add_argument(
        "--stop-param",
        action="append",
        default=[],
        metavar="CRITERION.KEY=VALUE",
        help=(
            "Override a stopping-criterion parameter (repeatable), e.g. "
            "--stop-param bg-floor.k=2.5 --stop-param positive-fraction-high.max_frac=0.6."
        ),
    )
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
        AdaptiveClipSettings,
        AutoExtractSettings,
        CnrClassifySettings,
        GmmCriterion,
        IterativeOtsuSettings,
        ThresholdAlgorithm,
        ThresholdingRound,
    )
    from percell4.workflows.phases import (
        apply_threshold_headless,
        pick_existing_segmentation,
        threshold_compute_one,
    )

    if args.strategy == "adaptive-clip" and args.d_min_um is None:
        print(
            "[error] --strategy adaptive-clip requires --d-min-um (smallest "
            "particle diameter, µm).",
            file=sys.stderr,
        )
        return 1

    if args.cnr_classify and args.cnr_threshold is None:
        print(
            "[error] --cnr-classify requires --cnr-threshold (guided CNR split value).",
            file=sys.stderr,
        )
        return 1
    if args.cnr_classify and args.strategy not in ("adaptive-clip", "auto-extract"):
        print(
            "[error] --cnr-classify is only valid with --strategy adaptive-clip or "
            "auto-extract (CNR is defined against the per-cell sigma detector).",
            file=sys.stderr,
        )
        return 1

    try:
        iterative = None
        adaptive = None
        auto_extract = None
        if args.strategy == "iterative-otsu":
            stop_params = tuple(_parse_stop_param(p) for p in args.stop_param)
            stop_criteria = tuple(c.strip() for c in args.stop_criteria.split(",") if c.strip())
            iterative = IterativeOtsuSettings(
                scope=args.iterative_scope,
                dilation_radius_px=args.dilation_radius,
                max_rounds=args.max_rounds,
                stop_criteria=stop_criteria,
                stop_params=stop_params,
                stop_combine=args.stop_combine,
                fixed_iterations=args.iterations,
            )
        elif args.strategy == "adaptive-clip":
            adaptive = AdaptiveClipSettings(
                d_min_um=args.d_min_um,
                k=args.k,
                presmooth_sigma_px=args.gaussian_sigma,
                d_min_unit=args.d_min_unit,
            )
        elif args.strategy == "auto-extract":
            auto_extract = AutoExtractSettings(
                smallest_particle_um=args.smallest_particle_um,
                presmooth_sigma_px=args.gaussian_sigma,
                smallest_particle_unit=args.smallest_particle_unit,
            )
        cnr_classify = (
            CnrClassifySettings(threshold=args.cnr_threshold) if args.cnr_classify else None
        )
        round_spec = ThresholdingRound(
            name=args.round_name,
            channel=args.channel,
            metric=args.metric,
            algorithm=ThresholdAlgorithm(args.algorithm),
            gmm_criterion=GmmCriterion(args.gmm_criterion),
            gmm_max_components=args.gmm_max_components,
            kmeans_n_clusters=args.kmeans_n_clusters,
            gaussian_sigma=args.gaussian_sigma,
            iterative_otsu=iterative,
            adaptive_clip=adaptive,
            auto_extract=auto_extract,
            cnr_classify=cnr_classify,
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
            print(
                f"[{idx + 1}/{n_total}] [error] {name}: threshold apply failed: {msg}",
                file=sys.stderr,
            )
            continue
        n_ok += 1
        written_for.append(name)
        if args.strategy == "iterative-otsu":
            strat = f" ({args.strategy}/{args.iterative_scope}; --verbose for peel report)"
        elif args.strategy == "adaptive-clip":
            strat = f" (adaptive-clip; d_min={args.d_min_um:g} {args.d_min_unit}, k={args.k:g})"
        elif args.strategy == "auto-extract":
            smallest = (
                f"{args.smallest_particle_um:g} {args.smallest_particle_unit}"
                if args.smallest_particle_um is not None
                else "auto"
            )
            strat = f" (auto-extract; smallest={smallest})"
        else:
            strat = ""
        if args.cnr_classify:
            strat += f" + CNR split @ {args.cnr_threshold:g}"
        print(f"[{idx + 1}/{n_total}] [ok] {name}: wrote /masks/{args.round_name}{strat}")

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
