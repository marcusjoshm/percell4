"""Tests for the Qt-free stitching grid-type / order vocabulary (U2).

These pin the alias equivalence the whole refactor rests on: ``TileConfig``
accepts eight ``order`` strings, but they are four behaviors under two alias
sets. Getting the mapping wrong silently misplaces tiles, so the equivalence is
asserted here against the real domain function rather than restated by hand.
"""

from __future__ import annotations

import pytest

from percell4.domain.io.assembler import _tile_positions
from percell4.domain.io.models import TileConfig
from percell4.gui._stitch_order import (
    CORNERS,
    GRID_TYPES,
    is_row_major,
    normalize_order,
)


def test_every_corner_maps_to_itself() -> None:
    for corner in CORNERS:
        assert normalize_order(corner) == corner


@pytest.mark.parametrize(
    ("row_centric", "corner"),
    [
        ("right_down", "top_left"),
        ("left_down", "top_right"),
        ("right_up", "bottom_left"),
        ("left_up", "bottom_right"),
    ],
)
def test_row_centric_alias_maps_to_its_corner(row_centric: str, corner: str) -> None:
    assert normalize_order(row_centric) == corner


@pytest.mark.parametrize(
    ("row_centric", "corner"),
    [
        ("right_down", "top_left"),
        ("left_down", "top_right"),
        ("right_up", "bottom_left"),
        ("left_up", "bottom_right"),
    ],
)
def test_alias_pairs_produce_identical_placement(row_centric: str, corner: str) -> None:
    """The mapping is only correct if the domain agrees.

    Uses a deliberately NON-SQUARE grid — a transposition or a row/col mixup is
    invisible on the square grids most tests use.
    """
    for grid_type in GRID_TYPES:
        assert _tile_positions(3, 4, grid_type, row_centric) == _tile_positions(
            3, 4, grid_type, corner
        ), f"{row_centric} != {corner} under {grid_type}"


def test_normalize_covers_every_value_tileconfig_accepts() -> None:
    """No accepted ``order`` may fall through normalization.

    A gap here would surface as the dialog silently falling back to its default
    when seeding from a dataset — wrong tiles, no error.
    """
    accepted = [
        "right_down", "right_up", "left_down", "left_up",
        "top_left", "top_right", "bottom_left", "bottom_right",
    ]
    for value in accepted:
        TileConfig(order=value)  # proves the domain really accepts it
        assert normalize_order(value) in CORNERS


def test_normalize_raises_rather_than_defaulting() -> None:
    """Silently defaulting is the failure mode this refactor exists to kill."""
    with pytest.raises(ValueError, match="Unknown stitching order"):
        normalize_order("sideways")


def test_row_major_classification_matches_fiji_order_sets() -> None:
    """Row types take the Right/Left order labels; column types take Down/Up."""
    assert is_row_major("row_by_row") is True
    assert is_row_major("snake_by_row") is True
    assert is_row_major("column_by_column") is False
    assert is_row_major("snake_by_column") is False


def test_row_major_rejects_unknown_grid_type() -> None:
    with pytest.raises(ValueError, match="Unknown grid_type"):
        is_row_major("diagonal")


def test_grid_types_match_the_domain_vocabulary() -> None:
    """The UI list and the domain validator must not drift apart."""
    for grid_type in GRID_TYPES:
        TileConfig(grid_type=grid_type)
    with pytest.raises(ValueError):
        TileConfig(grid_type="not_a_type")
