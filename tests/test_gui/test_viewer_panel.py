"""Tests for the Viewer task panel (U1, U2): Open/Hide Viewer + Cell Filter."""

from __future__ import annotations

from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.viewer_panel import ViewerPanel


class _Signal:
    def __init__(self) -> None:
        self._subs: list = []

    def connect(self, fn) -> None:
        self._subs.append(fn)

    def emit(self, change) -> None:
        for fn in self._subs:
            fn(change)


class _Change:
    def __init__(self, *, filter=False, data=False, channel=False) -> None:
        self.filter = filter
        self.data = data
        self.channel = channel


class _FakeModel:
    def __init__(self) -> None:
        self.state_changed = _Signal()
        self.selection_calls: list = []
        self.filter_calls: list = []
        self.selected_ids: set = set()
        self.is_filtered = False
        self.filtered_df: list = []
        self.df: list = []

    def set_selection(self, ids) -> None:
        self.selection_calls.append(list(ids))

    def set_filter(self, ids) -> None:
        self.filter_calls.append(ids)


class _FakeViewer:
    def __init__(self) -> None:
        self.hidden = 0

    def hide(self) -> None:
        self.hidden += 1


def _button(panel: ViewerPanel, text: str) -> QPushButton:
    return next(b for b in panel.findChildren(QPushButton) if b.text() == text)


def _make(model=None, viewer=None, shown=None, status=None) -> ViewerPanel:
    return ViewerPanel(
        model or _FakeModel(),
        show_window=(lambda key: shown.append(key)) if shown is not None else (lambda key: None),
        get_viewer_window=lambda: viewer,
        show_status=(lambda msg: status.append(msg)) if status is not None else (lambda _: None),
    )


# ── U1: Open / Hide Viewer ──────────────────────────────────


def test_open_viewer_button_shows_viewer(qtbot) -> None:
    shown: list[str] = []
    panel = _make(shown=shown)
    qtbot.addWidget(panel)
    _button(panel, "Open Viewer").click()
    assert shown == ["viewer"]


def test_hide_viewer_button_hides_existing_viewer(qtbot) -> None:
    viewer = _FakeViewer()
    panel = _make(viewer=viewer)
    qtbot.addWidget(panel)
    _button(panel, "Hide Viewer").click()
    assert viewer.hidden == 1


def test_hide_viewer_no_op_when_no_viewer(qtbot) -> None:
    panel = _make(viewer=None)
    qtbot.addWidget(panel)
    _button(panel, "Hide Viewer").click()  # must not raise


# ── U2: Cell Filter (relocated Selector) ────────────────────


def test_clear_selection_writes_empty_selection(qtbot) -> None:
    model = _FakeModel()
    panel = _make(model=model)
    qtbot.addWidget(panel)
    _button(panel, "Clear Selection").click()
    assert model.selection_calls == [[]]


def test_filter_to_selection_writes_filter(qtbot) -> None:
    model = _FakeModel()
    model.selected_ids = {1, 2, 3}
    panel = _make(model=model)
    qtbot.addWidget(panel)
    _button(panel, "Filter to Selection").click()
    assert len(model.filter_calls) == 1
    assert set(model.filter_calls[0]) == {1, 2, 3}


def test_filter_to_selection_empty_shows_status_no_filter(qtbot) -> None:
    model = _FakeModel()  # no selection
    status: list[str] = []
    panel = _make(model=model, status=status)
    qtbot.addWidget(panel)
    _button(panel, "Filter to Selection").click()
    assert model.filter_calls == []
    assert any("No cells selected" in s for s in status)


def test_clear_filter_writes_none(qtbot) -> None:
    model = _FakeModel()
    panel = _make(model=model)
    qtbot.addWidget(panel)
    # Clear Filter is only enabled while a filter is active.
    model.is_filtered = True
    model.filtered_df = [0, 1]
    model.df = [0] * 5
    model.state_changed.emit(_Change(filter=True))
    _button(panel, "Clear Filter").click()
    assert model.filter_calls == [None]


def test_filter_state_change_updates_label_and_button(qtbot) -> None:
    """A FILTER_CHANGED event updates the count label and enables Clear Filter."""
    model = _FakeModel()
    panel = _make(model=model)
    qtbot.addWidget(panel)

    clear_btn = _button(panel, "Clear Filter")
    assert clear_btn.isEnabled() is False

    model.is_filtered = True
    model.filtered_df = [0, 1, 2]
    model.df = [0] * 10
    model.state_changed.emit(_Change(filter=True))

    assert clear_btn.isEnabled() is True
    assert panel._filter_status_label.text() == "Showing 3 of 10 cells"

    model.is_filtered = False
    model.state_changed.emit(_Change(filter=True))
    assert clear_btn.isEnabled() is False
    assert panel._filter_status_label.text() == "No filter active"
