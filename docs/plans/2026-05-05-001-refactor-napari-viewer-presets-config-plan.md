---
title: "refactor: Centralize napari viewer presets in src/percell4/config/"
type: refactor
status: active
date: 2026-05-05
---

# refactor: Centralize napari viewer presets in src/percell4/config/

## Overview

Extract every hardcoded napari layer display setting (channel→colormap mapping,
default blending mode, default opacity, contrast-limit policy, label-color
RGBA tuples, mask `color_dict`s, multi-select staged color, phasor ROI preset,
threshold-QC preview, FLIM lifetime turbo, yellow-cmap analysis preview, yellow
ROI shapes preset, group categorical color cycle) from ~14 call sites across
`src/percell4/gui/` and `src/percell4/interfaces/gui/` into a single new
`src/percell4/config/viewer_presets.py` module of pure-Python constants and
small pure helpers.

The user's intent is operational: one file to edit when they want to retune any
napari display value. The module is **not** a UI surface — there is no settings
dialog, no runtime reload, no JSON. Editing the file and restarting the app is
the workflow.

This is a pure behavior-preserving refactor. Values move; semantics do not.

---

## Problem Frame

Napari display defaults are scattered across at least eight files. The same
yellow `DirectLabelColormap` is literally re-typed in `threshold_qc.py:419` and
`analysis_panel.py:376`. The same yellow ROI shapes preset appears in
`threshold_qc.py:432` and `analysis_panel.py:387` — with one drift
(`analysis_panel` lacks `blending="additive"`). Two segmentation cleanup
previews use different opacities (`segmentation_panel.py:491` is `0.5`,
`gui/workflows/single_cell/seg_qc.py:450` is `0.6`). The multi-select staged
overlay color sits in `gui/multi_select.py` as a private constant
`_STAGED_COLOR` that `gui/viewer.py` then lazy-imports (todo
`todos/022-pending-p2-viewer-imports-private-multi-select-constants.md` flags
this inverted dependency).

When the user wants to change "the look" of any layer kind — recolor selection
highlight, swap turbo for inferno on lifetime maps, soften the phasor ROI
overlay — they currently grep, find N copies, and decide which ones really
should change together. A single file makes that decision once, in one place.

The closest precedent in this codebase is
[`src/percell4/gui/theme.py`](../../src/percell4/gui/theme.py): pure constants,
`Final`-typed values, plain dicts, no Qt at module load. `gui/CLAUDE.md`
explicitly notes "every GUI file imports constants from here; no hardcoded hex
colors elsewhere". `viewer_presets.py` becomes the napari analog.

---

## Requirements Trace

- **R1.** Every napari display value the user is likely to want to tune lives
  in exactly one file: `src/percell4/config/viewer_presets.py`.
- **R2.** No call site retains a hardcoded equivalent of any value moved into
  the module.
- **R3.** Behavior is preserved char-by-char. Every existing `add_image` /
  `add_labels` / `add_mask` / `DirectLabelColormap` invocation produces a layer
  with the same colormap, blending, opacity, contrast-limit policy, and
  color-dict keys/values it does today.
- **R4.** The module is **napari-free**: no `import napari` at module top, no
  `DirectLabelColormap` construction. It exposes color *dicts* and string
  colormap names; call sites continue to construct `DirectLabelColormap` via
  the lazy import they already use. This preserves the option for
  `domain/`/`application/`/`ports/` to read from the module without tripping
  the import-linter contracts in `pyproject.toml:91-139`.
- **R5.** The module is **session-free** and **side-effect-free**: no reads of
  `Session`/`CellDataModel`, no module-level mutable state, no `global`. All
  helpers are pure functions of explicit args. (Reference:
  `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md`
  Issue 4 — module-level `_color_index` was a prior bug.)
- **R6.** The refactor absorbs todo
  `todos/022-pending-p2-viewer-imports-private-multi-select-constants.md`:
  `_STAGED_COLOR` and `_OVERLAY_LAYER_NAME` are renamed to public
  `STAGED_OVERLAY_COLOR` / `STAGED_OVERLAY_LAYER_NAME` in
  `viewer_presets.py`; `viewer.py` no longer reaches into `multi_select.py`'s
  private namespace.
- **R7.** Existing tests pass without behavioral changes. The two test files
  that currently import `_OVERLAY_LAYER_NAME` from `multi_select.py`
  (`tests/test_gui_workflows/test_multi_select_e2e.py:22`,
  `tests/test_gui_workflows/test_multi_select_keystroke.py:32`) are updated to
  import from the new public location.
- **R8.** A small unit test on the presets module asserts the load-bearing
  RGBA tuples and the default mask `color_dict` exist with their current
  values, since the existing suite contains zero assertions on
  `layer.blending` / `layer.opacity` / `layer.contrast_limits` / colormap
  names.
- **R9.** Classification constants (`PERCELL_TYPE_KEY`, `LAYER_TYPE_MASK`,
  `LAYER_TYPE_SEGMENTATION`, the `_phasor_roi_preview_` name prefix) do **not**
  move into `viewer_presets.py`. They are protocol contracts read by
  classifiers, not display values, and three call sites depend on their exact
  string values
  (`docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`).

---

## Scope Boundaries

- No introduction of a runtime config UI, settings dialog, JSON/TOML config
  file, hot-reload, or per-user override mechanism. The artifact is a Python
  module the developer edits.
- No consolidation of values that currently differ across call sites.
  User chose "preserve exactly". The two yellow-ROI-shapes drifts (`blending`
  present vs absent) and the two cleanup-preview opacity drifts (0.5 vs 0.6)
  survive as **separately named constants**, not a single merged value.
- No movement of classification constants (`PERCELL_TYPE_KEY`,
  `LAYER_TYPE_MASK`, `LAYER_TYPE_SEGMENTATION`, `_phasor_roi_preview_` prefix).
- No change to `_update_label_display`'s state-machine logic — only the four
  RGBA literals it builds `color_dict`s from move out. The order of operations,
  the `show_selected_label = False` precedence, the inclusion of both `0`
  and `None` keys, and the absence of `events.colormap.blocker()` all stay
  exactly as documented in
  `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`.
- No change to `add_image`'s contrast-limits-from-data computation. The
  *policy* ("compute from `nanmin`/`nanmax`") is behavior, not a setting.
- No re-export shims. `multi_select.py` will not retain
  `_STAGED_COLOR = STAGED_OVERLAY_COLOR` for "backward compatibility"
  (`docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md`).
  All call sites and the two test imports are updated atomically.

### Deferred to Follow-Up Work

- **Cleanup-preview opacity drift** — `segmentation_panel.py:491` reads
  `LABELS_OVERLAY_DEFAULT_OPACITY = 0.5` (the shared default);
  `seg_qc.py:450` reads `GROUPED_SEG_CLEANUP_PREVIEW_OPACITY = 0.6` (the
  drift). The only surviving identical-semantic drift after this PR. A
  follow-up PR may decide to collapse them to a single value after a visual
  A/B confirms `0.5` and `0.6` should look the same to the user.
- **Yellow-ROI `blending=` call-site asymmetry** — `threshold_qc.py:432`
  passes `blending=vp.YELLOW_ROI_BLENDING`; `analysis_panel.py:387` omits the
  kwarg. Drift survives at the call-site level. A follow-up PR may decide to
  set `blending=vp.YELLOW_ROI_BLENDING` at the analysis call site after a
  visual A/B confirms the difference is invisible.
