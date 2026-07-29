---
title: "refactor: dialog-scroll-helper rollout"
type: refactor
status: active
date: 2026-04-30
origin: docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md
---

# refactor: dialog-scroll-helper rollout

> Thread `dialog-scroll-helper-rollout` from the codebase audit. Retires 5 cells in `docs/audits/canonical-sources-matrix.yaml` under the `dialog-scroll-when-tall` column and finalizes `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` from `pre_canonical` to `canonical_clean`.

## Overview

Five Qt dialogs in `src/percell4/gui/` independently re-implement (or omit) the "tall-dialog scroll wrapping + screen-bound resize" pattern. The user has been issuing the instruction "wrap this in a `QScrollArea`" repeatedly throughout the project's lifetime; it was never compounded into a canonical helper. This thread promotes the pattern to a single helper module, converges the four re-implementing dialogs, fixes the one drifting dialog (`export_images_dialog.py`, no scroll wrapper at all), and adds a compliance test so future dialogs cannot silently drift again.

The pattern is small (a few lines each), but the *point* of this thread is structural: a canonical-source column transitions from `pre_canonical` to `canonical_clean`, and the audit-matrix mechanics (PR-cites-cells, same-PR matrix update, same-PR `duplicates_at` reset) get exercised end-to-end on a low-risk thread before the bigger threads (`append-flow-plan-consumption`, `write-boundary-discipline`) land.

---

## Problem Frame

`docs/solutions/ui-bugs/dialog-scroll-when-tall.md` (authored by the audit pass) defines the convention:

1. Wrap primary dialog content in `QScrollArea` with `setWidgetResizable(True)` and `setFrameShape(QScrollArea.NoFrame)`.
2. Cap dialog max height to ≤ 0.9 of the parent's `screen().availableGeometry().height()`.

Today five dialogs each handle this differently:

| Dialog | Scroll wrapper | Screen-bound resize | Matrix cell |
|---|---|---|---|
| `gui/add_layer_dialog.py` | per-tab (lines 172, 806) | yes (lines 71-77) | `re_implements` |
| `gui/import_dialog.py` | whole-content (lines 45-55) | no | `re_implements` |
| `gui/compress_dialog.py` | whole-content (lines 80-86) | no | `re_implements` |
| `gui/workflows/single_cell/config_dialog.py` | whole-content (lines 207-219) | no | `re_implements` |
| `gui/export_images_dialog.py` | none | no | `drifts_from_canonical` |

`add_layer_dialog.py` is the most complete pattern (it owns both halves) and the screen-bound resize block at lines 71-77 is currently the *only* implementation in the codebase. That block plus the tab-scroll wrapper together form the de-facto pattern; this thread codifies them as helpers.

---

## Requirements Trace

