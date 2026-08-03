"""Use case: create a "whole_field" segmentation across .h5 datasets.

For each input ``.h5``, writes ``/labels/whole_field`` — a 2D int32
array shaped to the dataset's native (H, W), with every pixel = 1.
Useful as a baseline gating layer for whole-field measurements (or
as a starting point before per-cell Cellpose runs).

Shape is inferred from ``/metadata.native_shape`` when present;
otherwise from the first ``/decay/<channel>`` group's shape. An empty
dataset with neither metadata nor decay can't be shaped — its item
ends ``failed`` with a clear error.

Pre-existing ``/labels/whole_field`` is silently overwritten — every
run rewrites all-ones at the current native shape. ``--dry-run``
classifies as a live run would but does not mutate any file.

Reuses :class:`BatchOperationItemResult` / :class:`BatchOperationReport`
from :mod:`batch_rename_resource` so all sibling CLIs share output
helpers and exit-code semantics.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import h5py
import numpy as np

from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    BatchOperationReport,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


# The single resource name written by this use case. Hard-coded by
# design — every dataset gets the same name so downstream tools can
# look it up unambiguously. Users who need a different name can run
# percell4-batch-rename --kind segmentation afterwards.
WHOLE_FIELD_NAME = "whole_field"


def batch_create_whole_field_segmentation(
    h5_paths: list[Path],
    *,
    dry_run: bool = False,
    progress_callback: Callable[[BatchOperationItemResult], None] | None = None,
) -> BatchOperationReport:
    """Write ``/labels/whole_field`` (all pixels = 1) into each .h5.

    Args:
        h5_paths: Datasets to operate on.
        dry_run: When True, classify each dataset as a live run would
            but do not mutate any file.
        progress_callback: Invoked once per dataset after its
            :class:`BatchOperationItemResult` is built.

    Returns:
        A :class:`BatchOperationReport` with one item per input path.
    """
    results: list[BatchOperationItemResult] = []
    for h5_path in h5_paths:
        result = _process_one_dataset(h5_path=h5_path, dry_run=dry_run)
        results.append(result)
        if progress_callback is not None:
            progress_callback(result)
    return BatchOperationReport(items=tuple(results))


def _infer_native_shape(
    store: DatasetStore, h5_path: Path,
) -> tuple[int, int]:
    """Return the dataset's native (H, W).

    Prefers ``/metadata.native_shape``; falls back to the first
    ``/decay/<channel>`` group's first two dims. Raises ``ValueError``
    when neither is available.
    """
    meta = store.metadata
    ns = meta.get("native_shape")
    if ns is not None:
        ns_tuple = tuple(int(x) for x in ns)
        if len(ns_tuple) == 2:
            return ns_tuple  # type: ignore[return-value]

    # Fall back to decay-layer inference. A 4-D time-lapse decay
    # (T_acq, H, W, T_bins) carries spatial dims at [1:3]; legacy 3-D
    # (H, W, T_bins) at [0:2].
    with h5py.File(h5_path, "r") as f:
        if "decay" in f:
            decay_grp = f["decay"]
            for channel in decay_grp:
                ds = decay_grp[channel]
                shape = ds.shape
                dims = ds.attrs.get("dims")
                is_4d = len(shape) == 4 or (
                    dims is not None and len(dims) > 0 and str(dims[0]) == "Tacq"
                )
                if is_4d and len(shape) >= 4:
                    return int(shape[1]), int(shape[2])
                if len(shape) >= 2:
                    return int(shape[0]), int(shape[1])

    raise ValueError(
        "could not determine dataset shape: no /metadata.native_shape "
        "and no /decay/<channel> group to infer from",
    )


def _process_one_dataset(
    *, h5_path: Path, dry_run: bool,
) -> BatchOperationItemResult:
    """Run one whole-field write. Catches all per-dataset failures."""
    try:
        store = DatasetStore(h5_path)
        if not store.exists():
            raise FileNotFoundError(f"file not found: {h5_path}")
        h, w = _infer_native_shape(store, h5_path)
    except Exception as exc:  # noqa: BLE001
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    if dry_run:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="succeeded",
            processed=(WHOLE_FIELD_NAME,),
        )

    try:
        array = np.ones((h, w), dtype=np.int32)
        store.write_labels(WHOLE_FIELD_NAME, array)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "write whole_field failed on %s", h5_path,
        )
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            errors={WHOLE_FIELD_NAME: f"{type(exc).__name__}: {exc}"},
        )

    return BatchOperationItemResult(
        h5_path=h5_path,
        status="succeeded",
        processed=(WHOLE_FIELD_NAME,),
    )
