"""Canonical timepoint token ↔ index ↔ name mapping.

Single source of truth for how the ``_t(\\d+)`` filename token (parsed by
:class:`~percell4.domain.io.scanner.FileScanner`) maps to a zero-based
timepoint index and to the ``t<NN>`` name used in storage and downstream
frame indexing. Both the writer side (import, segmentation, tracking) and
the reader side use these helpers so a token like ``"3"`` never diverges
from a stored ``"t03"`` or an integer index ``3``.

Pure Python — no numpy, h5py, Qt, or napari.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "timepoint_label",
    "parse_timepoint_token",
    "ordered_timepoint_tokens",
    "count_timepoints",
]


def timepoint_label(index: int) -> str:
    """Return the canonical ``t<NN>`` name for a zero-based timepoint index.

    >>> timepoint_label(0)
    't00'
    >>> timepoint_label(12)
    't12'
    """
    if index < 0:
        raise ValueError(f"timepoint index must be >= 0, got {index}")
    return f"t{index:02d}"


def parse_timepoint_token(token: str) -> int:
    """Return the integer value of a parsed timepoint token.

    The token is the digits captured by the ``_t(\\d+)`` pattern, e.g.
    ``"00"``, ``"1"``, ``"10"``. Leading zeros are insignificant.

    >>> parse_timepoint_token("00")
    0
    >>> parse_timepoint_token("10")
    10
    """
    return int(token)


def ordered_timepoint_tokens(tokens: Iterable[str]) -> list[str]:
    """Return the unique timepoint tokens sorted by numeric value.

    Numeric (not lexical) ordering: ``_t2`` precedes ``_t10``. Duplicate
    tokens collapse to one entry. The original token strings are preserved
    (e.g. ``"00"`` stays ``"00"``) so callers can map back to filenames.

    >>> ordered_timepoint_tokens({"10", "2", "1"})
    ['1', '2', '10']
    """
    return sorted(set(tokens), key=parse_timepoint_token)


def count_timepoints(tokens: Iterable[str]) -> int:
    """Return the number of distinct timepoints implied by ``tokens``.

    An empty iterable (no ``_t`` token on any file) means a single,
    non-time-lapse acquisition → ``1``.
    """
    n = len(set(tokens))
    return n if n > 0 else 1
