"""Single source of truth for phasor HDF5 paths and provenance attrs.

The phasor-related datasets in a percell4 ``.h5`` file live under
``phasor/{channel}/...``. This module enumerates those paths and the
attrs that must travel with them, so any reader or writer works from
the same canonical description instead of re-deriving it from reading
other use-case code.

Not a migration framework. No schema versioning. Just a shared
reference that makes future schema changes easy to diff.
"""

from __future__ import annotations

from typing import Any, Mapping


# ── Phasor path templates ─────────────────────────────────────────────

PHASOR_G: str = "phasor/{channel}/g"
PHASOR_S: str = "phasor/{channel}/s"
PHASOR_G_FILTERED: str = "phasor/{channel}/g_filtered"
PHASOR_S_FILTERED: str = "phasor/{channel}/s_filtered"
PHASOR_LIFETIME_FILTERED: str = "phasor/{channel}/lifetime_filtered"

FILTERED_DATASETS: tuple[str, ...] = (
    PHASOR_G_FILTERED,
    PHASOR_S_FILTERED,
    PHASOR_LIFETIME_FILTERED,
)


# ── Filter-status sentinel ────────────────────────────────────────────

FILTER_STATUS_ATTR: str = "filter_status"
FILTER_STATUS_IN_PROGRESS: str = "in_progress"
FILTER_STATUS_COMPLETE: str = "complete"


# ── Algorithm attr contract ───────────────────────────────────────────

ALGORITHM_ATTR: str = "algorithm"
LEGACY_ALGORITHM: str = "jcb_2025"
"""Default returned by :func:`read_wavelet_algorithm` when a filtered
dataset predates the per-algorithm-attr convention. Datasets written by
percell4 before this change used the JCB-style filter, so treating a
missing attr as ``"jcb_2025"`` preserves truth."""


def read_wavelet_algorithm(attrs: Mapping[str, Any]) -> str:
    """Return the algorithm attr, defaulting to :data:`LEGACY_ALGORITHM`
    for datasets written before the per-algorithm-attr convention.

    Centralises the fallback so every reader agrees. Pass in the
    ``attrs`` mapping from ``g_filtered`` (or any of the filtered
    datasets) — ``h5py`` exposes it as a dict-like.
    """
    return str(attrs.get(ALGORITHM_ATTR, LEGACY_ALGORITHM))


# ── Provenance attr builder ───────────────────────────────────────────

def wavelet_provenance_attrs(
    *,
    channel: str,
    algorithm: str,
    filter_level: int,
    biort: str,
    qshift: str,
    n_local_window: int,
    sigma_g_estimator: str,
    shrinkage: str,
    dtcwt_version: str,
    percell4_version: str,
) -> dict[str, Any]:
    """Build the full provenance-attr dict for a filtered phasor dataset.

    Keep this function the *only* place that constructs the dict, so
    adding a new attr is a one-line change that every writer picks up.
    """
    return {
        "dims": ["H", "W"],
        "channel": channel,
        "filter_level": filter_level,
        ALGORITHM_ATTR: algorithm,
        "biort": biort,
        "qshift": qshift,
        "n_local_window": n_local_window,
        "sigma_g_estimator": sigma_g_estimator,
        "shrinkage": shrinkage,
        "dtcwt_version": dtcwt_version,
        "percell4_version": percell4_version,
    }


# ── Per-algorithm parameter descriptions ──────────────────────────────

# Human-readable identifiers for the parameter attrs. Kept in one place
# so apply_wavelet (and any future filters) can look up what to record
# without re-deriving the strings.

ALGORITHM_PROVENANCE: dict[str, dict[str, Any]] = {
    "boe_2021": {
        "biort": "legall",
        "qshift": "qshift_a",
        "n_local_window": 3,                       # half-width; full 7×7
        "sigma_g_estimator": "mad_level1_pm45",
        "shrinkage": "bishrink_full",
    },
    "jcb_2025": {
        "biort": "legall",
        "qshift": "qshift_a",
        # JCB uses N = min(3, flevel) if flevel>10 else flevel
        # — resolve at write time when filter_level is known.
        "n_local_window": None,
        "sigma_g_estimator": "mean_medians_all",
        "shrinkage": "bishrink_jcb_simplified",
    },
}


def jcb_n_local_window(filter_level: int) -> int:
    """JCB's N-half-width derivation as a function of filter_level.

    Mirrors the rule in :func:`percell4.domain.flim.wavelet.jcb.
    calculate_local_noise_variance`. Exposed here so provenance
    recorders don't duplicate the logic.
    """
    return 3 if filter_level > 10 else filter_level


__all__ = [
    "PHASOR_G",
    "PHASOR_S",
    "PHASOR_G_FILTERED",
    "PHASOR_S_FILTERED",
    "PHASOR_LIFETIME_FILTERED",
    "FILTERED_DATASETS",
    "FILTER_STATUS_ATTR",
    "FILTER_STATUS_IN_PROGRESS",
    "FILTER_STATUS_COMPLETE",
    "ALGORITHM_ATTR",
    "LEGACY_ALGORITHM",
    "ALGORITHM_PROVENANCE",
    "read_wavelet_algorithm",
    "wavelet_provenance_attrs",
    "jcb_n_local_window",
]
