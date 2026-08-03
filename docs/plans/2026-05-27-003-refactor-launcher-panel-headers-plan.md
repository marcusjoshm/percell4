---
title: Unify launcher panel header style
type: refactor
status: completed
date: 2026-05-27
---

# Unify launcher panel header style

## Overview

Five of the eight launcher task panels (I/O, Segment, Analysis, FLIM, Data) currently render their title label with a 1 px `border-bottom` beneath bold white text. The other three (Viewer, Scripts, Workflows) use `LauncherWindow._section_label`, which produces the same bold white text with `border: none`. The five outliers are visually inconsistent and the stylesheet is duplicated across five files.

This plan lifts the existing `_section_label` helper into `src/percell4/gui/theme.py` as a public `section_label(text)` factory and updates all six call sites (the three already-correct panels plus the five outliers) to use it. Net effect: every launcher panel uses bold white text with no bottom border, and the styling lives in one place.

---

## Problem Frame

User-reported visual inconsistency: the I/O / Segment / Analysis / FLIM / Data panels show a thin bottom border beneath their title, while Viewer / Scripts / Workflows do not. The user wants them all to match the no-border style. The repo already has a helper for the no-border style — it just isn't shared. Fixing the inconsistency by editing five stylesheets in place would replicate the same drift; consolidating the helper closes the loop.

---

## Requirements Trace

- R1. The I/O, Segment, Analysis, FLIM, and Data panel headers render as bold white text with `border: none` — matching Viewer, Scripts, and Workflows.
- R2. The shared header factory lives in `src/percell4/gui/theme.py` alongside the other dark-theme constants and helpers (per the project's gui/CLAUDE.md: "Every GUI file imports constants from here; no hardcoded hex colors elsewhere.").
- R3. No functional/behavioral change. Window layouts, header spacing (`margin-bottom: 12px`), and font sizing (18 px) are preserved.

---

## Scope Boundaries

- Only the top-of-panel section header label is touched. The `QGroupBox` sub-section headers ("Cellpose", "Tracking (time-lapse)", "Phasor Analysis", etc.) are NOT changed — those use the existing groupbox style and are working as intended.
- No other styling cleanup in this pass (button styles, group borders, status bar, etc.).
- No test infrastructure for visual regression — the project has none today and adding it for this is overkill.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/interfaces/gui/main_window.py::LauncherWindow._section_label` (line 812) — the existing helper, currently a `@staticmethod` returning a `QLabel` styled `font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT}; margin-bottom: 12px; border: none; background: transparent;`. Used by the Viewer (line 268), Scripts (line 319), and Workflows (line 336) panel constructors. **This is the target style.**
- `src/percell4/gui/theme.py` — centralized dark-theme module per `src/percell4/gui/CLAUDE.md` ("centralized dark-theme constants (`BACKGROUND`, `TEXT`, `ACCENT`, etc.) and the global Fusion-style stylesheet. Every GUI file imports constants from here; no hardcoded hex colors elsewhere"). The natural home for a shared `section_label()` factory.
- Five panels with the drifted "boxed" style (each carries an identical stylesheet snippet `font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT}; margin-bottom: 12px; padding-bottom: 4px; border-bottom: 1px solid {theme.BORDER};`):
  - `src/percell4/interfaces/gui/task_panels/io_panel.py` (line 62, title `"Import / Export"`)
  - `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (line 78, title `"Analysis"`)
  - `src/percell4/interfaces/gui/task_panels/data_panel.py` (line 111, title `"Data"`)
  - `src/percell4/interfaces/gui/task_panels/flim_panel.py` (line 83, title `"FLIM"`)
  - `src/percell4/gui/segmentation_panel.py` (line 102, title `"Segmentation"`) — note this one lives under `gui/`, not `interfaces/gui/task_panels/`.

### Institutional Learnings

- None directly applicable (`docs/solutions/` has no entries on stylesheet drift).

