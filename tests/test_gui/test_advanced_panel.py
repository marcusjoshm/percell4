"""Tests for the Advanced task panel.

Headless: the panel is plain widgets, no GL. The environment readout is
patched throughout, so these assert the panel's behavior rather than the
machine's hardware.
"""

from __future__ import annotations

import pytest

from percell4.adapters.torch_device import TorchEnvironment
from percell4.config import advanced
from percell4.interfaces.gui.task_panels.advanced_panel import AdvancedPanel


@pytest.fixture
def env_report(monkeypatch):
    """Patch the environment readout with a controllable stub."""
    calls = []

    def _fake():
        calls.append(1)
        return TorchEnvironment(
            torch_available=True,
            torch_version="2.13.0",
            build="CUDA 13.0",
            backends={"cuda": None, "mps": "not linked", "xpu": "not compiled"},
            summary="PyTorch 2.13.0 (CUDA 13.0); usable: cuda.",
        )

    monkeypatch.setattr(
        "percell4.interfaces.gui.task_panels.advanced_panel."
        "describe_torch_environment",
        _fake,
    )
    return calls


def _panel(qtbot, **kwargs):
    panel = AdvancedPanel(**kwargs)
    qtbot.addWidget(panel)
    return panel


def test_constructs_without_a_launcher(qtbot, env_report):
    """Panels own their inputs via injected callbacks. A panel that needs a
    launcher to exist cannot be tested and cannot be reused."""
    seen = []
    panel = _panel(qtbot, show_status=seen.append)
    assert panel is not None


def test_environment_readout_is_not_built_during_construction(qtbot, env_report):
    """Probing initializes every backend it reaches. Doing that at launcher
    startup would cost every user seconds for a panel most never open."""
    _panel(qtbot)
    assert env_report == []


def test_environment_readout_populates_on_first_show(qtbot, env_report):
    panel = _panel(qtbot)
    panel.show()
    qtbot.waitExposed(panel)
    assert len(env_report) == 1
    assert "usable: cuda" in panel._env_label.text()


def test_environment_readout_is_built_once_not_on_every_show(qtbot, env_report):
    panel = _panel(qtbot)
    panel.show()
    qtbot.waitExposed(panel)
    panel.hide()
    panel.show()
    assert len(env_report) == 1


def test_refresh_rebuilds_the_environment_readout(qtbot, env_report):
    panel = _panel(qtbot)
    panel.show()
    qtbot.waitExposed(panel)
    panel._on_refresh_environment()
    assert len(env_report) == 2


def test_loads_an_existing_stored_override(qtbot, env_report):
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))
    panel = _panel(qtbot)
    assert panel._device.currentText() == "xpu"


def test_saving_writes_the_override(qtbot, env_report, monkeypatch):
    from percell4.adapters import torch_device

    monkeypatch.setattr(torch_device, "_probe_device", lambda spec: None)
    panel = _panel(qtbot)
    panel._device.setCurrentText("cuda:1")
    panel._on_save()
    assert advanced.load_advanced_settings().cellpose_device == "cuda:1"


def test_clearing_the_field_stores_none_not_empty_string(qtbot, env_report):
    """An empty string would be probed as a device name on every later run
    and reported as a fallback nobody configured."""
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))
    panel = _panel(qtbot)
    panel._device.setCurrentText("")
    panel._on_save()
    assert advanced.load_advanced_settings().cellpose_device is None


def test_saving_a_usable_device_reports_success(qtbot, env_report, monkeypatch):
    from percell4.adapters import torch_device

    monkeypatch.setattr(torch_device, "_probe_device", lambda spec: None)
    panel = _panel(qtbot)
    panel._device.setCurrentText("xpu")
    panel._on_save()
    assert "xpu" in panel._device_status.text()
    assert "not usable" not in panel._device_status.text().lower()


def test_saving_an_unusable_device_warns_immediately(qtbot, env_report, monkeypatch):
    """The whole point of this feature is that a bad device stops being a
    silent surprise. Accepting one here without comment, then reporting it
    an hour later mid-run, would rebuild the problem one layer up."""
    from percell4.adapters import torch_device

    monkeypatch.setattr(
        torch_device, "_probe_device", lambda spec: "Torch not compiled with XPU"
    )
    panel = _panel(qtbot)
    panel._device.setCurrentText("xpu")
    panel._on_save()

    status = panel._device_status.text()
    assert "xpu" in status
    assert "Torch not compiled with XPU" in status


def test_an_unusable_device_is_still_stored(qtbot, env_report, monkeypatch):
    """Warning is not refusing. A user may configure a device for hardware
    they are about to install, or for a machine they sync settings to."""
    from percell4.adapters import torch_device

    monkeypatch.setattr(torch_device, "_probe_device", lambda spec: "nope")
    panel = _panel(qtbot)
    panel._device.setCurrentText("xpu")
    panel._on_save()
    assert advanced.load_advanced_settings().cellpose_device == "xpu"


