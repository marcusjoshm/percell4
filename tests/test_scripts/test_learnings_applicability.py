"""Tests for ``scripts.learnings_applicability``."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scripts.learnings_applicability import (
    _glob_to_regex,
    _matches,
    _parse_frontmatter,
    applicable_entries,
)


# ── _glob_to_regex / _matches ───────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        # Single-star: matches anywhere except across `/`
        ("src/percell4/gui/*_dialog.py", "src/percell4/gui/import_dialog.py", True),
        ("src/percell4/gui/*_dialog.py", "src/percell4/gui/foo/bar_dialog.py", False),
        ("src/percell4/gui/*Dialog.py", "src/percell4/gui/AddLayerDialog.py", True),
        # Double-star: matches across `/` (zero or more components when **/)
        (
            "src/percell4/gui/workflows/**/config_dialog.py",
            "src/percell4/gui/workflows/single_cell/config_dialog.py",
            True,
        ),
        (
            "src/percell4/gui/workflows/**/config_dialog.py",
            "src/percell4/gui/workflows/config_dialog.py",
            True,
        ),
        ("src/**/*.py", "src/percell4/gui/import_dialog.py", True),
        # Literal paths match themselves (decay-write-path.md does this)
        ("src/percell4/store.py", "src/percell4/store.py", True),
        ("src/percell4/store.py", "src/percell4/other.py", False),
        # Free-form prose entries (no `/` and no `*`) never match
        ("any future entry point", "src/percell4/store.py", False),
        # Dot is escaped; `*.py` does not match `Xpy`
        ("src/*.py", "src/fooXpy", False),
    ],
)
def test_glob_matching(pattern: str, path: str, expected: bool) -> None:
    assert _matches(path, pattern) is expected


# ── _parse_frontmatter ──────────────────────────────────────────────


def test_parse_frontmatter_simple() -> None:
    text = '---\ntitle: "Hello"\nstatus: canonical_clean\n---\n# body\n'
    fm = _parse_frontmatter(text)
    assert fm["title"] == "Hello"
    assert fm["status"] == "canonical_clean"


def test_parse_frontmatter_list() -> None:
    text = (
        '---\napplies_to:\n  - "src/foo/*.py"\n  - "src/bar/*.py"\n'
        "status: canonical_clean\n---\nbody\n"
    )
    fm = _parse_frontmatter(text)
    assert fm["applies_to"] == ["src/foo/*.py", "src/bar/*.py"]
    assert fm["status"] == "canonical_clean"


def test_parse_frontmatter_inline_empty_list() -> None:
    text = "---\napplies_to: []\nstatus: canonical_clean\n---\n"
    fm = _parse_frontmatter(text)
    assert fm["applies_to"] == []


def test_parse_frontmatter_no_frontmatter_returns_empty() -> None:
    assert _parse_frontmatter("# just markdown\n") == {}


def test_parse_frontmatter_unterminated_returns_empty() -> None:
    assert _parse_frontmatter("---\ntitle: foo\nbody never closes\n") == {}


# ── applicable_entries against real registry ───────────────────────


def test_applicable_entries_dialog_match() -> None:
    matches = applicable_entries("src/percell4/gui/import_dialog.py")
    slugs = {e.slug for e in matches}
    assert "dialog-scroll-when-tall" in slugs


def test_applicable_entries_workflows_double_star_match() -> None:
    matches = applicable_entries(
        "src/percell4/gui/workflows/single_cell/config_dialog.py"
    )
    slugs = {e.slug for e in matches}
    assert "dialog-scroll-when-tall" in slugs


def test_applicable_entries_multiple_matches() -> None:
    matches = applicable_entries("src/percell4/store.py")
    slugs = {e.slug for e in matches}
    assert {"atomic-write-contract", "decay-write-path"} <= slugs


def test_applicable_entries_no_match() -> None:
    matches = applicable_entries("src/percell4/domain/measure/metrics.py")
    assert matches == []


def test_applicable_entries_accepts_absolute_paths(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    abs_path = repo_root / "src/percell4/gui/import_dialog.py"
    matches = applicable_entries(str(abs_path))
    slugs = {e.slug for e in matches}
    assert "dialog-scroll-when-tall" in slugs


def test_applicable_entries_path_outside_repo_returns_empty() -> None:
    matches = applicable_entries("/tmp/random/file.py")
    assert matches == []


# ── status filtering + isolation via fixture root ──────────────────


def _write_fixture(root: Path, name: str, frontmatter: str) -> None:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(f"---\n{frontmatter}\n---\nbody\n")


def test_superseded_entries_are_filtered(tmp_path: Path) -> None:
    fake_repo = tmp_path
    fake_root = fake_repo / "docs" / "solutions"
    _write_fixture(
        fake_root,
        "fresh.md",
        'applies_to:\n  - "src/foo.py"\nstatus: canonical_clean',
    )
    _write_fixture(
        fake_root,
        "stale.md",
        'applies_to:\n  - "src/foo.py"\nstatus: superseded',
    )

    matches = applicable_entries(
        "src/foo.py",
        solutions_root=fake_root,
        repo_root=fake_repo,
    )
    slugs = {e.slug for e in matches}
    assert "fresh" in slugs
    assert "stale" not in slugs


def test_empty_applies_to_filters_entry_out(tmp_path: Path) -> None:
    fake_repo = tmp_path
    fake_root = fake_repo / "docs" / "solutions"
    _write_fixture(
        fake_root,
        "empty.md",
        "applies_to: []\nstatus: canonical_clean",
    )

    matches = applicable_entries(
        "src/foo.py",
        solutions_root=fake_root,
        repo_root=fake_repo,
    )
    assert matches == []


def test_missing_applies_to_does_not_match_anything(tmp_path: Path) -> None:
    fake_repo = tmp_path
    fake_root = fake_repo / "docs" / "solutions"
    _write_fixture(
        fake_root,
        "no_applies.md",
        'title: "missing applies_to"\nstatus: canonical_clean',
    )

    matches = applicable_entries(
        "src/foo.py",
        solutions_root=fake_root,
        repo_root=fake_repo,
    )
    assert matches == []


def test_malformed_frontmatter_is_skipped_silently(tmp_path: Path) -> None:
    fake_repo = tmp_path
    fake_root = fake_repo / "docs" / "solutions"
    fake_root.mkdir(parents=True)
    (fake_root / "broken.md").write_text("---\ntitle: missing close\nbody only\n")

    # Should not raise.
    matches = applicable_entries(
        "src/foo.py",
        solutions_root=fake_root,
        repo_root=fake_repo,
    )
    assert matches == []


# ── performance ────────────────────────────────────────────────────


def test_applicable_entries_completes_under_50ms_against_real_registry() -> None:
    start = time.perf_counter()
    applicable_entries("src/percell4/gui/import_dialog.py")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 50, f"applicable_entries took {elapsed_ms:.1f}ms (>50ms budget)"
