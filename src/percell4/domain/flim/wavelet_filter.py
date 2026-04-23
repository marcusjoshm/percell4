"""Backward-compatibility shim.

The FLIM wavelet filter lives in :mod:`percell4.domain.flim.wavelet` now
— two implementations (``boe_2021`` and ``jcb_2025``) behind a single
dispatch. This module is kept only so callers that still import
``denoise_phasor`` from the old path keep working.

To be removed once all call sites have migrated to
``from percell4.domain.flim.wavelet import denoise_phasor``.
"""

from percell4.domain.flim.wavelet.jcb import denoise_phasor_jcb as denoise_phasor

__all__ = ["denoise_phasor"]
