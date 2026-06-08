"""CLI: per-cell measurement + particle analysis + CSV export over existing masks.

Headless, Qt-free front-end for the workflow's measure → particle →
export phases (``percell4.workflows.phases``). Each dataset must already
carry a segmentation (``/labels/<name>``) and the mask(s) to measure
(``/masks/<name>``) — this tool does not segment or threshold. It writes
a timestamped run folder of CSVs/parquet under ``--output``; measurements
never go back into the .h5 files (the provenance invariant).

Pairs with ``percell4-batch-threshold`` (which writes the masks) and
mirrors the GUI "use existing masks" workflow.

Usage:
    percell4-batch-measure dish_1.h5 dish_2.h5 --segmentation cellpose \\
        --mask pbody --min-particle-area 9 --output ~/runs
    percell4-batch-measure /scratch/dishes/ --mask grouped --csv-preset all

Exit codes:
    0 -- at least one dataset was measured and the export landed
    1 -- nothing measured (no labels / no masks / all failed) or export failed

Programmatic use:
    from percell4.interfaces.cli.batch_measure import main
    exit_code = main(["dish_1.h5", "--mask", "pbody", "--min-particle-area", "9"])
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import datetime
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
        prog="percell4-batch-measure",
        description=(
            "Measure per-cell metrics + particle analysis over existing masks "
            "and export CSVs/parquet into a run folder. Requires each dataset "
            "to already have a segmentation (/labels) and mask(s) (/masks)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "datasets",
        nargs="+",
        help="One or more .h5 files, or directories (every *.h5 within, non-recursive).",
    )
    parser.add_argument(
        "--segmentation",
        default=None,
        help="Existing /labels layer to measure against. Auto-picked per dataset if omitted.",
    )
    parser.add_argument(
        "--mask",
        action="append",
        default=None,
        dest="masks",
        help="Mask name to measure (repeatable). Default: every /masks layer present.",
    )
    parser.add_argument(
        "--min-particle-area",
        type=float,
        default=0.0,
        help="Minimum particle area; components below it are dropped (default 0 = keep all).",
    )
    parser.add_argument(
        "--particle-unit",
        choices=("px", "um2"),
        default="px",
        help="Unit for --min-particle-area (default px).",
    )
    parser.add_argument(
        "--edge-mode",
        choices=("exclude", "include_as_normal", "include_as_size_normalized_cohort"),
        default="exclude",
        help="How edge-touching cells are handled at measurement (default exclude).",
    )
    parser.add_argument(
        "--edge-margin",
        type=int,
        default=0,
        help="Pixel margin for the edge-cell test (default 0 = strict border).",
    )
    parser.add_argument(
        "--csv-preset",
        choices=("default", "all"),
        default="default",
        help="CSV columns: 'default' (area/integrated/mean + count/total-area/mean-intensity) "
        "or 'all' (every metric).",
    )
    parser.add_argument(
        "--output",
        default=".",
        help="Parent directory for the timestamped run folder (default cwd). "
        "A new run_<timestamp>_<id>/ subfolder is always created beneath it.",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging.")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    # Defer heavy imports so --help stays fast and Qt-free.
    from percell4.store import DatasetStore
    from percell4.workflows.artifacts import (
        create_run_folder,
        write_run_config,
    )
    from percell4.workflows.channels import intersect_channels
    from percell4.workflows.csv_columns import (
        ALL_PARTICLE_PER_CELL,
        ALL_PARTICLE_PER_CHANNEL,
        DEFAULT_CSV_METRICS,
        DEFAULT_CSV_PARTICLE_PER_CELL,
        DEFAULT_CSV_PARTICLE_PER_CHANNEL,
        build_selected_csv_columns,
    )
    from percell4.domain.measure.metrics import BUILTIN_METRICS
    from percell4.workflows.failures import DatasetFailure
    from percell4.workflows.models import (
        CellposeSettings,
        DatasetSource,
        EdgeMode,
        ParticleSettings,
        RunMetadata,
        ThresholdAlgorithm,
        ThresholdingRound,
        WorkflowConfig,
        WorkflowDatasetEntry,
    )
    from percell4.workflows.phases import (
        export_run,
        measure_one,
        measure_particles_one,
        pick_existing_segmentation,
        record_failure,
        write_staging_parquet,
        write_staging_particles_parquet,
    )
    from percell4.workflows.run_log import RunLog

    paths = resolve_paths(args.datasets)
    if not paths:
        print("No .h5 datasets found in the given paths.", file=sys.stderr)
        return 1

    particle_settings = ParticleSettings(
        min_area=args.min_particle_area, min_area_unit=args.particle_unit
    )
    edge_mode = EdgeMode(args.edge_mode)

    # ── Resolve each dataset: channels, segmentation, masks to measure ──
    entries: list[WorkflowDatasetEntry] = []
    selections: dict[str, list[str]] = {}
    seg_for: dict[str, str] = {}
    channel_sources: list[tuple[str, list[str]]] = []
    for path in paths:
        name = path.stem
        try:
            store = DatasetStore(path)
            meta = store.metadata
            channels = [str(c) for c in (meta.get("channel_names") or [])]
            labels = store.list_labels()
            masks_present = store.list_masks()
        except Exception as e:
            print(f"[error] {name}: cannot open dataset: {e}", file=sys.stderr)
            continue

        seg = args.segmentation or pick_existing_segmentation(labels)
        if seg is None or seg not in labels:
            print(
                f"[error] {name}: no usable segmentation "
                f"({'--segmentation ' + args.segmentation + ' absent' if args.segmentation else 'no /labels present'})"
                f"; skipping.",
                file=sys.stderr,
            )
            continue

        if args.masks is not None:
            masks = [m for m in args.masks if m in masks_present]
            missing = [m for m in args.masks if m not in masks_present]
            if missing:
                print(f"[warn] {name}: masks not present, skipped: {missing}", file=sys.stderr)
        else:
            masks = list(masks_present)
            if masks:
                print(f"[info] {name}: measuring all masks present: {masks}", file=sys.stderr)
        if not masks:
            print(f"[error] {name}: no masks to measure; skipping.", file=sys.stderr)
            continue

        entries.append(
            WorkflowDatasetEntry(
                name=name,
                source=DatasetSource.H5_EXISTING,
                h5_path=path,
                channel_names=channels,
            )
        )
        selections[name] = masks
        seg_for[name] = seg
        channel_sources.append((name, channels))

    if not entries:
        print("No measurable datasets (need /labels + /masks).", file=sys.stderr)
        return 1

    intersected, _outliers = intersect_channels(channel_sources)
    all_mask_names: list[str] = []
    for masks in selections.values():
        for m in masks:
            if m not in all_mask_names:
                all_mask_names.append(m)

    # ── CSV column selection (shared with the GUI via csv_columns) ──
    if args.csv_preset == "all":
        cols = build_selected_csv_columns(
            intersected,
            all_mask_names,
            metrics=sorted(BUILTIN_METRICS),
            particle_per_cell=ALL_PARTICLE_PER_CELL,
            particle_per_channel=ALL_PARTICLE_PER_CHANNEL,
        )
    else:
        cols = build_selected_csv_columns(
            intersected,
            all_mask_names,
            metrics=DEFAULT_CSV_METRICS,
            particle_per_cell=DEFAULT_CSV_PARTICLE_PER_CELL,
            particle_per_channel=DEFAULT_CSV_PARTICLE_PER_CHANNEL,
        )

    run_folder = create_run_folder(Path(args.output))
    config = WorkflowConfig(
        datasets=entries,
        cellpose=CellposeSettings(),
        thresholding_rounds=[],
        selected_csv_columns=cols,
        output_parent=Path(args.output),
        edge_mode=edge_mode,
        edge_margin_px=args.edge_margin,
        particle_settings=particle_settings,
        use_existing_masks=True,
        existing_mask_selections=selections,
    )
    metadata = RunMetadata(
        run_id=uuid.uuid4().hex,
        run_folder=run_folder,
        started_at=datetime.now(),
        intersected_channels=intersected,
    )
    run_log = RunLog(run_folder)
    write_run_config(run_folder, config, metadata)

    def _specs_for(name: str, channels: list[str]) -> list[ThresholdingRound]:
        ch = channels[0] if channels else "channel"
        out: list[ThresholdingRound] = []
        for mask_name in selections[name]:
            try:
                out.append(
                    ThresholdingRound(
                        name=mask_name,
                        channel=ch,
                        metric="mean_intensity",
                        algorithm=ThresholdAlgorithm.KMEANS,
                    )
                )
            except ValueError as e:
                print(f"[error] {name}: invalid mask name {mask_name!r}: {e}", file=sys.stderr)
                record_failure(
                    metadata,
                    dataset_name=name,
                    phase_name="measure",
                    failure=DatasetFailure.MEASUREMENT_ERROR,
                    message=f"invalid mask name {mask_name!r}: {e}",
                )
        return out

    n_ok = 0
    for entry in entries:
        store = DatasetStore(entry.h5_path)
        specs = _specs_for(entry.name, entry.channel_names)
        if not specs:
            continue
        df, failure, msg = measure_one(
            store,
            round_specs=specs,
            edge_mode=edge_mode,
            edge_margin_px=args.edge_margin,
            seg_name=seg_for[entry.name],
            particle_settings=particle_settings,
            run_log=run_log,
            dataset_name=entry.name,
        )
        if failure is not None:
            record_failure(
                metadata,
                dataset_name=entry.name,
                phase_name="measure",
                failure=failure,
                message=msg,
            )
        if df.empty:
            print(f"[error] {entry.name}: {msg}", file=sys.stderr)
            continue
        write_staging_parquet(run_folder, entry.name, df)
        n_ok += 1
        print(f"[ok] {entry.name}: {msg}")

        pdf, pfail, pmsg = measure_particles_one(
            store,
            round_specs=specs,
            particle_settings=particle_settings,
            seg_name=seg_for[entry.name],
            run_log=run_log,
            dataset_name=entry.name,
        )
        if pfail is not None:
            record_failure(
                metadata, dataset_name=entry.name, phase_name="particles",
                failure=pfail, message=pmsg,
            )
        elif not pdf.empty:
            write_staging_particles_parquet(run_folder, entry.name, pdf)

    if n_ok == 0:
        print("No dataset produced measurements.", file=sys.stderr)
        metadata.finished_at = datetime.now()
        write_run_config(run_folder, config, metadata)
        return 1

    efail, emsg = export_run(run_folder, config, metadata, all_mask_names)
    metadata.finished_at = datetime.now()
    write_run_config(run_folder, config, metadata)
    if efail is not None:
        print(f"[error] export failed: {emsg}", file=sys.stderr)
        return 1

    print(f"\n{n_ok}/{len(entries)} datasets measured. Run folder: {run_folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
