"""Device reporting on the two surfaces that cache their Cellpose model.

Both the seg-QC re-run and the workflow runner build a model themselves and
hand it to ``run_cellpose``, which then skips construction. That makes model
construction the only point where a device is chosen on these surfaces -- and
the only place a device callback can fire.
"""

from __future__ import annotations

from percell4.adapters.torch_device import DeviceResolution
from percell4.config import advanced


def _resolution(device="cpu", fell_back=True, reason="no accelerator"):
    return DeviceResolution(device=device, fell_back=fell_back, reason=reason)


# ── seg-QC re-run ────────────────────────────────────────────────────


def _qc_stub():
    """A bare controller carrying only the device-reporting state."""
    from percell4.gui.workflows.single_cell.seg_qc import SegmentationQCController

    qc = SegmentationQCController.__new__(SegmentationQCController)
    qc._cleanup_status_label = None
    qc._statuses = []
    qc._set_rerun_status = qc._statuses.append
    return qc


def test_qc_reports_a_successful_device_on_its_status_line():
    qc = _qc_stub()
    qc._on_device_resolved(_resolution(device="cuda", fell_back=False))
    assert any("cuda" in s for s in qc._statuses)


def test_qc_reports_a_fallback_reason_verbatim():
    qc = _qc_stub()
    qc._on_device_resolved(_resolution(reason="no supported accelerator found"))
    assert qc._statuses == ["no supported accelerator found"]


def test_qc_uses_no_dialog():
    """The QC window is a tight iterate-and-look loop; a modal in the middle
    of it would be worse than the silence this feature replaces."""
    from pathlib import Path

    source = Path(
        "src/percell4/gui/workflows/single_cell/seg_qc.py"
    ).read_text(encoding="utf-8")
    handler = source.split("def _on_device_resolved")[1].split("\n    def ")[0]
    assert "message_box" not in handler
    assert "QMessageBox" not in handler


def test_qc_attaches_the_callback_at_model_construction():
    """Wired to build_cellpose_model, not to the run_cellpose call that
    receives the cached model -- the latter never constructs, so a callback
    there would never fire."""
    from pathlib import Path

    source = Path(
        "src/percell4/gui/workflows/single_cell/seg_qc.py"
    ).read_text(encoding="utf-8")
    build_call = source.split("build_cellpose_model(")[1].split(")")[0]
    assert "device_callback" in build_call


def test_qc_cache_is_keyed_on_the_stored_override():
    """A model holds the device it was built on. Without invalidation an
    Advanced-panel edit would appear to do nothing until the window reopened."""
    from pathlib import Path

    source = Path(
        "src/percell4/gui/workflows/single_cell/seg_qc.py"
    ).read_text(encoding="utf-8")
    assert "load_cellpose_device" in source
    assert "_cellpose_model_device" in source


def test_changed_override_is_observable_between_reruns():
    """The invalidation predicate: what the cache was built with vs what is
    stored now."""
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))
    built_with = advanced.load_cellpose_device()
    assert built_with == "xpu"

    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="cuda:1"))
    assert advanced.load_cellpose_device() != built_with


# ── workflow runner ──────────────────────────────────────────────────


def _runner_stub():
    from percell4.gui.workflows.single_cell.runner import SingleCellThresholdingRunner

    runner = SingleCellThresholdingRunner.__new__(SingleCellThresholdingRunner)
    runner._entries = []
    runner._run_log = None
    logged = []
    runner._log = lambda **fields: logged.append(fields)
    runner._logged = logged
    return runner


def test_runner_records_the_device_in_the_run_log():
    """Into the run log, not a dialog: this drives long unattended batches
    and a modal partway through would strand the run behind a prompt nobody
    is there to dismiss."""
    runner = _runner_stub()
    runner._on_device_resolved(_resolution(reason="no supported accelerator found"))

    assert len(runner._logged) == 1
    entry = runner._logged[0]
    assert entry["phase"] == "segment"
    assert entry["event"] == "device"
    assert entry["fell_back"] is True
    assert entry["message"] == "no supported accelerator found"


def test_runner_records_a_successful_device_too():
    """A fallback is only legible against a record of the normal case."""
    runner = _runner_stub()
    runner._on_device_resolved(_resolution(device="cuda", fell_back=False))

    entry = runner._logged[0]
    assert entry["device"] == "cuda"
    assert entry["fell_back"] is False


def test_runner_attaches_the_callback_at_every_build_site():
    """Two lazy-build sites (interactive and headless). Both must report, or
    one whole workflow mode goes back to being silent."""
    from pathlib import Path

    source = Path(
        "src/percell4/gui/workflows/single_cell/runner.py"
    ).read_text(encoding="utf-8")
    build_sites = source.count("build_cellpose_model(")
    wired = source.count("device_callback=self._on_device_resolved")
    assert build_sites >= 2
    assert wired == build_sites
