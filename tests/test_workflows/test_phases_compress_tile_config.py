"""Regression test: phases.compress_one must forward tile_config.

Before the fix, ``compress_one`` ignored any ``tile_config`` key in the
compress_plan dict and called ``import_dataset`` without a
``tile_config=`` argument. Combined with the silent
``_load_and_stitch`` fallback this meant multi-tile datasets compressed
through the single-cell workflow lost all tiles except the first.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from percell4.workflows.models import (
    DatasetSource,
    WorkflowDatasetEntry,
)
from percell4.workflows.phases import compress_one


def test_compress_one_forwards_tile_config_to_import_dataset(
    tmp_path: Path,
) -> None:
    """A compress_plan with tile_config must yield a TileConfig kwarg."""
    output_h5 = tmp_path / "output.h5"
    entry = WorkflowDatasetEntry(
        name="multi_tile_dataset",
        source=DatasetSource.TIFF_PENDING,
        h5_path=output_h5,
        channel_names=["mScar", "mNG"],
        compress_plan={
            "source_dir": str(tmp_path),
            "files": [str(tmp_path / "fake.tif")],
            "output_path": str(output_h5),
            "z_project_method": "mip",
            "selected_channels": ["00", "01"],
            "layer_assignments": {},
            "tile_config": {
                "grid_rows": 2,
                "grid_cols": 3,
                "grid_type": "row_by_row",
                "order": "right_down",
            },
        },
    )

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        compress_one(entry)

    assert mock_import.called, "import_dataset was not called"
    kwargs = mock_import.call_args.kwargs
    tc = kwargs.get("tile_config")
    assert tc is not None, (
        "tile_config dropped between compress_plan and import_dataset call"
    )
    assert tc.grid_rows == 2
    assert tc.grid_cols == 3
    assert tc.grid_type == "row_by_row"
    assert tc.order == "right_down"


def test_compress_one_without_tile_config_passes_none(tmp_path: Path) -> None:
    """A compress_plan without tile_config should pass None (no stitching)."""
    output_h5 = tmp_path / "output.h5"
    entry = WorkflowDatasetEntry(
        name="single_tile_dataset",
        source=DatasetSource.TIFF_PENDING,
        h5_path=output_h5,
        channel_names=["mScar"],
        compress_plan={
            "source_dir": str(tmp_path),
            "files": [str(tmp_path / "fake.tif")],
            "output_path": str(output_h5),
            "z_project_method": "mip",
            "selected_channels": ["00"],
            "layer_assignments": {},
        },
    )

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        compress_one(entry)

    assert mock_import.called
    kwargs = mock_import.call_args.kwargs
    assert kwargs.get("tile_config") is None
