"""Tests for the merged Analyses & Workflows tab wiring (U8)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from qtpy.QtWidgets import QDialog, QGroupBox, QLabel, QPushButton

from percell4.application.analysis import registry as registry_mod
from percell4.application.analysis.modules.per_particle_donut import (  # noqa: F401
    PerParticleDonut,
)
from percell4.application.session import Session
from percell4.gui import per_particle_donut_dialog  # noqa: F401  # late-binds dialog_class
from percell4.interfaces.gui.main_window import LauncherWindow
from percell4.model import CellDataModel


@pytest.fixture(autouse=True)
def _reregister_analysis() -> Iterator[None]:
    if "per_particle_donut" not in registry_mod._REGISTRY:
        import importlib

        import percell4.application.analysis.modules.per_particle_donut as mod

        importlib.reload(mod)
    yield


def _make_launcher() -> LauncherWindow:
    session = Session()
    model = CellDataModel(session=session)
    return LauncherWindow(model)


def test_scripts_panel_populates_button_for_registered_analysis(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    panel = launcher._create_scripts_workflows_panel()
    qtbot.addWidget(panel)
    button_labels = [
        b.text() for b in panel.findChildren(QPushButton)
    ]
    assert PerParticleDonut.display_name in button_labels


def test_scripts_panel_button_tooltip_uses_description(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    panel = launcher._create_scripts_workflows_panel()
    qtbot.addWidget(panel)
    btn = next(
        b for b in panel.findChildren(QPushButton)
        if b.text() == PerParticleDonut.display_name
    )
    assert "donut" in btn.toolTip().lower() or PerParticleDonut.description in btn.toolTip()


def test_scripts_panel_empty_registry_shows_friendly_label(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    # Clear the registry so the panel renders the empty state.
    saved = dict(registry_mod._REGISTRY)
    registry_mod._REGISTRY.clear()
    try:
        panel = launcher._create_scripts_workflows_panel()
        qtbot.addWidget(panel)
        from qtpy.QtWidgets import QGroupBox
        analyses_group = next(
            g for g in panel.findChildren(QGroupBox) if g.title() == "Analyses"
        )
        labels = [lbl.text() for lbl in analyses_group.findChildren(QLabel)]
        assert any("No analyses registered" in t for t in labels)
        # No analysis buttons in the Analyses section (Workflows buttons live
        # in their own group and are unaffected by the analysis registry).
        assert analyses_group.findChildren(QPushButton) == []
    finally:
        registry_mod._REGISTRY.update(saved)


def test_merged_panel_has_analyses_and_workflows_sections(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    panel = launcher._create_scripts_workflows_panel()
    qtbot.addWidget(panel)
    titles = {g.title() for g in panel.findChildren(QGroupBox)}
    assert "Analyses" in titles
    assert "Workflows" in titles


def test_merged_panel_keeps_all_five_workflow_buttons(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    panel = launcher._create_scripts_workflows_panel()
    qtbot.addWidget(panel)
    workflows_group = next(
        g for g in panel.findChildren(QGroupBox) if g.title() == "Workflows"
    )
    labels = {b.text() for b in workflows_group.findChildren(QPushButton)}
    assert {
        "Single-cell thresholding analysis workflow",
        "Dilute phase mask generation",
        "Dilute phase mask from mask",
        "FLIM-FRET analysis",
        "Automated phasor-masks workflow",
    } <= labels


def test_sidebar_has_merged_workflows_and_batch_tools_tabs(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    names = [b.text() for b in launcher._sidebar_buttons]
    assert len(names) == 8
    # No standalone "Scripts" tab; the merged tab is named "Workflows".
    assert "Scripts" not in names
    assert "Workflows" in names
    assert "Batch Tools" in names


def test_on_open_analysis_workflow_locked_short_circuits(qtbot, monkeypatch):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    monkeypatch.setattr(
        type(launcher), "is_workflow_locked",
        property(lambda self: True),
    )
    # Should not raise and should not open a dialog. The status message
    # is set on the status bar.
    launcher._on_open_analysis("per_particle_donut")
    msg = launcher.statusBar().currentMessage()
    assert "workflow" in msg.lower()


def test_on_open_analysis_no_dialog_class_shows_status(qtbot):
    """When dialog_class is None, the status bar reports the issue."""
    launcher = _make_launcher()
    qtbot.addWidget(launcher)

    # Stub analysis with no dialog_class.
    from percell4.domain.analysis import Analysis, ImageRole

    saved = dict(registry_mod._REGISTRY)
    registry_mod._REGISTRY.clear()
    try:
        from percell4.application.analysis import register_analysis

        @register_analysis("stub_no_dialog")
        class StubNoDialog(Analysis):
            name = "stub_no_dialog"
            display_name = "Stub with no dialog"
            required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
            dialog_class = None

            def run(self, inputs, params):
                return {}

        launcher._on_open_analysis("stub_no_dialog")
        msg = launcher.statusBar().currentMessage()
        assert "no dialog" in msg.lower()
    finally:
        registry_mod._REGISTRY.clear()
        registry_mod._REGISTRY.update(saved)


def test_on_open_analysis_unknown_name_shows_status(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    launcher._on_open_analysis("nope_not_registered")
    msg = launcher.statusBar().currentMessage()
    assert "not registered" in msg.lower()


def test_on_open_analysis_instantiates_dialog_class(qtbot, monkeypatch):
    """The click handler reads cls.dialog_class and exec_s an instance."""
    launcher = _make_launcher()
    qtbot.addWidget(launcher)

    captured: dict = {}

    class FakeDialog(QDialog):
        last_run_folder = None

        def __init__(self, parent=None):
            super().__init__(parent)
            captured["instantiated"] = True
            captured["parent"] = parent

        def exec_(self):
            captured["exec_called"] = True
            self.last_run_folder = "/tmp/fake_run"
            return 1

        def deleteLater(self):  # noqa: N802 — Qt API name
            captured["deleted"] = True

    # Patch the live class in the registry (the test module's binding may
    # be on an older class object if importlib.reload fired earlier).
    from percell4.application.analysis import get as registry_get
    registered_cls = registry_get("per_particle_donut")
    monkeypatch.setattr(registered_cls, "dialog_class", FakeDialog)

    launcher._on_open_analysis("per_particle_donut")
    assert captured.get("instantiated") is True
    assert captured.get("parent") is launcher
    assert captured.get("exec_called") is True
    msg = launcher.statusBar().currentMessage()
    assert "/tmp/fake_run" in msg
