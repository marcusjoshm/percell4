"""Tests for FlimPanel's Compute Lifetime source selector (U5).

The source dropdown (Unfiltered / Median / Wavelet) and the median kernel
spinbox must forward the chosen source + kernel into ComputeLifetime, and
the Wavelet item must be gated on a present wavelet result.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.compute_lifetime import LifetimeResult
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.task_panels.flim_panel import FlimPanel
from percell4.model import CellDataModel


class FakeRepo:
    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.disk_metadata: dict = {"flim_frequency_mhz": 80.0}

    def write_array(self, handle, path, data, attrs=None):
        self.arrays[path] = data

    def read_array(self, handle, path, view_bin=1):
        if path not in self.arrays:
            raise KeyError(f"Array not found: {path}")
        return self.arrays[path]

    def read_metadata(self, handle):
        return dict(self.disk_metadata)


def _seed(repo: FakeRepo, channel="ch0", shape=(8, 8), *, wavelet=True):
    rng = np.random.default_rng(0)
    repo.arrays[f"phasor/{channel}/g"] = rng.uniform(0.1, 0.9, shape).astype(np.float32)
    repo.arrays[f"phasor/{channel}/s"] = rng.uniform(0.05, 0.5, shape).astype(np.float32)
    if wavelet:
        repo.arrays[f"phasor/{channel}/g_filtered"] = rng.uniform(size=shape).astype(np.float32)
        repo.arrays[f"phasor/{channel}/s_filtered"] = rng.uniform(size=shape).astype(np.float32)


@pytest.fixture
def session_with_dataset(tmp_path):
    s = Session()
    s._dataset = DatasetHandle(
        path=tmp_path / "fake.h5", metadata={"flim_frequency_mhz": 80.0}
    )
    s._active_channel = "ch0"
    return s


def _make_panel(qtbot, session, repo):
    data_model = CellDataModel(session)
    p = FlimPanel(
        data_model,
        get_repo=lambda: repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: MagicMock(),
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=lambda _: None,
    )
    qtbot.addWidget(p)
    return p


def _fake_result(source):
    return LifetimeResult(
        lifetime=np.zeros((8, 8), dtype=np.float32),
        channel="ch0",
        source=source,
        mean_tau=1.5,
        frequency_mhz=80.0,
        median_size=3 if source == "median" else None,
    )


@pytest.mark.parametrize(
    "index,expected_source",
    [(0, "unfiltered"), (1, "median"), (2, "wavelet")],
)
def test_source_combo_forwards_choice(qtbot, session_with_dataset, index, expected_source):
    repo = FakeRepo()
    _seed(repo, wavelet=True)
    panel = _make_panel(qtbot, session_with_dataset, repo)

    panel._lifetime_source_combo.setCurrentIndex(index)
    panel._lifetime_median_kernel.setValue(5)

    with patch(
        "percell4.application.use_cases.compute_lifetime.ComputeLifetime.execute",
        return_value=_fake_result(expected_source),
    ) as mock_exec:
        panel._on_compute_lifetime()

    mock_exec.assert_called_once()
    kwargs = mock_exec.call_args.kwargs
    assert kwargs["source"] == expected_source
    assert kwargs["median_size"] == 5
    assert kwargs["channel"] == "ch0"


def test_median_kernel_enabled_only_for_median(qtbot, session_with_dataset):
    repo = FakeRepo()
    _seed(repo, wavelet=True)
    panel = _make_panel(qtbot, session_with_dataset, repo)

    panel._lifetime_source_combo.setCurrentIndex(0)  # Unfiltered
    assert not panel._lifetime_median_kernel.isEnabled()

    panel._lifetime_source_combo.setCurrentIndex(1)  # Median
    assert panel._lifetime_median_kernel.isEnabled()

    panel._lifetime_source_combo.setCurrentIndex(2)  # Wavelet
    assert not panel._lifetime_median_kernel.isEnabled()


def test_wavelet_source_disabled_without_wavelet(qtbot, session_with_dataset):
    repo = FakeRepo()
    _seed(repo, wavelet=False)
    panel = _make_panel(qtbot, session_with_dataset, repo)
    panel._refresh_lifetime_source_enabled()

    from qtpy.QtGui import QStandardItemModel

    model = panel._lifetime_source_combo.model()
    assert isinstance(model, QStandardItemModel)
    assert not model.item(2).isEnabled()  # Wavelet item disabled


def test_wavelet_source_enabled_after_wavelet_present(qtbot, session_with_dataset):
    repo = FakeRepo()
    _seed(repo, wavelet=True)
    panel = _make_panel(qtbot, session_with_dataset, repo)
    panel._refresh_lifetime_source_enabled()

    from qtpy.QtGui import QStandardItemModel

    model = panel._lifetime_source_combo.model()
    assert isinstance(model, QStandardItemModel)
    assert model.item(2).isEnabled()


def test_selection_falls_back_when_wavelet_becomes_unavailable(qtbot, session_with_dataset):
    repo = FakeRepo()
    _seed(repo, wavelet=True)
    panel = _make_panel(qtbot, session_with_dataset, repo)
    panel._refresh_lifetime_source_enabled()
    panel._lifetime_source_combo.setCurrentIndex(2)  # Wavelet

    # Wavelet result disappears (e.g. phasor recomputed) → selection resets.
    del repo.arrays["phasor/ch0/g_filtered"]
    panel._refresh_lifetime_source_enabled()

    assert panel._lifetime_source_combo.currentIndex() == 0  # Unfiltered