- R1. (origin R7 + audit thread) Canonicalize the scroll-when-tall pattern into a single shared helper that all five dialogs consume.
- R2. (origin R7 + drift fix) `gui/export_images_dialog.py` MUST gain both the scroll wrapper and the screen-bound resize. This is the only behavioral change; the four others are pure refactors.
- R3. (origin R12) Each PR cites the matrix cells it retires using the format `Closes drift: dialog-scroll-when-tall#<dup-id>`. Stable dup-ids are listed in U7 below.
- R4. (origin R13) The matrix YAML at `docs/audits/canonical-sources-matrix.yaml` is updated **in the same PR** as the migration that retires its cells.
- R5. (origin R14) `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` is updated in the same PR(s):
  - `canonical_source: TBD` → `src/percell4/gui/_dialog_utils.py` (set in U1's PR).
  - `duplicates_at` entries removed as each migration lands.
  - `status: pre_canonical` → `canonical_clean` when the last migration lands.
- R6. (solution-doc TODO) Add a unit test that asserts every `*Dialog.py` either uses the helper or carries a documented exemption — covered by U1's test scenarios.

**Origin actors:** A1 (User), A4 (`/ce-plan`), A5 (PerCell4 codebase).
**Origin acceptance examples:** AE2 ("a thread that consolidates 'scrollbar use across dialogs' transitions matrix cells from `re_implements` to `consumes_canonical`, updates `duplicates_at`, regenerates the rendered Markdown view").

---

## Scope Boundaries

- **Not in scope: changing dialog UX or layout semantics.** Migrations are mechanical — same widget tree, wrapped through the helper. No new fields, no reordered groups, no theme tweaks.
- **Not in scope: porting other patterns into helpers.** Other Qt boilerplate (button rows, file pickers) may also be candidates for canonicalization but belong to separate threads.
- **Not in scope: a `ScrollableDialog` base class.** See Key Technical Decisions for why the function-pair shape was chosen.
- **Not in scope: refactoring the per-tab pattern in `add_layer_dialog.py` into a tabbed-dialog helper.** The two tabs that need wrapping are local to that dialog; further generalization would be premature without a second tabbed-dialog drift site.
- **Not in scope: the inverse (forcing dialogs that don't need scrolling to use the helper).** Short dialogs that fit on screen have no obligation; the compliance test in U1 must accept "documented exemption" entries.

### Deferred to Follow-Up Work

- Render script `scripts/render_canonical_sources_matrix.py` is mentioned in origin R6 but not implemented yet. Out of scope here; the matrix YAML is the source of truth and the rendered Markdown can be produced by a follow-up thread.
- Promoting other GUI conventions (e.g., button row layout, file-picker row pattern) — separate threads if the audit later flags them.

---

## Context & Research

### Relevant Code and Patterns

- **Canonical-shape donor:** `src/percell4/gui/add_layer_dialog.py:71-77` (screen-bound resize) and `:172-184` / `:806-820` (per-tab scroll wrap). These are the two halves of the helper.
- **Dialogs to migrate:** `src/percell4/gui/import_dialog.py:44-56`, `src/percell4/gui/compress_dialog.py:77-87`, `src/percell4/gui/workflows/single_cell/config_dialog.py:200-220`, `src/percell4/gui/add_layer_dialog.py:71-77, 172-184, 806-820`.
- **Drift fix:** `src/percell4/gui/export_images_dialog.py:42-55` — currently `layout = QVBoxLayout(self)` with no scroll wrapper.
- **Theme + global stylesheet:** `src/percell4/gui/theme.py` — the helper does not touch the theme; just preserves `QScrollArea.NoFrame` so the scroll area inherits the dialog's background.

### Institutional Learnings

- `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` (authored 2026-04-30) — the canonical convention this thread closes. Currently `status: pre_canonical`; finalized by this thread.
- `docs/solutions/ui-bugs/ui-theme-refactor-lessons.md` — confirms the project's pattern of centralizing GUI conventions; precedent for the helper-module shape.

### External References

- Qt docs on `QScrollArea` — `setWidgetResizable(True)` is the load-bearing flag; without it the inner widget cannot grow with the scroll area. Existing donor pattern already does this; the helper preserves it.

---

## Key Technical Decisions

- **Helper shape: function pair, not base class.** Two free functions (`wrap_in_scroll(content)` and `cap_to_screen(dialog, fraction=0.9)`) over a `ScrollableDialog(QDialog)` base class because (a) `add_layer_dialog.py` applies the wrapper *per tab* (the inner widgets are `QWidget`, not `QDialog`), which a base class cannot express cleanly, (b) function pair composes — a dialog that wants only the scroll part or only the cap part can take one without the other, (c) base classes invite future-creep ("while I'm here, let me move the button row into the base class too…") whereas free functions stay scoped.
- **Module path: `src/percell4/gui/_dialog_utils.py`.** Leading underscore signals "internal to `gui/`". Sibling to `theme.py`, the existing single-file convention container.
- **`QScrollArea` import lives in the helper, not at each callsite.** The four migrating dialogs currently import `QScrollArea` inline inside `_build_ui` (e.g., `add_layer_dialog.py:172`). Migrations remove that local import.
- **`cap_to_screen` fraction defaulted to 0.9.** Matches the donor (`add_layer_dialog.py:74-75`). Configurable per call but the default is what every existing site uses.
- **`cap_to_screen` failure handling.** Donor at `add_layer_dialog.py:71-77` wraps the call in `try/except Exception` because the parent may not have `screen()` (test harness, no QApplication). Helper preserves that swallow-and-skip behavior — capping is best-effort, never the cause of a crash.
- **Compliance test as guardrail.** A test that imports each `*Dialog.py` and asserts it either calls `wrap_in_scroll` / `cap_to_screen` OR is in an explicit `EXEMPT_DIALOGS` set. Future dialogs that grow tall without using the helper fail CI. The exempt set documents WHY an exemption applies (e.g., "QFileDialog subclass, layout fixed by Qt").
- **PR sequencing: helper first, migrations after.** U1 (helper module + tests) lands as one PR. U2–U6 (migrations) can land as separate PRs or one bundled PR — implementer's choice. U7 (matrix + solution-doc finalization) lands with the *last* migration PR so `status: canonical_clean` flips only when zero drift remains.

---

## Open Questions

### Resolved During Planning

- *Helper shape (function pair vs base class)?* — Function pair. See Key Technical Decisions.
- *Module path?* — `src/percell4/gui/_dialog_utils.py`.
- *`cap_to_screen` default fraction?* — 0.9, matching the donor.
- *Should `add_layer_dialog.py`'s Single TIFF / ROIs / Cellpose tabs also gain scroll wrappers?* — No. They're short; the solution doc's convention is "any dialog that *can* grow taller than the screen" — not a blanket rule. Leave them.

### Deferred to Implementation

- *Stable `dup-id` slug format inside the matrix YAML.* The plan uses short slugs (`import-dialog`, `compress-dialog`, etc.); the implementer may shorten or adjust as long as the slugs are stable and `git log --grep "Closes drift: dialog-scroll-when-tall#"` enumerates them cleanly.
- *Whether to bundle U2–U6 into one PR or split per-dialog.* Five dialog migrations are mechanical and small; one bundled PR is plausible, but the implementer may split if the diff feels large or if the per-dialog tests need separate review.
- *Exact compliance-test detection mechanism.* Options: AST walk for `wrap_in_scroll(` / `cap_to_screen(` calls; instantiate each dialog and assert a `QScrollArea` exists in its child tree; pure import-time grep. AST walk is most robust; pick during U1 implementation.

---

## Implementation Units

- U1. **Create `gui/_dialog_utils.py` with helper functions and tests.**

  **Goal:** Land the canonical helper module and its compliance test before any migration.

  **Requirements:** R1, R6.

  **Dependencies:** None.

  **Files:**
  - Create: `src/percell4/gui/_dialog_utils.py`
  - Test: `tests/test_gui/test_dialog_utils.py` (new directory if needed)
  - Test: `tests/test_gui/test_dialog_helper_compliance.py`
  - Modify: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — set `canonical_source: src/percell4/gui/_dialog_utils.py` (still `status: pre_canonical` until U7).

  **Approach:**
  - Two functions: `wrap_in_scroll(content: QWidget) -> QScrollArea` and `cap_to_screen(dialog: QDialog, fraction: float = 0.9) -> None`.
  - `wrap_in_scroll` returns a `QScrollArea` with `setWidgetResizable(True)`, `setFrameShape(QScrollArea.NoFrame)`, `setWidget(content)`. Caller adds the returned scroll area to its outer layout.
  - `cap_to_screen` reads the dialog's parent's `screen().availableGeometry()` and applies `setMaximumHeight` / `setMaximumWidth` at the given fraction. Wrapped in `try/except Exception` to swallow the test-harness no-screen case (preserves donor behavior).
  - Compliance test: AST-walk every `src/percell4/gui/**/*Dialog.py` (and `*_dialog.py`); assert each file either calls `wrap_in_scroll(` or appears in an explicit `EXEMPT_DIALOGS` set with a one-line reason. Exemption set lives in the test module, not in production code.

  **Patterns to follow:**
  - Donor: `add_layer_dialog.py:71-77` (cap shape) and `:172-184` (wrap shape).
  - Module-organization sibling: `src/percell4/gui/theme.py` (single-file convention container, no class).

  **Test scenarios:**
  - Happy path: `wrap_in_scroll(QWidget)` returns a `QScrollArea`; the returned area's `widget()` is the input widget; `widgetResizable()` is `True`; `frameShape()` is `QScrollArea.NoFrame`.
  - Happy path: `cap_to_screen(dialog, 0.9)` on a dialog with a parent that has `screen()` sets `maximumHeight()` to ≈ 0.9 × screen height. Use `pytest-qt`'s `qtbot` to construct the dialog with a real-screen parent.
  - Edge case: `cap_to_screen(dialog, 0.5)` respects the custom fraction.
  - Error path: `cap_to_screen(dialog)` on a dialog whose parent lacks `screen()` does NOT raise; the dialog's `maximumHeight()` is unchanged from the Qt default.
  - Error path: `cap_to_screen(dialog)` on `parent=None` does NOT raise.
  - Compliance: `tests/test_gui/test_dialog_helper_compliance.py` discovers every `*Dialog.py` under `src/percell4/gui/`; for each file, asserts `wrap_in_scroll(` is present in source OR the file is in `EXEMPT_DIALOGS`. Currently fails (5 dialogs not yet migrated, none exempt) — the test is xfail-marked or skipped until U6 lands; remove the xfail in U7. Alternative: U1 lands the test in `skip` mode and U7 unskips it as part of finalization.
  - Compliance: A made-up `tests/fixtures/EvilDialog.py` with no scroll wrapper and not in the exempt set causes the compliance test to fail. (Validates the test's detection logic.)

  **Verification:**
  - `_dialog_utils.py` exists, the two functions are importable from `percell4.gui._dialog_utils`.
  - `pytest tests/test_gui/test_dialog_utils.py` passes.
  - `pytest tests/test_gui/test_dialog_helper_compliance.py` is wired (xfail or skip until U7 unskips).

- U2. **Migrate `gui/import_dialog.py` to use the helper.**

  **Goal:** Replace inline `QScrollArea` boilerplate with `wrap_in_scroll`; add `cap_to_screen` (currently absent).

  **Requirements:** R1.

  **Dependencies:** U1.

  **Files:**
  - Modify: `src/percell4/gui/import_dialog.py`
  - Modify: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — remove `import_dialog.py` from `duplicates_at`.
  - Modify: `docs/audits/canonical-sources-matrix.yaml` — flip `import_dialog.py.dialog-scroll-when-tall` from `re_implements` to `consumes_canonical`.

  **Approach:**
  - Remove the inline `from qtpy.QtWidgets import QScrollArea` at line 45.
  - Replace lines 45-56 (build outer + scroll + content + inner layout) with `content = QWidget(); layout = QVBoxLayout(content); scroll = wrap_in_scroll(content); QVBoxLayout(self).addWidget(scroll)`.
  - In `__init__`, after `self.resize(500, 500)` (line 39), call `cap_to_screen(self)`.

  **Patterns to follow:** U1's helper signatures.

  **Test scenarios:**
  - Happy path (smoke): `pytest-qt` instantiates `ImportDialog(parent=...)`; assert the dialog has a `QScrollArea` child whose `widget()` contains the source `QGroupBox`. (Validates the migration didn't break the widget tree.)
  - Verification scenario: `QScrollArea` appears exactly once in the dialog's widget tree (no duplicate from a botched migration).

  **Verification:**
  - PR description includes `Closes drift: dialog-scroll-when-tall#import-dialog`.
  - Existing import-flow tests (`tests/test_io/test_importer.py` if it instantiates the dialog; otherwise a manual launch) succeed.
  - Visually, the dialog still scrolls on a small screen.

- U3. **Migrate `gui/compress_dialog.py` to use the helper.**

  **Goal:** Same shape as U2 for compress.

  **Requirements:** R1.

  **Dependencies:** U1.

  **Files:**
  - Modify: `src/percell4/gui/compress_dialog.py`
  - Modify: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — remove `compress_dialog.py` from `duplicates_at`.
  - Modify: `docs/audits/canonical-sources-matrix.yaml` — flip `compress_dialog.py.dialog-scroll-when-tall` from `re_implements` to `consumes_canonical`.

  **Approach:**
  - Remove the inline `QScrollArea` import (top-of-file or local).
  - Replace lines 78-87 with the helper-based pattern.
  - Add `cap_to_screen(self)` after the resize call in `__init__`.

  **Test scenarios:**
  - Happy path (smoke): `pytest-qt` instantiates `CompressDialog`; assert exactly one `QScrollArea` in the widget tree containing the Source group.

  **Verification:**
  - PR description includes `Closes drift: dialog-scroll-when-tall#compress-dialog`.

- U4. **Migrate `gui/workflows/single_cell/config_dialog.py` to use the helper.**

  **Goal:** Same shape as U2 for the single-cell workflow config dialog.

  **Requirements:** R1.

  **Dependencies:** U1.

  **Files:**
  - Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  - Modify: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — remove this dialog from `duplicates_at`.
  - Modify: `docs/audits/canonical-sources-matrix.yaml` — flip the cell to `consumes_canonical`.

  **Approach:**
  - Replace the inline `QScrollArea` block at lines 207-219 with `wrap_in_scroll(content)` + add `cap_to_screen(self)` to `__init__`.
  - Existing test at `tests/test_gui_workflows/test_config_dialog.py` should still pass; verify no widget-tree assumptions break.

  **Test scenarios:**
  - Happy path (smoke): existing `tests/test_gui_workflows/test_config_dialog.py` continues to pass post-migration.
  - Verification scenario: exactly one `QScrollArea` in the dialog tree.

  **Verification:**
  - PR description includes `Closes drift: dialog-scroll-when-tall#single-cell-config-dialog`.

- U5. **Migrate `gui/add_layer_dialog.py` to use the helper for both tabs and the screen-cap.**

  **Goal:** Replace the donor's inline patterns (lines 71-77, 172-184, 806-820) with `cap_to_screen(self)` + two `wrap_in_scroll` calls. This is the dialog the helpers were extracted *from*; migrating it back through the helper proves the API is shaped right.

  **Requirements:** R1.

  **Dependencies:** U1.

  **Files:**
  - Modify: `src/percell4/gui/add_layer_dialog.py`
  - Modify: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — remove `add_layer_dialog.py` from `duplicates_at`.
  - Modify: `docs/audits/canonical-sources-matrix.yaml` — flip `add_layer_dialog.py.dialog-scroll-when-tall` from `re_implements` to `consumes_canonical`.

  **Approach:**
  - Lines 71-77: replace the screen-bound `try/except` block with `cap_to_screen(self)` (after `self.resize(800, 700)`).
  - Lines 172-184 (Discover TIFFs tab): build `content = QWidget(); layout = QVBoxLayout(content)` — reuse existing `layout` body — then wrap with `scroll = wrap_in_scroll(content); outer.addWidget(scroll)`.
  - Lines 806-820 (TCSPC tab): same shape.
  - Single TIFF / ROIs / Cellpose tabs left as-is — they don't currently scroll-wrap and don't need to.

  **Test scenarios:**
  - Happy path: `pytest-qt` instantiates `AddLayerDialog`; assert two `QScrollArea` instances in the widget tree (one per affected tab) and that `maximumHeight()` is set (cap was applied).
  - Edge case: TCSPC tab construction does not regress — the per-channel token override widgets (Thread 1's `3beb964`, `c8875f3`) still appear inside the wrapped content.
  - Edge case: Single TIFF tab does NOT have a `QScrollArea` (deliberate exemption — the tab is short).

  **Verification:**
  - PR description includes `Closes drift: dialog-scroll-when-tall#add-layer-dialog`.
  - Manual launch: open Add Layer dialog, switch through all five tabs; confirm scroll behavior unchanged on the two wrapped tabs and absent on the three short ones.

- U6. **Add scroll wrapper + screen-cap to `gui/export_images_dialog.py` (drift fix).**

  **Goal:** This is the only behavioral change in the thread — `export_images_dialog.py` currently has no scroll wrapper at all and overflows on small screens with many channels/segs/masks.

  **Requirements:** R2.

  **Dependencies:** U1.

  **Files:**
  - Modify: `src/percell4/gui/export_images_dialog.py`
  - Modify: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — remove `export_images_dialog.py` from `duplicates_at`.
  - Modify: `docs/audits/canonical-sources-matrix.yaml` — flip `export_images_dialog.py.dialog-scroll-when-tall` from `drifts_from_canonical` to `consumes_canonical`.

  **Approach:**
  - In `_build_ui` (line 42), replace `layout = QVBoxLayout(self)` with `content = QWidget(); layout = QVBoxLayout(content); scroll = wrap_in_scroll(content); QVBoxLayout(self).addWidget(scroll)`.
  - In `__init__` (line 33), after `self.resize(500, 500)`, call `cap_to_screen(self)`.
  - The rest of `_build_ui` (the channel/seg/mask groups, format combo, button row) stays unchanged.

  **Test scenarios:**
  - Happy path: `pytest-qt` instantiates `ExportImagesDialog` against a fixture store with 16 channels + 8 segs + 8 masks; assert exactly one `QScrollArea` is in the widget tree, and the dialog's `maximumHeight()` is ≤ screen height × 0.9.
  - Edge case: store with zero channels / zero segs / zero masks — dialog still constructs without error (the existing `if n_channels > 0:` guards still apply).
  - Edge case: the Export and Cancel buttons remain inside the scrolled content and remain visible at the bottom (or, if the design choice is to keep buttons fixed, document the choice in the PR — but the simplest migration leaves them inside the scrolled content).

  **Verification:**
  - PR description includes `Closes drift: dialog-scroll-when-tall#export-images-dialog`.
  - Manual launch with a many-layered dataset: dialog scrolls; on a 1080p screen the dialog fits.

- U7. **Finalize the canonical-source column.**

  **Goal:** When the last migration (U2–U6) has landed, transition the column from `pre_canonical` to `canonical_clean` and unskip the compliance test.

  **Requirements:** R3, R4, R5.

  **Dependencies:** U1, U2, U3, U4, U5, U6.

  **Files:**
  - Modify: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — set `status: canonical_clean`, ensure `duplicates_at: []`.
  - Modify: `docs/audits/canonical-sources-matrix.yaml` — set the `canonical_sources:` entry's `status: canonical_clean` and `canonical_file: src/percell4/gui/_dialog_utils.py`.
  - Modify: `tests/test_gui/test_dialog_helper_compliance.py` — remove the xfail/skip; the test now enforces compliance going forward.

  **Approach:**
  - This unit lands in the *same PR* as the final migration if U2–U6 are bundled; otherwise it's a tiny standalone PR after the last migration. Either is fine; the matrix-update-in-same-PR rule (R4) is still satisfied because each individual migration PR has updated its own row already.
  - If U2–U6 were bundled into a single PR, U7 lands in that same PR.

  **Test scenarios:**
  - Test expectation: rerun `pytest tests/test_gui/test_dialog_helper_compliance.py` — it now passes (was xfail/skip). The test passing confirms all 5 dialogs use the helper or are explicitly exempt.
  - Test expectation: `git grep "from qtpy.QtWidgets import QScrollArea" src/percell4/gui/` returns zero hits (the helper now owns the import). Acceptable variance: if `_dialog_utils.py` itself imports it, that's fine; the grep should exclude that file or be scoped to dialogs.

  **Verification:**
  - PR description includes `Closes drift: dialog-scroll-when-tall#all-cells-clean` (or lists all five dup-ids if bundled).
  - `docs/audits/canonical-sources-matrix.yaml` shows the canonical-source entry with `status: canonical_clean` and 0 cells in `re_implements` / `drifts_from_canonical` for this column.
  - `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` shows `status: canonical_clean`, `canonical_source: src/percell4/gui/_dialog_utils.py`, `duplicates_at: []`.
  - The compliance test runs in CI without xfail.

---

## System-Wide Impact

- **Interaction graph:** None. Helpers are pure-Qt utilities; no signal/slot or state-management impact.
- **Error propagation:** `cap_to_screen` swallows `Exception` from `parent.screen()` to preserve the donor's headless-test tolerance — verified explicitly in U1's tests.
- **State lifecycle risks:** None. Mechanical refactor; widget trees gain one extra `QScrollArea` per migrated dialog (where one is missing today, in U6's case) and lose the inline boilerplate.
- **API surface parity:** No public API changes. `_dialog_utils.py` is intentionally underscored — it's an internal `gui/` helper, not an exported convention.
- **Integration coverage:** `tests/test_gui_workflows/test_config_dialog.py` already exercises one of the migrated dialogs (`workflows/single_cell/config_dialog.py`); confirm it still passes post-U4 to validate the migration didn't break widget-tree assumptions.
- **Unchanged invariants:** `theme.py` global stylesheet still drives all dialog colors; `setFrameShape(NoFrame)` on the helper preserves transparency-to-theme. No other dialog (`viewer.py`, `data_plot.py`, `cell_table.py`, `phasor_plot.py`) is affected — those are top-level windows, not dialogs that grow tall.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Migrating a dialog regresses a widget-tree assumption (e.g., a test that walks `dialog.layout().itemAt(0)` and expects a specific widget). | Each migration unit (U2–U6) includes a smoke test that the dialog instantiates and contains a `QScrollArea` with the expected child. The existing `test_config_dialog.py` is the most likely tripwire — run it after U4. |
| `cap_to_screen` on Windows / Linux behaves differently from macOS (multi-monitor edge cases). | Helper preserves donor's `try/except Exception` swallow; capping is best-effort and never blocks dialog construction. Documented in U1's test scenarios. |
| The compliance test creates a future-PR friction point — every new dialog must opt in or exempt. | This is the intended outcome — the audit is fixing a "drift creeps in unnoticed" failure mode. Exemption set lives in the test module with a one-line reason; opting in via `wrap_in_scroll` is two lines of dialog code. The friction is bounded and surfaces the convention at PR-review time, which is exactly when learning retrieval should happen. |
| Bundled U2–U6 PR grows large and reviewers miss a per-dialog issue. | Implementer may split per-dialog PRs if the diff feels large. Each U_i is independently mergeable as long as U7 lands last. |
| `add_layer_dialog.py` migration accidentally drops the per-channel token override widgets (Thread 1's recent work). | U5's test scenarios explicitly assert the per-channel token override widgets remain in the TCSPC tab tree post-migration. |

---

## Documentation / Operational Notes

- This thread closes a column in `docs/audits/canonical-sources-matrix.yaml`. The matrix YAML's `threads:` section moves the `dialog-scroll-helper-rollout` thread from `proposed` → `in_progress` (when U1 PR opens) → `closed` (when U7 lands).
- `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` is the canonical source going forward. Future dialogs in `src/percell4/gui/` consume the helper or carry an exemption.
- No user-facing release note needed — this is a structural refactor with one minor visible improvement (`export_images_dialog.py` now scrolls).
- The render script `scripts/render_canonical_sources_matrix.py` (origin R6) is not built yet; the rendered Markdown view of the matrix can be regenerated by a follow-up thread.

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md`
- **Audit matrix:** `docs/audits/canonical-sources-matrix.yaml` (column `dialog-scroll-when-tall`)
- **Canonical source doc:** `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`
- **Donor pattern:** `src/percell4/gui/add_layer_dialog.py:71-77, 172-184, 806-820`
- **Drift site:** `src/percell4/gui/export_images_dialog.py:42-55`
- **Related Thread 1 commit:** `6adde5a fix(gui): scrollable TCSPC tab + screen-bounded dialog height`
