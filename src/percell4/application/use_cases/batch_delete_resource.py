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
    name: str | None = None,
    all_resources: bool = False,
    dry_run: bool = False,
    progress_callback: Callable[[BatchOperationItemResult], None] | None = None,
) -> BatchOperationReport:
    """Delete a resource (or every resource of a kind) across .h5 files.

    Channels go through :meth:`DatasetStore.delete_channel`, which
    sweeps ``/decay/<name>``, ``/phasor/<name>``, the
    ``channel_names`` metadata entry, and the per-channel FLIM
    calibration attrs together. Masks and segmentations go through
    :meth:`DatasetStore.delete_item` against ``/masks/<name>`` and
    ``/labels/<name>`` respectively.

    Args:
        h5_paths: Datasets to operate on.
        kind: One of ``"channel"``, ``"mask"``, or ``"segmentation"``.
        name: Resource name to delete in each file. Mutually exclusive
            with ``all_resources`` — exactly one of the two must be
            specified.
        all_resources: When True, enumerate every resource of ``kind``
            in each dataset and delete them all. Channels enumerate
            from ``metadata.channel_names``; masks enumerate from
            ``/masks/*``; segmentations enumerate from ``/labels/*``.
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
    if (name is None) == (not all_resources):
        raise ValueError(
            "exactly one of `name` or `all_resources` must be specified",
        )

    results: list[BatchOperationItemResult] = []
    for h5_path in h5_paths:
        if all_resources:
            result = _delete_all_in_dataset(
                h5_path=h5_path, kind=kind, dry_run=dry_run,
            )
        else:
            assert name is not None  # narrowed by the validation above
            result = _delete_one_dataset(
                h5_path=h5_path, kind=kind, name=name, dry_run=dry_run,
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


def _enumerate_resources(store: DatasetStore, kind: str) -> list[str]:
    """Return the list of resource names of ``kind`` present in ``store``."""
    if kind == "channel":
        return list(store.metadata.get("channel_names", []))
    if kind == "mask":
        return store.list_masks()
    if kind == "segmentation":
        return store.list_labels()
    # Already validated upstream; this is defense-in-depth.
    raise ValueError(f"unknown kind: {kind!r}")


def _delete_all_in_dataset(
    *,
    h5_path: Path,
    kind: str,
    dry_run: bool,
) -> BatchOperationItemResult:
    """Enumerate every resource of ``kind`` in one dataset and delete them.

    Per-resource failures isolate within the dataset: a failure on
    ``ch1`` does not block deletion of ``ch2``. Successes accumulate in
    ``processed``; failures accumulate in ``errors``.
    """
    try:
        store = DatasetStore(h5_path)
        if not store.exists():
            raise FileNotFoundError(f"file not found: {h5_path}")
        names = _enumerate_resources(store, kind)
    except Exception as exc:  # noqa: BLE001
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"open failed: {exc}",
        )

    if not names:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="skipped_no_changes",
            skipped={"<all>": f"no {kind} resources present"},
        )

    if dry_run:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="succeeded",
            processed=tuple(names),
        )

    processed: list[str] = []
    errors: dict[str, str] = {}
    for resource_name in names:
        try:
            if kind == "channel":
                store.delete_channel(resource_name)
            else:
                store.delete_item(_hdf5_path_for_kind(kind, resource_name))
            processed.append(resource_name)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "delete %s %r failed on %s", kind, resource_name, h5_path,
            )
            errors[resource_name] = f"{type(exc).__name__}: {exc}"

    if errors and not processed:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "succeeded"

    return BatchOperationItemResult(
        h5_path=h5_path,
        status=status,
        processed=tuple(processed),
        errors=errors,
    )
