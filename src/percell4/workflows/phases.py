"""Pure helpers for the single-cell thresholding workflow's unattended phases.

These functions are the batteries-included core of Phase 0 (compress),
Phase 1 (segment), Phase 3/5/... (threshold compute + headless apply),
Phase 7 (measure), and Phase 8 (export). They are Qt-agnostic: the
concrete :class:`percell4.gui.workflows.single_cell.runner.SingleCellThresholdingRunner`
wraps each helper in an ``UNATTENDED`` :class:`PhaseRequest`, but these
helpers are also unit-testable standalone.

Design notes
------------

- Every helper returns a tuple ``(result, failure, message)`` where
  ``failure`` is either ``None`` (success) or a :class:`DatasetFailure`
  value. The runner appends a :class:`FailureRecord` to
  ``RunMetadata.failures`` for any failed dataset and excludes it from
  downstream phases. No per-cell exceptions bubble out — they are caught
  and turned into a failure record at the dataset boundary.

- :func:`apply_threshold_headless` is the Phase 4 stand-in for the
  interactive :class:`ThresholdQCController` that lands in Phase 6.
  It computes Otsu thresholds per group (after a Gaussian smoothing
  pass), unions the per-group masks into a combined binary mask, and
  writes ``/masks/<round_name>`` and ``/groups/<round_name>`` into the
  dataset's h5 — exactly what ``ThresholdQCController._finalize`` does
  after accepting every group's threshold. When Phase 6 lands, the
  runner will call the interactive controller instead and this helper
  becomes a "headless" fallback for unattended runs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

if TYPE_CHECKING:
    from percell4.workflows.run_log import RunLog

from percell4.adapters.cellpose import run_cellpose
from percell4.domain.measure.grouper import GroupingResult, group_cells_gmm, group_cells_kmeans
from percell4.domain.measure.measurer import measure_cells, measure_multichannel_with_masks
from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.domain.segmentation.postprocess import (
    filter_edge_cells,
    filter_small_cells,
    get_edge_labels,
    relabel_sequential,
)
from percell4.store import DatasetStore
from percell4.workflows.artifacts import write_atomic
from percell4.workflows.failures import DatasetFailure, FailureRecord
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

logger = logging.getLogger(__name__)


# ── Phase 0: Compress ───────────────────────────────────────────────────


def compress_one(
    entry: WorkflowDatasetEntry,
) -> tuple[WorkflowDatasetEntry, DatasetFailure | None, str]:
    """Compress one ``tiff_pending`` entry into an .h5 file.

    Returns an updated entry with ``source=H5_EXISTING`` and the real
    ``h5_path`` on success. Entries that are already ``h5_existing`` are
    returned unchanged.

    Errors during ``import_dataset`` are caught and returned as a
    :class:`DatasetFailure.COMPRESS_FAILED` record; the entry itself is
    still returned (with its original pending state) so the caller can
    drop it from later phases.
    """
    if entry.source is DatasetSource.H5_EXISTING:
        return entry, None, ""

    plan = entry.compress_plan or {}
    source_dir = plan.get("source_dir", "")
    files_paths: list[str] = plan.get("files", [])
    output_path = Path(plan.get("output_path", entry.h5_path))
    z_project_method = plan.get("z_project_method", "mip")
    selected_channels = set(plan.get("selected_channels", []))

    # Deserialize layer_assignments — preserves user-renamed channel
    # display names (e.g. token "00" -> "mNG") so the h5 written by
    # import_dataset reflects the names the researcher chose in the
    # CompressDialog, not the raw token IDs.
    layer_assignments: dict[str, Any] | None = None
    la_payload = plan.get("layer_assignments")
    if la_payload:
        from percell4.domain.io.models import LayerAssignment, LayerType

        layer_assignments = {}
        for ch_id, entry_dict in la_payload.items():
            try:
                lt = LayerType(entry_dict.get("layer_type", "channel"))
            except (KeyError, ValueError):
                lt = LayerType.CHANNEL
            layer_assignments[ch_id] = LayerAssignment(
                layer_type=lt,
                name=entry_dict.get("name", ""),
            )

    # Deserialize tile_config — preserves the stitching grid the user
    # configured in the CompressDialog (rows × cols, traversal pattern,
    # start corner). Without this, multi-tile datasets silently land in
    # the .h5 with only the first tile's pixels — _load_and_stitch
    # raises rather than dropping files once this key is missing AND
    # multiple files target one channel.
    tile_config: Any | None = None
    tc_payload = plan.get("tile_config")
    if tc_payload:
        from percell4.domain.io.models import TileConfig

        tile_config = TileConfig(
            grid_rows=int(tc_payload.get("grid_rows", 1)),
            grid_cols=int(tc_payload.get("grid_cols", 1)),
            grid_type=str(tc_payload.get("grid_type", "row_by_row")),
            order=str(tc_payload.get("order", "right_down")),
        )

    # import_dataset accepts ``files=`` as either DiscoveredFile-like
    # objects or plain path strings — its scanner re-derives tokens
    # from filenames. Pass path strings directly so we don't need to
    # serialize / reconstruct tokens through the compress_plan dict.
    try:
        from percell4.adapters.importer import import_dataset

        # Single-cell workflow inherits creation_bin from the captured
        # CompressDialog plan if present; otherwise defaults to 1 (no
        # binning), keeping existing single-cell runs byte-identical
        # apart from the two new /metadata keys.
        creation_bin = int(plan.get("creation_bin", 1))
        import_dataset(
            source_dir=source_dir or str(output_path.parent),
            output_h5=output_path,
            z_project_method=z_project_method,
            selected_channels=selected_channels or None,
            layer_assignments=layer_assignments,
            files=files_paths or None,
            creation_bin=creation_bin,
            tile_config=tile_config,
        )
    except Exception as e:
        logger.exception("compress_one failed for %s", entry.name)
        return (
            entry,
            DatasetFailure.COMPRESS_FAILED,
            f"{type(e).__name__}: {e}",
        )

    updated = WorkflowDatasetEntry(
        name=entry.name,
        source=DatasetSource.H5_EXISTING,
        h5_path=output_path,
        channel_names=list(entry.channel_names),
        compress_plan=None,
    )
    return updated, None, ""


# ── Phase 1: Segment ────────────────────────────────────────────────────


def _read_segmentation_channel(store: DatasetStore, channel_idx: int = 0) -> NDArray:
    """Read one channel plane from /intensity for segmentation.

    Works for both 2D (single-channel) and 3D (C, H, W) layouts by
    delegating to :meth:`DatasetStore.read_channel`. Single-timepoint only —
    ``read_channel`` raises on the 4D ``(T, C, H, W)`` time-lapse layout. Use
    :func:`_read_segmentation_channel_stack` for time-lapse datasets.
    """
    return store.read_channel("intensity", channel_idx)


def _channel_from_frame(frame: NDArray, channel_idx: int) -> NDArray:
    """Pick the segmentation channel out of one timepoint's intensity frame.

    A frame is ``(H, W)`` (single channel) or ``(C, H, W)`` (multichannel) —
    one rank below the stored ``(T, H, W)`` / ``(T, C, H, W)`` array.
    """
    if frame.ndim == 2:
        if channel_idx != 0:
            raise IndexError(f"channel_idx={channel_idx} out of range for single-channel frame")
        return frame
    return frame[channel_idx]


def _read_segmentation_channel_stack(
    store: DatasetStore, channel_idx: int, n_timepoints: int
) -> NDArray:
    """Assemble the ``(T, H, W)`` segmentation-channel stack for a time-lapse.

    Reads one timepoint at a time via :meth:`DatasetStore.read_array_frame`
    (which handles the 4D layout that ``read_channel`` cannot) and picks the
    channel from each frame. This per-frame read is the load-bearing piece
    reused by the time-lapse threshold/measure/QC paths.
    """
    frames = [
        _channel_from_frame(store.read_array_frame("intensity", t), channel_idx)
        for t in range(n_timepoints)
    ]
    return np.stack(frames, axis=0)


def pick_existing_segmentation(label_names: list[str]) -> str | None:
    """Pick the default segmentation to use from a dataset's label inventory.

    Rule: prefer a tracked layer (``*_tracked``); else, if exactly one
    segmentation, use it; else (multiple untracked, no tracked) use the
    lexicographically first — the caller logs a warning and the
    segmentation-select dialog (U12) is where the user overrides. Returns
    ``None`` when there is
    no segmentation (``label_names`` is empty), which signals "segment this
    dataset normally". Note ``store.list_labels()`` already excludes masks
    (they live in a separate ``/masks/`` group), so no subtraction is needed.
    """
    if not label_names:
        return None
    tracked = sorted(n for n in label_names if n.endswith("_tracked"))
    if tracked:
        return tracked[0]
    return sorted(label_names)[0]


def _postprocess_labels(
    labels: NDArray,
    cfg: CellposeSettings,
    edge_mode: EdgeMode,
    edge_margin_px: int,
) -> NDArray[np.int32]:
    """Edge filter (conditional) + small-cell filter + sequential relabel.

    Shared by the single-frame and per-frame time-lapse segmentation paths.
    Edge removal is conditional on ``edge_mode``; modes other than EXCLUDE
    keep edge cells (flagged ``is_edge`` at measure time).
    """
    labels = labels.astype(np.int32)
    if edge_mode == EdgeMode.EXCLUDE:
        labels, _n_edge = filter_edge_cells(labels, edge_margin=edge_margin_px)
    labels, _n_small = filter_small_cells(labels, min_area=cfg.min_size)
    return relabel_sequential(labels)


def segment_one(
    store: DatasetStore,
    cfg: CellposeSettings,
    cellpose_model: Any = None,
    channel_idx: int = 0,
    edge_mode: EdgeMode = EdgeMode.EXCLUDE,
    edge_margin_px: int = 0,
    seg_name: str = "cellpose_qc",
) -> tuple[NDArray[np.int32], DatasetFailure | None, str]:
    """Run Cellpose + postprocess on one dataset and write `/labels/cellpose_qc`.

    Returns the post-processed label array. On empty segmentation,
    returns an empty label array and a :class:`DatasetFailure` code so
    the runner can skip the dataset from later phases. Exceptions inside
    Cellpose become :data:`DatasetFailure.SEGMENTATION_ERROR` records.

    ``cellpose_model`` is optional: when the runner hoists a single
    ``CellposeModel`` instance out of the per-dataset loop and passes it
    here, model construction (seconds-to-minutes on CPU) happens once
    per phase, not once per dataset.

    ``edge_mode`` controls whether border-touching cells are removed in
    postprocess. Default ``EXCLUDE`` matches the pre-evolution workflow
    invariant. ``INCLUDE_AS_NORMAL`` and ``INCLUDE_AS_SIZE_NORMALIZED_COHORT``
    keep edge cells in labels; they are flagged via the ``is_edge`` column
    at measure time (recomputed from labels by ``get_edge_labels`` — no
    persistence here).
    """
    n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
    try:
        if n_timepoints > 1:
            image = _read_segmentation_channel_stack(store, channel_idx, n_timepoints)  # (T, H, W)
        else:
            image = _read_segmentation_channel(store, channel_idx=channel_idx)
    except (KeyError, IndexError, ValueError) as e:
        logger.exception("failed to read intensity for segmentation")
        return (
            np.zeros((0, 0), dtype=np.int32),
            DatasetFailure.SEGMENTATION_ERROR,
            f"read /intensity failed: {e}",
        )

    diameter = cfg.diameter if cfg.diameter > 0 else None

    # Pre-Cellpose saturation LUT — same ImageJ-style Enhance Contrast
    # operation the seg-QC Modify Channel group exposes interactively.
    # 0 is a no-op so legacy runs and users who opt out get
    # byte-identical Cellpose input. For time-lapse stacks the LUT is
    # applied per-frame so the percentile reference is the frame's
    # own intensity, not the whole stack's.
    from percell4.domain.segmentation.preprocess import apply_saturation_lut

    def _preprocess(plane: NDArray) -> NDArray:
        if cfg.saturation_pct > 0.0:
            return apply_saturation_lut(plane, cfg.saturation_pct)
        return plane

    def _infer(plane: NDArray) -> NDArray:
        return run_cellpose(
            _preprocess(plane),
            diameter=diameter,
            gpu=cfg.gpu,
            flow_threshold=cfg.flow_threshold,
            cellprob_threshold=cfg.cellprob_threshold,
            min_size=cfg.min_size,
            model=cellpose_model,  # reuse the hoisted model across frames
        )

    try:
        if n_timepoints > 1:
            # Time-lapse: segment every frame independently (per-frame ids;
            # tracking unifies them later), reusing the hoisted model.
            # _infer() handles the per-frame saturation LUT internally.
            frame_labels = [
                _postprocess_labels(_infer(image[t]), cfg, edge_mode, edge_margin_px)
                for t in range(n_timepoints)
            ]
            labels = np.stack(frame_labels, axis=0).astype(np.int32)
        else:
            labels = _postprocess_labels(_infer(image), cfg, edge_mode, edge_margin_px)
    except Exception as e:
        logger.exception("run_cellpose raised for this dataset")
        return (
            np.zeros((0, 0), dtype=np.int32),
            DatasetFailure.SEGMENTATION_ERROR,
            f"Cellpose failed: {type(e).__name__}: {e}",
        )

    # Empty Cellpose results used to short-circuit here with
    # ``SEGMENTATION_EMPTY``, which recorded a per-dataset failure and
    # routed the dataset around every later phase — including
    # interactive seg QC, leaving the user with no way to draw cells
    # manually. Now we always persist the (possibly all-zero) labels so
    # the QC phase has a layer to load and the user can draw cells in
    # napari directly. Cellpose finding nothing on a particular
    # channel/diameter combination is a routine condition (especially
    # on dim or unusual data); the workflow has to recover from it.
    try:
        store.write_labels(seg_name, labels)
    except Exception as e:
        logger.exception("failed to write /labels/%s", seg_name)
        return (
            labels,
            DatasetFailure.SEGMENTATION_ERROR,
            f"write /labels/{seg_name} failed: {e}",
        )

    n_cells = int(labels.max())
    if n_cells == 0:
        return (
            labels,
            None,
            "Cellpose found 0 cells — draw labels manually in QC",
        )
    return labels, None, f"{n_cells} cells after postprocess"


# ── Tracking (time-lapse): link cells across timepoints ────────────────


def track_one(
    store: DatasetStore,
    raw_seg_name: str,
    tracked_name: str | None = None,
    tracker: Any = None,
) -> tuple[str | None, DatasetFailure | None, str]:
    """Track a ``(T, H, W)`` raw segmentation; write the tracked labels + lineage.

    Reads ``/labels/<raw_seg_name>`` (a time-lapse stack), runs the tracker
    (laptrack by default), relabels via the shared
    :func:`~percell4.domain.tracking.build.build_tracked_result`, and writes
    a new ``<raw>_tracked`` segmentation plus its ``/tracks/<name>`` lineage
    table — preserving the raw segmentation. Returns
    ``(tracked_seg_name, failure, message)``; on failure the tracked name is
    ``None`` and a :class:`DatasetFailure` is returned so the runner drops the
    dataset from later phases. Mirrors ``TrackCells`` but on ``DatasetStore``
    (no Session), sharing the relabel/lineage logic.
    """
    from percell4.adapters.laptrack_tracker import LaptrackTracker
    from percell4.domain.tracking.build import build_tracked_result

    try:
        raw = store.read_labels(raw_seg_name)
    except (KeyError, ValueError) as e:
        return None, DatasetFailure.TRACKING_ERROR, f"read /labels/{raw_seg_name} failed: {e}"
    if raw.ndim != 3:
        return (
            None,
            DatasetFailure.TRACKING_ERROR,
            f"/labels/{raw_seg_name} is {raw.ndim}D, not a (T, H, W) stack",
        )

    tracker = tracker or LaptrackTracker()
    try:
        result = tracker.track(raw)
        built = build_tracked_result(raw, result.track_df, result.split_df)
    except Exception as e:
        logger.exception("tracking failed for /labels/%s", raw_seg_name)
        return None, DatasetFailure.TRACKING_ERROR, f"tracking failed: {type(e).__name__}: {e}"

    seg_name = tracked_name or f"{raw_seg_name}_tracked"
    try:
        store.write_labels(seg_name, built.tracked_labels)
        store.write_tracks(seg_name, built.lineage)
    except Exception as e:
        logger.exception("failed to write tracked segmentation %s", seg_name)
        return None, DatasetFailure.TRACKING_ERROR, f"write {seg_name} failed: {e}"

    return (
        seg_name,
        None,
        f"{built.n_tracks} tracks, {built.n_divisions} divisions",
    )


# ── Phase 3/5/...: Threshold compute + headless apply ──────────────────


def _group_image_labels(
    image: NDArray, labels: NDArray, round_spec: ThresholdingRound
) -> tuple[GroupingResult | None, DatasetFailure | None, str]:
    """Measure the round metric on one 2D frame and group its cells.

    Returns ``(GroupingResult | None, failure, message)``. Shared by the
    single-frame and per-timepoint threshold-compute paths.
    """
    if int(labels.max()) == 0:
        return None, DatasetFailure.THRESHOLD_EMPTY, "no cells"
    try:
        measure_df = measure_cells(image, labels, metrics=[round_spec.metric])
    except Exception as e:
        logger.exception("measure_cells failed for threshold_compute")
        return None, DatasetFailure.THRESHOLD_ERROR, f"measure_cells failed: {e}"
    if len(measure_df) == 0:
        return None, DatasetFailure.THRESHOLD_EMPTY, "measure_cells returned 0 rows"

    values = measure_df[round_spec.metric].to_numpy(dtype=np.float64)
    cell_labels = measure_df["label"].to_numpy(dtype=np.int32)
    try:
        if round_spec.algorithm is ThresholdAlgorithm.GMM:
            result = group_cells_gmm(
                values,
                cell_labels,
                criterion=round_spec.gmm_criterion.value,
                max_components=round_spec.gmm_max_components,
            )
        else:
            result = group_cells_kmeans(
                values,
                cell_labels,
                n_clusters=round_spec.kmeans_n_clusters,
            )
    except Exception as e:
        logger.exception("grouping failed")
        return None, DatasetFailure.THRESHOLD_ERROR, f"grouping failed: {e}"
    if result.n_groups == 0:
        return None, DatasetFailure.THRESHOLD_EMPTY, "grouping produced 0 groups"
    return result, None, f"{result.n_groups} groups"


def threshold_compute_one(
    store: DatasetStore,
    round_spec: ThresholdingRound,
    seg_name: str = "cellpose_qc",
) -> tuple[object | None, DatasetFailure | None, str]:
    """Compute the per-cell grouping for one round on one dataset.

    Single-timepoint: returns a :class:`GroupingResult`. Time-lapse
    (``n_timepoints > 1``): groups each frame independently and returns a
    ``dict[int, GroupingResult]`` keyed by timepoint (frames with no
    groupable cells are omitted). The runner caches whatever is returned;
    :func:`apply_threshold_headless` handles both shapes.
    """
    try:
        channel_idx = _channel_index(store, round_spec.channel)
    except (KeyError, ValueError) as e:
        return None, DatasetFailure.THRESHOLD_ERROR, str(e)

    n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
    if n_timepoints > 1:
        per_frame: dict[int, GroupingResult] = {}
        for t in range(n_timepoints):
            try:
                image = _channel_from_frame(store.read_array_frame("intensity", t), channel_idx)
                labels = store.read_labels(seg_name, timepoint=t)
            except KeyError as e:
                return None, DatasetFailure.THRESHOLD_ERROR, f"missing h5 key: {e}"
            grouping, _failure, _msg = _group_image_labels(image, labels, round_spec)
            if grouping is not None:
                per_frame[t] = grouping
        if not per_frame:
            return None, DatasetFailure.THRESHOLD_EMPTY, "no groups in any timepoint"
        return per_frame, None, f"{len(per_frame)} timepoint(s) grouped"

    try:
        image = store.read_channel("intensity", channel_idx)
        labels = store.read_labels(seg_name)
    except KeyError as e:
        return None, DatasetFailure.THRESHOLD_ERROR, f"missing h5 key: {e}"
    return _group_image_labels(image, labels, round_spec)


def _apply_puncta_groups(
    smoothed: NDArray,
    labels: NDArray,
    grouping: GroupingResult,
    puncta: object,
    combined: NDArray,
    round_name: str,
) -> str:
    """Two-pass puncta detection across one frame's groups, into ``combined``.

    Two phases per frame (U6 scale calibration):
    1. Run pass-1 seed detection ONCE per group, pooling the seed sizes, and
       derive a per-dataset ``scale_range`` by bounded (narrow-only) refinement
       of the locked prior.
    2. Detect each group with the refined range and its cached pass-1 seeds
       (so pass-1 never re-runs), unioning the per-group ``{0,1}`` masks.

    Returns an error string on detector failure, else ``""``. Pass-1 results
    stay in memory — no derived array is written to or re-read from HDF5.
    """
    from percell4.domain.measure.puncta_pipeline import (
        DEFAULT_SCALE_RANGE,
        calibrate_scale_range,
        compute_seeds,
        detect_two_pass,
        seed_sigmas,
    )

    base_range = puncta.spot_scale_prior or DEFAULT_SCALE_RANGE
    groups: list[tuple[NDArray, tuple]] = []
    sigmas: list[float] = []
    for group_id in range(1, grouping.n_groups + 1):
        cells_in_group = grouping.group_assignments.index[
            grouping.group_assignments.values == group_id
        ].to_numpy(dtype=np.int32)
        if len(cells_in_group) == 0:
            continue
        group_label_mask = np.isin(labels, list(cells_in_group))
        if not group_label_mask.any():
            continue
        try:
            seeds = compute_seeds(smoothed, group_label_mask, puncta, base_range)
        except Exception as e:
            logger.exception("puncta pass-1 failed for group %d", group_id)
            return f"puncta pass-1 for group {group_id}: {e}"
        groups.append((group_label_mask, seeds))
        sigmas.extend(seed_sigmas(seeds))

    refined, clamped = calibrate_scale_range(sigmas, puncta.spot_scale_prior)
    if clamped:
        logger.warning(
            "round %s: per-dataset scale refinement fell outside the locked "
            "prior %s; clamped to %s",
            round_name,
            puncta.spot_scale_prior,
            refined,
        )

    for group_label_mask, seeds in groups:
        try:
            group_mask = detect_two_pass(
                smoothed, group_label_mask, puncta, scale_range=refined, seeds=seeds
            )
        except Exception as e:
            logger.exception("puncta detect failed")
            return f"puncta detect: {e}"
        np.maximum(combined, group_mask, out=combined)
    # Guarantee a {0,1} uint8 union (the store does not binarize).
    np.minimum(combined, 1, out=combined)
    return ""


def _apply_threshold_frame(
    image: NDArray,
    labels: NDArray,
    grouping: GroupingResult,
    round_spec: ThresholdingRound,
) -> tuple[NDArray | None, pd.DataFrame | None, str]:
    """Per-group Otsu threshold on one 2D frame.

    Returns ``(combined_mask uint8, group_df, error_message)``. On success
    ``error_message`` is empty; on Otsu failure the mask/df are ``None`` and
    the message describes the failure. The ``group_df`` has columns
    ``["label", "group_<channel>_<metric>"]`` (the same shape the interactive
    controller writes). No store writes — the caller persists.
    """
    if round_spec.gaussian_sigma > 0:
        smoothed = apply_gaussian_smoothing(image.astype(np.float32), round_spec.gaussian_sigma)
    else:
        smoothed = image.astype(np.float32)

    # Puncta mode: a configured two-pass spot detector replaces per-group Otsu.
    # A None / "otsu" sentinel keeps the legacy path byte-identical.
    puncta = round_spec.puncta
    use_puncta = puncta is not None and puncta.detector_name != "otsu"

    combined = np.zeros(labels.shape, dtype=np.uint8)
    if use_puncta:
        err = _apply_puncta_groups(smoothed, labels, grouping, puncta, combined, round_spec.name)
        if err:
            return None, None, err
    else:
        for group_id in range(1, grouping.n_groups + 1):
            cells_in_group = grouping.group_assignments.index[
                grouping.group_assignments.values == group_id
            ].to_numpy(dtype=np.int32)
            if len(cells_in_group) == 0:
                continue
            group_label_mask = np.isin(labels, list(cells_in_group))
            if not group_label_mask.any():
                continue
            group_pixels = smoothed[group_label_mask]
            if group_pixels.size == 0 or not np.isfinite(group_pixels).any():
                continue
            try:
                from skimage.filters import threshold_otsu as sk_otsu

                if np.unique(group_pixels).size < 2:
                    # Constant group — accept every pixel (safer than none).
                    group_mask = group_label_mask
                else:
                    thr = float(sk_otsu(group_pixels))
                    group_mask = group_label_mask & (smoothed >= thr)
            except Exception as e:
                logger.exception("otsu failed for group %d", group_id)
                return None, None, f"otsu for group {group_id}: {e}"
            np.maximum(combined, group_mask.astype(np.uint8), out=combined)

    col_name = f"group_{round_spec.channel}_{round_spec.metric}"
    group_df = grouping.group_assignments.reset_index()
    group_df.columns = ["label", col_name]
    return combined, group_df, ""


def apply_threshold_headless(
    store: DatasetStore,
    round_spec: ThresholdingRound,
    grouping: object,
    seg_name: str = "cellpose_qc",
) -> tuple[DatasetFailure | None, str]:
    """Headless per-group Otsu thresholding — the Phase 4 QC stand-in.

    For each group returned by :func:`threshold_compute_one`, masks the
    channel to that group's cells, smooths, computes a per-group Otsu
    threshold, and unions the per-group masks. Writes
    ``/masks/<round_spec.name>`` and ``/groups/<round_spec.name>`` — the same
    shape the interactive ``ThresholdQCController`` produces.

    Single-timepoint: ``grouping`` is a :class:`GroupingResult`, the mask is
    2D. Time-lapse (``n_timepoints > 1``): ``grouping`` is the
    ``dict[int, GroupingResult]`` from :func:`threshold_compute_one`; each
    frame is thresholded with its own grouping, masks are stacked into a
    ``(T, H, W)`` mask, and the ``/groups`` table gains a ``timepoint`` column.
    """
    try:
        channel_idx = _channel_index(store, round_spec.channel)
    except (KeyError, ValueError) as e:
        return DatasetFailure.THRESHOLD_ERROR, str(e)

    n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)

    if n_timepoints > 1:
        per_frame: dict[int, GroupingResult] = grouping  # type: ignore[assignment]
        mask_frames: list[NDArray] = []
        group_dfs: list[pd.DataFrame] = []
        for t in range(n_timepoints):
            try:
                labels = store.read_labels(seg_name, timepoint=t)
                image = _channel_from_frame(store.read_array_frame("intensity", t), channel_idx)
            except (KeyError, ValueError) as e:
                return DatasetFailure.THRESHOLD_ERROR, str(e)
            g = per_frame.get(t)
            if g is None:
                # No groups for this frame — empty mask, no group rows.
                mask_frames.append(np.zeros(labels.shape, dtype=np.uint8))
                continue
            mask, gdf, err = _apply_threshold_frame(image, labels, g, round_spec)
            if err:
                return DatasetFailure.THRESHOLD_ERROR, err
            mask_frames.append(mask)
            gdf = gdf.copy()
            gdf["timepoint"] = t
            group_dfs.append(gdf)
        combined = np.stack(mask_frames, axis=0).astype(np.uint8)  # (T, H, W)
        try:
            store.write_mask(round_spec.name, combined)
        except Exception as e:
            logger.exception("write_mask failed")
            return DatasetFailure.THRESHOLD_ERROR, f"write_mask failed: {e}"
        groups_all = (
            pd.concat(group_dfs, ignore_index=True)
            if group_dfs
            else pd.DataFrame(
                columns=["label", f"group_{round_spec.channel}_{round_spec.metric}", "timepoint"]
            )
        )
        try:
            store.write_dataframe(f"/groups/{round_spec.name}", groups_all)
        except Exception as e:
            logger.exception("write_dataframe /groups failed")
            return DatasetFailure.THRESHOLD_ERROR, f"write /groups failed: {e}"
        return None, f"{int(combined.sum())} positive pixels across {len(group_dfs)} timepoint(s)"

    # Single-timepoint path.
    try:
        image = store.read_channel("intensity", channel_idx)
        labels = store.read_labels(seg_name)
    except (KeyError, ValueError) as e:
        return DatasetFailure.THRESHOLD_ERROR, str(e)
    mask, group_df, err = _apply_threshold_frame(image, labels, grouping, round_spec)
    if err:
        return DatasetFailure.THRESHOLD_ERROR, err
    try:
        store.write_mask(round_spec.name, mask)
    except Exception as e:
        logger.exception("write_mask failed")
        return DatasetFailure.THRESHOLD_ERROR, f"write_mask failed: {e}"
    try:
        store.write_dataframe(f"/groups/{round_spec.name}", group_df)
    except Exception as e:
        logger.exception("write_dataframe /groups failed")
        return DatasetFailure.THRESHOLD_ERROR, f"write /groups failed: {e}"
    return None, f"{int(mask.sum())} positive pixels across {grouping.n_groups} groups"


def _channel_index(store: DatasetStore, channel_name: str) -> int:
    """Translate a channel name to its index in /intensity via store.metadata.

    Raises ``KeyError`` if the channel is not in the dataset.
    """
    meta = store.metadata
    names = meta.get("channel_names", [])
    names_list: list[str] = []
    for n in names:
        names_list.append(n.decode() if isinstance(n, bytes) else str(n))
    if channel_name not in names_list:
        raise KeyError(f"channel {channel_name!r} not in dataset; available: {names_list}")
    return names_list.index(channel_name)


# ── Phase 8: Summary CSV builders (U6) ──────────────────────────────────


def _is_metric_column(col: str) -> bool:
    """True if ``col`` is a numeric metric column the summary should aggregate.

    Excludes identity / cohort flag columns. Anything else numeric is
    treated as a metric — including per-round mask-overlap columns
    (``{channel}_{metric}_in_{round}`` etc.) which are legitimate
    per-cell quantities. The actual numeric-dtype check happens at the
    caller so this helper stays pure-string.
    """
    return col not in _NON_METRIC_COLUMNS and col != "dataset"


def _build_summary_groups(
    df: pd.DataFrame,
    thresholding_round_names: list[str],
) -> pd.DataFrame:
    """Per (dataset × round × group) aggregation: n_cells + metric stats.

    Synthetic rows (``is_edge_synthetic=True``) are excluded — their
    ``group_<round>`` is NaN and they must not contaminate group
    statistics. Per origin R18.

    For each thresholding round, produces one row per (dataset,
    group_label) with:
      - ``dataset``, ``round_name``, ``group_label``
      - ``n_cells`` (count of per-cell rows in that group)
      - ``fraction_of_dataset_cells`` (n_cells / total per-cell rows in
        that dataset, within this round)
      - ``<metric>_mean``, ``<metric>_median``, ``<metric>_std`` for
        every numeric metric column in ``df``
    """
    if "is_edge_synthetic" in df.columns:
        real = df[~df["is_edge_synthetic"]].copy()
    else:
        real = df.copy()

    if real.empty:
        return pd.DataFrame(
            columns=["dataset", "round_name", "group_label", "n_cells", "fraction_of_dataset_cells"]
        )

    metric_cols = [
        c
        for c in real.columns
        if _is_metric_column(c)
        and pd.api.types.is_numeric_dtype(real[c])
        # Group columns are categorical-ish; skip even if numeric.
        and not c.startswith("group_")
    ]

    frames: list[pd.DataFrame] = []
    for round_name in thresholding_round_names:
        col = f"group_{round_name}"
        if col not in real.columns:
            continue
        # Cells without a group assignment (NaN) are dropped.
        grouped = real.dropna(subset=[col]).groupby(["dataset", col], observed=True)
        if grouped.ngroups == 0:
            continue
        counts = grouped.size().rename("n_cells").reset_index()
        counts = counts.rename(columns={col: "group_label"})

        # Fraction within (dataset) — total real cells per dataset
        # across all groups in this round (not the dataset's grand
        # total) so per-round fractions sum to 1.0.
        per_dataset_total = counts.groupby("dataset", observed=True)["n_cells"].transform("sum")
        counts["fraction_of_dataset_cells"] = counts["n_cells"] / per_dataset_total

        if metric_cols:
            stats = grouped[metric_cols].agg(["mean", "median", "std"])
            # Flatten MultiIndex columns: (metric, stat) → metric_stat
            stats.columns = [f"{m}_{s}" for m, s in stats.columns]
            stats = stats.reset_index().rename(columns={col: "group_label"})
            counts = counts.merge(stats, on=["dataset", "group_label"], how="left")

        counts.insert(1, "round_name", round_name)
        frames.append(counts)

    if not frames:
        return pd.DataFrame(
            columns=["dataset", "round_name", "group_label", "n_cells", "fraction_of_dataset_cells"]
        )
    return pd.concat(frames, ignore_index=True)


def _build_summary_datasets(
    df: pd.DataFrame,
    config: WorkflowConfig,
    metadata: RunMetadata,
) -> pd.DataFrame:
    """Per-dataset run audit: counts, edge_mode, dilute round counts, failures.

    One row per dataset in ``config.datasets``. Columns:
      - ``dataset``, ``source`` (original — TIFF_PENDING is rewritten
        as ``compressed_from_tiff`` to match the brainstorm's user-facing
        encoding)
      - ``n_cells_total``, ``n_cells_whole``, ``n_cells_edge`` (real
        cells only — synthetic rows excluded from the totals)
      - ``n_rounds_thresholding`` (len of ``config.thresholding_rounds``)
      - ``n_rounds_dilute`` (per-dataset count from metadata; NaN if
        dilute is disabled)
      - ``dilute_enabled`` (bool — True iff ``config.dilute_settings`` is set)
      - ``edge_mode`` (the run-wide value, broadcast per row for
        readability)
      - ``failure_reason`` (semicolon-joined messages for any failures
        recorded against this dataset; NaN if none)

    Per origin R19.
    """
    rows: list[dict[str, Any]] = []
    failures_by_ds: dict[str, list[str]] = {}
    for f in metadata.failures:
        failures_by_ds.setdefault(f.dataset_name, []).append(f"{f.phase_name}: {f.message}")

    dilute_enabled = config.dilute_settings is not None
    n_rounds_thresholding = len(config.thresholding_rounds)
    edge_mode_value = config.edge_mode.value

    if "is_edge_synthetic" in df.columns:
        real = df[~df["is_edge_synthetic"]]
    else:
        real = df

    # Group real cells by dataset once.
    if not real.empty and "dataset" in real.columns:
        counts_by_ds = (
            real.groupby("dataset", observed=True)
            .agg(
                n_cells_total=("label", "size"),
                n_cells_whole=("is_edge", lambda s: int((~s).sum())),
                n_cells_edge=("is_edge", lambda s: int(s.sum())),
            )
            .to_dict("index")
        )
    else:
        counts_by_ds = {}

    for ds_entry in config.datasets:
        name = ds_entry.name
        counts = counts_by_ds.get(name, {})
        source_str = (
            "compressed_from_tiff"
            if ds_entry.source == DatasetSource.TIFF_PENDING
            else ds_entry.source.value
        )
        row: dict[str, Any] = {
            "dataset": name,
            "source": source_str,
            "n_cells_total": int(counts.get("n_cells_total", 0)),
            "n_cells_whole": int(counts.get("n_cells_whole", 0)),
            "n_cells_edge": int(counts.get("n_cells_edge", 0)),
            "n_rounds_thresholding": n_rounds_thresholding,
            "n_rounds_dilute": (
                metadata.per_dataset_dilute_round_counts.get(name, 0) if dilute_enabled else None
            ),
            "dilute_enabled": dilute_enabled,
            "edge_mode": edge_mode_value,
            "failure_reason": ("; ".join(failures_by_ds[name]) if name in failures_by_ds else None),
        }
        rows.append(row)

    return pd.DataFrame(rows)


# ── Phase 7: Measure ────────────────────────────────────────────────────


# Columns that are identity / cohort flags, NOT metrics — excluded from the
# synthetic-row aggregation. ``label`` is the post-relabel sequential ID;
# ``cell_id`` is the same value (carried forward for parquet identity).
_NON_METRIC_COLUMNS = frozenset({"label", "cell_id", "is_edge", "is_edge_synthetic"})


def _read_pixel_size_um(store: DatasetStore) -> float | None:
    """Read /metadata.pixel_size_um from the store, or None if absent / invalid.

    Persisted by ``import_dataset`` from the first source TIFF's resolution
    tag. Returns ``None`` when:
      - the dataset was imported before this metadata was persisted, OR
      - the source TIFFs didn't carry a resolution tag, OR
      - the persisted value is non-positive (defensive guard).
    """
    try:
        meta = store.metadata
    except Exception:
        return None
    raw = meta.get("pixel_size_um")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _resolve_min_area_px(
    particle_settings: ParticleSettings,
    pixel_size_um: float | None,
    *,
    dataset_name: str = "",
) -> int:
    """Convert a ParticleSettings.min_area into an integer pixel threshold.

    px mode is a straight ``int(round(value))``. µm² mode divides by the
    dataset's pixel area; a µm² threshold against a dataset without a
    known pixel size raises ``ValueError`` so the workflow phase can
    record the failure rather than silently default to ``pixel_size_um=1``
    (which would produce a threshold orders of magnitude off).
    """
    unit = particle_settings.min_area_unit
    value = float(particle_settings.min_area)
    if unit == "px":
        return int(round(value))
    if unit == "um2":
        if pixel_size_um is None or pixel_size_um <= 0:
            label = f" for dataset {dataset_name!r}" if dataset_name else ""
            raise ValueError(
                f"µm² particle threshold requires a known pixel size{label}; "
                "re-import the dataset with TIFF resolution metadata or "
                "switch the workflow Min particle area unit to px."
            )
        return int(round(value / (pixel_size_um * pixel_size_um)))
    # __post_init__ guards against unknown units, but stay defensive.
    raise ValueError(f"unknown min_area_unit: {unit!r}")


def _add_area_um2_columns(df: pd.DataFrame, pixel_size_um: float | None) -> pd.DataFrame:
    """Emit `<area_col>_um2` sibling columns for every area column in ``df``.

    No-ops when ``pixel_size_um`` is ``None``. Otherwise, for every column
    whose name is exactly ``area`` OR ends with ``_area`` OR contains
    ``_area_in_``, add a sibling ``<name>_um2`` column whose values are
    the pixel-count area multiplied by ``pixel_size_um ** 2``.

    Idempotent — running twice doesn't double-add. Pixel-side columns
    are preserved alongside (lossless).
    """
    if pixel_size_um is None or pixel_size_um <= 0:
        return df

    factor = pixel_size_um * pixel_size_um
    new_cols: dict[str, NDArray] = {}
    for col in df.columns:
        if col.endswith("_um2"):
            continue
        is_area = (
            col == "area"
            or col.endswith("_area")
            or "_area_in_" in col
            or col.endswith("_particle_area")
        )
        if not is_area:
            continue
        sibling = f"{col}_um2"
        if sibling in df.columns:
            continue
        # Multiply numeric values; non-numeric columns (shouldn't happen
        # for area) are left as-is via pandas' default float coercion.
        try:
            new_cols[sibling] = df[col].astype(float) * factor
        except Exception:
            logger.exception("failed to compute %s from %s — skipping", sibling, col)

    if not new_cols:
        return df

    # Insert each um2 column immediately after its pixel-side sibling
    # so the parquet/CSV columns stay readable left-to-right.
    result = df.copy()
    for col_name, values in new_cols.items():
        result[col_name] = values
    # Reorder so each _um2 column sits next to its source.
    ordered: list[str] = []
    for col in df.columns:
        ordered.append(col)
        sibling = f"{col}_um2"
        if sibling in new_cols:
            ordered.append(sibling)
    return result[ordered]


def _append_synthetic_row(
    df: pd.DataFrame,
    edge_label_set: set[int],
    edge_mode: EdgeMode,
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Append the size-normalized edge-cohort synthetic row when applicable.

    Origin R7: ``N_theoretical = sum(edge_areas) / mean(whole_areas)``;
    for each metric column M, ``synthetic_M = nansum(M across edge cells)
    / N_theoretical``. ``sum(M)/N_theoretical`` is intentional — density-
    based extrapolation, not a sample mean. See plan U4.

    Returns ``(df, None, "")`` unchanged in these cases:
    - ``edge_mode`` is not ``INCLUDE_AS_SIZE_NORMALIZED_COHORT`` (most runs).
    - The dataset has zero edge cells (R10a). No synthetic row needed.

    Returns ``(df, DatasetFailure.MEASUREMENT_ERROR, msg)`` and skips the
    append when the dataset has zero whole cells (R10b). The per-cell df
    is returned unchanged so the runner can still stage the dataset's
    rows; the failure record marks the dataset for the summary CSV's
    ``failure_reason`` column. AE2.

    NaN policy: ``nansum`` so a single NaN in any edge cell's metric
    column does not blank the entire synthetic value (Tier 2 doc-review
    finding). Mask-overlap columns (``<channel>_<metric>_in_<round>``,
    ``..._out_<round>``) are treated as numeric metrics — the same
    ``sum/N_theoretical`` formula applies uniformly per origin R7.
    """
    if edge_mode != EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT:
        return df, None, ""

    if "is_edge" not in df.columns or "label" not in df.columns:
        # measure_one's caller must populate these before invoking this
        # helper — a programming error rather than a runtime failure.
        return df, None, ""

    if not edge_label_set:
        # R10a: no edge cells in this dataset, no synthetic row.
        return df, None, ""

    edge_rows = df[df["is_edge"]]
    whole_rows = df[~df["is_edge"]]

    if whole_rows.empty:
        # R10b / AE2: cannot compute A_mean for normalization.
        return (
            df,
            DatasetFailure.MEASUREMENT_ERROR,
            "no whole cells to compute A_mean for edge-cohort normalization",
        )

    if "area" not in df.columns:
        # Defensive: every per-cell row should carry area (core column).
        return df, None, ""

    a_mean = float(np.nanmean(whole_rows["area"]))
    if not np.isfinite(a_mean) or a_mean <= 0:
        return (
            df,
            DatasetFailure.MEASUREMENT_ERROR,
            "whole-cell mean area is non-positive or non-finite — cannot normalize",
        )

    edge_area_sum = float(np.nansum(edge_rows["area"]))
    n_theoretical = edge_area_sum / a_mean
    if n_theoretical <= 0:
        return df, None, ""  # nothing to spread across

    # Build the synthetic row. Numeric metric columns get
    # nansum / N_theoretical; identity / cohort flags get explicit
    # values; non-numeric columns get the column's null.
    synthetic: dict[str, Any] = {}
    for col in df.columns:
        if col in _NON_METRIC_COLUMNS:
            continue
        # Only aggregate numeric columns. Object / categorical / group
        # columns are left out of the synthetic row (group_<round>
        # columns will be NaN after the existing left-merge — natural
        # behavior with the synthetic label of -1).
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        synthetic[col] = float(np.nansum(edge_rows[col])) / n_theoretical

    synthetic["label"] = -1
    synthetic["cell_id"] = -1
    synthetic["is_edge"] = False
    synthetic["is_edge_synthetic"] = True

    df_out = pd.concat([df, pd.DataFrame([synthetic])], ignore_index=True)
    return (
        df_out,
        None,
        f"appended edge-cohort synthetic row (n_edge={len(edge_rows)}, "
        f"n_theoretical={n_theoretical:.2f})",
    )


