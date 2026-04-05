"""Dataclasses for the import and batch compress pipelines."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
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


# ---------------------------------------------------------------------------
# Batch compress models
# ---------------------------------------------------------------------------


class LayerType(StrEnum):
    """Layer type for manual import assignment."""

    CHANNEL = "channel"
    SEGMENTATION = "segmentation"
    MASK = "mask"


class DiscoveryMode(StrEnum):
    """How datasets are discovered from a root directory."""

    SUBDIRECTORY = "subdirectory"
    FLAT = "flat"


class CompressMode(StrEnum):
    """Auto or manual layer assignment during compress."""

    AUTO = "auto"
    MANUAL = "manual"


@dataclass(frozen=True)
class FlimConfig:
    """FLIM calibration parameters for a dataset."""

    frequency_mhz: float = 80.0
    channel_calibrations: tuple[tuple[float, float], ...] = ()
    bin_x: int = 0
    bin_y: int = 0
    bin_t: int = 0
    bin_dtype: str = "uint16"
    bin_dim_order: str = "XYTC"
    bin_header_bytes: int = 0


@dataclass(frozen=True)
class LayerAssignment:
    """Per-file layer type and name override for manual mode."""

    layer_type: LayerType = LayerType.CHANNEL
    name: str = ""


@dataclass(frozen=True)
class DatasetSpec:
    """Immutable discovery result — what was found on disk."""

    name: str
    source_dir: Path | None
    files: tuple[DiscoveredFile, ...]
    output_path: Path
    scan_result: ScanResult | None = None
    tile_config: TileConfig | None = None
    flim_config: FlimConfig | None = None


@dataclass
class DatasetGuiState:
    """Mutable GUI state for a dataset (lives in the dialog, not io/)."""

    checked: bool = True
    layer_assignments: dict[Path, LayerAssignment] | None = None
    tile_config_override: TileConfig | None = None
    flim_config_override: FlimConfig | None = None


@dataclass
class CompressConfig:
    """Collected settings for a batch compress operation."""

    z_project_method: str = "mip"
    token_config: TokenConfig = field(default_factory=TokenConfig)
    output_dir: Path | None = None
    selected_channels: set[str] = field(default_factory=set)
    tile_config: TileConfig | None = None
    layer_assignments: dict[str, LayerAssignment] | None = None  # ch_id -> assignment
    dataset_name_overrides: dict[str, str] = field(default_factory=dict)  # orig -> new
    datasets: list[DatasetSpec] = field(default_factory=list)
    gui_states: dict[str, DatasetGuiState] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetResult:
    """Successful compression result for one dataset."""

    name: str
    output_path: Path


@dataclass(frozen=True)
class DatasetError:
    """Failed compression result for one dataset."""

    name: str
    error_message: str


@dataclass(frozen=True)
class BatchResult:
    """Result of a batch compress operation."""

    completed: tuple[DatasetResult, ...] = ()
    failed: tuple[DatasetError, ...] = ()
    cancelled: bool = False
