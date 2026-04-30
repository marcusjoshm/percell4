"""Map a file path to applicable ``docs/solutions/`` entries.

Each entry's frontmatter declares ``applies_to`` as a list of glob patterns.
This helper walks the registry, parses each entry's frontmatter, and returns
the entries whose patterns match the given path.

Stdlib-only — invoked by Claude Code hooks that run as subprocesses without
``.venv`` activation.

Glob convention (mirrors gitignore / pathspec):

- ``*`` matches anything except ``/``.
- ``**`` matches anything including ``/``.
- ``**/`` matches zero or more directory components.
- ``?`` matches any single character except ``/``.
- Free-form prose entries (no ``*`` or ``/``) are silently skipped.

See ``docs/solutions/ui-bugs/dialog-scroll-when-tall.md`` for an exemplar
entry, and ``docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md``
(R7) for the frontmatter contract.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOLUTIONS_ROOT = REPO_ROOT / "docs" / "solutions"


@dataclass(frozen=True)
class Entry:
    slug: str
    path: str
    canonical_source: str | None
    status: str | None
    matched_pattern: str


def _glob_to_regex(pattern: str) -> str:
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i:i + 3] == "**/":
            parts.append("(?:.*/)?")
            i += 3
        elif pattern[i:i + 2] == "**":
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return "^" + "".join(parts) + "$"


def _matches(path: str, pattern: str) -> bool:
    if "/" not in pattern and "*" not in pattern:
        return False
    return re.match(_glob_to_regex(pattern), path) is not None


def _parse_frontmatter(text: str) -> dict[str, str | list[str] | None]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str | list[str] | None] = {}
    current_list_key: str | None = None
    key_re = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$")
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if current_list_key is not None and line.startswith("  - "):
            item = line[4:].strip()
            if (item.startswith('"') and item.endswith('"')) or (
                item.startswith("'") and item.endswith("'")
            ):
                item = item[1:-1]
            assert isinstance(out[current_list_key], list)
            out[current_list_key].append(item)  # type: ignore[union-attr]
            continue
        m = key_re.match(line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            current_list_key = None
            if value == "":
                out[key] = []
                current_list_key = key
            elif value == "[]":
                out[key] = []
            else:
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                out[key] = value
        else:
            current_list_key = None
    return {}


def applicable_entries(
    file_path: str,
    solutions_root: Path | None = None,
    repo_root: Path | None = None,
) -> list[Entry]:
    repo = repo_root or REPO_ROOT
    root = solutions_root or (repo / "docs" / "solutions")
    if not root.is_dir():
        return []

    p = Path(file_path)
    if p.is_absolute():
        try:
            p = p.relative_to(repo)
        except ValueError:
            return []
    rel = str(p).replace("\\", "/")

    results: list[Entry] = []
    for md_path in sorted(root.rglob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if not fm:
            continue
        applies = fm.get("applies_to")
        if not isinstance(applies, list) or not applies:
            continue
        status = fm.get("status")
        if status == "superseded":
            continue
        for pattern in applies:
            if not isinstance(pattern, str):
                continue
            if _matches(rel, pattern):
                canonical = fm.get("canonical_source")
                results.append(
                    Entry(
                        slug=md_path.stem,
                        path=str(md_path.relative_to(repo)).replace("\\", "/"),
                        canonical_source=canonical if isinstance(canonical, str) else None,
                        status=status if isinstance(status, str) else None,
                        matched_pattern=pattern,
                    )
                )
                break
    return results


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python3 scripts/learnings_applicability.py <file-path>", file=sys.stderr)
        return 2
    matches = applicable_entries(args[0])
    if not matches:
        print(f"# no applicable entries for {args[0]}")
        return 0
    print(f"# {len(matches)} applicable entry(ies) for {args[0]}")
    for e in matches:
        canonical = e.canonical_source or "TBD"
        print(f"  - {e.slug} (status={e.status}, canonical_source={canonical}, matched={e.matched_pattern})")
        print(f"    {e.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
