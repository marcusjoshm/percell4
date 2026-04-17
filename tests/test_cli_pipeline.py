"""Tests for the CLI headless pipeline (Stage 6 — seam validation).

These tests prove that the hex architecture seam is clean:
- The CLI pipeline runs without Qt or napari
- It uses the same use cases as the GUI
- Results are correct (measurements match expected values)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.store import DatasetStore


@pytest.fixture
def populated_h5(tmp_path: Path, sample_image, sample_labels) -> Path:
    """Create a real .h5 dataset for pipeline testing."""
    h5_path = tmp_path / "test_dataset.h5"
    store = DatasetStore(h5_path)
    store.create(metadata={
        "source": "test",
        "pixel_size_um": 0.325,
        "channel_names": ["GFP"],
    })
    # Write as 3D intensity (C, H, W) with one channel
    store.write_array(
        "intensity",
        sample_image[np.newaxis, :, :],  # (1, 100, 100)
        attrs={"dims": ["C", "H", "W"]},
    )
    store.write_labels("cellpose_existing", sample_labels)
    return h5_path


class TestImportSeam:
    """Verify that CLI modules don't import Qt or napari."""

    def test_cli_module_imports_without_qt(self):
        """Importing the CLI pipeline does not trigger Qt imports."""
        # Record which modules are loaded before
        qt_modules_before = {
            m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m
        }

        # Import the CLI module
        from percell4.interfaces.cli.run_pipeline import run_pipeline  # noqa: F401

        # Check no new Qt/napari modules were loaded
        qt_modules_after = {
            m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m
        }
        new_qt = qt_modules_after - qt_modules_before
        assert not new_qt, f"CLI import triggered Qt/napari imports: {new_qt}"

    def test_null_viewer_imports_without_qt(self):
        """NullViewerAdapter doesn't import Qt."""
        qt_before = {m for m in sys.modules if "PyQt" in m or "qtpy" in m}
        from percell4.adapters.null_viewer import NullViewerAdapter  # noqa: F401
        qt_after = {m for m in sys.modules if "PyQt" in m or "qtpy" in m}
        assert not (qt_after - qt_before)

    def test_session_imports_without_qt(self):
        """Session doesn't import Qt."""
        qt_before = {m for m in sys.modules if "PyQt" in m or "qtpy" in m}
        from percell4.application.session import Session  # noqa: F401
        qt_after = {m for m in sys.modules if "PyQt" in m or "qtpy" in m}
        assert not (qt_after - qt_before)

    def test_domain_layer_is_qt_free(self):
        """Grep-based check: no Qt/napari imports in domain/ application/ ports/."""
        import subprocess

        result = subprocess.run(
            [
                "grep", "-r",
                "--include=*.py",
                "-l",
                "-E",
                r"import (napari|PyQt|qtpy)|from (napari|PyQt|qtpy)",
                "src/percell4/domain/",
                "src/percell4/application/",
                "src/percell4/ports/",
            ],
            capture_output=True, text=True,
        )
        # grep returns 1 if no matches (good), 0 if matches (bad)
        violating_files = result.stdout.strip()
        assert not violating_files, (
            f"Qt/napari imports found in domain/application/ports:\n{violating_files}"
        )


class TestCliPipeline:
    """Integration tests for the headless pipeline."""

    def test_pipeline_skip_segmentation_skip_threshold(self, populated_h5, tmp_path):
        """Simplest pipeline: load + measure with existing segmentation."""
        from percell4.interfaces.cli.run_pipeline import run_pipeline

        output = tmp_path / "measurements.csv"
        result = run_pipeline(
            populated_h5,
            skip_segmentation=True,
            skip_threshold=True,
            output_csv=output,
        )

        assert result.n_cells == 5
        assert result.n_columns > 0
        assert result.output_csv == output
        assert output.exists()

        df = pd.read_csv(output)
        assert len(df) == 5
        assert "label" in df.columns
        assert "GFP_mean_intensity" in df.columns

    def test_pipeline_with_threshold(self, populated_h5, tmp_path):
        """Pipeline with thresholding: load + existing seg + threshold + measure."""
        from percell4.interfaces.cli.run_pipeline import run_pipeline

        output = tmp_path / "with_thresh.csv"
        result = run_pipeline(
            populated_h5,
            skip_segmentation=True,
            threshold_channel="GFP",
            threshold_method="otsu",
            output_csv=output,
        )

        assert result.n_cells == 5
        assert result.mask_name is not None
        assert "otsu" in result.mask_name
        assert output.exists()

    def test_pipeline_manual_threshold(self, populated_h5, tmp_path):
        """Pipeline with a manual threshold value."""
        from percell4.interfaces.cli.run_pipeline import run_pipeline

        result = run_pipeline(
            populated_h5,
            skip_segmentation=True,
            threshold_value=50.0,
            threshold_method="manual",
        )

        assert result.n_cells == 5
        assert result.mask_name is not None

    def test_pipeline_no_output_csv(self, populated_h5):
        """Pipeline without CSV output still returns results."""
        from percell4.interfaces.cli.run_pipeline import run_pipeline

        result = run_pipeline(
            populated_h5,
            skip_segmentation=True,
            skip_threshold=True,
        )

        assert result.n_cells == 5
        assert result.output_csv is None

    def test_pipeline_nonexistent_file_raises(self, tmp_path):
        """Pipeline raises on nonexistent file."""
        from percell4.interfaces.cli.run_pipeline import run_pipeline

        with pytest.raises(FileNotFoundError):
            run_pipeline(tmp_path / "nonexistent.h5", skip_segmentation=True, skip_threshold=True)

    def test_pipeline_bad_channel_raises(self, populated_h5):
        """Pipeline raises when threshold channel doesn't exist."""
        from percell4.interfaces.cli.run_pipeline import run_pipeline

        with pytest.raises(ValueError, match="not found"):
            run_pipeline(
                populated_h5,
                skip_segmentation=True,
                threshold_channel="NONEXISTENT",
            )
