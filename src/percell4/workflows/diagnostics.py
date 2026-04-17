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

import re
import sys
from dataclasses import dataclass
from enum import Enum

# PyTorch 2.9.x requires the MSVC 2015-2022 x64 Redistributable at version
# 14.50 or newer; older copies cause OSError [WinError 1114] loading c10.dll.
# See pytorch/pytorch#169429.
_MIN_MSVC_REDIST = (14, 50)


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


def _parse_msvc_version(version: str) -> tuple[int, int] | None:
    """Parse an MSVC Redistributable version string like 'v14.44.35211.00'
    into a (major, minor) tuple. Returns None on unparseable input."""
    match = re.match(r"v?(\d+)\.(\d+)", version)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def check_msvc_redist_version() -> tuple[bool, str | None]:
    """Check the installed MSVC 2015-2022 x64 Redistributable version.

    Returns ``(is_current, version_string)``. ``is_current`` is True on
    non-Windows platforms (there is no such concept) and on Windows when
    the installed Redistributable is 14.50 or newer. ``version_string`` is
    the raw registry value when available, or ``None`` if the key is
    missing (Redist not installed) or the platform is not Windows.
    """
    if sys.platform != "win32":
        return True, None

    try:
        import winreg  # Windows-only stdlib module

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        ) as key:
            version, _ = winreg.QueryValueEx(key, "Version")
    except (OSError, FileNotFoundError):
        return False, None

    parsed = _parse_msvc_version(version)
    if parsed is None:
        return True, version
    return parsed >= _MIN_MSVC_REDIST, version
