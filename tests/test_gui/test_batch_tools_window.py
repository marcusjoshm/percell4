"""Tests for BatchToolsWindow (U1): hide-on-close host for the batch console."""

from __future__ import annotations

from qtpy.QtCore import QByteArray

from percell4.interfaces.gui.peer_views import batch_tools_window as btw_mod
from percell4.interfaces.gui.peer_views.batch_tools_window import BatchToolsWindow
from percell4.interfaces.gui.task_panels.batch_console_panel import BatchConsolePanel


class _FakeSettings:
    """In-memory stand-in for the settings store, isolated per test."""

    def __init__(self, store: dict) -> None:
        self._store = store

    def setValue(self, key: str, value) -> None:
        self._store[key] = value

    def value(self, key: str):
        return self._store.get(key)


def _install_fake_settings(monkeypatch) -> dict:
    store: dict = {}
    monkeypatch.setattr(btw_mod, "app_settings", lambda: _FakeSettings(store))
    return store


# ── Hosting ─────────────────────────────────────────────────────────


def test_hosts_batch_console_panel(qtbot) -> None:
    win = BatchToolsWindow(get_open_h5_path=lambda: "/data/open.h5")
    qtbot.addWidget(win)
    assert isinstance(win.centralWidget(), BatchConsolePanel)
    assert win.panel is win.centralWidget()
    # Injected getter reaches the hosted panel.
    assert win.panel._get_open_h5_path() == "/data/open.h5"
    assert win.windowTitle() == "PerCell4 — Batch Tools"


def test_injected_reload_and_status_reach_panel(qtbot) -> None:
    reloaded: list[int] = []
    status: list[str] = []
    win = BatchToolsWindow(
        reload_open_dataset=lambda: reloaded.append(1),
        show_status=lambda msg: status.append(msg),
    )
    qtbot.addWidget(win)
    win.panel._reload_open_dataset()
    win.panel._show_status("hi")
    assert reloaded == [1]
    assert status == ["hi"]


# ── Hide-on-close ───────────────────────────────────────────────────


def test_close_hides_not_destroys(qtbot) -> None:
    win = BatchToolsWindow()
    qtbot.addWidget(win)
    win.show()
    assert win.isVisible()
    win.close()
    # closeEvent hides + ignores → window is hidden but still alive.
    assert not win.isVisible()
    assert win.windowTitle() == "PerCell4 — Batch Tools"  # not destroyed


# ── Geometry persistence ────────────────────────────────────────────


def test_close_saves_geometry(qtbot, monkeypatch) -> None:
    store = _install_fake_settings(monkeypatch)
    win = BatchToolsWindow()
    qtbot.addWidget(win)
    win.resize(900, 640)
    win.close()
    assert "batch_tools/geometry" in store
    assert isinstance(store["batch_tools/geometry"], QByteArray)


def test_restore_applies_saved_geometry_when_present(qtbot, monkeypatch) -> None:
    store = {"batch_tools/geometry": QByteArray(b"seed")}
    monkeypatch.setattr(btw_mod, "app_settings", lambda: _FakeSettings(store))
    calls: list = []
    monkeypatch.setattr(
        BatchToolsWindow,
        "restoreGeometry",
        lambda self, data: calls.append(data) or True,
    )
    win = BatchToolsWindow()
    qtbot.addWidget(win)
    assert calls == [store["batch_tools/geometry"]]


def test_restore_noop_when_absent(qtbot, monkeypatch) -> None:
    _install_fake_settings(monkeypatch)  # empty store
    calls: list = []
    monkeypatch.setattr(
        BatchToolsWindow,
        "restoreGeometry",
        lambda self, data: calls.append(data) or True,
    )
    win = BatchToolsWindow()
    qtbot.addWidget(win)
    assert calls == []  # nothing stored → restoreGeometry never called
