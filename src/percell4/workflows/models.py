"""Configuration dataclasses for batch workflows.

All config objects are frozen after Start: the ``WorkflowConfig`` that drives
a run is immutable, and runtime state (``RunMetadata``) lives in a separate
mutable dataclass so the recipe and the instance can be serialized / tested
independently.

Every dataclass validates its invariants in ``__post_init__`` so a hand-edited
or stale ``run_config.json`` fails loudly at load time instead of silently
running with garbage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from percell4.measure.metrics import BUILTIN_METRICS
from percell4.workflows.failures import FailureRecord

# Matches single-line HDF5 paths AND pandas column suffixes. Length-capped so
# downstream CSV columns stay readable. Must start with a letter or underscore
# to avoid collisions with numeric-only names that pandas may coerce to ints.
_ROUND_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,39}$")


class ThresholdAlgorithm(StrEnum):
    """Per-round grouping algorithm."""

    GMM = "gmm"
    KMEANS = "kmeans"


class GmmCriterion(StrEnum):
    """Component-count selection criterion for GMM grouping."""

    BIC = "bic"
    SILHOUETTE = "silhouette"


class DatasetSource(StrEnum):
    """How the workflow should source a dataset's h5 file."""

    H5_EXISTING = "h5_existing"
    TIFF_PENDING = "tiff_pending"


@dataclass(frozen=True)
class CellposeSettings:
    """Global Cellpose configuration for a run.

    The workflow uses one model for every dataset; there are no per-dataset
    overrides. Edge-cell removal is always on for this workflow — it is a
    workflow invariant, not a config knob.
    """

    model: str = "cpsam"
    diameter: float = 30.0  # 0 = auto
    gpu: bool = True
    flow_threshold: float = 0.4
    cellprob_threshold: float = 0.0
    min_size: int = 15

    def __post_init__(self) -> None:
        if self.diameter < 0:
            raise ValueError("diameter must be >= 0 (0 = auto)")
        if self.min_size < 0:
            raise ValueError("min_size must be >= 0")


@dataclass(frozen=True)
class ThresholdingRound:
    """One named round of grouped thresholding.

    Rounds are ordered; the run executes them in list order. Each round's
    ``name`` becomes the HDF5 mask/group path component AND a pandas column
    suffix, so it is validated against a strict regex.
    """

    name: str
    channel: str
    metric: str
    algorithm: ThresholdAlgorithm
    gmm_criterion: GmmCriterion = GmmCriterion.BIC
    gmm_max_components: int = 4
    kmeans_n_clusters: int = 3
    gaussian_sigma: float = 1.0

    def __post_init__(self) -> None:
        if not _ROUND_NAME_RE.match(self.name):
            raise ValueError(
                "round name must match "
                f"{_ROUND_NAME_RE.pattern}, got {self.name!r}"
            )
        if not self.channel:
            raise ValueError("channel must be non-empty")
        if self.metric not in BUILTIN_METRICS:
            raise ValueError(
                f"metric must be one of {sorted(BUILTIN_METRICS)}, "
                f"got {self.metric!r}"
            )
        if self.gmm_max_components < 2:
            raise ValueError("gmm_max_components must be >= 2")
        if self.kmeans_n_clusters < 2:
            raise ValueError("kmeans_n_clusters must be >= 2")
        if self.gaussian_sigma < 0:
            raise ValueError("gaussian_sigma must be >= 0")


@dataclass
class WorkflowDatasetEntry:
    """One dataset selected for a workflow run.

    ``source`` distinguishes already-compressed ``.h5`` files from pending
    ``.tiff`` sources that will be compressed in Phase 0. For
    ``tiff_pending`` entries, ``h5_path`` is the *target* path (the file does
    not yet exist) and ``compress_plan`` carries whatever the dialog needs to
    drive ``import_dataset`` later.
    """

    name: str
    source: DatasetSource
    h5_path: Path
    channel_names: list[str] = field(default_factory=list)
    # TODO(phase2): promote to CompressPlan TypedDict / frozen dataclass
    compress_plan: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("dataset name must be non-empty")
        if self.source == DatasetSource.TIFF_PENDING and self.compress_plan is None:
            raise ValueError(
                "tiff_pending datasets require a compress_plan"
            )


@dataclass(frozen=True)
class WorkflowConfig:
    """The recipe. Immutable once the user clicks Start."""

    datasets: list[WorkflowDatasetEntry]
    cellpose: CellposeSettings
    thresholding_rounds: list[ThresholdingRound]
    selected_csv_columns: list[str]
    output_parent: Path

    def __post_init__(self) -> None:
        if not self.datasets:
            raise ValueError("at least one dataset is required")
        if not self.thresholding_rounds:
            raise ValueError("at least one thresholding round is required")
        names = [r.name for r in self.thresholding_rounds]
        if len(set(names)) != len(names):
            raise ValueError(f"thresholding round names must be unique: {names}")
        ds_names = [d.name for d in self.datasets]
        if len(set(ds_names)) != len(ds_names):
            raise ValueError(f"dataset names must be unique: {ds_names}")


@dataclass
class RunMetadata:
    """The runtime instance. Separate from WorkflowConfig (the recipe).

    Mutable: updated as the run progresses. Stamped with ``finished_at`` on
    any exit path (finish / cancel / exception). Failures accumulate on
    ``failures`` and are persisted to ``run_config.json`` alongside the
    recipe.
    """

    run_id: str
    run_folder: Path
    started_at: datetime
    finished_at: datetime | None = None
    intersected_channels: list[str] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)
