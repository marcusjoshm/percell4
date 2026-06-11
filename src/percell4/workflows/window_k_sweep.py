"""Qt-free window×k sweep harness for Adaptive Local Clipping (plan U2).

For one dataset this runs the production ``adaptive`` detector **inside the
Cellpose cell** at every point of a ``(window, k)`` grid and writes each result
back into the ``.h5`` as a descriptively-named ``/masks/<name>`` so the user can
flip through them in the napari viewer and judge by eye. There is **no oracle
and no automatic scoring** — the masks are the deliverable. The cheap per-mask
stats (particle count, in-cell positive px, fraction) are a *navigation aid*
only, not an accept/reject criterion.

Pass-1 seeds and the Gaussian smoothing are computed **once per dataset** and
reused across every grid point (both are window- and k-independent), so an
``N``-point grid re-runs only the background estimate, the signal gate, the
per-window ``threshold_local`` and the size filter — not the whole detection.

This module touches :mod:`percell4.store` at the load/write boundary but no Qt
or napari. The pure detection logic lives in :mod:`percell4.domain.measure`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from percell4.domain.measure.adaptive_clip import auto_window
from percell4.domain.measure.puncta_pipeline import (
    DEFAULT_SCALE_RANGE,
    compute_seeds,
    detect_two_pass,
)
from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.workflows.models import PunctaDetectorSettings
from percell4.workflows.phases import _channel_index

if TYPE_CHECKING:
    from percell4.store import DatasetStore

# Broad default grid (both axes CLI-overridable). Windows span the adaptive
# clamp range [11, 151]; k spans the usual contrast-margin band.
DEFAULT_WINDOWS: tuple[int, ...] = (15, 31, 51, 71, 101, 151)
DEFAULT_KS: tuple[float, ...] = (1.5, 2.0, 2.5, 3.0, 3.5)

# The auto window-finders whose picks are recorded per dataset (R4), so the
# manual sweep connects to the automation goal.
_AUTO_FINDERS: tuple[str, ...] = ("otsu-mean", "granule-size")

_NAME_RE = re.compile(r"^(?P<prefix>.+)_w(?P<w>\d+)_k(?P<k>\d+)$")


@dataclass(frozen=True)
class FixedSettings:
    """The detection settings held constant across the whole sweep (recorded).

    Only ``window`` and ``k`` vary across the grid; everything else is pinned
    here and echoed into the manifest so a sweep is reproducible and
    self-describing. ``noise_estimator`` defaults to ``"mad"`` — the GUI default
    and the settled σ estimate.
    """

    noise_estimator: str = "mad"
    seed_detector: str = "otsu"
    gaussian_sigma: float = 1.0
    min_spot_px: int = 3
    spot_scale_prior: tuple[float, float] = (1.0, 4.0)


@dataclass(frozen=True)
class SweepRow:
    """One grid point: its mask name, the two knobs, and navigation stats."""

    name: str
    window: int
    k: float
    particle_count: int
    in_cell_positive_px: int
    in_cell_fraction: float


@dataclass(frozen=True)
class AutoPick:
    """What an auto window-finder would pick, mapped to the nearest grid window."""

    method: str
    raw_window: int
    nearest_grid_window: int


@dataclass
class SweepReport:
    """The per-dataset result: rows, auto-finder picks, and the fixed context."""

    dataset: str
    shape: tuple[int, ...] | None
    pixel_size_um: float | None
    fixed: FixedSettings
    windows: tuple[int, ...]
    ks: tuple[float, ...]
    rows: list[SweepRow] = field(default_factory=list)
    auto_picks: list[AutoPick] = field(default_factory=list)
    cell_px: int = 0
    failure: str | None = None


# ── name helpers ──────────────────────────────────────────────────────────


def mask_name(prefix: str, window: int, k: float) -> str:
    """Sortable, round-trippable mask name, e.g. ``sweep_w051_k25`` for w=51,k=2.5.

    Window is zero-padded to 3 digits (lexical sort across the clamp range); k
    is encoded ×10 to avoid a ``.`` in the HDF5 group name.
    """
    return f"{prefix}_w{int(window):03d}_k{int(round(float(k) * 10)):02d}"


def parse_mask_name(name: str) -> tuple[int, float] | None:
    """Inverse of :func:`mask_name`: ``"sweep_w051_k25" -> (51, 2.5)``.

    Returns ``None`` for any name not matching the sweep scheme (so callers can
    tell sweep masks from the user's own masks).
    """
    m = _NAME_RE.match(name)
    if m is None:
        return None
    return int(m.group("w")), int(m.group("k")) / 10.0


# ── settings builders ─────────────────────────────────────────────────────


def base_settings(fixed: FixedSettings) -> PunctaDetectorSettings:
    """The ``adaptive`` settings with the fixed knobs; window/k are placeholders.

    Built exactly as the Adaptive Local Clipping panel does, so the sweep masks
    are what the production detector would produce.
    """
    return PunctaDetectorSettings(
        detector_name="adaptive",
        seed_detector_name=fixed.seed_detector,
        background_estimator_name=fixed.noise_estimator,
        detector_params={"window_px": 31, "k": 2.5},
        min_spot_px=max(1, int(fixed.min_spot_px)),
        spot_scale_prior=fixed.spot_scale_prior,
    )


def settings_with(fixed: FixedSettings, window_px: int, k: float) -> PunctaDetectorSettings:
    """A copy of the base settings with this grid point's ``window_px`` and ``k``."""
    return PunctaDetectorSettings(
        detector_name="adaptive",
        seed_detector_name=fixed.seed_detector,
        background_estimator_name=fixed.noise_estimator,
        detector_params={"window_px": int(window_px) | 1, "k": float(k)},
        min_spot_px=max(1, int(fixed.min_spot_px)),
        spot_scale_prior=fixed.spot_scale_prior,
    )


# ── the sweep ─────────────────────────────────────────────────────────────


def run_sweep(
    store: DatasetStore,
    channel: str,
    segmentation: str,
    windows,
    ks,
    fixed: FixedSettings,
    *,
    prefix: str = "sweep",
    clear: bool = False,
    dry_run: bool = False,
) -> SweepReport:
    """Sweep ``(window, k)`` for one dataset, writing a mask per grid point.

    Loads ``channel`` and the ``segmentation`` cell labels, smooths and computes
    pass-1 seeds once, then for every ``(window, k)`` runs the cell-restricted
    ``adaptive`` detector (group = ``labels > 0``) and writes the ``{0,1}`` uint8
    mask back into the ``.h5`` under :func:`mask_name` with provenance attrs.
    Any load/detection error is captured into ``SweepReport.failure`` (rows left
    empty) instead of raising, so a batch run isolates a bad dataset.

    ``clear`` deletes prior ``<prefix>_*`` masks first (the user's own masks are
    untouched); ``dry_run`` computes the report and the intended names without
    writing.
    """
    dataset = Path(str(store.path)).stem
    windows = tuple(int(w) | 1 for w in windows)  # the detector forces odd; mirror it
    ks = tuple(float(k) for k in ks)
    report = SweepReport(
        dataset=dataset,
        shape=None,
        pixel_size_um=None,
        fixed=fixed,
        windows=windows,
        ks=ks,
    )

    try:
        from skimage import measure

        meta = store.metadata
        report.pixel_size_um = meta.get("pixel_size_um")
        idx = _channel_index(store, channel)
        image = np.asarray(store.read_channel("intensity", idx), dtype=np.float32)
        group = np.asarray(store.read_labels(segmentation)) > 0
        report.shape = tuple(image.shape)
        cell_px = int(group.sum())
        report.cell_px = cell_px

        # Smooth + seed once for the whole grid (both window/k-independent).
        smoothed = apply_gaussian_smoothing(image, fixed.gaussian_sigma)
        base = base_settings(fixed)
        scale_range = base.spot_scale_prior or DEFAULT_SCALE_RANGE
        seeds = compute_seeds(smoothed, group, base, scale_range)

        if clear and not dry_run:
            for existing in list(store.list_masks()):
                if existing.startswith(f"{prefix}_"):
                    store.delete_item(f"masks/{existing}")

        for w in windows:
            for k in ks:
                settings = settings_with(fixed, w, k)
                # Inlined (not via detect_adaptive_in_group) to reuse the single
                # smoothing + the shared seeds across every grid point.
                mask = detect_two_pass(smoothed, group, settings, seeds=seeds)
                name = mask_name(prefix, w, k)
                if not dry_run:
                    store.write_mask(
                        name,
                        np.asarray(mask, dtype=np.uint8),
                        attrs={"window_px": int(w), "k": float(k), "sweep_prefix": prefix},
                    )
                pos = int(np.asarray(mask).sum())
                count = int(measure.label(np.asarray(mask) > 0).max())
                frac = (pos / cell_px) if cell_px > 0 else 0.0
                report.rows.append(SweepRow(name, int(w), float(k), count, pos, frac))

        # R4: record what each auto finder would pick (restricted to the cell).
        for method in _AUTO_FINDERS:
            raw = int(auto_window(image, fixed.gaussian_sigma, base, method=method, cp_mask=group))
            nearest = min(windows, key=lambda ww: (abs(ww - raw), ww))
            report.auto_picks.append(AutoPick(method, raw, int(nearest)))

    except Exception as exc:  # noqa: BLE001 — per-dataset isolation, recorded not raised
        report.rows = []
        report.auto_picks = []
        report.failure = f"{type(exc).__name__}: {exc}"

    return report


# ── manifest serialization ────────────────────────────────────────────────


def report_to_dict(report: SweepReport) -> dict:
    """JSON-friendly manifest for one dataset (the self-describing sidecar)."""
    return {
        "dataset": report.dataset,
        "shape": list(report.shape) if report.shape is not None else None,
        "pixel_size_um": report.pixel_size_um,
        "cell_px": report.cell_px,
        "windows": list(report.windows),
        "ks": list(report.ks),
        "fixed": {
            "noise_estimator": report.fixed.noise_estimator,
            "seed_detector": report.fixed.seed_detector,
            "gaussian_sigma": report.fixed.gaussian_sigma,
            "min_spot_px": report.fixed.min_spot_px,
            "spot_scale_prior": list(report.fixed.spot_scale_prior),
        },
        "auto_picks": [
            {
                "method": p.method,
                "raw_window": p.raw_window,
                "nearest_grid_window": p.nearest_grid_window,
            }
            for p in report.auto_picks
        ],
        "masks": [
            {
                "name": r.name,
                "window": r.window,
                "k": r.k,
                "particle_count": r.particle_count,
                "in_cell_positive_px": r.in_cell_positive_px,
                "in_cell_fraction": r.in_cell_fraction,
            }
            for r in report.rows
        ],
        "failure": report.failure,
        "note": (
            "particle_count / in_cell_positive_px / in_cell_fraction are a "
            "navigation aid only — inspect the masks visually to judge "
            "false positives vs missed granules."
        ),
    }
