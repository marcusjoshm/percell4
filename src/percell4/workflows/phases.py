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
    relabel_sequential,
)
from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure, FailureRecord
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
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

    # Convert the stored file path strings back into DiscoveredFile-like
    # inputs. The existing `import_dataset` accepts a `files=` override
    # that's a list of DiscoveredFile tuples; we need to reconstruct
    # those from the captured plan.
    try:
        from percell4.adapters.importer import import_dataset
        from percell4.domain.io.models import DiscoveredFile

        discovered = [DiscoveredFile(path=Path(p)) for p in files_paths]
        import_dataset(
            source_dir=source_dir or str(output_path.parent),
            output_h5=output_path,
            z_project_method=z_project_method,
            selected_channels=selected_channels or None,
            files=discovered or None,
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

    # Postprocess: edge removal is always on per workflow invariant.
    labels, _n_edge = filter_edge_cells(labels.astype(np.int32), edge_margin=0)
    labels, _n_small = filter_small_cells(labels, min_area=cfg.min_size)
    labels = relabel_sequential(labels)

    if int(labels.max()) == 0:
        return (
            labels,
            DatasetFailure.SEGMENTATION_EMPTY,
            "Cellpose + postprocess removed all cells",
        )

    try:
        store.write_labels("cellpose_qc", labels)
    except Exception as e:
        logger.exception("failed to write /labels/cellpose_qc")
        return (
            labels,
            DatasetFailure.SEGMENTATION_ERROR,
            f"write /labels/cellpose_qc failed: {e}",
        )

    return labels, None, f"{int(labels.max())} cells after postprocess"


# ── Phase 3/5/...: Threshold compute + headless apply ──────────────────


def threshold_compute_one(
    store: DatasetStore,
    round_spec: ThresholdingRound,
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
        labels = store.read_labels("cellpose_qc")
    except KeyError as e:
        return None, DatasetFailure.THRESHOLD_ERROR, f"missing h5 key: {e}"

    if int(labels.max()) == 0:
        return None, DatasetFailure.THRESHOLD_EMPTY, "no cells in /labels/cellpose_qc"

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
        labels = store.read_labels("cellpose_qc")
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


# ── Phase 7: Measure ────────────────────────────────────────────────────


def measure_one(
    store: DatasetStore,
    round_specs: list[ThresholdingRound],
    metric_names: list[str] | None = None,
) -> tuple[pd.DataFrame, DatasetFailure | None, str]:
    """Measure one dataset: all channels × all metrics × all round masks.

    Opens one session, reads the full intensity cube, labels, and every
    round's mask and group DataFrame, calls the single-pass
    :func:`measure_multichannel_with_masks`, then merges the
    ``group_<round>`` columns from each round's stored DataFrame.

    Returns an empty DataFrame on failure (alongside a failure code);
    the caller appends it to ``staging/`` regardless and lets
    :func:`export_run` skip empty datasets in the concat.
    """
    metric_names = metric_names or sorted(BUILTIN_METRICS.keys())

    try:
        with store.open_read() as s:
            intensity = s.read_array("intensity")
            labels = s.read_labels("cellpose_qc")
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
