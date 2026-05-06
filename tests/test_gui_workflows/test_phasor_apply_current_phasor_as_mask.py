"""Apply Current Phasor as Mask — capture the filter intersection (no ROIs).

The new button writes a single binary mask of every pixel currently
passing the phasor filters. ROIs are ignored — this is "literally what
the histogram shows" as a binary mask. The signal payload is a
``(name: str, binary: np.ndarray uint8 2D)`` tuple; the launcher
subscriber (lands in U3) writes /masks/<name> and auto-selects it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from qtpy.QtWidgets import QInputDialog, QMessageBox

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import (
    PhasorPlotWindow,
    PhasorROI,
)


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    sess = Session()
    sess._dataset = DatasetHandle(path=tmp_path / "fake.h5", metadata={})
    return sess


def _wide_phasor_maps(shape=(16, 16)) -> tuple[np.ndarray, np.ndarray]:
    """Build deterministic g/s maps that span a wide phasor region."""
    h, w = shape
    gs = np.linspace(0.05, 0.95, h * w, dtype=np.float32).reshape(shape)
    ss = np.linspace(0.05, 0.45, h * w, dtype=np.float32).reshape(shape)
    return gs, ss


def _add_wide_roi(window: PhasorPlotWindow, name: str = "ROI_test") -> None:
    """Place a single ROI big enough to enclose the entire phasor cloud."""
    roi = PhasorROI(
        name=name,
        center=(0.5, 0.25),
        radii=(0.6, 0.4),
        angle_deg=0,
        label=1,
        color="#ff00ff",
    )
    window._create_roi_widget(roi)


@pytest.fixture
def phasor_window(qtbot, session_with_dataset) -> PhasorPlotWindow:
    repo = MagicMock()
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)
    g, s = _wide_phasor_maps()
    win._g_map = g
    win._s_map = s
    win._total_valid_pixels = int((np.isfinite(g) & (g != 0)).sum())
    # Phasor data was patched in directly (matching the existing test
    # fixture), so push the gate update by hand. set_phasor_data normally
    # does this for us.
    win._refresh_apply_buttons_enabled()
    return win


def _accept_default_name(monkeypatch) -> None:
    """Patch QInputDialog.getText to OK whatever default it received."""
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: (kwargs.get("text", args[3] if len(args) > 3 else ""), True)),
    )


def _capture_phasor_apply(window: PhasorPlotWindow):
    """Capture the most recent phasor_mask_applied payload."""
    captured: list[tuple[str, np.ndarray]] = []
    window.phasor_mask_applied.connect(captured.append)
    window._on_apply_current_phasor_as_mask()
    return captured


# ── Structural-equality regression guard ─────────────────────────────


def test_apply_current_phasor_equals_visible_predicate(
    phasor_window, session_with_dataset, monkeypatch,
):
    """The emitted binary equals _compute_visible_valid_2d().astype(uint8).

    Parallel to test_apply_equals_napari_preview from the per-ROI test
    suite — the structural-equality guard against the "Apply diverged
    from preview" bug class documented in
    docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md.
    The new button must call _compute_visible_valid_2d verbatim, never
    recompute the predicate inline.
    """
    sparse = np.zeros((16, 16), dtype=np.uint8)
    sparse[4:12, 4:12] = 1
    session_with_dataset.set_active_mask("nucleus")
    phasor_window._mask_filter_check.setChecked(True)
    phasor_window._active_mask_array = sparse
    phasor_window._active_mask_flat = sparse.ravel()

    intensity = np.full((16, 16), 100.0, dtype=np.float32)
    intensity[:, :4] = 0.0
    phasor_window._intensity = intensity
    phasor_window._intensity_threshold = 50.0

    phasor_window._ref_circle_center = (0.5, 0.25)
    phasor_window._ref_circle_radius = 0.4

    expected = phasor_window._compute_visible_valid_2d().astype(np.uint8)

    _accept_default_name(monkeypatch)
    captured = _capture_phasor_apply(phasor_window)

    assert captured, "phasor_mask_applied was never emitted"
    name, binary = captured[-1]
    assert isinstance(name, str)
    np.testing.assert_array_equal(binary, expected)


# ── Default-name template ────────────────────────────────────────────


def test_default_name_with_active_channel_no_existing(
    phasor_window, session_with_dataset,
):
    """phasor_<channel>_1 when no masks exist."""
    session_with_dataset.set_active_channel("NADH")
    assert phasor_window._default_phasor_mask_name() == "phasor_NADH_1"


def test_default_name_increments_on_collision(
    phasor_window, session_with_dataset,
):
    """phasor_<channel>_<N> picks the smallest non-colliding N."""
    session_with_dataset.set_active_channel("NADH")
    session_with_dataset.dataset.metadata["mask_names"] = [
        "phasor_NADH_1", "phasor_NADH_2"
    ]
    assert phasor_window._default_phasor_mask_name() == "phasor_NADH_3"


def test_default_name_falls_back_when_active_channel_is_none(
    phasor_window, session_with_dataset,
):
    """No channel ⇒ phasor_<N>, no 'unknown' placeholder."""
    session_with_dataset.set_active_channel(None)
    assert phasor_window._default_phasor_mask_name() == "phasor_1"


def test_default_name_falls_back_when_active_channel_is_empty(
    phasor_window, session_with_dataset,
):
    """Empty-string channel is also falsy ⇒ phasor_<N>."""
    session_with_dataset._active_channel = ""
    assert phasor_window._default_phasor_mask_name() == "phasor_1"


# ── Filters-only-no-ROIs (ROIs present but ignored) ──────────────────


def test_rois_do_not_contribute_to_emitted_mask(
    phasor_window, session_with_dataset, monkeypatch,
):
    """Drawn ROIs are layered on top and must not affect the output.

    The captured pixels equal the visibility predicate regardless of
    whether ROIs are present.
    """
    _accept_default_name(monkeypatch)

    captured_no_roi = _capture_phasor_apply(phasor_window)
    assert captured_no_roi
    _, binary_no_roi = captured_no_roi[-1]

    # Add a small ROI; should not change the result.
    _add_wide_roi(phasor_window, name="distractor")
    captured_with_roi = _capture_phasor_apply(phasor_window)
    _, binary_with_roi = captured_with_roi[-1]

    np.testing.assert_array_equal(binary_no_roi, binary_with_roi)


# ── Per-filter respect ───────────────────────────────────────────────


def test_apply_respects_intensity_threshold(phasor_window, monkeypatch):
    intensity = np.zeros((16, 16), dtype=np.float32)
    intensity[:, 8:] = 100.0
    phasor_window._intensity = intensity
    phasor_window._intensity_threshold = 50.0

    _accept_default_name(monkeypatch)
    captured = _capture_phasor_apply(phasor_window)
    _, binary = captured[-1]

    assert np.all(binary[intensity < 50.0] == 0)
    assert binary[intensity >= 50.0].sum() > 0


def test_apply_respects_reference_circle(phasor_window, monkeypatch):
    phasor_window._ref_circle_center = (0.5, 0.25)
    phasor_window._ref_circle_radius = 0.1

    _accept_default_name(monkeypatch)
    captured = _capture_phasor_apply(phasor_window)
    _, binary = captured[-1]

    g = phasor_window._g_map
    s = phasor_window._s_map
    inside = (g - 0.5) ** 2 + (s - 0.25) ** 2 <= 0.1 ** 2
    assert np.all(binary[~inside] == 0)


def test_apply_respects_filter_ids(phasor_window, session_with_dataset, monkeypatch):
    labels = np.zeros((16, 16), dtype=np.int32)
    labels[:8, :] = 1
    labels[8:, :] = 2
    phasor_window._labels = labels
    phasor_window._labels_flat = labels.ravel()

    session_with_dataset.set_filter({1})

    _accept_default_name(monkeypatch)
    captured = _capture_phasor_apply(phasor_window)
    _, binary = captured[-1]

    assert np.all(binary[labels == 2] == 0)
    assert binary[labels == 1].sum() > 0


def test_apply_respects_active_mask_filter(
    phasor_window, session_with_dataset, monkeypatch,
):
    sparse = np.zeros((16, 16), dtype=np.uint8)
    sparse[2:6, 2:6] = 1
    session_with_dataset.set_active_mask("nucleus")
    phasor_window._mask_filter_check.setChecked(True)
    phasor_window._active_mask_array = sparse
    phasor_window._active_mask_flat = sparse.ravel()

    _accept_default_name(monkeypatch)
    captured = _capture_phasor_apply(phasor_window)
    _, binary = captured[-1]

    assert np.all(binary[sparse == 0] == 0)
    assert binary.sum() > 0


def test_apply_respects_cleared_mask(phasor_window, monkeypatch):
    """Pixels in _cleared_mask must be 0 in the emitted binary."""
    cleared = np.zeros((16, 16), dtype=bool)
    cleared[6:10, 6:10] = True
    phasor_window._cleared_mask = cleared

    _accept_default_name(monkeypatch)
    captured = _capture_phasor_apply(phasor_window)
    _, binary = captured[-1]

    assert np.all(binary[cleared] == 0)
    # Pixels outside the cleared region survive.
    assert binary[~cleared].sum() > 0


# ── Cancel / empty-name / collision / empty-mask paths ───────────────


def test_cancel_emits_nothing(phasor_window, monkeypatch):
    """Cancel from the name dialog → no signal."""
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: ("", False)),
    )
    captured = _capture_phasor_apply(phasor_window)
    assert captured == []


def test_empty_name_then_cancel_emits_nothing(phasor_window, monkeypatch):
    """Submit blank, then cancel — no signal, no exception."""
    sequence = iter([("   ", True), ("", False)])
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: next(sequence)),
    )
    captured = _capture_phasor_apply(phasor_window)
    assert captured == []


def test_empty_name_reprompts_with_original_default(
    phasor_window, session_with_dataset, monkeypatch,
):
    """Submitting blank re-prompts with the *original* computed default."""
    session_with_dataset.set_active_channel("NADH")

    seen_defaults: list[str] = []

    def fake_get_text(parent, title, label, *args, **kwargs):
        text = kwargs.get("text")
        if text is None and len(args) >= 1:
            text = args[0]
        seen_defaults.append(text)
        if len(seen_defaults) == 1:
            return ("   ", True)  # blank, OK → must re-prompt
        return ("phasor_NADH_1", True)  # accept the default

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fake_get_text))
    captured = _capture_phasor_apply(phasor_window)

    assert captured, "expected emission after the second prompt"
    assert seen_defaults == ["phasor_NADH_1", "phasor_NADH_1"], (
        "blank submission must re-prompt with the original default"
    )


def test_collision_warns_and_reprompts_with_typed_name(
    phasor_window, session_with_dataset, monkeypatch,
):
    """Typing an existing name → warning + re-prompt with typed name pre-filled."""
    session_with_dataset.dataset.metadata["mask_names"] = ["nucleus"]

    seen_defaults: list[str] = []

    def fake_get_text(parent, title, label, *args, **kwargs):
        text = kwargs.get("text")
        if text is None and len(args) >= 1:
            text = args[0]
        seen_defaults.append(text)
        if len(seen_defaults) == 1:
            return ("nucleus", True)  # collision
        return ("phasor_X", True)  # accept second prompt

    warned: list[bool] = []
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fake_get_text))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: warned.append(True)),
    )

    captured = _capture_phasor_apply(phasor_window)

    assert warned == [True], "collision must show a warning"
    assert seen_defaults[0] == phasor_window._default_phasor_mask_name() or seen_defaults[0] == "phasor_1"
    assert seen_defaults[1] == "nucleus", (
        "collision re-prompt must pre-fill the typed name"
    )
    assert captured
    name, _binary = captured[-1]
    assert name == "phasor_X"


def test_collision_then_cancel_emits_nothing(
    phasor_window, session_with_dataset, monkeypatch,
):
    """Collision warning + Cancel from the second prompt → no signal."""
    session_with_dataset.dataset.metadata["mask_names"] = ["nucleus"]

    sequence = iter([("nucleus", True), ("", False)])
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: next(sequence)),
    )
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    captured = _capture_phasor_apply(phasor_window)
    assert captured == []


def test_empty_mask_no_emits_nothing(phasor_window, monkeypatch):
    """When the AND of filters is empty and user clicks No → no signal."""
    # Force an empty visible predicate via a degenerate ref-circle.
    phasor_window._ref_circle_center = (0.5, 0.25)
    phasor_window._ref_circle_radius = 0.0

    _accept_default_name(monkeypatch)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
    )

    captured = _capture_phasor_apply(phasor_window)
    assert captured == []


def test_empty_mask_yes_emits_zero_binary(phasor_window, monkeypatch):
    """When the user confirms Yes on the empty-mask dialog, emit an all-zero binary."""
    phasor_window._ref_circle_center = (0.5, 0.25)
    phasor_window._ref_circle_radius = 0.0

    _accept_default_name(monkeypatch)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.Yes),
    )

    captured = _capture_phasor_apply(phasor_window)
    assert captured
    _, binary = captured[-1]
    assert binary.sum() == 0
    assert binary.shape == phasor_window._g_map.shape


# ── Disabled-when-empty gate ─────────────────────────────────────────


def test_both_buttons_disabled_at_construction(qtbot, session_with_dataset):
    """Fresh window with no phasor data → both apply buttons disabled."""
    repo = MagicMock()
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)

    assert win._g_map is None
    assert not win._btn_apply_rois.isEnabled()
    assert not win._btn_apply_current_phasor.isEnabled()


def test_both_buttons_enable_together_on_set_phasor_data(
    qtbot, session_with_dataset,
):
    """set_phasor_data with valid arrays → both buttons enabled in one call."""
    repo = MagicMock()
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)
    assert not win._btn_apply_rois.isEnabled()
    assert not win._btn_apply_current_phasor.isEnabled()

    g, s = _wide_phasor_maps()
    win.set_phasor_data(g, s)

    assert win._btn_apply_rois.isEnabled()
    assert win._btn_apply_current_phasor.isEnabled()


def test_both_buttons_disable_together_on_clear_phasor_display(
    qtbot, session_with_dataset,
):
    """The clear-data path returns both buttons to disabled."""
    repo = MagicMock()
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)
    g, s = _wide_phasor_maps()
    win.set_phasor_data(g, s)
    assert win._btn_apply_rois.isEnabled()
    assert win._btn_apply_current_phasor.isEnabled()

    win._clear_phasor_display()

    assert not win._btn_apply_rois.isEnabled()
    assert not win._btn_apply_current_phasor.isEnabled()


# ── Signal payload shape ─────────────────────────────────────────────


def test_signal_payload_shape(phasor_window, monkeypatch):
    """Payload is (str, np.ndarray uint8 2D, shape == _g_map.shape)."""
    _accept_default_name(monkeypatch)
    captured = _capture_phasor_apply(phasor_window)

    assert captured
    payload = captured[-1]
    assert isinstance(payload, tuple)
    assert len(payload) == 2
    name, binary = payload
    assert isinstance(name, str)
    assert isinstance(binary, np.ndarray)
    assert binary.dtype == np.uint8
    assert binary.ndim == 2
    assert binary.shape == phasor_window._g_map.shape