- **Registering canonical sources:** if `viewer_presets.py` becomes a
  canonical source under `docs/audits/canonical-sources-matrix.yaml`, that
  registry edit happens in a separate PR — conflating refactor with audit
  bookkeeping pollutes both.

---

## Context & Research

### Relevant Code and Patterns

- **Precedent — `src/percell4/gui/theme.py`:** pure-constants module pattern.
  `from __future__ import annotations`, top-level constants in
  SCREAMING_SNAKE_CASE, plain dicts, one `apply_theme(app)` helper. Imported
  absolutely as `from percell4.gui import theme; theme.BACKGROUND`. Zero Qt
  imports at module top would be ideal, but the file uses no widgets — the
  stylesheet is a string. Mirror the **shape**, not the contents.
- **Per-module `Final`-typed constants:** `src/percell4/gui/multi_select.py:60-62`
  shows the local convention (`_OVERLAY_LAYER_NAME: Final = "..."`,
  `_STAGED_COLOR: Final = (0.0, 0.9, 0.9, 0.6)`). Use `typing.Final` on every
  module-level constant in the new module.
- **No TypedDict in the package** — plain dicts are idiomatic for color tables
  (see `viewer.CHANNEL_COLORMAPS`).
- **Lazy `DirectLabelColormap` import:** every call site uses
  `from napari.utils.colormaps import DirectLabelColormap` lazily inside the
  function. Eleven sites, fully consistent. Keep this pattern.
- **Absolute imports:** every src import is `from percell4.x import y`. Mirror
  in the new module and in updated call sites.
- **Top-level subpackages live as siblings under `src/percell4/`** — `gui`,
  `interfaces`, `application`, `adapters`, `ports`, `domain`, etc. New
  `src/percell4/config/` is the natural fit.
- **`pyproject.toml:79-80`** uses `setuptools.packages.find` — new subpackage
  is auto-discovered, no packaging change.
- **Ruff `target-version = "py312"`, line-length 100, lint set `["E", "F", "I",
  "N", "W", "UP"]`** (`pyproject.toml:11, 142-153`). The
  `"src/percell4/gui/*.py" = ["N802"]` per-file ignore is GUI-specific and
  correctly does not apply to `config/`.

### Institutional Learnings

- **`docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`**
  — four compound bugs were needed to make multi-cell selection highlighting
  work. The yellow `[1.0, 1.0, 0.0, 0.8]`, transparent `[0.0, 0.0, 0.0, 0.0]`,
  dim `[0.5, 0.5, 0.5, 0.15]`, and filter-only-cyan `[0.3, 0.8, 0.8, 0.5]`
  RGBAs were tuned through these iterations. **Char-by-char preservation is
  load-bearing.** Do not touch the assignment order in `_update_label_display`
  or the lack of `events.colormap.blocker()` — only the literals move.
- **`docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
  Pattern 1** documents the canonical `color_dict` shape
  (`{0: "transparent", None: <default-rgba>, <id>: <highlight-rgba>}`).
  Caveat: this doc still shows the deprecated `events.colormap.blocker()`
  pattern — superseded by the doc above. Reference Pattern 1 for the **shape**;
  do not copy its snippets.
- **`docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`**
  — phasor ROI preview layers are launcher-mediated. The peer view picks the
  hex; the launcher creates the napari layer. A constant for the default
  preview color is fine in `viewer_presets.py`. A *helper* that constructs the
  layer would cross the producer/consumer seam — do not introduce one.
- **`docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`**
  — semantic constants (`PERCELL_TYPE_KEY`, `LAYER_TYPE_MASK`,
  `LAYER_TYPE_SEGMENTATION`) are read by three classifiers. They are not
  display presets; they stay in `viewer.py`.
- **`docs/solutions/ui-bugs/ui-theme-refactor-lessons.md`** is the closest
  prior centralization refactor in this repo. Six iterative visual bugs
  surfaced after the move because per-widget rules had been silently
  compensating for missing globals. Treat each of the ~14 call sites as a
  separate verification, not one batch.
- **`docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md`**
  — no transitional re-exports. Atomic move + update all call sites + greps
  return clean.
- **`docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md`
  Issue 4** — `_color_index` was previously a module-level `global` and got
  fixed by becoming `self._color_index`. Helpers in the new module must take
  state as args, return updated state, never own it.
- **T1 audit scope** (`docs/audits/canonical-sources-matrix.yaml`): the new
  file is **not** in any `applies_to` glob. The `PreToolUse` learnings hook
  will not warn on edits to it. After the refactor, run
  `python3 scripts/learnings_applicability.py src/percell4/config/viewer_presets.py`
  to confirm clean.

### External References

External research skipped. The codebase's own `theme.py` is a stronger
precedent than any external "centralize constants" pattern, and napari's
`DirectLabelColormap` API has not changed across the 0.5–0.7 range.

---

## Key Technical Decisions

- **Module is napari-free, returning data not constructed objects.**
  `viewer_presets.py` exposes `dict`s like
  `BINARY_MASK_COLOR_DICT = {0: "transparent", 1: "yellow", None: "transparent"}`
  rather than a constructed `DirectLabelColormap`. Call sites keep their
  existing lazy `from napari.utils.colormaps import DirectLabelColormap`
  pattern. Why: import-linter contracts forbid `domain/`/`application/`/
  `ports/` from importing napari; keeping the new module data-only preserves
  the option for any layer to read from it later. Also keeps test imports
  cheap (no Qt warm-up).
- **All constants are `Final`-typed and immutable.** Tuples for RGBAs,
  `MappingProxyType` (or just plain dicts under the discipline "do not
  mutate") for color tables. No module-level mutable state. Helpers take
  state as args and return new state — the existing
  `_colormap_for_channel(name, color_index) -> tuple[str, int]` shape
  generalizes. Why: prior bug history (Issue 4 above).
- **Helpers are pure functions, no session reads.** `_colormap_for_channel`
  takes channel name and a counter int; returns `(cmap_name, next_int)`. The
  underscore is preserved from the current `viewer.py:154` definition — same
  name, same signature, different home. No `Session` / `CellDataModel` access.
  Why: keeps the module testable from a vanilla `pytest` invocation, and aligns
  with the Selector/Creator/Action taxonomy — Actions read session at the call
  site, not via a presets helper.
- **Identical values consolidate; genuine drift gets its own constant.** Where
  multiple call sites use the same display value for the same purpose
  (e.g. five preview-style labels overlays at opacity 0.5, blending
  "translucent"), they share a single named constant —
  `LABELS_OVERLAY_DEFAULT_OPACITY` and `LABELS_OVERLAY_DEFAULT_BLENDING`.
  Where the values are genuinely different across call sites, each gets its
  own name — `GROUPED_SEG_CLEANUP_PREVIEW_OPACITY = 0.6` is the only surviving
  cleanup-preview-opacity drift; `MASK_DEFAULT_OPACITY = 0.5` is kept separate
  from `LABELS_OVERLAY_DEFAULT_OPACITY = 0.5` (same value today, but tunable
  independently going forward). The yellow ROI shapes preset consolidates to a
  single `YELLOW_ROI_*` set used by both `threshold_qc.py:432` and
  `analysis_panel.py:387`; the call-site difference (`threshold_qc` passes
  `blending=`, `analysis_panel` omits it) is preserved at the call-site level,
  not in the module. This *partially* overrides "preserve exactly via separate
  constants" — chosen because the yellow ROI duplication was the user's named
  motivating example, and four-identical-`0.5` constants directly undermine
  the "one file to retune" goal. The remaining drift (cleanup-preview opacity
  0.5 vs 0.6) survives as documented.
- **Atomic move; no shim layer.** `_STAGED_COLOR` and `_OVERLAY_LAYER_NAME`
  are deleted from `multi_select.py` in the same commit they appear in
  `viewer_presets.py`. The two test files that import them are updated in the
  same diff. No `_STAGED_COLOR = STAGED_OVERLAY_COLOR` alias.
- **Group constants by call-site purpose, not by attribute type.** Layout is
  by section: channel mapping, image defaults, labels defaults, mask defaults,
  selection/filter colors, multi-select staged overlay, phasor ROI mask,
  threshold-QC group preview, threshold-QC yellow_cmap preview, threshold-QC
  ROI shapes, segmentation cleanup preview, grouped-seg cleanup preview, FLIM
  lifetime, analysis threshold preview, analysis ROI shapes. Each section is
  a few lines with a short comment header (purpose only — no "WHY" prose
  unless non-obvious). User scans the file and finds what they want by
  feature area.
- **Naming convention: `<DOMAIN>_<ATTRIBUTE>` SCREAMING_SNAKE_CASE.**
  `IMAGE_DEFAULT_BLENDING = "additive"`,
  `MASK_DEFAULT_OPACITY = 0.5`,
  `SELECTION_HIGHLIGHT_RGBA = (1.0, 1.0, 0.0, 0.8)`. Distinct from
  `theme.py`'s shorter names because there are more axes (domain × attribute);
  shorter names would collide. The High-Level Technical Design module sketch
  below is the authoritative source for exact constant names — when the
  decision section text and the sketch differ, the sketch wins.

---

## Open Questions

### Resolved During Planning

- **Should helpers construct `DirectLabelColormap`?** No — keep module
  napari-free; call sites construct via lazy import (already the convention).
- **Should `_STAGED_COLOR` move into the new module?** Yes — absorbs todo 022
  cleanly; the alternative (leaving it in `multi_select.py` and importing
  from there into `viewer_presets.py`) inverts the dependency wrong-way.
- **What about classification constants (`PERCELL_TYPE_KEY` etc.)?** Stay in
  `viewer.py`; they are protocol keys, not display values.
- **Should we consolidate identical values across call sites?** Yes for
  identical-value duplication that undermines the "one file to retune" goal:
  the four preview-overlay opacities (`0.5`/`"translucent"`) collapse to a
  single shared `LABELS_OVERLAY_DEFAULT_*`; the two yellow ROI shapes presets
  collapse to a single `YELLOW_ROI_*`. Genuine drift (cleanup-preview opacity
  0.5 vs 0.6) survives as separate names. The yellow-ROI `blending=` drift
  (present in threshold-QC, absent in analysis) survives at the call-site
  level: same module constants, different call-site kwarg presence.
- **Should the channel-colormap helper be renamed?** No — keep
  `_colormap_for_channel` (matches the current `viewer.py:154` name; underscore
  signals "internal helper"). Smaller diff, no rationale to rename.
- **Module comment header lines?** Mirror `theme.py`'s `# ── Section ──`
  style for visual scannability.

