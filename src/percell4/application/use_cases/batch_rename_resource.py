"""Use case: batch rename a channel, mask, or segmentation across .h5 datasets.

Single `(kind, old_name) → new_name` rename, applied to every input
``.h5``. Per-dataset failures isolate — a missing target on one file
is recorded as skipped (no-op for that file); a target-name collision
or IO error is recorded as a per-dataset error and the batch
continues to the next file.

Sister of :mod:`percell4.application.use_cases.batch_delete_resource`
and a structural mirror of
:mod:`percell4.application.use_cases.batch_compute_phasor`.

The orchestrator is pure-Python and Session-free: it talks directly
to ``DatasetStore``. The ``rename_channel`` (canonical per-channel
rename) and ``rename_item`` (generic HDF5 path move) methods do the
real work; this module is composition and classification.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


# ── Status taxonomy + report dataclasses ────────────────────────────────


ITEM_STATUSES: tuple[str, ...] = (
    "succeeded",
    "skipped_no_changes",
    "failed",
)

VALID_KINDS: tuple[str, ...] = ("channel", "mask", "segmentation")


@dataclass(frozen=True)
class BatchOperationItemResult:
    """Outcome of one dataset in a batch rename / delete run.

    Each per-dataset run touches at most one resource, so
    ``processed`` / ``skipped`` / ``errors`` each have 0 or 1 entries.
    The dataclass shape matches what :mod:`batch_compute_phasor` uses
    so the CLI print helpers can format both.
    """

    h5_path: Path
    status: str  # one of ITEM_STATUSES
    processed: tuple[str, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class BatchOperationReport:
    """Aggregated result of a batch rename / delete run."""

    items: tuple[BatchOperationItemResult, ...] = ()

    @property
    def total_succeeded(self) -> int:
        return sum(1 for r in self.items if r.status == "succeeded")

    @property
    def total_skipped(self) -> int:
        return sum(1 for r in self.items if r.status == "skipped_no_changes")

    @property
    def total_failed(self) -> int:
        return sum(1 for r in self.items if r.status == "failed")


# ── Pre-flight helpers ──────────────────────────────────────────────────


def _path_exists_in_h5(path: Path, hdf5_path: str) -> bool:
    """Return True if ``hdf5_path`` exists in the .h5 at ``path``."""
    import h5py
    try:
        with h5py.File(path, "r") as f:
            return hdf5_path in f
    except OSError:
        return False


def _channel_exists(store: DatasetStore, name: str) -> bool:
    """Return True if a channel ``name`` exists in the dataset.

    The canonical check is membership in ``metadata.channel_names``
    OR presence of ``/decay/<name>``. Either signal qualifies — they
    can diverge briefly during half-finished imports or manual edits.
    """
    try:
        names = list(store.metadata.get("channel_names", []))
        if name in names:
            return True
    except Exception:
        # If metadata read fails, fall through to the decay-path check.
        pass
    return _path_exists_in_h5(store.path, f"decay/{name}")


def _hdf5_path_for_kind(kind: str, name: str) -> str:
    """Map a `(kind, name)` to its canonical HDF5 path.

    Channels live under ``/decay/<name>`` (the canonical anchor used
    for existence checks). Masks and segmentations live under
    ``/masks/<name>`` and ``/labels/<name>`` respectively.
    """
    if kind == "channel":
        return f"decay/{name}"
    if kind == "mask":
        return f"masks/{name}"
    if kind == "segmentation":
        return f"labels/{name}"
    raise ValueError(f"unknown kind: {kind!r}")


# ── Main entry point ────────────────────────────────────────────────────


def batch_rename_resource(
    h5_paths: list[Path],
    *,
    kind: str,
    old_name: str,
    new_name: str,
    dry_run: bool = False,
    progress_callback: Callable[[BatchOperationItemResult], None] | None = None,
) -> BatchOperationReport:
    """Rename ``(kind, old_name) → new_name`` in every ``.h5`` in ``h5_paths``.

    Per-dataset isolation: a missing source name is reported as
    skipped; a target-name collision (``rename_*`` raised
    ``ValueError``) is reported as a per-dataset error; an unreadable
    file is reported as a dataset-level error. The batch always
    completes.

    Args:
        h5_paths: Datasets to operate on.
        kind: One of ``"channel"``, ``"mask"``, or ``"segmentation"``.
        old_name: Source name to find in each file.
        new_name: Target name to rename to.
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
        result = _rename_one_dataset(
            h5_path=h5_path,
            kind=kind,
            old_name=old_name,
            new_name=new_name,
            dry_run=dry_run,
        )
        results.append(result)
        if progress_callback is not None:
            progress_callback(result)
    return BatchOperationReport(items=tuple(results))


def _rename_one_dataset(
    *,
    h5_path: Path,
    kind: str,
    old_name: str,
    new_name: str,
    dry_run: bool,
) -> BatchOperationItemResult:
    """Run one rename. Catches all per-dataset failures."""
    # Open / sanity-check the store. The actual rename methods will
    # re-open the file in 'a' mode; this guards against missing files
    # and corrupted headers before we get there.
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

    # Same-name no-op short-circuit. rename_channel handles this
    # internally; we classify here so the batch summary reads
    # honestly ("skipped: source == target") instead of "succeeded".
    if old_name == new_name:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="skipped_no_changes",
            skipped={old_name: "source equals target — no rename needed"},
        )

    # Existence check per kind.
    if kind == "channel":
        exists = _channel_exists(store, old_name)
    else:
        hdf5_path = _hdf5_path_for_kind(kind, old_name)
        exists = _path_exists_in_h5(h5_path, hdf5_path)

    if not exists:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="skipped_no_changes",
            skipped={old_name: f"{kind} not found"},
        )

    if dry_run:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="succeeded",
            processed=(old_name,),
        )

    # Apply.
    try:
        if kind == "channel":
            store.rename_channel(old_name, new_name)
        else:
            store.rename_item(
                _hdf5_path_for_kind(kind, old_name),
                _hdf5_path_for_kind(kind, new_name),
            )
    except ValueError as exc:
        # rename_item / rename_channel raise ValueError on
        # target-exists collisions. Per-dataset error; batch
        # continues.
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            errors={old_name: str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "rename %s %r → %r failed on %s",
            kind, old_name, new_name, h5_path,
        )
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            errors={old_name: f"{type(exc).__name__}: {exc}"},
        )

    return BatchOperationItemResult(
        h5_path=h5_path,
        status="succeeded",
        processed=(old_name,),
    )
