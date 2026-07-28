"""Canonical tile-stitching grid-type / order vocabulary.

Qt-free so it is unit-testable without a ``QApplication``. This module is the
single place that knows how the stitching combos' *display labels* relate to the
*values* handed to ``TileConfig``.

The distinction matters. ``TileConfig.order`` accepts eight strings, but
``assembler._tile_positions`` normalizes them to two booleans
``(start_bottom, start_right)`` — so the eight are really **four behaviors under
two alias sets**:

===============  ===============  ==========================  =============
corner name      row-centric      ``(start_bottom, right)``   tile 0 sits
===============  ===============  ==========================  =============
``top_left``     ``right_down``   (False, False)              top-left
``top_right``    ``left_down``    (False, True)               top-right
``bottom_left``  ``right_up``     (True, False)               bottom-left
``bottom_right`` ``left_up``      (True, True)                bottom-right
===============  ===============  ==========================  =============

Which corner tile 0 occupies is independent of ``grid_type`` — the grid type
only decides whether the scan walks rows or columns first. That is why a single
corner vocabulary serves every type, and why switching type can preserve the
user's choice rather than resetting it.

The corner names are the canonical carrier: they read correctly under both row
and column types, whereas ``right_up`` is row-centric and reads wrong under a
column type. The row-centric names remain fully accepted on input — they appear
in existing ``.h5`` ``/metadata`` and in existing ``run_config.json`` plans, and
:func:`normalize_order` maps them in.
"""

from __future__ import annotations

# The four canonical corner values, in the order the UI presents them.
CORNERS: tuple[str, ...] = ("top_left", "top_right", "bottom_left", "bottom_right")

# The four scan patterns, in the order the UI presents them.
GRID_TYPES: tuple[str, ...] = (
    "row_by_row",
    "column_by_column",
    "snake_by_row",
    "snake_by_column",
)

# Grid types whose scan walks along a row first (vs. down a column first).
ROW_MAJOR_TYPES: frozenset[str] = frozenset({"row_by_row", "snake_by_row"})

# Every value ``TileConfig.order`` accepts, mapped onto its canonical corner.
# The identity entries keep ``normalize_order`` total over the accepted set.
_ORDER_TO_CORNER: dict[str, str] = {
    "top_left": "top_left",
    "top_right": "top_right",
    "bottom_left": "bottom_left",
    "bottom_right": "bottom_right",
    "right_down": "top_left",
    "left_down": "top_right",
    "right_up": "bottom_left",
    "left_up": "bottom_right",
}


# ── Display labels (Fiji Grid/Collection Stitching vocabulary) ──────────
#
# Taken from the plugin's own ``GridType.java``: ``choose1`` for Type and
# ``choose2[gridType]`` for Order. The Order set is keyed to the Type, which is
# the dependency this vocabulary reproduces.
#
# Order labels read "travel & step": for a row type the first word is the
# direction along a row and the second is how rows advance; for a column type
# the first word is the direction down a column and the second is how columns
# advance. Either way the pair resolves to the corner tile 0 occupies, which is
# what the value carries.

GRID_TYPE_LABELS: dict[str, str] = {
    "row_by_row": "Row-by-row",
    "column_by_column": "Column-by-column",
    "snake_by_row": "Snake-by-row",
    "snake_by_column": "Snake-by-column",
}

# (label, corner value) pairs, in Fiji's presentation order.
_ROW_ORDER_LABELS: tuple[tuple[str, str], ...] = (
    ("Right & Down", "top_left"),
    ("Left & Down", "top_right"),
    ("Right & Up", "bottom_left"),
    ("Left & Up", "bottom_right"),
)

_COLUMN_ORDER_LABELS: tuple[tuple[str, str], ...] = (
    ("Down & Right", "top_left"),
    ("Down & Left", "top_right"),
    ("Up & Right", "bottom_left"),
    ("Up & Left", "bottom_right"),
)


def order_labels_for(grid_type: str) -> tuple[tuple[str, str], ...]:
    """The ``(label, value)`` pairs the Order combo shows for ``grid_type``.

    The *values* are identical across both sets and in the same positions —
    only the wording differs — which is what lets a Type change preserve the
    user's current pick instead of resetting it.
    """
    return _ROW_ORDER_LABELS if is_row_major(grid_type) else _COLUMN_ORDER_LABELS


def normalize_order(value: str) -> str:
    """Map any accepted ``TileConfig.order`` string onto its canonical corner.

    Used when seeding the UI from persisted state — a dataset's ``/metadata``
    may carry either vocabulary, and the combo carries only corners.

    Raises ``ValueError`` rather than silently defaulting: a miss here would
    otherwise surface as the dialog quietly falling back to ``top_left``, which
    misplaces tiles with no error. Callers that must tolerate junk should catch
    it and leave their default selected explicitly.
    """
    try:
        return _ORDER_TO_CORNER[value]
    except KeyError:
        raise ValueError(
            f"Unknown stitching order {value!r}, must be one of "
            f"{sorted(_ORDER_TO_CORNER)}"
        ) from None


def is_row_major(grid_type: str) -> bool:
    """True when the scan walks along a row before stepping to the next.

    Decides which of the two order-label sets the UI shows.
    """
    if grid_type not in GRID_TYPES:
        raise ValueError(
            f"Unknown grid_type {grid_type!r}, must be one of {list(GRID_TYPES)}"
        )
    return grid_type in ROW_MAJOR_TYPES
