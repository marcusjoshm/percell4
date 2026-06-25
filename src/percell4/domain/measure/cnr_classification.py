"""CNR-based subpopulation classification for extracted foci.

Given an already-extracted feature mask (e.g. the OR-combined output of the
multi-scale Adaptive Local Clipping routine, :func:`percell4.domain.measure.
adaptive_clip.detect_adaptive_multiscale`), decide **whether** the foci fall into
distinct populations on the basis of their contrast-to-noise ratio (CNR), and
split them **only when the data support it**. Extraction is done elsewhere;
this module interprets.

What CNR is here
----------------
For each focus,

    CNR = (interior intensity - local background) / sigma_cell

where ``sigma_cell = 1.4826 * MAD`` of the **presmoothed** image within the
focus's host cell -- the *same* per-cell noise estimate the detector uses
(:func:`percell4.domain.measure.adaptive_clip.per_cell_sigma`). CNR is thus the
number of noise widths a focus rises above its local background, i.e. exactly the
axis the detection threshold ``k`` lives on. It is dimensionless and comparable
across cells and images, which is why population structure (if any) is read here.

How structure is decided (and why not just clustering)
------------------------------------------------------
A Gaussian-mixture / BIC fit will almost always prefer >=2 components for a
skewed unimodal distribution, manufacturing "populations" that are really just
distributional skew. Existence is therefore decided with a test for a genuine
*gap* -- Hartigan's dip test on ``log(CNR)`` (``diptest``; falls back to the
bimodality coefficient flagged ``reliable=False`` if the package is absent). The
mixture model is used only to place the boundary once a split is warranted.

Modes
-----
* **discover** (default): split only on a significant CNR gap. Conservative --
  returns a single population for a continuum and will not invent structure.
* **guided** (``threshold=<CNR value>``): split at a CNR threshold you provide.
  The right mode for two real but *overlapping* populations that have no gap.
* **forced** (``n_populations=2``): split into two at the data boundary
  regardless, flagged low-confidence when there is no gap.

The size-CNR residual is reported for inspection only -- it is NEVER used to
split automatically (a straight-line fit to a curved relationship fires
spuriously). A split is reported only if the smaller group exceeds
:data:`MIN_FRACTION` of foci (so a single outlier is never called a population).

Pure domain: numpy / scipy / skimage, plus ``diptest`` (required) and the
optional ``scikit-learn`` (boundary placement; quantile fallback) and ``pandas``
(only for :func:`to_dataframe`), all imported lazily. No Qt / h5py / store.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import pandas as pd
from scipy.ndimage import binary_dilation, center_of_mass, find_objects, label

from percell4.domain.measure.adaptive_clip import per_cell_sigma
from percell4.domain.measure.thresholding import apply_gaussian_smoothing

__all__ = [
    "classify_by_cnr",
    "classify_by_cnr_stack",
    "measure_cnr",
    "to_dataframe",
    "ClassificationResult",
    "StackClassificationResult",
    "assign_segments",
    "segment_label_image",
    "segment_masks_from_label_image",
]

# Fixed measurement geometry (reference, eye-validated). Not user-facing: the
# local-background annulus is the region between RING_INNER and RING_OUTER
# dilations of the focus (excluding other foci); INTERIOR_PCT is the percentile
# of the smoothed interior taken as the focus intensity (robust to edge/hot px).
RING_INNER = 3
RING_OUTER = 9
INTERIOR_PCT = 90.0

# Fixed classification guards (reference). Not user-facing.
ALPHA = 0.05            # dip-test significance level
MIN_COMPONENTS = 40     # below this, discover returns a single population
MIN_FRACTION = 0.02     # smaller group must hold >= this fraction, else no split


# --------------------------------------------------------------------------- #
# result container
# --------------------------------------------------------------------------- #
@dataclass
class ClassificationResult:
    """Outcome of :func:`classify_by_cnr`.

    Attributes
    ----------
    n_subpopulations : int
        1 if no split is supported, otherwise 2.
    labels_image : np.ndarray
        Int image, 0 = background, 1 = lower-CNR population, 2 = higher-CNR
        population. When ``n_subpopulations == 1`` every (valid) focus is 1.
    components : list[dict]
        One record per focus (see :func:`measure_cnr`) with an added
        ``subpopulation`` key (0 for foci dropped for a non-finite CNR).
    split_axis : str | None
        ``'cnr'`` or ``None`` if no split.
    threshold : float | None
        CNR boundary used (raw CNR units), or ``None``.
    report : dict
        Test statistics and the decision trail (dip p-values, slope, group
        sizes, the method used, and any warnings). Read this before trusting a
        split.
    """

    n_subpopulations: int
    labels_image: np.ndarray
    components: list[dict]
    split_axis: str | None
    threshold: float | None
    report: dict = field(default_factory=dict)


@dataclass
class StackClassificationResult:
    """Per-frame CNR classification of a ``(T,H,W)`` feature mask (time-lapse).

    Attributes
    ----------
    low_stack, high_stack : np.ndarray
        ``(T,H,W)`` ``{0,1}`` uint8 population masks with **exactly ``T`` frames** — an
        empty or single-population frame is an all-zero plane, never dropped — so each
        round-trips a store mask whose leading axis must equal ``n_timepoints``.
    table : pandas.DataFrame
        The per-focus CNR table for the whole stack: every ``classify_by_cnr`` record
        plus a ``timepoint`` column and a per-frame ``n_subpopulations`` column (so a
        ``_low`` time-course can flag frames that did not split — see the
        single-population note on :func:`classify_by_cnr_stack`). Empty when no frame
        has foci.
    per_frame : list[dict]
        One summary per frame (``timepoint``, ``n_subpopulations``, ``decision``,
        ``low_px``, ``high_px``) for the run-log.
    """

    low_stack: np.ndarray
    high_stack: np.ndarray
    table: pd.DataFrame
    per_frame: list[dict]


# --------------------------------------------------------------------------- #
# low-level statistics helpers
# --------------------------------------------------------------------------- #
def _bimodality_coefficient(x: np.ndarray) -> float:
    """Sarle's bimodality coefficient (heuristic; > ~0.555 hints at bimodality)."""
    from scipy.stats import kurtosis, skew

    n = len(x)
    if n < 4:
        return float("nan")
    g = skew(x, bias=False)
    kel = kurtosis(x, fisher=True, bias=False)
    denom = kel + 3.0 * ((n - 1) ** 2) / ((n - 2) * (n - 3))
    return float((g**2 + 1.0) / denom) if denom != 0 else float("nan")


