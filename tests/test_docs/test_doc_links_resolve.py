"""Links and anchors in the top-level documentation must resolve.

The README was split across several ``docs/`` pages, which moved a pile of
in-document anchors into cross-file links and created two pages whose headings
collide (``### Windows`` and ``### Linux`` each appear under both Installation
and Troubleshooting, so GitHub disambiguates the second with a ``-1`` suffix).
Both are easy to get wrong by hand and invisible until a reader clicks.

Every failure is reported at once rather than aborting on the first, because
fixing links one test run at a time is miserable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CHECKED_DOCS = (
    "README.md",
    "docs/installation.md",
    "docs/cli.md",
    "docs/workflow-protocol.md",
    "docs/architecture.md",
    "docs/writing_an_analysis.md",
    "docs/adaptive-local-clipping.md",
    "docs/CONCEPTS.md",
    "docs/CHANGELOG.md",
    "docs/screenshots/README.md",
)

# ![alt](target) and [text](target), skipping reference-style and bare autolinks.
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)


def _github_slug(heading_text: str) -> str:
    """Reproduce github-slugger's rules.

    The subtle part, and the reason this is not a one-liner: github-slugger
    replaces *each* space with a hyphen rather than collapsing runs. A heading
    like ``` `percell4-batch-export` -- TIFF export ``` loses its em-dash to
    punctuation-stripping and is left with two adjacent spaces, so the real
    anchor carries a *double* hyphen. Collapsing whitespace here would reject
    every correct command anchor in docs/cli.md.
    """
    text = re.sub(r"`([^`]*)`", r"\1", heading_text)  # strip code spans, keep contents
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # strip links, keep label
    text = re.sub(r"[*_]{1,3}(.+?)[*_]{1,3}", r"\1", text)  # strip emphasis, keep text
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)  # drop punctuation; keep word chars, hyphen, space
    return text.replace(" ", "-")


def _strip_fenced_blocks(text: str) -> str:
    """Blank out fenced code blocks, preserving line count.

    A shell comment inside a fence (``# Tesseract OCR engine:``) is not a
    heading, but it matches the heading regex. Counting those invents phantom
    slugs, which both lets a genuinely broken anchor pass and -- if a comment
    ever collides with a real heading -- pushes that real heading to a ``-1``
    variant here but not on GitHub, failing a correct link.
    """
    out, in_fence = [], False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            out.append("")
        else:
            out.append("" if in_fence else line)
    return "\n".join(out)


def _slugs_for(text: str) -> set[str]:
    """Every anchor GitHub would generate, including -1 suffixes for duplicates."""
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    for _, heading in HEADING.findall(_strip_fenced_blocks(text)):
        base = _github_slug(heading)
        if not base:
            continue
        count = seen.get(base, 0)
        slugs.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    return slugs


@pytest.mark.parametrize("doc", CHECKED_DOCS)
def test_document_exists(doc: str) -> None:
    assert (REPO_ROOT / doc).is_file(), f"{doc} is referenced by the docs contract but missing."


@pytest.mark.parametrize("doc", CHECKED_DOCS)
def test_relative_links_resolve(doc: str) -> None:
    path = REPO_ROOT / doc
    text = path.read_text()
    failures: list[str] = []

    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part = target.split("#", 1)[0]
        if not file_part:
            continue
        resolved = (path.parent / file_part).resolve()
        if not resolved.exists():
            failures.append(f"  {target}  ->  {resolved}")

    assert not failures, "{} has {} unresolvable relative link(s):\n{}".format(
        doc, len(failures), "\n".join(failures)
    )


@pytest.mark.parametrize("doc", CHECKED_DOCS)
def test_same_document_anchors_resolve(doc: str) -> None:
    path = REPO_ROOT / doc
    text = path.read_text()
    slugs = _slugs_for(text)

    failures = [
        f"  #{target[1:]}"
        for target in LINK.findall(text)
        if target.startswith("#") and target[1:].lower() not in slugs
    ]

    assert not failures, (
        "{} has {} anchor(s) with no matching heading:\n{}\n"
        "Available: {}".format(doc, len(failures), "\n".join(failures), ", ".join(sorted(slugs)))
    )


@pytest.mark.parametrize("doc", CHECKED_DOCS)
def test_cross_document_anchors_resolve(doc: str) -> None:
    path = REPO_ROOT / doc
    text = path.read_text()
    failures: list[str] = []

    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")) or "#" not in target:
            continue
        file_part, _, anchor = target.partition("#")
        resolved = (path.parent / file_part).resolve()
        if resolved.suffix.lower() != ".md" or not resolved.is_file():
            continue
        if anchor.lower() not in _slugs_for(resolved.read_text()):
            failures.append(f"  {target}")

    assert not failures, "{} points at {} missing heading(s) in another file:\n{}".format(
        doc, len(failures), "\n".join(failures)
    )


def test_every_screenshot_slot_resolves() -> None:
    """An unfilled slot must point at the committed placeholder, never nowhere."""
    failures: list[str] = []
    for doc in CHECKED_DOCS:
        path = REPO_ROOT / doc
        for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", path.read_text()):
            if target.startswith(("http://", "https://")):
                continue
            if not (path.parent / target.split("#")[0]).resolve().exists():
                failures.append(f"  {doc}: {target}")

    assert not failures, (
        "Image reference(s) resolve to nothing, so they render as a broken-image "
        "glyph:\n{}\nPoint unfilled slots at docs/screenshots/_placeholder.png.".format(
            "\n".join(failures)
        )
    )
