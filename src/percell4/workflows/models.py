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

from percell4.domain.measure.iterative_otsu_names import (
    SCOPE_NAMES,
    STOP_CRITERION_NAMES,
)
from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.domain.measure.puncta_names import (
    BG_ESTIMATOR_NAMES,
    DETECTOR_NAMES,
)
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


# Cellpose model identifiers, in display order. Qt-free single source of
# truth shared by the GUI form (CellposeSettingsForm), the workflow dialog,
# and the headless batch CLI. The first entry is the default model.
CELLPOSE_MODELS = ("cpsam", "cyto3", "cyto2", "cyto", "nuclei")


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
    # ImageJ-style Enhance Contrast applied to the segmentation channel
    # before Cellpose runs. Same math as the seg-QC Modify Channel
    # group. 1.0% mirrors the QC default; 0.0 disables the pre-LUT.
    # Strictly a Cellpose-input preprocessor — the on-disk /intensity
    # is never modified.
    saturation_pct: float = 1.0
    # Gaussian blur sigma (standard deviation of the kernel) applied to
    # the segmentation channel before Cellpose runs, after the saturation
    # LUT. Smooths shot noise so speckled channels segment as single cell
    # bodies. 0.0 disables it. Like saturation_pct, strictly a
    # Cellpose-input preprocessor — the on-disk /intensity is never modified.
    blur_sigma: float = 0.0

    def __post_init__(self) -> None:
        if self.diameter < 0:
            raise ValueError("diameter must be >= 0 (0 = auto)")
        if self.min_size < 0:
            raise ValueError("min_size must be >= 0")
        if not (0.0 <= self.saturation_pct <= 50.0):
            raise ValueError(f"saturation_pct must be in [0, 50] (got {self.saturation_pct})")
        if self.blur_sigma < 0:
            raise ValueError(
                f"blur_sigma must be >= 0 (0 = no blur), got {self.blur_sigma}"
            )


def _normalize_params(params: Any) -> tuple[tuple[str, Any], ...]:
    """Canonicalize a params bag to a sorted tuple of ``(key, value)`` pairs.

    Accepts a dict or an iterable of pairs; values must be JSON scalars
    (``str``/``int``/``float``/``bool``/``None``). Sorting makes the frozen
    ``PunctaDetectorSettings`` hashable and its ``run_config.json`` round-trip
    order-independent. Convert back to a plain dict at the registry boundary
    with ``dict(...)``.
    """
    items = params.items() if isinstance(params, dict) else params
    out: list[tuple[str, Any]] = []
    for key, value in items:
        if not isinstance(key, str):
            raise ValueError(f"param key must be str, got {key!r}")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError(
                f"param value for {key!r} must be a JSON scalar, got {type(value).__name__}"
            )
        out.append((key, value))
    return tuple(sorted(out, key=lambda kv: kv[0]))


@dataclass(frozen=True)
class PunctaDetectorSettings:
    """Pluggable two-pass puncta-detection settings for a thresholding round.

    When a :class:`ThresholdingRound` carries this, the headless apply phase
    runs the two-pass spot detector
    (``percell4.domain.measure.puncta_pipeline.detect_two_pass``) instead of
    per-group Otsu. All names validate against the skimage-free tuples in
    ``percell4.domain.measure.puncta_names`` so constructing a round never
    imports scikit-image.

    ``detector_params`` / ``seed_params`` are stored as canonical sorted tuples
    of ``(key, value)`` pairs (JSON scalars only) so the dataclass stays frozen
    *and hashable* and round-trips through ``run_config.json`` byte-for-byte.
    Convert to a plain dict at the registry boundary with
    ``dict(settings.detector_params)``.
    """

    detector_name: str = "otsu"
    seed_detector_name: str = "log"
    background_estimator_name: str = "gaussian-peak"
    detector_params: tuple[tuple[str, Any], ...] = ()
    seed_params: tuple[tuple[str, Any], ...] = ()
    min_spot_px: int = 2
    max_spot_px: int | None = None
    spot_scale_prior: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if self.detector_name not in DETECTOR_NAMES:
            raise ValueError(
                f"detector_name must be one of {DETECTOR_NAMES}, got {self.detector_name!r}"
            )
        if self.seed_detector_name not in DETECTOR_NAMES:
            raise ValueError(
                f"seed_detector_name must be one of {DETECTOR_NAMES}, "
                f"got {self.seed_detector_name!r}"
            )
        if self.background_estimator_name not in BG_ESTIMATOR_NAMES:
            raise ValueError(
                "background_estimator_name must be one of "
                f"{BG_ESTIMATOR_NAMES}, got {self.background_estimator_name!r}"
            )
        if self.min_spot_px < 1:
            raise ValueError("min_spot_px must be >= 1")
        if self.max_spot_px is not None and self.max_spot_px < self.min_spot_px:
            raise ValueError("max_spot_px must be >= min_spot_px")
        # Normalize params to canonical sorted tuples (hashable + stable
        # round-trip). Accept a dict or an iterable of pairs at construction.
        object.__setattr__(self, "detector_params", _normalize_params(self.detector_params))
        object.__setattr__(self, "seed_params", _normalize_params(self.seed_params))
        # Coerce a JSON-list scale prior back to a float tuple so the frozen
        # __eq__ / __hash__ are stable across a run_config.json round-trip.
        if self.spot_scale_prior is not None:
            lo, hi = self.spot_scale_prior
            lo, hi = float(lo), float(hi)
            if not (0.0 < lo <= hi):
                raise ValueError(
                    "spot_scale_prior must be (lo, hi) with 0 < lo <= hi, "
                    f"got {self.spot_scale_prior!r}"
                )
            object.__setattr__(self, "spot_scale_prior", (lo, hi))


