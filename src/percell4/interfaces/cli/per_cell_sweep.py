"""CLI adapter: render per-cell ``(window, k)`` contact sheets for labelling.

Headless front-end for :mod:`percell4.workflows.per_cell_sweep`. For each
dataset it sweeps every Cellpose cell over the ``(window, k)`` grid and writes a
contact-sheet PNG per cell (plus ``cells.csv`` index and a blank ``labels.csv``
template) into ``<out>/<dataset>/``. Read-only with respect to the ``.h5``.

Workflow: run this, open each ``cellNNN_contactsheet.png``, pick the best
``(window, k)`` tile per cell, fill those into ``labels.csv``, and hand it back
for analysis.

Usage:
    percell4-per-cell-sweep Test1.h5 --out sheets/
    percell4-per-cell-sweep Test1.h5 --out sheets/ --windows 31 71 111 --ks 1.5 2.5 3.5

Exit codes:
    0 -- at least one dataset rendered
    1 -- every dataset failed (or a bad argument)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_parser(default_windows, default_ks) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="percell4-per-cell-sweep",
        description=(
            "Render a per-cell (window, k) contact sheet for each Cellpose cell, "
            "for manual best-parameter labelling."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-per-cell-sweep Test1.h5 --out sheets/\n"
            "  percell4-per-cell-sweep Test1.h5 --out sheets/ --windows 31 71 111 "
            "--ks 1.5 2.5 3.5\n"
        ),
    )
    parser.add_argument("datasets", nargs="+", help="Dataset .h5 file paths.")
    parser.add_argument("--channel", default="Channel", help="Channel name (default: Channel).")
    parser.add_argument(
        "--segmentation",
        default="Cellpose",
        help="Cellpose segmentation label set name (default: Cellpose).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory; per-dataset sheets go to <out>/<dataset>/.",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=list(default_windows),
        help=f"Window sizes in px (forced odd). Default: {list(default_windows)}.",
    )
    parser.add_argument(
        "--ks",
        nargs="+",
        type=float,
        default=list(default_ks),
        help=f"k (contrast margin in σ units) values. Default: {list(default_ks)}.",
    )
    parser.add_argument(
        "--padding", type=int, default=8, help="Crop padding around each cell, px (default: 8)."
    )
    parser.add_argument(
        "--min-cell-px",
        type=int,
        default=50,
        help="Skip cells smaller than this area in px (default: 50).",
    )
    parser.add_argument(
        "--max-cells",
        type=int,
        default=None,
        help="Cap to the N largest cells per dataset (default: all).",
    )
    parser.add_argument(
        "--gaussian-sigma", type=float, default=1.0, help="Pre-smoothing sigma (default: 1.0)."
    )
    parser.add_argument(
        "--min-spot-px", type=int, default=3, help="Minimum spot area in px (default: 3)."
    )
    parser.add_argument(
        "--noise-estimator",
        default="mad",
        help="Background/noise estimator held fixed (default: mad).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    from percell4.workflows.window_k_sweep import DEFAULT_KS, DEFAULT_WINDOWS

    parser = _build_parser(DEFAULT_WINDOWS, DEFAULT_KS)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    from percell4.domain.measure.puncta_names import BG_ESTIMATOR_NAMES

    if args.noise_estimator not in BG_ESTIMATOR_NAMES:
        print(
            f"error: unknown --noise-estimator {args.noise_estimator!r}; "
            f"choose one of {BG_ESTIMATOR_NAMES}",
            file=sys.stderr,
        )
        return 1

    from percell4.store import DatasetStore
    from percell4.workflows.per_cell_sweep import run_per_cell_sweep
    from percell4.workflows.window_k_sweep import FixedSettings

    fixed = FixedSettings(
        noise_estimator=args.noise_estimator,
        gaussian_sigma=args.gaussian_sigma,
        min_spot_px=args.min_spot_px,
    )
    out_root = Path(args.out)

    any_ok = False
    for ds in args.datasets:
        store = DatasetStore(ds)
        out_dir = out_root / Path(ds).stem
        report = run_per_cell_sweep(
            store,
            args.channel,
            args.segmentation,
            args.windows,
            args.ks,
            fixed,
            out_dir,
            padding=args.padding,
            min_cell_px=args.min_cell_px,
            max_cells=args.max_cells,
        )
        if report.failure is not None:
            print(f"{report.dataset}: FAILED — {report.failure}", file=sys.stderr)
            continue
        any_ok = True
        print(
            f"{report.dataset}: {len(report.rows)} cell sheet(s) -> {out_dir}/  "
            f"(grid {len(report.windows)}×{len(report.ks)}; fill in {out_dir}/labels.csv)"
        )

    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
