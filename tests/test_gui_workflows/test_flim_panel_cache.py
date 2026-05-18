"""Tests for FlimPanel cache-check behavior on Compute Phasor / Apply Wavelet.

Both buttons load from cache by default; Shift-click forces recompute.
The cache check goes through LoadCachedPhasor (U1); the recompute path
goes through the existing ComputePhasor / ApplyWavelet use cases.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from qtpy.QtCore import Qt

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.task_panels.flim_panel import FlimPanel
from percell4.model import CellDataModel


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


def _seed_full_cache(repo: FakeRepo, channel: str = "ch0", shape=(8, 8)):
    rng = np.random.default_rng(0)
    repo.arrays[f"phasor/{channel}/g"] = rng.uniform(0.1, 0.9, size=shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/s"] = rng.uniform(0.05, 0.5, size=shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/g_filtered"] = rng.uniform(size=shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/s_filtered"] = rng.uniform(size=shape).astype(np.float32)
    repo.arrays[f"decay/{channel}"] = rng.uniform(size=(*shape, 16)).astype(np.float32)


@pytest.fixture
def session_with_dataset(tmp_path):
    s = Session()
    handle = DatasetHandle(path=tmp_path / "fake.h5", metadata={"flim_frequency_mhz": 80.0})
    s._dataset = handle
    s._active_channel = "ch0"
    return s


@pytest.fixture
def panel(qtbot, session_with_dataset):
    repo = FakeRepo()
    _seed_full_cache(repo)
    phasor_win = MagicMock()
    data_model = CellDataModel(session_with_dataset)
    p = FlimPanel(
        data_model,
        get_repo=lambda: repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: phasor_win,
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=lambda _: None,
    )
    qtbot.addWidget(p)
    p._test_repo = repo
    p._test_phasor_win = phasor_win
    return p


# ── Cache-hit path ────────────────────────────────────────────


def test_compute_phasor_loads_from_cache_when_no_shift(panel):
    """No Shift held + cache present → compute use case NOT invoked; phasor window populated from cache."""
    with patch(
        "percell4.application.use_cases.compute_phasor.ComputePhasor.execute"
    ) as mock_compute:
        with patch.object(panel, "_shift_held", return_value=False):
            panel._on_compute_phasor()

    mock_compute.assert_not_called()
    panel._test_phasor_win.set_phasor_data.assert_called_once()


def test_apply_wavelet_loads_from_cache_when_no_shift(panel):
    """No Shift held + wavelet cache present → wavelet use case NOT invoked."""
    with patch(
        "percell4.application.use_cases.apply_wavelet.ApplyWavelet.execute"
    ) as mock_wavelet:
        with patch.object(panel, "_shift_held", return_value=False):
            panel._on_apply_wavelet()

    mock_wavelet.assert_not_called()
    panel._test_phasor_win.set_phasor_data.assert_called_once()


# ── Shift bypass ──────────────────────────────────────────────


def test_compute_phasor_shift_bypasses_cache(panel):
    """Shift held → ComputePhasor.execute IS invoked even when cache present."""
    fake_result = MagicMock(g_map=np.zeros((4, 4), dtype=np.float32),
                            s_map=np.zeros((4, 4), dtype=np.float32),
                            n_valid=10)
    with patch(
        "percell4.application.use_cases.compute_phasor.ComputePhasor.execute",
        return_value=fake_result,
    ) as mock_compute:
        with patch.object(panel, "_shift_held", return_value=True):
            panel._on_compute_phasor()

    mock_compute.assert_called_once()


def test_apply_wavelet_shift_bypasses_cache(panel):
    """Shift held → ApplyWavelet.execute IS invoked even when wavelet cache present."""
    fake_result = MagicMock(
        g_filtered=np.zeros((4, 4), dtype=np.float32),
        s_filtered=np.zeros((4, 4), dtype=np.float32),
        n_valid=10,
    )
    with patch(
        "percell4.application.use_cases.apply_wavelet.ApplyWavelet.execute",
        return_value=fake_result,
    ) as mock_wavelet:
        with patch.object(panel, "_shift_held", return_value=True):
            panel._on_apply_wavelet()

    mock_wavelet.assert_called_once()


# ── No cache → fall through to compute ────────────────────────


def test_compute_phasor_no_cache_runs_compute(panel):
    """Empty repo → cache check raises NoCachedPhasorError → ComputePhasor runs."""
    panel._test_repo.arrays.clear()
    fake_result = MagicMock(g_map=np.zeros((4, 4), dtype=np.float32),
                            s_map=np.zeros((4, 4), dtype=np.float32),
                            n_valid=10)
    with patch(
        "percell4.application.use_cases.compute_phasor.ComputePhasor.execute",
        return_value=fake_result,
    ) as mock_compute:
        with patch.object(panel, "_shift_held", return_value=False):
            panel._on_compute_phasor()

    mock_compute.assert_called_once()


def test_apply_wavelet_raw_cache_only_runs_wavelet(panel):
    """Raw phasor cache but no wavelet → falls through to wavelet compute."""
    # Remove wavelet cache, keep raw
    panel._test_repo.arrays.pop("phasor/ch0/g_filtered")
    panel._test_repo.arrays.pop("phasor/ch0/s_filtered")
    fake_result = MagicMock(
        g_filtered=np.zeros((4, 4), dtype=np.float32),
        s_filtered=np.zeros((4, 4), dtype=np.float32),
        n_valid=10,
    )
    with patch(
        "percell4.application.use_cases.apply_wavelet.ApplyWavelet.execute",
        return_value=fake_result,
    ) as mock_wavelet:
        with patch.object(panel, "_shift_held", return_value=False):
            panel._on_apply_wavelet()

    mock_wavelet.assert_called_once()


# ── Tooltip discoverability ───────────────────────────────────


def test_compute_phasor_button_tooltip_mentions_shift(panel):
    """Find the Compute Phasor button in the panel and assert tooltip contains 'Shift+click'."""
    from qtpy.QtWidgets import QPushButton

    buttons = panel.findChildren(QPushButton)
    compute_btn = next(b for b in buttons if b.text() == "Compute Phasor")
    assert "Shift+click" in compute_btn.toolTip()


def test_apply_wavelet_button_tooltip_mentions_shift(panel):
    from qtpy.QtWidgets import QPushButton

    buttons = panel.findChildren(QPushButton)
    wavelet_btn = next(b for b in buttons if b.text() == "Apply Wavelet Filter")
    assert "Shift+click" in wavelet_btn.toolTip()


# ── No active channel ─────────────────────────────────────────


def test_compute_phasor_no_active_channel_returns_early(panel, session_with_dataset):
    session_with_dataset._active_channel = None
    with patch(
        "percell4.application.use_cases.compute_phasor.ComputePhasor.execute"
    ) as mock_compute:
        panel._on_compute_phasor()
    mock_compute.assert_not_called()
    panel._test_phasor_win.set_phasor_data.assert_not_called()
