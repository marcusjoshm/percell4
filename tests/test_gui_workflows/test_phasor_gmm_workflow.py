"""Qt-level integration tests for the phasor plot's GMM workflow surface (U5).

Exercises ``set_phasor_filters`` (filter-knob push from FlimPanel),
``place_gmm_rois`` (append + 10-cap + color cycle), and the cov_f /
shift / Reset to fit slots on the Selected-ROI panel. The pure
geometry math is covered in ``tests/test_flim/test_phasor_gmm.py``;
this file focuses on the GUI plumbing and Qt invariants.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.run_phasor_gmm import PhasorROIGeometry
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import (
    GMMFit,
    PhasorPlotWindow,
)


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    sess = Session()
    handle = DatasetHandle(
        path=tmp_path / "fake.h5",
        metadata={"flim_frequency_mhz": 80.0},
    )
    sess._dataset = handle
    return sess


@pytest.fixture
def fake_repo() -> MagicMock:
    repo = MagicMock()
    repo.read_mask.return_value = np.ones((16, 16), dtype=np.uint8)
    return repo


@pytest.fixture
def phasor_window(qtbot, session_with_dataset, fake_repo) -> PhasorPlotWindow:
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: fake_repo)
    qtbot.addWidget(win)
    return win


@pytest.fixture
def phasor_window_with_data(phasor_window) -> PhasorPlotWindow:
    """Phasor window seeded with a 16x16 (g, s) frame."""
    rng = np.random.default_rng(11)
    g = rng.uniform(0.2, 0.6, size=(16, 16)).astype(np.float32)
    s = rng.uniform(0.1, 0.5, size=(16, 16)).astype(np.float32)
    intensity = np.full((16, 16), 100.0, dtype=np.float32)
    phasor_window.set_phasor_data(g, s, intensity=intensity)
    return phasor_window


def _make_geometry(
    label: int = 1,
    *,
    mean_g: float = 0.4,
    mean_s: float = 0.3,
) -> PhasorROIGeometry:
    return PhasorROIGeometry(
        center=(mean_g, mean_s),
        radii=(0.10, 0.05),
        angle_deg=15.0,
        mean_g=mean_g, mean_s=mean_s,
        lambda_major=0.0025, lambda_minor=0.000625,
        principal_angle_rad=np.radians(15.0),
        label=label,
    )


# ── place_gmm_rois ───────────────────────────────────────────


def test_place_gmm_rois_appends_two_to_empty_list(phasor_window_with_data):
    win = phasor_window_with_data
    geos = [_make_geometry(1, mean_g=0.3), _make_geometry(2, mean_g=0.5)]
    win.place_gmm_rois(geos, shape="ellipse", criterion="BIC", sampled_pixels=100_000)
    assert len(win._roi_widgets) == 2
    names = [w.phasor_roi.name for w in win._roi_widgets]
    assert names == ["GMM_1", "GMM_2"]
    for w in win._roi_widgets:
        assert w.phasor_roi.origin == "gmm"
        assert isinstance(w.phasor_roi.gmm_fit, GMMFit)
        assert w.phasor_roi.gmm_fit.shape == "ellipse"
        assert w.phasor_roi.gmm_fit.criterion == "BIC"
        assert w.phasor_roi.gmm_fit.sampled_pixels == 100_000


def test_place_gmm_rois_appends_after_manual_rois(phasor_window_with_data, qtbot):
    win = phasor_window_with_data
    qtbot.mouseClick  # noqa: avoid lint — fixture is required to keep widgets alive
    win._on_add_roi()  # seed a manual ROI
    win._on_add_roi()
    assert len(win._roi_widgets) == 2
    geos = [_make_geometry(1)]
    win.place_gmm_rois(geos, shape="ellipse", criterion=None, sampled_pixels=50_000)
    assert len(win._roi_widgets) == 3
    # Prior manual ROIs unchanged
    assert win._roi_widgets[0].phasor_roi.origin == "manual"
    assert win._roi_widgets[1].phasor_roi.origin == "manual"
    assert win._roi_widgets[2].phasor_roi.origin == "gmm"


def test_place_gmm_rois_color_continues_global_cycle(phasor_window_with_data):
    """GMM ROI colors don't collide with manual ROIs already in the list."""
    win = phasor_window_with_data
    win._on_add_roi()  # COLOR_CYCLE[0]
    win._on_add_roi()  # COLOR_CYCLE[1]
    win._on_add_roi()  # COLOR_CYCLE[2]
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    # New ROI's color should be COLOR_CYCLE[3]
    from percell4.interfaces.gui.peer_views.phasor_plot import COLOR_CYCLE
    assert win._roi_widgets[3].phasor_roi.color == COLOR_CYCLE[3]
    # And NOT collide with any manual ROI's color
    manual_colors = {win._roi_widgets[i].phasor_roi.color for i in range(3)}
    assert win._roi_widgets[3].phasor_roi.color not in manual_colors


