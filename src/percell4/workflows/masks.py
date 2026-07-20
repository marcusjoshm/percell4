"""Mask set operations for the existing-mask reuse picker.

The two-pane group builder in the single-cell workflow config dialog needs the
masks that are common to a set of selected datasets — a dataset can only be
assigned a mask it actually has, so a group's selectable masks are the
intersection of its datasets' available ``/masks`` layers. This mirrors
``channels.intersect_channels`` (which does the same for channel names) and is
kept Qt-free so it can be unit-tested in the local venv.
"""

from __future__ import annotations

from collections.abc import Iterable


def intersect_masks(mask_lists: Iterable[Iterable[str]]) -> list[str]:
    """Masks present in *every* dataset's available-mask set.

    Parameters
    ----------
    mask_lists
        One iterable of mask names per dataset. May be empty.

    Returns
    -------
    list[str]
        The intersection, sorted for a stable display order (matching
        ``DatasetStore.list_masks`` alphabetical ordering). Empty when
        ``mask_lists`` is empty or the datasets share no common mask.
    """
    sets = [set(masks) for masks in mask_lists]
    if not sets:
        return []
    return sorted(set.intersection(*sets))
