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

from percell4.domain.measure.metrics import BUILTIN_METRICS
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


class EdgeMode(StrEnum):
    """How the workflow handles cells touching the image border.

    Configured per-run; default ``EXCLUDE`` matches the historical
    workflow invariant (edge filtering always on).
    """

    # Filter edge-touching cells out of labels in Phase 1 (today's behavior).
    EXCLUDE = "exclude"
    # Keep edge cells in labels; treat them as ordinary cells in clustering,
    # thresholding, and per-cell measurement. Edge cells appear in the
    # parquet with ``is_edge=True``.
    INCLUDE_AS_NORMAL = "include_as_normal"
    # Keep edge cells in labels (participate in clustering/thresholding as
    # whole cells). At measurement time, also emit ONE synthetic row per
    # dataset whose metric values are ``sum(M across edge cells) /
    # N_theoretical``, where ``N_theoretical = sum(edge_areas) /
    # mean(whole_areas)``. See plan U4 for the formula and edge cases.
    INCLUDE_AS_SIZE_NORMALIZED_COHORT = "include_as_size_normalized_cohort"


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


@dataclass(frozen=True)
class ParticleSettings:
    """Optional particle analysis configuration.

    When present on ``WorkflowConfig``, the measure phase additionally
    runs :func:`percell4.domain.measure.particle.analyze_particles` for
    every thresholding round's mask. The per-cell summary columns
    (counts, total/mean/max area, coverage_fraction, per-channel
    intensity aggregates) are merged into the existing measurements
    DataFrame with a ``<round_name>_`` prefix. The per-particle detail
    rows (one row per detected particle, with parent ``cell_id``) are
    written to a separate ``particles.parquet`` / ``particles.csv``
    in the run folder.

    ``min_area`` filters out connected components below that area before
    counting / measuring. 0 keeps every component. The unit is
    interpreted per ``min_area_unit``:

    - ``"px"`` — area in pixels. Applied uniformly across datasets.
    - ``"um2"`` — area in µm². Converted to a per-dataset pixel
      threshold inside the workflow phase using that dataset's
      ``pixel_size_um``; datasets missing ``pixel_size_um`` fail their
      particle phase explicitly rather than silently using ``1`` µm/px.
    """

    min_area: float = 0.0
    min_area_unit: str = "px"

    def __post_init__(self) -> None:
        if self.min_area < 0:
            raise ValueError(
                f"min_area must be >= 0, got {self.min_area}"
            )
        if self.min_area_unit not in ("px", "um2"):
            raise ValueError(
                f"min_area_unit must be 'px' or 'um2', got {self.min_area_unit!r}"
            )


@dataclass(frozen=True)
class DiluteSettings:
    """Optional Phase 5 (dilute-phase mask) configuration.

    When present on ``WorkflowConfig``, Phase 5 runs as a per-dataset
    interactive queue that reuses the existing single-dataset dilute UI
    as the inner loop. Settings are locked at workflow Start.

    The algorithm encoding uses the canonical ``ThresholdAlgorithm`` and
    ``GmmCriterion`` StrEnums (matching ``ThresholdingRound``), not the
    GUI-snapshot ``GroupedThresholdConfig`` form, so ``run_config.json``
    has one consistent encoding across rounds and dilute settings.

    Per-algorithm parameters (``gmm_*``, ``kmeans_*``) are always present
    with sensible defaults — the unused parameter for the selected
    algorithm is ignored, matching the ``ThresholdingRound`` convention.

    The ``channel`` field is required: the standalone single-dataset
    dilute UI gets its channel from ``session.active_channel``, but batch
    mode has no session-bound channel — the field is the explicit source
    for the per-dataset queue handler.
    """

    mask_name: str
    dilation_radius_px: int
    channel: str
    metric: str
    algorithm: ThresholdAlgorithm
    gmm_criterion: GmmCriterion = GmmCriterion.BIC
    gmm_max_components: int = 4
    kmeans_n_clusters: int = 3
    gaussian_sigma: float = 1.0

    def __post_init__(self) -> None:
        if not _ROUND_NAME_RE.match(self.mask_name):
            raise ValueError(
                "dilute mask_name must match "
                f"{_ROUND_NAME_RE.pattern}, got {self.mask_name!r}"
            )
        if not self.channel:
            raise ValueError("dilute channel must be non-empty")
        if self.metric not in BUILTIN_METRICS:
            raise ValueError(
                f"dilute metric must be one of {sorted(BUILTIN_METRICS)}, "
                f"got {self.metric!r}"
            )
        if self.dilation_radius_px <= 0:
            raise ValueError("dilution_radius_px must be positive")
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
    # Which channel from /intensity to feed to Cellpose. Stored as a
    # name (not an index) so the runner resolves it per-dataset via
    # store.metadata["channel_names"].
    seg_channel_name: str = ""
    # How the workflow handles cells touching the image border. Defaults
    # to EXCLUDE to preserve the pre-evolution workflow invariant on
    # pre-existing run folders loaded via read_run_config.
    edge_mode: EdgeMode = EdgeMode.EXCLUDE
    # Pixel margin used by both Phase 1 edge filtering (when
    # edge_mode == EXCLUDE) and Phase 7's get_edge_labels recompute
    # (for INCLUDE_AS_SIZE_NORMALIZED_COHORT). 0 = strict border only.
    # Higher values pull cells closer to the edge into the "edge cell"
    # set.
    edge_margin_px: int = 0
    # Optional Phase 5 (dilute-phase mask) configuration. ``None`` means
    # the workflow skips Phase 5 entirely.
    dilute_settings: DiluteSettings | None = None
    # HDF5 path component for the Cellpose-produced segmentation that
    # downstream phases read. Defaults to "cellpose_qc" (the
    # pre-evolution hardcoded name); configurable so a researcher can
    # run multiple Cellpose parameterizations on the same h5 and keep
    # each segmentation's downstream measurements separate.
    cellpose_segmentation_name: str = "cellpose_qc"
    # Optional particle analysis. When set, measure_one adds per-cell
    # particle summary columns to the measurements parquet and writes
    # a separate particles.parquet/csv with per-particle detail.
    particle_settings: ParticleSettings | None = None

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
        if self.edge_margin_px < 0:
            raise ValueError(
                f"edge_margin_px must be >= 0, got {self.edge_margin_px}"
            )
        if not _ROUND_NAME_RE.match(self.cellpose_segmentation_name):
            raise ValueError(
                "cellpose_segmentation_name must match "
                f"{_ROUND_NAME_RE.pattern}, got "
                f"{self.cellpose_segmentation_name!r}"
            )
        # Cross-field: dilute mask name must not collide with any
        # thresholding round name (both compete for /masks/<name> in
        # each dataset's h5). Origin R14 / AE4.
        if self.dilute_settings is not None:
            dilute_name = self.dilute_settings.mask_name
            for r in self.thresholding_rounds:
                if r.name == dilute_name:
                    raise ValueError(
                        f"dilute mask_name {dilute_name!r} conflicts with "
                        f"thresholding round name {r.name!r}"
                    )


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
    # Per-dataset count of dilute-phase rounds completed. Populated by the
    # runner at each per-dataset workflow_done in Phase 5; consumed by U6's
    # summary_datasets.csv builder for the ``n_rounds_dilute`` column.
    # Empty when dilute is disabled or before any dataset finishes.
    per_dataset_dilute_round_counts: dict[str, int] = field(default_factory=dict)
