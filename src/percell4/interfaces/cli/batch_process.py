"""CLI adapter: headless batch compress + segment-all-timepoints + track.

Front-end for
:func:`percell4.application.use_cases.batch_process_datasets.batch_process_datasets`.
Built to run overnight on large multi-timepoint experiments before the
interactive single-cell workflow.

Usage:
    python -m percell4.interfaces.cli.batch_process \\
        /data/dish1 /data/dish2 --output-dir /data/h5 --seg-channel mNG
    percell4-batch /data/dishes/* --output-dir out/ --no-track --quiet
    percell4-batch /data/dish1 --output-dir out/ \\
        --channel-names DAPI,GFP,RFP --seg-channel GFP --seg-name nuclei

Each positional argument is one dataset's TIFF source directory; the output
``.h5`` is written to ``--output-dir/<source_dirname>.h5``. Time-lapse
datasets (``_tN`` filename tokens) are segmented across every timepoint and
tracked unless ``--no-track`` is given.

``--channel-names`` renames the imported channels (one comma-separated name
per channel, in order); ``--seg-channel`` is then matched against these new
names. ``--seg-name`` sets the segmentation layer name (default
``cellpose_<n_cells>``). Both apply to every dataset in the batch.

Exit codes:
    0 -- at least one dataset processed successfully
    1 -- no datasets succeeded (all failed or no inputs)

Programmatic use:
    from percell4.interfaces.cli.batch_process import main
    exit_code = main(["/data/dish1", "--output-dir", "out"])
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import percell4._compat  # noqa: F401 — NumPy 2.0 shims

from percell4.application.use_cases.batch_process_datasets import (
    DatasetSpec,
    batch_process_datasets,
)

logger = logging.getLogger(__name__)


def _build_specs(sources: list[Path], output_dir: Path) -> list[DatasetSpec]:
    """One DatasetSpec per existing source directory; output named after it."""
    specs: list[DatasetSpec] = []
    for src in sources:
        if not src.is_dir():
            print(f"skipping {src}: not a directory", file=sys.stderr)
            continue
        specs.append(DatasetSpec(source_dir=src, output_h5=output_dir / f"{src.name}.h5"))
    return specs


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch",
        description=(
            "Headless batch compress + segment (all timepoints) + track for "
            "multi-timepoint experiments. Each positional argument is a TIFF "
            "source directory; the output .h5 lands at "
            "<output-dir>/<source_dirname>.h5. Time-lapse datasets are "
            "tracked unless --no-track."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("sources", nargs="+", type=Path,
                        help="One or more dataset TIFF source directories.")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Directory for the output .h5 files.")
    parser.add_argument("--seg-channel", default=None,
                        help="Channel name to segment (default: first channel). "
                             "Matched against --channel-names when given.")
    parser.add_argument("--channel-names", default=None,
                        help="Comma-separated names to rename the imported "
                             "channels, in order (e.g. 'DAPI,GFP,RFP'). Must "
                             "match the imported channel count.")
    parser.add_argument("--seg-name", default=None,
                        help="Name for the segmentation layer "
                             "(default: cellpose_<n_cells>).")
    parser.add_argument("--cellpose-model", default="cyto3",
                        help="Cellpose model type (default: cyto3).")
    parser.add_argument("--cellpose-diameter", type=float, default=None,
                        help="Cell diameter in pixels (default: auto).")
    parser.add_argument("--gpu", action="store_true", help="Use GPU for Cellpose.")
    parser.add_argument("--no-track", action="store_true",
                        help="Skip tracking even for time-lapse datasets.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-dataset progress lines.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging.")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    specs = _build_specs(args.sources, args.output_dir)
    if not specs:
        print("No valid dataset source directories given.", file=sys.stderr)
        return 1

    def _progress(done: int, total: int, message: str) -> None:
        if not args.quiet:
            print(f"[{done}/{total}] {message}", flush=True)

    channel_names = None
    if args.channel_names is not None:
        channel_names = [c.strip() for c in args.channel_names.split(",") if c.strip()]
        if not channel_names:
            print("--channel-names was empty after parsing.", file=sys.stderr)
            return 1

    report = batch_process_datasets(
        specs,
        seg_channel=args.seg_channel,
        channel_names=channel_names,
        seg_name=args.seg_name,
        cellpose_model=args.cellpose_model,
        cellpose_diameter=args.cellpose_diameter,
        gpu=args.gpu,
        track=not args.no_track,
        progress_callback=_progress,
    )

    print(
        f"Done: {report.n_succeeded} succeeded, {report.n_failed} failed "
        f"of {len(report.items)} datasets."
    )
    return 0 if report.n_succeeded >= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