---

## Key Technical Decisions

- **Lift to `theme.py`, not a new `widgets.py`.** `theme.py` already owns the cross-cutting style constants and is imported by every GUI file. Adding a `section_label(text: str) -> QLabel` factory there keeps "things that produce themed widgets" in one place without a new module.
- **Delete `LauncherWindow._section_label` and route all three of its call sites through the new helper.** Otherwise the helper has two homes — the new one and the legacy private method — and a future contributor could grow the divergence again. The three internal call sites (Viewer/Scripts/Workflows) all just import `theme` and call `theme.section_label("…")` instead.
- **Keep the stylesheet inline inside the factory**, not a separate constant. The existing `_section_label` already inlines it; the factory does the same. A `SECTION_LABEL_STYLESHEET` constant would add a hop with no payoff.
- **Single PR / single branch.** The change is small and self-contained; phased delivery would create more friction than it removes.

---

## Open Questions

### Resolved During Planning

- **Lift the helper or just edit five stylesheets in place?** Lifted. Editing in place would re-create the drift the next time someone adds a panel; a shared helper closes the loop.
- **Where does the helper live — `theme.py`, `_widgets.py`, or as a free function in `main_window.py`?** `theme.py`. Matches the existing "GUI styling lives in theme" convention from `src/percell4/gui/CLAUDE.md`.
- **Keep `LauncherWindow._section_label` as a thin wrapper or delete it?** Delete; route Viewer/Scripts/Workflows directly to `theme.section_label`.

### Deferred to Implementation

- The exact public name in `theme.py` — `section_label`, `panel_header`, `panel_title_label`. Pick during implementation based on what reads cleanest at the call sites.

---

## Implementation Units

- U1. **Add `section_label()` factory to `theme.py`**

**Goal:** Lift the existing `_section_label` stylesheet into `src/percell4/gui/theme.py` as a public `section_label(text) -> QLabel` factory. Replace `LauncherWindow._section_label` (and its three call sites in Viewer/Scripts/Workflows panel constructors) to use the new factory. No visual change from this unit alone — the three already-correct panels stay pixel-identical.

