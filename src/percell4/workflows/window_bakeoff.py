"""Qt-free bake-off harness for the auto-window-size finders (plan U7).

Scores every window-finder against the SG-mask oracle so the winner is chosen
from evidence, not asserted. Mirrors :mod:`percell4.workflows.puncta_validation`:
this module may touch :class:`percell4.store.DatasetStore` and the pure scoring
core, but imports no Qt / napari. All HDF5 reads happen here at the store
boundary; the pure oracle (:func:`percell4.domain.measure.puncta_scoring.
sweep_ideal_window`) and the finders receive plain numpy arrays.

Contract honored:
* **k is pinned** for the whole run and recorded in the report — the oracle and
  every finder window are valid only at that ``k``.
* **Calibration vs validation** — ``holdout_field_names`` marks held-out fields;
  a score on a non-holdout field is flagged ``in_sample`` so the report never
  presents a single-labeled-image fit as "validated".
* **R9** — a dataset with no ``SG_mask`` is flagged in ``missing_label_fields``,
  never scored, never crashed on.
* **R10** — a finder that raises on a field is recorded as a failure row, not
  allowed to abort the run.
* Detection is reused, never reimplemented (``detect_adaptive_whole_frame`` /
  ``detect_two_pass`` via the oracle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from percell4.domain.measure.puncta_scoring import FinderScore, WindowOracle

if TYPE_CHECKING:
    from percell4.store import DatasetStore


@dataclass
class BakeoffField:
    """One labeled field for the bake-off: image + SG ground truth (+ cells)."""

    name: str
    image: NDArray
    sg_mask: NDArray | None  # binary; ``None`` when the dataset carries no SG label
    cp_mask: NDArray | None  # binary in-cell mask, or ``None``
    gaussian_sigma: float = 1.0


@dataclass(frozen=True)
class FinderFieldScore:
    """One finder scored on one field (or a recorded failure)."""

    field: str
    method: str
    score: FinderScore | None  # ``None`` when the finder raised
    error: str | None  # exception text when the finder raised, else ``None``


@dataclass
class BakeoffReport:
    """Outcome of :func:`run_bakeoff`."""

    k: float
    window_grid: tuple[int, ...]
    oracles: dict[str, WindowOracle] = field(default_factory=dict)  # field name -> oracle
    scores: list[FinderFieldScore] = field(default_factory=list)
    missing_label_fields: list[str] = field(default_factory=list)
    # (method, mean |err|) best first
    ranking: list[tuple[str, float]] = field(default_factory=list)


def _binarize_2d(arr: NDArray) -> NDArray:
    """Boolean 2-D view of a mask/label array (time-stacked masks are unioned)."""
    m = np.asarray(arr) > 0
    if m.ndim == 3:
        m = m.any(axis=0)
    return m


def load_bakeoff_field(
    store: DatasetStore,
    channel: str,
    *,
    sg_mask_name: str = "SG_mask",
    cp_name: str | None = None,
    gaussian_sigma: float = 1.0,
    field_name: str | None = None,
) -> BakeoffField:
    """Read one dataset into a :class:`BakeoffField` at the store boundary.

    ``SG_mask`` is read as a binary mask (``read_mask`` → ``{0,1}``; binarized
    defensively); the cell mask comes from ``read_labels(cp_name) > 0`` (instance
    labels). A dataset without ``sg_mask_name`` yields ``sg_mask=None`` (flagged
    by the harness, not an error). Time-lapse datasets are out of scope.
    """
    from percell4.workflows.phases import _channel_index

    channel_idx = _channel_index(store, channel)
    image = np.asarray(store.read_channel("intensity", channel_idx), dtype=np.float32)
    if image.ndim != 2:
        raise ValueError(
            f"window bake-off expects a 2-D channel; got shape {image.shape} "
            "(time-lapse datasets are out of scope)"
        )
    name = field_name or Path(str(store.path)).stem

    masks = store.list_masks() if hasattr(store, "list_masks") else []
    sg = _binarize_2d(store.read_mask(sg_mask_name)) if sg_mask_name in masks else None

    cp = None
    if cp_name:
        labels = store.list_labels() if hasattr(store, "list_labels") else []
        if cp_name in labels:
            cp = _binarize_2d(store.read_labels(cp_name))
    return BakeoffField(
        name=name, image=image, sg_mask=sg, cp_mask=cp, gaussian_sigma=gaussian_sigma
    )


def run_bakeoff(
    fields: list[BakeoffField],
    finders: list[str],
    settings,
    window_grid,
    *,
    finder_params: dict[str, dict] | None = None,
    holdout_field_names: list[str] | None = None,
) -> BakeoffReport:
    """Score each finder against the SG-mask oracle on each labeled field.

    ``settings`` is a duck-typed ``PunctaDetectorSettings`` whose ``k`` /
    background estimator are pinned for the whole run. For each labeled field the
    oracle's IoU-argmax window is the target; each finder's ``auto_window`` is
    scored by ``|auto - ideal|`` plus the IoU/recall of its own mask. Fields with
    no ``SG_mask`` are flagged (R9); finders that raise are recorded as failure
    rows (R10). The ranking is by mean window error (lower is better).
    """
    import dataclasses

    from percell4.domain.measure.adaptive_clip import auto_window, detect_adaptive_whole_frame
    from percell4.domain.measure.puncta_scoring import score_finder, sweep_ideal_window

    finder_params = finder_params or {}
    holdout = set(holdout_field_names or [])
    k = float(dict(settings.detector_params).get("k", 2.5))

    report = BakeoffReport(k=k, window_grid=tuple(int(w) | 1 for w in window_grid))
    per_method_errors: dict[str, list[int]] = {m: [] for m in finders}

    for fld in fields:
        if fld.sg_mask is None:
            report.missing_label_fields.append(fld.name)
            continue
        oracle = sweep_ideal_window(
            fld.image, fld.gaussian_sigma, settings, fld.sg_mask, window_grid
        )
        report.oracles[fld.name] = oracle
        in_sample = (not holdout) or (fld.name not in holdout)

        for method in finders:
            try:
                w = auto_window(
                    fld.image,
                    fld.gaussian_sigma,
                    settings,
                    method,
                    cp_mask=fld.cp_mask,
                    params=dict(finder_params.get(method, {})),
                )
                s = dataclasses.replace(
                    settings,
                    detector_params={**dict(settings.detector_params), "window_px": w},
                )
                mask = detect_adaptive_whole_frame(fld.image, fld.gaussian_sigma, s)
                fs = score_finder(method, w, oracle, mask, fld.sg_mask, k=k, in_sample=in_sample)
                report.scores.append(FinderFieldScore(fld.name, method, fs, None))
                per_method_errors[method].append(fs.window_error)
            except Exception as exc:  # noqa: BLE001 — one bad finder must not abort the run
                report.scores.append(
                    FinderFieldScore(fld.name, method, None, f"{type(exc).__name__}: {exc}")
                )

    report.ranking = sorted(
        ((m, float(np.mean(errs))) for m, errs in per_method_errors.items() if errs),
        key=lambda t: t[1],
    )
    return report


def calibrate_c(
    fields: list[BakeoffField],
    method: str,
    settings,
    window_grid,
    c_grid,
) -> tuple[float, float]:
    """Pick the multiplier ``c`` minimizing mean window error across labeled fields.

    Returns ``(best_c, mean_error)``. Calibration only — the caller is
    responsible for validating the chosen ``c`` on a held-out field before
    promoting it (with a single labeled image, that held-out field does not yet
    exist; the result is in-sample and must be reported as such).
    """
    best: tuple[float, float] | None = None
    for c in c_grid:
        rep = run_bakeoff(
            fields, [method], settings, window_grid, finder_params={method: {"c": float(c)}}
        )
        errs = [s.score.window_error for s in rep.scores if s.score is not None]
        if not errs:
            continue
        mean_err = float(np.mean(errs))
        if best is None or mean_err < best[1]:
            best = (float(c), mean_err)
    if best is None:
        raise ValueError(f"calibrate_c: no labeled field scored for finder {method!r}")
    return best
