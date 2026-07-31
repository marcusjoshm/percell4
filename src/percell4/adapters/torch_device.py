"""Resolve which torch device Cellpose should run on, and say so out loud.

Cellpose resolves its own device in ``cellpose/core.py::_use_gpu_torch``, and
that resolver tries exactly two things: ``cuda``, then ``mps``. Anything else
-- an Intel XPU, a second CUDA card -- returns False, and ``assign_device``
quietly assigns ``torch.device('cpu')`` with an INFO log nobody sees. On a
laptop running the ``cpsam_v2`` SAM backbone, that silent downgrade looks
exactly like a hang.

This module replaces that silence with a :class:`DeviceResolution` that always
states which device won and why. It also reaches devices Cellpose's own
resolver never considers: ``CellposeModel`` accepts an explicit ``device``
argument that bypasses ``assign_device`` entirely (``models.py:129``), so any
device this module can probe is a device Cellpose can use.

**ROCm needs nothing special here.** A ROCm build of PyTorch reports
``torch.cuda.is_available() == True`` and uses ``torch.device("cuda")`` as its
HIP device, so AMD cards resolve through the ordinary auto path.

Qt-free and napari-free on purpose: the launcher panel, the interactive
segmentation path, and the headless batch CLI all share this one resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Auto-detect probe order. Mirrors Cellpose's own preference so that leaving
#: the override unset reproduces its behavior exactly -- with the reasoning
#: made visible.
_AUTO_ORDER = ("cuda", "mps")

#: Backends worth reporting in the Advanced panel's environment readout.
#: Broader than :data:`_AUTO_ORDER` because the point of the readout is to
#: show what a machine offers *before* someone configures an override.
_REPORTED_BACKENDS = ("cuda", "mps", "xpu")


@dataclass(frozen=True, slots=True)
class DeviceResolution:
    """The outcome of resolving a device, and the account of how it got there.

    ``reason`` is display-ready: callers surface it verbatim rather than
    reconstructing an explanation from the other fields.
    """

    #: The torch device string Cellpose should use (``cpu`` when nothing else worked).
    device: str
    #: True only when a GPU was wanted and could not be had. Explicitly asking
    #: for CPU is not a fallback, so this stays False there -- otherwise every
    #: deliberate CPU run would raise a warning that means nothing.
    fell_back: bool
    #: Why this device won, phrased for a person.
    reason: str
    #: The override that produced this resolution, normalized, or None when
    #: auto-detection ran. Callers that cache a built model compare this
    #: against the current stored override to decide whether the cache is stale.
    requested: str | None = None


@dataclass(frozen=True, slots=True)
class TorchEnvironment:
    """What the installed PyTorch build actually offers, for display."""

    torch_available: bool
    torch_version: str
    #: Build tag: the CUDA version, ``ROCm <v>`` for a HIP build, or ``cpu``.
    build: str
    #: ``{backend: None if usable else reason}``.
    backends: dict[str, str | None] = field(default_factory=dict)
    #: One-line human summary, safe to render even when torch is missing.
    summary: str = ""


def _import_torch():
    """Import torch. Split out so tests can simulate a broken install."""
    import torch

    return torch


def _normalize(override: str | None) -> str | None:
    """Fold a blank or whitespace-only override to None.

    A cleared text field arrives as ``""`` and a half-cleared one as ``"  "``;
    both mean "auto", and passing either to ``torch.device`` would fail in a
    way that reads like a real misconfiguration.
    """
    if override is None:
        return None
    cleaned = override.strip().lower()
    return cleaned or None


def _probe_device(spec: str) -> str | None:
    """Try to allocate on ``spec``. Return None if it works, else the reason.

    Allocation is the test, not a capability flag. On a machine with no
    NVIDIA driver at all, ``torch.accelerator.current_accelerator()`` still
    reports ``cuda`` while ``torch.cuda.is_available()`` reports False -- the
    flags disagree and only a real allocation settles it.

    ``AssertionError`` is caught alongside ``RuntimeError`` because that is
    what torch raises for a device its build does not include (``Torch not
    compiled with XPU enabled``); catching only ``RuntimeError`` would let an
    XPU request escape as a crash.
    """
    torch = _import_torch()
    try:
        device = torch.device(spec)
        torch.zeros(1).to(device)
    except (RuntimeError, AssertionError, ValueError, TypeError) as exc:
        return str(exc) or exc.__class__.__name__
    return None


def resolve_device(
    gpu_requested: bool,
    override: str | None = None,
) -> DeviceResolution:
    """Decide which device Cellpose runs on.

    ``gpu_requested`` is the "Use GPU" control. When it is False the answer is
    CPU and no probe runs -- an unchecked box is a deliberate choice, and it
    stays the one override-free way back to a known-good run.

    ``override`` names an explicit torch device (``xpu``, ``cuda:1``). It only
    applies when a GPU was requested, and an unusable one falls back to CPU
    rather than failing the run.
    """
    requested = _normalize(override)

    if not gpu_requested:
        return DeviceResolution(
            device="cpu",
            fell_back=False,
            reason="Running on CPU: GPU was not requested.",
            requested=requested,
        )

    if requested is not None:
        try:
            failure = _probe_device(requested)
        except Exception as exc:  # noqa: BLE001 - a broken torch must not abort the run
            failure = str(exc)
        if failure is None:
            # Asking for CPU and getting it is not a degradation.
            return DeviceResolution(
                device=requested,
                fell_back=False,
                reason=f"Running on {requested} (configured in Advanced settings).",
                requested=requested,
            )
        return DeviceResolution(
            device="cpu",
            fell_back=requested != "cpu",
            reason=(
                f"Running on CPU: the configured device {requested!r} is not "
                f"usable on this machine ({failure})."
            ),
            requested=requested,
        )

    for candidate in _AUTO_ORDER:
        try:
            failure = _probe_device(candidate)
        except Exception:  # noqa: BLE001 - see above
            continue
        if failure is None:
            return DeviceResolution(
                device=candidate,
                fell_back=False,
                reason=f"Running on {candidate}.",
                requested=None,
            )

    return DeviceResolution(
        device="cpu",
        fell_back=True,
        reason=(
            "Running on CPU: no supported accelerator was found. PyTorch "
            "reaches NVIDIA (CUDA), AMD (ROCm, which also reports as CUDA), "
            "and Apple (MPS) automatically. Other hardware needs an explicit "
            "device in Advanced settings."
        ),
        requested=None,
    )


def describe_torch_environment() -> TorchEnvironment:
    """Report what the installed torch build offers, for the Advanced panel.

    Probing initializes each backend it can reach, so callers should build
    this lazily rather than at startup.
    """
    try:
        torch = _import_torch()
    except Exception as exc:  # noqa: BLE001 - the panel must render this state
        return TorchEnvironment(
            torch_available=False,
            torch_version="",
            build="",
            backends={},
            summary=f"PyTorch could not be imported: {exc}",
        )

    if getattr(torch.version, "hip", None):
        build = f"ROCm {torch.version.hip}"
    elif getattr(torch.version, "cuda", None):
        build = f"CUDA {torch.version.cuda}"
    else:
        build = "cpu"

    backends: dict[str, str | None] = {}
    for name in _REPORTED_BACKENDS:
        try:
            backends[name] = _probe_device(name)
        except Exception as exc:  # noqa: BLE001
            backends[name] = str(exc)

    usable = [name for name, failure in backends.items() if failure is None]
    if usable:
        summary = f"PyTorch {torch.__version__} ({build}); usable: {', '.join(usable)}."
    else:
        summary = (
            f"PyTorch {torch.__version__} ({build}); no accelerator is usable, "
            f"so Cellpose will run on CPU."
        )

    return TorchEnvironment(
        torch_available=True,
        torch_version=str(torch.__version__),
        build=build,
        backends=backends,
        summary=summary,
    )
