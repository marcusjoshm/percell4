"""Group datasets by their available-mask set.

The single-cell workflow config dialog lets the user reuse existing
``/masks`` layers instead of computing threshold rounds. When many datasets
expose the *same* set of mask layers, forcing the user to pick masks once
per dataset is prohibitive. This helper partitions datasets into groups that
share an identical available-mask set, so the dialog can render one shared
picker per group.

The logic is deliberately Qt-free and dialog-agnostic: the caller passes
``(name, available_masks)`` tuples (``name`` is the dataset's disambiguated
display name; ``available_masks`` is ``DatasetStore.list_masks()`` for
``h5_existing`` entries, or ``[]`` for ``tiff_pending`` / mask-less datasets).
Keeping it here — alongside ``channels.py`` / ``models.py`` in the workflows
core rather than under ``gui/`` — lets it be unit-tested in the local venv,
outside the ``qtbot``-gated GUI test suite.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Canonical, order-insensitive identity of a dataset's available masks.
MaskSignature = tuple[str, ...]


@dataclass(frozen=True)
class MaskGroupPlan:
    """One group of datasets sharing an identical available-mask set.

    ``signature`` is the canonical (sorted, de-duplicated) tuple of mask
    names — the empty tuple ``()`` marks the no-mask group. ``member_names``
    lists the datasets in this group in input (queue) order.
    """

    signature: MaskSignature
    member_names: list[str]


def _canonical(masks: Iterable[str]) -> MaskSignature:
    """Order-insensitive, de-duplicated signature for a mask list."""
    return tuple(sorted(set(masks)))


def group_by_mask_signature(
    items: Iterable[tuple[str, Iterable[str]]],
) -> list[MaskGroupPlan]:
    """Partition datasets into groups sharing an identical available-mask set.

    Parameters
    ----------
    items
        Ordered iterable of ``(name, available_masks)``. ``name`` must be
        unique across items (the dialog guarantees this via its display-name
        disambiguation pass). ``available_masks`` may be empty.

    Returns
    -------
    list[MaskGroupPlan]
        Groups in **first-appearance** order of their signature, with member
        names preserved in input order — **except** the empty-signature
        (no-mask) group, which is always placed last so the dialog can render
        it as a trailing non-selectable row. Returns ``[]`` for empty input.
    """
    order: list[MaskSignature] = []
    members: dict[MaskSignature, list[str]] = {}
    for name, masks in items:
        sig = _canonical(masks)
        if sig not in members:
            members[sig] = []
            order.append(sig)
        members[sig].append(name)

    # Empty signature (no-mask group) always sorts last.
    order.sort(key=lambda sig: sig == ())

    return [MaskGroupPlan(signature=sig, member_names=members[sig]) for sig in order]