@dataclass(frozen=True)
class IterativeOtsuSettings:
    """Iterative-Otsu *peeling* settings for a thresholding round.

    When a :class:`ThresholdingRound` carries this, the headless apply phase runs
    iterative Otsu peeling (``percell4.domain.measure.iterative_otsu.peel``)
    instead of per-group Otsu. ``scope`` and stop-criterion names validate against
    the skimage-free tuples in ``percell4.domain.measure.iterative_otsu_names`` so
    constructing a round never imports scikit-image.

    ``stop_criteria`` names the active stopping signals; ``stop_combine`` is
    ``"any"`` (stop a unit when any fires) or ``"all"``. ``stop_params`` keys are
    **dotted-namespaced by criterion** (``"bg-floor.k"``,
    ``"positive-fraction-high.max_frac"``) — the prefix keeps two criteria that
    share a bare param name (e.g. ``k``) from colliding in the single flat bag.
    Like ``PunctaDetectorSettings.detector_params``, params canonicalize to a
    sorted tuple of ``(key, value)`` pairs (JSON scalars only) so the frozen
    dataclass stays hashable and round-trips through ``run_config.json``
    byte-for-byte. A hard ``max_rounds`` cap and the degenerate-residual guard
    always apply on top of the named criteria.

    ``fixed_iterations`` switches the loop to a *fixed-count* mode: when set, the
    peel runs exactly that many iterations per unit and the stop criteria are
    **blocked** (never evaluated) — ``max_rounds``/``stop_criteria``/
    ``stop_combine`` are ignored. The degenerate-residual guard still applies (a
    unit with nothing left to split latches done early). ``None`` (the default)
    keeps the criteria-driven mode. ``stop_criteria`` may be empty only in
    fixed-count mode.
    """

    scope: str = "per-cell"
    dilation_radius_px: int = 5
    max_rounds: int = 10
    stop_criteria: tuple[str, ...] = ("bg-floor", "positive-fraction-high")
    stop_params: tuple[tuple[str, Any], ...] = ()
    stop_combine: str = "any"
    fixed_iterations: int | None = None

    def __post_init__(self) -> None:
        if self.scope not in SCOPE_NAMES:
            raise ValueError(f"scope must be one of {SCOPE_NAMES}, got {self.scope!r}")
        if self.dilation_radius_px <= 0:
            raise ValueError("dilation_radius_px must be positive")
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if self.fixed_iterations is not None and self.fixed_iterations < 1:
            raise ValueError("fixed_iterations must be >= 1 when set")
        # Stop criteria are required only in criteria-driven mode; fixed-count
        # mode blocks them, so an empty tuple is valid there.
        if not self.stop_criteria and self.fixed_iterations is None:
            raise ValueError("stop_criteria must be non-empty")
        for name in self.stop_criteria:
            if name not in STOP_CRITERION_NAMES:
                raise ValueError(
                    f"stop_criteria entries must be one of {STOP_CRITERION_NAMES}, got {name!r}"
                )
        if self.stop_combine not in ("any", "all"):
            raise ValueError(f"stop_combine must be 'any' or 'all', got {self.stop_combine!r}")
        # Normalize params to canonical sorted tuples and validate the dotted
        # "<criterion>.<param>" key convention that params_by_name relies on.
        normalized = _normalize_params(self.stop_params)
        for key, _ in normalized:
            if "." not in key or key.split(".", 1)[0] not in STOP_CRITERION_NAMES:
                raise ValueError(
                    f"stop_params key {key!r} must be '<criterion>.<param>' with a known "
                    f"criterion (one of {STOP_CRITERION_NAMES})"
                )
        object.__setattr__(self, "stop_criteria", tuple(self.stop_criteria))
        object.__setattr__(self, "stop_params", normalized)


