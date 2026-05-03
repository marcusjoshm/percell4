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


def test_axis_spinboxes_disabled_for_manual_roi(phasor_window_with_data):
    win = phasor_window_with_data
    win._on_add_roi()
    win._roi_list.setCurrentRow(0)
    assert not win._stretch_parallel_spin.isEnabled()
    assert not win._stretch_perpendicular_spin.isEnabled()
    assert not win._shift_parallel_spin.isEnabled()
    assert not win._shift_perpendicular_spin.isEnabled()
    assert not win._reset_fit_btn.isEnabled()


def test_axis_spinboxes_enabled_for_gmm_roi(phasor_window_with_data):
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    assert win._stretch_parallel_spin.isEnabled()
    assert win._stretch_perpendicular_spin.isEnabled()
    assert win._shift_parallel_spin.isEnabled()
    assert win._shift_perpendicular_spin.isEnabled()
    assert win._reset_fit_btn.isEnabled()
    # Spinbox values match the GMMFit defaults
    assert win._stretch_parallel_spin.value() == pytest.approx(2.0)
    assert win._stretch_perpendicular_spin.value() == pytest.approx(2.0)
    assert win._shift_parallel_spin.value() == pytest.approx(0.0)
    assert win._shift_perpendicular_spin.value() == pytest.approx(0.0)


def test_stretch_parallel_grows_only_major_axis_radius(phasor_window_with_data):
    """Independent per-axis stretch — parallel grows major, perpendicular stays."""
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    initial_radii = win._roi_widgets[0].phasor_roi.radii
    win._stretch_parallel_spin.setValue(3.0)
    new_radii = win._roi_widgets[0].phasor_roi.radii
    # parallel: 3.0 / 2.0 = 1.5x growth on the major axis only
    assert new_radii[0] == pytest.approx(initial_radii[0] * 1.5, abs=1e-9)
    assert new_radii[1] == pytest.approx(initial_radii[1], abs=1e-9)


def test_stretch_does_not_move_center(phasor_window_with_data):
    """Bug fix: changing stretch must not shift the ROI center.

    Pre-redesign, _on_cov_f_changed re-applied the shift on top of an
    anchor=phasor_roi.center base, so a non-zero shift caused cov_f
    edits to translate the ROI. With shifts measured against the
    cluster mean, stretch changes affect radii only.
    """
    win = phasor_window_with_data
    geo = _make_geometry(1, mean_g=0.4, mean_s=0.3)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    # Apply a non-zero parallel shift first
    win._shift_parallel_spin.setValue(-1.5)
    center_after_shift = win._roi_widgets[0].phasor_roi.center
    # Now change stretch — center must NOT move
    win._stretch_parallel_spin.setValue(2.1)
    center_after_stretch = win._roi_widgets[0].phasor_roi.center
    assert center_after_stretch[0] == pytest.approx(center_after_shift[0], abs=1e-9)
    assert center_after_stretch[1] == pytest.approx(center_after_shift[1], abs=1e-9)


def test_shift_perpendicular_translates_along_minor_axis(phasor_window_with_data):
    """Perpendicular shift moves center 90° rotated from the major axis."""
    win = phasor_window_with_data
    geo = _make_geometry(1, mean_g=0.4, mean_s=0.3)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    win._shift_perpendicular_spin.setValue(0.5)
    center = win._roi_widgets[0].phasor_roi.center
    # geo principal angle = 15°; perpendicular unit = (-sin 15°, cos 15°)
    angle = np.radians(15.0)
    expected_dx = -0.5 * np.sqrt(geo.lambda_minor) * np.sin(angle)
    expected_dy = +0.5 * np.sqrt(geo.lambda_minor) * np.cos(angle)
    assert center[0] == pytest.approx(geo.mean_g + expected_dx, abs=1e-9)
    assert center[1] == pytest.approx(geo.mean_s + expected_dy, abs=1e-9)


