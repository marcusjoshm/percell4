"""Tests for `percell4.workflows.diagnostics` — the Qt-agnostic worker
error classifier."""

from __future__ import annotations

import sys

from percell4.workflows.diagnostics import (
    ErrorKind,
    WorkerError,
    _parse_msvc_version,
    check_msvc_redist_version,
    classify,
)


def _err(
    *,
    exc_type: str = "OSError",
    message: str = "",
    is_import_error: bool = True,
    winerror: int | None = None,
    traceback: str = "",
) -> WorkerError:
    return WorkerError(
        exc_type=exc_type,
        message=message,
        is_import_error=is_import_error,
        winerror=winerror,
        traceback=traceback,
    )


class TestClassify:
    def test_winerror_1114_with_torch_is_dll_init(self) -> None:
        err = _err(
            winerror=1114,
            message=(
                "[WinError 1114] A dynamic link library (DLL) initialization "
                "routine failed. Error loading "
                r"E:\percell4\.venv\Lib\site-packages\torch\lib\c10.dll"
            ),
        )
        assert classify(err) == ErrorKind.TORCH_DLL_INIT

    def test_winerror_1114_unrelated_is_generic(self) -> None:
        err = _err(
            winerror=1114,
            message="Error loading C:\\Windows\\System32\\somefile.dll",
        )
        assert classify(err) == ErrorKind.GENERIC

    def test_winerror_126_torch_is_dll_missing(self) -> None:
        err = _err(
            winerror=126,
            message="Could not find module 'torch_python.dll'",
        )
        assert classify(err) == ErrorKind.TORCH_DLL_MISSING

    def test_winerror_126_unrelated_is_generic(self) -> None:
        err = _err(
            winerror=126,
            message="Could not find module 'somelib.dll'",
        )
        assert classify(err) == ErrorKind.GENERIC

    def test_winerror_193_is_bitness_regardless_of_module(self) -> None:
        err = _err(winerror=193, message="%1 is not a valid Win32 application")
        assert classify(err) == ErrorKind.TORCH_BITNESS

    def test_import_error_with_torch_is_import_failed(self) -> None:
        err = _err(
            exc_type="ImportError",
            message="cannot import name 'foo' from 'torch.utils'",
            is_import_error=True,
            winerror=None,
        )
        assert classify(err) == ErrorKind.TORCH_IMPORT_FAILED

    def test_import_error_without_torch_is_generic(self) -> None:
        err = _err(
            exc_type="ImportError",
            message="No module named 'numpy.something'",
            is_import_error=True,
            winerror=None,
        )
        assert classify(err) == ErrorKind.GENERIC

    def test_c10_dll_substring_alone_matches_torch(self) -> None:
        """c10.dll in the message should match even if 'torch' is absent
        (some error formats drop the path and only name the DLL)."""
        err = _err(winerror=1114, message="Error loading c10.dll")
        assert classify(err) == ErrorKind.TORCH_DLL_INIT

    def test_runtime_error_is_generic(self) -> None:
        err = _err(
            exc_type="RuntimeError",
            message="something went wrong",
            is_import_error=False,
            winerror=None,
        )
        assert classify(err) == ErrorKind.GENERIC


class TestWorkerError:
    def test_is_frozen(self) -> None:
        err = _err()
        try:
            err.exc_type = "changed"  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("WorkerError should be frozen")

    def test_uses_slots(self) -> None:
        err = _err()
        assert not hasattr(err, "__dict__")


class TestParseMsvcVersion:
    def test_canonical_format(self) -> None:
        assert _parse_msvc_version("v14.44.35211.00") == (14, 44)

    def test_no_leading_v(self) -> None:
        assert _parse_msvc_version("14.50.35719.00") == (14, 50)

    def test_only_major_minor(self) -> None:
        assert _parse_msvc_version("v14.50") == (14, 50)

    def test_below_threshold(self) -> None:
        # Real observation from a Lee Lab Windows box, April 2026.
        assert _parse_msvc_version("v14.44.35211.00") < (14, 50)

    def test_at_threshold(self) -> None:
        assert _parse_msvc_version("v14.50.35719.00") >= (14, 50)

    def test_unparseable_returns_none(self) -> None:
        assert _parse_msvc_version("garbage") is None

    def test_empty_returns_none(self) -> None:
        assert _parse_msvc_version("") is None


class TestCheckMsvcRedistVersion:
    def test_non_windows_always_current(self) -> None:
        """On macOS/Linux the check must return (True, None) without
        attempting to import winreg."""
        if sys.platform == "win32":
            # This test is for the non-Windows contract; skip on Windows
            # where the behavior depends on registry state.
            return
        is_current, version = check_msvc_redist_version()
        assert is_current is True
        assert version is None