@dataclass(frozen=True)
class AdaptiveClipSettings:
    """Per-cell Adaptive Local Clipping settings for a thresholding round.

    When a :class:`ThresholdingRound` carries this, the headless apply phase runs
    the eye-validated per-cell detector
    (``percell4.domain.measure.adaptive_clip.detect_adaptive_by_particle_size``)
    instead of grouped Otsu / puncta / iterative-Otsu. The detector is driven by
    one physical knob, ``d_min_um`` (the smallest particle diameter, in µm, to
    detect): it derives the local-background window and size filter, while the
    noise floor is a robust per-cell ``1.4826*MAD`` so a fixed ``k`` transfers
    across cells/datasets whose intensity scale varies many-fold.

    ``presmooth_sigma_px`` is the detector's fixed-pixel presmooth (noise
    suppression); it defaults to ``1.0`` — the eye-validated value. It is stored
    here rather than borrowed from the round's ``gaussian_sigma`` because that
    field defaults to ``0`` (no smoothing) for the grouped-Otsu path, and an
    adaptive round must not silently inherit ``0`` (which collapses detection on
    noisy data). ``k`` defaults to 1 (the validated value); raise it to be more
    conservative.
    """

    d_min_um: float
    k: float = 1.0
    presmooth_sigma_px: float = 1.0

    def __post_init__(self) -> None:
        if self.d_min_um <= 0:
            raise ValueError(f"d_min_um must be > 0 µm, got {self.d_min_um}")
        if self.k < 0:
            raise ValueError(f"k must be >= 0, got {self.k}")
        if self.presmooth_sigma_px < 0:
            raise ValueError(f"presmooth_sigma_px must be >= 0, got {self.presmooth_sigma_px}")


@dataclass(frozen=True)
class AutoExtractSettings:
    """Per-cell two-pass Auto-extraction settings for a thresholding round.

    When a :class:`ThresholdingRound` carries this, the headless apply phase runs
    the two-pass total-feature extractor
    (``percell4.domain.measure.auto_extraction.auto_extract``) instead of grouped
    Otsu / puncta / iterative-Otsu / single-window adaptive clip. A fine pass
    (window ``≈ 3 × smallest particle``) catches small particles and a coarse pass
    (window ``≈ 3 × LoG-measured largest particle``) fills large ones; the two are
    OR-unioned into one combined mask.

    ``smallest_particle_um`` is the smallest particle diameter (µm) the user
    supplies to **override** auto-detection of the smallest particle. ``None``
    leaves it to be auto-detected from the image (no pixel size needed); when set,
    the apply phase converts it to pixels via the dataset ``pixel_size_um``.

    ``presmooth_sigma_px`` is the detector's fixed-pixel presmooth; it defaults to
    ``1.0`` (the eye-validated value) and is stored here rather than borrowed from
    the round's ``gaussian_sigma`` (which defaults to ``0`` for the grouped-Otsu
    path) so an auto-extract round never silently inherits ``0``. The remaining
    knobs (fill factor, FDR, min spot size, coarse-``k`` noise floor) are fixed,
    eye-validated module constants in ``auto_extraction`` and are not surfaced.
    """

    smallest_particle_um: float | None = None
    presmooth_sigma_px: float = 1.0

    def __post_init__(self) -> None:
        if self.smallest_particle_um is not None and self.smallest_particle_um <= 0:
            raise ValueError(
                "smallest_particle_um must be > 0 µm or None (auto-detect), got "
                f"{self.smallest_particle_um}"
            )
        if self.presmooth_sigma_px < 0:
            raise ValueError(f"presmooth_sigma_px must be >= 0, got {self.presmooth_sigma_px}")


