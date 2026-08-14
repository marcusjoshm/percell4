"""docs/cli.md must document exactly the flags the CLI parsers actually define.

This is the regression guard for the largest class of documentation drift this
project has had: a flag added to a CLI module and never written down. A README
audit found two such flags (``--device``, ``--cnr-forced``) live and
undocumented, and two whole console scripts that shipped on every user's PATH
with no documentation at all.

Reaching a parser is the awkward part. Every entry point builds its
``ArgumentParser`` inside ``main()`` -- there is no module-level factory to
import -- and adding one purely for the tests would be production code shaped by
a test. Instead ``_capture_parser`` monkeypatches ``ArgumentParser.parse_args``
to record the constructed parser and abort, then calls ``main([])``. The parser
is fully built by the time ``parse_args`` runs, so the recorded object is
complete, and nothing past argument construction executes.

The doc-side contract this asserts, which ``docs/cli.md`` must keep:

* every command has an ``##`` heading whose first element is the exact console
  script name in backticks -- ``## `percell4-batch-export` -- TIFF export``
* every flag appears as an inline code span in the first column of that
  command's option table

A flag named only in prose or in a worked example does not count as documented,
because a reader scanning the option table would not find it.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DOC = REPO_ROOT / "docs" / "cli.md"

# argparse adds these to every parser; documenting them in 14 tables would be
# noise, so they are excluded from the contract in both directions.
AUTO_FLAGS = frozenset({"-h", "--help"})


class _ParserCapturedError(Exception):
    """Raised inside the patched ``parse_args`` to stop before the command runs."""


def _console_scripts() -> dict[str, str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return data["project"]["scripts"]


def _capture_parser(target: str) -> argparse.ArgumentParser:
    """Build the parser a console-script target constructs, without running it."""
    module_name = target.split(":")[0]
    captured: dict[str, argparse.ArgumentParser] = {}
    original = argparse.ArgumentParser.parse_args

    def _spy(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["parser"] = self
        raise _ParserCapturedError

    argparse.ArgumentParser.parse_args = _spy  # type: ignore[method-assign]
    try:
        module = importlib.import_module(module_name)
        # main([]) with no arguments: argparse may exit on missing required
        # positionals, which is fine -- the parser is already built by then.
        with contextlib.suppress(_ParserCapturedError, SystemExit):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                module.main([])
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[method-assign]

    parser = captured.get("parser")
    if parser is None:
        raise AssertionError(
            f"{target} never called parse_args, so its flags cannot be checked. "
            "If the module stopped using argparse, this test needs updating."
        )
    return parser


def _parser_flags(parser: argparse.ArgumentParser) -> set[str]:
    return {
        option
        for action in parser._actions  # noqa: SLF001 -- the only way to enumerate flags
        for option in action.option_strings
        if option not in AUTO_FLAGS
    }


def _doc_sections() -> dict[str, str]:
    """Map console-script name -> the body of its ``##`` section in docs/cli.md."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in CLI_DOC.read_text().splitlines():
        heading = re.match(r"^##\s+`([^`]+)`", line)
        if heading:
            current = heading.group(1)
            sections[current] = []
        elif line.startswith("## "):
            current = None  # a non-command section, e.g. "## Command index"
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(body) for name, body in sections.items()}


def _documented_flags(section_body: str) -> set[str]:
    """Flags in the first column of any option table in this section."""
    flags: set[str] = set()
    for first_column in re.findall(r"^\|([^|]*)\|", section_body, re.MULTILINE):
        for span in re.findall(r"`(-[^`]*)`", first_column):
            # a cell may read `--smallest-particle-unit {um,px}` or `--out=PATH`
            flags.add(span.split()[0].split("=")[0])
    return flags - AUTO_FLAGS


SCRIPTS = _console_scripts()


def test_cli_doc_exists() -> None:
    assert CLI_DOC.is_file(), f"{CLI_DOC} is missing; the CLI reference is the contract here."


@pytest.mark.parametrize("command", sorted(SCRIPTS))
def test_every_console_script_has_a_section(command: str) -> None:
    sections = _doc_sections()
    assert command in sections, (
        f"{command} is declared in [project.scripts] but has no `## `{command}`` section "
        f"in docs/cli.md. It installs on every user's PATH, so it needs documenting "
        f"(or removing from the entry points)."
    )


@pytest.mark.parametrize("command", sorted(SCRIPTS))
def test_documented_flags_match_the_parser(command: str) -> None:
    parser = _capture_parser(SCRIPTS[command])
    real = _parser_flags(parser)
    documented = _documented_flags(_doc_sections()[command])

    undocumented = sorted(real - documented)
    assert not undocumented, (
        f"{command} accepts {undocumented} but docs/cli.md does not list them in its "
        f"option table. Add a row per flag, or the next reader will not know it exists."
    )

    phantom = sorted(documented - real)
    assert not phantom, (
        f"docs/cli.md documents {phantom} for {command}, but the parser has no such flag. "
        f"A user copying the documented invocation gets an argparse error."
    )
