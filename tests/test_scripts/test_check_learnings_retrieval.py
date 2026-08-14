"""Tests for ``scripts/claude_code_hooks/check_learnings_retrieval.py``.

Drives the hook script via ``subprocess`` so the JSON-on-stdin contract,
exit code, and stderr output are all exercised end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = REPO_ROOT / "scripts" / "claude_code_hooks" / "check_learnings_retrieval.py"


def _run(payload: object | str) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


# ── Happy path: warning emitted ─────────────────────────────────────


def test_edit_on_t1_dialog_emits_warning() -> None:
    result = _run({
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/percell4/gui/import_dialog.py"},
    })
    assert result.returncode == 0
    assert "dialog-scroll-when-tall" in result.stderr
    assert "src/percell4/gui/_dialog_utils.py" in result.stderr


def test_write_on_t1_store_emits_multiple_warnings() -> None:
    result = _run({
        "tool_name": "Write",
        "tool_input": {"file_path": "src/percell4/store.py"},
    })
    assert result.returncode == 0
    assert "atomic-write-contract" in result.stderr
    assert "decay-write-path" in result.stderr


def test_multiedit_is_watched() -> None:
    result = _run({
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": "src/percell4/gui/import_dialog.py"},
    })
    assert result.returncode == 0
    assert "dialog-scroll-when-tall" in result.stderr


def test_absolute_path_normalized_to_repo_relative() -> None:
    abs_path = REPO_ROOT / "src/percell4/gui/import_dialog.py"
    result = _run({
        "tool_name": "Edit",
        "tool_input": {"file_path": str(abs_path)},
    })
    assert result.returncode == 0
    assert "dialog-scroll-when-tall" in result.stderr


# ── Silent paths ────────────────────────────────────────────────────


def test_edit_on_non_t1_path_emits_nothing() -> None:
    result = _run({
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/percell4/domain/measure/metrics.py"},
    })
    assert result.returncode == 0
    assert result.stderr == ""


def test_edit_outside_watched_prefixes_emits_nothing() -> None:
    result = _run({
        "tool_name": "Edit",
        "tool_input": {"file_path": "docs/foo.md"},
    })
    assert result.returncode == 0
    assert result.stderr == ""


def test_read_tool_emits_nothing() -> None:
    result = _run({
        "tool_name": "Read",
        "tool_input": {"file_path": "src/percell4/gui/import_dialog.py"},
    })
    assert result.returncode == 0
    assert result.stderr == ""


def test_bash_tool_emits_nothing() -> None:
    result = _run({
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    assert result.returncode == 0
    assert result.stderr == ""


# ── Defensive failure paths ─────────────────────────────────────────


def test_malformed_stdin_exits_zero_with_skip_message() -> None:
    result = _run("not json at all")
    assert result.returncode == 0
    assert "skipped" in result.stderr.lower()


def test_missing_file_path_exits_zero_with_skip_message() -> None:
    result = _run({
        "tool_name": "Edit",
        "tool_input": {},
    })
    assert result.returncode == 0
    assert "skipped" in result.stderr.lower()


def test_missing_tool_name_exits_zero() -> None:
    result = _run({"tool_input": {"file_path": "src/percell4/store.py"}})
    assert result.returncode == 0


def test_empty_stdin_exits_zero_with_skip_message() -> None:
    result = _run("")
    assert result.returncode == 0
    assert "skipped" in result.stderr.lower()


# ── Performance ─────────────────────────────────────────────────────


def test_hook_invocation_completes_under_500ms() -> None:
    start = time.perf_counter()
    _run({
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/percell4/gui/import_dialog.py"},
    })
    elapsed_ms = (time.perf_counter() - start) * 1000
    # Includes Python interpreter startup; budget is generous.
    assert elapsed_ms < 500, f"hook took {elapsed_ms:.1f}ms (>500ms budget)"
