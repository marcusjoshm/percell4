"""Result dataclasses for the analysis batch runner.

3-state per-dataset taxonomy (``succeeded`` / ``failed`` / ``skipped``).
Distinct from :class:`percell4.workflows.models.BatchPhasorItemResult`
because analyses are atomic per dataset — :meth:`Analysis.run` either
returns the full output dict or raises. There is no per-channel
``partial`` here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class BatchAnalysisItemResult:
    """Outcome of running an analysis against a single dataset."""

    h5_path: Path
    status: Literal["succeeded", "failed", "skipped"]
    produced_outputs: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class BatchAnalysisReport:
    """Aggregate result of a batch analysis run."""

    items: tuple[BatchAnalysisItemResult, ...]
    run_folder: Path
    cancelled: bool = False

    @property
    def succeeded_count(self) -> int:
        return sum(1 for i in self.items if i.status == "succeeded")

    @property
    def failed_count(self) -> int:
        return sum(1 for i in self.items if i.status == "failed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for i in self.items if i.status == "skipped")
