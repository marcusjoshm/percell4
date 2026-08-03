"""Tests for the Batch Tools Console CLI catalog (Qt-free).

Exercises enumeration of the importable ``percell4-*`` console scripts
and resolution of a typed command line into an executable argv. No Qt.
"""

from __future__ import annotations

import sys
from importlib import util as importlib_util

import pytest

from percell4.interfaces.cli import catalog as catalog_mod
from percell4.interfaces.cli.catalog import (
    CommandParseError,
    UnknownCommandError,
    list_batch_tools,
    resolve_command,
    split_command,
)

# ── Enumeration ─────────────────────────────────────────────────────────


def test_lists_real_tool_mapped_to_module():
    tools = {t.name: t for t in list_batch_tools()}
    assert "percell4-batch-cellpose-laptrack" in tools
    assert (
        tools["percell4-batch-cellpose-laptrack"].module
        == "percell4.interfaces.cli.batch_process"
    )


def test_every_entry_is_prefixed_and_importable():
    # The importability filter is what excludes phantom entries; proving
    # every listed tool imports proves no phantom survived.
    tools = list_batch_tools()
    assert tools  # at least the real batch tools are installed
    for tool in tools:
        assert tool.name.startswith("percell4-")
        assert importlib_util.find_spec(tool.module) is not None


def test_phantom_entry_excluded():
    # Stale installed metadata points percell4-per-cell-sweep /
    # percell4-window-k-sweep at deleted modules. They must be filtered.
    names = {t.name for t in list_batch_tools()}
    for phantom, module in (
        ("percell4-per-cell-sweep", "percell4.interfaces.cli.per_cell_sweep"),
        ("percell4-window-k-sweep", "percell4.interfaces.cli.window_k_sweep"),
    ):
        if importlib_util.find_spec(module) is None:
            assert phantom not in names


def test_gui_script_not_in_catalog():
    # percell4-gui lives in the gui_scripts group, not console_scripts.
    names = {t.name for t in list_batch_tools()}
    assert "percell4-gui" not in names


# ── Command resolution ──────────────────────────────────────────────────


def test_resolve_builds_module_argv():
    argv = resolve_command(
        "percell4-batch-cellpose-laptrack ./exp1 --seg-channel 0 --gpu"
    )
    assert argv == [
        sys.executable,
        "-m",
        "percell4.interfaces.cli.batch_process",
        "./exp1",
        "--seg-channel",
        "0",
        "--gpu",
    ]


def test_resolve_quoted_path_with_spaces():
    argv = resolve_command('percell4-batch-export "/a b/exp.h5"')
    assert argv[:3] == [
        sys.executable,
        "-m",
        "percell4.interfaces.cli.batch_export",
    ]
    assert argv[3] == "/a b/exp.h5"


def test_resolve_unknown_command():
    with pytest.raises(UnknownCommandError) as exc:
        resolve_command("ls -la")
    assert exc.value.name == "ls"


def test_resolve_empty_and_whitespace():
    with pytest.raises(UnknownCommandError):
        resolve_command("")
    with pytest.raises(UnknownCommandError):
        resolve_command("   ")


def test_resolve_unbalanced_quote():
    with pytest.raises(CommandParseError):
        resolve_command('percell4-batch-export "unclosed')


# ── split_command: native-Windows path safety ───────────────────────────


def _simulate_windows(monkeypatch):
    monkeypatch.setattr(catalog_mod.os, "name", "nt")


def test_split_command_posix_default():
    # On POSIX (this platform), plain shlex.split — forward-slash paths and
    # quoted paths tokenize cleanly.
    assert split_command("t /a/b.h5") == ["t", "/a/b.h5"]
    assert split_command('t "/a b/x.h5"') == ["t", "/a b/x.h5"]


def test_split_command_windows_preserves_backslashes(monkeypatch):
    # The exact defect: a typed native path must NOT lose its separators.
    _simulate_windows(monkeypatch)
    assert split_command(r"t E:\data\dish.h5") == ["t", r"E:\data\dish.h5"]
    assert split_command(r"m C:\Users\me\dishes") == ["m", r"C:\Users\me\dishes"]


def test_split_command_windows_quoted_paths_round_trip(monkeypatch):
    _simulate_windows(monkeypatch)
    # double-quoted path with a space
    assert split_command(r't "E:\My Data\d.h5"') == ["t", r"E:\My Data\d.h5"]
    # single-quoted path — what shlex.quote (navigator / drag-drop) inserts
    assert split_command(r"t 'E:\data\d.h5'") == ["t", r"E:\data\d.h5"]


def test_split_command_windows_hash_in_path(monkeypatch):
    # '#' must not start a comment — a real filename can contain it.
    _simulate_windows(monkeypatch)
    assert split_command(r"t E:\exp#1\d.h5") == ["t", r"E:\exp#1\d.h5"]


def test_split_command_windows_unbalanced_quote_raises(monkeypatch):
    _simulate_windows(monkeypatch)
    with pytest.raises(ValueError):
        split_command('t "unclosed')
