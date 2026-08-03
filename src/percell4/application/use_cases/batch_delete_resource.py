"""Use case: batch delete a channel, mask, segmentation, or FLIM phasor
resource across .h5 datasets.

Single `(kind, name)` deletion, applied to every input ``.h5``.
Symmetric to :mod:`batch_rename_resource` and reuses its
``BatchOperationItemResult`` / ``BatchOperationReport`` dataclasses
so the CLIs share output helpers.

Delete supports two FLIM kinds the rename CLI does not (phasor maps are
per-channel derived groups, not standalone renamable resources):

* ``phasor`` — removes the whole ``/phasor/<channel>`` group: base
  ``g``/``s`` AND the derived wavelet triple. Recompute with Compute
  Phasor.
* ``wavelet`` — removes only the wavelet output
  ``{g_filtered, s_filtered, lifetime_filtered}`` for a channel, leaving
  the base phasor intact. Re-run Apply Wavelet Filter without recomputing
  the phasor.

For both, the resource ``name`` is the CHANNEL name (e.g. ``mNG``).

Per-dataset isolation: a missing resource is skipped, not failed;
an unreadable file is recorded as a dataset-level error; the batch
continues either way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from percell4.application.use_cases.batch_rename_resource import (
    VALID_KINDS as _RENAMABLE_KINDS,
)
from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    BatchOperationReport,
    _channel_exists,
    _hdf5_path_for_kind,
    _path_exists_in_h5,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)

# Delete supports every renamable kind plus the two FLIM phasor resources.
# phasor/wavelet are NOT renamable (they are per-channel derived groups under
# /phasor/<channel>, not standalone named resources), so they are absent from
# batch_rename_resource.VALID_KINDS and added only here. For both, the resource
# "name" is the CHANNEL name (e.g. "mNG").
VALID_KINDS: tuple[str, ...] = (*_RENAMABLE_KINDS, "phasor", "wavelet")

# The wavelet filter output for a channel, stored under /phasor/<channel>/.
# Atomic group: readers treat a partial triple as "no filtered cache"
# (load_cached_phasor), so all present members are deleted together.
# ``lifetime_filtered`` is only written when a FLIM frequency is available, so
# a channel may legitimately carry just g_filtered + s_filtered.
_WAVELET_SUFFIXES: tuple[str, ...] = (
    "g_filtered",
    "s_filtered",
    "lifetime_filtered",
)


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
    ``/labels/<name>`` respectively. ``phasor`` removes the whole
    ``/phasor/<channel>`` group (base ``g``/``s`` and the derived wavelet
    triple); ``wavelet`` removes only ``{g_filtered, s_filtered,
    lifetime_filtered}`` for a channel, leaving the base phasor intact.

    Args:
        h5_paths: Datasets to operate on.
        kind: One of ``"channel"``, ``"mask"``, ``"segmentation"``,
            ``"phasor"``, or ``"wavelet"``. For ``phasor``/``wavelet``,
            ``name`` is the CHANNEL name.
        name: Resource name to delete in each file. Mutually exclusive
            with ``all_resources`` — exactly one of the two must be
            specified.
        all_resources: When True, enumerate every resource of ``kind``
            in each dataset and delete them all. Channels enumerate
            from ``metadata.channel_names``; masks enumerate from
            ``/masks/*``; segmentations enumerate from ``/labels/*``;
            ``phasor``/``wavelet`` enumerate channels from ``/phasor/*``.
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
    if not _resource_exists(store, h5_path, kind, name):
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
        _apply_delete(store, kind, name)
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


def _wavelet_members_present(store: DatasetStore, channel: str) -> bool:
    """True if any wavelet-triple member exists under /phasor/<channel>/."""
    members = set(store.list_groups(f"phasor/{channel}"))
    return any(suffix in members for suffix in _WAVELET_SUFFIXES)


def _resource_exists(
    store: DatasetStore, h5_path: Path, kind: str, name: str
) -> bool:
    """True if the ``(kind, name)`` resource is present in the dataset.

    For ``wavelet``, "present" means at least one member of the filtered
    triple exists; the apply step then sweeps whichever members are there.
    """
    if kind == "channel":
        return _channel_exists(store, name)
    if kind == "phasor":
        return _path_exists_in_h5(h5_path, f"phasor/{name}")
    if kind == "wavelet":
        return _wavelet_members_present(store, name)
    return _path_exists_in_h5(h5_path, _hdf5_path_for_kind(kind, name))


def _apply_delete(store: DatasetStore, kind: str, name: str) -> None:
    """Execute the deletion for one ``(kind, name)``.

    ``phasor`` drops the whole ``/phasor/<channel>`` group (base g/s AND
    the derived wavelet triple — the base cannot be removed without the
    derived maps, or the leftovers no longer match any base phasor:
    flim-phasor-cross-layer-alignment). ``wavelet`` drops only the derived
    triple, leaving base g/s intact; ``delete_item`` is drop-if-absent, so
    an absent ``lifetime_filtered`` is a harmless no-op.
    """
    if kind == "channel":
        store.delete_channel(name)
    elif kind == "phasor":
        store.delete_item(f"phasor/{name}")
    elif kind == "wavelet":
        for suffix in _WAVELET_SUFFIXES:
            store.delete_item(f"phasor/{name}/{suffix}")
    else:
        store.delete_item(_hdf5_path_for_kind(kind, name))


def _enumerate_resources(store: DatasetStore, kind: str) -> list[str]:
    """Return the list of resource names of ``kind`` present in ``store``.

    For ``phasor``/``wavelet`` the "names" are channel names under
    ``/phasor/*`` — every channel for ``phasor``, and only channels that
    actually carry a wavelet-triple member for ``wavelet``.
    """
    if kind == "channel":
        return list(store.metadata.get("channel_names", []))
    if kind == "mask":
        return store.list_masks()
    if kind == "segmentation":
        return store.list_labels()
    if kind == "phasor":
        return store.list_groups("phasor")
    if kind == "wavelet":
        return [
            ch for ch in store.list_groups("phasor")
            if _wavelet_members_present(store, ch)
        ]
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
            _apply_delete(store, kind, resource_name)
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