def _gap_test(x: np.ndarray, alpha: float) -> dict:
    """Test a 1-D sample for a real gap between modes.

    Prefers Hartigan's dip test (``diptest``, a required dependency); falls back
    to the bimodality coefficient with a clear ``reliable=False`` flag if the
    package is missing or errors (defensive — diptest should be present).
    """
    x = np.asarray(x, dtype=float)
    try:
        import diptest

        stat, p = diptest.diptest(x)
        return {
            "method": "hartigan_dip",
            "statistic": float(stat),
            "pvalue": float(p),
            "bimodal": bool(p < alpha),
            "reliable": True,
        }
    except Exception:
        bc = _bimodality_coefficient(x)
        return {
            "method": "bimodality_coefficient",
            "statistic": bc,
            "pvalue": float("nan"),
            "bimodal": bool(np.isfinite(bc) and bc > 0.555),
            "reliable": False,
        }


def _boundary(values: np.ndarray) -> float:
    """Place a split boundary between the two modes of ``values``.

    Uses a 2-component Gaussian mixture (the crossover between the weighted
    components); falls back to the median if scikit-learn is unavailable.
    """
    try:
        from scipy.stats import norm
        from sklearn.mixture import GaussianMixture

        g = GaussianMixture(2, n_init=5, random_state=0).fit(values.reshape(-1, 1))
        order = np.argsort(g.means_.ravel())
        m = g.means_.ravel()[order]
        s = np.sqrt(g.covariances_.ravel())[order]
        w = g.weights_[order]
        grid = np.linspace(values.min(), values.max(), 4000)
        diff = w[1] * norm.pdf(grid, m[1], s[1]) - w[0] * norm.pdf(grid, m[0], s[0])
        between = (grid > m[0]) & (grid < m[1])
        if between.any():
            gg, dd = grid[between], diff[between]
            cross = np.nonzero(np.diff(np.sign(dd)) != 0)[0]
            if cross.size:
                return float(gg[cross[0]])
        return float((m[0] + m[1]) / 2.0)
    except Exception:
        return float(np.median(values))


