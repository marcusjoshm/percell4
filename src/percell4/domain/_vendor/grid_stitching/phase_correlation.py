# Vendored into percell4 from the user's `grid_stitching` package.
# Algorithm: Preibisch, Saalfeld & Tomancak 2009 (Fiji Grid/Collection Stitching,
# Bioinformatics 25(11):1463-1465). Numpy-only computational core; copied verbatim.
"""
Phase-correlation based translation estimation.

This implements the core registration routine used by the Fiji
Grid/Collection Stitching plugin (Preibisch, Saalfeld & Tomancak, 2009,
Bioinformatics 25(11):1463-1465).

The key ideas reproduced here:

1.  Translation between two overlapping tiles is found via the
    *phase correlation matrix* (PCM): the normalized cross-power
    spectrum, inverse-transformed back to the spatial domain.  The
    location of the peak gives the shift.

2.  A single peak in the PCM is ambiguous: because the FFT is periodic
    and because the true sign of each axis shift is unknown, every peak
    corresponds to up to 2**ndim candidate integer shifts.  The plugin
    therefore extracts the *n* strongest peaks and, for each, evaluates
    all sign/wrap combinations by the real-space normalized
    cross-correlation over the implied overlap.  The candidate with the
    highest correlation (and sufficient overlapping area) wins.

This module works for both 2D and 3D (z-stacks), matching the plugin's
support for both.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np


@dataclass
class PeakResult:
    """Result of a phase-correlation registration between two tiles."""
    shift: tuple                # integer shift (per axis) to map img2 onto img1
    correlation: float          # normalized cross-correlation at that shift
    overlap_size: int           # number of overlapping pixels evaluated

    def __repr__(self) -> str:
        sh = ", ".join(f"{s:+d}" for s in self.shift)
        return (f"PeakResult(shift=({sh}), "
                f"R={self.correlation:.4f}, overlap={self.overlap_size})")


def _normalize(img: np.ndarray) -> np.ndarray:
    """Subtract mean so the DC component does not dominate the PCM."""
    img = img.astype(np.float64, copy=False)
    return img - img.mean()


def phase_correlation_matrix(img1: np.ndarray, img2: np.ndarray) -> np.ndarray:
    """
    Compute the phase correlation matrix (real-valued, same shape as inputs).

    PCM = IFFT( (F1 . conj(F2)) / |F1 . conj(F2)| )

    A small epsilon guards against division by zero in empty frequencies.
    """
    if img1.shape != img2.shape:
        raise ValueError(f"shape mismatch: {img1.shape} vs {img2.shape}")

    f1 = np.fft.fftn(_normalize(img1))
    f2 = np.fft.fftn(_normalize(img2))

    cross = f1 * np.conj(f2)
    mag = np.abs(cross)
    eps = np.finfo(np.float64).eps
    cross /= (mag + eps)

    pcm = np.fft.ifftn(cross).real
    return pcm


def _extract_peaks(pcm: np.ndarray, n_peaks: int) -> list:
    """Return indices of the n strongest peaks in the PCM (descending)."""
    flat = pcm.ravel()
    n_peaks = min(n_peaks, flat.size)
    # argpartition is O(N); we only need the top-n, then sort just those.
    idx = np.argpartition(flat, -n_peaks)[-n_peaks:]
    idx = idx[np.argsort(flat[idx])[::-1]]
    return [np.unravel_index(i, pcm.shape) for i in idx]


def _candidate_shifts(peak: tuple, shape: tuple) -> set:
    """
    Expand one PCM peak into all plausible integer shifts.

    For each axis the peak position p can mean a shift of either +p or
    -(N - p) (the periodic wrap-around).  All combinations across axes
    are returned, giving up to 2**ndim candidates.
    """
    per_axis = []
    for p, n in zip(peak, shape):
        options = {p, p - n}          # forward and wrapped (negative)
        per_axis.append(sorted(options))
    return set(itertools.product(*per_axis))


def _overlap_correlation(img1: np.ndarray, img2: np.ndarray,
                         shift: tuple, min_overlap: int):
    """
    Normalized cross-correlation (Pearson) over the region where img2,
    translated by `shift`, overlaps img1.

    Returns (correlation, overlap_pixel_count).  Correlation is set to
    -1 when the overlap is smaller than `min_overlap` or degenerate.
    """
    ndim = img1.ndim
    s1, s2 = [], []
    for ax in range(ndim):
        sh = int(shift[ax])
        n1 = img1.shape[ax]
        n2 = img2.shape[ax]
        # Region of img1 covered by the shifted img2.
        start1 = max(0, sh)
        end1 = min(n1, sh + n2)
        if end1 <= start1:
            return -1.0, 0
        s1.append(slice(start1, end1))
        s2.append(slice(start1 - sh, end1 - sh))

    a = img1[tuple(s1)].astype(np.float64).ravel()
    b = img2[tuple(s2)].astype(np.float64).ravel()
    n = a.size
    if n < min_overlap:
        return -1.0, n

    a -= a.mean()
    b -= b.mean()
    da = np.sqrt(np.dot(a, a))
    db = np.sqrt(np.dot(b, b))
    if da == 0 or db == 0:
        return 0.0, n
    return float(np.dot(a, b) / (da * db)), n


def register_pair(img1: np.ndarray, img2: np.ndarray,
                  n_peaks: int = 5,
                  min_overlap_ratio: float = 0.05,
                  expected_shift=None,
                  max_dev=None) -> PeakResult:
    """
    Estimate the translation that best maps img2 onto img1.

    Parameters
    ----------
    img1, img2 : ndarray
        Equal-shape tiles (2D or 3D).  img2 is shifted to align with img1.
    n_peaks : int
        Number of PCM peaks to test (the plugin's "number of peaks to
        check", default 5).  Each peak expands to 2**ndim sign candidates.
    min_overlap_ratio : float
        Minimum fraction of the smaller image that must overlap for a
        candidate shift to be accepted.
    expected_shift, max_dev :
        PerCell4 grid-prior band constraint (both or neither). ``expected_shift``
        is the image-axis (row, col[, plane]) shift the regular grid predicts for
        this pair; ``max_dev`` is the per-axis pixel tolerance (the overlap band).
        Candidate shifts deviating from ``expected_shift`` by more than ``max_dev``
        on any axis are discarded BEFORE scoring, so the best IN-BAND candidate
        wins and a spurious far peak can never be selected. Default ``None`` keeps
        the unconstrained Preibisch behaviour.

    Returns
    -------
    PeakResult
        Best shift, its correlation, and the evaluated overlap size.
    """
    pcm = phase_correlation_matrix(img1, img2)
    peaks = _extract_peaks(pcm, n_peaks)

    min_overlap = int(min_overlap_ratio * min(img1.size, img2.size))
    min_overlap = max(min_overlap, 1)

    constrained = expected_shift is not None and max_dev is not None
    if constrained:
        exp = [int(round(float(e))) for e in expected_shift]
        tol = int(max_dev)

    best = PeakResult(shift=(0,) * img1.ndim, correlation=-1.0, overlap_size=0)
    seen = set()
    for peak in peaks:
        for cand in _candidate_shifts(peak, img1.shape):
            if cand in seen:
                continue
            seen.add(cand)
            # Grid-prior band gate: only consider shifts within the overlap
            # band of the expected grid position (PerCell4 addition).
            if constrained and any(
                abs(int(cand[ax]) - exp[ax]) > tol for ax in range(img1.ndim)
            ):
                continue
            corr, n = _overlap_correlation(img1, img2, cand, min_overlap)
            if corr > best.correlation:
                best = PeakResult(shift=cand, correlation=corr, overlap_size=n)
    return best
