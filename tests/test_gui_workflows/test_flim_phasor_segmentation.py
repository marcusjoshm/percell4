"""Qt-level integration tests for the FLIM panel's segmentation workflow (U6).

Covers Phasor Filters group (intensity threshold, reference circle with
freq-aware enable), Phasor Segmentation group (Shape, Auto, n_clusters,
criterion, cov_f, shift), Run GMM button + worker plumbing, and the
two terminal handlers ``_on_gmm_finished`` (with dataset-snapshot
mismatch protection) / ``_on_gmm_error``.

Pure GMM math is covered in tests/test_flim/test_phasor_gmm.py; the use
case shape in tests/test_use_cases.py::TestRunPhasorGMM; the phasor
plot's downstream ``set_phasor_filters`` and ``place_gmm_rois`` in
tests/test_gui_workflows/test_phasor_gmm_workflow.py. This file focuses
on the FLIM tab as the orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.run_phasor_gmm import (
    PhasorROIGeometry,
    RunPhasorGMMResult,
)
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow
from percell4.interfaces.gui.task_panels.flim_panel import FlimPanel
from percell4.model import CellDataModel
from percell4.workflows.diagnostics import WorkerError


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def session_with_freq(tmp_path) -> Session:
    sess = Session()
    sess._dataset = DatasetHandle(
        path=tmp_path / "fake.h5",
        metadata={"flim_frequency_mhz": 80.0},
    )
    sess.set_active_channel("ch0")
    return sess


@pytest.fixture
def session_no_freq(tmp_path) -> Session:
    sess = Session()
    sess._dataset = DatasetHandle(
        path=tmp_path / "fake.h5",
        metadata={},
    )
    sess.set_active_channel("ch0")
    return sess


@pytest.fixture
def model_with_freq(session_with_freq) -> CellDataModel:
    return CellDataModel(session_with_freq)


@pytest.fixture
def model_no_freq(session_no_freq) -> CellDataModel:
    return CellDataModel(session_no_freq)


@pytest.fixture
def fake_repo() -> MagicMock:
    repo = MagicMock()
    # Default read_metadata returns whatever the dataset metadata holds
    repo.read_metadata = lambda handle: dict(handle.metadata)
    repo.read_mask.return_value = np.ones((16, 16), dtype=np.uint8)
    return repo


@pytest.fixture
def phasor_window(qtbot, session_with_freq, fake_repo) -> PhasorPlotWindow:
    win = PhasorPlotWindow(session_with_freq, get_repo=lambda: fake_repo)
    qtbot.addWidget(win)
    # Seed phasor data so place_gmm_rois has something to anchor to
    rng = np.random.default_rng(11)
    g = rng.uniform(0.2, 0.6, size=(16, 16)).astype(np.float32)
    s = rng.uniform(0.1, 0.5, size=(16, 16)).astype(np.float32)
    intensity = np.full((16, 16), 100.0, dtype=np.float32)
    win.set_phasor_data(g, s, intensity=intensity)
    return win


@pytest.fixture
def flim_panel(qtbot, model_with_freq, fake_repo, phasor_window) -> FlimPanel:
    statuses: list[str] = []
    panel = FlimPanel(
        model_with_freq,
        get_repo=lambda: fake_repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: phasor_window,
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=statuses.append,
    )
    panel._statuses = statuses  # type: ignore[attr-defined]  # for assertion helpers
    qtbot.addWidget(panel)
    return panel


@pytest.fixture
def flim_panel_no_freq(qtbot, model_no_freq, fake_repo) -> FlimPanel:
    panel = FlimPanel(
        model_no_freq,
        get_repo=lambda: fake_repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: None,
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=lambda _: None,
    )
    qtbot.addWidget(panel)
    return panel


def _make_geometry(label: int = 1, mean_g: float = 0.4) -> PhasorROIGeometry:
    return PhasorROIGeometry(
        center=(mean_g, 0.3),
        radii=(0.10, 0.05),
        angle_deg=15.0,
        mean_g=mean_g, mean_s=0.3,
        lambda_major=0.0025, lambda_minor=0.000625,
        principal_angle_rad=np.radians(15.0),
        label=label,
    )


# ── Phasor Filters group ─────────────────────────────────────


def test_intensity_threshold_pushes_to_phasor_plot(flim_panel, phasor_window):
    flim_panel._intensity_threshold_spin.setValue(150)
    assert phasor_window._intensity_threshold == 150.0


def test_ref_circle_disabled_when_no_freq(flim_panel_no_freq):
    assert not flim_panel_no_freq._ref_circle_check.isEnabled()
    assert "needs to be set" in flim_panel_no_freq._ref_circle_check.toolTip()


def test_ref_circle_enabled_when_freq_present(flim_panel):
    assert flim_panel._ref_circle_check.isEnabled()


def test_ref_circle_check_enables_tau_and_radius_spinboxes(flim_panel, phasor_window):
    assert not flim_panel._ref_circle_tau_spin.isEnabled()
    assert not flim_panel._ref_circle_radius_spin.isEnabled()
    flim_panel._ref_circle_check.setChecked(True)
    assert flim_panel._ref_circle_tau_spin.isEnabled()
    assert flim_panel._ref_circle_radius_spin.isEnabled()
    # Push lands on the phasor plot
    assert phasor_window._ref_circle_tau_ns is not None
    assert phasor_window._ref_circle_center is not None


def test_unchecking_ref_circle_disables_filter_on_phasor_plot(flim_panel, phasor_window):
    flim_panel._ref_circle_check.setChecked(True)
    assert phasor_window._ref_circle_radius is not None
    flim_panel._ref_circle_check.setChecked(False)
    assert phasor_window._ref_circle_radius is None


def test_push_filters_when_phasor_window_closed_does_not_crash(
    qtbot, model_with_freq, fake_repo
):
    statuses: list[str] = []
    panel = FlimPanel(
        model_with_freq,
        get_repo=lambda: fake_repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: None,  # phasor window not open
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=statuses.append,
    )
    qtbot.addWidget(panel)
    # Should be a silent no-op, not an AttributeError
    panel._intensity_threshold_spin.setValue(50)


# ── Phasor Segmentation group ───────────────────────────────


def test_flim_panel_does_not_expose_cov_f_or_shift_spinboxes(flim_panel):
    """Per-ROI tuning lives exclusively in the phasor window."""
    assert not hasattr(flim_panel, "_cov_f_spin")
    assert not hasattr(flim_panel, "_shift_spin")


def test_auto_toggles_n_label_and_criterion_combo(flim_panel):
    # Default Auto=True
    assert flim_panel._n_label.text() == "n_max:"
    assert flim_panel._criterion_combo.isEnabled()
    flim_panel._auto_check.setChecked(False)
    assert flim_panel._n_label.text() == "n:"
    assert not flim_panel._criterion_combo.isEnabled()
    flim_panel._auto_check.setChecked(True)
    assert flim_panel._n_label.text() == "n_max:"
    assert flim_panel._criterion_combo.isEnabled()


def test_run_gmm_no_active_channel_returns_with_status(qtbot, fake_repo, phasor_window):
    sess = Session()
    sess._dataset = DatasetHandle(
        path=Path("/tmp/x.h5"), metadata={"flim_frequency_mhz": 80.0},
    )
    # No active channel set
    statuses: list[str] = []
    panel = FlimPanel(
        CellDataModel(sess),
        get_repo=lambda: fake_repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: phasor_window,
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=statuses.append,
    )
    qtbot.addWidget(panel)
    panel._on_run_gmm()
    assert any("channel" in s for s in statuses)
    assert panel._gmm_worker is None
    # Button stays enabled because we never disabled it
    assert panel._run_gmm_btn.isEnabled()


def test_run_gmm_no_phasor_window_returns_with_status(qtbot, model_with_freq, fake_repo):
    statuses: list[str] = []
    panel = FlimPanel(
        model_with_freq,
        get_repo=lambda: fake_repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: None,
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=statuses.append,
    )
    qtbot.addWidget(panel)
    panel._on_run_gmm()
    assert any("Phasor Plot" in s for s in statuses)
    assert panel._gmm_worker is None


def test_run_gmm_double_click_race_guard(flim_panel, monkeypatch):
    """A second click while the worker is running is ignored."""
    fake_worker = MagicMock()
    fake_worker.isRunning.return_value = True
    flim_panel._gmm_worker = fake_worker
    # Sentinel: if a second worker were created, kwargs would be assembled
    # and uc.execute would be referenced. We didn't replace anything, so
    # the only verifiable side effect is that _gmm_worker is unchanged.
    flim_panel._on_run_gmm()
    assert flim_panel._gmm_worker is fake_worker


# ── _on_gmm_finished / _on_gmm_error ─────────────────────────


def test_gmm_finished_places_rois_via_phasor_window(flim_panel, phasor_window):
    geometries = [_make_geometry(1), _make_geometry(2, mean_g=0.55)]
    result = RunPhasorGMMResult(
        geometries=geometries,
        chosen_n=2,
        criterion="BIC",
        criterion_value=-1234.0,
        sampled_pixels=100_000,
        dataset_path=phasor_window._session.dataset.path,
    )
    flim_panel._run_gmm_btn.setEnabled(False)
    flim_panel._on_gmm_finished(result)
    assert flim_panel._run_gmm_btn.isEnabled()
    assert len(phasor_window._roi_widgets) == 2


def test_gmm_finished_dataset_mismatch_discards_result(flim_panel, phasor_window):
    """Result computed on a different dataset → discarded, no ROIs placed."""
    geometries = [_make_geometry(1)]
    result = RunPhasorGMMResult(
        geometries=geometries,
        chosen_n=1,
        criterion=None,
        criterion_value=None,
        sampled_pixels=100_000,
        dataset_path=Path("/tmp/some/other/dataset.h5"),
    )
    flim_panel._run_gmm_btn.setEnabled(False)
    flim_panel._on_gmm_finished(result)
    assert flim_panel._run_gmm_btn.isEnabled()
    assert len(phasor_window._roi_widgets) == 0
    assert any(
        "Dataset changed mid-GMM" in s for s in flim_panel._statuses
    )


def test_gmm_finished_phasor_window_closed_discards(qtbot, model_with_freq, fake_repo):
    """Phasor window closed during the fit → status message, no crash."""
    statuses: list[str] = []
    panel = FlimPanel(
        model_with_freq,
        get_repo=lambda: fake_repo,
        get_viewer_window=lambda: None,
        get_phasor_window=lambda: None,  # closed mid-fit
        get_active_seg_labels=lambda: None,
        show_window=lambda _: None,
        show_status=statuses.append,
    )
    qtbot.addWidget(panel)
    result = RunPhasorGMMResult(
        geometries=[_make_geometry(1)],
        chosen_n=1, criterion=None, criterion_value=None,
        sampled_pixels=100_000,
        dataset_path=model_with_freq.session.dataset.path,
    )
    panel._run_gmm_btn.setEnabled(False)
    panel._on_gmm_finished(result)
    assert panel._run_gmm_btn.isEnabled()
    assert any("Phasor window closed" in s for s in statuses)


def test_gmm_error_re_enables_button_and_surfaces_message(flim_panel):
    err = WorkerError(
        exc_type="ValueError",
        message="Phasor data not found for channel 'ch0'.",
        is_import_error=False,
        winerror=None,
        traceback="",
    )
    flim_panel._run_gmm_btn.setEnabled(False)
    flim_panel._on_gmm_error(err)
    assert flim_panel._run_gmm_btn.isEnabled()
    assert any("Phasor data not found" in s for s in flim_panel._statuses)


def test_run_gmm_starts_worker_with_correct_kwargs(flim_panel, monkeypatch, phasor_window):
    """End-to-end kwarg assembly check — capture the call into Worker()."""
    captured = {}

    def fake_worker_ctor(fn, *args, **kwargs):
        captured["fn"] = fn
        captured["kwargs"] = kwargs
        worker = MagicMock()
        worker.isRunning.return_value = False
        return worker

    monkeypatch.setattr(
        "percell4.gui.workers.Worker", fake_worker_ctor,
    )
    # Set non-default values across the whole control surface
    flim_panel._intensity_threshold_spin.setValue(150)
    flim_panel._ref_circle_check.setChecked(True)
    flim_panel._ref_circle_tau_spin.setValue(3.0)
    flim_panel._ref_circle_radius_spin.setValue(0.4)
    flim_panel._shape_combo.setCurrentText("Circle")
    flim_panel._auto_check.setChecked(False)
    flim_panel._n_clusters_spin.setValue(3)

    flim_panel._on_run_gmm()

    kwargs = captured["kwargs"]
    assert kwargs["channel"] == "ch0"
    assert kwargs["shape"] == "circle"
    assert kwargs["n_components"] == 3
    assert kwargs["criterion"] is None  # auto off
    # Placement defaults are hardcoded on the FlimPanel; per-ROI tuning
    # lives in the Phasor window's Selected-ROI panel.
    assert kwargs["cov_f"] == pytest.approx(2.0)
    assert kwargs["shift"] == pytest.approx(0.0)
    assert kwargs["intensity_threshold"] == pytest.approx(150.0)
    assert kwargs["ref_circle_tau_ns"] == pytest.approx(3.0)
    assert kwargs["ref_circle_radius"] == pytest.approx(0.4)
    assert kwargs["use_filtered_gs"] is False  # phasor_window has no _filtered toggle on
    assert kwargs["harmonic"] == 1
    # Run GMM button should be disabled while the (fake) worker runs
    assert not flim_panel._run_gmm_btn.isEnabled()
