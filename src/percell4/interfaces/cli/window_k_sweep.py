"""CLI adapter: sweep the Adaptive Local Clipping ``(window, k)`` grid across datasets.

Headless front-end for :mod:`percell4.workflows.window_k_sweep` (plan U3). For
each dataset it runs the cell-restricted ``adaptive`` detector at every
``(window, k)`` grid point, writes each ``{0,1}`` mask back into the ``.h5`` as
``/masks/<prefix>_wWWW_kKK``, and prints per-dataset + cross-dataset tables plus
each auto window-finder's pick. The masks are for **visual inspection** — the
printed stats are a navigation aid, not an accept/reject criterion.

Usage:
    percell4-window-k-sweep Test1.h5 Test2.h5 Test3.h5 Test4.h5
    percell4-window-k-sweep Test1.h5 --windows 31 51 71 --ks 2.0 2.5 3.0 \\
        --out manifests/ --clear

Exit codes:
    0 -- at least one dataset swept successfully
    1 -- every dataset failed (or a bad argument)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from percell4.workflows.window_k_sweep import SweepReport

logger = logging.getLogger(__name__)


def _build_parser(
    default_windows: Sequence[int], default_ks: Sequence[float]
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="percell4-window-k-sweep",
        description=(
            "Sweep the Adaptive Local Clipping (window, k) grid across datasets, "
            "writing a mask per grid point into each .h5 for manual inspection."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-window-k-sweep Test1.h5 Test2.h5 Test3.h5 Test4.h5\n"
            "  percell4-window-k-sweep Test1.h5 --windows 31 51 71 --ks 2.0 2.5 "
            "--out manifests/ --clear\n"
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
        "--windows",
        nargs="+",
        type=int,
        default=list(default_windows),
        help=f"Window sizes in px to sweep (forced odd). Default: {list(default_windows)}.",
    )
    parser.add_argument(
        "--ks",
        nargs="+",
        type=float,
        default=list(default_ks),
        help=f"k (contrast margin in σ units) values to sweep. Default: {list(default_ks)}.",
    )
    parser.add_argument("--prefix", default="sweep", help="Mask name prefix (default: sweep).")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete prior <prefix>_* masks before writing (other masks untouched).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the report and intended names without writing masks or manifests.",
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
        help="Background/noise estimator held fixed for the sweep (default: mad).",
    )
    parser.add_argument(
        "--out", default=None, help="Directory for per-dataset manifest sidecars (JSON + CSV)."
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    return parser


def _print_report(report: SweepReport) -> None:
    """Per-dataset table: the masks, knobs, and navigation stats."""
    if report.failure is not None:
        # Failures go to stderr so an agent piping stdout gets clean table data.
        print(f"\n=== {report.dataset}: FAILED — {report.failure} ===", file=sys.stderr)
        return
    print(
        f"\n=== {report.dataset}  shape={report.shape}  "
        f"pixel_size_um={report.pixel_size_um}  cell_px={report.cell_px:,} ==="
    )
    picks = "  ".join(
        f"{p.method}={p.raw_window}px(~{p.nearest_grid_window})" for p in report.auto_picks
    )
    print(f"auto-window picks: {picks}")
    print(f"{'mask':<20}{'window':>7}{'k':>6}{'count':>8}{'in_cell_px':>12}{'fraction':>10}")
    print("-" * 63)
    for r in report.rows:
        print(
            f"{r.name:<20}{r.window:>7}{r.k:>6.1f}{r.particle_count:>8}"
            f"{r.in_cell_positive_px:>12,}{r.in_cell_fraction:>10.4f}"
        )


def _print_summary(reports: Sequence[SweepReport]) -> None:
    """Cross-dataset summary so the datasets compare side by side."""
    print("\n=== Cross-dataset summary ===")
    print(
        f"{'dataset':<16}{'shape':>14}{'px_um':>8}{'masks':>7}"
        f"{'otsu-mean':>11}{'granule':>9}{'status':>9}"
    )
    print("-" * 74)
    for rep in reports:
        if rep.failure is not None:
            print(f"{rep.dataset:<16}{'-':>14}{'-':>8}{0:>7}{'-':>11}{'-':>9}{'FAILED':>9}")
            continue
        picks = {p.method: p.raw_window for p in rep.auto_picks}
        shape_s = "x".join(str(d) for d in (rep.shape or ()))
        px = f"{rep.pixel_size_um:.4g}" if rep.pixel_size_um is not None else "-"
        print(
            f"{rep.dataset:<16}{shape_s:>14}{px:>8}{len(rep.rows):>7}"
            f"{picks.get('otsu-mean', '-'):>11}{picks.get('granule-size', '-'):>9}{'ok':>9}"
        )


def _write_manifest(
    out_dir: Path, report: SweepReport, report_to_dict: Callable[[SweepReport], dict]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{report.dataset}.sweep.json").write_text(
        json.dumps(report_to_dict(report), indent=2), encoding="utf-8"
    )
    # Flat CSV of the rows for spreadsheet navigation.
    lines = ["name,window,k,particle_count,in_cell_positive_px,in_cell_fraction"]
    for r in report.rows:
        lines.append(
            f"{r.name},{r.window},{r.k},{r.particle_count},"
            f"{r.in_cell_positive_px},{r.in_cell_fraction}"
        )
    (out_dir / f"{report.dataset}.sweep.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    # Light defaults import (no skimage/scipy) so --help stays fast.
    from percell4.workflows.window_k_sweep import DEFAULT_KS, DEFAULT_WINDOWS

    parser = _build_parser(DEFAULT_WINDOWS, DEFAULT_KS)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Validate the fixed noise estimator up front so a typo fails cleanly
    # before any dataset is touched.
    from percell4.domain.measure.puncta_names import BG_ESTIMATOR_NAMES

    if args.noise_estimator not in BG_ESTIMATOR_NAMES:
        print(
            f"error: unknown --noise-estimator {args.noise_estimator!r}; "
            f"choose one of {BG_ESTIMATOR_NAMES}",
            file=sys.stderr,
        )
        return 1

    # Heavy imports deferred past --help and the arg check.
    from percell4.store import DatasetStore
    from percell4.workflows.window_k_sweep import (
        FixedSettings,
        report_to_dict,
        run_sweep,
    )

    fixed = FixedSettings(
        noise_estimator=args.noise_estimator,
        gaussian_sigma=args.gaussian_sigma,
        min_spot_px=args.min_spot_px,
    )
    out_dir = Path(args.out) if args.out else None

    reports = []
    for ds in args.datasets:
        store = DatasetStore(ds)
        report = run_sweep(
            store,
            args.channel,
            args.segmentation,
            args.windows,
            args.ks,
            fixed,
            prefix=args.prefix,
            clear=args.clear,
            dry_run=args.dry_run,
        )
        _print_report(report)
        if out_dir is not None and not args.dry_run:
            _write_manifest(out_dir, report, report_to_dict)
        reports.append(report)

    _print_summary(reports)

    if all(r.failure is not None for r in reports):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
