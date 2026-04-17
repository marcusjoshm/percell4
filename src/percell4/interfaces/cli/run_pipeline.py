"""CLI adapter: headless pipeline that runs through use cases.

Stage 6 proof that the hex architecture seam is clean:
  - No Qt, no napari imports anywhere in this module's dependency chain
  - Uses the same Session, use cases, and repository as the GUI
  - NullViewerAdapter satisfies the ViewerPort protocol silently

Usage:
    python -m percell4.interfaces.cli.run_pipeline dataset.h5 \\
        --threshold-channel GFP --threshold-method otsu \\
        --output measurements.csv

Or programmatically:
    from percell4.interfaces.cli.run_pipeline import run_pipeline
    result = run_pipeline(Path("dataset.h5"), threshold_channel="GFP")
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.adapters.null_viewer import NullViewerAdapter
from percell4.application.session import Session
from percell4.application.use_cases.accept_threshold import AcceptThreshold
from percell4.application.use_cases.load_dataset import LoadDataset
from percell4.application.use_cases.measure_cells import MeasureCells
from percell4.application.use_cases.segment_cells import SegmentCells

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of a headless pipeline run."""

    n_cells: int
    n_columns: int
    output_csv: Path | None
    seg_name: str | None
    mask_name: str | None


def run_pipeline(
    h5_path: Path,
    *,
    threshold_channel: str | None = None,
    threshold_method: str = "otsu",
    threshold_value: float | None = None,
    cellpose_model: str = "cyto3",
    cellpose_diameter: float | None = None,
    metrics: list[str] | None = None,
    output_csv: Path | None = None,
    skip_segmentation: bool = False,
    skip_threshold: bool = False,
) -> PipelineResult:
    """Run the analysis pipeline headlessly through use cases.

    This function proves the hex architecture seam: it uses the same
    Session, use cases, and DatasetRepository as the GUI, but with a
    NullViewerAdapter instead of napari. No Qt or napari is imported.

    Args:
        h5_path: Path to the .h5 dataset file.
        threshold_channel: Channel to threshold. If None, uses first channel.
        threshold_method: Threshold algorithm (otsu, triangle, li, adaptive).
        threshold_value: Manual threshold value (overrides method).
        cellpose_model: Cellpose model type for segmentation.
        cellpose_diameter: Cell diameter for Cellpose (None = auto).
        metrics: Metric names to compute (None = all builtins).
        output_csv: Path to write measurements CSV. None = skip export.
        skip_segmentation: If True, use existing segmentation in the .h5.
        skip_threshold: If True, skip thresholding step.

    Returns:
        PipelineResult with cell count, column count, and output paths.
    """
    # ── Composition root (CLI version) ──
    repo = Hdf5DatasetRepository()
    viewer = NullViewerAdapter()
    session = Session()

    # ── Load dataset ──
    load_uc = LoadDataset(repo, viewer, session)
    handle = load_uc.execute(h5_path)
    logger.info("Loaded: %s (%d channels)", handle.name, len(handle.metadata.get("channel_names", [])))

    seg_name = None
    mask_name = None

    # ── Segmentation ──
    if not skip_segmentation:
        from percell4.adapters.cellpose import CellposeSegmenter

        segment_uc = SegmentCells(repo, session, segmenter=CellposeSegmenter())

        # Read the first channel image for segmentation
        images = repo.read_channel_images(handle)
        if not images:
            raise ValueError("Dataset has no channel images")
        first_channel = next(iter(images.values()))

        logger.info("Running Cellpose (%s)...", cellpose_model)
        raw_masks = segment_uc.run_inference(
            first_channel,
            model_type=cellpose_model,
            diameter=cellpose_diameter,
        )

        result = segment_uc.finalize(raw_masks)
        seg_name = result.seg_name
        session.set_active_segmentation(seg_name)
        logger.info(
            "Segmented: %d cells (%d edge, %d small removed)",
            result.n_cells, result.edge_removed, result.small_removed,
        )
    else:
        # Use existing segmentation
        labels_list = repo.list_labels(handle)
        if labels_list:
            seg_name = labels_list[0]
            session.set_active_segmentation(seg_name)
            logger.info("Using existing segmentation: %s", seg_name)
        else:
            raise ValueError("No segmentation found and --skip-segmentation was set")

    # ── Thresholding ──
    if not skip_threshold:
        images = repo.read_channel_images(handle)
        ch_name = threshold_channel or next(iter(images))
        if ch_name not in images:
            raise ValueError(f"Channel '{ch_name}' not found. Available: {list(images)}")

        image = images[ch_name].astype(np.float32)

        if threshold_value is not None:
            value = threshold_value
        else:
            from percell4.domain.measure.thresholding import THRESHOLD_METHODS

            method_key = threshold_method.lower()
            if method_key not in THRESHOLD_METHODS:
                raise ValueError(
                    f"Unknown threshold method '{method_key}'. "
                    f"Available: {list(THRESHOLD_METHODS)}"
                )
            _, value = THRESHOLD_METHODS[method_key](image)

        thresh_uc = AcceptThreshold(repo, viewer, session)
        thresh_result = thresh_uc.execute(image, value, threshold_method, ch_name)
        mask_name = thresh_result.mask_name
        pct = 100.0 * thresh_result.n_positive / thresh_result.n_total if thresh_result.n_total else 0
        logger.info(
            "Threshold: %s = %.1f → %d/%d px (%.1f%%)",
            mask_name, value, thresh_result.n_positive, thresh_result.n_total, pct,
        )

    # ── Measurement ──
    if metrics is None:
        from percell4.domain.measure.metrics import BUILTIN_METRICS
        metrics = list(BUILTIN_METRICS.keys())

    measure_uc = MeasureCells(repo, session)
    df = measure_uc.execute(metrics=metrics)
    logger.info("Measured: %d cells, %d columns", len(df), len(df.columns))

    # ── Export ──
    if output_csv is not None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_csv, index=False)
        logger.info("Exported: %s", output_csv)

    return PipelineResult(
        n_cells=len(df),
        n_columns=len(df.columns),
        output_csv=output_csv,
        seg_name=seg_name,
        mask_name=mask_name,
    )


def main() -> int:
    """CLI entry point with argparse."""
    parser = argparse.ArgumentParser(
        description="PerCell4 headless analysis pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("dataset", type=Path, help="Path to .h5 dataset file")
    parser.add_argument("--threshold-channel", help="Channel to threshold (default: first)")
    parser.add_argument("--threshold-method", default="otsu", help="Threshold algorithm")
    parser.add_argument("--threshold-value", type=float, help="Manual threshold value")
    parser.add_argument("--cellpose-model", default="cyto3", help="Cellpose model type")
    parser.add_argument("--cellpose-diameter", type=float, help="Cell diameter (auto if omitted)")
    parser.add_argument("--output", "-o", type=Path, help="Output CSV path")
    parser.add_argument("--skip-segmentation", action="store_true", help="Use existing segmentation")
    parser.add_argument("--skip-threshold", action="store_true", help="Skip thresholding")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        result = run_pipeline(
            args.dataset,
            threshold_channel=args.threshold_channel,
            threshold_method=args.threshold_method,
            threshold_value=args.threshold_value,
            cellpose_model=args.cellpose_model,
            cellpose_diameter=args.cellpose_diameter,
            output_csv=args.output,
            skip_segmentation=args.skip_segmentation,
            skip_threshold=args.skip_threshold,
        )
        print(f"Done: {result.n_cells} cells, {result.n_columns} columns")
        if result.output_csv:
            print(f"Output: {result.output_csv}")
        return 0
    except Exception as e:
        logger.error("Pipeline failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