def test_reset_to_fit_resets_all_four_axes(phasor_window_with_data):
    """Reset returns all spinboxes to (stretch=2.0, shift=0)."""
    win = phasor_window_with_data
    geo = _make_geometry(1, mean_g=0.4, mean_s=0.3)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    # Move all four
    win._stretch_parallel_spin.setValue(3.5)
    win._stretch_perpendicular_spin.setValue(1.5)
    win._shift_parallel_spin.setValue(-1.0)
    win._shift_perpendicular_spin.setValue(0.5)
    # Reset
    win._on_reset_fit_clicked()
    fit = win._roi_widgets[0].phasor_roi.gmm_fit
    assert fit.stretch_parallel == pytest.approx(2.0)
    assert fit.stretch_perpendicular == pytest.approx(2.0)
    assert fit.shift_parallel == pytest.approx(0.0)
    assert fit.shift_perpendicular == pytest.approx(0.0)
    assert win._roi_widgets[0].phasor_roi.center[0] == pytest.approx(geo.mean_g, abs=1e-9)
    assert win._roi_widgets[0].phasor_roi.center[1] == pytest.approx(geo.mean_s, abs=1e-9)


def test_gmm_roi_is_non_draggable(phasor_window_with_data):
    """GMM ROIs must be non-translatable with no resize handles."""
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    rect_roi = win._roi_widgets[0].roi
    assert rect_roi.translatable is False
    assert len(rect_roi.handles) == 0


def test_manual_roi_remains_draggable(phasor_window_with_data):
    """Manual ROIs keep the standard drag/resize affordances."""
    win = phasor_window_with_data
    win._on_add_roi()
    rect_roi = win._roi_widgets[0].roi
    assert rect_roi.translatable is True
    assert len(rect_roi.handles) > 0


def test_spinbox_change_does_not_loop_through_roi_moved_widget(
    phasor_window_with_data, monkeypatch
):
    """programmatic RectROI updates are blockSignals-wrapped."""
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)

    spy = MagicMock(side_effect=win._on_roi_moved_widget)
    monkeypatch.setattr(win, "_on_roi_moved_widget", spy)

    win._stretch_parallel_spin.setValue(3.0)
    assert spy.call_count == 0


def test_cluster_center_marker_appears_for_gmm_rois(phasor_window_with_data):
    """A scatter marker is rendered at each GMM ROI's stored cluster mean."""
    win = phasor_window_with_data
    geos = [_make_geometry(1, mean_g=0.3), _make_geometry(2, mean_g=0.5)]
    win.place_gmm_rois(geos, shape="ellipse", criterion=None, sampled_pixels=50_000)
    spots = win._cluster_center_scatter.data
    # ScatterPlotItem stores x/y as a structured array; len() works.
    assert len(spots) == 2


def test_cluster_center_marker_clears_on_remove(phasor_window_with_data):
    win = phasor_window_with_data
    geos = [_make_geometry(1)]
    win.place_gmm_rois(geos, shape="ellipse", criterion=None, sampled_pixels=50_000)
    assert len(win._cluster_center_scatter.data) == 1
    win._roi_list.setCurrentRow(0)
    win._on_remove_roi()
    assert len(win._cluster_center_scatter.data) == 0


def test_cluster_center_marker_skips_invisible_gmm_rois(phasor_window_with_data):
    win = phasor_window_with_data
    geos = [_make_geometry(1)]
    win.place_gmm_rois(geos, shape="ellipse", criterion=None, sampled_pixels=50_000)
    win._roi_list.setCurrentRow(0)
    win._vis_check.setChecked(False)
    assert len(win._cluster_center_scatter.data) == 0


def test_v3_json_round_trip_then_stretch_edit(phasor_window_with_data, tmp_path):
    """Load v3 JSON with a GMM-origin ROI and edit stretch via the spinbox."""
    win = phasor_window_with_data
    geo = _make_geometry(1)
    win.place_gmm_rois([geo], shape="ellipse", criterion="BIC", sampled_pixels=50_000)
    save_path = tmp_path / "rois.json"
    import json
    data = {
        "schema_version": 3,
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

    initial_radii = win._roi_widgets[0].phasor_roi.radii
    win._stretch_parallel_spin.setValue(3.0)
    new_radii = win._roi_widgets[0].phasor_roi.radii
    # parallel only — major axis radius scales 1.5x; minor stays
    assert new_radii[0] == pytest.approx(initial_radii[0] * 1.5, abs=1e-9)
    assert new_radii[1] == pytest.approx(initial_radii[1], abs=1e-9)


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
