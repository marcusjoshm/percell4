"""Channel intersection helper.

The batch workflow needs to compute which channels are common to every
selected dataset. Because datasets may still be ``tiff_pending`` at Start
time — no ``.h5`` file exists yet — the helper does not read from
``DatasetStore``. Instead the caller passes a list of ``(name, channels)``
tuples: for ``h5_existing`` entries they come from
``store.metadata["channel_names"]``; for ``tiff_pending`` entries they come
from the already-populated ``CompressConfig`` scan result.
"""

from __future__ import annotations

ChannelSource = tuple[str, list[str]]


def intersect_channels(
    sources: list[ChannelSource],
) -> tuple[list[str], list[str]]:
    """Compute the channel intersection and identify outlier datasets.

    Parameters
    ----------
    sources
        List of ``(dataset_name, channel_names)`` tuples. May be empty.

    Returns
    -------
    intersection
        Channel names present in every source, in the order they appear in
        the *first* source. Empty list if no common channels exist or if
        ``sources`` itself is empty.
    outliers
        Dataset names that have **zero** channels in common with the other
        sources. These are the datasets the user probably meant to leave
        out (wrong folder, corrupt metadata, etc.). The config dialog
        prompts the user to drop them and continue or abort. Always empty
        if ``sources`` is empty or has a single entry.

    Semantics
    ---------
    - If every source shares at least one channel, ``outliers`` is empty and
      ``intersection`` is the common set.
    - If at least one source has zero overlap with the rest, the function
      first computes the intersection over the *other* sources, then
      classifies as outliers any source with zero channels in that
      intersection. This handles the typical "user dragged in one wrong
      folder" case cleanly.
    - If the full intersection is empty *and* no single-source outlier
      explains it (every source contributes to the mismatch), all sources
      are returned as outliers and ``intersection`` is empty.
    """
    if not sources:
        return [], []

    if len(sources) == 1:
        only_name, only_channels = sources[0]
        # De-dupe while preserving order.
        seen: set[str] = set()
        intersection: list[str] = []
        for ch in only_channels:
            if ch not in seen:
                seen.add(ch)
                intersection.append(ch)
        return intersection, []

    first_order = sources[0][1]
    sets = [set(channels) for _, channels in sources]
    full_intersection = set.intersection(*sets)

    if full_intersection:
        return _ordered(first_order, full_intersection), []

    # No full intersection — try to recover by dropping outliers.
    # An outlier is a source whose channels don't overlap with any other.
    outliers: list[str] = []
    for i, (name, ch_list) in enumerate(sources):
        own = set(ch_list)
        others = [sets[j] for j in range(len(sources)) if j != i]
        if not any(own & o for o in others):
            outliers.append(name)

    if outliers:
        remaining_sets = [
            sets[i] for i, (name, _) in enumerate(sources) if name not in outliers
        ]
        remaining_order = next(
            (ch_list for name, ch_list in sources if name not in outliers),
            [],
        )
        if remaining_sets:
            kept = set.intersection(*remaining_sets)
            return _ordered(remaining_order, kept), outliers
        return [], outliers

    # Every source has *some* overlap with *some* other source, but there is
    # no single channel common to all. The run can't proceed with any subset
    # without a real policy call from the user — report all as outliers so
    # the dialog surfaces it.
    return [], [name for name, _ in sources]


def _ordered(reference: list[str], keep: set[str]) -> list[str]:
    """Filter ``reference`` to entries in ``keep``, preserving first-occurrence order."""
    seen: set[str] = set()
    out: list[str] = []
    for ch in reference:
        if ch in keep and ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out
