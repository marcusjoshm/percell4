"""Tests for the Cellpose device resolver.

Every probe is patched. These tests must pass on a machine with no
accelerator of any kind, which is the common case for CI and for most
development laptops -- a test that only passes where CUDA happens to exist
would be silently skipped exactly where the resolver matters most.
"""

from __future__ import annotations

import pytest

from percell4.adapters import torch_device


def _patch_probe(monkeypatch, results):
    """Patch the probe with a dict of ``{device_spec: error_or_None}``.

    A spec absent from ``results`` probes as unusable with a generic message,
    so a test only has to name the devices it cares about.
    """
    calls = []

    def _fake(spec):
        calls.append(spec)
        return results.get(spec, f"no {spec} here")

    monkeypatch.setattr(torch_device, "_probe_device", _fake)
    return calls


def test_gpu_not_requested_is_not_a_fallback(monkeypatch):
    """An unchecked GPU box is a deliberate choice, not a degraded run."""
    _patch_probe(monkeypatch, {})
    res = torch_device.resolve_device(gpu_requested=False, override=None)
    assert res.device == "cpu"
    assert res.fell_back is False
    assert "not requested" in res.reason.lower()


def test_no_accelerator_falls_back_and_says_why(monkeypatch):
    """AE1: GPU requested, nothing available -> CPU with a stated reason."""
    _patch_probe(monkeypatch, {})
    res = torch_device.resolve_device(gpu_requested=True, override=None)
    assert res.device == "cpu"
    assert res.fell_back is True
    assert "no supported accelerator" in res.reason.lower()


def test_auto_prefers_cuda_over_mps(monkeypatch):
    """Probe order is CUDA then MPS, matching Cellpose's own resolution."""
    calls = _patch_probe(monkeypatch, {"cuda": None, "mps": None})
    res = torch_device.resolve_device(gpu_requested=True, override=None)
    assert res.device == "cuda"
    assert res.fell_back is False
    assert calls[0] == "cuda"


def test_auto_uses_mps_when_cuda_absent(monkeypatch):
    _patch_probe(monkeypatch, {"mps": None})
    res = torch_device.resolve_device(gpu_requested=True, override=None)
    assert res.device == "mps"
    assert res.fell_back is False


def test_unparseable_override_falls_back_without_raising(monkeypatch):
    """A hand-typed nonsense device must not crash the run."""
    _patch_probe(monkeypatch, {"nonsense": "Expected one of cpu, cuda, ..."})
    res = torch_device.resolve_device(gpu_requested=True, override="nonsense")
    assert res.device == "cpu"
    assert res.fell_back is True
    assert "nonsense" in res.reason


def test_override_probe_assertion_error_is_caught(monkeypatch):
    """AE3: torch raises AssertionError -- not RuntimeError -- for an XPU
    request on a build without XPU support. Catching only RuntimeError would
    let this escape as a crash."""
    _patch_probe(monkeypatch, {"xpu": "Torch not compiled with XPU enabled"})
    res = torch_device.resolve_device(gpu_requested=True, override="xpu")
    assert res.device == "cpu"
    assert res.fell_back is True
    assert "xpu" in res.reason
    assert "Torch not compiled with XPU enabled" in res.reason


def test_override_probe_runtime_error_is_caught(monkeypatch):
    """The RuntimeError class behaves identically to the AssertionError one."""
    _patch_probe(monkeypatch, {"cuda:3": "Found no NVIDIA driver"})
    res = torch_device.resolve_device(gpu_requested=True, override="cuda:3")
    assert res.device == "cpu"
    assert res.fell_back is True
    assert "Found no NVIDIA driver" in res.reason


def test_working_override_is_used(monkeypatch):
    _patch_probe(monkeypatch, {"xpu": None})
    res = torch_device.resolve_device(gpu_requested=True, override="xpu")
    assert res.device == "xpu"
    assert res.fell_back is False


def test_override_ignored_when_gpu_not_requested(monkeypatch):
    """KTD4: unchecking the box forces CPU regardless of Advanced settings,
    so a user always has an override-free way back to a known-good run."""
    calls = _patch_probe(monkeypatch, {"xpu": None})
    res = torch_device.resolve_device(gpu_requested=False, override="xpu")
    assert res.device == "cpu"
    assert res.fell_back is False
    assert calls == []  # no probe at all -- nothing to initialize


def test_resolution_records_the_override_that_produced_it(monkeypatch):
    """Callers that cache a built model compare this against the current
    stored override to decide whether the cache is stale."""
    _patch_probe(monkeypatch, {"xpu": None})
    res = torch_device.resolve_device(gpu_requested=True, override="xpu")
    assert res.requested == "xpu"

    auto = torch_device.resolve_device(gpu_requested=True, override=None)
    assert auto.requested is None


def test_blank_override_is_treated_as_unset(monkeypatch):
    """A cleared text field can arrive as an empty or whitespace string; that
    means 'auto', not 'a device named empty string'."""
    calls = _patch_probe(monkeypatch, {"cuda": None})
    res = torch_device.resolve_device(gpu_requested=True, override="   ")
    assert res.device == "cuda"
    assert res.requested is None
    assert "   " not in calls


def test_describe_environment_reports_backends(monkeypatch):
    _patch_probe(monkeypatch, {"cuda": None})
    report = torch_device.describe_torch_environment()
    assert report.torch_available is True
    assert report.torch_version
    assert report.backends["cuda"] is None
    assert report.backends["xpu"] is not None  # unusable, carries the reason


def test_describe_environment_survives_missing_torch(monkeypatch):
    """The Advanced panel renders this. A torch that fails to import is a
    state the panel must display, not one that takes the panel down."""
    def _boom():
        raise ImportError("No module named 'torch'")

    monkeypatch.setattr(torch_device, "_import_torch", _boom)
    report = torch_device.describe_torch_environment()
    assert report.torch_available is False
    assert "torch" in report.summary.lower()


def test_resolve_survives_missing_torch(monkeypatch):
    """Resolution must degrade to CPU rather than raise when torch is broken --
    the run then fails on Cellpose's own import with a clearer message."""
    def _boom(spec):
        raise ImportError("No module named 'torch'")

    monkeypatch.setattr(torch_device, "_probe_device", _boom)
    res = torch_device.resolve_device(gpu_requested=True, override=None)
    assert res.device == "cpu"
    assert res.fell_back is True


@pytest.mark.parametrize("spec", ["cpu", "CPU", " cpu "])
def test_explicit_cpu_override_is_not_a_fallback(monkeypatch, spec):
    """Asking for CPU and getting CPU is not a degradation, so it must not
    raise the fallback warning."""
    _patch_probe(monkeypatch, {"cpu": None})
    res = torch_device.resolve_device(gpu_requested=True, override=spec)
    assert res.device == "cpu"
    assert res.fell_back is False
