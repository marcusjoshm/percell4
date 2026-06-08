"""Iterative Otsu *peeling* — capture the brightest layer, remove it, repeat.

A single Otsu split assumes balanced classes, so over a whole unit the threshold
is dragged toward whichever pixel population dominates and dim foci are missed.
This module peels one layer at a time per **unit** (an intensity group / a single
cell / the whole field): run Otsu, accept the foreground, dilate it by a guard
ring and NaN-stamp it out of a working buffer, then re-run Otsu on the reduced
buffer. Peeling continues per unit until a configurable **stopping criterion**
says "no real foreground left," at which point that unit latches done. When all
units are done (or a hard ``max_rounds`` cap is hit) the union of every iteration
is returned as one ``{0, 1}`` uint8 mask.

Pure domain (``numpy`` / ``scipy`` / ``skimage`` only — no Qt, napari, h5py, or
store). ``settings`` is duck-typed (a
:class:`~percell4.workflows.models.IterativeOtsuSettings`) so this layer never
imports the workflows layer at runtime, mirroring
:mod:`percell4.domain.measure.adaptive_clip`.

Key disciplines (see the plan and ``docs/solutions/``):

* The image is smoothed **once** by the caller; the loop only NaN-stamps. Finite
  filtering (``residual[isfinite]``) carries NaN-safety — the per-iteration Otsu
  never sees stamped pixels.
* A degenerate residual (empty / constant) means the unit is **done**, the
  opposite of the single-shot "constant group -> accept all" guard.
* The dilation guard ring is re-clamped to the unit mask **every iteration**, so
  a 5 px ring can never bleed into a neighbouring cell.
* The union is binarised with ``np.minimum(.., 1)`` before returning — the store
  does not normalise, and values > 1 render blank in napari.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from percell4.domain.measure.iterative_otsu_names import STOP_CRITERION_NAMES

if TYPE_CHECKING:  # type-only; no runtime import of the workflows layer
    from percell4.workflows.models import IterativeOtsuSettings

# Robust-sigma scale: MAD * this == standard deviation for a normal distribution.
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class IterativeOtsuReport:
    """Summary of one :func:`peel` call — the tuning signal for stop criteria.

    ``criterion_fires`` maps each active criterion name to the number of units it
    latched done. ``units_hit_max_rounds`` counts units still un-converged when
    the loop hit ``max_rounds`` (a high number means the criteria rarely fired).
    """

    n_iterations_run: int
    units_total: int
    units_hit_max_rounds: int
    criterion_fires: dict[str, int]
    n_positive: int


def otsu_with_separability(values: NDArray) -> tuple[float, float]:
    """Otsu threshold of a 1-D finite array plus its separability ``eta``.

    ``eta = sigma_between^2 / sigma_total^2`` in ``[0, 1]`` measures how cleanly
    the histogram splits into two classes: high for a real bimodal signal, low
    for unimodal noise. The threshold matches the rest of the codebase
    (``skimage.filters.threshold_otsu``); the foreground convention is
    ``value >= threshold`` (same as the per-group Otsu path).

    Caller guarantees ``values`` is finite with at least two distinct values.
    """
    from skimage.filters import threshold_otsu as sk_otsu

    thr = float(sk_otsu(values))
    total_var = float(values.var())
    if total_var <= 0.0:
        return thr, 0.0
    below = values[values < thr]
    above = values[values >= thr]
    n = values.size
    w0 = below.size / n
    w1 = above.size / n
    if w0 == 0.0 or w1 == 0.0:
        return thr, 0.0
    between = w0 * w1 * (float(above.mean()) - float(below.mean())) ** 2
    return thr, between / total_var


class RoundContext:
    """Per-unit, per-iteration scalars the stopping predicates read.

    Cheap fields are eager; the expensive ones (background/sigma from the
    below-threshold class, and the connected-component area) are
    :class:`cached_property` so a criterion that is switched off costs nothing.
    """

    def __init__(
        self,
        *,
        residual: NDArray,
        candidate_mask: NDArray,
        unit_mask: NDArray,
        thr: float,
        eta: float,
        cumulative_px: int,
        iteration: int,
    ) -> None:
        self.residual = residual  # 1-D finite values inside the unit this iter
        self.candidate_mask = candidate_mask  # 2-D bool would-be foreground
        self.unit_mask = unit_mask
        self.thr = thr
        self.eta = eta
        self.cumulative_px = cumulative_px  # px already accepted in this unit
        self.iteration = iteration

    @property
    def candidate_px(self) -> int:
        return int(self.candidate_mask.sum())

    @property
    def new_px(self) -> int:
        return self.candidate_px

    @property
    def residual_px(self) -> int:
        return int(self.residual.size)

    @property
    def candidate_frac_of_residual(self) -> float:
        return self.candidate_px / self.residual_px if self.residual_px else 1.0

    @property
    def residual_max(self) -> float:
        return float(self.residual.max()) if self.residual.size else 0.0

    @cached_property
    def _below(self) -> NDArray:
        b = self.residual[self.residual < self.thr]
        return b if b.size else self.residual

    @cached_property
    def bg(self) -> float:
        return float(np.median(self._below))

    @cached_property
    def sigma(self) -> float:
        med = float(np.median(self._below))
        mad = float(np.median(np.abs(self._below - med)))
        return _MAD_TO_SIGMA * mad

    @cached_property
    def max_component_area(self) -> int:
        from scipy.ndimage import label as ndlabel

        labelled, n = ndlabel(self.candidate_mask)
        if n == 0:
            return 0
        counts = np.bincount(labelled.ravel())
        return int(counts[1:].max()) if counts.size > 1 else 0


# Stopping-criterion registry. Each predicate answers "is this unit done?" from a
# RoundContext + its own (prefix-stripped) params. Keys MUST equal
# STOP_CRITERION_NAMES (asserted below — drift guard).
STOP_CRITERIA: dict[str, callable] = {
    # Otsu's split sits within noise: the remaining signal is <= bg + k*sigma.
    "bg-floor": lambda c, p: c.thr <= c.bg + float(p.get("k", 2.0)) * c.sigma,
    # The remaining histogram no longer splits into two real classes. Note a
    # unimodal-noise split still scores eta ~= 0.6-0.65 (Otsu halves the
    # Gaussian), so the default cutoff sits at 0.8 to fire on noise but not on a
    # genuine bimodal signal (eta ~= 0.95+).
    "separability": lambda c, p: c.eta < float(p.get("min_eta", 0.8)),
    # On pure noise Otsu marks ~half the residual — a high positive fraction is
    # the tell that it is thresholding nothing (the user's own observation).
    "positive-fraction-high": lambda c, p: c.candidate_frac_of_residual
    >= float(p.get("max_frac", 0.5)),
    # This iteration captured a negligible number of pixels.
    "min-positive": lambda c, p: c.candidate_px < int(p.get("min_px", 5)),
    # Diminishing returns: the new capture is a tiny fraction of the cumulative.
    "diminishing-returns": lambda c, p: c.cumulative_px > 0
    and c.new_px < float(p.get("min_gain_frac", 0.02)) * c.cumulative_px,
    # The brightest remaining pixel is no longer prominent above background.
    "peak-prominence": lambda c, p: (c.residual_max - c.bg)
    < float(p.get("k", 3.0)) * c.sigma,
    # No captured blob meets a minimum particle area (all sub-particle speckle).
    "min-area-components": lambda c, p: c.max_component_area < int(p.get("min_area_px", 3)),
}

assert set(STOP_CRITERIA) == set(STOP_CRITERION_NAMES), (
    "STOP_CRITERIA keys drifted from STOP_CRITERION_NAMES: "
    f"{set(STOP_CRITERIA) ^ set(STOP_CRITERION_NAMES)}"
)


def params_by_name(stop_params: tuple[tuple[str, object], ...]) -> dict[str, dict[str, object]]:
    """Group dotted-namespaced ``stop_params`` into ``{criterion: {param: value}}``.

    ``("bg-floor.k", 2.5)`` -> ``{"bg-floor": {"k": 2.5}}``. The dotted prefix is
    what keeps two criteria that both use a bare ``k`` from colliding in the flat
    settings bag. Entries without a dot are ignored.
    """
    out: dict[str, dict[str, object]] = {}
    for key, value in stop_params:
        if "." not in key:
            continue
        name, sub = key.split(".", 1)
        out.setdefault(name, {})[sub] = value
    return out


def _evaluate(
    ctx: RoundContext,
    active: tuple[str, ...],
    combine: str,
    params: dict[str, dict[str, object]],
) -> tuple[bool, list[str]]:
    """Run the active predicates; return ``(should_stop, fired_names)``.

    ``combine == "any"`` stops when any active criterion fires; ``"all"`` stops
    only when every active criterion fires. ``fired_names`` is always the list of
    criteria that fired (for the report), regardless of combine mode.
    """
    if not active:
        return False, []
    fired = [name for name in active if STOP_CRITERIA[name](ctx, params.get(name, {}))]
    if combine == "all":
        return len(fired) == len(active), fired
    return len(fired) > 0, fired


def peel(
    smoothed: NDArray,
    units: list[NDArray],
    settings: IterativeOtsuSettings,
) -> tuple[NDArray[np.uint8], IterativeOtsuReport]:
    """Iteratively Otsu-peel each unit; return ``(union_mask, report)``.

    Args:
        smoothed: The channel image, **already smoothed once** by the caller
            (float-coercible). Never re-smoothed inside the loop.
        units: One boolean mask per unit (intensity group / cell / whole field).
            The core is agnostic to how units were built.
        settings: Duck-typed ``IterativeOtsuSettings`` — reads ``max_rounds``,
            ``dilation_radius_px``, ``stop_criteria``, ``stop_combine``,
            ``stop_params``.

    Returns:
        ``(mask, report)`` where ``mask`` is ``{0, 1}`` uint8 with ``smoothed``'s
        2-D shape, and ``report`` summarises iterations / convergence / fires.
    """
    from skimage.morphology import dilation, disk

    working = np.asarray(smoothed, dtype=np.float32).copy()
    combined = np.zeros(working.shape, dtype=np.uint8)

    n_units = len(units)
    done = [False] * n_units
    cumulative_px = [0] * n_units

    max_rounds = int(settings.max_rounds)
    radius = int(settings.dilation_radius_px)
    active = tuple(settings.stop_criteria)
    combine = str(settings.stop_combine)
    params = params_by_name(tuple(settings.stop_params))
    footprint = disk(radius) if radius > 0 else None

    fires: dict[str, int] = {name: 0 for name in active}
    iterations_run = 0

    for iteration in range(1, max_rounds + 1):
        if all(done):
            break
        iterations_run = iteration
        for u, unit_mask in enumerate(units):
            if done[u]:
                continue
            um = np.asarray(unit_mask, dtype=bool)
            finite_in_unit = um & np.isfinite(working)
            residual = working[finite_in_unit]
            if residual.size == 0:
                done[u] = True
                continue
            if float(residual.min()) == float(residual.max()):
                # Degenerate residual (constant / single value) -> nothing left
                # to split. Mark done (NOT "accept all").
                done[u] = True
                continue

            thr, eta = otsu_with_separability(residual)
            # Build the candidate without comparing against NaN (avoids warnings):
            # residual is in finite_in_unit order, so scatter the comparison back.
            candidate = np.zeros(working.shape, dtype=bool)
            candidate[finite_in_unit] = residual >= thr

            ctx = RoundContext(
                residual=residual,
                candidate_mask=candidate,
                unit_mask=um,
                thr=thr,
                eta=eta,
                cumulative_px=cumulative_px[u],
                iteration=iteration,
            )
            stop, fired = _evaluate(ctx, active, combine, params)
            if stop:
                for name in fired:
                    fires[name] += 1
                done[u] = True
                continue

            # Accept this iteration's foreground.
            np.maximum(combined, candidate.astype(np.uint8), out=combined)
            cumulative_px[u] += int(candidate.sum())

            # Remove it (plus a guard ring) from the working buffer. Re-clamp the
            # ring to the unit every iteration so it cannot bleed across cells.
            if footprint is not None:
                ring = dilation(candidate, footprint=footprint) & um
            else:
                ring = candidate
            working[ring] = np.nan

    units_hit_cap = sum(1 for d in done if not d)
    np.minimum(combined, 1, out=combined)

    report = IterativeOtsuReport(
        n_iterations_run=iterations_run,
        units_total=n_units,
        units_hit_max_rounds=units_hit_cap,
        criterion_fires=fires,
        n_positive=int(combined.sum()),
    )
    return combined, report
