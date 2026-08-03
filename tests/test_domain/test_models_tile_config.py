"""Tests for ``TileConfig`` registration fields (U2).

``TileConfig`` is a dumb carrier: it validates only the single-field rule
``0.0 <= overlap < 1.0``. The cross-field rule ``register ⇒ overlap>0 ∧
reference_channel set`` is enforced at the importer gate, not in the dataclass,
so back-compat construction and ``register=True, overlap=0.0`` stay valid.
"""

from __future__ import annotations

import pytest

from percell4.domain.io.models import TileConfig


def test_defaults_are_backcompat() -> None:
    """A minimal grid config defaults the new registration fields off."""
    cfg = TileConfig(grid_rows=1, grid_cols=1)
    assert cfg.register is False
    assert cfg.overlap == 0.0
    assert cfg.reference_channel is None
    assert cfg.fusion_method == "none"  # measurement-correct default


def test_fusion_method_linear_blending_valid() -> None:
    cfg = TileConfig(grid_rows=2, grid_cols=2, fusion_method="linear_blending")
    assert cfg.fusion_method == "linear_blending"


@pytest.mark.parametrize("bad", ["blend", "average", "Linear_Blending", ""])
def test_invalid_fusion_method_raises(bad: str) -> None:
    with pytest.raises(ValueError, match="fusion_method"):
        TileConfig(grid_rows=2, grid_cols=2, fusion_method=bad)


def test_frozen_and_hashable_equality() -> None:
    """Two default instances are == and hash-equal (frozen + hashable)."""
    a = TileConfig(grid_rows=1, grid_cols=1)
    b = TileConfig(grid_rows=1, grid_cols=1)
    assert a == b
    assert hash(a) == hash(b)
    # Hashable means usable in a set / as a dict key.
    assert len({a, b}) == 1


@pytest.mark.parametrize("bad_overlap", [-0.1, 1.0, 1.5, -1.0])
def test_overlap_out_of_range_raises(bad_overlap: float) -> None:
    """overlap outside [0.0, 1.0) is rejected in __post_init__."""
    with pytest.raises(ValueError, match="overlap"):
        TileConfig(grid_rows=2, grid_cols=2, overlap=bad_overlap)


@pytest.mark.parametrize("ok_overlap", [0.0, 0.1, 0.5, 0.99])
def test_overlap_in_range_constructs(ok_overlap: float) -> None:
    """overlap inside [0.0, 1.0) constructs cleanly."""
    cfg = TileConfig(grid_rows=2, grid_cols=2, overlap=ok_overlap)
    assert cfg.overlap == ok_overlap


def test_cross_field_rule_not_enforced_in_dataclass() -> None:
    """``register=True`` with ``overlap=0.0`` constructs WITHOUT error.

    Cross-field validation is deferred to the importer gate; coupling it back
    into the frozen carrier would break this guard.
    """
    cfg = TileConfig(register=True, overlap=0.0)
    assert cfg.register is True
    assert cfg.overlap == 0.0
    assert cfg.reference_channel is None