# --------------------------------------------------------------------------- #
# per-focus measurement
# --------------------------------------------------------------------------- #
def measure_cnr(
    image: np.ndarray,
    feature_mask: np.ndarray,
    cell_labels: np.ndarray,
    *,
    presmooth_sigma_px: float = 1.0,
) -> list[dict]:
    """Measure size, local background, per-cell sigma and CNR for every focus.

    Parameters
    ----------
    image : 2D float array
        Raw single-channel image at native bit depth.
    feature_mask : 2D array
        Binary mask of all foci (e.g. the output of ``detect_adaptive_multiscale``).
    cell_labels : 2D int array
        Cell segmentation; supplies the per-cell sigma.
    presmooth_sigma_px : float, default 1.0
        Gaussian pre-smoothing, matched to the detector. Fixed in callers — σ
        (and therefore CNR) must be computed from the same buffer the detector
        thresholded.

    Returns
    -------
    list[dict]
        Per focus: ``label``, ``cell``, ``area``, ``diameter`` (equiv.), ``y``,
        ``x``, ``interior``, ``background``, ``contrast``, ``sigma``, ``cnr``.
        Foci with no resolvable host cell / sigma get ``cnr = nan`` and are
        dropped by the classifier.

    The local background is the median of an annulus around the focus (between
    :data:`RING_INNER` and :data:`RING_OUTER` dilations), excluding other foci;
    the interior is the :data:`INTERIOR_PCT`-th percentile of the smoothed focus.
    """
    img = np.asarray(image, dtype=np.float32)
    fmask = np.asarray(feature_mask) > 0
    cells = np.asarray(cell_labels)
    work = apply_gaussian_smoothing(img, presmooth_sigma_px)
    sigma = per_cell_sigma(work, cells)

    lab, _ = label(fmask)
    records: list[dict] = []
    for idx, sl in enumerate(find_objects(lab)):
        if sl is None:
            continue
        comp = lab[sl] == (idx + 1)
        area = int(comp.sum())
        if area == 0:
            continue

        ids = cells[sl][comp]
        ids = ids[ids > 0]
        if ids.size == 0:
            cid, s = 0, float("nan")
        else:
            cid = int(np.bincount(ids).argmax())
            s = sigma.get(cid, float("nan"))

        pad = tuple(
            slice(max(0, sp.start - RING_OUTER - 2), min(dim, sp.stop + RING_OUTER + 2))
            for sp, dim in zip(sl, img.shape)
        )
        subm = lab[pad] == (idx + 1)
        ring = (
            binary_dilation(subm, iterations=RING_OUTER)
            & ~binary_dilation(subm, iterations=RING_INNER)
            & ~(fmask[pad])
        )
        if ring.sum() >= 8:
            bg = float(np.median(work[pad][ring]))
        elif cid in sigma:
            bg = float(np.median(work[cells == cid]))
        else:
            bg = float("nan")

        interior = float(np.percentile(work[sl][comp], INTERIOR_PCT))
        contrast = interior - bg
        cnr = contrast / s if (np.isfinite(s) and s > 0.0) else float("nan")
        cy, cx = center_of_mass(comp)

        records.append(
            {
                "label": idx + 1,
                "cell": cid,
                "area": area,
                "diameter": float(2.0 * math.sqrt(area / math.pi)),
                "y": float(sl[0].start + cy),
                "x": float(sl[1].start + cx),
                "interior": interior,
                "background": bg,
                "contrast": float(contrast),
                "sigma": float(s),
                "cnr": float(cnr),
            }
        )
    return records


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #
def classify_by_cnr(
    image: np.ndarray,
    feature_mask: np.ndarray,
    cell_labels: np.ndarray,
    *,
    threshold: float | None = None,
    n_populations="auto",
    presmooth_sigma_px: float = 1.0,
) -> ClassificationResult:
    """Split extracted foci into CNR-defined populations.

    Three modes, selected by the arguments:

    * **discover** (default, ``n_populations='auto'`` and ``threshold=None``):
      split *only* if there is a statistically significant GAP in the CNR
      distribution (Hartigan dip on ``log(CNR)``). Conservative -- it will not
      invent populations and reports a single population for a continuum.
      Overlapping populations *without* a gap are not discoverable this way; use
      guided mode for those.
    * **guided** (``threshold=<CNR value>``): split at a CNR threshold you
      provide. The report includes the group sizes so you can sanity-check it.
    * **forced** (``n_populations=2``): split into two at the data-driven
      boundary regardless of gap significance, flagged low-confidence if no gap.

    Parameters
    ----------
    image, feature_mask, cell_labels
        Raw image, the all-foci mask, and the cell segmentation.
    threshold : float or None
        CNR value at which to split (guided mode), in raw CNR units.
    n_populations : 'auto' or int
        ``'auto'`` for gap-based discovery; ``2`` to force a two-way split.
    presmooth_sigma_px : float, default 1.0
        Forwarded to :func:`measure_cnr` (matched to the detector).

    Returns
    -------
    ClassificationResult
        Inspect ``.report`` before trusting any split. ``report['dip_cnr']`` is
        the decision basis; ``report['dip_size']`` and
        ``report['dip_size_cnr_residual']`` are INFORMATIONAL only. A
        ``report['candidate_cnr_threshold']`` is always provided as a starting
        point for guided mode.
    """
    records = measure_cnr(
        image, feature_mask, cell_labels, presmooth_sigma_px=presmooth_sigma_px
    )
    valid = [r for r in records if np.isfinite(r["cnr"]) and r["cnr"] > 0]

    report: dict[str, Any] = {
        "n_components_total": len(records),
        "n_components_valid": len(valid),
        "alpha": ALPHA,
        "decision": None,
        "warnings": [],
    }

    fmask = np.asarray(feature_mask) > 0
    lab, _ = label(fmask)

    def _single_population(reason: str) -> ClassificationResult:
        report["decision"] = f"single population ({reason})"
        for r in records:
            r["subpopulation"] = 1 if (np.isfinite(r["cnr"]) and r["cnr"] > 0) else 0
        return ClassificationResult(
            n_subpopulations=1,
            labels_image=(lab > 0).astype(np.int32),
            components=records,
            split_axis=None,
            threshold=None,
            report=report,
        )

    too_few = len(valid) < MIN_COMPONENTS
    if too_few and threshold is None:
        return _single_population(f"only {len(valid)} foci, need >= {MIN_COMPONENTS}")
    if len(valid) < 4:
        return _single_population("too few foci to measure structure")

    cnr = np.array([r["cnr"] for r in valid])
    diam = np.array([r["diameter"] for r in valid])
    logc = np.log10(cnr)
    logd = np.log10(diam)
    design = np.c_[logd, np.ones(len(logd))]
    coef, *_ = np.linalg.lstsq(design, logc, rcond=None)
    resid = logc - design @ coef

    dip_cnr = _gap_test(logc, ALPHA)
    report["dip_cnr"] = dip_cnr
    report["size_cnr_slope"] = float(coef[0])
    report["cnr_percentiles"] = {
        p: float(np.percentile(cnr, p)) for p in (10, 25, 50, 75, 90, 99)
    }
    report["candidate_cnr_threshold"] = float(10 ** _boundary(logc))
    # informational only -- not used to split:
    report["dip_size"] = _gap_test(logd, ALPHA)
    report["dip_size_cnr_residual"] = _gap_test(resid, ALPHA)
    report["_note_residual"] = (
        "dip_size_cnr_residual is informational; it can fire on fit curvature, "
        "so it is not used for automatic splitting."
    )
    if not dip_cnr["reliable"]:
        report["warnings"].append(
            "diptest unavailable: used the bimodality-coefficient heuristic, "
            "which is far less reliable. `pip install diptest` for a real gap test."
        )

    # ---- choose the split (mode resolution) ----
    if threshold is not None:
        mode = "guided (user threshold)"
        thr_log = float(np.log10(threshold))
    elif isinstance(n_populations, (int, np.integer)) and int(n_populations) >= 2:
        if int(n_populations) > 2:
            report["warnings"].append(
                "only 2-way splitting is implemented; using 2 populations."
            )
        mode = "forced (n_populations=2)"
        thr_log = _boundary(logc)
        if not dip_cnr["bimodal"]:
            report["warnings"].append(
                "forced split with no significant CNR gap -- low confidence; "
                "the boundary is arbitrary on a continuum."
            )
    else:  # auto / discover
        if dip_cnr["bimodal"]:
            mode = "discovered (significant CNR gap)"
            thr_log = _boundary(logc)
        else:
            return _single_population(
                "no significant CNR gap; for known populations pass "
                "threshold=<value> or n_populations=2 "
                f"(candidate threshold ~ {report['candidate_cnr_threshold']:.1f})"
            )

    high = logc >= thr_log
    frac = float(min(high.mean(), (~high).mean()))
    report["split_axis"] = "cnr"
    report["mode"] = mode
    report["threshold_cnr"] = float(10 ** thr_log)
    report["group_sizes"] = [int((~high).sum()), int(high.sum())]
    report["smaller_group_fraction"] = frac

    if frac < MIN_FRACTION:
        return _single_population(
            f"candidate split rejected: smaller group {frac:.1%} < "
            f"min_fraction={MIN_FRACTION:.1%}"
        )

    report["decision"] = f"2 populations [{mode}] at CNR={10 ** thr_log:.1f}"
    sub_of_label = {}
    for r, h in zip(valid, high):
        r["subpopulation"] = 2 if h else 1
        sub_of_label[r["label"]] = r["subpopulation"]
    for r in records:
        r.setdefault("subpopulation", 0)

    labels_img = np.zeros(lab.shape, dtype=np.int32)
    for lbl, sub in sub_of_label.items():
        labels_img[lab == lbl] = sub

    return ClassificationResult(
        n_subpopulations=2,
        labels_image=labels_img,
        components=records,
        split_axis="cnr",
        threshold=float(10 ** thr_log),
        report=report,
    )


