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
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from percell4.domain.measure.grouper import GroupingResult, group_cells_gmm, group_cells_kmeans
from percell4.domain.measure.measurer import measure_cells, measure_multichannel_with_masks
from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.adapters.cellpose import run_cellpose
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


def _read_segmentation_channel(
    store: DatasetStore, channel_idx: int = 0
) -> NDArray:
    """Read one channel plane from /intensity for segmentation.

    Works for both 2D (single-channel) and 3D (C, H, W) layouts by
    delegating to :meth:`DatasetStore.read_channel`.
    """
    return store.read_channel("intensity", channel_idx)


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
    try:
        image = _read_segmentation_channel(store, channel_idx=channel_idx)
    except (KeyError, IndexError, ValueError) as e:
        logger.exception("failed to read intensity for segmentation")
        return (
            np.zeros((0, 0), dtype=np.int32),
            DatasetFailure.SEGMENTATION_ERROR,
            f"read /intensity failed: {e}",
        )

    try:
        diameter = cfg.diameter if cfg.diameter > 0 else None
        labels = run_cellpose(
            image,
            diameter=diameter,
            gpu=cfg.gpu,
            flow_threshold=cfg.flow_threshold,
            cellprob_threshold=cfg.cellprob_threshold,
            min_size=cfg.min_size,
            model=cellpose_model,
        )
    except Exception as e:
        logger.exception("run_cellpose raised for this dataset")
        return (
            np.zeros_like(image, dtype=np.int32),
            DatasetFailure.SEGMENTATION_ERROR,
            f"Cellpose failed: {type(e).__name__}: {e}",
        )

    # Postprocess: edge removal is conditional on the workflow's edge_mode.
    # Modes other than EXCLUDE keep edge cells in labels; they will be
    # flagged with ``is_edge=True`` at measure time (recomputed from the
    # post-QC labels via ``get_edge_labels`` in U4) — no extra HDF5
    # persistence here.
    labels = labels.astype(np.int32)
    if edge_mode == EdgeMode.EXCLUDE:
        labels, _n_edge = filter_edge_cells(labels, edge_margin=edge_margin_px)
    labels, _n_small = filter_small_cells(labels, min_area=cfg.min_size)
    labels = relabel_sequential(labels)

    if int(labels.max()) == 0:
        return (
            labels,
            DatasetFailure.SEGMENTATION_EMPTY,
            "Cellpose + postprocess removed all cells",
        )

    try:
        store.write_labels(seg_name, labels)
    except Exception as e:
        logger.exception("failed to write /labels/%s", seg_name)
        return (
            labels,
            DatasetFailure.SEGMENTATION_ERROR,
            f"write /labels/{seg_name} failed: {e}",
        )

    return labels, None, f"{int(labels.max())} cells after postprocess"


# ── Phase 3/5/...: Threshold compute + headless apply ──────────────────


def threshold_compute_one(
    store: DatasetStore,
    round_spec: ThresholdingRound,
    seg_name: str = "cellpose_qc",
) -> tuple[GroupingResult | None, DatasetFailure | None, str]:
    """Compute the per-cell grouping for one round on one dataset.

    Reads the round's channel and the QC-accepted labels, computes the
    per-cell metric, and runs GMM or K-means grouping. Returns a
    :class:`GroupingResult` on success.
    """
    try:
        channel_idx = _channel_index(store, round_spec.channel)
    except (KeyError, ValueError) as e:
        return None, DatasetFailure.THRESHOLD_ERROR, str(e)

    try:
        image = store.read_channel("intensity", channel_idx)
        labels = store.read_labels(seg_name)
    except KeyError as e:
        return None, DatasetFailure.THRESHOLD_ERROR, f"missing h5 key: {e}"

    if int(labels.max()) == 0:
        return None, DatasetFailure.THRESHOLD_EMPTY, f"no cells in /labels/{seg_name}"

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


