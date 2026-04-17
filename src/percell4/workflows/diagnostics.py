"""Qt-agnostic classification of worker/background errors.

`WorkerError` is the structured shape a background worker emits in place of
a stringified exception. `classify()` maps it to an `ErrorKind` so UI code
can decide whether to show a specific dialog or fall back to a generic
status-bar message.

The classifier keys off Windows `winerror` codes (deterministic) plus
`exc_type` + a minimal substring guard, not DLL-name whack-a-mole. See
`docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorKind(Enum):
    GENERIC = "generic"
    TORCH_DLL_INIT = "torch_dll_init"
    TORCH_DLL_MISSING = "torch_dll_missing"
    TORCH_BITNESS = "torch_bitness"
    TORCH_IMPORT_FAILED = "torch_import_failed"


@dataclass(frozen=True, slots=True)
class WorkerError:
    exc_type: str
    message: str
    is_import_error: bool
    winerror: int | None
    traceback: str


def classify(err: WorkerError) -> ErrorKind:
    if err.winerror == 1114 and _looks_like_torch(err.message):
        return ErrorKind.TORCH_DLL_INIT
    if err.winerror == 126 and _looks_like_torch(err.message):
        return ErrorKind.TORCH_DLL_MISSING
    if err.winerror == 193:
        return ErrorKind.TORCH_BITNESS
    if err.exc_type == "ImportError" and "torch" in err.message.lower():
        return ErrorKind.TORCH_IMPORT_FAILED
    return ErrorKind.GENERIC


def _looks_like_torch(msg: str) -> bool:
    low = msg.lower()
    return "torch" in low or "c10.dll" in low