# --------------------------------------------------------------------------- #
# convenience
# --------------------------------------------------------------------------- #
def to_dataframe(result: ClassificationResult):
    """Return the per-focus table as a pandas DataFrame (requires pandas)."""
    import pandas as pd

    return pd.DataFrame(result.components)


def classify_by_cnr_stack(
    image_thw: np.ndarray,
    feature_mask_thw: np.ndarray,
    labels_thw: np.ndarray,
    *,
    mode: str = "guided",
    threshold: float | None = None,
    presmooth_sigma_px: float = 1.0,
) -> StackClassificationResult:
    """Classify each timepoint of a ``(T,H,W)`` feature mask independently (time-lapse).

    The per-frame analog of :func:`classify_by_cnr`: every frame is classified on its
    OWN ``image[t]`` / ``feature_mask[t]`` / ``cell_labels[t]``, so the per-cell σ and
    the CNR are per-frame. ``mode`` mirrors :func:`classify_by_cnr`:

    * ``"guided"`` (default): split each frame at the **one shared** ``threshold`` — the
      same CNR cut applied to every timepoint (the time-lapse spec'd path).
    * ``"forced"``: a 2-way split each frame.
    * ``"discover"``: gap-based discovery each frame.

    Per-frame **single-population semantics**: a frame that does not split — ``< 4`` foci,
    or (after a split) the smaller group below :data:`MIN_FRACTION` — has every focus
    labelled ``1``, so its foci land in ``low_stack[t]`` and ``high_stack[t]`` is empty,
    exactly as the single-frame path does. ``low`` on such a frame therefore means
    "unclassified for that frame", **not** "dim". The per-frame ``n_subpopulations`` (on
    the returned table and ``per_frame`` summary) flags these so a ``_low`` time-course is
    not silently contaminated by an unclassified frame.

    Caveat: because ``σ_cell = 1.4826·MAD`` is measured per frame, one shared ``threshold``
    is a fixed CNR *number* but not a fixed statistical *stringency* across frames whose
    texture/brightness drifts (the convention doc's caveat). Expected, not a bug.

    Raises :class:`ValueError` on a shape mismatch (all three must be ``(T,H,W)`` with the
    same shape) or a guided call with no ``threshold``. Pure (numpy + pandas via
    :func:`to_dataframe`); does no I/O.
    """
    import pandas as pd

    image = np.asarray(image_thw)
    fmask = np.asarray(feature_mask_thw)
    labels = np.asarray(labels_thw)
    if image.ndim != 3:
        raise ValueError(
            f"classify_by_cnr_stack expects a (T,H,W) image; got shape {image.shape}"
        )
    if not (image.shape == fmask.shape == labels.shape):
        raise ValueError(
            "image, feature_mask and cell_labels must share shape (T,H,W); got "
            f"{image.shape}, {fmask.shape}, {labels.shape}"
        )
    if mode == "guided" and threshold is None:
        raise ValueError("guided mode requires a threshold")

    n_t = int(image.shape[0])
    low_frames: list[np.ndarray] = []
    high_frames: list[np.ndarray] = []
    tables: list = []
    per_frame: list[dict] = []
    for t in range(n_t):
        if mode == "guided":
            res = classify_by_cnr(
                image[t], fmask[t], labels[t],
                threshold=float(threshold), presmooth_sigma_px=presmooth_sigma_px,
            )
        elif mode == "forced":
            res = classify_by_cnr(
                image[t], fmask[t], labels[t],
                n_populations=2, presmooth_sigma_px=presmooth_sigma_px,
            )
        else:  # discover
            res = classify_by_cnr(
                image[t], fmask[t], labels[t], presmooth_sigma_px=presmooth_sigma_px
            )
        lab_img = np.asarray(res.labels_image)
        low = (lab_img == 1).astype(np.uint8)
        high = (lab_img == 2).astype(np.uint8)
        low_frames.append(low)
        high_frames.append(high)
        df_t = to_dataframe(res)
        df_t["timepoint"] = t
        df_t["n_subpopulations"] = res.n_subpopulations
        tables.append(df_t)
        per_frame.append(
            {
                "timepoint": t,
                "n_subpopulations": int(res.n_subpopulations),
                "decision": res.report.get("decision"),
                "low_px": int(low.sum()),
                "high_px": int(high.sum()),
            }
        )

    # Always exactly T frames — empty/single-population frames are all-zero planes, so the
    # stacks round-trip a store mask whose leading axis must equal n_timepoints.
    low_stack = np.stack(low_frames, axis=0).astype(np.uint8)
    high_stack = np.stack(high_frames, axis=0).astype(np.uint8)
    table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    return StackClassificationResult(
        low_stack=low_stack, high_stack=high_stack, table=table, per_frame=per_frame
    )


