"""Use case: batch set / append / clear the description across .h5 datasets.

One description operation, applied to every input ``.h5``. Per-dataset
failures isolate -- an unreadable file is recorded as a per-dataset error
and the batch continues to the next file.

Sister of :mod:`percell4.application.use_cases.batch_rename_resource` and
:mod:`percell4.application.use_cases.batch_delete_resource`; it reuses their
report dataclasses so the shared CLI print helpers format all three the same
way.

The orchestrator is pure-Python and Session-free: it talks directly to
``DatasetStore``. The ``description`` / ``set_description`` /
``clear_description`` accessors do the real work; this module is
composition and classification.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    BatchOperationReport,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


VALID_VERBS: tuple[str, ...] = ("set", "append", "clear")

# Separator between an existing description and appended text. A blank line
# keeps shared experiment context and per-dish detail visually distinct
# once several appends have accumulated.
_APPEND_SEPARATOR = "\n\n"

# Label used for the single "resource" each per-dataset result touches. The
# report dataclasses are keyed by resource name; a description has no name,
# so this constant stands in for one.
_RESOURCE = "description"


def join_description(existing: str | None, addition: str) -> str:
    """Return ``existing`` with ``addition`` appended below it.

    Appending onto a dataset with no description yields the addition alone,
    with no leading blank line. Trailing whitespace on the existing text is
    stripped first so repeated appends do not accumulate blank lines.
    """
    if existing is None or not existing.strip():
        return addition
    return existing.rstrip() + _APPEND_SEPARATOR + addition


def batch_set_description(
    h5_paths: list[Path],
    *,
    verb: str,
    text: str | None = None,
    dry_run: bool = False,
    progress_callback: Callable[[BatchOperationItemResult], None] | None = None,
) -> BatchOperationReport:
    """Apply one description operation to every ``.h5`` in ``h5_paths``.

    Per-dataset isolation: an unreadable or missing file is reported as a
    dataset-level error; a ``clear`` on a dataset that has no description is
    reported as skipped. The batch always completes.

    Args:
        h5_paths: Datasets to operate on.
        verb: One of ``"set"``, ``"append"``, or ``"clear"``.
        text: The description text. Required for ``set`` and ``append``,
            ignored for ``clear``.
        dry_run: When True, classify exactly as a live run would but do not
            mutate any file.
        progress_callback: Invoked once per dataset after its
            :class:`BatchOperationItemResult` is built.

    Returns:
        A :class:`BatchOperationReport` with one item per input path.
    """
    if verb not in VALID_VERBS:
        raise ValueError(f"verb must be one of {VALID_VERBS}, got {verb!r}")
    if verb != "clear" and (text is None or not text.strip()):
        raise ValueError(f"verb {verb!r} requires non-empty text")

    results: list[BatchOperationItemResult] = []
    for h5_path in h5_paths:
        result = _describe_one_dataset(
            h5_path=h5_path,
            verb=verb,
            text=text,
            dry_run=dry_run,
        )
        results.append(result)
        if progress_callback is not None:
            progress_callback(result)
    return BatchOperationReport(items=tuple(results))


def _describe_one_dataset(
    *,
    h5_path: Path,
    verb: str,
    text: str | None,
    dry_run: bool,
) -> BatchOperationItemResult:
    """Run one description operation. Catches all per-dataset failures."""
    # Open / sanity-check the store first. Reading the current description
    # also proves the file is a readable .h5 before we classify anything --
    # the same pre-flight the rename/delete use cases do.
    try:
        store = DatasetStore(h5_path)
        if not store.exists():
            raise FileNotFoundError(f"file not found: {h5_path}")
        existing = store.description
    except Exception as exc:  # noqa: BLE001
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"open failed: {exc}",
        )

    # Clearing a dataset that has nothing to clear is a no-op. Classified
    # here so the batch summary reads honestly ("skipped: no description")
    # instead of "succeeded".
    if verb == "clear" and existing is None:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="skipped_no_changes",
            skipped={_RESOURCE: "no description to clear"},
        )

    if dry_run:
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="succeeded",
            processed=(_RESOURCE,),
        )

    try:
        if verb == "clear":
            store.clear_description()
        elif verb == "set":
            store.set_description(text)
        else:  # append
            store.set_description(join_description(existing, text or ""))
    except Exception as exc:  # noqa: BLE001
        logger.exception("description %s failed on %s", verb, h5_path)
        return BatchOperationItemResult(
            h5_path=h5_path,
            status="failed",
            errors={_RESOURCE: f"{type(exc).__name__}: {exc}"},
        )

    return BatchOperationItemResult(
        h5_path=h5_path,
        status="succeeded",
        processed=(_RESOURCE,),
    )
