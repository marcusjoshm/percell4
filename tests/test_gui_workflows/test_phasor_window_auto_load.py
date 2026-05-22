"""Tests for PhasorPlotWindow auto-load on showEvent + active-channel switch.

Auto-load reads cached phasor via LoadCachedPhasor and populates the
window. Lazy: nothing happens until the window is shown or the active
channel changes while it is already visible.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Event, Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow


class FakeRepo:
    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.disk_metadata: dict = {}

    def write_array(self, handle, path, data, attrs=None):
        self.arrays[path] = data

    def read_array(self, handle, path, view_bin=1):
        if path not in self.arrays:
            raise KeyError(f"Array not found: {path}")
        return self.arrays[path]

    def read_metadata(self, handle):
        return dict(self.disk_metadata)


def _seed_full_cache(repo: FakeRepo, channel: str = "ch0", shape=(8, 8), seed: int = 0):
    rng = np.random.default_rng(seed)
    repo.arrays[f"phasor/{channel}/g"] = rng.uniform(0.1, 0.9, size=shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/s"] = rng.uniform(0.05, 0.5, size=shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/g_filtered"] = rng.uniform(size=shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/s_filtered"] = rng.uniform(size=shape).astype(np.float32)
    repo.arrays[f"decay/{channel}"] = rng.uniform(size=(*shape, 16)).astype(np.float32)


def _seed_raw_only(repo: FakeRepo, channel: str = "ch0", shape=(8, 8), seed: int = 0):
    rng = np.random.default_rng(seed)
    repo.arrays[f"phasor/{channel}/g"] = rng.uniform(size=shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/s"] = rng.uniform(size=shape).astype(np.float32)
    repo.arrays[f"decay/{channel}"] = rng.uniform(size=(*shape, 16)).astype(np.float32)


@pytest.fixture
def session_with_dataset(tmp_path):
    s = Session()
    handle = DatasetHandle(path=tmp_path / "fake.h5", metadata={"flim_frequency_mhz": 80.0})
    s._dataset = handle
    s._active_channel = "ch0"
    return s


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def window(qtbot, session_with_dataset, repo):
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)
    return win


# ── showEvent auto-load ───────────────────────────────────────


def test_show_with_full_cache_populates_window(qtbot, window, repo):
    _seed_full_cache(repo)
    assert window._g_map is None  # empty before show

    window.show()
    qtbot.waitExposed(window)

    # _g_map should now be populated with the raw (unfiltered) cache
    assert window._g_map is not None
    # the wavelet cache should be available as the optional view
    assert window._g_map_wavelet is not None


def test_show_with_raw_only_cache_populates_without_unfiltered(qtbot, window, repo):
    _seed_raw_only(repo)

    window.show()
    qtbot.waitExposed(window)

    assert window._g_map is not None
    # No wavelet cache → no wavelet view available
    assert window._g_map_wavelet is None


def test_show_with_no_cache_leaves_window_empty(qtbot, window):
    """No /phasor cache → auto-load no-ops, window stays empty."""
    window.show()
    qtbot.waitExposed(window)

    assert window._g_map is None


# ── Channel switch auto-load ──────────────────────────────────


def test_channel_switch_to_cached_rehydrates(qtbot, window, repo, session_with_dataset):
    _seed_full_cache(repo, channel="ch0", seed=0)
    _seed_full_cache(repo, channel="ch1", shape=(8, 8), seed=42)

    window.show()
    qtbot.waitExposed(window)
    initial_g = window._g_map.copy()

    # Switch to ch1
    session_with_dataset.set_active_channel("ch1")

    # _g_map should now be ch1's data, different from ch0
    assert window._g_map is not None
    # Both have the same shape but different random values
    assert not np.array_equal(window._g_map, initial_g)


def test_channel_switch_to_uncached_clears_display(qtbot, window, repo, session_with_dataset):
    """Decision #15: switching to uncached channel clears the histogram."""
    _seed_full_cache(repo, channel="ch0")
    # Do NOT seed ch1

    window.show()
    qtbot.waitExposed(window)
    assert window._g_map is not None

    session_with_dataset.set_active_channel("ch1")

    assert window._g_map is None
    # Status reflects the empty state
    assert "No phasor cached" in window._status.currentMessage()


def test_channel_switch_while_hidden_is_noop(qtbot, window, repo, session_with_dataset):
    """When the window is hidden, channel switch should NOT trigger auto-load."""
    _seed_full_cache(repo, channel="ch1")
    # Window is not shown.
    session_with_dataset.set_active_channel("ch1")

    assert window._g_map is None  # no auto-load fired


# ── Race guard ────────────────────────────────────────────────


# ── Dataset switch auto-load ─────────────────────────────────


