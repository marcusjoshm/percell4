"""Helpers shared by FLIM wavelet filter implementations.

Keep this module small: only functions that both `jcb` and `boe` filters
depend on. Anything algorithm-specific belongs in the relevant module.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


# ── Anscombe transform ────────────────────────────────────────────────

def anscombe_forward(data: NDArray) -> NDArray:
    """Anscombe transform `2·√(x + 3/8)` with the inner argument clamped
    at 0. Stabilises the variance of Poisson-distributed photon counts so
    they become approximately unit-variance Gaussian before wavelet
    filtering.
    """
    return 2.0 * np.sqrt(np.maximum(data + (3.0 / 8.0), 0.0))


def anscombe_inverse(y: NDArray) -> NDArray:
    """Mäkitalo-Foi closed-form unbiased inverse of the Anscombe transform.

    This is the correct inverse for low-count Poisson data (the FLIM
    phasor regime). The naïve asymptotic form `(y/2)² − 3/8` accumulates
    visible bias below ~10 photons/pixel; this closed-form is unbiased
    across the full range.

    Reference
    ---------
    Mäkitalo & Foi, "Optimal inversion of the Anscombe transformation in
    low-count Poisson image denoising," IEEE Trans. Image Process. 20(1),
    99–109 (2011). DOI 10.1109/TIP.2010.2056693.
    """
    y = np.asarray(y, dtype=np.float64)
    y = np.maximum(y, 1e-6)  # avoid divide-by-zero on 1/y terms
    inverse = (
        (y ** 2 / 4.0)
        - (1.0 / 8.0)
        + (np.sqrt(3.0 / 2.0) * (1.0 / y) / 4.0)
        - (11.0 / (8.0 * y ** 2))
        + (np.sqrt(5.0 / 2.0) * (1.0 / y ** 3) / 8.0)
        - (1.0 / (8.0 * y ** 4))
    )
    return np.maximum(inverse, 0.0)


# ── Padding helpers ───────────────────────────────────────────────────

def next_pow2(n: int) -> int:
    """Smallest power of 2 ≥ n. Used by the JCB filter to match the
    LeeLabBCM/ComplexWaveletFilter reference implementation byte-for-byte.
    """
    p = 1
    while p < n:
        p *= 2
    return p


def next_multiple(n: int, m: int) -> int:
    """Smallest multiple of m that is ≥ n. Used by the BOE filter — dtcwt
    only requires each dimension to be divisible by 2^n_levels, so
    padding to the next multiple saves memory and compute on non-pow2
    inputs vs. over-padding to the next power of 2.
    """
    return ((n + m - 1) // m) * m