### Deferred to Implementation

- **Final exact wording of constant names.** Names in the High-Level Technical
  Design module sketch are the spec. Implementer may rename for clarity during
  the move only if every call site is updated in the same diff and the value
  at every call site is preserved exactly. Names are scoped to the module;
  call-site usage is trivially greppable.
- **Whether to wrap the channel→colormap lookup in `MappingProxyType`.**
  Decide during implementation: if any test wants to monkey-patch it for a
  scenario, plain dict is friendlier. Default: plain dict, with a docstring
  comment "do not mutate at runtime".
- **Whether the visual smoke-test pass is one app session or one per area.**
  Implementer's call. Suggested: one session per top-level area (viewer
  defaults, threshold QC, segmentation cleanup, FLIM, analysis preview,
  multi-select, phasor ROI) so failures are localizable.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Module shape sketch (`src/percell4/config/viewer_presets.py`)

```text
"""Centralized napari layer display presets for PerCell4.

Edit values in this file to retune any napari display behavior — layer
colormaps, blending, opacity, contrast policy, label colors, mask color
dicts. No napari or Qt imports here; call sites construct
DirectLabelColormap from data exposed below.
"""

from __future__ import annotations
from typing import Final

# ── Channel → colormap mapping ──────────────────────────────
CHANNEL_COLORMAPS: Final[dict[str, str]] = { "dapi": "blue", ... }
CHANNEL_FALLBACK_CYCLE: Final[tuple[str, ...]] = ("green", "magenta", ...)
def _colormap_for_channel(name: str, color_index: int) -> tuple[str, int]: ...
# Same name and signature as the current viewer.py helper. Underscore
# preserved to signal "internal helper, not part of the documented surface."
# Public reads happen via the constants above; this helper is a small piece
# of logic that happens to live next to the data it reads.

# ── Image layer defaults ────────────────────────────────────
IMAGE_DEFAULT_BLENDING: Final[str] = "additive"

# ── Labels layer defaults ───────────────────────────────────
LABELS_DEFAULT_BLENDING: Final[str] = "additive"

# ── Mask layer defaults (runtime mask creation; tunable independently of preview overlays) ─
MASK_DEFAULT_BLENDING: Final[str] = "additive"
MASK_DEFAULT_OPACITY:  Final[float] = 0.5
BINARY_MASK_COLOR_DICT: Final[dict[int | None, str]] = {
    0: "transparent", 1: "yellow", None: "transparent",
}

# ── Shared label-overlay defaults for preview-style layers ──
# Used by every "preview" labels layer: threshold-QC group preview,
# threshold-QC yellow_cmap preview, segmentation cleanup preview, analysis
# threshold preview. To retune any preview-overlay opacity/blending in one
# place, edit here. Values that genuinely need to differ get their own
# constant (see GROUPED_SEG_CLEANUP_PREVIEW_OPACITY below).
LABELS_OVERLAY_DEFAULT_OPACITY:  Final[float] = 0.5
LABELS_OVERLAY_DEFAULT_BLENDING: Final[str]   = "translucent"

# ── Selection & filter highlight RGBAs ──────────────────────
SELECTION_HIGHLIGHT_RGBA:    Final[tuple[float, ...]] = (1.0, 1.0, 0.0, 0.8)
SELECTION_DIM_NONSELECTED:   Final[tuple[float, ...]] = (0.5, 0.5, 0.5, 0.15)
FILTER_ONLY_VISIBLE_RGBA:    Final[tuple[float, ...]] = (0.3, 0.8, 0.8, 0.5)
TRANSPARENT_RGBA:            Final[tuple[float, ...]] = (0.0, 0.0, 0.0, 0.0)

# ── Multi-select staged overlay ─────────────────────────────
STAGED_OVERLAY_LAYER_NAME: Final[str] = "_multi_select_staged"
STAGED_OVERLAY_COLOR:      Final[tuple[float, ...]] = (0.0, 0.9, 0.9, 0.6)
STAGED_OVERLAY_OPACITY:    Final[float] = 0.6
STAGED_OVERLAY_BLENDING:   Final[str] = "translucent"

# ── Phasor ROI mask (peer-view picks hex; launcher reads opacity/blending) ─
PHASOR_ROI_MASK_OPACITY:  Final[float] = 0.4
PHASOR_ROI_MASK_BLENDING: Final[str]   = "translucent"

# ── Threshold-QC group preview (image; labels overlay reads LABELS_OVERLAY_*) ─
THRESHOLD_QC_GROUP_COLORS:         Final[tuple[str, ...]] = ("#1f77b4", ...)  # 10
THRESHOLD_QC_GROUP_IMAGE_COLORMAP: Final[str] = "gray"
THRESHOLD_QC_GROUP_IMAGE_BLENDING: Final[str] = "additive"

# ── Yellow labels color_dict (shared by threshold_qc + analysis_panel) ─
YELLOW_LABEL_COLOR_DICT: Final[dict[int | None, str]] = {
    0: "transparent", 1: "yellow", None: "transparent",
}

# ── Yellow ROI shapes preset (shared between threshold-QC + analysis) ──
# Used by threshold_qc.py:432 and analysis_panel.py:387. The threshold-QC
# call site passes blending=YELLOW_ROI_BLENDING explicitly; the analysis
# call site continues to omit blending=, preserving today's drift at the
# call-site level rather than in the module.
YELLOW_ROI_EDGE_COLOR: Final[str]   = "yellow"
YELLOW_ROI_EDGE_WIDTH: Final[int]   = 2
YELLOW_ROI_FACE_COLOR: Final[tuple[float, ...]] = (1, 1, 0, 0.1)
YELLOW_ROI_BLENDING:   Final[str]   = "additive"

# ── Segmentation cleanup previews (drift survives in opacity only) ─────
# segmentation_panel.py:491 reads LABELS_OVERLAY_DEFAULT_OPACITY (0.5).
# seg_qc.py:450 reads GROUPED_SEG_CLEANUP_PREVIEW_OPACITY (0.6 — the drift).
# Both read LABELS_OVERLAY_DEFAULT_BLENDING for the "translucent" string.
GROUPED_SEG_CLEANUP_PREVIEW_OPACITY: Final[float] = 0.6

# ── FLIM lifetime ───────────────────────────────────────────
FLIM_LIFETIME_COLORMAP: Final[str] = "turbo"
FLIM_LIFETIME_BLENDING: Final[str] = "additive"
```

