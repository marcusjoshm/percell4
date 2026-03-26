"""Project index management via a flat CSV file.

Each row in project.csv represents one dataset (.h5 file). No hierarchy,
no database — just pandas. Writes are atomic (temp file + os.replace).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

# Required columns in project.csv
_COLUMNS = ["path", "condition", "replicate", "notes", "status"]


class ProjectIndex:
    """Thin wrapper around a project.csv file with atomic writes."""

    def __init__(self, csv_path: str | Path) -> None:
        self.csv_path = Path(csv_path)

    def create(self) -> None:
        """Create a new empty project.csv with header row."""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(columns=_COLUMNS)
        self._write_atomic(df)

    def exists(self) -> bool:
        return self.csv_path.exists()

    def load(self) -> pd.DataFrame:
        """Load the full project index as a DataFrame."""
        if not self.csv_path.exists():
            return pd.DataFrame(columns=_COLUMNS)
        return pd.read_csv(self.csv_path, dtype=str).fillna("")

    def add_dataset(
        self,
        path: str,
        condition: str = "",
        replicate: str = "",
        notes: str = "",
        status: str = "complete",
    ) -> int:
        """Append a dataset row. Returns new total row count."""
        df = self.load()
        new_row = pd.DataFrame(
            [
                {
                    "path": str(path),
                    "condition": condition,
                    "replicate": replicate,
                    "notes": notes,
                    "status": status,
                }
            ]
        )
        df = pd.concat([df, new_row], ignore_index=True)
        self._write_atomic(df)
        return len(df)

    def remove_dataset(self, path: str) -> int:
        """Remove a dataset row by path. Returns new total row count.

        Does NOT delete the .h5 file — only removes the CSV row.
        """
        df = self.load()
        df = df[df["path"] != str(path)].reset_index(drop=True)
        self._write_atomic(df)
        return len(df)

    def filter(self, **kwargs: str) -> pd.DataFrame:
        """Filter datasets by column values.

        Example: index.filter(condition="treated", status="complete")
        """
        df = self.load()
        for col, val in kwargs.items():
            if col in df.columns:
                df = df[df[col] == val]
        return df.reset_index(drop=True)

    def reconcile(self, project_dir: str | Path | None = None) -> dict[str, list[str]]:
        """Find orphan .h5 files and stale CSV rows.

        Scans project_dir (defaults to CSV parent) for .h5 files and
        compares against the CSV.

        Returns dict with:
            'orphan_files': .h5 files on disk not in CSV
            'missing_files': CSV rows pointing to .h5 files that don't exist
        """
        if project_dir is None:
            project_dir = self.csv_path.parent
        project_dir = Path(project_dir)

        df = self.load()
        csv_paths = set(df["path"].tolist())

        # Find .h5 files on disk
        disk_files = {str(p) for p in project_dir.rglob("*.h5")}

        orphans = sorted(disk_files - csv_paths)
        missing = sorted(csv_paths - disk_files)

        return {"orphan_files": orphans, "missing_files": missing}

    def _write_atomic(self, df: pd.DataFrame) -> None:
        """Write DataFrame to CSV atomically via temp file + rename."""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".csv.tmp", dir=self.csv_path.parent
        )
        os.close(fd)
        try:
            df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, self.csv_path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
