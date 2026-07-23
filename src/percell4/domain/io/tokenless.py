"""Tokenless channel-name derivation for name-suffixed TIFF imports.

Some microscopy exports identify a channel by *name* in the filename suffix
(``..._cells.tif``, ``..._DNA.tif``, ``..._G3BP1.tif``, ``..._SG_mask.tif``)
rather than by a numeric ``_ch00`` token.  This module derives the channel-name
vocabulary structurally from a flat set of filename stems — no ``chXX`` token and
no user-typed regex — and synthesizes an internal channel regex that the existing
scanner / discovery / import pipeline consumes unchanged.

Split rule (user-confirmed): within a dataset group, the shared *leading prefix*
is the ``.h5`` dataset name and the differing *trailing remainder* is the channel
name.  Multiple datasets in one flat folder are separated by their distinct
prefixes.

The one subtlety is multi-underscore channel names: ``SG_mask`` must be derived
as one channel — not split into ``SG`` + ``mask`` and orphaned into its own
singleton group.  ``derive_channel_names`` handles this with a grouping-
consistency rule (see below); the correct vocabulary yields rectangular groups
(every group carries the same channel set), whereas a too-shallow split leaves a
tell-tale ragged singleton that gets re-absorbed.

Pure domain: stdlib only, no Qt / h5py / ``percell4.store`` (import-linter
forbids those here).
"""

from __future__ import annotations

import re

from percell4.domain.io.models import _MAX_PATTERN_LENGTH


def derive_channel_names(
    stems: list[str] | tuple[str, ...],
) -> tuple[list[str], dict[str, str]]:
    """Derive the channel-name vocabulary from a flat set of filename stems.

    Parameters
    ----------
    stems:
        Filename stems (no directory, no extension), e.g.
        ``"CellProfiler_U2OS_60min_As_3x4_SG_mask"``.

    Returns
    -------
    (names, channel_of)
        ``names`` is the sorted unique channel-name vocabulary; ``channel_of``
        maps each input stem to its derived channel name.  For the motivating
        3-dataset / 4-channel set this returns
        ``(["DNA", "G3BP1", "SG_mask", "cells"], {stem: channel, ...})`` with
        every ``SG_mask`` file mapped to ``"SG_mask"``.

    Notes
    -----
    Grouping is ultimately performed downstream by ``discover_flat`` stripping
    the synthesized channel regex (see :func:`build_channel_pattern`); this
    function's contract is to return the *correct vocabulary*.  The per-stem map
    is returned for validation and callers that want the assignment directly.
    """
    stems = list(stems)
    if not stems:
        return [], {}

    # Provisional split: channel = segment after the last underscore.
    # channel_of[stem] and group_of[stem] evolve together as the consistency
    # rule re-absorbs orphaned multi-underscore channels.
    channel_of: dict[str, str] = {}
    group_of: dict[str, str] = {}
    for s in stems:
        group, sep, ch = s.rpartition("_")
        if not sep:
            # No underscore at all — the whole stem is the channel, group empty.
            group, ch = "", s
        channel_of[s] = ch
        group_of[s] = group

    # Consistency rule: if a provisional group G is a proper underscore-prefixed
    # descendant of another, richer group H (H has more distinct channels), then
    # G's files are actually H's files with a multi-underscore channel name. Move
    # them and extend the channel by the intervening segment(s). Iterate to a
    # fixpoint. The "richer" guard (len > , not >=) prevents merging two equally
    # sized, genuinely distinct datasets whose names share a prefix.
    for _ in range(len(stems) + 1):  # bounded; converges well before this
        groups = _groups_by_channel_count(stems, group_of, channel_of)
        changed = False
        # Process longest group names first so orphans fold into their immediate
        # parent rather than a more distant ancestor.
        for g in sorted(groups, key=len, reverse=True):
            parent = _richest_ancestor(g, groups)
            if parent is None:
                continue
            intervening = g[len(parent) + 1:]  # segment(s) between parent_ and g
            for s in stems:
                if group_of[s] == g:
                    group_of[s] = parent
                    channel_of[s] = f"{intervening}_{channel_of[s]}"
            changed = True
        if not changed:
            break

    names = sorted(set(channel_of.values()))
    return names, channel_of


def _groups_by_channel_count(
    stems: list[str],
    group_of: dict[str, str],
    channel_of: dict[str, str],
) -> dict[str, set[str]]:
    """Map each current group name to its set of distinct channel names."""
    groups: dict[str, set[str]] = {}
    for s in stems:
        groups.setdefault(group_of[s], set()).add(channel_of[s])
    return groups


def _richest_ancestor(group: str, groups: dict[str, set[str]]) -> str | None:
    """Longest existing group that is a proper ``prefix_`` ancestor of *group*
    and carries strictly more distinct channels than *group* itself."""
    g_count = len(groups[group])
    best: str | None = None
    for other, channels in groups.items():
        if other == group:
            continue
        if group.startswith(other + "_") and len(channels) > g_count:
            if best is None or len(other) > len(best):
                best = other
    return best


def build_channel_pattern(names: list[str] | tuple[str, ...]) -> str:
    """Synthesize an end-anchored channel regex from a name vocabulary.

    Alternatives are escaped and ordered longest-first so a multi-underscore
    name (``SG_mask``) wins over any shorter name that is its substring, and the
    pattern is anchored to the end of the stem so only the trailing channel
    suffix matches (never a channel name appearing earlier in a prefix). The
    single capture group returns the channel name verbatim, exactly like the
    numeric ``_ch(\\d+)`` default — so the scanner, ``_derive_dataset_name``
    grouping, and the importer all consume it with no further change.

    Raises
    ------
    ValueError
        If *names* is empty, or the synthesized pattern would exceed
        ``TokenConfig``'s ``_MAX_PATTERN_LENGTH`` (raised here with a clear
        message rather than surfacing later as a raw ``re.error``).
    """
    unique = [n for n in dict.fromkeys(names) if n]
    if not unique:
        raise ValueError("cannot build a channel pattern from an empty vocabulary")
    # Longest-first, then lexical, for deterministic, substring-safe alternation.
    ordered = sorted(unique, key=lambda n: (-len(n), n))
    alternation = "|".join(re.escape(n) for n in ordered)
    pattern = rf"_({alternation})$"
    if len(pattern) > _MAX_PATTERN_LENGTH:
        raise ValueError(
            f"derived channel vocabulary is too large for a token pattern "
            f"({len(pattern)} > {_MAX_PATTERN_LENGTH} chars); "
            f"{len(unique)} channel names"
        )
    return pattern