**Requirements:** R2

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/gui/theme.py` (add factory)
- Modify: `src/percell4/interfaces/gui/main_window.py` (delete `_section_label`, update Viewer/Scripts/Workflows call sites)

**Approach:**
- Add `section_label(text: str) -> QLabel` to `theme.py`. Body mirrors the existing `LauncherWindow._section_label` exactly: stylesheet `font-size: 18px; font-weight: bold; color: {TEXT_BRIGHT}; margin-bottom: 12px; border: none; background: transparent;`. Imports stay top-of-module (`QLabel` from `qtpy.QtWidgets`).
- In `main_window.py`: remove the `_section_label` static method (line 812 region). Replace the three call sites (`layout.addWidget(self._section_label("Viewer"))`, `…("Scripts")`, `…("Workflows")`) with `layout.addWidget(theme.section_label("Viewer"))` etc. The `from percell4.gui import theme` import is already at module top — no new import needed.

**Patterns to follow:**
- `src/percell4/gui/theme.py` — existing module-level constants and any helper-function patterns there.
- `main_window.py::_section_label` (lines 812–820) — the exact stylesheet body to lift.

**Test scenarios:**
- Test expectation: none -- pure refactor with no visual or behavioral change. Verify by launching `percell4-gui`, clicking the Viewer / Scripts / Workflows sidebar entries in turn, and confirming the headers render identically to the pre-change state. The U2 visual change happens in the next commit.

**Verification:**
- `theme.section_label("X")` returns a `QLabel` whose text is `"X"` and whose styleSheet matches the original `_section_label` output verbatim.
- `LauncherWindow._section_label` is gone (grep returns no matches).
- Launching `percell4-gui` shows Viewer / Scripts / Workflows panels unchanged from before this commit.

---

- U2. **Switch the five boxed panels to `theme.section_label`**

**Goal:** Replace the duplicated boxed-header stylesheet block in I/O, Analysis, Data, FLIM, and Segment panel constructors with `theme.section_label(...)`. This is the user-visible change: the underline disappears from the five panel headers, matching the existing Viewer / Scripts / Workflows look.

**Requirements:** R1, R3

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/io_panel.py`
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py`
- Modify: `src/percell4/interfaces/gui/task_panels/data_panel.py`
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
- Modify: `src/percell4/gui/segmentation_panel.py`

**Approach:**
- In each of the five files, locate the title-construction block (the `title = QLabel("…")` line followed by the `title.setStyleSheet(...)` call with `border-bottom: 1px solid {theme.BORDER}`). Replace those two-to-three lines with `title = theme.section_label("<panel name>")`. The `layout.addWidget(title)` line stays unchanged. The lazy `from percell4.gui import theme` import that already wraps these blocks stays (or remains hoisted to top of file if it's already there).
- The string literals preserved per panel: `"Import / Export"` (io_panel), `"Segmentation"` (segmentation_panel), `"Analysis"` (analysis_panel), `"FLIM"` (flim_panel), `"Data"` (data_panel).
- Do NOT touch the inner `QGroupBox("Cellpose")`, `QGroupBox("Phasor Analysis")`, etc. — those are sub-section headers with the intentional groupbox border. They are out of scope.

**Patterns to follow:**
- U1's new `theme.section_label` factory.
- The existing usage shape in `main_window.py` after U1 (`layout.addWidget(theme.section_label("Viewer"))`).

**Test scenarios:**
- Test expectation: none -- pure styling change with no behavioral impact. Verify by launching `percell4-gui` and clicking through all eight sidebar entries (I/O, Viewer, Segment, Analysis, FLIM, Scripts, Workflows, Data). All eight headers should render as bold white text with no underline, no surrounding box, and no other visual differences from the prior Viewer/Scripts/Workflows headers.

**Verification:**
- The five panels' titles render visually identical to Viewer / Scripts / Workflows (bold white text, no underline, same margin below).
- `grep -rn "border-bottom: 1px solid" src/percell4/` returns zero matches inside the five panel files for the title-block region (the constant `{theme.BORDER}` may still appear elsewhere for `QGroupBox` styling — that's fine).
- No regression in tests that pytest-qt-construct any of these panels (existing `tests/test_gui/` suite continues to pass with no edits required, since the title text strings and layout order are preserved).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| The lazy `from percell4.gui import theme` import inside the constructor body becomes unused after the stylesheet block is replaced (because `theme.BORDER` is no longer referenced). | The import stays — it's already used elsewhere in the panel constructors (e.g., `theme.ACCENT`, `theme.TEXT`). If a panel genuinely no longer references `theme` after U2, the unused-import shows up cleanly in linting; remove it then. |
| A future contributor re-introduces a boxed header by copy-pasting from a pre-refactor commit. | The shared `theme.section_label` exists as the obvious one-line answer to "how do I make a panel header?"; the duplicated stylesheet block is gone from every file the next contributor would copy from. Low residual risk. |
| Other (non-launcher) widgets in the repo use a similar `font-size: 18px; font-weight: bold; border-bottom: ...` stylesheet pattern and would inconsistently keep their border after this change. | The grep in Verification (U2) flags any remaining `border-bottom: 1px solid` matches in `src/percell4/`. Out of scope to refactor those in this pass, but the grep makes them visible if the user wants a follow-up. |

---

## Sources & References

- Related code:
  - `src/percell4/interfaces/gui/main_window.py::_section_label` (the target style)
  - `src/percell4/gui/theme.py` (new home for the helper)
  - `src/percell4/gui/CLAUDE.md` (explicitly mandates theme-as-single-source for styling)
  - The five panel files listed in U2 (current divergent style)