def test_place_gmm_rois_truncates_at_ten_cap(phasor_window_with_data):
    win = phasor_window_with_data
    for _ in range(8):
        win._on_add_roi()
    assert len(win._roi_widgets) == 8
    geos = [_make_geometry(i, mean_g=0.3 + i * 0.02) for i in range(1, 5)]
    win.place_gmm_rois(geos, shape="ellipse", criterion=None, sampled_pixels=50_000)
    # 8 manual + 2 GMM (truncated from 4) = 10 cap
    assert len(win._roi_widgets) == 10
    msg = win._status.currentMessage()
    assert "truncated 2" in msg


def test_place_gmm_rois_full_list_refuses_with_status(phasor_window_with_data):
    win = phasor_window_with_data
    for _ in range(10):
        win._on_add_roi()
    geos = [_make_geometry(1)]
    win.place_gmm_rois(geos, shape="ellipse", criterion=None, sampled_pixels=50_000)
    assert len(win._roi_widgets) == 10  # nothing added
    assert "ROI list full" in win._status.currentMessage()


def test_place_gmm_rois_unique_name_collision(phasor_window_with_data):
    """Same GMM_<N> twice → second gets _2 suffix."""
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    names = [w.phasor_roi.name for w in win._roi_widgets]
    assert names == ["GMM_1", "GMM_1_2"]


def test_place_gmm_rois_no_phasor_data_discards_with_status(phasor_window):
    win = phasor_window  # _g_map is None
    geos = [_make_geometry(1)]
    win.place_gmm_rois(geos, shape="ellipse", criterion=None, sampled_pixels=50_000)
    assert len(win._roi_widgets) == 0
    assert "Phasor data missing" in win._status.currentMessage()


# ── set_phasor_filters ───────────────────────────────────────


def test_set_phasor_filters_invalidates_caches(phasor_window_with_data):
    win = phasor_window_with_data
    win._on_add_roi()
    # Force compute of the cached mask
    win._compute_combined_mask()
    assert win._roi_widgets[0].cached_mask is not None
    win.set_phasor_filters(
        intensity_threshold=100.0,
        ref_circle_tau_ns=None,
        ref_circle_radius=None,
    )
    assert win._roi_widgets[0].cached_mask is None
    assert win._intensity_threshold == 100.0


def test_set_phasor_filters_resolves_ref_circle_with_freq(phasor_window_with_data):
    win = phasor_window_with_data
    win.set_phasor_filters(
        intensity_threshold=0.0,
        ref_circle_tau_ns=2.5,
        ref_circle_radius=0.5,
    )
    assert win._ref_circle_center is not None
    g_c, s_c = win._ref_circle_center
    # Lies on universal semicircle
    assert (g_c - 0.5) ** 2 + s_c ** 2 == pytest.approx(0.25, abs=1e-9)
    assert win._ref_circle_curve.isVisible()


def test_set_phasor_filters_freq_missing_degrades_silently(
    phasor_window_with_data, session_with_dataset
):
    """No flim_frequency_mhz → overlay hidden, no crash, status message."""
    win = phasor_window_with_data
    # Strip the freq from metadata
    session_with_dataset.dataset.metadata.pop("flim_frequency_mhz", None)
    win.set_phasor_filters(
        intensity_threshold=0.0,
        ref_circle_tau_ns=2.5,
        ref_circle_radius=0.5,
    )
    assert win._ref_circle_center is None
    assert not win._ref_circle_curve.isVisible()
    assert "flim_frequency_mhz" in win._status.currentMessage()


