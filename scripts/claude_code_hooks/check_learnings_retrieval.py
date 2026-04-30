"""Claude Code ``PreToolUse`` hook: emit applicable ``docs/solutions/``
learnings before an Edit/Write/MultiEdit lands.

Reads the tool-call JSON from stdin, extracts the target file path,
calls ``scripts.learnings_applicability.applicable_entries``, and prints
a structured warning to stderr when the path matches one or more
canonical sources. Exits 0 in all cases — the underlying tool call
always proceeds (v1 is warn-only per R15/R16 of the audit brainstorm).

Stdlib only; runs as a subprocess without ``.venv`` activation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

WATCHED_TOOLS = {"Edit", "Write", "MultiEdit"}
WATCHED_PREFIXES = ("src/percell4/", "tests/")


def _emit_warning(file_path: str, entries: list) -> None:
    lines = [
        "",
        f"⚠ Audit retrieval: {len(entries)} canonical source(s) apply to {file_path}",
    ]
    for e in entries:
        canonical = e.canonical_source or "TBD (pre_canonical)"
        lines.append(f"  • {e.slug}  [{e.status}]")
        lines.append(f"    canonical_source: {canonical}")
        lines.append(f"    doc: {e.path}")
    lines.append(
        "  Consult ce-learnings-researcher with the file path before substantive edits."
    )
    lines.append("")
    sys.stderr.write("\n".join(lines) + "\n")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.stderr.write("# learnings-retrieval check skipped: malformed stdin\n")
        return 0

    tool_name = payload.get("tool_name")
    if tool_name not in WATCHED_TOOLS:
        return 0

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        sys.stderr.write("# learnings-retrieval check skipped: no file_path\n")
        return 0

    p = Path(file_path)
    if p.is_absolute():
        try:
            rel = p.relative_to(REPO_ROOT)
        except ValueError:
            return 0
        rel_str = str(rel).replace("\\", "/")
    else:
        rel_str = str(p).replace("\\", "/")

    if not rel_str.startswith(WATCHED_PREFIXES):
        return 0

    try:
        from scripts.learnings_applicability import applicable_entries

        entries = applicable_entries(file_path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"# learnings-retrieval check skipped: {type(exc).__name__}\n")
        return 0

    if entries:
        _emit_warning(rel_str, entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