@dataclass(frozen=True)
class CnrClassifySettings:
    """Opt-in guided CNR subpopulation classification for a thresholding round.

    A *post-step*, not a thresholding method: after the round's feature mask is
    produced, its foci are split by contrast-to-noise ratio at ``threshold``
    (guided mode of
    ``percell4.domain.measure.cnr_classification.classify_by_cnr``) into per-
    population masks (``<round>_low`` / ``<round>_high``) plus a per-focus CNR
    table at ``/classification/<round>``. Guided mode only — the user supplies the
    threshold; there is no discover / forced / interactive mode here.

    Because CNR is defined against the per-cell ``1.4826·MAD`` noise scale the
    Adaptive Local Clipping detector uses, it is only valid on a round that carries
    an ALC method (``adaptive_clip`` or ``auto_extract``); :class:`ThresholdingRound`
    rejects it otherwise. It is mutually *inclusive* with the method sentinels and
    is NOT part of their at-most-one exclusion.
    """

    threshold: float

    def __post_init__(self) -> None:
        if self.threshold <= 0:
            raise ValueError(f"CNR threshold must be > 0, got {self.threshold}")


@dataclass(frozen=True)
class ThresholdingRound:
    """One named round of grouped thresholding.

    Rounds are ordered; the run executes them in list order. Each round's
    ``name`` becomes the HDF5 mask/group path component AND a pandas column
    suffix, so it is validated against a strict regex.

    When ``puncta``, ``iterative_otsu``, ``adaptive_clip``, and ``auto_extract``
    are all ``None`` (the default), the apply phase uses the legacy per-group Otsu
    path unchanged. When it carries a :class:`PunctaDetectorSettings`, the headless
    two-pass spot detector runs instead; an :class:`IterativeOtsuSettings` runs
    iterative Otsu peeling; an :class:`AdaptiveClipSettings` runs the per-cell
    single-window adaptive clip detector; an :class:`AutoExtractSettings` runs the
    two-pass auto-extraction detector. These four method sentinels are mutually
    exclusive on a single round.

    ``cnr_classify`` is a separate, mutually-*inclusive* opt-in post-step (guided
    CNR subpopulation classification); it is valid only when the round carries an
    ALC method (``adaptive_clip`` or ``auto_extract``) and is NOT part of the
    method exclusion.
    """

    name: str
    channel: str
    metric: str
    algorithm: ThresholdAlgorithm
    gmm_criterion: GmmCriterion = GmmCriterion.BIC
    gmm_max_components: int = 4
    kmeans_n_clusters: int = 3
    gaussian_sigma: float = 1.0
    puncta: PunctaDetectorSettings | None = None
    iterative_otsu: IterativeOtsuSettings | None = None
    adaptive_clip: AdaptiveClipSettings | None = None
    auto_extract: AutoExtractSettings | None = None
    cnr_classify: CnrClassifySettings | None = None

    def __post_init__(self) -> None:
        if not _ROUND_NAME_RE.match(self.name):
            raise ValueError(f"round name must match {_ROUND_NAME_RE.pattern}, got {self.name!r}")
        if not self.channel:
            raise ValueError("channel must be non-empty")
        if self.metric not in BUILTIN_METRICS:
            raise ValueError(
                f"metric must be one of {sorted(BUILTIN_METRICS)}, got {self.metric!r}"
            )
        if self.gmm_max_components < 2:
            raise ValueError("gmm_max_components must be >= 2")
        if self.kmeans_n_clusters < 2:
            raise ValueError("kmeans_n_clusters must be >= 2")
        if self.gaussian_sigma < 0:
            raise ValueError("gaussian_sigma must be >= 0")
        method_sentinels = (
            self.puncta,
            self.iterative_otsu,
            self.adaptive_clip,
            self.auto_extract,
        )
        if sum(s is not None for s in method_sentinels) > 1:
            raise ValueError(
                "a round carries at most one of puncta / iterative_otsu / "
                "adaptive_clip / auto_extract"
            )
        # cnr_classify is an opt-in post-step, not a method — it is mutually
        # INCLUSIVE with the method sentinels and excluded from the at-most-one
        # check above. It splits the produced feature mask by per-cell-σ CNR, so
        # it is only meaningful on an Adaptive Local Clipping round.
        if (
            self.cnr_classify is not None
            and self.adaptive_clip is None
            and self.auto_extract is None
        ):
            raise ValueError(
                "cnr_classify requires an Adaptive Local Clipping method on the "
                "round (adaptive_clip or auto_extract)"
            )


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
            raise ValueError(f"min_area must be >= 0, got {self.min_area}")
        if self.min_area_unit not in ("px", "um2"):
            raise ValueError(f"min_area_unit must be 'px' or 'um2', got {self.min_area_unit!r}")


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
                f"dilute mask_name must match {_ROUND_NAME_RE.pattern}, got {self.mask_name!r}"
            )
        if not self.channel:
            raise ValueError("dilute channel must be non-empty")
        if self.metric not in BUILTIN_METRICS:
            raise ValueError(
                f"dilute metric must be one of {sorted(BUILTIN_METRICS)}, got {self.metric!r}"
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
            raise ValueError("tiff_pending datasets require a compress_plan")


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
    # Whether datasets that arrive ALREADY segmented (e.g. from the
    # percell4-batch CLI, or an explicit segmentation_overrides pick)
    # run the interactive segmentation-QC step on their selected
    # /labels layer before group thresholding. Defaults to True so a
    # batch-produced segmentation gets a review pass by default; set
    # False to use the existing labels as-is and go straight to
    # thresholding. Gates ONLY the pre-segmented path — datasets
    # segmented by Cellpose inside the workflow always run seg-QC under
    # the runner's interactive_qc switch, independent of this flag.
    run_seg_qc_on_existing: bool = True
    # Existing-mask reuse. When ``use_existing_masks`` is True the workflow
    # skips the Threshold Rounds step entirely (either/or per run) and
    # measures the masks already present in each dataset.
    # ``existing_mask_selections`` maps a dataset ``name`` to the list of
    # ``/masks/<name>`` layers chosen for that dataset. In this mode
    # ``thresholding_rounds`` may be empty; the runner synthesizes
    # measure-only round specs from the selections (see runner
    # ``_measure_round_specs_for``).
    use_existing_masks: bool = False
    existing_mask_selections: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.datasets:
            raise ValueError("at least one dataset is required")
        # The Threshold Rounds step is optional only when the run reuses
        # existing masks AND at least one dataset actually selected a mask.
        # A config with neither rounds nor mask selections still fails loud.
        has_mask_selection = self.use_existing_masks and any(
            self.existing_mask_selections.values()
        )
        if not self.thresholding_rounds and not has_mask_selection:
            raise ValueError(
                "at least one thresholding round is required "
                "(or set use_existing_masks with a non-empty mask selection)"
            )
        if self.existing_mask_selections:
            ds_name_set = {d.name for d in self.datasets}
            unknown = set(self.existing_mask_selections) - ds_name_set
            if unknown:
                raise ValueError(
                    f"existing_mask_selections references unknown dataset(s): {sorted(unknown)}"
                )
            if self.use_existing_masks:
                empty = [k for k, v in self.existing_mask_selections.items() if not v]
                if empty:
                    raise ValueError(
                        "existing_mask_selections has empty selection for "
                        f"dataset(s): {sorted(empty)}"
                    )
        names = [r.name for r in self.thresholding_rounds]
        if len(set(names)) != len(names):
            raise ValueError(f"thresholding round names must be unique: {names}")
        # Cross-round: a cnr_classify round mints /masks/<name>_low and
        # /masks/<name>_high population masks (the CNR post-step). Those reserved
        # names must not collide with another round's base name — round-name
        # uniqueness above is base-name-only and blind to the suffixed masks.
        name_set = set(names)
        for r in self.thresholding_rounds:
            if r.cnr_classify is None:
                continue
            for suffix in ("_low", "_high"):
                reserved = f"{r.name}{suffix}"
                if reserved in name_set:
                    raise ValueError(
                        f"round {r.name!r} with cnr_classify reserves population "
                        f"mask name {reserved!r}, which collides with another "
                        f"thresholding round name"
                    )
        ds_names = [d.name for d in self.datasets]
        if len(set(ds_names)) != len(ds_names):
            raise ValueError(f"dataset names must be unique: {ds_names}")
        if self.edge_margin_px < 0:
            raise ValueError(f"edge_margin_px must be >= 0, got {self.edge_margin_px}")
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


# ── FLIM-FRET workflow ───────────────────────────────────────


class FlimFretStatus(StrEnum):
    """Per-pair outcome for the FLIM-FRET analysis workflow.

    See ``percell4.application.use_cases.run_flim_fret`` for the orchestrator
    and ``docs/plans/2026-05-25-001-feat-flim-fret-analysis-workflow-plan.md``
    for the contract.
    """

    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    MISSING_LAYER = "missing_layer"
    DATASET_OPEN_FAILED = "dataset_open_failed"
    DONOR_REFERENCE_EMPTY = "donor_reference_empty"
    ERROR = "error"


@dataclass(frozen=True)
class FlimFretPair:
    """One donor / donor+acceptor pairing for the FLIM-FRET workflow.

    Layer names are stored as strings and resolved against the live ``.h5``
    each time the orchestrator runs — stale dropdown state is caught by the
    per-pair revalidation pass.

    Segmentation fields are ``None`` in whole-field mode. In single-cell mode
    both must be set; ``FlimFretConfig.__post_init__`` enforces this.
    """

    name: str
    donor_h5: Path
    da_h5: Path
    donor_mask: str
    donor_phasor: str
    donor_lifetime: str
    da_mask: str
    da_phasor: str
    da_lifetime: str
    donor_segmentation: str | None = None
    da_segmentation: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("FLIM-FRET pair name must be non-empty")
        # Layer name fields are required (strings); empty rejected so a
        # half-built FlimFretPair can't slip past dialog validation.
        for field_name in (
            "donor_mask",
            "donor_phasor",
            "donor_lifetime",
            "da_mask",
            "da_phasor",
            "da_lifetime",
        ):
            value = getattr(self, field_name)
            if not value:
                raise ValueError(f"FLIM-FRET pair {self.name!r}: {field_name} must be non-empty")


@dataclass(frozen=True)
class FlimFretConfig:
    """The FLIM-FRET workflow recipe. Frozen at Start.

    ``pairs`` is a list of (donor, donor+acceptor) dataset pairings.
    ``single_cell`` is a global toggle: when True every pair must carry both
    segmentation fields. ``output_parent`` is the user-chosen parent folder;
    the dialog creates a timestamped run folder beneath it via
    ``workflows.artifacts.create_run_folder``.
    """

    pairs: list[FlimFretPair]
    single_cell: bool
    output_parent: Path

    def __post_init__(self) -> None:
        if not self.pairs:
            raise ValueError("at least one FLIM-FRET pair is required")
        names = [p.name for p in self.pairs]
        if len(set(names)) != len(names):
            raise ValueError(f"FLIM-FRET pair names must be unique: {names}")
        for pair in self.pairs:
            try:
                same_path = pair.donor_h5.resolve() == pair.da_h5.resolve()
            except OSError:
                # resolve() can raise on missing files / loops; fall back
                # to string compare so the invariant still fires.
                same_path = str(pair.donor_h5) == str(pair.da_h5)
            if same_path:
                raise ValueError(
                    f"FLIM-FRET pair {pair.name!r}: donor and DA must be "
                    f"different .h5 files (both resolve to {pair.donor_h5})"
                )
            if self.single_cell:
                if not pair.donor_segmentation:
                    raise ValueError(
                        f"FLIM-FRET pair {pair.name!r}: donor_segmentation "
                        "is required when single_cell is True"
                    )
                if not pair.da_segmentation:
                    raise ValueError(
                        f"FLIM-FRET pair {pair.name!r}: da_segmentation "
                        "is required when single_cell is True"
                    )


@dataclass(frozen=True)
class FlimFretPairResult:
    """One pair's outcome from the FLIM-FRET orchestrator.

    ``rows`` are the dict payloads that the dialog assembles into the
    combined CSV. ``status`` is the canonical FlimFretStatus value.
    """

    pair: FlimFretPair
    status: FlimFretStatus
    reason: str | None
    rows: list[dict[str, Any]]
    n_pixels_donor: int
    n_cells_donor_reference: int
    n_da_cells_skipped: int


@dataclass(frozen=True)
class FlimFretReport:
    """Aggregated FLIM-FRET run outcome.

    ``run_folder`` is set by the dialog after it materializes the folder
    via ``create_run_folder``; the orchestrator itself leaves it ``None``.
    """

    results: list[FlimFretPairResult]
    run_folder: Path | None = None


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