### Call-site read pattern

Each call site replaces hardcoded literals with module imports:

```text
# Before (analysis_panel.py:374-385):
from napari.utils.colormaps import DirectLabelColormap
yellow_cmap = DirectLabelColormap({0: "transparent", 1: "yellow", None: "transparent"})
viewer.add_labels(mask, opacity=0.5, blending="translucent", colormap=yellow_cmap)

# After:
from napari.utils.colormaps import DirectLabelColormap
from percell4.config import viewer_presets as vp
yellow_cmap = DirectLabelColormap(vp.YELLOW_LABEL_COLOR_DICT)
viewer.add_labels(mask, opacity=vp.LABELS_OVERLAY_DEFAULT_OPACITY,
                  blending=vp.LABELS_OVERLAY_DEFAULT_BLENDING, colormap=yellow_cmap)
```

The `DirectLabelColormap` import stays at the call site (lazy / function-local
where it is today).

---

## Implementation Units

- U1. **Create `src/percell4/config/` package and `viewer_presets.py` module skeleton**

**Goal:** Land the new subpackage with all constants in place, plus the `_colormap_for_channel` helper (moved verbatim from `viewer.py:154-165`), plus a small unit test focused on import purity and helper behavior. No call site changes yet — the module is dead code at end of unit, but importable.

**Requirements:** R1, R4, R5, R8

**Dependencies:** None

**Files:**
- Create: `src/percell4/config/__init__.py` (empty file is fine; mirrors other subpackages)
- Create: `src/percell4/config/viewer_presets.py`
- Create: `tests/test_config/__init__.py`
- Create: `tests/test_config/test_viewer_presets.py`

**Approach:**
- Mirror `src/percell4/gui/theme.py` shape: `from __future__ import annotations`, `Final`-typed module-level constants grouped by section with `# ── Section ──` headers. No napari/Qt imports.
- Populate every constant from the inventory in the High-Level Technical Design. Source values directly from current call sites (open each file, copy the literal). This is the only step where a typo can ship a regression — verify each value against the source line.
- **Source-of-truth note for `STAGED_OVERLAY_COLOR`:** the value is `(0.0, 0.9, 0.9, 0.6)`, sourced verbatim from `src/percell4/gui/multi_select.py:61`. Todo `todos/022-pending-p2-viewer-imports-private-multi-select-constants.md` shows `(0.0, 1.0, 1.0, 0.7)` in its body — that is a placeholder ("or whatever the current tuple is"), not a spec. Ignore the todo body's RGBA.
- `_colormap_for_channel(name: str, color_index: int) -> tuple[str, int]` is a verbatim move of the current `viewer.py:154-165` body, with the local `CHANNEL_COLORMAPS` / `_COLOR_CYCLE` references rewired to the module's own `CHANNEL_COLORMAPS` and `CHANNEL_FALLBACK_CYCLE`. Same name (underscore preserved), same signature, pure function.
- Test file is small: import-purity and helper round-trip only. Per-constant literal-equality assertions are intentionally omitted — they are tautological (assert `0.5 == 0.5`) and catch a typo in source only when the test value was independently typed correctly. Char-by-char preservation is enforced instead by copying values directly from the source files at write time and by U8's visual smoke pass.

**Patterns to follow:**
- `src/percell4/gui/theme.py` — module shape, `# ── ... ──` headers, `Final` typing
- `src/percell4/gui/multi_select.py:60-62` — local convention for `Final`-typed constants

