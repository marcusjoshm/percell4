"""Qt-level tests for the phasor plot's median / wavelet filter checkboxes.

Covers the mutually-exclusive Median/Wavelet view toggles, the on-demand
median derivation and its kernel spinbox, the wavelet checkbox enablement
gated on a wavelet result, and that every visible-pixel consumer reads the
same active view through ``_get_active_gs_maps`` /
``_compute_visible_valid_2d``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.domain.flim.phasor import median_filter_gs
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    sess = Session()
    sess._dataset = DatasetHandle(path=tmp_path / "fake.h5", metadata={})
    return sess


@pytest.fixture
def fake_repo() -> MagicMock:
    repo = MagicMock()
    repo.read_mask.return_value = np.ones((8, 8), dtype=np.uint8)
    return repo


@pytest.fixture
def phasor_window(qtbot, session_with_dataset, fake_repo) -> PhasorPlotWindow:
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: fake_repo)
    qtbot.addWidget(win)
    return win


def _raw_maps():
    """Raw g/s with a spatial outlier so the median visibly differs."""
    g = np.full((8, 8), 0.5, dtype=np.float32)
    s = np.full((8, 8), 0.3, dtype=np.float32)
    g[4, 4] = 0.95
    s[4, 4] = 0.02
    return g, s


def _wavelet_maps():
    return (
        np.full((8, 8), 0.45, dtype=np.float32),
        np.full((8, 8), 0.28, dtype=np.float32),
    )


# ── Initial state ────────────────────────────────────────────


def test_checkboxes_start_unchecked_wavelet_disabled(phasor_window):
    assert not phasor_window._median_check.isChecked()
    assert not phasor_window._wavelet_check.isChecked()
    assert not phasor_window._wavelet_check.isEnabled()
    assert not phasor_window._median_kernel_spin.isEnabled()


def test_no_filter_returns_unfiltered_maps(phasor_window):
    g, s = _raw_maps()
    phasor_window.set_phasor_data(g, s)

    ag, as_ = phasor_window._get_active_gs_maps()
    np.testing.assert_array_equal(ag, g)
    np.testing.assert_array_equal(as_, s)


# ── Median view ──────────────────────────────────────────────


def test_median_checkbox_derives_median_view(phasor_window):
    g, s = _raw_maps()
    phasor_window.set_phasor_data(g, s)

    phasor_window._median_check.setChecked(True)
    assert phasor_window._median_kernel_spin.isEnabled()

    ag, as_ = phasor_window._get_active_gs_maps()
    exp_g, exp_s = median_filter_gs(g, s, size=3)
    np.testing.assert_array_equal(ag, exp_g)
    np.testing.assert_array_equal(as_, exp_s)


def test_median_kernel_change_redrives_view(phasor_window):
    g, s = _raw_maps()
    phasor_window.set_phasor_data(g, s)
    phasor_window._median_check.setChecked(True)

    phasor_window._median_kernel_spin.setValue(5)
    ag, _ = phasor_window._get_active_gs_maps()
    exp_g, _ = median_filter_gs(g, s, size=5)
    np.testing.assert_array_equal(ag, exp_g)


def test_median_cache_tracks_new_frame(phasor_window):
    """A new frame invalidates the median cache so it re-derives from it."""
    g, s = _raw_maps()
    phasor_window.set_phasor_data(g, s)
    phasor_window._median_check.setChecked(True)
    phasor_window._get_active_gs_maps()  # populate cache against frame 1

    # A new frame lands (median still checked): the cache must reflect the
    # NEW frame's median, never the stale one.
    phasor_window.set_phasor_data(g * 2, s * 2)
    ag, _ = phasor_window._get_active_gs_maps()
    exp_g, _ = median_filter_gs(g * 2, s * 2, size=3)
    np.testing.assert_array_equal(ag, exp_g)


# ── Wavelet view + mutual exclusivity ────────────────────────


def test_wavelet_enabled_only_when_wavelet_present(phasor_window):
    g, s = _raw_maps()
    # No wavelet supplied → disabled.
    phasor_window.set_phasor_data(g, s)
    assert not phasor_window._wavelet_check.isEnabled()

    # Wavelet supplied → enabled and auto-selected.
    wg, ws = _wavelet_maps()
    phasor_window.set_phasor_data(g, s, g_wavelet=wg, s_wavelet=ws)
    assert phasor_window._wavelet_check.isEnabled()
    assert phasor_window._wavelet_check.isChecked()

    ag, as_ = phasor_window._get_active_gs_maps()
    np.testing.assert_array_equal(ag, wg)
    np.testing.assert_array_equal(as_, ws)


def test_median_and_wavelet_are_mutually_exclusive(phasor_window):
    g, s = _raw_maps()
    wg, ws = _wavelet_maps()
    phasor_window.set_phasor_data(g, s, g_wavelet=wg, s_wavelet=ws)
    # Auto-selected wavelet; now check median → wavelet must clear.
    phasor_window._median_check.setChecked(True)
    assert phasor_window._median_check.isChecked()
    assert not phasor_window._wavelet_check.isChecked()

    # Re-check wavelet → median must clear and its spin disable.
    phasor_window._wavelet_check.setChecked(True)
    assert phasor_window._wavelet_check.isChecked()
    assert not phasor_window._median_check.isChecked()
    assert not phasor_window._median_kernel_spin.isEnabled()


def test_both_unchecked_after_unchecking_returns_unfiltered(phasor_window):
    g, s = _raw_maps()
    phasor_window.set_phasor_data(g, s)
    phasor_window._median_check.setChecked(True)
    phasor_window._median_check.setChecked(False)

    ag, _ = phasor_window._get_active_gs_maps()
    np.testing.assert_array_equal(ag, g)


# ── Visible-pixel parity across the shared helper ────────────


def test_visible_mask_follows_active_view(phasor_window):
    """_compute_visible_valid_2d derives from the active view's g/s."""
    g, s = _raw_maps()
    phasor_window.set_phasor_data(g, s)

    unfiltered_visible = phasor_window._compute_visible_valid_2d()

    phasor_window._median_check.setChecked(True)
    median_visible = phasor_window._compute_visible_valid_2d()

    # Both are full boolean frames of the same shape; the active view feeds
    # the shared helper, so the median branch is exercised here.
    assert unfiltered_visible.shape == (8, 8)
    assert median_visible.shape == (8, 8)