# --------------------------------------------------------------------------- #
# manual divider-based segmentation (the interactive segmenter)
# --------------------------------------------------------------------------- #
def assign_segments(cnr: np.ndarray, dividers: Sequence[float]) -> np.ndarray:
    """Map CNR values to 1-based segment ids by sorted ``dividers``.

    ``N`` dividers partition the CNR axis into ``N+1`` segments: a value ≤ the
    first divider is segment 1, between the first and second is segment 2, …, and
    above the last divider is segment ``N+1`` (``np.digitize`` with the sorted
    dividers, +1). Empty ``dividers`` → all values in segment 1. Dividers need not
    be sorted on input.
    """
    d = np.sort(np.asarray(list(dividers), dtype=float))
    return (np.digitize(np.asarray(cnr, dtype=float), d) + 1).astype(np.int32)


def segment_label_image(
    component_labels: np.ndarray,
    focus_labels: np.ndarray,
    focus_cnr: np.ndarray,
    dividers: Sequence[float],
) -> np.ndarray:
    """Build a 0/1..N segment image from a component-label image + per-focus CNR.

    ``component_labels`` is the labelled feature mask (0 = background, each focus a
    unique positive id, as produced by ``scipy.ndimage.label``). ``focus_labels``
    /``focus_cnr`` are aligned arrays giving each measured focus's component id and
    CNR. Each focus is assigned a segment via :func:`assign_segments`, scattered
    into a per-component lookup, and indexed back over the image — one fancy-index,
    fast enough for live updates. Background and any component without a measured
    focus stay 0.
    """
    comp = np.asarray(component_labels)
    out_n = int(comp.max())
    seg_of = np.zeros(out_n + 1, dtype=np.int32)
    fl = np.asarray(focus_labels, dtype=np.int64)
    if fl.size:
        seg = assign_segments(focus_cnr, dividers)
        # Guard against any stray focus id outside the component range.
        valid = (fl >= 1) & (fl <= out_n)
        seg_of[fl[valid]] = seg[valid]
    return seg_of[comp].astype(np.int32)


def segment_masks_from_label_image(
    seg_img: np.ndarray, n_segments: int
) -> list[np.ndarray]:
    """Split a 0/1..N segment image into a list of ``{0,1}`` uint8 masks (seg 1..N)."""
    img = np.asarray(seg_img)
    return [(img == i).astype(np.uint8) for i in range(1, int(n_segments) + 1)]
