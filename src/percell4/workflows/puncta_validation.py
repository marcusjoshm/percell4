"""Dev-time puncta-detection validation harness (plan U5).

Races candidate detection methods against hybrid ground truth and locks a
winner whose recipe (a frozen ``PunctaDetectorSettings``) can then run headless
without per-image QC. The matching/metrics math is the pure-domain module
:mod:`percell4.domain.measure.puncta_scoring`; this module orchestrates it.

Ground truth is two-tiered:

- **Tier A** — exhaustive napari-point CSVs of ``(y, x)`` centroids per field.
  This is the recall ceiling *and* the only source of precision / FP. A method
  that finds a focus the Tier-B mask lacks is therefore never penalized for it
  (it is scored against Tier A).
- **Tier B** — an existing approved ``/masks/<name>``, used only as a recall
  lower-bound (the floor) and an over-capture alarm. The Tier-B floor and the
  candidate recall are scored at the *same* tolerance.

The lock criterion (user): lock the grid point with the best ``f_beta`` that
also satisfies ``precision >= precision_floor`` AND (``tier_b_recall is None`` or
``recall >= tier_b_recall``) AND passes a pass-1-``k``-perturbation stability
probe. If none qualifies, no lock is emitted and ``keep_qc`` is ``True``.

Candidate masks are produced by **reusing the production detection path**
(``percell4.workflows.phases._apply_threshold_frame``) so the harness scores
exactly what a real headless run would produce — detection is never
reimplemented here.

Qt-free: this module may touch ``percell4.store`` and ``percell4.workflows`` but
imports no Qt / napari.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from percell4.domain.measure.puncta_scoring import (
    MatchCounts,
    accumulate,
    match_detections,
)
from percell4.workflows.models import (
    PunctaDetectorSettings,
    ThresholdAlgorithm,
    ThresholdingRound,
)
from percell4.workflows.phases import _apply_threshold_frame, threshold_compute_one

if TYPE_CHECKING:
    from percell4.domain.measure.grouper import GroupingResult
    from percell4.store import DatasetStore


@dataclass
class LabeledField:
    """One field used to score a candidate: image, cell labels, and grouping.

    A candidate mask for this field is produced by feeding
    ``(image, labels, grouping, round_spec)`` to
    :func:`percell4.workflows.phases._apply_threshold_frame`, where ``round_spec``
    carries the candidate :class:`PunctaDetectorSettings`. Detection is reused,
    never reimplemented.
    """

    name: str
    image: NDArray
    labels: NDArray
    grouping: GroupingResult


@dataclass(frozen=True)
class GridPoint:
    """One combination of detector / background / k / tol / scale-range to race."""

    detector_name: str = "log"
    background_estimator_name: str = "gaussian-peak"
    k: float = 2.5
    tol: float = 4.0
    scale_range: tuple[float, float] = (1.0, 4.0)
    min_spot_px: int = 2
    seed_detector_name: str = "log"
    extra_params: tuple[tuple[str, object], ...] = ()

    def settings(self, *, k_override: float | None = None) -> PunctaDetectorSettings:
        """Build the candidate :class:`PunctaDetectorSettings` for this grid point.

        ``k_override`` perturbs the pass-1 seed ``k`` for the stability probe;
        when ``None`` both passes use ``self.k``.
        """
        k_val = self.k if k_override is None else k_override
        detector_params: dict[str, object] = {"k": self.k}
        detector_params.update(dict(self.extra_params))
        return PunctaDetectorSettings(
            detector_name=self.detector_name,
            seed_detector_name=self.seed_detector_name,
            background_estimator_name=self.background_estimator_name,
            detector_params=detector_params,
            seed_params={"k": k_val},
            min_spot_px=self.min_spot_px,
            spot_scale_prior=self.scale_range,
        )


@dataclass
class ValidationReport:
    """Outcome of :func:`run_validation`.

    Attributes
    ----------
    ranked:
        One dict per grid point (best ``f_beta`` first): ``settings`` summary,
        ``recall``, ``precision``, ``f_beta``, ``stable``.
    locked_settings:
        The winning :class:`PunctaDetectorSettings`, or ``None`` if nothing
        cleared the bar.
    keep_qc:
        ``True`` when no grid point qualified (the condition keeps interactive
        QC).
    reason:
        Human-readable explanation of the lock / keep-QC decision.
    """

    ranked: list[dict] = field(default_factory=list)
    locked_settings: PunctaDetectorSettings | None = None
    keep_qc: bool = False
    reason: str = ""


def _round_for(settings: PunctaDetectorSettings, field: LabeledField) -> ThresholdingRound:
    """A minimal ThresholdingRound carrying the candidate settings.

    Channel / metric / algorithm are placeholders: ``_apply_threshold_frame``
    only uses ``round_spec.puncta`` (for detection) and ``gaussian_sigma`` /
    ``channel`` / ``metric`` (the last two only for the group-df column name,
    which the harness discards).
    """
    return ThresholdingRound(
        name="validate",
        channel="ch",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=1.0,
        puncta=settings,
    )


def _score_grid_point(
    settings: PunctaDetectorSettings,
    fields: list[LabeledField],
    gt_by_field: dict[str, NDArray],
    *,
    tol: float,
    close_radius: int,
) -> list[MatchCounts]:
    """Detect over every field and return the per-field :class:`MatchCounts`."""
    from skimage import measure, morphology

    per_field: list[MatchCounts] = []
    for fld in fields:
        round_spec = _round_for(settings, fld)
        mask, _gdf, err = _apply_threshold_frame(fld.image, fld.labels, fld.grouping, round_spec)
        if err or mask is None:
            mask = np.zeros(fld.labels.shape, dtype=np.uint8)
        # close + label (closing merges fragments of one granule within scale).
        binary = np.asarray(mask) > 0
        if close_radius > 0:
            binary = morphology.closing(binary, morphology.disk(close_radius))
        det_labels = measure.label(binary)
        gt = gt_by_field.get(fld.name, np.empty((0, 2)))
        per_field.append(match_detections(det_labels, gt, tol))
    return per_field


def run_validation(
    fields: list[LabeledField],
    gt_by_field: dict[str, NDArray],
    grid: list[GridPoint],
    *,
    tier_b_recall: float | None = None,
    beta: float = 2.0,
    close_radius: int = 1,
    precision_floor: float = 0.9,
    stability_delta: float = 0.5,
    stability_band: float = 0.1,
) -> ValidationReport:
    """Race ``grid`` over ``fields`` and lock a qualifying winner.

    Fields are processed sorted by name so micro-accumulation and tie-breaks are
    run-order independent. Each grid point is scored (micro-averaged) and probed
    for stability (pass-1 ``k`` perturbed by ``+/- stability_delta``; recall and
    precision must each move by ``<= stability_band``). The lock is the grid
    point with the best ``f_beta`` that also clears ``precision_floor``, the
    Tier-B recall floor (when given), and the stability probe.

    Returns a :class:`ValidationReport`.
    """
    fields = sorted(fields, key=lambda f: f.name)

    ranked: list[dict] = []
    for gp in grid:
        settings = gp.settings()
        counts = _score_grid_point(
            settings, fields, gt_by_field, tol=gp.tol, close_radius=close_radius
        )
        score = accumulate(counts)
        recall = score.recall
        precision = score.precision
        fbeta = score.f_beta(beta)

        # Stability probe: perturb the pass-1 seed k by +/- delta.
        stable = True
        for dk in (stability_delta, -stability_delta):
            k_pert = gp.k + dk
            if k_pert <= 0:
                continue
            pert_settings = gp.settings(k_override=k_pert)
            pert_counts = _score_grid_point(
                pert_settings,
                fields,
                gt_by_field,
                tol=gp.tol,
                close_radius=close_radius,
            )
            pert = accumulate(pert_counts)
            if (
                abs(recall - pert.recall) > stability_band
                or abs(precision - pert.precision) > stability_band
            ):
                stable = False
                break

        ranked.append(
            {
                "settings": settings,
                "detector_name": gp.detector_name,
                "background_estimator_name": gp.background_estimator_name,
                "k": gp.k,
                "tol": gp.tol,
                "scale_range": gp.scale_range,
                "threshold_rel": dict(settings.detector_params).get("threshold_rel"),
                "min_spot_px": gp.min_spot_px,
                "recall": recall,
                "precision": precision,
                "f_beta": fbeta,
                "stable": stable,
            }
        )

    # Rank by f_beta descending; deterministic tie-break on the settings repr.
    ranked.sort(key=lambda d: (-d["f_beta"], repr(d["settings"])))

    # Lock: best f_beta among the qualifying grid points.
    qualifying = [
        d
        for d in ranked
        if d["precision"] >= precision_floor
        and (tier_b_recall is None or d["recall"] >= tier_b_recall)
        and d["stable"]
    ]
    if qualifying:
        winner = qualifying[0]
        return ValidationReport(
            ranked=ranked,
            locked_settings=winner["settings"],
            keep_qc=False,
            reason=(
                f"locked {winner['detector_name']}/"
                f"{winner['background_estimator_name']} "
                f"(recall={winner['recall']:.3f}, "
                f"precision={winner['precision']:.3f}, "
                f"f_beta={winner['f_beta']:.3f})"
            ),
        )

    # Nothing qualified — explain why for the top candidate.
    if ranked:
        top = ranked[0]
        reasons = []
        if top["precision"] < precision_floor:
            reasons.append(f"precision {top['precision']:.3f} < floor {precision_floor}")
        if tier_b_recall is not None and top["recall"] < tier_b_recall:
            reasons.append(f"recall {top['recall']:.3f} < Tier-B floor {tier_b_recall:.3f}")
        if not top["stable"]:
            reasons.append("failed stability probe")
        why = "; ".join(reasons) or "no qualifying grid point"
    else:
        why = "empty grid"
    return ValidationReport(
        ranked=ranked,
        locked_settings=None,
        keep_qc=True,
        reason=f"keep interactive QC for this condition ({why})",
    )


# ── Loaders (thin, store-touching) ─────────────────────────────────────────


def load_tier_a_csv(paths: list[str | Path]) -> dict[str, NDArray]:
    """Read per-field Tier-A CSVs into ``{field_name: (M, 2) (y, x) array}``.

    Each CSV must have ``y`` and ``x`` columns. An optional ``field`` column
    groups points across files; otherwise every row in a file is assigned to
    that file's stem (so one CSV per field is the simplest layout).
    """
    by_field: dict[str, list[NDArray]] = {}
    for raw in paths:
        p = Path(raw)
        df = pd.read_csv(p)
        cols = {c.lower(): c for c in df.columns}
        if "y" not in cols or "x" not in cols:
            raise ValueError(f"{p}: CSV must have 'y' and 'x' columns, got {list(df.columns)}")
        ycol, xcol = cols["y"], cols["x"]
        if "field" in cols:
            fcol = cols["field"]
            for name, sub in df.groupby(fcol):
                pts = sub[[ycol, xcol]].to_numpy(dtype=np.float64)
                by_field.setdefault(str(name), []).append(pts)
        else:
            pts = df[[ycol, xcol]].to_numpy(dtype=np.float64)
            by_field.setdefault(p.stem, []).append(pts)

    out: dict[str, NDArray] = {}
    for name, chunks in by_field.items():
        out[name] = np.concatenate(chunks, axis=0) if chunks else np.empty((0, 2), dtype=np.float64)
    return out


def load_tier_b_recall(
    store: DatasetStore,
    mask_name: str,
    gt_by_field: dict[str, NDArray],
    tol: float,
    *,
    field_name: str | None = None,
    close_radius: int = 1,
) -> float:
    """Micro recall of an existing approved ``/masks/<mask_name>`` vs Tier A.

    This is the recall floor: a candidate must match or beat it. The mask is
    scored at the *same* ``tol`` as the candidates. ``field_name`` selects which
    Tier-A field the mask corresponds to (defaults to the single field when
    there is exactly one).
    """
    from skimage import measure, morphology

    mask = store.read_mask(mask_name)
    if field_name is None:
        if len(gt_by_field) != 1:
            raise ValueError("field_name is required when gt_by_field has != 1 field")
        field_name = next(iter(gt_by_field))
    gt = gt_by_field.get(field_name, np.empty((0, 2)))

    binary = np.asarray(mask) > 0
    if binary.ndim != 2:
        # Time-stacked mask: union the frames into a single field footprint.
        binary = binary.any(axis=0)
    if close_radius > 0:
        binary = morphology.closing(binary, morphology.disk(close_radius))
    det_labels = measure.label(binary)
    counts = match_detections(det_labels, gt, tol)
    score = accumulate([counts])
    return score.recall


def load_fields_from_store(
    store: DatasetStore,
    round_spec: ThresholdingRound,
    *,
    field_name: str | None = None,
    seg_name: str = "cellpose_qc",
) -> list[LabeledField]:
    """Build a single-timepoint :class:`LabeledField` from a dataset.

    Mirrors ``apply_threshold_headless``'s single-TP reads: compute the grouping
    via :func:`threshold_compute_one`, read the channel image + labels, and
    package them. Time-lapse datasets are out of scope for the harness loader.
    """
    from percell4.workflows.phases import _channel_index

    grouping, failure, msg = threshold_compute_one(store, round_spec, seg_name)
    if failure is not None or grouping is None:
        raise ValueError(f"grouping failed for {round_spec.name}: {msg}")
    if isinstance(grouping, dict):
        raise ValueError("load_fields_from_store does not support time-lapse datasets")

    channel_idx = _channel_index(store, round_spec.channel)
    image = store.read_channel("intensity", channel_idx)
    labels = store.read_labels(seg_name)
    name = field_name or Path(str(store.path)).stem
    return [LabeledField(name=name, image=image, labels=labels, grouping=grouping)]
