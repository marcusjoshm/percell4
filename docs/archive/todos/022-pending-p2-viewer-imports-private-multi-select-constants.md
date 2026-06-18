---
status: pending
priority: p2
issue_id: "022"
tags: [code-review, architecture, dependency-direction, multi-select]
dependencies: []
---

# `ViewerWindow` imports private `_OVERLAY_LAYER_NAME` / `_STAGED_COLOR` from `multi_select.py`

## Problem Statement

`ViewerWindow` reaches into the multi-select feature module's private names:

```python
# viewer.py:471-474, 513-516, 530
from percell4.gui.multi_select import (
    _OVERLAY_LAYER_NAME,
    _STAGED_COLOR,
)
```

This inverts the intended dependency. The `StagedRenderer` Protocol (`multi_select.py:65`) was supposed to be the seam: `multi_select` depends on `viewer` via the protocol, `viewer` knows nothing about multi_select internals. Instead, `viewer.py` now imports feature-specific leading-underscore constants at three call sites — a circular-ish coupling where the tail wags the dog.

Symptoms:
- Lazy imports hide the dependency from static analysis.
- The Protocol no longer buys decoupling; if a second modal tool ever appears, it won't be able to reuse these overlay primitives without colliding.
- `_OVERLAY_LAYER_NAME` is private (leading underscore), signaling internal-to-multi-select — violated by cross-module import.

## Findings

- **kieran-python-reviewer (P2-3):** "Layer-import contract breaks the ViewerWindow encapsulation."
- **architecture-strategist (P3):** "Lazy imports reach back across modules"; hide dependency from static analysis.
- **pattern-recognition-specialist (P2 #16):** `ViewerWindow` leaks multi-select concepts despite the Protocol being meant to prevent exactly this.

## Proposed Solutions

### Option A — Move constants into `viewer.py` (Recommended)

They describe what the overlay looks like — a rendering concern, not a state concern. Drop the leading underscore on the viewer side.

- Define `STAGED_OVERLAY_LAYER_NAME: Final = "_multi_select_staged"` and `STAGED_OVERLAY_COLOR: Final[tuple[float, float, float, float]] = (0.0, 1.0, 1.0, 0.7)` (or whatever the current tuple is) at the top of `viewer.py`.
- Delete the imports at `viewer.py:471, 513, 530`.
- Delete the constants at `multi_select.py:61-62`.

- **Pros:** Smallest diff; eliminates import entirely.
- **Cons:** Binds the color to the viewer (cannot customize per-tool), but that's not currently a feature.
- **Effort:** Small (15 min).
- **Risk:** None.

### Option B — Pass color + name through the Protocol signature

Change `StagedRenderer.add_staged_overlay(ids)` → `add_staged_overlay(ids, *, color, name)`. Controller owns policy; viewer owns mechanics.

- **Pros:** Purer Protocol seam; future modal tools could reuse with different colors/names.
- **Cons:** Wider Protocol; no current second consumer.
- **Effort:** Small-Medium.
- **Risk:** Low.

## Recommended Action

Option A. If a second modal tool appears, upgrade to Option B then.

## Technical Details

**Affected files:**
- `src/percell4/gui/viewer.py` (lines 471-474, 513-516, 530, plus new module-top constants)
- `src/percell4/gui/multi_select.py` (lines 61-62 constants deleted)

## Acceptance Criteria

- [ ] `grep -rn "from percell4.gui.multi_select import" src/` returns zero results
- [ ] `multi_select.py` imports from `viewer` only via `StagedRenderer` Protocol / TYPE_CHECKING
- [ ] All 29 multi_select tests still pass
- [ ] Overlay renders cyan in a manual smoke test

## Work Log

- 2026-04-23 — Flagged by kieran-python-reviewer + architecture-strategist + pattern-recognition-specialist.

## Resources

- Protocol definition: `src/percell4/gui/multi_select.py:65` (`StagedRenderer`)
