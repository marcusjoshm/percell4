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

# A negative numeric argument value looks like a flag to a naive tokenizer.
_NEGATIVE_NUMBER = re.compile(r"-\d+(\.\d+)?")


class _ParserCapturedError(Exception):
    """Raised inside the patched ``parse_args`` to stop before the command runs."""


def _console_scripts() -> dict[str, str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return data["project"]["scripts"]


def _gui_scripts() -> dict[str, str]:
    """``[project.gui-scripts]`` -- installed commands too, but not argparse CLIs.

    ``percell4-gui`` launches the Qt app and takes no flags, so it is a valid
    thing to see in a documented example while being outside the flag contract.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return data["project"].get("gui-scripts", {})


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
GUI_SCRIPTS = _gui_scripts()


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


EXAMPLE_DOCS = (
    "README.md",
    "docs/cli.md",
    "docs/installation.md",
    "docs/workflow-protocol.md",
)


def _example_invocations(text: str) -> list[tuple[str, list[str]]]:
    """Every ``percell4-*`` invocation inside a fenced block, as (command, flags).

    Joins backslash continuations and drops trailing comments so a wrapped,
    annotated example is read as one command.
    """
    invocations: list[tuple[str, list[str]]] = []
    in_fence = False
    pending = ""
    for raw in text.splitlines():
        if re.match(r"^\s*(```|~~~)", raw):
            in_fence = not in_fence
            pending = ""
            continue
        if not in_fence:
            continue
        line = re.sub(r"\s+#.*$", "", raw).rstrip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        full = (pending + line).strip()
        pending = ""
        tokens = full.split()
        if not tokens or not tokens[0].startswith("percell4-"):
            continue
        flags = []
        for token in tokens[1:]:
            # usage synopses wrap alternatives in shell metacharacters:
            # `(--name NAME | --all)`, `[--quiet]`, `--kind {a,b}`
            cleaned = token.strip("()[]{}|,").split("=")[0]
            if not cleaned.startswith("-"):
                continue
            if _NEGATIVE_NUMBER.fullmatch(cleaned):
                continue  # a negative value, e.g. --cellprob-threshold -1.0
            flags.append(cleaned)
        invocations.append((tokens[0], flags))
    return invocations


@pytest.mark.parametrize("doc", EXAMPLE_DOCS)
def test_example_invocations_use_real_flags(doc: str) -> None:
    """Every documented example must be runnable.

    The option tables were already guarded, but the worked examples were not --
    and that is precisely where two broken commands survived into the very
    change that added the drift guard. A reader tries the Quickstart first.

    Scope limit worth knowing: this checks that every flag in an example *exists*
    on that parser. It cannot catch a *combination* rule enforced at runtime
    rather than by argparse -- ``--strategy adaptive-clip`` requiring
    ``--d-min-um``, for instance, is a hand-rolled check inside ``main()``.
    Catching those would mean executing the commands against real datasets.
    """
    path = REPO_ROOT / doc
    if not path.is_file():
        pytest.skip(f"{doc} not present")

    failures: list[str] = []
    for command, flags in _example_invocations(path.read_text()):
        if command in GUI_SCRIPTS:
            continue  # installed, but not an argparse CLI
        if command not in SCRIPTS:
            failures.append(f"  {command}: not a declared entry point in pyproject.toml")
            continue
        real = _parser_flags(_capture_parser(SCRIPTS[command])) | AUTO_FLAGS
        for flag in flags:
            if flag not in real:
                failures.append(f"  {command}: {flag} is not a flag this parser accepts")

    assert not failures, "{} contains {} example(s) that would fail if run:\n{}".format(
        doc, len(failures), "\n".join(failures)
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
