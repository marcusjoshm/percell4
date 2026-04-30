"""Qt-level integration tests for the phasor plot's mask-filter checkbox.

Covers the checkbox enable/disable behavior driven by
``Session.set_active_mask``, the lazy-load + cache pattern for the mask
array, and that toggling the checkbox triggers a histogram refresh.

The pure filter composition (cell-only, mask-only, AND, shape mismatch)
is covered in ``tests/test_flim/test_phasor_display.py`` — those tests
don't need Qt and run faster.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    """A Session with a fake DatasetHandle attached."""
    sess = Session()
    handle = DatasetHandle(path=tmp_path / "fake.h5", metadata={})
    sess._dataset = handle
    return sess


@pytest.fixture
def fake_repo() -> MagicMock:
    """A repo with read_mask() returning a 16x16 binary mask by default."""
    repo = MagicMock()
    repo.read_mask.return_value = np.ones((16, 16), dtype=np.uint8)
    return repo


@pytest.fixture
def phasor_window(qtbot, session_with_dataset, fake_repo) -> PhasorPlotWindow:
    """A PhasorPlotWindow wired to a fake session + repo."""
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: fake_repo)
    qtbot.addWidget(win)
    return win


def test_mask_filter_checkbox_starts_disabled(phasor_window):
    """No active_mask → checkbox disabled and unchecked."""
    assert not phasor_window._mask_filter_check.isEnabled()
    assert not phasor_window._mask_filter_check.isChecked()


def test_checkbox_enables_on_active_mask_set(phasor_window, session_with_dataset):
    """Setting active_mask enables the checkbox."""
    session_with_dataset.set_active_mask("nucleus")
    assert phasor_window._mask_filter_check.isEnabled()
    # But not auto-checked — opt-in is load-bearing
    assert not phasor_window._mask_filter_check.isChecked()


def test_checkbox_disables_and_unchecks_on_active_mask_clear(
    phasor_window, session_with_dataset
):
    """Clearing active_mask disables AND unchecks the checkbox."""
    session_with_dataset.set_active_mask("nucleus")
    phasor_window._mask_filter_check.setChecked(True)
    assert phasor_window._mask_filter_check.isChecked()

    session_with_dataset.set_active_mask(None)
    assert not phasor_window._mask_filter_check.isEnabled()
    assert not phasor_window._mask_filter_check.isChecked()


def test_load_active_mask_flat_returns_none_when_unchecked(phasor_window):
    """Helper returns None when the user hasn't opted in."""
    phasor_window._g_map = np.zeros((16, 16), dtype=np.float32)
    assert phasor_window._load_active_mask_flat() is None


def test_load_active_mask_flat_caches_after_first_read(
    phasor_window, session_with_dataset, fake_repo
):
    """Mask is read once and cached for subsequent calls."""
    session_with_dataset.set_active_mask("nucleus")
    phasor_window._g_map = np.zeros((16, 16), dtype=np.float32)
    phasor_window._mask_filter_check.setChecked(True)

    # Reset call count after the toggle (which itself calls _refresh_histogram)
    fake_repo.reset_mock()

    flat1 = phasor_window._load_active_mask_flat()
    flat2 = phasor_window._load_active_mask_flat()
    flat3 = phasor_window._load_active_mask_flat()

    assert flat1 is flat2 is flat3  # same cached array
    assert fake_repo.read_mask.call_count == 1  # only one HDF5 read


def test_active_mask_change_invalidates_cache(
    phasor_window, session_with_dataset, fake_repo
):
    """Switching to a different active_mask drops the cached array."""
    session_with_dataset.set_active_mask("nucleus")
    phasor_window._g_map = np.zeros((16, 16), dtype=np.float32)
    phasor_window._mask_filter_check.setChecked(True)
    phasor_window._load_active_mask_flat()
    fake_repo.reset_mock()

    # Switch to a different mask
    fake_repo.read_mask.return_value = np.zeros((16, 16), dtype=np.uint8)
    session_with_dataset.set_active_mask("cytoplasm")

    flat = phasor_window._load_active_mask_flat()
    assert flat is not None
    assert (flat == 0).all()
    fake_repo.read_mask.assert_called_once()


def test_shape_mismatch_returns_none_no_crash(
    phasor_window, session_with_dataset, fake_repo
):
    """Mask of wrong shape silently bypasses the filter."""
    session_with_dataset.set_active_mask("nucleus")
    phasor_window._g_map = np.zeros((32, 32), dtype=np.float32)  # 32x32
    fake_repo.read_mask.return_value = np.ones((16, 16), dtype=np.uint8)  # 16x16
    phasor_window._mask_filter_check.setChecked(True)

    flat = phasor_window._load_active_mask_flat()
    assert flat is None
    # Checkbox stays checked so the user can fix the mismatch and refresh
    assert phasor_window._mask_filter_check.isChecked()


def test_repo_read_failure_returns_none_no_crash(
    phasor_window, session_with_dataset, fake_repo
):
    """KeyError or OSError from repo.read_mask is caught, filter bypassed."""
    session_with_dataset.set_active_mask("missing")
    phasor_window._g_map = np.zeros((16, 16), dtype=np.float32)
    fake_repo.read_mask.side_effect = KeyError("masks/missing")
    phasor_window._mask_filter_check.setChecked(True)

    flat = phasor_window._load_active_mask_flat()
    assert flat is None


def test_checkbox_state_synced_for_preexisting_mask(
    qtbot, session_with_dataset, fake_repo
):
    """When the window opens with active_mask already set, checkbox is enabled."""
    session_with_dataset.set_active_mask("preexisting")
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: fake_repo)
    qtbot.addWidget(win)
    assert win._mask_filter_check.isEnabled()
    assert not win._mask_filter_check.isChecked()  # opt-in, not auto-on


def test_no_repo_returns_none_gracefully(qtbot, session_with_dataset):
    """When the window has no get_repo, the filter degrades to no-op."""
    win = PhasorPlotWindow(session_with_dataset, get_repo=None)
    qtbot.addWidget(win)
    session_with_dataset.set_active_mask("nucleus")
    win._g_map = np.zeros((16, 16), dtype=np.float32)
    win._mask_filter_check.setChecked(True)

    # Should not crash; returns None
    assert win._load_active_mask_flat() is None