def test_clearing_reports_auto_detect_rather_than_a_probe_result(qtbot, env_report):
    panel = _panel(qtbot)
    panel._device.setCurrentText("")
    panel._on_save()
    assert "auto" in panel._device_status.text().lower()


def test_renders_when_torch_is_unavailable(qtbot, monkeypatch):
    """A broken torch is a state the panel must display, not one that takes
    the panel down -- this readout is where a user goes to find out why."""
    monkeypatch.setattr(
        "percell4.interfaces.gui.task_panels.advanced_panel."
        "describe_torch_environment",
        lambda: TorchEnvironment(
            torch_available=False,
            torch_version="",
            build="",
            backends={},
            summary="PyTorch could not be imported: No module named 'torch'",
        ),
    )
    panel = _panel(qtbot)
    panel.show()
    qtbot.waitExposed(panel)
    assert "could not be imported" in panel._env_label.text()


def test_status_callback_is_optional(qtbot, env_report, monkeypatch):
    """Default no-op status keeps the panel constructible in a bare harness."""
    from percell4.adapters import torch_device

    monkeypatch.setattr(torch_device, "_probe_device", lambda spec: None)
    panel = _panel(qtbot)
    panel._device.setCurrentText("cuda")
    panel._on_save()  # must not raise without show_status injected


def test_panel_holds_no_launcher_references():
    """Guard matching the panel-extraction checklist.

    Checks code, not prose: the module docstring says "no launcher
    reference", which is the thing being asserted, not a violation of it.
    """
    import ast
    from pathlib import Path

    source = Path(
        "src/percell4/interfaces/gui/task_panels/advanced_panel.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # No import of the launcher module.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "main_window" not in (node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "main_window" not in alias.name

    # No identifier or attribute mentioning a launcher/parent private.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert "launcher" not in node.id.lower()
        elif isinstance(node, ast.Attribute):
            assert "launcher" not in node.attr.lower()
        elif isinstance(node, ast.arg):
            assert "launcher" not in node.arg.lower()


# ── Launcher registration ────────────────────────────────────────────


def test_launcher_exposes_an_advanced_sidebar_entry():
    """Registration is by name in the categories list; assert the entry
    exists without constructing a launcher (which would build a viewer)."""
    from pathlib import Path

    source = Path("src/percell4/interfaces/gui/main_window.py").read_text(
        encoding="utf-8"
    )
    assert '("Advanced", self._create_advanced_panel)' in source
    assert "def _create_advanced_panel" in source


def test_advanced_is_registered_last_so_existing_positions_hold():
    """Sidebar order is positional. Inserting anywhere but the end would
    shift every panel after it, and Batch Tools resolves its own index by
    scanning this list."""
    import re
    from pathlib import Path

    source = Path("src/percell4/interfaces/gui/main_window.py").read_text(
        encoding="utf-8"
    )
    block = re.search(r"categories = \[(.*?)\]", source, re.DOTALL)
    assert block is not None
    names = re.findall(r'\("([^"]+)",', block.group(1))
    assert names[-1] == "Advanced"
    assert names[:-1] == [
        "I/O",
        "Viewer",
        "Segmentation",
        "Analysis",
        "FLIM",
        "Workflows",
        "Batch Tools",
        "Data",
    ]


# ── Readout legibility ───────────────────────────────────────────────


def test_backend_failures_are_trimmed_for_display(qtbot, monkeypatch):
    """Torch's CUDA message is a paragraph ending in a download URL. Shown
    whole, one unavailable backend pushes the others out of view -- the
    opposite of what a what-does-this-machine-offer readout is for."""
    verbose = (
        "Found no NVIDIA driver on your system. Please check that you have "
        "an NVIDIA GPU and installed a driver from "
        "http://www.nvidia.com/Download/index.aspx"
    )
    monkeypatch.setattr(
        "percell4.interfaces.gui.task_panels.advanced_panel."
        "describe_torch_environment",
        lambda: TorchEnvironment(
            torch_available=True,
            torch_version="2.13.0",
            build="CUDA 13.0",
            backends={"cuda": verbose, "mps": "not linked", "xpu": "not compiled"},
            summary="no accelerator is usable",
        ),
    )
    panel = _panel(qtbot)
    panel._refresh_environment()
    text = panel._env_label.text()

    assert "Found no NVIDIA driver on your system" in text
    assert "nvidia.com/Download" not in text
    # Every backend still gets a line -- trimming must not drop any.
    for name in ("cuda", "mps", "xpu"):
        assert name in text
    # The untruncated text stays reachable.
    assert "nvidia.com/Download" in panel._env_label.toolTip()


def test_editing_the_device_clears_a_stale_verdict(qtbot, env_report, monkeypatch):
    """Leaving the previous device's result next to a new unsaved value is
    how a user comes to believe an untested device works."""
    from percell4.adapters import torch_device

    monkeypatch.setattr(torch_device, "_probe_device", lambda spec: None)
    panel = _panel(qtbot)
    panel._device.setCurrentText("cuda")
    panel._on_save()
    assert panel._device_status.text() != ""

    panel._device.setCurrentText("xpu")
    assert panel._device_status.text() == ""