def apply_threshold_headless(
    store: DatasetStore,
    round_spec: ThresholdingRound,
    grouping: GroupingResult,
    seg_name: str = "cellpose_qc",
) -> tuple[DatasetFailure | None, str]:
    """Headless per-group Otsu thresholding — the Phase 4 QC stand-in.

    For each group returned by :func:`threshold_compute_one`, we:

    1. Mask the channel image to the cells belonging to that group
       (values outside the group are zeroed).
    2. Apply a Gaussian smoothing pass at ``round_spec.gaussian_sigma``.
    3. Compute an Otsu threshold over the non-zero pixels.
    4. Take pixels above the threshold as the group's binary mask.

    The per-group masks are unioned into one combined ``uint8`` mask.
    We write ``/masks/<round_spec.name>`` and a ``/groups/<round_spec.name>``
    DataFrame to the store — the same shape :class:`ThresholdQCController._finalize`
    produces, so downstream :func:`measure_one` can load both without
    caring whether the thresholds were interactive or headless.

    This function will be replaced by the interactive
    ``ThresholdQCController`` path when Phase 6 lands. Headless mode
    will remain as a fallback for unattended runs.
    """
    try:
        channel_idx = _channel_index(store, round_spec.channel)
        image = store.read_channel("intensity", channel_idx)
        labels = store.read_labels(seg_name)
    except (KeyError, ValueError) as e:
        return DatasetFailure.THRESHOLD_ERROR, str(e)

    # Pre-smooth the whole channel once; per-group processing just masks it.
    if round_spec.gaussian_sigma > 0:
        smoothed = apply_gaussian_smoothing(
            image.astype(np.float32), round_spec.gaussian_sigma
        )
    else:
        smoothed = image.astype(np.float32)

    combined = np.zeros(labels.shape, dtype=np.uint8)

    # Group assignments Series has index=cell_label, value=group_id (1-based).
    for group_id in range(1, grouping.n_groups + 1):
        cells_in_group = grouping.group_assignments.index[
            grouping.group_assignments.values == group_id
        ].to_numpy(dtype=np.int32)
        if len(cells_in_group) == 0:
            continue

        # Mask the smoothed channel to only this group's cells.
        group_label_mask = np.isin(labels, list(cells_in_group))
        if not group_label_mask.any():
            continue

        group_pixels = smoothed[group_label_mask]
        if group_pixels.size == 0 or not np.isfinite(group_pixels).any():
            continue

        try:
            # threshold_otsu expects the sub-image (nonzero pixels), so
            # we pass the masked values and broadcast the result back.
            # The helper returns (binary_mask, threshold_value) on the
            # FULL image shape when given a full image — but we want
            # per-group application, so we compute the threshold
            # ourselves on the group's pixels and broadcast.
            from skimage.filters import threshold_otsu as sk_otsu

            if np.unique(group_pixels).size < 2:
                # Constant group — cannot compute a meaningful threshold.
                # Accept every pixel of the group as "positive" (safer
                # than accepting none).
                group_mask = group_label_mask
            else:
                thr = float(sk_otsu(group_pixels))
                group_mask = group_label_mask & (smoothed >= thr)
        except Exception as e:
            logger.exception("otsu failed for group %d", group_id)
            return (
                DatasetFailure.THRESHOLD_ERROR,
                f"otsu for group {group_id}: {e}",
            )

        # Union into combined mask.
        np.maximum(combined, group_mask.astype(np.uint8), out=combined)

    try:
        store.write_mask(round_spec.name, combined)
    except Exception as e:
        logger.exception("write_mask failed")
        return DatasetFailure.THRESHOLD_ERROR, f"write_mask failed: {e}"

    # Persist the group assignments DataFrame — same shape the
    # ThresholdQCController writes so measure_one can consume it
    # regardless of source (interactive vs headless).
    col_name = f"group_{round_spec.channel}_{round_spec.metric}"
    group_df = grouping.group_assignments.reset_index()
    group_df.columns = ["label", col_name]

    try:
        store.write_dataframe(f"/groups/{round_spec.name}", group_df)
    except Exception as e:
        logger.exception("write_dataframe /groups failed")
        return DatasetFailure.THRESHOLD_ERROR, f"write /groups failed: {e}"

    return None, f"{int(combined.sum())} positive pixels across {grouping.n_groups} groups"


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
        raise KeyError(
            f"channel {channel_name!r} not in dataset; available: {names_list}"
        )
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
            columns=["dataset", "round_name", "group_label", "n_cells",
                     "fraction_of_dataset_cells"]
        )

    metric_cols = [
        c for c in real.columns
        if _is_metric_column(c) and pd.api.types.is_numeric_dtype(real[c])
        # Group columns are categorical-ish; skip even if numeric.
        and not c.startswith("group_")
    ]

    frames: list[pd.DataFrame] = []
    for round_name in thresholding_round_names:
        col = f"group_{round_name}"
        if col not in real.columns:
            continue
        # Cells without a group assignment (NaN) are dropped.
        grouped = real.dropna(subset=[col]).groupby(
            ["dataset", col], observed=True
        )
        if grouped.ngroups == 0:
            continue
        counts = grouped.size().rename("n_cells").reset_index()
        counts = counts.rename(columns={col: "group_label"})

        # Fraction within (dataset) — total real cells per dataset
        # across all groups in this round (not the dataset's grand
        # total) so per-round fractions sum to 1.0.
        per_dataset_total = counts.groupby("dataset", observed=True)[
            "n_cells"
        ].transform("sum")
        counts["fraction_of_dataset_cells"] = counts["n_cells"] / per_dataset_total

        if metric_cols:
            stats = grouped[metric_cols].agg(["mean", "median", "std"])
            # Flatten MultiIndex columns: (metric, stat) → metric_stat
            stats.columns = [f"{m}_{s}" for m, s in stats.columns]
            stats = stats.reset_index().rename(columns={col: "group_label"})
            counts = counts.merge(
                stats, on=["dataset", "group_label"], how="left"
            )

        counts.insert(1, "round_name", round_name)
        frames.append(counts)

    if not frames:
        return pd.DataFrame(
            columns=["dataset", "round_name", "group_label", "n_cells",
                     "fraction_of_dataset_cells"]
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
        failures_by_ds.setdefault(f.dataset_name, []).append(
            f"{f.phase_name}: {f.message}"
        )

    dilute_enabled = config.dilute_settings is not None
    n_rounds_thresholding = len(config.thresholding_rounds)
    edge_mode_value = config.edge_mode.value

    if "is_edge_synthetic" in df.columns:
        real = df[~df["is_edge_synthetic"]]
    else:
        real = df

    # Group real cells by dataset once.
    if not real.empty and "dataset" in real.columns:
        counts_by_ds = real.groupby("dataset", observed=True).agg(
            n_cells_total=("label", "size"),
            n_cells_whole=("is_edge", lambda s: int((~s).sum())),
            n_cells_edge=("is_edge", lambda s: int(s.sum())),
        ).to_dict("index")
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
                metadata.per_dataset_dilute_round_counts.get(name, 0)
                if dilute_enabled
                else None
            ),
            "dilute_enabled": dilute_enabled,
            "edge_mode": edge_mode_value,
            "failure_reason": (
                "; ".join(failures_by_ds[name])
                if name in failures_by_ds
                else None
            ),
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


