"""Bin-aware resource naming helper.

When a Creator (segmentation, mask, measurement, phasor result) is
produced at a non-default view bin (``session.active_bin != 1``), the
resulting layer carries a ``_bin<k>`` suffix so it can coexist with a
``k == 1`` peer of the same logical name -- e.g., ``cellpose`` (k=1) and
``cellpose_bin3`` (k=3) live in the same ``.h5`` without collision.

This module owns the *only* place in the codebase where the literal
``_bin<digits>`` suffix is computed. Callers pass a clean base name plus
the active bin; the helper produces the canonical resource name. Stripping
an already-present suffix before re-applying makes it idempotent against
callers that pass the *last-used* name (e.g., the resource-name prompt
re-seeded from the previous selection).
"""

from __future__ import annotations

import re

_BIN_SUFFIX_RE = re.compile(r"_bin\d+$")


def bin_suffix(name: str, k: int) -> str:
    """Return ``name`` annotated with the view-bin suffix.

    Rules:
      * ``k == 1`` returns ``name`` unchanged (after stripping any
        trailing ``_bin<digits>`` that may have come from the last-used
        name).
      * ``k > 1`` returns ``f"{base}_bin{k}"`` where ``base`` is ``name``
        with any trailing ``_bin<digits>`` stripped.
      * Raises ``ValueError`` on ``k < 1``.

    The strip step is intentional: callers commonly seed the helper with
    the last name the user picked (e.g., ``cellpose_bin3``) and expect
    ``bin_suffix("cellpose_bin3", 5)`` to produce ``cellpose_bin5``, not
    ``cellpose_bin3_bin5``. Only the trailing token is stripped, so
    legitimate names that contain the word "bin" elsewhere
    (``my_bin_name``) are unaffected.
    """
    if not isinstance(k, int) or isinstance(k, bool):
        raise ValueError(f"bin must be an int >= 1, got {k!r}")
    if k < 1:
        raise ValueError(f"bin must be >= 1, got {k}")
    base = _BIN_SUFFIX_RE.sub("", name)
    if k == 1:
        return base
    return f"{base}_bin{k}"
