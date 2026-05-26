"""Regression: the single-cell workflow's compress_plan must persist tile_config.

The bug: ``_add_tiff_via_compress_dialog`` built a compress_plan dict
from a ``CompressConfig`` but never serialized the tile_config the user
configured in the CompressDialog. Run_config.json was therefore
written without a tile_config key, and compress_one couldn't know to
stitch — so the .h5 ended up with only the first scene's pixels.

Tests target the pure helper extracted as part of the fix so the
construction logic is unit-testable without a running QApplication.
"""

from __future__ import annotations

from pathlib import Path

from percell4.domain.io.models import (
    CompressConfig,
    DatasetGuiState,
    DatasetSpec,
    DiscoveredFile,
    TileConfig,
)
from percell4.gui.workflows.single_cell.config_dialog import (
    _build_compress_plan,
)


def _spec(name: str, files: tuple[Path, ...]) -> DatasetSpec:
    return DatasetSpec(
        name=name,
        source_dir=Path("/tmp/src"),
        files=tuple(
            DiscoveredFile(path=p, tokens={"channel": "00"}) for p in files
        ),
        output_path=Path(f"/tmp/{name}.h5"),
    )


def test_compress_plan_serializes_global_tile_config() -> None:
    """When cfg.tile_config is set, the per-dataset compress_plan picks it up."""
    ds = _spec("ds1", (Path("a.tif"), Path("b.tif")))
    cfg = CompressConfig(
        z_project_method="mip",
        selected_channels={"00"},
        tile_config=TileConfig(grid_rows=2, grid_cols=3),
        datasets=[ds],
    )

    plan = _build_compress_plan(
        ds=ds,
        gui_state=None,
        cfg=cfg,
        selected_token_ids=["00"],
        layer_assignments_payload={},
    )

    assert plan.get("tile_config") == {
        "grid_rows": 2,
        "grid_cols": 3,
        "grid_type": "row_by_row",
        "order": "right_down",
    }


def test_compress_plan_per_dataset_override_beats_global() -> None:
    """A per-dataset tile_config_override takes precedence over cfg.tile_config."""
    ds = _spec("ds1", (Path("a.tif"),))
    gui_state = DatasetGuiState(
        tile_config_override=TileConfig(grid_rows=1, grid_cols=4),
    )
    cfg = CompressConfig(
        z_project_method="mip",
        selected_channels={"00"},
        tile_config=TileConfig(grid_rows=2, grid_cols=3),
        datasets=[ds],
    )

    plan = _build_compress_plan(
        ds=ds,
        gui_state=gui_state,
        cfg=cfg,
        selected_token_ids=["00"],
        layer_assignments_payload={},
    )

    assert plan.get("tile_config") == {
        "grid_rows": 1,
        "grid_cols": 4,
        "grid_type": "row_by_row",
        "order": "right_down",
    }


def test_compress_plan_omits_tile_config_when_none() -> None:
    """No stitching → no tile_config key (keeps run_config.json minimal)."""
    ds = _spec("ds1", (Path("a.tif"),))
    cfg = CompressConfig(
        z_project_method="mip",
        selected_channels={"00"},
        datasets=[ds],
    )

    plan = _build_compress_plan(
        ds=ds,
        gui_state=None,
        cfg=cfg,
        selected_token_ids=["00"],
        layer_assignments_payload={},
    )

    assert "tile_config" not in plan