def _add_area_um2_columns(
    df: pd.DataFrame, pixel_size_um: float | None
) -> pd.DataFrame:
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
            logger.exception(
                "failed to compute %s from %s — skipping", sibling, col
            )

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


def measure_one(
    store: DatasetStore,
    round_specs: list[ThresholdingRound],
    metric_names: list[str] | None = None,
    edge_mode: EdgeMode = EdgeMode.EXCLUDE,
    edge_margin_px: int = 0,
    seg_name: str = "cellpose_qc",
    particle_settings: ParticleSettings | None = None,
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Measure one dataset: all channels × all metrics × all round masks.

    Opens one session, reads the full intensity cube, labels, and every
    round's mask and group DataFrame, calls the single-pass
    :func:`measure_multichannel_with_masks`, then merges the
    ``group_<round>`` columns from each round's stored DataFrame.

    Adds the per-cell identity / cohort columns ``cell_id``, ``is_edge``,
    and ``is_edge_synthetic`` to every row. When ``edge_mode`` is
    ``INCLUDE_AS_SIZE_NORMALIZED_COHORT``, additionally appends one
    synthetic edge-cohort row per origin R7 via
    :func:`_append_synthetic_row`.

    Returns an empty DataFrame on a hard failure (e.g., read error,
    empty labels). For the "soft" zero-whole-cells case (R10b), returns
    the per-cell df with a recorded ``DatasetFailure`` — the runner
    stages the df anyway so the dataset's per-cell rows reach the
    parquet, and ``summary_datasets.csv`` notes the failure reason.
    """
    metric_names = metric_names or sorted(BUILTIN_METRICS.keys())

    try:
        with store.open_read() as s:
            intensity = s.read_array("intensity")
            labels = s.read_labels(seg_name)
            meta = s.metadata
            channel_names_raw = meta.get("channel_names", [])
            channel_names = [
                n.decode() if isinstance(n, bytes) else str(n)
                for n in channel_names_raw
            ]

            # Build channel → image dict
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

            # Load all round masks
            round_masks: dict[str, NDArray[np.uint8]] = {}
            group_dfs: dict[str, pd.DataFrame] = {}
            for round_spec in round_specs:
                try:
                    round_masks[round_spec.name] = s.read_mask(round_spec.name)
                except KeyError:
                    # Round was skipped for this dataset (e.g. threshold failed).
                    # Skip it from measure but don't fail the whole dataset.
                    logger.info(
                        "dataset missing mask /masks/%s — skipping from measure",
                        round_spec.name,
                    )
                    continue
                try:
                    group_dfs[round_spec.name] = s.read_dataframe(
                        f"/groups/{round_spec.name}"
                    )
                except KeyError:
                    logger.info(
                        "dataset missing /groups/%s — group column won't be added",
                        round_spec.name,
                    )
    except Exception as e:
        logger.exception("measure_one read session failed")
        return (
            pd.DataFrame(),
            DatasetFailure.MEASUREMENT_ERROR,
            f"read session failed: {e}",
        )

    if int(labels.max()) == 0:
        return (
            pd.DataFrame(),
            DatasetFailure.MEASUREMENT_ERROR,
            "empty labels — nothing to measure",
        )

    try:
        df = measure_multichannel_with_masks(
            images=images,
            labels=labels,
            metrics=metric_names,
            masks=round_masks,
        )
    except Exception as e:
        logger.exception("measure_multichannel_with_masks failed")
        return (
            pd.DataFrame(),
            DatasetFailure.MEASUREMENT_ERROR,
            f"measure failed: {e}",
        )

    # Drop the per-round "_out_<round>" columns — this workflow is
    # interested only in stats INSIDE each round's mask (where the
    # particles / thresholded signal lives), not the cell-minus-mask
    # complement. The "_in_<round>" columns are kept. See iteration-3
    # user feedback.
    out_cols = [c for c in df.columns if "_out_" in c]
    if out_cols:
        df = df.drop(columns=out_cols)

    # Pixel size from /metadata.pixel_size_um (auto-detected at import
    # time from TIFF resolution tags). Drives the `<area_col>_um2`
    # sibling columns emitted near the end of this function. None when
    # the h5 carries no positive value — measure_one then stays in
    # pixel units only.
    pixel_size_um = _read_pixel_size_um(store)

    # Per-cell identity + cohort columns. ``cell_id`` mirrors the
    # post-relabel sequential ``label`` for real cells (synthetic row
    # below carries ``cell_id=-1``). ``(dataset, cell_id)`` is the
    # composite key — ``cell_id`` is NOT globally unique across
    # datasets (see plan's From 2026-05-20 ce-doc-review section on
    # adversarial finding #1).
    df["cell_id"] = df["label"]

    # Recompute the edge label set from the post-QC labels (cheap
    # border-row/column scan; no new HDF5 contract).
    edge_label_set = get_edge_labels(
        labels.astype(np.int32), edge_margin=edge_margin_px
    )
    df["is_edge"] = df["label"].isin(edge_label_set)
    df["is_edge_synthetic"] = False

    # Particle analysis (per-cell summary). For each round mask, count
    # connected components within each cell and merge the summary
    # columns into df with a "<round_name>_" prefix. The detailed
    # per-particle rows are produced separately by
    # :func:`measure_particles_one` (called by the runner).
    if particle_settings is not None:
        from percell4.domain.measure.particle import analyze_particles

        # Resolve the configured min_area into a per-dataset pixel
        # threshold. µm² mode requires this dataset's pixel size — fail
        # the dataset's particle phase explicitly when missing rather
        # than silently default. Return an empty df (not the partially
        # built one) so the runner doesn't stage a schema-divergent
        # parquet — the rest of measure_one's per-cell columns
        # (group_<round>, area_um2 siblings) have not been merged yet
        # at this point in the function.
        try:
            resolved_min_area_px = _resolve_min_area_px(
                particle_settings, pixel_size_um,
            )
        except ValueError as e:
            logger.error("particle threshold resolve failed: %s", e)
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

        for round_name, round_mask in round_masks.items():
            try:
                particle_summary = analyze_particles(
                    images=images,
                    labels=labels,
                    mask=round_mask,
                    min_area=resolved_min_area_px,
                )
            except Exception as e:
                logger.exception(
                    "analyze_particles failed for round %s — skipping",
                    round_name,
                )
                continue
            if particle_summary.empty:
                continue
            # Rename non-label columns with a per-round prefix.
            rename_map = {
                c: f"{round_name}_{c}"
                for c in particle_summary.columns
                if c != "label"
            }
            particle_summary = particle_summary.rename(columns=rename_map)
            df = df.merge(particle_summary, on="label", how="left")

    # Append the size-normalized synthetic row when the mode requires
    # it. The helper handles all the R10 edge cases and returns a
    # DatasetFailure for the zero-whole-cells case (df is preserved
    # so the runner can still stage per-cell rows).
    df, edge_failure, edge_msg = _append_synthetic_row(
        df, edge_label_set, edge_mode
    )
    if edge_failure is not None:
        # Surface as the function's failure; the runner stages the
        # df anyway when it is non-empty (see _make_measure_handler).
        # Merge group_<round> columns first so the staging parquet
        # has the same column shape as a successful run.
        for round_name, g_df in group_dfs.items():
            cols = list(g_df.columns)
            if len(cols) != 2 or cols[0] != "label":
                continue
            g_df = g_df.rename(columns={cols[1]: f"group_{round_name}"})
            df = df.merge(g_df, on="label", how="left")
        return df, edge_failure, edge_msg

    # Merge group_<round> columns from the per-round stored DataFrames.
    # Each group_df has columns ["label", "group_<channel>_<metric>"]; we
    # rename the second column to "group_<round_name>" for unambiguous
    # per-round provenance, then left-merge on label.
    for round_name, g_df in group_dfs.items():
        cols = list(g_df.columns)
        if len(cols) != 2 or cols[0] != "label":
            logger.warning(
                "unexpected group_df schema for %s: %s", round_name, cols
            )
            continue
        g_df = g_df.rename(columns={cols[1]: f"group_{round_name}"})
        df = df.merge(g_df, on="label", how="left")

    # Emit _um2 sibling columns for every area-style column (cell area,
    # particle area summaries, per-round mask intersection areas). No-op
    # when pixel_size_um is missing from the h5 metadata.
    df = _add_area_um2_columns(df, pixel_size_um)

    return df, None, f"{len(df)} cells, {len(df.columns)} columns"


def write_staging_parquet(
    run_folder: Path, dataset_name: str, df: pd.DataFrame
) -> Path:
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


def measure_particles_one(
    store: DatasetStore,
    round_specs: list[ThresholdingRound],
    particle_settings: ParticleSettings,
    seg_name: str = "cellpose_qc",
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Per-particle detail rows for one dataset across every round.

    Re-reads the dataset's intensity cube, labels, and each round's mask,
    then calls :func:`analyze_particles_detail` per round. Returns one
    combined DataFrame whose columns are:

    - ``round_name`` (added here)
    - ``cell_id``, ``particle_id``, ``area``, ``centroid_y``, ``centroid_x``
    - ``{channel}_mean_intensity``, ``{channel}_integrated_intensity``
      for every channel

    Rounds whose mask is missing are skipped silently (consistent with
    ``measure_one``). On read error returns an empty DataFrame + failure.
    """
    try:
        with store.open_read() as s:
            intensity = s.read_array("intensity")
            labels = s.read_labels(seg_name)
            meta = s.metadata
            channel_names_raw = meta.get("channel_names", [])
            channel_names = [
                n.decode() if isinstance(n, bytes) else str(n)
                for n in channel_names_raw
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
            particle_settings, pixel_size_um,
        )
    except ValueError as e:
        logger.error("particle threshold resolve failed: %s", e)
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


def write_staging_particles_parquet(
    run_folder: Path, dataset_name: str, df: pd.DataFrame
) -> Path:
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
    identity_cols = [c for c in ("dataset", "cell_id", "label") if c in df.columns]
    selected = [c for c in config.selected_csv_columns if c in df.columns]
    # De-duplicate while preserving order.
    csv_cols: list[str] = []
    seen: set[str] = set()
    for c in identity_cols + selected:
        if c not in seen:
            seen.add(c)
            csv_cols.append(c)

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

    # Particles export (U7) — concat the per-dataset particle staging
    # files into particles.parquet + particles.csv when present. Errors
    # are recorded but non-fatal (measurements.parquet has landed).
    particles_dir = run_folder / "staging_particles"
    particles_files = sorted(particles_dir.glob("*.parquet")) if particles_dir.is_dir() else []
    if particles_files:
        try:
            import pyarrow.dataset as pa_ds

            pds = pa_ds.dataset(
                [str(p) for p in particles_files], format="parquet"
            )
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
