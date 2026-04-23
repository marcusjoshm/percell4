"""FLIM complex-wavelet-filter family.

Two filter implementations coexist behind a single dispatch:

- ``"boe_2021"`` — strict replication of Wang et al. 2021
  (*Biomed. Opt. Express* 12(6):3463, DOI 10.1364/BOE.420953). LeGall 5/3
  + Q-shift, σ_g from level-1 ±45° bands only, full Sendur-Selesnick
  BiShrink.
- ``"jcb_2025"`` — byte-for-byte port of
  ``LeeLabBCM/ComplexWaveletFilter``, the code backing Fahim, Marcus et
  al., *JCB* 2025 (DOI 10.1083/jcb.202311105). Kept for reproducibility
  of the published paper.

Callers dispatch via the public :func:`denoise_phasor`; the GUI combo and
the use-case layer both read the same :data:`ALGORITHM_CHOICES` registry
so adding a third algorithm is a one-line change here.

Requires the optional ``dtcwt`` package: ``pip install percell4[flim]``.
"""

from __future__ import annotations

from typing import Callable, Literal, Protocol

from numpy.typing import NDArray

from percell4.domain.errors import MissingOptionalDependencyError


DEFAULT_FILTER_LEVEL: int = 9
N_LOCAL_WINDOW_BOE: int = 3
"""Half-width of the local-noise-variance window for BOE. Full window is
``2·N + 1`` → 7×7 by default. Sendur & Selesnick convention."""


AlgorithmId = Literal["boe_2021", "jcb_2025"]


class PhasorDenoiser(Protocol):
    """Static contract for the two FLIM wavelet filter implementations.

    All implementations return a dict with the same shape so callers
    can treat them interchangeably.
    """

    def __call__(
        self,
        g: NDArray,
        s: NDArray,
        intensity: NDArray,
        *,
        filter_level: int = DEFAULT_FILTER_LEVEL,
        omega: float | None = None,
    ) -> dict[str, NDArray]: ...


def _load_jcb() -> PhasorDenoiser:
    from percell4.domain.flim.wavelet.jcb import denoise_phasor_jcb
    return denoise_phasor_jcb


def _load_boe() -> PhasorDenoiser:
    from percell4.domain.flim.wavelet.boe import denoise_phasor_boe
    return denoise_phasor_boe


_FILTER_REGISTRY: dict[str, Callable[[], PhasorDenoiser]] = {
    "boe_2021": _load_boe,
    "jcb_2025": _load_jcb,
}


ALGORITHM_CHOICES: list[tuple[str, str, str]] = [
    # (id, short_label, tooltip_citation)
    (
        "boe_2021",
        "BOE",
        "Strict replication of Wang et al. 2021\n"
        "Biomed. Opt. Express 12(6):3463\n"
        "DOI 10.1364/BOE.420953",
    ),
    (
        "jcb_2025",
        "JCB",
        "Matches LeeLabBCM/ComplexWaveletFilter\n"
        "(Fahim & Marcus et al., J. Cell Biol. 2025)\n"
        "for reproducibility of the published paper",
    ),
]


def denoise_phasor(
    g: NDArray,
    s: NDArray,
    intensity: NDArray,
    *,
    algorithm: AlgorithmId = "boe_2021",
    filter_level: int = DEFAULT_FILTER_LEVEL,
    omega: float | None = None,
) -> dict[str, NDArray]:
    """Apply the selected FLIM wavelet filter to phasor G/S maps.

    Parameters
    ----------
    g, s : (H, W) float arrays
        Unfiltered phasor coordinate maps.
    intensity : (H, W) float array
        Per-pixel total photon counts.
    algorithm : {"boe_2021", "jcb_2025"}
        Which implementation to run. BOE is paper-strict; JCB reproduces
        the published JCB 2025 code.
    filter_level : int, optional
        DTCWT decomposition depth. Default 9.
    omega : float, optional
        Angular frequency in rad/ns. When provided, a lifetime map is
        computed and returned under key ``"T"``.

    Returns
    -------
    dict with keys ``G, S, T, GU, SU, TU, filter_level``. ``T``/``TU``
    are ``None`` when ``omega`` is not supplied.

    Raises
    ------
    ValueError
        If ``algorithm`` is not one of the registered values.
    MissingOptionalDependencyError
        If the ``dtcwt`` package is not installed.
    """
    try:
        loader = _FILTER_REGISTRY[algorithm]
    except KeyError:
        raise ValueError(
            f"Unknown wavelet algorithm: {algorithm!r}. "
            f"Expected one of {sorted(_FILTER_REGISTRY)}."
        ) from None

    try:
        fn = loader()
    except ImportError as exc:
        raise MissingOptionalDependencyError(
            "Wavelet filtering requires the optional 'flim' extra: "
            "pip install 'percell4[flim]'"
        ) from exc

    return fn(g, s, intensity, filter_level=filter_level, omega=omega)


__all__ = [
    "DEFAULT_FILTER_LEVEL",
    "N_LOCAL_WINDOW_BOE",
    "AlgorithmId",
    "ALGORITHM_CHOICES",
    "PhasorDenoiser",
    "denoise_phasor",
]