def test_dataset_switch_same_channel_name_auto_loads_new_cache(qtbot, tmp_path, repo):
    """Switching datasets where both have the same active_channel name
    must auto-load the new dataset's cached phasor.

    Bug: Session.set_dataset suppresses ACTIVE_CHANNEL_CHANGED when
    prev_channel == new_channel (session.py:166). This is very common
    in microscopy workflows where every dataset has channels named
    'ch0'/'ch1'/... or domain-specific names that repeat across
    datasets. Without an explicit auto-load call in
    _on_dataset_changed, the user is forced to click Compute Phasor
    and then Apply Wavelet Filter every time they load a new dataset
    — even when the new dataset's HDF5 already contains both caches.
    """
    s = Session()
    handle_a = DatasetHandle(
        path=tmp_path / "a.h5",
        metadata={"flim_frequency_mhz": 80.0, "channel_names": ["ch0"]},
    )
    s.set_dataset(handle_a)

    win = PhasorPlotWindow(s, get_repo=lambda: repo)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    # No cache seeded → window is empty (no spurious data from showEvent).
    assert win._g_map is None

    # Seed the cache the new dataset will hit. Same channel name "ch0".
    _seed_full_cache(repo, channel="ch0")

    # Switch to dataset B with the same channel name. Per session.set_dataset
    # emit ordering, ACTIVE_CHANNEL_CHANGED is SUPPRESSED here because
    # prev_channel == new_channel == "ch0" — so the existing
    # _on_active_channel_changed auto-load path will NOT fire.
    handle_b = DatasetHandle(
        path=tmp_path / "b.h5",
        metadata={"flim_frequency_mhz": 80.0, "channel_names": ["ch0"]},
    )
    s.set_dataset(handle_b)

    # The fix: _on_dataset_changed must explicitly call
    # _try_auto_load_cached so the new dataset's cached phasor lands
    # without requiring user clicks. Mirror the existing pattern at
    # phasor_plot.py for the mask checkbox (line 1576-1578), which
    # already handles the same suppressed-event case for masks.
    assert win._g_map is not None, (
        "Phasor auto-load did not fire on dataset switch when prev_channel "
        "== new_channel — _on_dataset_changed needs to call "
        "_try_auto_load_cached() at the end."
    )
    # Wavelet was cached → the wavelet view is available alongside raw g/s.
    assert win._g_map_wavelet is not None


def test_dataset_switch_different_channel_name_still_auto_loads(
    qtbot, tmp_path, repo
):
    """Switching datasets where channel names differ also auto-loads.

    In this case ACTIVE_CHANNEL_CHANGED fires (prev != new), and the
    existing _on_active_channel_changed path would auto-load. The
    explicit call in _on_dataset_changed makes it idempotent — calling
    twice with the same cache hits the same set_phasor_data path.
    """
    s = Session()
    handle_a = DatasetHandle(
        path=tmp_path / "a.h5",
        metadata={"flim_frequency_mhz": 80.0, "channel_names": ["chA"]},
    )
    s.set_dataset(handle_a)

    win = PhasorPlotWindow(s, get_repo=lambda: repo)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    assert win._g_map is None

    _seed_full_cache(repo, channel="chB")

    handle_b = DatasetHandle(
        path=tmp_path / "b.h5",
        metadata={"flim_frequency_mhz": 80.0, "channel_names": ["chB"]},
    )
    s.set_dataset(handle_b)

    assert win._g_map is not None


def test_dataset_switch_no_cache_leaves_window_empty(qtbot, tmp_path, repo):
    """If the new dataset has no cached phasor, auto-load no-ops.
    'No phasor computed' status from _on_dataset_changed remains."""
    s = Session()
    handle_a = DatasetHandle(
        path=tmp_path / "a.h5",
        metadata={"flim_frequency_mhz": 80.0, "channel_names": ["ch0"]},
    )
    s.set_dataset(handle_a)
    _seed_full_cache(repo, channel="ch0")

    win = PhasorPlotWindow(s, get_repo=lambda: repo)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    assert win._g_map is not None  # auto-loaded from showEvent

    # Switch to a dataset with no cache. Same channel name (so
    # ACTIVE_CHANNEL_CHANGED suppressed). _on_dataset_changed clears
    # state, then _try_auto_load_cached sees no cache and silently
    # leaves _g_map cleared.
    handle_b = DatasetHandle(
        path=tmp_path / "b.h5",
        metadata={"flim_frequency_mhz": 80.0, "channel_names": ["ch0"]},
    )
    # Do NOT seed for the switch — but the existing seed remains under
    # "ch0" in the FakeRepo. Wipe so we simulate a truly empty cache.
    repo.arrays.clear()

    s.set_dataset(handle_b)

    assert win._g_map is None


def test_show_with_g_map_already_set_does_not_clobber(qtbot, window, repo):
    """If a compute is already in flight (g_map set), auto-load is a no-op."""
    _seed_full_cache(repo)
    # Pre-populate g_map directly to simulate an in-progress compute
    sentinel = np.ones((4, 4), dtype=np.float32)
    window._g_map = sentinel

    window.show()
    qtbot.waitExposed(window)

    # _g_map content is unchanged (no clobber). Compare values rather
    # than identity — a defensive copy in set_phasor_data would change
    # the object identity without changing the semantic outcome.
    assert window._g_map.shape == sentinel.shape
    np.testing.assert_array_equal(window._g_map, sentinel)


# ── Hide / show cycle ─────────────────────────────────────────


def test_hide_then_show_re_triggers_auto_load(qtbot, window, repo):
    """Closing (hide) then reopening should re-fire auto-load."""
    _seed_full_cache(repo)

    window.show()
    qtbot.waitExposed(window)
    assert window._g_map is not None

    # Close emits preview_all_cleared and hides; doesn't set _g_map to None
    # (the user may still want their data when reopening). Auto-load on
    # show with _g_map already set is a no-op (correct — no clobber).
    # To test hide→show re-trigger, simulate dataset close-and-reopen:
    window._g_map = None
    window.hide()
    window.show()
    qtbot.waitExposed(window)
    assert window._g_map is not None


# ── No active channel ─────────────────────────────────────────


def test_no_active_channel_skips_auto_load(qtbot, window, repo, session_with_dataset):
    _seed_full_cache(repo)
    session_with_dataset._active_channel = None

    window.show()
    qtbot.waitExposed(window)

    assert window._g_map is None
