"""Runtime catalog of installed ``percell4-*`` batch CLI tools.

Qt-free helper for the Batch Tools Console. Enumerates the importable
``percell4-*`` console entry points and resolves a typed command line into
an executable argv that runs the tool in the *current* interpreter's
virtual environment (via ``python -m <module>``), independent of ``PATH``.

Kept free of Qt/napari/h5py by convention so it stays cheaply importable
and unit-testable. No import-linter contract covers ``percell4.interfaces``
today; a ``forbidden`` contract on this module could enforce the rule.
"""

from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass
from importlib import util as importlib_util
from importlib.metadata import entry_points

_PREFIX = "percell4-"


class CommandParseError(ValueError):
    """The typed command line could not be tokenized (e.g. unbalanced quotes)."""


class UnknownCommandError(ValueError):
    """The first token is not an importable ``percell4-*`` catalog tool."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name!r} is not a percell4-* batch tool")


@dataclass(frozen=True)
class BatchTool:
    """One importable ``percell4-*`` console entry point.

    ``name`` is the console-script name (``percell4-batch-export``);
    ``module`` is the import path of its ``:main`` target
    (``percell4.interfaces.cli.batch_export``).
    """

    name: str
    module: str
    summary: str = ""


def _console_entry_points() -> list:
    # entry_points(group=...) returns an empty selection (not an error)
    # when the package/group is absent, so a broad guard is enough.
    try:
        return list(entry_points(group="console_scripts"))
    except Exception:
        return []


def _is_importable(module: str) -> bool:
    try:
        return importlib_util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def list_batch_tools() -> list[BatchTool]:
    """Return the importable ``percell4-*`` console scripts, sorted by name.

    Installed metadata can drift from source: an entry may point at a module
    that was later deleted (a "phantom" tool). Filtering by
    ``importlib.util.find_spec`` drops those, so the console never lists a
    tool that would die at spawn with ``No module named``.
    """
    tools: dict[str, BatchTool] = {}
    for ep in _console_entry_points():
        if not ep.name.startswith(_PREFIX):
            continue
        module = ep.module  # module part of "module:attr"
        if not _is_importable(module):
            continue
        tools[ep.name] = BatchTool(name=ep.name, module=module)
    return sorted(tools.values(), key=lambda t: t.name)


def split_command(line: str) -> list[str]:
    """Tokenize a typed command line, preserving native Windows backslash paths.

    POSIX ``shlex`` treats backslash as an escape character, which silently eats
    the separators in a typed Windows path (``C:\\data\\x.h5`` tokenizes to
    ``C:datax.h5``). On Windows, tokenize with escaping and ``#``-comment
    handling disabled so *both* raw backslash paths and quoted paths — the file
    navigator and drag-drop ``shlex.quote`` their inserts — tokenize correctly.
    On POSIX, plain ``shlex.split``. Raises ``ValueError`` on unbalanced quotes
    on either platform.
    """
    if os.name == "nt":
        lex = shlex.shlex(line, posix=True)
        lex.whitespace_split = True
        lex.escape = ""
        lex.commenters = ""
        return list(lex)
    return shlex.split(line, posix=True)


def resolve_command(line: str) -> list[str]:
    """Resolve a typed command line into an executable argv.

    Tokenizes ``line`` the way a shell would split it — but with no shell
    features (no pipes, redirects, globbing, or substitution) and preserving
    native Windows paths (see :func:`split_command`) — verifies the first token
    is an importable ``percell4-*`` catalog tool, and returns
    ``[sys.executable, "-m", <module>, *rest]``.

    Raises :class:`CommandParseError` on unbalanced quotes and
    :class:`UnknownCommandError` when the first token is empty or not a catalog
    tool.
    """
    try:
        argv = split_command(line)
    except ValueError as exc:
        raise CommandParseError(str(exc)) from exc
    if not argv:
        raise UnknownCommandError("")
    name, *rest = argv
    module = _module_for(name)
    if module is None:
        raise UnknownCommandError(name)
    return [sys.executable, "-m", module, *rest]


def _module_for(name: str) -> str | None:
    for tool in list_batch_tools():
        if tool.name == name:
            return tool.module
    return None
