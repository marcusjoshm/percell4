"""Use case: batch delete a channel, mask, or segmentation across .h5 datasets.

Single `(kind, name)` deletion, applied to every input ``.h5``.
Symmetric to :mod:`batch_rename_resource` and reuses its
``BatchOperationItemResult`` / ``BatchOperationReport`` dataclasses
so the CLIs share output helpers.

Per-dataset isolation: a missing resource is skipped, not failed;
an unreadable file is recorded as a dataset-level error; the batch
continues either way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    BatchOperationReport,
    VALID_KINDS,
    _channel_exists,
    _hdf5_path_for_kind,
    _path_exists_in_h5,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


def batch_delete_resource(
    h5_paths: list[Path],
    *,
    kind: str,
    name: str,
    dry_run: bool = False,
    progress_callback: Callable[[BatchOperationItemResult], None] | None = None,
) -> BatchOperationReport:
    """Delete ``(kind, name)`` in every ``.h5`` in ``h5_paths``.

    Channels go through :meth:`DatasetStore.delete_channel`, which
    sweeps ``/decay/<name>``, ``/phasor/<name>``, the
    ``channel_names`` metadata entry, and the per-channel FLIM
    calibration attrs together. Masks and segmentations go through
    :meth:`DatasetStore.delete_item` against ``/masks/<name>`` and
    ``/labels/<name>`` respectively.

    Args:
        h5_paths: Datasets to operate on.
        kind: One of ``"channel"``, ``"mask"``, or ``"segmentation"``.
        name: Resource name to delete in each file.
        dry_run: When True, classify exactly as a live run would but
            do not mutate any file.
        progress_callback: Invoked once per dataset after its
            :class:`BatchOperationItemResult` is built.

    Returns:
        A :class:`BatchOperationReport` with one item per input path.
    """
    if kind not in VALID_KINDS:
        raise ValueError(
            f"kind must be one of {VALID_KINDS}, got {kind!r}",
        )

    results: list[BatchOperationItemResult] = []
    for h5_path in h5_paths:
        result = _delete_one_dataset(
            h5_path=h5_path,
            kind=kind,
            name=name,
            dry_run=dry_run,
        )
        results.append(result)
        if progress_callback is not None:
            progress_callback(result)
    return BatchOperationReport(items=tuple(results))


def _delete_one_dataset(
    *,
    h5_path: Path,
    kind: str,
    name: str,
    dry_run: bool,
) -> BatchOperationItemResult:
    """Run one delete. Catches all per-dataset failures."""
    try:
        store = DatasetStore(h5_path)
        if not store.exists():
            raise FileNotFoundError(f"file not found: {h5_path}")
    except Exception as exc:  # noqa: BLE001
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"open failed: {exc}",
        )

    # Existence check per kind.
    if kind == "channel":
        exists = _channel_exists(store, name)
    else:
        exists = _path_exists_in_h5(h5_path, _hdf5_path_for_kind(kind, name))

    if not exists:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="skipped_no_changes",
            skipped={name: f"{kind} not found"},
        )

    if dry_run:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="succeeded",
            processed=(name,),
        )

    # Apply.
    try:
        if kind == "channel":
            store.delete_channel(name)
        else:
            store.delete_item(_hdf5_path_for_kind(kind, name))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "delete %s %r failed on %s", kind, name, h5_path,
        )
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            errors={name: f"{type(exc).__name__}: {exc}"},
        )

    return BatchOperationItemResult(
        h5_path=h5_path,
        status="succeeded",
        processed=(name,),
    )