**Test scenarios:**
- Integration boundary: assert `percell4.config.viewer_presets.__dict__` contains no `napari` / `qtpy` / `DirectLabelColormap` symbols. Mirrors the existing pattern at `tests/test_workflows/test_qt_free_imports.py:39-42` — checking `mod.__dict__` rather than `sys.modules` is required because pytest-qt and sibling test modules may have already populated `sys.modules['napari']` (test isolation does not extend to `sys.modules`).
- Happy path (helper): `_colormap_for_channel("DAPI", 0) == ("blue", 0)` — case-insensitive match returns existing colormap, index unchanged.
- Edge case (helper): `_colormap_for_channel("unknown_chan", 0)` returns the first cycle entry and increments the index. Verify exact return shape against `viewer.py:154-165` before writing the assertion (don't re-derive — copy current behavior).
- Edge case (helper): `_colormap_for_channel("unknown_chan", len(CHANNEL_FALLBACK_CYCLE))` wraps modulo cycle length (mirror current behavior). Same: read the source, then write the assertion.

**Verification:**
- `python -c "from percell4.config import viewer_presets"` succeeds.
- `pytest tests/test_config/test_viewer_presets.py` passes.
- `grep -rE "from napari|import napari" src/percell4/config/` returns nothing.
- The new module has no module-level mutable state (`grep -E "^\\s*global " src/percell4/config/viewer_presets.py` empty).

---

- U2. **Migrate `src/percell4/gui/viewer.py` to import from `viewer_presets`**

**Goal:** Replace every hardcoded display literal in `viewer.py` with an import from the new module. Includes the channel→colormap mapping, the cycle, `_colormap_for_channel`, the three add_* method defaults, the four `_update_label_display` RGBAs, and the staged overlay color/opacity/blending. Removes the lazy import of `_STAGED_COLOR` / `_OVERLAY_LAYER_NAME` from `multi_select.py`. Adds a one-release deprecation shim for `CHANNEL_COLORMAPS` to protect external readers (notebooks, scripts).

**Requirements:** R1, R2, R3, R6

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/viewer.py`

**Approach:**
- Add `from percell4.config import viewer_presets as vp` at the top of `viewer.py`. (Module-level import is fine — `viewer_presets.py` does not transitively import napari/Qt.)
- Delete the local `CHANNEL_COLORMAPS` dict (currently lines 24-40) and `_COLOR_CYCLE` (line 43). All in-module readers route to `vp.CHANNEL_COLORMAPS` / `vp.CHANNEL_FALLBACK_CYCLE`. **Add a one-line deprecation shim at the top of `viewer.py` after the import:** `CHANNEL_COLORMAPS = vp.CHANNEL_COLORMAPS  # DEPRECATED: import from percell4.config.viewer_presets; will be removed in a future release.` This contradicts the general "no shims" rule, but `CHANNEL_COLORMAPS` was a public top-level name and external notebooks/scripts may import it; the shim costs one line and protects discoverability.
- Delete the local `_colormap_for_channel` (lines 154-165). The function moves to `viewer_presets.py` with the same name (underscore preserved). Callers route to `vp._colormap_for_channel`.
- In `add_image` (288-306): replace `blending="additive"` with `blending=vp.IMAGE_DEFAULT_BLENDING`. Channel→colormap call site uses `vp._colormap_for_channel`.
- In `add_labels` (308-315): replace `blending="additive"` with `blending=vp.LABELS_DEFAULT_BLENDING`.
- In `add_mask` (317-356): replace `blending="additive"`, `opacity=0.5`, and the binary-mask `color_dict` literal with the corresponding `vp.MASK_*` references.
- In `_update_label_display` (465-535): replace each RGBA literal with the named `vp.SELECTION_*` / `vp.FILTER_ONLY_VISIBLE_RGBA` / `vp.TRANSPARENT_RGBA`. Preserve the order of operations (the `show_selected_label = False` precedence and the assignment to `labels_layer.colormap` stay exactly as they are).
- In `add_staged_overlay` / `update_staged_overlay` / `remove_staged_overlay` (649-714): delete the lazy `from percell4.gui.multi_select import _OVERLAY_LAYER_NAME, _STAGED_COLOR` lines. Use `vp.STAGED_OVERLAY_LAYER_NAME`, `vp.STAGED_OVERLAY_COLOR`, `vp.STAGED_OVERLAY_OPACITY`, `vp.STAGED_OVERLAY_BLENDING` instead.
- Do **not** touch `PERCELL_TYPE_KEY`, `LAYER_TYPE_MASK`, `LAYER_TYPE_SEGMENTATION`, the `_phasor_roi_preview_` prefix logic, or any classification path — those are R9 exclusions.

**Patterns to follow:**
- Existing absolute-import style (`from percell4.x import y`) used everywhere else in the file.

**Test scenarios:**
- Happy path: existing `tests/test_gui_workflows/test_session_to_napari_push.py` passes unchanged. It calls `viewer_win.add_labels` and `add_mask`; no behavior changes.
- Integration: `tests/test_gui_workflows/test_multi_select_e2e.py` continues to assert `_OVERLAY_LAYER_NAME in viewer.layers` and `overlay.colormap.color_dict` contains staged ids. Layer name and color values must match. (Test file itself is updated in U3 to import the new constant location.)
- Manual smoke (deferred to U8): launch the app, load a dataset, confirm channel colormap auto-detection still works for `dapi`, `gfp`, `cy5`, and a mock `unknown_chan` (cycles through fallback).

**Verification:**
- `grep -nE '(\[1\.0,\s*1\.0,\s*0\.0,\s*0\.8\]|\[0\.5,\s*0\.5,\s*0\.5,\s*0\.15\])' src/percell4/gui/viewer.py` returns no matches (RGBAs gone).
- `grep -nE 'blending\s*=\s*"(additive|translucent)"' src/percell4/gui/viewer.py` returns no matches (literals replaced).
- `grep -n "from percell4.gui.multi_select import _" src/percell4/gui/viewer.py` returns no matches.
- `grep -nE 'def _colormap_for_channel|_COLOR_CYCLE\s*=' src/percell4/gui/viewer.py` returns no matches (definitions deleted).
- `grep -n "CHANNEL_COLORMAPS" src/percell4/gui/viewer.py` returns only the deprecation-shim line `CHANNEL_COLORMAPS = vp.CHANNEL_COLORMAPS  # DEPRECATED: ...`.

---

- U3. **Move staged-overlay constants out of `multi_select.py` and update test imports**

**Goal:** Delete the private `_STAGED_COLOR` and `_OVERLAY_LAYER_NAME` constants from `gui/multi_select.py` and update the two test files that import them. This absorbs todo 022. After this unit, no module reaches into `multi_select.py`'s private namespace from outside.

**Requirements:** R6, R7

**Dependencies:** U1, U2 (U2 must already not import these names from multi_select)

**Files:**
- Modify: `src/percell4/gui/multi_select.py`
- Modify: `tests/test_gui_workflows/test_multi_select_e2e.py`
- Modify: `tests/test_gui_workflows/test_multi_select_keystroke.py`

**Approach:**
- Delete `_OVERLAY_LAYER_NAME: Final = ...` and `_STAGED_COLOR: Final = ...` from `multi_select.py:60-62`. If `multi_select.py` itself reads these (verify by `grep`), update it to import from `vp.STAGED_OVERLAY_LAYER_NAME` / `vp.STAGED_OVERLAY_COLOR`.
- In `tests/test_gui_workflows/test_multi_select_e2e.py:22`: change `from percell4.gui.multi_select import _OVERLAY_LAYER_NAME` to `from percell4.config.viewer_presets import STAGED_OVERLAY_LAYER_NAME as _OVERLAY_LAYER_NAME` (alias preserves test-local readability) — or rename the test-local symbol if cheap. Decide based on how many call sites in the test file use it.
- In `tests/test_gui_workflows/test_multi_select_keystroke.py:32`: same swap.
- Do **not** add a re-export shim in `multi_select.py`. The grep
  `grep -rn "from percell4.gui.multi_select import _" src/ tests/` should
  return empty after this unit (todo 022's acceptance criterion).

**Patterns to follow:**
- `docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md` — atomic move, no transitional aliases.

**Test scenarios:**
- Happy path: `pytest tests/test_gui_workflows/test_multi_select_e2e.py` passes. The test reads the staged overlay layer by name and asserts colormap key population.
- Happy path: `pytest tests/test_gui_workflows/test_multi_select_keystroke.py` passes. The test confirms the `M` keystroke creates the overlay.
- Integration: `pytest tests/test_gui_workflows/test_multi_select.py` (controller behavior, mocks `add_staged_overlay`) passes — no source-of-truth changes affect this test, but include in regression run.

**Verification:**
- `grep -rn "from percell4.gui.multi_select import _" src/ tests/` returns empty.
- `grep -n "_STAGED_COLOR\\|_OVERLAY_LAYER_NAME" src/percell4/gui/multi_select.py` returns empty (or only references to the new public-name imports if the file itself reads them).
- Todo file `todos/022-pending-p2-viewer-imports-private-multi-select-constants.md` can be marked resolved or moved to a `done/` location per repo convention (verify how todos are normally resolved before doing this).

---

- U4. **Migrate `src/percell4/gui/threshold_qc.py`**

**Goal:** Replace all hardcoded display literals in `threshold_qc.py` with imports from `viewer_presets`. Covers four call-site clusters: `_GROUP_COLORS` constant, group-image `add_image` preview, group-labels `add_labels` preview, yellow_cmap `DirectLabelColormap` block, yellow ROI shapes preset.

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/threshold_qc.py`

**Approach:**
- Add `from percell4.config import viewer_presets as vp` at the top of `threshold_qc.py`.
- Replace the file-local `_GROUP_COLORS = [...]` (line 50-53) with a local convenience alias: `_GROUP_COLORS = vp.THRESHOLD_QC_GROUP_COLORS`. Local alias only — not re-exported. (Verify: `grep -rn "from percell4.gui.threshold_qc import _GROUP_COLORS" src/` returns empty after the change.)
- Group-image preview (397-402): `colormap="gray"` → `vp.THRESHOLD_QC_GROUP_IMAGE_COLORMAP`; `blending="additive"` → `vp.THRESHOLD_QC_GROUP_IMAGE_BLENDING`.
- Group-labels preview (165-181): `opacity=0.5` → `vp.LABELS_OVERLAY_DEFAULT_OPACITY`; `blending="translucent"` → `vp.LABELS_OVERLAY_DEFAULT_BLENDING`.
- Yellow_cmap block (418-428): replace the inline `DirectLabelColormap({0: "transparent", 1: "yellow", None: "transparent"})` with `DirectLabelColormap(vp.YELLOW_LABEL_COLOR_DICT)`. Replace `opacity=0.5` / `blending="translucent"` with `vp.LABELS_OVERLAY_DEFAULT_OPACITY` / `vp.LABELS_OVERLAY_DEFAULT_BLENDING`.
- Yellow ROI shapes preset (432-440): `edge_color="yellow"` → `vp.YELLOW_ROI_EDGE_COLOR`; `edge_width=2` → `vp.YELLOW_ROI_EDGE_WIDTH`; `face_color=[1, 1, 0, 0.1]` → `list(vp.YELLOW_ROI_FACE_COLOR)` (the napari API may want a list; tuple→list at the call site is acceptable). `blending="additive"` → `vp.YELLOW_ROI_BLENDING`.

**Patterns to follow:**
- Same absolute-import style.

**Test scenarios:**
- Test expectation: none — no automated test covers this file's display values. Verification is by manual smoke pass in U8.

**Verification:**
- `grep -nE 'colormap\s*=\s*"gray"|opacity\s*=\s*0\.5|edge_color\s*=\s*"yellow"' src/percell4/gui/threshold_qc.py` returns no matches.
- `grep -nE '\{\s*0:\s*"transparent",\s*1:\s*"yellow"' src/percell4/gui/threshold_qc.py` returns no matches. (The current source spreads `DirectLabelColormap(...)` across multiple lines with `color_dict={0: "transparent", 1: "yellow", None: "transparent"}` indented on its own line — the regex matches the dict literal directly rather than trying to anchor on `DirectLabelColormap(` on the same line.)
- File runs through ruff cleanly (`ruff check src/percell4/gui/threshold_qc.py`).

---

- U5. **Migrate the two segmentation-cleanup-preview call sites**

**Goal:** Replace hardcoded display literals in the two cleanup-preview implementations. Both files create a `_cleanup_preview` labels layer with `blending="translucent"` but differ in opacity (0.5 vs 0.6). Drift is preserved as two named constants.

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/segmentation_panel.py`
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py`

**Approach:**
- `segmentation_panel.py:491-493`: replace `opacity=0.5` with `vp.LABELS_OVERLAY_DEFAULT_OPACITY`; `blending="translucent"` with `vp.LABELS_OVERLAY_DEFAULT_BLENDING`.
- `seg_qc.py:450-455`: replace `opacity=0.6` with `vp.GROUPED_SEG_CLEANUP_PREVIEW_OPACITY` (drift survives — different value than the shared default); `blending="translucent"` with `vp.LABELS_OVERLAY_DEFAULT_BLENDING` (same value as default; consolidates).
- Both files add `from percell4.config import viewer_presets as vp`.

**Patterns to follow:**
- Same absolute-import style.

**Test scenarios:**
- Test expectation: none — no automated test asserts on cleanup-preview opacity/blending. Verification is by manual smoke pass in U8 (segmentation cleanup flow + grouped-seg flow are separate UI paths).

**Verification:**
- `grep -nE "opacity\\s*=\\s*0\\.[56]" src/percell4/gui/segmentation_panel.py src/percell4/gui/workflows/single_cell/seg_qc.py` returns no matches in cleanup-preview neighborhoods (other call sites in those files may legitimately keep their own values; spot-check by line number).

---

- U6. **Migrate `src/percell4/interfaces/gui/main_window.py` phasor ROI mask preset**

**Goal:** Replace the phasor ROI mask preset literals with imports. Preserve the producer/consumer split — the launcher still creates the napari layer; the peer view picks the hex color. Only opacity/blending move; the hex color stays parameterized.

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py`

**Approach:**
- At the phasor ROI mask preset block (1041-1061), replace `opacity=0.4` with `vp.PHASOR_ROI_MASK_OPACITY`; `blending="translucent"` with `vp.PHASOR_ROI_MASK_BLENDING`.
- The `DirectLabelColormap({0: transparent, 1: hex_color, None: transparent})` call: the `0`/`None` keys are constant; the `1` key receives the runtime `hex_color` argument. Construct from a dict literal using `vp.TRANSPARENT_LABEL_KEY = "transparent"` style — actually keep it simple: `{0: "transparent", 1: hex_color, None: "transparent"}` is fine since the only variability is `hex_color`. Optionally expose a helper `vp.build_phasor_roi_color_dict(hex_color: str) -> dict` if cleaner; decide during implementation. Either way, the `"transparent"` literal is the same as `BINARY_MASK_COLOR_DICT[0]`.
- Add `from percell4.config import viewer_presets as vp`.

**Patterns to follow:**
- Producer/consumer pattern from `docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md` — do not introduce a helper that crosses the seam.

**Test scenarios:**
- Test expectation: none — `tests/test_gui_workflows/test_phasor_remove_roi.py:22` explicitly disclaims that "no real napari viewer, so layer-list state is not asserted here." Verification is by manual smoke pass in U8.

**Verification:**
- `grep -nE "opacity\\s*=\\s*0\\.4|blending\\s*=\\s*\"translucent\"" src/percell4/interfaces/gui/main_window.py` line ~1041-1061 returns no matches (other call sites in this large file may legitimately keep their values; verify by line range).

---

- U7. **Migrate task-panel call sites: FLIM lifetime + analysis threshold preview**

**Goal:** Replace hardcoded display literals in the two task-panel files. Covers FLIM turbo lifetime, analysis threshold yellow_cmap labels preview, and analysis ROI shapes preset (with its drift — missing `blending` — preserved exactly).

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py`

**Approach:**
- `flim_panel.py:511-516`: `colormap="turbo"` → `vp.FLIM_LIFETIME_COLORMAP`; `blending="additive"` → `vp.FLIM_LIFETIME_BLENDING`.
- `analysis_panel.py:374-385`: replace inline `yellow_cmap = DirectLabelColormap({...})` with `DirectLabelColormap(vp.YELLOW_LABEL_COLOR_DICT)`. Replace `opacity=0.5` / `blending="translucent"` with `vp.LABELS_OVERLAY_DEFAULT_OPACITY` / `vp.LABELS_OVERLAY_DEFAULT_BLENDING` (consolidated — same shared constants used by threshold_qc, seg cleanup preview).
- `analysis_panel.py:387-394` (yellow ROI shapes): replace `edge_color="yellow"`, `edge_width=2`, `face_color=[1, 1, 0, 0.1]` with the corresponding `vp.YELLOW_ROI_*` constants (shared with threshold_qc). **Critical:** do *not* add `blending=` here. The current call site does not set blending; the drift is preserved at the call-site level (this call site omits the kwarg; threshold_qc passes `blending=vp.YELLOW_ROI_BLENDING`). The module does define `YELLOW_ROI_BLENDING`, but this call site simply never reads it.
- Both files add `from percell4.config import viewer_presets as vp`.

**Patterns to follow:**
- Same absolute-import style.

**Test scenarios:**
- Test expectation: none — no automated test asserts on lifetime colormap or analysis preview opacity/blending. Verification is by manual smoke pass in U8.

**Verification:**
- `grep -nE 'colormap\s*=\s*"turbo"' src/percell4/interfaces/gui/task_panels/flim_panel.py` returns no matches.
- `grep -nE '\{\s*0:\s*"transparent",\s*1:\s*"yellow"' src/percell4/interfaces/gui/task_panels/analysis_panel.py` returns no matches.
- `analysis_panel.py:387-394` block: `blending=` keyword is absent in the `viewer.add_shapes(...)` call — preserves drift.

---

- U8. **Manual visual smoke pass and final greps**

**Goal:** Confirm zero behavior regression by exercising each migrated call site in a running PerCell4 session, then run final verification greps to confirm no hardcoded display literals slipped through.

**Requirements:** R3, R7

**Dependencies:** U1, U2, U3, U4, U5, U6, U7 (all migrations complete)

**Files:** No source edits in this unit.

**Approach:**
- Launch the app: `source .venv/bin/activate && python main.py`.
- Open a real PerCell4 experiment dataset (any local `.h5` with multi-channel images, masks, and ideally FLIM data). The `*.svg` files at the repo root are figure exports, not loadable data — do not point at them.
- Walk each of the seven UI areas and visually confirm the layer looks unchanged from before this PR:
  1. **Viewer image defaults:** load TIFFs across multiple channels (`dapi`, `gfp`, `cy5`, plus an unknown channel name to exercise the fallback cycle). Confirm colormaps and additive blending look unchanged.
  2. **Mask layer defaults:** create a binary mask. Confirm yellow appears on label-1 pixels at opacity 0.5, additive blending.
  3. **Selection / filter highlights:** select a few cells; confirm yellow at full opacity 0.8. Apply a filter; confirm dim-gray non-selected at 0.15 alpha. Combine selection + filter; confirm both behaviors compose. Filter only, no selection: confirm cyan at 0.5 alpha.
  4. **Multi-select staged overlay:** press `M`; click cells to stage them; confirm cyan staged overlay at opacity 0.6, translucent blending. Layer name is `_multi_select_staged`.
  5. **Threshold QC:** open grouped segmentation panel; run threshold QC; confirm gray group-image preview, group-labels at opacity 0.5 translucent, yellow_cmap labels at opacity 0.5 translucent, yellow ROI rectangle with edge=yellow, width=2, face=(1,1,0,0.1), additive blending.
  6. **Segmentation cleanup previews:** trigger cleanup preview from segmentation panel (opacity 0.5); trigger from grouped-seg cleanup (opacity 0.6). Both translucent.
  7. **FLIM lifetime + phasor ROI mask + analysis threshold preview:** load FLIM data; confirm turbo additive lifetime; create phasor ROI; confirm preview at opacity 0.4 translucent and "Apply" produces a mask with the picked hex color; open analysis panel threshold preview; confirm yellow_cmap at opacity 0.5 translucent and yellow ROI rectangle without explicit blending (drift preserved).
- Run final verification greps:
  - `grep -rnE 'blending\s*=\s*"' src/percell4/gui/ src/percell4/interfaces/gui/` — every match should be `vp.<NAME>_BLENDING`, not a string literal. Spot-check exceptions (test fixtures, tooltip text, etc.) and decide whether they belong in the module.
  - `grep -rn "from percell4.gui.multi_select import _" src/ tests/` — empty.
  - `grep -rnE '\{\s*0:\s*"transparent",\s*1:\s*"yellow"' src/percell4/` — empty (every yellow-cmap construction reads from `vp.YELLOW_LABEL_COLOR_DICT`).
  - `python3 scripts/learnings_applicability.py src/percell4/config/viewer_presets.py` — confirm no T1 hook warnings on the new file.
  - `pytest tests/` — full suite green.
- **Per-call-site `vp.*` correctness audit.** Manual smoke alone cannot distinguish "wrong constant, identical value" cases (e.g. `vp.YELLOW_ROI_EDGE_COLOR` typo'd in a call site that should read `vp.LABELS_OVERLAY_DEFAULT_OPACITY` — both yellow / both 0.5, indistinguishable visually). For each modified file, run `grep -nE 'vp\.[A-Z_]+' <file>` and walk every match:
  - Read the surrounding line. Confirm the constant name semantically matches the call site (e.g. `vp.FLIM_LIFETIME_COLORMAP` is in the FLIM panel, not the threshold-QC panel).
  - Cross-check against the migration mapping in U2-U7's Approach sections — every `vp.*` in the diff should appear in exactly one Approach mapping, in the matching file.
  - Flag any `vp.*` reference that mentions a constant the plan did not authorize for that file.
  This is a 5-minute audit and catches the one regression class manual smoke cannot.
- Re-read `docs/audits/subscriber-rebind-matrix.md`'s `_original_colormaps` cache row and confirm it still matches the post-refactor `viewer.py` (no semantic changes expected — the cache is per-layer and not session-derived; the refactor moved literals, not lifecycle).

**Patterns to follow:**
- `docs/solutions/ui-bugs/ui-theme-refactor-lessons.md` — verify each call site independently. The UI theme refactor surfaced 6 iterative bugs because per-context drift was missed in batch verification.

**Test scenarios:**
- Test expectation: none — this is an explicit human-in-the-loop verification unit. The presets-module unit test from U1 is the only automated assertion on values.
- Manual checklist (see Approach for the seven areas).

**Verification:**
- All seven UI areas above behave identically to pre-PR. Any visual diff is a regression and must be fixed before merge.
- Final greps return empty.
- `pytest tests/` passes cleanly.
- A short note in `docs/solutions/` may be worth writing if any non-obvious gotcha emerged during the smoke pass — defer to ce-compound after merge.

---

## System-Wide Impact

- **Interaction graph:** None of the 9 subscribers in `docs/audits/subscriber-rebind-matrix.md` change their read sets. `_update_label_display`'s state machine still consumes `change.selection`/`change.filter` flags; only the literal RGBAs it threads into the resulting `color_dict` move out. Verify post-refactor that the matrix's cache columns are still accurate (no edit needed if they are).
- **Error propagation:** Unaffected. No new failure modes — the new module raises nothing; `Final` constants cannot fail to load. An `ImportError` on `percell4.config.viewer_presets` from any updated call site would be a packaging regression caught at first app launch.
- **State lifecycle risks:** None. The module is stateless. The pre-existing `_color_index` lives on `ViewerWindow`, not the new module — `_colormap_for_channel` is a pure function over `(name, color_index)`.
- **API surface parity:** No public API breakage. `viewer.py`'s `CHANNEL_COLORMAPS` was a public name and gets a one-release deprecation shim (`CHANNEL_COLORMAPS = vp.CHANNEL_COLORMAPS  # DEPRECATED: ...`) to protect external notebooks/scripts that may import it. The shim is removed in a follow-up release once consumers have migrated. `_colormap_for_channel` was already underscore-private and gets no shim — internal helpers may move freely.
- **Integration coverage:** `tests/test_gui_workflows/test_multi_select_e2e.py` and `test_multi_select_keystroke.py` are the only multi-layer integration tests touching the migrated names. Both updated in U3.
- **Unchanged invariants:** `PERCELL_TYPE_KEY`, `LAYER_TYPE_MASK`, `LAYER_TYPE_SEGMENTATION`, the `_phasor_roi_preview_` name prefix, the `add_mask` `metadata={"percell_type": "mask"}` tagging, `add_labels` `metadata={"percell_type": "segmentation"}` tagging, the `show_selected_label = False` precedence in `_update_label_display`, and the avoidance of `events.colormap.blocker()` are all preserved exactly. The contrast-limits-from-data computation in `add_image` is preserved (it is behavior, not a setting).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **Char-by-char value typo during the move** — silently shipping a wrong RGBA tuple (e.g., `0.85` instead of `0.8`) produces a subtle visual regression that no test catches. | Implementer copies values directly from the source files (open file, copy literal) rather than retyping. U8's manual smoke pass and per-call-site `vp.*` audit verify each migrated site. (Note: the U1 unit test deliberately omits per-constant literal-equality assertions — they are tautological and don't add signal beyond what code review and U8 already provide.) |
| **Wrong-but-identical-looking `vp.*` constant at a call site** — implementer writes `vp.YELLOW_ROI_EDGE_COLOR` where `vp.LABELS_OVERLAY_DEFAULT_OPACITY` was meant; both happen to produce visually-identical layers today, so smoke pass cannot distinguish. Future retunes propagate to the wrong call site. | U8 includes a per-call-site `vp.*` audit step: grep each modified file for `vp.[A-Z_]+` and walk every match against the U2-U7 Approach mappings. 5-minute audit; catches the one class manual smoke misses. |
| **Test imports break due to `_OVERLAY_LAYER_NAME` deletion** — two test files import the private name. | U3 updates both test files in the same diff that deletes the constants from `multi_select.py`. Verified by `grep -rn "from percell4.gui.multi_select import _" src/ tests/` returning empty. |
| **The classification constants are accidentally swept into `viewer_presets.py`** during the move (looks like display, is actually protocol). | Plan explicitly enumerates them in R9 and Scope Boundaries. Implementer verifies during U2 by checking that `PERCELL_TYPE_KEY`, `LAYER_TYPE_MASK`, `LAYER_TYPE_SEGMENTATION` remain defined in `viewer.py`. |
| **The peer view / launcher seam is crossed during U6 phasor ROI migration** by introducing a "build the whole layer" helper. | U6 explicitly forbids this — only opacity/blending move; the launcher still owns layer creation; the peer view still picks the hex. The producer/consumer rule is restated in the unit's "Patterns to follow". |
| **Visual regressions in untested UI surfaces** — no automated test asserts on `layer.blending`/`layer.opacity`/`layer.contrast_limits`. | U8 manual smoke pass, broken into seven independent areas. Precedent: UI Theme refactor surfaced 6 iterative visual bugs (`docs/solutions/ui-bugs/ui-theme-refactor-lessons.md`). |
| **Module accidentally imports napari** — closing off `domain/`/`application/`/`ports/`'s ability to read presets later. | U1 test asserts no `napari`/`qtpy`/`DirectLabelColormap` symbols in `percell4.config.viewer_presets.__dict__` (mirrors `tests/test_workflows/test_qt_free_imports.py:39-42`). Final grep `grep -rE "from napari\|import napari" src/percell4/config/` returns empty. |

---

## Documentation / Operational Notes

- Update `src/percell4/gui/CLAUDE.md`'s "Infrastructure" section to mention
  `viewer_presets.py` alongside `theme.py`, with the same one-liner: "every
  napari layer call imports display constants from
  `percell4.config.viewer_presets`; no hardcoded blending / opacity / colormap
  / RGBA literals elsewhere."
- Optionally add `src/percell4/config/CLAUDE.md` describing the subpackage's
  purpose ("non-UI configuration constants for the app; pure-data, napari-free,
  session-free"). One short paragraph; no plans, no history.
- Consider a `docs/solutions/architecture-patterns/` entry after merge if the
  UI Theme refactor's lesson ("verify each call site independently") shows up
  again during U8 — defer to ce-compound.
- No rollout, monitoring, or migration concerns. This is a local refactor.

---

## Sources & References

- **Origin:** Direct user request in conversation 2026-05-05 — no upstream
  requirements doc; planning bootstrap covered scope, format, location,
  behavior preservation, and API shape via three rounds of clarifying
  questions.
- **Precedent:** `src/percell4/gui/theme.py`
- **Absorbed todo:** `todos/022-pending-p2-viewer-imports-private-multi-select-constants.md`
- **Critical learnings:**
  - `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`
  - `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
  - `docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`
  - `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
  - `docs/solutions/ui-bugs/ui-theme-refactor-lessons.md`
  - `docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md`
  - `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md` (Issue 4)
  - `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
- **Audit references:**
  - `docs/audits/canonical-sources-matrix.yaml` (T1 scope — new file is unconstrained)
  - `docs/audits/subscriber-rebind-matrix.md` (no edits required if cache columns stay accurate)
- **Project guidance:**
  - `CLAUDE.md` (root) — GUI state-ownership three-class taxonomy
  - `src/percell4/CLAUDE.md` — top-level subpackage layout
  - `src/percell4/gui/CLAUDE.md` — `theme.py` precedent description
- **Packaging:** `pyproject.toml:79-80` (setuptools find), `pyproject.toml:91-139` (import-linter), `pyproject.toml:142-153` (ruff)
