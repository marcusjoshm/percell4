"""Shared name-prompt helper for "save new resource" Qt flows.

Used by every dialog/panel that asks the user for a name before writing
a new HDF5 resource (segmentation, mask, …). Encapsulates the validate-
and-re-prompt loop so all call sites behave identically:

- Default name pre-filled in the input.
- Empty submission → silently re-prompt with the original default.
- Collision with an existing name → warn and re-prompt with the
  conflicting name as the new default.
- Cancel → return ``None`` so the caller can early-out.

The helper is **resource-kind-agnostic**. Callers supply the dialog
title, label text, default, and the collision set. Domain knowledge
(``/labels/`` vs ``/masks/``, what "existing" means) stays at the call
site.
"""

from __future__ import annotations

from collections.abc import Iterable

from qtpy.QtWidgets import QInputDialog, QMessageBox, QWidget


def prompt_for_resource_name(
    parent: QWidget | None,
    *,
    title: str,
    label: str,
    default: str,
    existing_names: Iterable[str],
) -> str | None:
    """Modal text prompt for a new-resource name.

    Returns the user's chosen name (validated non-empty and not in
    ``existing_names``), or ``None`` if the user cancelled.

    The prompt re-fires on empty submission and on name collisions; the
    user can only escape the loop by typing a valid name or pressing
    Cancel. Whitespace-only inputs are treated as empty (per ``str.strip``).
    """
    existing_set = set(existing_names)
    current_default = default
    while True:
        name, ok = QInputDialog.getText(parent, title, label, text=current_default)
        if not ok:
            return None
        if name.strip() == "":
            # Re-prompt with the original default, not the blank value
            # the user just submitted.
            current_default = default
            continue
        if name in existing_set:
            QMessageBox.warning(
                parent,
                "Name in use",
                f"A resource named '{name}' already exists. "
                "Please enter a different name below.",
            )
            current_default = name
            continue
        return name
