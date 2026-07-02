"""Tests for the Batch Tools Console CLI catalog (Qt-free).

Exercises enumeration of the importable ``percell4-*`` console scripts
and resolution of a typed command line into an executable argv. No Qt.
"""

from __future__ import annotations

import sys
from importlib import util as importlib_util

import pytest

from percell4.interfaces.cli.catalog import (
    CommandParseError,
    UnknownCommand,
    list_batch_tools,
    resolve_command,
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
    with pytest.raises(UnknownCommand) as exc:
        resolve_command("ls -la")
    assert exc.value.name == "ls"


def test_resolve_empty_and_whitespace():
    with pytest.raises(UnknownCommand):
        resolve_command("")
    with pytest.raises(UnknownCommand):
        resolve_command("   ")


def test_resolve_unbalanced_quote():
    with pytest.raises(CommandParseError):
        resolve_command('percell4-batch-export "unclosed')
