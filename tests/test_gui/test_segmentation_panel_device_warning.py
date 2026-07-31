"""The Segment tab reports which device Cellpose actually used.

A fallback is announced once per distinct reason and then demoted to the
status line. That balance is the whole point of the feature: the original
defect was a silent CPU fallback that read as a hang, but a machine with no
accelerator falls back on *every* run, and a dialog every run trains the user
to dismiss it -- rebuilding the same blindness through a different door.
"""

from __future__ import annotations

import pytest

from percell4.adapters.torch_device import DeviceResolution
from percell4.gui.segmentation_panel import SegmentationPanel


@pytest.fixture
def panel(qtbot, monkeypatch):
    """A panel with its dialog suppressed and its status line captured."""
    p = SegmentationPanel.__new__(SegmentationPanel)
    p._launcher = None
    p._seen_device_warnings = set()
    p._status_messages = []
    monkeypatch.setattr(
        type(p), "_show_status", lambda self, msg: self._status_messages.append(msg)
    )
    dialogs = []
    monkeypatch.setattr(
        "percell4.gui.segmentation_panel.message_box",
        lambda *args, **kwargs: dialogs.append((args, kwargs)),
    )
    p._dialogs = dialogs
    return p


def _resolution(device="cpu", fell_back=True, reason="no accelerator", requested=None):
    return DeviceResolution(
        device=device, fell_back=fell_back, reason=reason, requested=requested
    )


def test_successful_device_is_reported_without_a_dialog(panel):
    """A GPU run that worked should stay quiet."""
    panel._on_device_resolved(_resolution(device="cuda", fell_back=False,
                                          reason="Running on cuda."))
    assert panel._dialogs == []
    assert any("cuda" in m for m in panel._status_messages)


def test_fallback_raises_a_dialog_the_first_time(panel):
    panel._on_device_resolved(_resolution(reason="no supported accelerator found"))
    assert len(panel._dialogs) == 1
    body = " ".join(str(a) for a in panel._dialogs[0][0])
    assert "no supported accelerator found" in body


def test_repeat_fallback_reports_without_another_dialog(panel):
    """Same machine, same reason, second run: the status line still says it,
    but the dialog does not return."""
    res = _resolution(reason="no supported accelerator found")
    panel._on_device_resolved(res)
    panel._on_device_resolved(res)

    assert len(panel._dialogs) == 1
    # Still reported both times -- suppression drops the dialog, not the report.
    assert panel._status_messages == [res.reason, res.reason]


def test_a_different_fallback_reason_raises_its_own_dialog(panel):
    """Suppression is per reason, not blanket. Configuring a new device and
    having it fail is new information."""
    panel._on_device_resolved(_resolution(reason="no supported accelerator found"))
    panel._on_device_resolved(
        _resolution(reason="configured device 'xpu' is not usable", requested="xpu")
    )
    assert len(panel._dialogs) == 2


def test_configured_device_failure_names_the_device(panel):
    """AE3: the warning has to say which device was rejected, or the user
    cannot tell whether their Advanced setting was even read."""
    panel._on_device_resolved(
        _resolution(reason="the configured device 'xpu' is not usable "
                           "(Torch not compiled with XPU enabled)",
                    requested="xpu")
    )
    body = " ".join(str(a) for a in panel._dialogs[0][0])
    assert "xpu" in body
    assert "Torch not compiled with XPU enabled" in body


def test_explicit_cpu_choice_is_not_announced_as_a_fallback(panel):
    """Asking for CPU and getting it is not a degradation."""
    panel._on_device_resolved(
        _resolution(device="cpu", fell_back=False, reason="GPU was not requested.")
    )
    assert panel._dialogs == []


def test_warning_does_not_abort_the_run(panel):
    """Falling back is not an error path -- segmentation still completes.
    The handler must return normally so the worker keeps going."""
    assert panel._on_device_resolved(_resolution()) is None


def test_dialog_goes_through_the_shared_helper():
    """Popup compliance: every popup routes through _dialog_utils so GNOME
    does not glue it to its parent."""
    from pathlib import Path

    source = Path("src/percell4/gui/segmentation_panel.py").read_text(encoding="utf-8")
    assert "from percell4.gui._dialog_utils import" in source
    assert "QMessageBox.warning(" not in source
