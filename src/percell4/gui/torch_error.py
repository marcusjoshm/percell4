"""Shared dialog for classifying worker errors into actionable guidance.

Callers pass a :class:`WorkerError` from a worker's ``error`` signal; if the
classifier recognizes it as a torch/DLL environment problem, this module
shows a :class:`QMessageBox.warning` with a triage pointer and returns True.
Otherwise it returns False and the caller falls back to its normal status-bar
path.

See ``docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md``.
"""

from __future__ import annotations

from qtpy.QtWidgets import QMessageBox, QWidget

from percell4.workflows.diagnostics import ErrorKind, WorkerError, classify

_TITLES_AND_BODIES: dict[ErrorKind, tuple[str, str]] = {
    ErrorKind.TORCH_DLL_INIT: (
        "PyTorch failed to initialize",
        "PyTorch's c10.dll could not load on this machine.\n\n"
        "Most common fixes on Windows, in order:\n"
        "1. Install Microsoft Visual C++ 2015-2022 x64 Redistributable 14.50+\n"
        "   (https://aka.ms/vs/17/release/vc_redist.x64.exe) and reboot.\n"
        "2. Reinstall CPU-only torch:\n"
        "   pip install --no-cache-dir --force-reinstall torch "
        "--index-url https://download.pytorch.org/whl/cpu\n"
        "3. If you have torch==2.9.0 specifically, downgrade:\n"
        "   pip install \"torch<2.9\" --index-url "
        "https://download.pytorch.org/whl/cpu\n"
        "   (pytorch#169429 — regression with Qt import order).\n\n"
        "Full triage: docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md"
    ),
    ErrorKind.TORCH_DLL_MISSING: (
        "PyTorch DLL missing",
        "A PyTorch DLL could not be found on the DLL search path.\n\n"
        "Reinstall CPU-only torch:\n"
        "  pip install --no-cache-dir --force-reinstall torch "
        "--index-url https://download.pytorch.org/whl/cpu\n\n"
        "If the error persists, check that antivirus (Defender) has not "
        "quarantined files under `.venv\\Lib\\site-packages\\torch\\lib\\`."
    ),
    ErrorKind.TORCH_BITNESS: (
        "PyTorch architecture mismatch",
        "The installed PyTorch does not match this Python's architecture "
        "(32-bit vs 64-bit).\n\n"
        "Recreate the venv with 64-bit Python 3.12 from python.org, then "
        "reinstall dependencies."
    ),
    ErrorKind.TORCH_IMPORT_FAILED: (
        "PyTorch import failed",
        "PyTorch could not be imported. This usually means the package is "
        "partially installed or the wheel is incompatible with this Python.\n\n"
        "Reinstall:\n"
        "  pip install --no-cache-dir --force-reinstall torch "
        "--index-url https://download.pytorch.org/whl/cpu"
    ),
}


def handle_worker_error(
    parent: QWidget | None,
    err: WorkerError,
    *,
    context: str = "",
) -> bool:
    """Classify ``err`` and show a dialog if it matches a known category.

    Returns True if a dialog was shown (caller can skip its fallback),
    False otherwise (caller should show its normal status-bar message).
    """
    kind = classify(err)
    entry = _TITLES_AND_BODIES.get(kind)
    if entry is None:
        return False

    title, body = entry
    prefix = f"[{context}] " if context else ""
    QMessageBox.warning(
        parent,
        title,
        f"{prefix}{body}\n\nRaw error: {err.exc_type}: {err.message}",
    )
    return True