def test_set_phasor_filters_disabling_hides_overlay(phasor_window_with_data):
    win = phasor_window_with_data
    win.set_phasor_filters(
        intensity_threshold=0.0,
        ref_circle_tau_ns=2.5, ref_circle_radius=0.5,
    )
    assert win._ref_circle_curve.isVisible()
    win.set_phasor_filters(
        intensity_threshold=0.0,
        ref_circle_tau_ns=None, ref_circle_radius=None,
    )
    assert not win._ref_circle_curve.isVisible()


def test_set_phasor_filters_overlay_clipped_to_viewport(phasor_window_with_data):
    """A radius that pushes the circle out of S=[0,0.7] is clipped, not crashed."""
    win = phasor_window_with_data
    # tau=2.5ns + freq=80MHz + r=0.5 → top of circle at S≈0.99 (above 0.7)
    win.set_phasor_filters(
        intensity_threshold=0.0,
        ref_circle_tau_ns=2.5, ref_circle_radius=0.5,
    )
    assert win._ref_circle_curve.isVisible()
    # All emitted points must be in [0, 0.7]
    data = win._ref_circle_curve.getData()
    assert data is not None
    _, ss = data
    assert (ss >= 0.0).all()
    assert (ss <= 0.7).all()


# ── cov_f / shift / Reset to fit slots ───────────────────────


def _fit_for_geo(geo: PhasorROIGeometry, *, cov_f: float = 2.0, shift: float = 0.0) -> GMMFit:
    return GMMFit(
        mean_g=geo.mean_g, mean_s=geo.mean_s,
        lambda_major=geo.lambda_major, lambda_minor=geo.lambda_minor,
        principal_angle_rad=geo.principal_angle_rad,
        cov_f=cov_f, shift=shift,
        shape="ellipse", criterion=None, sampled_pixels=50_000,
    )


def test_cov_f_spinbox_disabled_for_manual_roi(phasor_window_with_data):
    win = phasor_window_with_data
    win._on_add_roi()
    win._roi_list.setCurrentRow(0)
    assert not win._cov_f_spin.isEnabled()
    assert not win._shift_spin.isEnabled()
    assert not win._reset_fit_btn.isEnabled()


def test_cov_f_spinbox_enabled_for_gmm_roi(phasor_window_with_data):
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    assert win._cov_f_spin.isEnabled()
    assert win._shift_spin.isEnabled()
    assert win._reset_fit_btn.isEnabled()
    # Spinbox values match the GMMFit defaults
    assert win._cov_f_spin.value() == pytest.approx(2.0)
    assert win._shift_spin.value() == pytest.approx(0.0)


def test_cov_f_change_grows_radii_proportionally(phasor_window_with_data):
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    initial_radii = win._roi_widgets[0].phasor_roi.radii
    win._cov_f_spin.setValue(3.0)
    new_radii = win._roi_widgets[0].phasor_roi.radii
    # 3.0 / 2.0 = 1.5x growth on both axes (eigenstructure unchanged)
    assert new_radii[0] == pytest.approx(initial_radii[0] * 1.5, abs=1e-9)
    assert new_radii[1] == pytest.approx(initial_radii[1] * 1.5, abs=1e-9)


def test_drag_then_cov_f_change_preserves_drag_position(phasor_window_with_data):
    """The user's drag is preserved across cov_f edits (not snapped back)."""
    win = phasor_window_with_data
    geo = _make_geometry(1, mean_g=0.4, mean_s=0.3)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    widget = win._roi_widgets[0]

    # Simulate a manual drag by directly setting RectROI position. Use
    # the public _on_roi_moved_widget hook which the real drag path also
    # invokes via sigRegionChangeFinished.
    rx, ry = widget.phasor_roi.radii
    new_cx, new_cy = 0.55, 0.45
    widget.roi.setPos((new_cx - rx, new_cy - ry))
    win._on_roi_moved_widget(widget)
    assert widget.phasor_roi.center == pytest.approx((new_cx, new_cy), abs=1e-9)

    # Now change cov_f. Center should stay around (0.55, 0.45), only
    # radii grow.
    win._cov_f_spin.setValue(3.0)
    assert widget.phasor_roi.center[0] == pytest.approx(0.55, abs=1e-3)
    assert widget.phasor_roi.center[1] == pytest.approx(0.45, abs=1e-3)


