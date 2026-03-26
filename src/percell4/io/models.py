"""Dataclasses for the import pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_MAX_PATTERN_LENGTH = 200


@dataclass(frozen=True)
class TokenConfig:
    """Regex patterns for parsing filename tokens.

    Each pattern must contain exactly one capture group ``(...)`` that
    extracts the token value (e.g., channel number, z-slice index).
    Set a pattern to ``None`` to disable that token.
    """

    channel: str | None = r"_ch(\d+)"
    timepoint: str | None = r"_t(\d+)"
    z_slice: str | None = r"_z(\d+)"
    tile: str | None = r"_s(\d+)"

    def __post_init__(self) -> None:
        for field_name in ("channel", "timepoint", "z_slice", "tile"):
            pattern = getattr(self, field_name)
            if pattern is None:
                continue
            if len(pattern) > _MAX_PATTERN_LENGTH:
                raise ValueError(
                    f"{field_name} pattern exceeds {_MAX_PATTERN_LENGTH} chars"
                )
            try:
                compiled = re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    f"{field_name} has invalid regex {pattern!r}: {e}"
                ) from e
            if compiled.groups < 1:
                raise ValueError(
                    f"{field_name} pattern {pattern!r} must have at least one"
                    " capture group (...)"
                )


@dataclass(frozen=True)
class TileConfig:
    """Configuration for tile stitching layout."""

    grid_rows: int = 1
    grid_cols: int = 1
    grid_type: str = "row_by_row"  # row_by_row, column_by_column, snake_by_row, snake_by_column
    order: str = "right_down"  # right_down, right_up, left_down, left_up

    def __post_init__(self) -> None:
        valid_types = {"row_by_row", "column_by_column", "snake_by_row", "snake_by_column"}
        if self.grid_type not in valid_types:
            raise ValueError(
                f"Unknown grid_type {self.grid_type!r}, must be one of {valid_types}"
            )
        valid_orders = {
            "right_down", "right_up", "left_down", "left_up",
            "top_left", "top_right", "bottom_left", "bottom_right",
        }
        if self.order not in valid_orders:
            raise ValueError(
                f"Unknown order {self.order!r}, must be one of {valid_orders}"
            )


@dataclass(frozen=True)
class DiscoveredFile:
    """A single file discovered during scanning."""

    path: Path
    tokens: dict[str, str]  # e.g. {"channel": "00", "z_slice": "03"}
    shape: tuple[int, ...] | None = None
    dtype: str | None = None
    pixel_size_um: float | None = None


@dataclass
class ScanResult:
    """Result of scanning a directory for image files."""

    files: list[DiscoveredFile] = field(default_factory=list)
    channels: set[str] = field(default_factory=set)
    timepoints: set[str] = field(default_factory=set)
    z_slices: set[str] = field(default_factory=set)
    tiles: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
