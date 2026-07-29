---
status: pending
priority: p2
issue_id: "026"
tags: [code-review, ux, multi-select, sibling-convention]
dependencies: []
---

# Accept button lacks `setDefault(True)` and `ACTION_GREEN` styling; Enter does nothing

## Problem Statement

The multi-select dock's Accept button (`multi_select.py:375`) has a tooltip but no visual emphasis and no `setDefault(True)`. Sibling controllers do both:

- `gui/workflows/single_cell/seg_qc.py:298` — `btn_accept.setDefault(True)` so Enter accepts when the dock has focus.
- `gui/threshold_qc.py:310-312, 510-512` — `theme.ACTION_GREEN` background, white text, bold on the primary action button.

Consequences:
1. **Enter-key UX regression vs. siblings.** In multi-select, the user must press `Ctrl+Return`; plain Enter does nothing. In seg_qc the user can press either. Undocumented difference.
2. **No visual primacy.** Cancel and Accept look identical — the user must read labels to find the primary action.

The PR body's claim that "Accept matches `seg_qc.py:297`" is accurate only for the text label, not for the button's styling or default-button status.

## Findings

- **pattern-recognition-specialist (P2 #5, #6):** Missing `setDefault(True)`; missing `theme.ACTION_GREEN`; Enter-key UX mismatch.

## Proposed Solutions

### Option A — Mirror `seg_qc.py` styling (Recommended)

```python
# multi_select.py around line 375
self._accept_button = QPushButton("Accept")
self._accept_button.setDefault(True)
self._accept_button.setStyleSheet(
    f"background-color: {theme.ACTION_GREEN}; color: white; font-weight: bold;"
)
```

- **Pros:** Matches house style exactly; restores Enter-accepts UX; minimal change.
- **Cons:** None.
- **Effort:** Trivial (5 min).
- **Risk:** None.

### Option B — Leave plain, document the Ctrl+Return-only UX

- **Pros:** Zero diff.
- **Cons:** Cements inconsistent UX.
- **Effort:** Trivial.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py:375`
- Import `theme` constants if not already (check existing imports)

## Acceptance Criteria

- [ ] Accept button uses `ACTION_GREEN` background
- [ ] Accept button has `setDefault(True)` set
- [ ] Pressing plain `Enter` when the dock has focus accepts (verify manually)
- [ ] Ctrl+Return still accepts (existing shortcut preserved)

## Work Log

- 2026-04-23 — Flagged by pattern-recognition-specialist.

## Resources

- Sibling convention: `src/percell4/gui/workflows/single_cell/seg_qc.py:298, 194`
- Sibling convention: `src/percell4/gui/threshold_qc.py:310-312, 510-512`
