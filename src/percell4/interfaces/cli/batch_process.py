"""CLI adapter: headless batch compress + segment-all-timepoints + track.

Front-end for
:func:`percell4.application.use_cases.batch_process_datasets.batch_process_datasets`.
Built to run overnight on large multi-timepoint experiments before the
interactive single-cell workflow. Exposes the full GUI Segment-tab Cellpose
controls (model, diameter, GPU, flow/cellprob thresholds, min size,
saturation, blur, edge removal) so headless runs reproduce interactive tuning.

Usage:
    python -m percell4.interfaces.cli.batch_process \\
        /data/dish1 /data/dish2 --output-dir /data/h5 --seg-channel mNG
    percell4-batch-cellpose-laptrack /data/dishes/* --output-dir out/ --no-track
    percell4-batch-cellpose-laptrack /data/dish1 --output-dir out/ \\
        --channel-names DAPI,GFP,RFP --seg-channel GFP --seg-name nuclei
    # Re-segment an already-compressed .h5 in place:
    percell4-batch-cellpose-laptrack /data/h5/dish1.h5 --cellprob-threshold -1.0
    # Track-only: re-run laptrack on an existing segmentation, no Cellpose:
    percell4-batch-cellpose-laptrack /data/h5/movie.h5 \\
        --skip-segmentation --seg-name cellpose_88
    # Verbose: surface Cellpose/laptrack native logs + per-frame timing:
    percell4-batch-cellpose-laptrack /data/h5/movie.h5 --verbose

Each positional argument is either a dataset's TIFF source **directory** (which
is imported to ``<output-dir>/<source_dirname>.h5``) or an already-compressed
``.h5`` **file** (the compress/import step is skipped). With ``--output-dir``
an ``.h5`` source is copied there first and the copy is processed; without it
the ``.h5`` is segmented in place. TIFF directory sources require
``--output-dir``. Time-lapse datasets (``_tN`` filename tokens) are segmented
across every timepoint and tracked unless ``--no-track`` is given.

``--channel-names`` renames the imported channels (one comma-separated name
per channel, in order); ``--seg-channel`` is then matched against these new
names. ``--seg-name`` sets the segmentation layer name (default
``cellpose_<n_cells>``). Both apply to every dataset in the batch.

Exit codes:
    0 -- at least one dataset processed successfully
    1 -- no datasets succeeded (all failed, no inputs, or bad settings)

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


def _configure_logging(verbose: bool) -> None:
    """Set up logging for the run.

    Normal mode is quiet: only WARNING+ from libraries, and the use case's
    own INFO milestones. ``--verbose`` switches to a timestamped DEBUG stream
    and lifts the Cellpose and laptrack loggers to INFO so their native
    progress (model load, GPU/MPS device, per-image cell counts and timing,
    linking progress) is surfaced. Those loggers are NOTSET by default and
    would otherwise stay silent behind the WARNING root in normal mode.
    """
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    dep_level = logging.INFO if verbose else logging.WARNING
    for name in ("cellpose", "laptrack"):
        logging.getLogger(name).setLevel(dep_level)


def _build_specs(sources: list[Path], output_dir: Path | None) -> list[DatasetSpec]:
    """Build one DatasetSpec per valid source.

    A ``.h5`` file becomes ``<output-dir>/<name>.h5`` when ``output_dir`` is
    given (copy-then-segment) or its own path when not (segment in place). A
    directory is treated as a TIFF source and requires ``output_dir``;
    anything else is skipped with a note.
    """
    specs: list[DatasetSpec] = []
    for src in sources:
        if src.suffix.lower() == ".h5" and src.is_file():
            out = (output_dir / f"{src.stem}.h5") if output_dir is not None else src
            specs.append(DatasetSpec(source_dir=src, output_h5=out))
        elif src.is_dir():
            if output_dir is None:
                print(
                    f"skipping {src}: TIFF source directory requires --output-dir",
                    file=sys.stderr,
                )
                continue
            specs.append(
                DatasetSpec(source_dir=src, output_h5=output_dir / f"{src.name}.h5")
            )
        else:
            print(f"skipping {src}: not a directory or .h5 file", file=sys.stderr)
    return specs


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    from percell4.workflows.models import CELLPOSE_MODELS, CellposeSettings

    defaults = CellposeSettings()

    parser = argparse.ArgumentParser(
        prog="percell4-batch-cellpose-laptrack",
        description=(
            "Headless batch compress + Cellpose segment (all timepoints) + "
            "laptrack tracking for multi-timepoint experiments. Each positional "
            "argument is a TIFF source directory (imported to "
            "<output-dir>/<source_dirname>.h5) or an already-compressed .h5 "
            "(segmented in place, or copied to --output-dir first). Time-lapse "
            "datasets are tracked unless --no-track."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("sources", nargs="+", type=Path,
                        help="Dataset TIFF source directories and/or .h5 files.")
    parser.add_argument("--output-dir", default=None, type=Path,
                        help="Directory for the output .h5 files. Required for "
                             "TIFF directory sources; for .h5 sources, omit to "
                             "segment in place or give it to copy-then-segment.")
    parser.add_argument("--seg-channel", default=None,
                        help="Channel name to segment (default: first channel). "
                             "Matched against --channel-names when given.")
    parser.add_argument("--channel-names", default=None,
                        help="Comma-separated names to rename the imported "
                             "channels, in order (e.g. 'DAPI,GFP,RFP'). Must "
                             "match the imported channel count.")
    parser.add_argument("--seg-name", default=None,
                        help="Name for the segmentation layer "
                             "(default: cellpose_<n_cells>). With "
                             "--skip-segmentation this is the EXISTING "
                             "segmentation layer to track (required).")
    parser.add_argument("--skip-segmentation", action="store_true",
                        help="Skip Cellpose; only run laptrack on an existing "
                             "segmentation. Requires --seg-name (the existing "
                             "(T,H,W) layer) and a time-lapse dataset.")

    cp = parser.add_argument_group("Cellpose settings (match the GUI Segment tab)")
    cp.add_argument("--cellpose-model", default=defaults.model,
                    choices=CELLPOSE_MODELS,
                    help=f"Cellpose 4.x model (default: {defaults.model}). "
                         f"cpsam_v2 = improved CellposeSAM (better low-contrast); "
                         f"cpsam = original; cpdino / cpdino-vitb = DINOv3 backbones.")
    cp.add_argument("--cellpose-diameter", type=float, default=defaults.diameter,
                    help=f"Cell diameter in pixels, 0 = auto-detect "
                         f"(default: {defaults.diameter:g}).")
    cp.add_argument("--gpu", action="store_true", help="Use GPU for Cellpose.")
    cp.add_argument("--flow-threshold", type=float, default=defaults.flow_threshold,
                    help=f"Flow error threshold; higher = more permissive "
                         f"(default: {defaults.flow_threshold}).")
    cp.add_argument("--cellprob-threshold", type=float,
                    default=defaults.cellprob_threshold,
                    help=f"Cell probability threshold (default: "
                         f"{defaults.cellprob_threshold}).")
    cp.add_argument("--min-size", type=int, default=defaults.min_size,
                    help=f"Minimum cell size in pixels (default: "
                         f"{defaults.min_size}).")
    cp.add_argument("--saturation", type=float, default=defaults.saturation_pct,
                    help="Saturation %% for an ImageJ-style Enhance Contrast LUT "
                         "applied to the segmentation channel before Cellpose; "
                         f"0 disables (default: {defaults.saturation_pct:g}). The "
                         "on-disk /intensity is never modified.")
    cp.add_argument("--blur-sigma", type=float, default=defaults.blur_sigma,
                    help="Gaussian blur sigma applied after the saturation LUT "
                         f"and before Cellpose; 0 disables (default: "
                         f"{defaults.blur_sigma:g}). The on-disk /intensity is "
                         "never modified.")
    cp.add_argument("--no-remove-edge-cells", dest="remove_edge_cells",
                    action="store_false",
                    help="Keep cells touching the image border (default: remove).")
    cp.add_argument("--edge-margin", type=int, default=0,
                    help="Pixels from the border counted as edge when removing "
                         "edge cells (default: 0 = strict border-touching).")
    parser.set_defaults(remove_edge_cells=True)

    parser.add_argument("--no-track", action="store_true",
                        help="Skip tracking even for time-lapse datasets.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-dataset progress lines.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging.")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.skip_segmentation and args.seg_name is None:
        print(
            "--skip-segmentation requires --seg-name (the existing segmentation "
            "layer to track).",
            file=sys.stderr,
        )
        return 1

    try:
        settings = CellposeSettings(
            model=args.cellpose_model,
            diameter=args.cellpose_diameter,
            gpu=args.gpu,
            flow_threshold=args.flow_threshold,
            cellprob_threshold=args.cellprob_threshold,
            min_size=args.min_size,
            saturation_pct=args.saturation,
            blur_sigma=args.blur_sigma,
        )
    except ValueError as e:
        print(f"Invalid Cellpose settings: {e}", file=sys.stderr)
        return 1

    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    specs = _build_specs(args.sources, args.output_dir)
    if not specs:
        print("No valid dataset sources given.", file=sys.stderr)
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
        settings=settings,
        remove_edge_cells=args.remove_edge_cells,
        edge_margin=args.edge_margin,
        skip_segmentation=args.skip_segmentation,
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
