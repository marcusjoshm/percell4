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
    """Compute the channel intersection across datasets.

    Parameters
    ----------
    sources
        List of ``(dataset_name, channel_names)`` tuples. May be empty.

    Returns
    -------
    intersection
        Channel names present in every source, in the order they appear in
        the *first* source (de-duplicated). Empty list if no common
        channels exist or if ``sources`` itself is empty.
    outliers
        Dataset names that explain why the intersection is empty. Currently
        all dataset names are reported when the intersection is empty —
        the Phase 1 rule is "all or nothing". A smarter "drop one outlier
        and retry" policy can land in Phase 2 when the config dialog
        actually needs it and the UX requirements are known.
        Always empty when the intersection is non-empty.
    """
    if not sources:
        return [], []

    sets = [set(channels) for _, channels in sources]
    full = set.intersection(*sets)
    if full:
        return _ordered(sources[0][1], full), []
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
