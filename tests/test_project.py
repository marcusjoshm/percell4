"""Tests for ProjectIndex."""

from __future__ import annotations

from pathlib import Path

import h5py
import pytest

from percell4.project import ProjectIndex


@pytest.fixture
def project(tmp_path):
    """Create a ProjectIndex with an initialized CSV."""
    idx = ProjectIndex(tmp_path / "project.csv")
    idx.create()
    return idx


def test_create_empty(project):
    """New project CSV has headers but no rows."""
    df = project.load()
    assert len(df) == 0
    assert "path" in df.columns
    assert "condition" in df.columns
    assert "status" in df.columns


def test_add_dataset(project):
    """Add a dataset row and verify it appears."""
    count = project.add_dataset(
        "data/exp1.h5", condition="control", replicate="1"
    )
    assert count == 1

    df = project.load()
    assert len(df) == 1
    assert df.iloc[0]["path"] == "data/exp1.h5"
    assert df.iloc[0]["condition"] == "control"
    assert df.iloc[0]["status"] == "complete"


def test_add_multiple_datasets(project):
    """Add multiple datasets."""
    project.add_dataset("a.h5", condition="ctrl")
    project.add_dataset("b.h5", condition="treated")
    count = project.add_dataset("c.h5", condition="ctrl")
    assert count == 3

    df = project.load()
    assert len(df) == 3


def test_remove_dataset(project):
    """Remove a dataset by path."""
    project.add_dataset("a.h5")
    project.add_dataset("b.h5")
    count = project.remove_dataset("a.h5")
    assert count == 1

    df = project.load()
    assert len(df) == 1
    assert df.iloc[0]["path"] == "b.h5"


def test_filter_by_condition(project):
    """Filter datasets by condition."""
    project.add_dataset("a.h5", condition="ctrl")
    project.add_dataset("b.h5", condition="treated")
    project.add_dataset("c.h5", condition="ctrl")

    result = project.filter(condition="ctrl")
    assert len(result) == 2
    assert set(result["path"]) == {"a.h5", "c.h5"}


def test_filter_multiple_columns(project):
    """Filter by multiple columns simultaneously."""
    project.add_dataset("a.h5", condition="ctrl", status="complete")
    project.add_dataset("b.h5", condition="ctrl", status="error")

    result = project.filter(condition="ctrl", status="complete")
    assert len(result) == 1
    assert result.iloc[0]["path"] == "a.h5"


def test_reconcile_finds_orphans(project, tmp_path):
    """reconcile() detects .h5 files not in the CSV."""
    # Create an .h5 file on disk but don't add to CSV
    orphan_path = tmp_path / "orphan.h5"
    with h5py.File(orphan_path, "w") as f:
        f.create_dataset("test", data=[1])

    result = project.reconcile(tmp_path)
    assert str(orphan_path) in result["orphan_files"]


def test_reconcile_finds_missing_files(project, tmp_path):
    """reconcile() detects CSV rows pointing to missing files."""
    project.add_dataset(str(tmp_path / "missing.h5"))

    result = project.reconcile(tmp_path)
    assert str(tmp_path / "missing.h5") in result["missing_files"]


def test_atomic_write_survives(project):
    """Multiple rapid writes don't corrupt the CSV."""
    for i in range(10):
        project.add_dataset(f"dataset_{i}.h5", condition=f"cond_{i % 3}")

    df = project.load()
    assert len(df) == 10


def test_load_nonexistent_returns_empty():
    """Loading a nonexistent CSV returns empty DataFrame with correct columns."""
    idx = ProjectIndex(Path("/nonexistent/project.csv"))
    df = idx.load()
    assert len(df) == 0
    assert "path" in df.columns