def _images_from_plane(plane: NDArray, channel_names: list[str]) -> dict[str, NDArray]:
    """Build a ``{channel_name: 2D image}`` dict from one intensity plane.

    ``plane`` is ``(H, W)`` (single channel) or ``(C, H, W)`` (multichannel)
    — for a time-lapse dataset this is one timepoint's frame. Raises
    ``ValueError`` on an unexpected rank.
    """
    images: dict[str, NDArray] = {}
    if plane.ndim == 2:
        images[channel_names[0] if channel_names else "ch0"] = plane
    elif plane.ndim == 3:
        for i, name in enumerate(channel_names):
            if i < plane.shape[0]:
                images[name] = plane[i]
    else:
        raise ValueError(f"unexpected intensity ndim: {plane.ndim}")
    return images


def _merge_group_dfs(df: pd.DataFrame, group_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Left-merge each round's ``group_<round>`` column onto ``df`` by label.

    Each ``group_df`` has columns ``["label", "group_<channel>_<metric>"]``;
    the metric column is renamed to ``group_<round_name>``.
    """
    for round_name, g_df in group_dfs.items():
        cols = list(g_df.columns)
        if len(cols) != 2 or cols[0] != "label":
            logger.warning("unexpected group_df schema for %s: %s", round_name, cols)
            continue
        g_df = g_df.rename(columns={cols[1]: f"group_{round_name}"})
        df = df.merge(g_df, on="label", how="left")
    return df


def _join_lineage_columns(store: DatasetStore, seg_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Attach track_id + lineage columns when ``seg_name`` is a tracked layer.

    The tracked segmentation's label value IS the (1-based) track id, so
    ``track_id == label``; ``tree_id`` / ``parent_track_id`` are joined from
    ``/tracks/<seg_name>``. A raw (untracked) segmentation is left unchanged
    (only the ``timepoint`` column from the caller).
    """
    if "label" not in df.columns:
        return df
    try:
        tracks = store.read_tracks(seg_name)
    except KeyError:
        return df
    df = df.copy()
    df["track_id"] = df["label"]
    if tracks is not None and not tracks.empty:
        tree = dict(zip(tracks["track_id"], tracks["tree_id"]))
        parent = dict(zip(tracks["track_id"], tracks["parent_track_id"]))
        df["tree_id"] = df["track_id"].map(tree)
        df["parent_track_id"] = df["track_id"].map(parent)
    return df


def _measure_frame(
    images: dict[str, NDArray],
    labels: NDArray,
    round_masks: dict[str, NDArray],
    group_dfs: dict[str, pd.DataFrame],
    metric_names: list[str],
    edge_mode: EdgeMode,
    edge_margin_px: int,
    particle_settings: ParticleSettings | None,
    pixel_size_um: float | None,
    run_log: RunLog | None,
    dataset_name: str,
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Measure one 2D frame: channels × metrics × round masks, + identity/cohort.

    The per-frame core shared by single-timepoint and time-lapse
    measurement. ``group_dfs`` are 2-column ``[label, group_col]`` (the
    caller slices the timepoint out for time-lapse). Returns
    ``(df, failure, message)``; on a soft zero-whole-cells edge-cohort
    failure the df is preserved (group columns merged) so the caller can
    still stage rows.
    """
    try:
        df = measure_multichannel_with_masks(
            images=images,
            labels=labels,
            metrics=metric_names,
            masks=round_masks,
        )
    except Exception as e:
        logger.exception("measure_multichannel_with_masks failed")
        return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, f"measure failed: {e}"

    # Keep only "_in_<round>" mask-overlap columns, drop "_out_<round>".
    out_cols = [c for c in df.columns if "_out_" in c]
    if out_cols:
        df = df.drop(columns=out_cols)

    df["cell_id"] = df["label"]
    edge_label_set = get_edge_labels(labels.astype(np.int32), edge_margin=edge_margin_px)
    df["is_edge"] = df["label"].isin(edge_label_set)
    df["is_edge_synthetic"] = False

    if particle_settings is not None:
        from percell4.domain.measure.particle import analyze_particles

        try:
            resolved_min_area_px = _resolve_min_area_px(
                particle_settings,
                pixel_size_um,
                dataset_name=dataset_name,
            )
        except ValueError as e:
            logger.error("particle threshold resolve failed: %s", e)
            if run_log is not None:
                run_log.log(
                    phase="measure",
                    dataset=dataset_name,
                    event="min_area_resolve_failed",
                    min_area_value=float(particle_settings.min_area),
                    min_area_unit=particle_settings.min_area_unit,
                    pixel_size_um=pixel_size_um,
                    error=str(e),
                )
            return (
                pd.DataFrame(),
                DatasetFailure.MEASUREMENT_ERROR,
                f"particle threshold resolve failed: {e}",
            )
        if run_log is not None:
            run_log.log(
                phase="measure",
                dataset=dataset_name,
                event="min_area_resolved",
                min_area_value=float(particle_settings.min_area),
                min_area_unit=particle_settings.min_area_unit,
                pixel_size_um=pixel_size_um,
                resolved_min_area_px=resolved_min_area_px,
            )
        for round_name, round_mask in round_masks.items():
            try:
                particle_summary = analyze_particles(
                    images=images,
                    labels=labels,
                    mask=round_mask,
                    min_area=resolved_min_area_px,
                )
            except Exception:
                logger.exception("analyze_particles failed for round %s — skipping", round_name)
                continue
            if particle_summary.empty:
                continue
            rename_map = {c: f"{round_name}_{c}" for c in particle_summary.columns if c != "label"}
            particle_summary = particle_summary.rename(columns=rename_map)
            df = df.merge(particle_summary, on="label", how="left")

    df, edge_failure, edge_msg = _append_synthetic_row(df, edge_label_set, edge_mode)
    if edge_failure is not None:
        df = _merge_group_dfs(df, group_dfs)
        return df, edge_failure, edge_msg

    df = _merge_group_dfs(df, group_dfs)
    df = _add_area_um2_columns(df, pixel_size_um)
    return df, None, f"{len(df)} cells, {len(df.columns)} columns"


def measure_one(
    store: DatasetStore,
    round_specs: list[ThresholdingRound],
    metric_names: list[str] | None = None,
    edge_mode: EdgeMode = EdgeMode.EXCLUDE,
    edge_margin_px: int = 0,
    seg_name: str = "cellpose_qc",
    particle_settings: ParticleSettings | None = None,
    run_log: RunLog | None = None,
    dataset_name: str = "",
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Measure one dataset: all channels × all metrics × all round masks.

    Single-timepoint: one frame, no ``timepoint`` column (output unchanged).
    Time-lapse (``n_timepoints > 1``): measures every timepoint, tags each row
    with ``timepoint``, and — for a tracked segmentation — joins
    ``track_id``/``tree_id``/``parent_track_id`` from ``/tracks``. A frame with
    no cells contributes no rows (per-timepoint counts may differ).

    Adds the per-cell identity / cohort columns ``cell_id``, ``is_edge``,
    ``is_edge_synthetic`` per frame. Returns an empty DataFrame on a hard
    failure; for the soft zero-whole-cells case the per-cell df is returned
    with a recorded failure (single-timepoint only).
    """
    metric_names = metric_names or sorted(BUILTIN_METRICS.keys())
    pixel_size_um = _read_pixel_size_um(store)
    try:
        meta = store.metadata
        channel_names = [
            n.decode() if isinstance(n, bytes) else str(n) for n in meta.get("channel_names", [])
        ]
        n_timepoints = int(meta.get("n_timepoints", 1) or 1)
    except Exception as e:
        logger.exception("measure_one metadata read failed")
        return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, f"metadata read failed: {e}"

    def _read_round_layers(s, timepoint: int | None):
        round_masks: dict[str, NDArray] = {}
        group_dfs: dict[str, pd.DataFrame] = {}
        for round_spec in round_specs:
            try:
                round_masks[round_spec.name] = (
                    s.read_mask(round_spec.name, timepoint=timepoint)
                    if timepoint is not None
                    else s.read_mask(round_spec.name)
                )
            except KeyError:
                logger.info(
                    "dataset missing mask /masks/%s — skipping from measure",
                    round_spec.name,
                )
                continue
            try:
                g_df = s.read_dataframe(f"/groups/{round_spec.name}")
                if timepoint is not None and "timepoint" in g_df.columns:
                    g_df = g_df[g_df["timepoint"] == timepoint].drop(columns=["timepoint"])
                group_dfs[round_spec.name] = g_df
            except KeyError:
                logger.info(
                    "dataset missing /groups/%s — group column won't be added",
                    round_spec.name,
                )
        return round_masks, group_dfs

    if n_timepoints > 1:
        frames: list[pd.DataFrame] = []
        for t in range(n_timepoints):
            try:
                with store.open_read() as s:
                    plane = s.read_array_frame("intensity", t)
                    labels = s.read_labels(seg_name, timepoint=t)
                    round_masks, group_dfs = _read_round_layers(s, t)
            except Exception as e:
                logger.exception("measure_one read session failed (t=%d)", t)
                return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, f"read session failed: {e}"
            if int(labels.max()) == 0:
                continue  # frame with no cells: death / exit / pre-birth
            try:
                images = _images_from_plane(plane, channel_names)
            except ValueError as e:
                return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, str(e)
            df_t, _failure, _msg = _measure_frame(
                images,
                labels,
                round_masks,
                group_dfs,
                metric_names,
                edge_mode,
                edge_margin_px,
                particle_settings,
                pixel_size_um,
                run_log,
                dataset_name,
            )
            if df_t.empty:
                continue
            df_t = df_t.copy()
            df_t["timepoint"] = t
            frames.append(df_t)
        if not frames:
            return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, "no cells in any timepoint"
        df = pd.concat(frames, ignore_index=True)
        df = _join_lineage_columns(store, seg_name, df)
        return df, None, f"{len(df)} cells across {df['timepoint'].nunique()} timepoints"

    # Single-timepoint (output unchanged from the historical path).
    try:
        with store.open_read() as s:
            intensity = s.read_array("intensity")
            labels = s.read_labels(seg_name)
            round_masks, group_dfs = _read_round_layers(s, None)
    except Exception as e:
        logger.exception("measure_one read session failed")
        return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, f"read session failed: {e}"

    if int(labels.max()) == 0:
        return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, "empty labels — nothing to measure"
    try:
        images = _images_from_plane(intensity, channel_names)
    except ValueError as e:
        return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, str(e)
    return _measure_frame(
        images,
        labels,
        round_masks,
        group_dfs,
        metric_names,
        edge_mode,
        edge_margin_px,
        particle_settings,
        pixel_size_um,
        run_log,
        dataset_name,
    )


def write_staging_parquet(run_folder: Path, dataset_name: str, df: pd.DataFrame) -> Path:
    """Write a dataset's measurement DataFrame to ``run_folder/staging/``.

    The staging parquet is an intermediate artifact that :func:`export_run`
    concatenates at the end. On successful export the staging folder is
    deleted.
    """
    staging_dir = run_folder / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    # Prefix with dataset name so rows can be attributed during concat.
    df_out = df.copy()
    df_out.insert(0, "dataset", dataset_name)
    path = staging_dir / f"{dataset_name}.parquet"
    df_out.to_parquet(path, engine="pyarrow", index=False, compression="snappy")
    return path


# ── U7 particle analysis ────────────────────────────────────────────────


def _particles_detail_frame(
    images: dict[str, NDArray],
    labels: NDArray,
    round_masks: dict[str, NDArray],
    min_area_px: int,
) -> pd.DataFrame:
    """Per-particle detail across rounds for one 2D frame (no um2, no timepoint).

    Returns the combined per-particle DataFrame with a ``round_name`` column,
    or an empty DataFrame when no particles are detected.
    """
    from percell4.domain.measure.particle import analyze_particles_detail

    frames: list[pd.DataFrame] = []
    for round_name, round_mask in round_masks.items():
        try:
            detail = analyze_particles_detail(
                images=images,
                labels=labels,
                mask=round_mask,
                min_area=min_area_px,
            )
        except Exception:
            logger.exception("analyze_particles_detail failed for round %s — skipping", round_name)
            continue
        if detail.empty:
            continue
        detail = detail.copy()
        detail.insert(0, "round_name", round_name)
        frames.append(detail)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _measure_particles_timelapse(
    store: DatasetStore,
    round_specs: list[ThresholdingRound],
    particle_settings: ParticleSettings,
    seg_name: str,
    run_log: RunLog | None,
    dataset_name: str,
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Per-particle detail for a time-lapse dataset, tagged with ``timepoint``."""
    pixel_size_um = _read_pixel_size_um(store)
    try:
        resolved_min_area_px = _resolve_min_area_px(
            particle_settings,
            pixel_size_um,
            dataset_name=dataset_name,
        )
    except ValueError as e:
        logger.error("particle threshold resolve failed: %s", e)
        if run_log is not None:
            run_log.log(
                phase="particles",
                dataset=dataset_name,
                event="min_area_resolve_failed",
                min_area_value=float(particle_settings.min_area),
                min_area_unit=particle_settings.min_area_unit,
                pixel_size_um=pixel_size_um,
                error=str(e),
            )
        return (
            pd.DataFrame(),
            DatasetFailure.MEASUREMENT_ERROR,
            f"particle threshold resolve failed: {e}",
        )

    channel_names = [
        n.decode() if isinstance(n, bytes) else str(n)
        for n in store.metadata.get("channel_names", [])
    ]
    n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)

    out_frames: list[pd.DataFrame] = []
    for t in range(n_timepoints):
        try:
            with store.open_read() as s:
                plane = s.read_array_frame("intensity", t)
                labels = s.read_labels(seg_name, timepoint=t)
                round_masks: dict[str, NDArray] = {}
                for round_spec in round_specs:
                    try:
                        round_masks[round_spec.name] = s.read_mask(round_spec.name, timepoint=t)
                    except KeyError:
                        continue
        except Exception as e:
            logger.exception("measure_particles_one read session failed (t=%d)", t)
            return pd.DataFrame(), DatasetFailure.MEASUREMENT_ERROR, f"read session failed: {e}"
        if not round_masks or int(labels.max()) == 0:
            continue
        images = _images_from_plane(plane, channel_names)
        detail = _particles_detail_frame(images, labels, round_masks, resolved_min_area_px)
        if detail.empty:
            continue
        detail["timepoint"] = t
        out_frames.append(detail)

    if not out_frames:
        return pd.DataFrame(), None, "no particles detected in any timepoint"
    combined = pd.concat(out_frames, ignore_index=True)
    combined = _add_area_um2_columns(combined, pixel_size_um)
    return (
        combined,
        None,
        f"{len(combined)} particles across {combined['timepoint'].nunique()} timepoints",
    )


def measure_particles_one(
    store: DatasetStore,
    round_specs: list[ThresholdingRound],
    particle_settings: ParticleSettings,
    seg_name: str = "cellpose_qc",
    run_log: RunLog | None = None,
    dataset_name: str = "",
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Per-particle detail rows for one dataset across every round.

    Re-reads the dataset's intensity cube, labels, and each round's mask,
    then calls :func:`analyze_particles_detail` per round. Returns one
    combined DataFrame whose columns are:

    - ``round_name`` (added here)
    - ``cell_id``, ``particle_id``, ``area``, ``centroid_y``, ``centroid_x``
    - ``{channel}_mean_intensity``, ``{channel}_integrated_intensity``
      for every channel

    Time-lapse datasets detail every timepoint (each row tagged
    ``timepoint``). Rounds whose mask is missing are skipped silently
    (consistent with ``measure_one``). On read error returns an empty
    DataFrame + failure.
    """
    if int(store.metadata.get("n_timepoints", 1) or 1) > 1:
        return _measure_particles_timelapse(
            store, round_specs, particle_settings, seg_name, run_log, dataset_name
        )
    try:
        with store.open_read() as s:
            intensity = s.read_array("intensity")
            labels = s.read_labels(seg_name)
            meta = s.metadata
            channel_names_raw = meta.get("channel_names", [])
            channel_names = [
                n.decode() if isinstance(n, bytes) else str(n) for n in channel_names_raw
            ]

            images: dict[str, NDArray] = {}
            if intensity.ndim == 2:
                name = channel_names[0] if channel_names else "ch0"
                images[name] = intensity
            elif intensity.ndim == 3:
                for i, name in enumerate(channel_names):
                    if i < intensity.shape[0]:
                        images[name] = intensity[i]
            else:
                return (
                    pd.DataFrame(),
                    DatasetFailure.MEASUREMENT_ERROR,
                    f"unexpected intensity ndim: {intensity.ndim}",
                )

            round_masks: dict[str, NDArray[np.uint8]] = {}
            for round_spec in round_specs:
                try:
                    round_masks[round_spec.name] = s.read_mask(round_spec.name)
                except KeyError:
                    continue
    except Exception as e:
        logger.exception("measure_particles_one read session failed")
        return (
            pd.DataFrame(),
            DatasetFailure.MEASUREMENT_ERROR,
            f"read session failed: {e}",
        )

    if not round_masks:
        return pd.DataFrame(), None, "no round masks present — nothing to detail"
    if int(labels.max()) == 0:
        return pd.DataFrame(), None, "empty labels — no particles to detail"

    # Resolve min_area once per dataset using its own pixel_size_um.
    # µm² mode against a dataset without a known pixel size fails
    # explicitly rather than silently default.
    pixel_size_um = _read_pixel_size_um(store)
    try:
        resolved_min_area_px = _resolve_min_area_px(
            particle_settings,
            pixel_size_um,
            dataset_name=dataset_name,
        )
    except ValueError as e:
        logger.error("particle threshold resolve failed: %s", e)
        if run_log is not None:
            run_log.log(
                phase="particles",
                dataset=dataset_name,
                event="min_area_resolve_failed",
                min_area_value=float(particle_settings.min_area),
                min_area_unit=particle_settings.min_area_unit,
                pixel_size_um=pixel_size_um,
                error=str(e),
            )
        return (
            pd.DataFrame(),
            DatasetFailure.MEASUREMENT_ERROR,
            f"particle threshold resolve failed: {e}",
        )
    logger.info(
        "particle min_area resolved: %.4f %s -> %d px (pixel_size_um=%s)",
        particle_settings.min_area,
        particle_settings.min_area_unit,
        resolved_min_area_px,
        pixel_size_um,
    )
    if run_log is not None:
        run_log.log(
            phase="particles",
            dataset=dataset_name,
            event="min_area_resolved",
            min_area_value=float(particle_settings.min_area),
            min_area_unit=particle_settings.min_area_unit,
            pixel_size_um=pixel_size_um,
            resolved_min_area_px=resolved_min_area_px,
        )

    from percell4.domain.measure.particle import analyze_particles_detail

    frames: list[pd.DataFrame] = []
    for round_name, round_mask in round_masks.items():
        try:
            detail = analyze_particles_detail(
                images=images,
                labels=labels,
                mask=round_mask,
                min_area=resolved_min_area_px,
            )
        except Exception:
            logger.exception(
                "analyze_particles_detail failed for round %s — skipping",
                round_name,
            )
            continue
        if detail.empty:
            continue
        detail = detail.copy()
        detail.insert(0, "round_name", round_name)
        frames.append(detail)

    if not frames:
        return pd.DataFrame(), None, "no particles detected in any round"

    combined = pd.concat(frames, ignore_index=True)

    # Each per-particle row carries an ``area`` column. Emit an
    # ``area_um2`` sibling when /metadata.pixel_size_um is available.
    combined = _add_area_um2_columns(combined, pixel_size_um)

    return combined, None, f"{len(combined)} particles across {len(frames)} rounds"


def write_staging_particles_parquet(run_folder: Path, dataset_name: str, df: pd.DataFrame) -> Path:
    """Write a dataset's per-particle DataFrame to ``run_folder/staging_particles/``.

    Sibling of :func:`write_staging_parquet`. :func:`export_run`
    concatenates these into ``particles.parquet`` / ``particles.csv``
    when present, alongside the existing per-cell artifacts.
    """
    staging_dir = run_folder / "staging_particles"
    staging_dir.mkdir(parents=True, exist_ok=True)
    df_out = df.copy()
    df_out.insert(0, "dataset", dataset_name)
    path = staging_dir / f"{dataset_name}.parquet"
    df_out.to_parquet(path, engine="pyarrow", index=False, compression="snappy")
    return path


# ── Phase 8: Export ─────────────────────────────────────────────────────


def _ordered_csv_columns(
    df: pd.DataFrame,
    selected_csv_columns: list[str],
    extra_identity: tuple[str, ...] = (),
) -> list[str]:
    """Identity columns + user-selected columns, de-duplicated, order-preserving.

    Identity columns (``dataset``, ``cell_id``, ``label``, plus any
    ``extra_identity`` such as the tracking columns) come first and are always
    kept when present in ``df``, even if absent from ``selected_csv_columns``.
    The user-selected columns follow in their configured order. Any column not
    present in ``df`` is dropped. Shared by ``combined.csv`` and
    ``complete_tracks.csv`` so both honour the column selection identically.
    """
    identity = ["dataset", "cell_id", "label", *extra_identity]
    ordered: list[str] = []
    seen: set[str] = set()
    for c in [*identity, *selected_csv_columns]:
        if c in df.columns and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def export_run(
    run_folder: Path,
    config: WorkflowConfig,
    metadata: RunMetadata,
) -> tuple[DatasetFailure | None, str]:
    """Aggregate per-dataset staging parquets into the final run artifacts.

    Produces, in ``run_folder/``:
      - ``measurements.parquet`` — the full cross-dataset DataFrame
        (every metric on every channel, plus per-round columns), with
        ``dataset`` as a categorical column
      - ``combined.csv`` — user-selected columns + identity columns
        (``dataset``, ``cell_id``, ``label``)
      - ``per_dataset/<name>.csv`` — one CSV per dataset with the same
        selected columns; no ``dataset`` column since the filename is
        the identifier

    Deletes ``staging/`` on success.
    """
    staging_dir = run_folder / "staging"
    if not staging_dir.is_dir():
        return (
            DatasetFailure.MEASUREMENT_ERROR,
            f"staging/ missing: {staging_dir}",
        )

    staging_files = sorted(staging_dir.glob("*.parquet"))
    if not staging_files:
        return (
            DatasetFailure.MEASUREMENT_ERROR,
            "no staging parquets to concatenate",
        )

    try:
        import pyarrow.dataset as pa_ds

        ds = pa_ds.dataset([str(p) for p in staging_files], format="parquet")
        table = ds.to_table()
        df = table.to_pandas(types_mapper=None)
    except Exception as e:
        logger.exception("staging concat failed")
        return DatasetFailure.MEASUREMENT_ERROR, f"staging concat failed: {e}"

    if len(df) == 0:
        return DatasetFailure.MEASUREMENT_ERROR, "aggregated DataFrame is empty"

    # Categorical dataset column saves memory + guarantees dictionary encoding.
    if "dataset" in df.columns:
        df["dataset"] = pd.Categorical(df["dataset"])

    # Downcast float64 → float32 where lossless. Skip if the column has
    # anything NaN-y that numpy.all(finite) can't evaluate safely.
    for col in df.select_dtypes(include="float64").columns:
        try:
            df[col] = pd.to_numeric(df[col], downcast="float")
        except Exception:
            pass

    # Write measurements.parquet (full fidelity, snappy, row groups of 100k)
    measurements_path = run_folder / "measurements.parquet"
    try:
        df.to_parquet(
            measurements_path,
            engine="pyarrow",
            compression="snappy",
            index=False,
            row_group_size=100_000,
            use_dictionary=True,
        )
    except Exception as e:
        logger.exception("measurements.parquet write failed")
        return (
            DatasetFailure.MEASUREMENT_ERROR,
            f"measurements.parquet write failed: {e}",
        )

    # Build the CSV export subset.
    csv_cols = _ordered_csv_columns(df, config.selected_csv_columns)

    combined_csv = run_folder / "combined.csv"
    try:
        df.to_csv(
            combined_csv,
            columns=csv_cols,
            index=False,
            float_format="%.6g",
            na_rep="",
            encoding="utf-8",
            lineterminator="\n",
        )
    except Exception as e:
        logger.exception("combined.csv write failed")
        return DatasetFailure.MEASUREMENT_ERROR, f"combined.csv write failed: {e}"

    # Per-dataset CSVs: same columns as combined, minus the dataset column
    # (since the filename is the identifier).
    per_dataset_dir = run_folder / "per_dataset"
    per_dataset_dir.mkdir(parents=True, exist_ok=True)
    per_dataset_cols = [c for c in csv_cols if c != "dataset"]
    for ds_name, ds_df in df.groupby("dataset", observed=True):
        out = per_dataset_dir / f"{ds_name}.csv"
        try:
            ds_df.to_csv(
                out,
                columns=per_dataset_cols,
                index=False,
                float_format="%.6g",
                na_rep="",
                encoding="utf-8",
                lineterminator="\n",
            )
        except Exception as e:
            logger.exception("per_dataset/%s.csv failed", ds_name)
            return (
                DatasetFailure.MEASUREMENT_ERROR,
                f"per_dataset/{ds_name}.csv failed: {e}",
            )

    # Summary CSVs (U6) — derived from the same in-memory df.
    # Failures here are recorded but do NOT abort the run: the
    # measurements.parquet and CSVs have already landed.
    round_names = [r.name for r in config.thresholding_rounds]
    try:
        summary_groups = _build_summary_groups(df, round_names)
        write_atomic(
            run_folder / "summary_groups.csv",
            lambda tmp: summary_groups.to_csv(
                tmp,
                index=False,
                float_format="%.6g",
                na_rep="",
                encoding="utf-8",
                lineterminator="\n",
            ),
        )
    except Exception as e:
        logger.exception("summary_groups.csv write failed")
        record_failure(
            metadata,
            dataset_name="<export>",
            phase_name="summary_groups",
            failure=DatasetFailure.MEASUREMENT_ERROR,
            message=str(e),
        )

    try:
        summary_datasets = _build_summary_datasets(df, config, metadata)
        write_atomic(
            run_folder / "summary_datasets.csv",
            lambda tmp: summary_datasets.to_csv(
                tmp,
                index=False,
                float_format="%.6g",
                na_rep="",
                encoding="utf-8",
                lineterminator="\n",
            ),
        )
    except Exception as e:
        logger.exception("summary_datasets.csv write failed")
        record_failure(
            metadata,
            dataset_name="<export>",
            phase_name="summary_datasets",
            failure=DatasetFailure.MEASUREMENT_ERROR,
            message=str(e),
        )

    # Complete-tracks CSV — tracked (time-lapse) datasets only. One row per
    # (track, timepoint) for tracks followed cleanly through every timepoint
    # (no gaps, not a division daughter, never a division parent). Derived
    # per dataset from the staged measurements (track_id / parent_track_id /
    # timepoint), so no per-dataset store re-open is needed. Non-fatal.
    if "track_id" in df.columns and "timepoint" in df.columns:
        try:
            from percell4.domain.tracking.lineage import select_complete_tracks

            parts: list[pd.DataFrame] = []
            for _ds_name, ds_df in df.groupby("dataset", observed=True):
                if "parent_track_id" not in ds_df.columns:
                    continue
                ds_df = ds_df.dropna(subset=["track_id"])
                if ds_df.empty:
                    continue
                lineage = ds_df.groupby("track_id", as_index=False)["parent_track_id"].first()
                n_timepoints = int(ds_df["timepoint"].max()) + 1
                sub = select_complete_tracks(ds_df, lineage, n_timepoints)
                if not sub.empty:
                    parts.append(sub)
            complete_df = pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]
            # Same column selection as combined.csv, plus the per-timepoint
            # tracking identity columns (the whole point of this report). The
            # one row per (track, timepoint) IS the per-timepoint analysis.
            complete_cols = _ordered_csv_columns(
                complete_df,
                config.selected_csv_columns,
                extra_identity=("timepoint", "track_id", "tree_id", "parent_track_id"),
            )
            write_atomic(
                run_folder / "complete_tracks.csv",
                lambda tmp: complete_df.to_csv(
                    tmp,
                    columns=complete_cols,
                    index=False,
                    float_format="%.6g",
                    na_rep="",
                    encoding="utf-8",
                    lineterminator="\n",
                ),
            )
        except Exception as e:
            logger.exception("complete_tracks.csv write failed")
            record_failure(
                metadata,
                dataset_name="<export>",
                phase_name="complete_tracks",
                failure=DatasetFailure.MEASUREMENT_ERROR,
                message=str(e),
            )

    # Particles export (U7) — concat the per-dataset particle staging
    # files into particles.parquet + particles.csv when present. Errors
    # are recorded but non-fatal (measurements.parquet has landed).
    particles_dir = run_folder / "staging_particles"
    particles_files = sorted(particles_dir.glob("*.parquet")) if particles_dir.is_dir() else []
    if particles_files:
        try:
            import pyarrow.dataset as pa_ds

            pds = pa_ds.dataset([str(p) for p in particles_files], format="parquet")
            particles_df = pds.to_table().to_pandas()
            if "dataset" in particles_df.columns:
                particles_df["dataset"] = pd.Categorical(particles_df["dataset"])
            particles_path = run_folder / "particles.parquet"
            particles_df.to_parquet(
                particles_path,
                engine="pyarrow",
                compression="snappy",
                index=False,
                use_dictionary=True,
            )
            particles_csv = run_folder / "particles.csv"
            particles_df.to_csv(
                particles_csv,
                index=False,
                float_format="%.6g",
                na_rep="",
                encoding="utf-8",
                lineterminator="\n",
            )
            # Clean up per-particle staging on success.
            try:
                for p in particles_files:
                    p.unlink()
                particles_dir.rmdir()
            except OSError:
                logger.exception("failed to clean up staging_particles/")
        except Exception as e:
            logger.exception("particles export failed")
            record_failure(
                metadata,
                dataset_name="<export>",
                phase_name="particles",
                failure=DatasetFailure.MEASUREMENT_ERROR,
                message=f"particles export failed: {e}",
            )

    # Clean up staging on success.
    try:
        for p in staging_files:
            p.unlink()
        staging_dir.rmdir()
    except OSError:
        # Non-fatal: leaving staging behind is ugly but not incorrect.
        logger.exception("failed to clean up staging/")

    return (
        None,
        f"exported {len(df)} rows across {df['dataset'].nunique()} datasets",
    )


# ── Failure tracking helper ─────────────────────────────────────────────


def record_failure(
    metadata: RunMetadata,
    dataset_name: str,
    phase_name: str,
    failure: DatasetFailure,
    message: str,
) -> None:
    """Append a FailureRecord to run metadata. Pure helper for the runner."""
    metadata.failures.append(
        FailureRecord(
            dataset_name=dataset_name,
            phase_name=phase_name,
            failure=failure,
            message=message,
            ts=datetime.now(UTC),
        )
    )


def datasets_without_failures(
    entries: Iterable[WorkflowDatasetEntry],
    metadata: RunMetadata,
) -> list[WorkflowDatasetEntry]:
    """Return the entries that have no failure records yet.

    Used by each phase to skip datasets that were marked failed by
    upstream phases.
    """
    failed = {rec.dataset_name for rec in metadata.failures}
    return [e for e in entries if e.name not in failed]
