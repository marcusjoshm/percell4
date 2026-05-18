"""Tests for the always-visible Session window (U1).

The Session window is the canonical Selector site for
``session.active_channel``, ``session.active_segmentation``, and
``session.active_mask``. It's a wide top-of-screen always-on-top
QMainWindow that subscribes to Session events for re-population and
writes to Session on combo changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qtpy.QtCore import QSettings, Qt

from percell4.application.session import Event, Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.session_window import SessionWindow
from percell4.model import CellDataModel


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Sandbox QSettings so geometry/pin tests don't bleed into the real store."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # On macOS, QSettings uses ~/Library/Preferences. Forcing INI format to
    # tmp_path/.config keeps the test hermetic across platforms.
    from qtpy.QtCore import QCoreApplication
    QCoreApplication.setOrganizationName("LeeLabPerCell4")
    QCoreApplication.setApplicationName("PerCell4")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    # Clear any leftover from previous tests
    QSettings("LeeLabPerCell4", "PerCell4").clear()
    yield tmp_path
    QSettings("LeeLabPerCell4", "PerCell4").clear()


def _make_model(channel_names=None, mask_names=None, seg_names=None,
                 active_channel=None, active_mask=None, active_seg=None,
                 tmp_path=None):
    """Construct a CellDataModel pre-seeded with a fake dataset."""
    channel_names = channel_names or []
    mask_names = mask_names or []
    seg_names = seg_names or []
    path = (tmp_path or Path("/tmp")) / "ds.h5"
    handle = DatasetHandle(
        path=path,
        metadata={
            "channel_names": list(channel_names),
            "mask_names": list(mask_names),
            "segmentation_names": list(seg_names),
        },
    )
    model = CellDataModel()
    model.session._dataset = handle
    if active_channel is not None:
        model.session._active_channel = active_channel
    if active_mask is not None:
        model.session._active_mask = active_mask
    if active_seg is not None:
        model.session._active_segmentation = active_seg
    return model


# ── Population from Session ─────────────────────────────────────────


def test_combos_populate_from_session_metadata_on_construct(qtbot, tmp_path):
    """Channel/mask/segmentation combos read from session.dataset.metadata."""
    model = _make_model(
        channel_names=["mNG", "CA-SiR", "mTQ2"],
        mask_names=["phasor", "SG_mask"],
        seg_names=["cellpose"],
        active_channel="mNG",
        active_mask="phasor",
        active_seg="cellpose",
        tmp_path=tmp_path,
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    chan_items = [win._channel_combo.itemText(i) for i in range(win._channel_combo.count())]
    mask_items = [win._mask_combo.itemText(i) for i in range(win._mask_combo.count())]
    seg_items = [win._seg_combo.itemText(i) for i in range(win._seg_combo.count())]

    assert chan_items == ["mNG", "CA-SiR", "mTQ2"]
    assert mask_items == ["phasor", "SG_mask"]
    assert seg_items == ["cellpose"]


def test_combos_match_session_active_values_on_construct(qtbot, tmp_path):
    """Combo currentText() reflects session.active_* on first paint."""
    model = _make_model(
        channel_names=["A", "B"],
        mask_names=["m1", "m2"],
        seg_names=["s1", "s2"],
        active_channel="B",
        active_mask="m2",
        active_seg="s2",
        tmp_path=tmp_path,
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    assert win._channel_combo.currentText() == "B"
    assert win._mask_combo.currentText() == "m2"
    assert win._seg_combo.currentText() == "s2"


# ── Combo change → Session write ────────────────────────────────────


def test_changing_channel_combo_writes_to_session(qtbot, tmp_path):
    """User picks a new channel → session.active_channel updates, event fires once."""
    model = _make_model(
        channel_names=["mNG", "CA-SiR"], active_channel="mNG", tmp_path=tmp_path
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[None] = []
    model.session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, lambda: events.append(None))

    win._channel_combo.setCurrentText("CA-SiR")
    assert model.session.active_channel == "CA-SiR"
    assert len(events) == 1


def test_changing_mask_combo_writes_to_session(qtbot, tmp_path):
    model = _make_model(
        mask_names=["m1", "m2"], active_mask="m1", tmp_path=tmp_path
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[None] = []
    model.session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append(None))

    win._mask_combo.setCurrentText("m2")
    assert model.session.active_mask == "m2"
    assert len(events) == 1


def test_changing_seg_combo_writes_to_session(qtbot, tmp_path):
    model = _make_model(
        seg_names=["s1", "s2"], active_seg="s1", tmp_path=tmp_path
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[None] = []
    model.session.subscribe(Event.ACTIVE_SEGMENTATION_CHANGED, lambda: events.append(None))

    win._seg_combo.setCurrentText("s2")
    assert model.session.active_segmentation == "s2"
    assert len(events) == 1


# ── Session event → combo refresh (no echo) ─────────────────────────


def test_external_set_active_channel_updates_combo_without_echo(qtbot, tmp_path):
    """Programmatic Session change refreshes combo but does NOT re-fire set_active_*.

    Verifies no echo loop: ACTIVE_CHANNEL_CHANGED fires exactly once for
    the external set call, not twice.
    """
    model = _make_model(
        channel_names=["A", "B"], active_channel="A", tmp_path=tmp_path
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[None] = []
    model.session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, lambda: events.append(None))

    model.session.set_active_channel("B")

    assert win._channel_combo.currentText() == "B"
    assert len(events) == 1  # Only the external change, no echo


def test_external_set_active_mask_updates_combo_without_echo(qtbot, tmp_path):
    model = _make_model(
        mask_names=["m1", "m2"], active_mask="m1", tmp_path=tmp_path
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[None] = []
    model.session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append(None))

    model.session.set_active_mask("m2")

    assert win._mask_combo.currentText() == "m2"
    assert len(events) == 1


# ── Dataset lifecycle ───────────────────────────────────────────────


def test_no_dataset_renders_empty_combos_and_placeholder_header(qtbot):
    """On construction with no dataset, combos are empty and header shows placeholder."""
    model = CellDataModel()  # no dataset
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    assert win._channel_combo.count() == 0
    assert win._mask_combo.count() == 0
    assert win._seg_combo.count() == 0
    # Header shows the placeholder text (no-dataset state)
    assert "no dataset" in win._dataset_label.text().lower()


def test_dataset_changed_repopulates_combos(qtbot, tmp_path):
    """When Session.set_dataset fires DATASET_CHANGED, combos repopulate."""
    model = CellDataModel()
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    handle = DatasetHandle(
        path=tmp_path / "new.h5",
        metadata={
            "channel_names": ["X", "Y"],
            "mask_names": ["mA"],
            "segmentation_names": ["sA"],
        },
    )
    model.session.set_dataset(handle)

    chan_items = [win._channel_combo.itemText(i) for i in range(win._channel_combo.count())]
    assert chan_items == ["X", "Y"]
    # set_dataset auto-selects first of each kind
    assert win._channel_combo.currentText() == "X"
    assert win._mask_combo.currentText() == "mA"
    assert win._seg_combo.currentText() == "sA"
    # Dataset name surfaces in the header
    assert "new" in win._dataset_label.text()


def test_dataset_changed_does_not_re_fire_set_active_during_refresh(qtbot, tmp_path):
    """Repopulating combos must not re-emit ACTIVE_*_CHANGED on top of the
    events Session.set_dataset already fired."""
    model = CellDataModel()
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    handle = DatasetHandle(
        path=tmp_path / "new.h5",
        metadata={
            "channel_names": ["X"],
            "mask_names": ["m"],
            "segmentation_names": ["s"],
        },
    )

    # Subscribe AFTER window construction but BEFORE set_dataset, so we
    # only count events from the set_dataset call.
    channel_events: list[None] = []
    model.session.subscribe(
        Event.ACTIVE_CHANNEL_CHANGED, lambda: channel_events.append(None)
    )

    model.session.set_dataset(handle)

    # set_dataset emits ACTIVE_CHANNEL_CHANGED exactly once (auto-select).
    # The window's combo refresh must not double-fire it.
    assert len(channel_events) == 1


# ── Resource list events ────────────────────────────────────────────


def test_mask_list_changed_refreshes_mask_combo(qtbot, tmp_path):
    """When a Creator adds a mask via refresh_resource_lists, mask combo gains it."""
    model = _make_model(
        mask_names=["existing"], active_mask="existing", tmp_path=tmp_path
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    model.session.refresh_resource_lists(mask_names=["existing", "new_mask"])

    mask_items = [win._mask_combo.itemText(i) for i in range(win._mask_combo.count())]
    assert mask_items == ["existing", "new_mask"]
    # Current selection preserved
    assert win._mask_combo.currentText() == "existing"


def test_channel_list_changed_refreshes_channel_combo(qtbot, tmp_path):
    model = _make_model(
        channel_names=["A"], active_channel="A", tmp_path=tmp_path
    )
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    model.session.refresh_resource_lists(channel_names=["A", "B"])

    chan_items = [win._channel_combo.itemText(i) for i in range(win._channel_combo.count())]
    assert chan_items == ["A", "B"]
    assert win._channel_combo.currentText() == "A"


# ── Pin-on-top toggle ───────────────────────────────────────────────


def test_pin_on_top_defaults_to_true(qtbot, isolated_settings):
    """Fresh window with no saved preference defaults to always-on-top."""
    model = CellDataModel()
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    assert win._pin_check.isChecked() is True
    assert bool(win.windowFlags() & Qt.WindowStaysOnTopHint)


def test_pin_on_top_toggle_off_clears_flag(qtbot, isolated_settings):
    model = CellDataModel()
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    win._pin_check.setChecked(False)
    assert not bool(win.windowFlags() & Qt.WindowStaysOnTopHint)


def test_pin_on_top_toggle_keeps_window_visible(qtbot, isolated_settings):
    """Toggling pin-on-top must NOT hide the window.

    Regression: ``QWidget.setWindowFlag`` hides the widget as a side effect.
    The toggle handler must re-show after flipping the flag so the user
    doesn't lose the window every time they toggle the pin.
    """
    model = CellDataModel()
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    assert win.isVisible()

    # Toggle off
    win._pin_check.setChecked(False)
    assert win.isVisible(), "Window vanished after toggling Pin-on-top OFF"

    # Toggle back on
    win._pin_check.setChecked(True)
    assert win.isVisible(), "Window vanished after toggling Pin-on-top ON"


def test_pin_on_top_state_persists_across_construction(qtbot, isolated_settings):
    """Toggling off and reopening preserves the off state."""
    model = CellDataModel()
    win1 = SessionWindow(data_model=model)
    qtbot.addWidget(win1)
    win1._pin_check.setChecked(False)
    win1.close()

    # New window, same settings store
    model2 = CellDataModel()
    win2 = SessionWindow(data_model=model2)
    qtbot.addWidget(win2)

    assert win2._pin_check.isChecked() is False
    assert not bool(win2.windowFlags() & Qt.WindowStaysOnTopHint)


# ── Geometry persistence ────────────────────────────────────────────


def test_geometry_round_trip_via_close_and_reopen(qtbot, isolated_settings):
    """Closing the window saves geometry; reopening restores it.

    Width chosen to comfortably exceed the natural minimum of the row of
    Selectors so Qt does not clamp.
    """
    model = CellDataModel()
    win1 = SessionWindow(data_model=model)
    qtbot.addWidget(win1)
    win1.setGeometry(150, 50, 1100, 90)
    # Allow geometry to apply
    win1.show()
    qtbot.waitExposed(win1)
    win1.close()

    # New window in the same settings sandbox
    model2 = CellDataModel()
    win2 = SessionWindow(data_model=model2)
    qtbot.addWidget(win2)
    win2.show()
    qtbot.waitExposed(win2)

    g = win2.geometry()
    # restoreGeometry may adjust by 1-2 px on some platforms; use loose equality
    assert abs(g.width() - 1100) <= 5
    assert abs(g.height() - 90) <= 5


# ── No reads of viewer.layers ───────────────────────────────────────


def test_module_does_not_import_viewer_layers(qtbot):
    """SessionWindow must not read napari layers — Session is canonical."""
    import inspect

    source = inspect.getsource(SessionWindow)
    assert "viewer.layers" not in source
    assert "napari" not in source.lower()


# ── View-bin SpinBox (U9) ───────────────────────────────────────────


def test_bin_spin_defaults_to_one_on_construct(qtbot, tmp_path):
    """SpinBox displays 1 (session.active_bin default) at startup."""
    model = _make_model(channel_names=["A"], tmp_path=tmp_path)
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)
    assert win._bin_spin.value() == 1


def test_bin_spin_range_is_one_to_sixteen(qtbot, tmp_path):
    model = _make_model(channel_names=["A"], tmp_path=tmp_path)
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)
    assert win._bin_spin.minimum() == 1
    assert win._bin_spin.maximum() == 16


def test_bin_spin_value_writes_to_session(qtbot, tmp_path):
    """User changes spinner → session.active_bin updates, event fires once."""
    model = _make_model(channel_names=["A"], tmp_path=tmp_path)
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[int] = []
    model.session.subscribe(Event.ACTIVE_BIN_CHANGED, lambda: events.append(1))

    win._bin_spin.setValue(3)
    assert model.session.active_bin == 3
    assert events == [1]


def test_session_set_active_bin_pushes_into_spinner(qtbot, tmp_path):
    """External mutation (Session.set_active_bin) syncs the SpinBox display."""
    model = _make_model(channel_names=["A"], tmp_path=tmp_path)
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    model.session.set_active_bin(5)
    assert win._bin_spin.value() == 5


def test_session_set_active_bin_does_not_echo_back(qtbot, tmp_path):
    """The session-driven push uses the loading guard so Session writes
    do not bounce back through valueChanged into another set_active_bin."""
    model = _make_model(channel_names=["A"], tmp_path=tmp_path)
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[int] = []
    model.session.subscribe(Event.ACTIVE_BIN_CHANGED, lambda: events.append(1))

    model.session.set_active_bin(7)
    # Exactly ONE event -- the one fired by set_active_bin itself.
    # An echo through the SpinBox's valueChanged would fire a second.
    assert events == [1]


def test_dataset_switch_resets_bin_spin_to_one(qtbot, tmp_path):
    """Dataset switch resets session.active_bin to 1; SpinBox follows."""
    model = _make_model(channel_names=["A"], tmp_path=tmp_path)
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    model.session.set_active_bin(3)
    assert win._bin_spin.value() == 3

    new_handle = DatasetHandle(
        path=tmp_path / "new.h5",
        metadata={"channel_names": ["B"]},
    )
    model.session.set_dataset(new_handle)
    assert model.session.active_bin == 1
    assert win._bin_spin.value() == 1


def test_bin_spin_idempotent_set_no_event_storm(qtbot, tmp_path):
    """Calling setValue with the current value fires no Session event."""
    model = _make_model(channel_names=["A"], tmp_path=tmp_path)
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)

    events: list[int] = []
    model.session.subscribe(Event.ACTIVE_BIN_CHANGED, lambda: events.append(1))

    win._bin_spin.setValue(1)  # same as current
    assert events == []
