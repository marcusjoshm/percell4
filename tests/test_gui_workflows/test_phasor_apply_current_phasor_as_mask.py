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

from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow

# Shared fixtures (`session_with_dataset`, `phasor_window`) and helpers
# (`_wide_phasor_maps`, `_add_wide_roi`) live in
# `tests/test_gui_workflows/conftest.py`.
from .conftest import _add_wide_roi, _wide_phasor_maps  # noqa: F401


def _accept_default_name(monkeypatch) -> None:
    """Patch QInputDialog.getText to OK whatever default it received."""
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(
            lambda *args, **kwargs: (
                kwargs.get("text", args[3] if len(args) > 3 else ""),
                True,
            )
        ),
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
    # The fixture leaves session.active_channel unset, so the default
    # falls back to phasor_<N>. With "nucleus" registered as the only
    # existing mask name, the next free integer is 1.
    assert seen_defaults[0] == "phasor_1"
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


# ── Launcher integration (U3) ────────────────────────────────────────


@pytest.fixture
def launcher_store(tmp_path):
    """Create a real DatasetStore backing a fresh .h5 file."""
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_path / "launcher.h5")
    store.create(metadata={"source": "test"})
    return store


class _StubViewer:
    """Minimal stub of ViewerWindow for launcher integration tests."""

    def __init__(self, alive: bool = True):
        self._alive = alive
        self.add_mask_calls: list[tuple[np.ndarray, str]] = []

    def _is_alive(self) -> bool:
        return self._alive

    def add_mask(self, binary, name, **kwargs):
        # Capture exactly the payload the launcher sent so the test
        # can assert (binary, name) parity with the per-ROI flow.
        self.add_mask_calls.append((binary, name))

    def close(self) -> None:  # pragma: no cover - cleanup only
        """Required by LauncherWindow.closeEvent which closes managed windows."""
        pass


@pytest.fixture
def launcher(qtbot, launcher_store, phasor_window):
    """Build a real LauncherWindow connected to a real DatasetStore.

    The phasor window is registered in ``_windows`` so that the
    launcher slot can read filter-state attributes back from it
    (Option A wiring per U3 plan).
    """
    from percell4.application.session import Session
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    sess = Session()
    sess._dataset = phasor_window._session._dataset  # share dataset metadata
    model = CellDataModel(session=sess)
    win = LauncherWindow(model)
    qtbot.addWidget(win)
    win._current_store = launcher_store
    win._windows["phasor_plot"] = phasor_window
    # Connect the new signal manually — _show_window normally does this
    # but we are not exercising the show path.
    phasor_window.phasor_mask_applied.connect(
        win._on_phasor_current_mask_applied
    )
    return win


def _binary_8(value: int = 1) -> np.ndarray:
    arr = np.zeros((8, 8), dtype=np.uint8)
    arr[2:5, 2:5] = value
    return arr


def test_launcher_persists_mask_and_sets_active(launcher, launcher_store):
    """Happy path: HDF5 entry exists, list_masks reflects it, active_mask set."""
    binary = _binary_8()

    state_changes: list = []
    launcher.data_model.state_changed.connect(state_changes.append)

    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", binary))

    assert "phasor_NADH_1" in launcher_store.list_masks()
    np.testing.assert_array_equal(
        launcher_store.read_mask("phasor_NADH_1"), binary
    )
    assert launcher.data_model.session.active_mask == "phasor_NADH_1"
    flags = {
        flag
        for change in state_changes
        for flag in ("mask", "mask_list")
        if getattr(change, flag, False)
    }
    assert {"mask", "mask_list"} <= flags


def test_launcher_adds_napari_layer_when_viewer_alive(launcher, launcher_store):
    """A live viewer receives an add_mask call with (binary, name)."""
    stub = _StubViewer(alive=True)
    launcher._windows["viewer"] = stub
    binary = _binary_8()

    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", binary))

    assert len(stub.add_mask_calls) == 1
    captured_binary, captured_name = stub.add_mask_calls[0]
    np.testing.assert_array_equal(captured_binary, binary)
    assert captured_name == "phasor_NADH_1"


def test_launcher_skips_layer_when_no_viewer(launcher, launcher_store):
    """No viewer attached → write proceeds, layer step is skipped."""
    binary = _binary_8()
    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", binary))

    assert "phasor_NADH_1" in launcher_store.list_masks()
    assert launcher.data_model.session.active_mask == "phasor_NADH_1"


def test_launcher_skips_layer_when_viewer_dead(launcher, launcher_store):
    """Viewer present but ``_is_alive`` False → no add_mask call."""
    stub = _StubViewer(alive=False)
    launcher._windows["viewer"] = stub

    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", _binary_8()))

    assert stub.add_mask_calls == []
    assert "phasor_NADH_1" in launcher_store.list_masks()


def test_launcher_returns_silently_when_no_store(launcher):
    """``_current_store is None`` → no write, no active_mask change."""
    launcher._current_store = None
    initial_active = launcher.data_model.session.active_mask

    # Must not raise.
    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", _binary_8()))

    assert launcher.data_model.session.active_mask == initial_active


