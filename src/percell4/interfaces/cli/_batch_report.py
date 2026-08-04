"""Shared helpers for batch-* CLIs (rename, delete).

Both ``percell4-batch-rename`` and ``percell4-batch-delete`` produce
the same shape of report — a list of per-dataset items, each with
status + processed/skipped/errors collections — and want to print
them the same way modulo the verb ("renamed" vs "deleted"). This
module is the one place those helpers live so the two CLIs stay in
lockstep.

``percell4-batch-phasor`` predates this module and keeps its in-file
copies of these helpers for now. A follow-up could consolidate, but
the duplication is small and stable enough that it isn't worth a
breaking refactor on a different CLI's branch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from percell4.io.paths import is_sidecar, scan_files


def resolve_paths(args: list[str]) -> list[Path]:
    """Expand positional arguments into a flat list of .h5 file paths.

    Each argument can be either an ``.h5`` file path or a directory; if
    a directory, every ``*.h5`` immediately under it is included
    (non-recursive). Order is preserved: file args land in argv order,
    directory globs sort alphabetically.
    """
    paths: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            paths.extend(scan_files(p, "*.h5"))
        elif not is_sidecar(p):
            paths.append(p)
    return paths


def format_item_line(item: Any, *, verb: str = "processed") -> str:
    """One-line per-dataset summary.

    ``verb`` swaps the count noun so the same machinery prints
    "1 renamed" for the rename CLI and "1 deleted" for the delete CLI.
    Other batch CLIs that adopt this helper pick their own verb.
    """
    n_p = len(item.processed)
    n_s = len(item.skipped)
    n_e = len(item.errors)
    return (
        f"[{item.status}] {item.h5_path.name} -- "
        f"{n_p} {verb}, {n_s} skipped, {n_e} errors"
    )


def print_item_status(
    item: Any, *, quiet: bool = False, verb: str = "processed",
) -> None:
    """Print one dataset's result to stdout.

    Always prints the one-line summary. When ``quiet`` is False, also
    prints per-channel skip / error / dataset-level error details
    indented underneath.
    """
    print(format_item_line(item, verb=verb))
    if quiet:
        return
    if getattr(item, "error", None):
        print(f"    error: {item.error}")
    for channel, reason in item.skipped.items():
        print(f"    {channel} skipped: {reason}")
    for channel, msg in item.errors.items():
        print(f"    {channel} error: {msg}")