def test_reset_to_fit_returns_center_to_cluster_mean(phasor_window_with_data):
    win = phasor_window_with_data
    geo = _make_geometry(1, mean_g=0.4, mean_s=0.3)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    widget = win._roi_widgets[0]

    # Simulate drag away from mean
    rx, ry = widget.phasor_roi.radii
    new_cx, new_cy = 0.6, 0.5
    widget.roi.setPos((new_cx - rx, new_cy - ry))
    win._on_roi_moved_widget(widget)
    assert widget.phasor_roi.center != pytest.approx((0.4, 0.3), abs=1e-3)

    # Reset to fit → back to (mean_g, mean_s) since shift == 0
    win._on_reset_fit_clicked()
    assert widget.phasor_roi.center[0] == pytest.approx(0.4, abs=1e-9)
    assert widget.phasor_roi.center[1] == pytest.approx(0.3, abs=1e-9)


def test_cov_f_change_does_not_loop_through_roi_moved_widget(
    phasor_window_with_data, monkeypatch
):
    """RectROI.setPos / setSize are wrapped in blockSignals so the slot
    doesn't feed back through ``_on_roi_moved_widget``."""
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)

    spy = MagicMock(side_effect=win._on_roi_moved_widget)
    monkeypatch.setattr(win, "_on_roi_moved_widget", spy)

    win._cov_f_spin.setValue(3.0)
    assert spy.call_count == 0


def test_v2_json_round_trip_then_cov_f_edit(phasor_window_with_data, tmp_path):
    """Load v2 JSON with a GMM-origin ROI and edit cov_f via the spinbox."""
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion="BIC", sampled_pixels=50_000)
    save_path = tmp_path / "rois.json"
    # Save via the public dataclass round-trip (avoid the QFileDialog path)
    import json
    data = {
        "schema_version": 2,
        "rois": [w.phasor_roi.to_dict() for w in win._roi_widgets],
    }
    save_path.write_text(json.dumps(data))

    # Clear and load
    while win._roi_widgets:
        win._selected_roi_index = 0
        win._on_remove_roi()
    assert len(win._roi_widgets) == 0

    blob = json.loads(save_path.read_text())
    from percell4.interfaces.gui.peer_views.phasor_plot import COLOR_CYCLE, PhasorROI
    for i, roi_data in enumerate(blob["rois"]):
        roi = PhasorROI.from_dict(
            roi_data, label=i + 1, default_color=COLOR_CYCLE[i % len(COLOR_CYCLE)],
        )
        win._create_roi_widget(roi)
    win._refresh_roi_list()
    win._roi_list.setCurrentRow(0)

    # cov_f edit works against the loaded gmm_fit
    initial_radii = win._roi_widgets[0].phasor_roi.radii
    win._cov_f_spin.setValue(3.0)
    new_radii = win._roi_widgets[0].phasor_roi.radii
    assert new_radii[0] == pytest.approx(initial_radii[0] * 1.5, abs=1e-9)


def test_close_event_stops_timers_before_unsubscribe(phasor_window_with_data):
    """Timers must stop before unsub so a queued slot doesn't fire post-teardown."""
    win = phasor_window_with_data
    win._filter_timer.start()
    win._preview_timer.start()
    assert win._filter_timer.isActive()

    # Trigger close — we don't actually close the window (it ignores the
    # event) but the cleanup still runs.
    from qtpy.QtGui import QCloseEvent
    win.closeEvent(QCloseEvent())
    assert not win._filter_timer.isActive()
    assert not win._preview_timer.isActive()