def test_launcher_two_consecutive_saves(launcher, launcher_store):
    """Both masks land in HDF5; the second becomes active."""
    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", _binary_8(1)))
    launcher._on_phasor_current_mask_applied(("phasor_NADH_2", _binary_8(1)))

    masks = launcher_store.list_masks()
    assert "phasor_NADH_1" in masks
    assert "phasor_NADH_2" in masks
    assert launcher.data_model.session.active_mask == "phasor_NADH_2"


def test_launcher_persists_filter_state_attributes(
    launcher, launcher_store, phasor_window,
):
    """/masks/<name> carries every documented provenance attribute."""
    import h5py

    phasor_window._intensity_threshold = 42.5
    phasor_window._ref_circle_center = (0.5, 0.25)
    phasor_window._ref_circle_radius = 0.3
    phasor_window._cleared_mask = np.zeros((16, 16), dtype=bool)
    phasor_window._cleared_mask[0:3, 0:3] = True
    phasor_window._session.set_active_channel("NADH")
    phasor_window._session.set_active_mask("nucleus")

    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", _binary_8()))

    with h5py.File(launcher_store.path, "r") as f:
        attrs = dict(f["masks/phasor_NADH_1"].attrs)

    assert attrs["phasor_intensity_threshold"] == pytest.approx(42.5)
    assert attrs["phasor_ref_circle_center_g"] == pytest.approx(0.5)
    assert attrs["phasor_ref_circle_center_s"] == pytest.approx(0.25)
    assert attrs["phasor_ref_circle_radius"] == pytest.approx(0.3)
    assert attrs["phasor_active_mask_at_capture"] == "nucleus"
    assert attrs["phasor_cleared_pixel_count"] == 9
    assert attrs["phasor_active_channel"] == "NADH"
    assert "phasor_capture_iso8601" in attrs
    iso = attrs["phasor_capture_iso8601"]
    if isinstance(iso, bytes):
        iso = iso.decode()
    assert iso.endswith("Z")


def test_launcher_filter_state_distinct_across_saves(
    launcher, launcher_store, phasor_window,
):
    """Two saves at different thresholds yield distinguishable attrs."""
    import h5py

    phasor_window._intensity_threshold = 10.0
    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", _binary_8()))

    phasor_window._intensity_threshold = 200.0
    launcher._on_phasor_current_mask_applied(("phasor_NADH_2", _binary_8()))

    with h5py.File(launcher_store.path, "r") as f:
        a1 = dict(f["masks/phasor_NADH_1"].attrs)
        a2 = dict(f["masks/phasor_NADH_2"].attrs)

    assert a1["phasor_intensity_threshold"] == pytest.approx(10.0)
    assert a2["phasor_intensity_threshold"] == pytest.approx(200.0)


def test_launcher_filter_state_sentinels_when_unset(
    launcher, launcher_store, phasor_window,
):
    """Unset ref-circle / channel / mask use documented sentinels."""
    import h5py

    phasor_window._intensity_threshold = 0.0
    phasor_window._ref_circle_center = None
    phasor_window._ref_circle_radius = None
    phasor_window._cleared_mask = None
    phasor_window._session.set_active_channel(None)
    phasor_window._session.set_active_mask(None)

    launcher._on_phasor_current_mask_applied(("phasor_1", _binary_8()))

    with h5py.File(launcher_store.path, "r") as f:
        attrs = dict(f["masks/phasor_1"].attrs)

    assert attrs["phasor_intensity_threshold"] == pytest.approx(0.0)
    assert attrs["phasor_ref_circle_center_g"] == pytest.approx(0.0)
    assert attrs["phasor_ref_circle_center_s"] == pytest.approx(0.0)
    assert attrs["phasor_ref_circle_radius"] == pytest.approx(-1.0)
    assert attrs["phasor_active_mask_at_capture"] == ""
    assert attrs["phasor_cleared_pixel_count"] == 0
    assert attrs["phasor_active_channel"] == ""


def test_launcher_status_bar_message(launcher, launcher_store):
    """Status bar shows the auto-select notification on save."""
    messages: list[tuple[str, int]] = []
    real_status_bar = launcher.statusBar()

    def fake_show(msg, timeout=0):
        messages.append((msg, timeout))

    real_status_bar.showMessage = fake_show  # type: ignore[assignment]

    launcher._on_phasor_current_mask_applied(("phasor_NADH_1", _binary_8()))

    assert messages, "expected at least one status-bar message"
    text, _timeout = messages[-1]
    assert "phasor_NADH_1" in text
    assert "now active" in text


def test_cancel_does_not_invoke_launcher(
    launcher, launcher_store, phasor_window, monkeypatch,
):
    """Phasor cancel → signal not emitted → launcher slot not invoked.

    End-to-end assertion that the cancel path on the dialog never
    reaches the HDF5 write or active_mask transition.
    """
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: ("", False)),
    )

    initial_active = launcher.data_model.session.active_mask
    initial_masks = list(launcher_store.list_masks())

    phasor_window._on_apply_current_phasor_as_mask()

    assert launcher_store.list_masks() == initial_masks
    assert launcher.data_model.session.active_mask == initial_active
