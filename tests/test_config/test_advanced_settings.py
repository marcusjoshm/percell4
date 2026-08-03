"""Tests for the advanced-settings store.

Every test here runs against a redirected store. The suite-wide autouse
fixture in ``tests/conftest.py`` guarantees that even for a test that forgets
to ask -- see ``test_advanced_settings_isolation_compliance.py`` for why that
belt-and-braces matters.
"""

from __future__ import annotations

import json
import os

import pytest

from percell4.config import advanced


def test_missing_file_returns_defaults():
    """A machine that has never opened the Advanced panel must behave exactly
    as it did before the panel existed."""
    settings = advanced.load_advanced_settings()
    assert settings.cellpose_device is None


def test_round_trip_preserves_the_override():
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))
    assert advanced.load_advanced_settings().cellpose_device == "xpu"


def test_round_trip_preserves_a_cleared_override():
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device=None))
    assert advanced.load_advanced_settings().cellpose_device is None


def test_blank_override_is_stored_as_unset():
    """A cleared text field arrives as an empty string. Storing that verbatim
    would make every later run probe a device named empty-string."""
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="  "))
    assert advanced.load_advanced_settings().cellpose_device is None


def test_malformed_json_returns_defaults():
    """An opt-in convenience file must never be able to break the default
    path -- that would let an unused feature take down every run."""
    advanced.config_path().parent.mkdir(parents=True, exist_ok=True)
    advanced.config_path().write_text("{not json at all", encoding="utf-8")
    assert advanced.load_advanced_settings().cellpose_device is None


@pytest.mark.parametrize("payload", ["[]", '"a string"', "42", "null"])
def test_non_object_json_returns_defaults(payload):
    """Valid JSON that isn't an object is still not a settings file."""
    advanced.config_path().parent.mkdir(parents=True, exist_ok=True)
    advanced.config_path().write_text(payload, encoding="utf-8")
    assert advanced.load_advanced_settings().cellpose_device is None


def test_wrong_typed_value_returns_defaults():
    """A hand-edited file with a number where a device name belongs must not
    reach the resolver as an int."""
    advanced.config_path().parent.mkdir(parents=True, exist_ok=True)
    advanced.config_path().write_text(
        json.dumps({"cellpose_device": 7}), encoding="utf-8"
    )
    assert advanced.load_advanced_settings().cellpose_device is None


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses the permission bit this test relies on",
)
def test_unreadable_file_returns_defaults():
    advanced.config_path().parent.mkdir(parents=True, exist_ok=True)
    advanced.config_path().write_text(
        json.dumps({"cellpose_device": "xpu"}), encoding="utf-8"
    )
    advanced.config_path().chmod(0o000)
    try:
        assert advanced.load_advanced_settings().cellpose_device is None
    finally:
        advanced.config_path().chmod(0o600)


def test_unknown_keys_are_ignored():
    """A file written by a newer build must not break an older one."""
    advanced.config_path().parent.mkdir(parents=True, exist_ok=True)
    advanced.config_path().write_text(
        json.dumps({"cellpose_device": "cuda:1", "future_setting": True}),
        encoding="utf-8",
    )
    assert advanced.load_advanced_settings().cellpose_device == "cuda:1"


def test_unknown_keys_survive_a_round_trip():
    """Loading and re-saving on an older build must not silently delete a
    newer build's settings."""
    advanced.config_path().parent.mkdir(parents=True, exist_ok=True)
    advanced.config_path().write_text(
        json.dumps({"cellpose_device": "cuda:1", "future_setting": True}),
        encoding="utf-8",
    )
    advanced.save_advanced_settings(advanced.load_advanced_settings())
    reread = json.loads(advanced.config_path().read_text(encoding="utf-8"))
    assert reread["future_setting"] is True


def test_save_creates_the_parent_directory(tmp_path):
    advanced.redirect_to(tmp_path / "deep" / "nested")
    try:
        advanced.save_advanced_settings(
            advanced.AdvancedSettings(cellpose_device="mps")
        )
        assert advanced.load_advanced_settings().cellpose_device == "mps"
    finally:
        advanced.clear_redirect()


def test_redirect_keeps_writes_inside_the_given_directory(tmp_path):
    advanced.redirect_to(tmp_path)
    try:
        advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))
        assert advanced.config_path().is_relative_to(tmp_path)
    finally:
        advanced.clear_redirect()


def test_redirect_holds_for_directly_imported_functions(tmp_path):
    """The redirect is consulted per call through a module global rather than
    captured at import, so ``from ... import save_advanced_settings`` is
    covered too. Binding style is exactly what defeated the previous
    generation of settings sandboxes in this repo."""
    from percell4.config.advanced import load_advanced_settings, save_advanced_settings

    advanced.redirect_to(tmp_path)
    try:
        save_advanced_settings(advanced.AdvancedSettings(cellpose_device="cuda:2"))
        assert (tmp_path / advanced.CONFIG_FILENAME).exists()
        assert load_advanced_settings().cellpose_device == "cuda:2"
    finally:
        advanced.clear_redirect()


def test_module_imports_without_qt_or_torch():
    """The batch CLI reads this store headlessly. A Qt import here would drag
    a GUI toolkit into a terminal run, and a torch import would cost seconds
    of startup for a value that is usually unset."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import percell4.config.advanced, sys; "
            "assert 'qtpy' not in sys.modules, 'qtpy leaked'; "
            "assert 'torch' not in sys.modules, 'torch leaked'; "
            "print('clean')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
