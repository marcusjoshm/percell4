"""Tests for the à-trous wavelet detector stub (plan U9, Phase 1).

The full hand-rolled B3-spline implementation is evidence-gated to Phase 3;
Phase 1 ships only a stub registered into the detector registry so the registry
and ``DETECTOR_NAMES`` stay complete. These tests pin the stub contract:
``NotImplementedError`` on call, registry membership, and the drift guard.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.measure.atrous import atrous_wavelet
from percell4.domain.measure.puncta_detectors import DETECTORS, DETECTOR_NAMES


def test_atrous_wavelet_raises_not_implemented():
    """Calling the stub raises NotImplementedError citing the deferred plan."""
    residual = np.zeros((40, 40), dtype=float)
    group_mask = np.ones((40, 40), dtype=bool)
    with pytest.raises(NotImplementedError) as exc:
        atrous_wavelet(residual, group_mask, None, {})
    assert "a-trous" in str(exc.value).lower()


def test_registry_entry_is_the_stub():
    """DETECTORS['atrous-wavelet'] is the stub function and raises when invoked."""
    assert DETECTORS["atrous-wavelet"] is atrous_wavelet
    residual = np.zeros((20, 20), dtype=float)
    group_mask = np.ones((20, 20), dtype=bool)
    with pytest.raises(NotImplementedError):
        DETECTORS["atrous-wavelet"](residual, group_mask, 1.0, {})


def test_registry_keys_match_names_including_atrous():
    """Registry keys == DETECTOR_NAMES and include the atrous-wavelet stub."""
    assert set(DETECTORS) == set(DETECTOR_NAMES)
    assert "atrous-wavelet" in DETECTOR_NAMES
    assert "atrous-wavelet" in DETECTORS
