---
title: "refactor: Axis parity across viewer_presets.py layer-kind sections"
type: refactor
status: active
date: 2026-05-05
---

# refactor: Axis parity across viewer_presets.py layer-kind sections

## Overview

Bring the per-layer-kind configurable surface in
`src/percell4/config/viewer_presets.py` to parity. Today the file is
inconsistent across layer kinds: mask layers expose
`{BLENDING, OPACITY, COLOR_DICT}`; segmentation labels and image layers
expose only `BLENDING`; FLIM lifetime exposes `{COLORMAP, BLENDING}` but no
opacity; yellow-ROI shapes expose `{EDGE_COLOR, EDGE_WIDTH, FACE_COLOR,
BLENDING}` but no opacity. The user wants every layer-kind section to
expose every applicable axis so a single file edit can retune any aspect of
display.

This is a pure-refactor follow-up to plan
`docs/plans/2026-05-05-001-refactor-napari-viewer-presets-config-plan.md`
(merged into `main` earlier today). Behavior is preserved char-by-char on
day one via a `None`-sentinel discipline at the call sites — every new
constant ships as `None`, meaning "don't pass the kwarg, let napari use its
own default." Editing the constant to a numeric value later starts
applying it. No visible behavior change at merge.

---

## Problem Frame

The just-shipped `viewer_presets.py` centralized scattered display literals
into one file but preserved the inconsistencies between call sites
verbatim. The user has the file open and wants to tune segmentation-layer
opacity. There is no `LABELS_DEFAULT_OPACITY`. They have to discover that
`add_labels` doesn't pass `opacity=` at all, that napari's labels default
is 0.7, and that to override they would need to edit `viewer.py` directly
— which is exactly the problem `viewer_presets.py` was supposed to solve.

The fix is a parity sweep. For every layer kind in the file, expose every
axis that napari's layer constructor accepts and that is meaningful for
that kind. Wire each new constant into its call site through a uniform
`None`-aware pop pattern so the day-one behavior is unchanged but the
"single file to edit" promise is fully delivered.

The just-merged plan's R3 ("preserve behavior char-by-char") and the
char-by-char preservation comment on the four selection RGBA tuples remain
load-bearing — this refactor must not change the rendered output until the
user explicitly edits a constant.

---

## Requirements Trace

- **R1.** Every layer-kind section in `src/percell4/config/viewer_presets.py`
  exposes the full applicable axis surface — at minimum `BLENDING`,
  `OPACITY`, and (where applicable) `COLOR`/`COLORMAP`/`COLOR_DICT`. Plus
  `CONTRAST_OVERRIDE` for the generic `IMAGE` section.
- **R2.** Day-one behavior is preserved char-by-char. New constants ship as
  `None` (or as today's numeric value where one already exists); call
  sites pop them with a `None`-aware pattern that skips passing the kwarg
  when the constant is `None`. No layer should render differently after
  this PR than before.
- **R3.** Editing any new constant from `None` to a concrete value
  immediately propagates to every call site that consumes it. Editing a
  numeric constant changes its visible effect. The "one file to retune"
  promise is real, not aspirational.
- **R4.** The four selection-highlight RGBA tuples
  (`SELECTION_HIGHLIGHT_RGBA`, `SELECTION_DIM_NONSELECTED`,
  `FILTER_ONLY_VISIBLE_RGBA`, `TRANSPARENT_RGBA`) are NOT touched and are
  NOT subsumed into a generic `OVERLAY_OPACITY` knob. They were tuned
  through four compound bugs documented in
  `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`.
- **R5.** The module remains pure-data, napari-free, session-free. The
  existing import-purity test
  (`tests/test_config/test_viewer_presets.py::test_viewer_presets_imports_without_napari_or_qt`)
  must continue to pass without modification.
- **R6.** Documentation in the file makes the `None`-sentinel convention
  explicit so a user who opens the file knows that `LABELS_DEFAULT_OPACITY
  = None` means "napari decides" and that they can edit it to a float to
  start overriding.
- **R7.** The producer/consumer seam for phasor ROI previews
  (`docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`)
  is preserved — the peer view continues to pick the hex color, the
  launcher continues to construct the napari layer. No helper introduced
  here crosses that seam.

---

## Scope Boundaries

- **Selection / filter highlight RGBAs are immutable for this refactor.**
  They are not opacity knobs and they do not become opacity knobs. Their
  alpha values are tuned, not configured.
- **No `build_singleton_label_color_dict(color)` helper.** Only one
  consumer (`main_window.py:1045` for phasor ROI mask) would benefit, and
  the current inline literal is three keys. YAGNI per
  `docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md`.
- **No `LABELS_OVERLAY_*` color-dict default.** The "preview labels overlay"
  shared section already exposes opacity and blending; each consumer
  brings its own colormap (yellow_cmap, group_cmap). Forcing a default
  color-dict here would break consumer customization.
- **No `STAGED_OVERLAY_COLOR_DICT` constant.** The staged overlay's color
  dict is built dynamically from staged-id sets at the call site. The
  canonical tuning knob is `STAGED_OVERLAY_COLOR`; the dict is a function
  of that.
- **No `PHASOR_ROI_MASK_COLOR_DICT_DEFAULT` constant.** The phasor ROI
  mask color is selected at runtime by the peer view per ROI (see R7); a
  static default at the file level would conflict with the
  per-view-picks-the-hex contract documented in
  `docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`.
  The peer view passes the hex through a Qt signal; the launcher
  constructs the dict inline.
- **No `MASK_*` changes.** That section already has full coverage; touching
  it would be churn.
- **No drift consolidation.** The two surviving drifts —
  `GROUPED_SEG_CLEANUP_PREVIEW_OPACITY = 0.6` and the `analysis_panel.py:391`
  no-`blending=` call site for yellow ROI — survive untouched.
- *(Removed: FLIM lifetime and threshold-QC group image now DO get
  their own `CONTRAST_OVERRIDE` constants per the user's "every applicable
  axis" directive — see Open Questions and U1/U3/U4.)*
- **No new layer kinds.** This is a parity refactor across existing
  sections, not an expansion of the layer-kind taxonomy.
- **No removal of the `CHANNEL_COLORMAPS` deprecation shim** at
  `viewer.py:26`. That shim's removal belongs in a separate "deprecation
  sweep" PR after one release of stability.

---

## Context & Research

### Relevant Code and Patterns

- **`src/percell4/config/viewer_presets.py`** — the module under refactor.
  Pure-data, `Final`-typed constants, `# ── Section ──` headers.
- **`src/percell4/gui/viewer.py` — `ViewerWindow.add_image` / `add_labels`
  / `add_mask`** (lines 257-325) define today's `kwargs.pop(name,
  vp.X_DEFAULT)` pattern. Each method pops `blending` from kwargs with
  the `vp.*_DEFAULT_BLENDING` fallback, then *always* passes the resolved
  blending to napari. This is the spec for `BLENDING` (always-pass);
  `OPACITY` for new axes uses the `None`-aware variant (pop with `None`
  fallback, conditionally pass).
- **`src/percell4/gui/threshold_qc.py:396-401`** — `add_image` for the
  group image preview. Currently passes `colormap` and `blending` only;
  needs the same `None`-aware opacity treatment as the generic image
  path.
- **`src/percell4/interfaces/gui/task_panels/flim_panel.py:511-516`** —
  lifetime image. Same shape as group image; same treatment.
- **`src/percell4/gui/threshold_qc.py:431-440` and
  `src/percell4/interfaces/gui/task_panels/analysis_panel.py:387-394`** —
  the two `add_shapes` call sites for yellow ROIs. Today neither passes
  `opacity`; napari uses default 0.7. Adding `YELLOW_ROI_OPACITY = None`
  preserves this. (See note in Key Technical Decisions about the
  shape-opacity ↔ face-color-alpha multiplier.)
- **`src/percell4/gui/viewer.py:281-284` `add_labels`** — segmentation
  default path. Currently pops `blending` and `metadata`; passes neither
  `opacity` nor `colormap`. New axes wire here.

### Institutional Learnings

- **`docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`**
  — the four selection RGBAs are immutable. R4 is direct from this learning.
- **`docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`**
  — section structure in `viewer_presets.py` should mirror the layer-kind
  taxonomy (image / labels-segmentation / mask / labels-overlay /
  shapes / FLIM-lifetime / staged-overlay). This refactor preserves the
  taxonomy; no new tier.
- **`docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`**
  — peer-view → launcher seam stays intact. This refactor doesn't touch
  the phasor flow except to ensure no change to `PHASOR_ROI_MASK_*`
  constants.
- **`docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md`**
  — YAGNI bar. Dictates: only add knobs that are wired into a real
  consumer. Resolution in this plan: every new constant gets a
  `kwargs.pop("axis", vp.X_DEFAULT_AXIS)` at the matching call site, with
  a `None`-aware "skip the kwarg" branch. The constant is real plumbing
  the day it ships.
- **`docs/solutions/ui-bugs/ui-theme-refactor-lessons.md`** — visual
  smoke-test discipline applies; the just-shipped refactor's plan U8
  established the seven-area smoke pass pattern.
- **`docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md`**
  Issue #4 — no module-level mutable state. All new constants stay
  `Final`-typed.
- **`docs/audits/canonical-sources-matrix.yaml`** — `viewer_presets.py` is
  not in T1 scope; PreToolUse hook stays silent on edits.

### External References

External research skipped — the just-shipped refactor is a multi-direct-
example local precedent and napari 0.7.0 defaults are verified from the
installed package source. No additional value from external best-practice
research.

---

## Key Technical Decisions

- **`None` sentinel = "don't pass this kwarg".** Every new constant ships
  as `None`. The call site uses a uniform pattern:

  ```text
  opacity = kwargs.pop("opacity", vp.LABELS_DEFAULT_OPACITY)
  if opacity is not None:
      kwargs["opacity"] = opacity
  ```

  This preserves today's behavior exactly (no kwarg passed → napari
  default) while making the constant real plumbing. Editing
  `LABELS_DEFAULT_OPACITY` from `None` to `0.5` immediately starts
  applying it without further code changes. Why this over "set to napari's
  current default": the file's purpose is "edit me to override"; `None`
  unambiguously signals "no override active", whereas `0.7` would force
  every reader to ask "is this our chosen value or napari's current
  default?" The "decouple from napari version changes" angle exists but is
  weak given `napari>=0.5,<0.8` is pinned — the primary reason is semantic
  clarity for the file's reader.

  **Caller-passed `kwarg=None` edge case.** `kwargs.pop("opacity", default)`
  returns the explicit value (`None`) when a caller passed `opacity=None`,
  not the default. The conditional then skips re-injection — net behavior
  is "use napari's default", same as omitting the kwarg. Today no caller
  passes `opacity=None` explicitly (verified by grep across `src/`). The
  contract is: if you want to force napari's default, omit the kwarg; do
  not write `opacity=None`. The docstring update in U1 calls this out.

- **`None` is not used for axes that already have a numeric value.**
  `MASK_DEFAULT_OPACITY = 0.75`, `STAGED_OVERLAY_OPACITY = 0.6`,
  `LABELS_OVERLAY_DEFAULT_OPACITY = 0.5`,
  `GROUPED_SEG_CLEANUP_PREVIEW_OPACITY = 0.6`,
  `PHASOR_ROI_MASK_OPACITY = 0.4` — all stay numeric. Their call sites
  always pass them, as today. Only NEW axes with no current numeric value
  get the `None` treatment.

- **`BLENDING` constants stay always-passed (no `None` sentinel).**
  Today's `add_image` / `add_labels` / `add_mask` always pass
  `blending=vp.X_DEFAULT_BLENDING`. Switching to a `None`-aware skip would
  be a behavior change (today napari sees our blending; with `None` it
  would see its own default). Constants stay numeric strings; pop pattern
  stays "always pass."

- **`COLORMAP` for `IMAGE` is special.** `add_image` already auto-resolves
  per-channel via `_colormap_for_channel`. We do NOT add a global
  `IMAGE_DEFAULT_COLORMAP` — that would conflict. Per-channel auto
  remains the only path; per-call kwargs override remains the override.
  The user's "every axis" request is satisfied for `IMAGE` by the
  existing `CHANNEL_COLORMAPS` mapping, which IS the colormap-config knob
  for that section.

- **`*_CONTRAST_OVERRIDE` precedence chain — explicit.** Three image-class
  sections gain a contrast-override constant:
  - `IMAGE_DEFAULT_CONTRAST_OVERRIDE: tuple[float, float] | None = None`
    (consumed by `ViewerWindow.add_image`)
  - `THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE: tuple[float, float] | None = None`
    (consumed at `threshold_qc.py:396`)
  - `FLIM_LIFETIME_CONTRAST_OVERRIDE: tuple[float, float] | None = None`
    (consumed at `flim_panel.py:512`)

  Resolution order at each call site:
  1. **Caller-explicit wins.** If `contrast_limits` is already in kwargs,
     use it and skip both override and any auto-compute.
  2. **Override constant next.** Else if the section's `_CONTRAST_OVERRIDE`
     is non-`None`, use it.
  3. **Section-specific fallback.** For `IMAGE`, the existing
     auto-from-`nanmin`/`nanmax` block runs (today's behavior). For
     threshold-QC group image and FLIM lifetime, no auto-compute exists;
     the kwarg is simply omitted, and napari uses its own auto-clip from
     the data range (today's behavior).

  Implementer guidance: structure each call-site body as nested
  `if/elif/else` so the order is unambiguous. Do not run auto-compute
  first then conditionally overwrite — that path swallows the override
  under a "not in kwargs" guard.

- **`COLOR_DICT` for `LABELS` (segmentation) is `None` = napari's random
  colormap.** Today's `add_labels` doesn't pass `colormap=`; napari
  generates a random label colormap. `LABELS_DEFAULT_COLOR_DICT = None`
  preserves this. A user who later wants every segmentation layer to use
  a specific palette can populate the dict.

  **`add_labels` colormap precedence — caller wins.** Unlike `add_mask`
  (which silently drops caller-supplied `colormap=` because
  `BINARY_MASK_COLOR_DICT` is the contract), `add_labels` follows the
  same caller-wins rule used by `IMAGE_DEFAULT_CONTRAST_OVERRIDE`: if the
  caller passes `colormap=`, that wins; the preset's color_dict only
  fires when the caller didn't pass one. This matches the rest of the
  None-aware pop pattern. The asymmetry between `add_labels` (caller
  wins) and `add_mask` (constant wins) is deliberate — `add_mask`'s
  contract is "binary mask layer with the canonical color dict" and
  `BINARY_MASK_COLOR_DICT` is the load-bearing default; `add_labels`'s
  contract is "general-purpose labels layer" where caller customization
  is expected.

- **`YELLOW_ROI_OPACITY` = `None` interaction matters.** napari's default
  shape opacity is 0.7; the face_color alpha is 0.1. The layer-level
  opacity propagates to **all** sub-elements at the vispy node level —
  face fill, edge stroke, and any text — multiplying their per-element
  alpha. Effective face alpha today ≈ `0.1 * 0.7 = 0.07`; effective edge
  alpha ≈ `1.0 * 0.7 = 0.7`. `None` sentinel preserves both.

  A user who later sets `YELLOW_ROI_OPACITY = 1.0` will get effective
  face alpha ≈ `0.1 * 1.0 = 0.1` (a ~43% visibility bump on the face)
  AND a more saturated edge (`1.0 * 1.0 = 1.0`). The file comment near
  `YELLOW_ROI_*` must call out: *"layer.opacity multiplies into face,
  edge, and text alphas. Tune layer-level opacity for global effect, or
  edit `YELLOW_ROI_FACE_COLOR[3]` / `YELLOW_ROI_EDGE_COLOR` for
  per-element alpha."*

- **No `build_singleton_label_color_dict` helper.** Single consumer
  (`main_window.py:1045`); inline literal is 3 keys. YAGNI.

- **Wire-up location: `ViewerWindow` methods, not call sites.** For axes
  exposed at the `ViewerWindow.add_image` / `add_labels` / `add_mask`
  level, the `None`-aware pop happens once in `viewer.py`. Callers
  (`threshold_qc.py`, etc.) don't need to know about the pattern. For
  axes exposed at specialized call sites (e.g., `THRESHOLD_QC_GROUP_IMAGE_OPACITY`
  on a direct `viewer.add_image` call), the pop pattern lives at that
  call site.

- **Update the file's docstring to document the `None`-sentinel
  convention** so the contract is discoverable by anyone editing
  the file.

---

## Open Questions

### Resolved During Planning

- **Should `IMAGE_DEFAULT_COLORMAP` be a constant?** No — the
  per-channel `CHANNEL_COLORMAPS` mapping IS the image colormap config.
  A second knob would conflict with auto-resolution.
- **Should we add a `build_singleton_label_color_dict(color)` helper?** No
  — only one consumer; YAGNI.
- **Should FLIM lifetime and threshold-QC group image get a contrast
  knob?** **Yes** — per the user's "every applicable axis" directive.
  Both gain their own `_CONTRAST_OVERRIDE` constant (`None` ships, so
  today's behavior is preserved exactly: napari's own auto-clip applies
  when no kwarg is passed). Editing either constant to a `(lo, hi)`
  tuple immediately starts forcing those limits at that call site only.
- **Should `LABELS_OVERLAY_DEFAULT_COLOR_DICT` exist?** No — each
  consumer brings its own colormap, forcing a default would break them.
- **Should we add `STAGED_OVERLAY_COLOR_DICT`?** No — built dynamically
  from `STAGED_OVERLAY_COLOR` + staged-id sets; the color is the knob.
- **Should `MASK_*` change?** No — already at full coverage; would be
  churn.
- **Should the `CHANNEL_COLORMAPS` deprecation shim at `viewer.py:26`
  also be removed in this PR?** No — that's a separate deprecation-window
  decision; out of scope.
- **Should we replace flat `*_DEFAULT_*` constants with a single
  `LAYER_DEFAULTS` dict + a `_apply_defaults(kwargs, kind)` helper?** No.
  The flat-constants pattern was the user's explicit schema choice and
  matches the existing module style. A nested-dict alternative would (a)
  lose greppability (`grep LABELS_DEFAULT_OPACITY` no longer points to
  one symbol), (b) break the existing `Final`-typed convention, and (c)
  forfeit IDE autocomplete on constant names. The dict alternative would
  win only if the module had ~50+ constants; today's count makes the
  flat shape strictly simpler.

### Deferred to Implementation

- **Exact wording of the file docstring update** explaining the `None`
  sentinel. The implementer may iterate on phrasing during the unit;
  the requirement is that the convention is documented somewhere a user
  who opens the file will see it.
- **Whether the `None`-aware pop pattern lives as inline code or as a
  small private helper inside `viewer.py`.** Three call sites in `viewer.py`
  (`add_image`, `add_labels`, `add_mask`) plus a few specialized call
  sites would each repeat ~3 lines. A helper like
  `_pop_optional(kwargs, name, default)` is acceptable if it improves
  readability; not required if inline reads cleanly.
- **Whether to add a single shape-opacity-multiplier comment or pin it to
  every shape constant.** Implementer's judgment — one well-placed comment
  near `YELLOW_ROI_*` is probably enough.

---

## Implementation Units

- U1. **Extend `viewer_presets.py` with new `None`-sentinel constants and document the convention**

**Goal:** Add the new axis constants to every layer-kind section that lacks
them today. Update the module docstring to document the `None` sentinel.
Extend the import-purity test to cover the new constants. No call site
changes yet — module is reorganized, but no behavior reaches napari from
the new symbols until U2-U5.

**Requirements:** R1, R5, R6

**Dependencies:** None

**Files:**
- Modify: `src/percell4/config/viewer_presets.py`
- Modify: `tests/test_config/test_viewer_presets.py`

**Approach:**
- Module docstring gains a short paragraph defining the `None` sentinel:
  "A constant set to `None` means: don't pass this kwarg to napari; let
  napari use its own default. Editing a `None` to a concrete value
  starts applying that value at every call site that reads this constant.
  Constants set to a numeric/string value are *always* passed."
- Add to the `IMAGE` section: `IMAGE_DEFAULT_OPACITY: Final[float | None] = None`,
  `IMAGE_DEFAULT_CONTRAST_OVERRIDE: Final[tuple[float, float] | None] = None`.
- Add to the `LABELS` section:
  `LABELS_DEFAULT_OPACITY: Final[float | None] = None`,
  `LABELS_DEFAULT_COLOR_DICT: Final[dict[int | None, str] | None] = None`.
- Add to the `THRESHOLD_QC_GROUP_IMAGE` block:
  `THRESHOLD_QC_GROUP_IMAGE_OPACITY: Final[float | None] = None`,
  `THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE: Final[tuple[float, float] | None] = None`.
- Add to the `YELLOW_ROI` block:
  `YELLOW_ROI_OPACITY: Final[float | None] = None`. Add a comment near
  the section noting the shape-opacity multiplier propagates to face,
  edge, and text alphas at the vispy node level (effective face alpha =
  `face_color[3] * layer.opacity`; same multiplier applies to edge and
  text). Tune layer-level opacity for global effect, or per-element
  alphas for local effect.
- Add to the `FLIM_LIFETIME` section:
  `FLIM_LIFETIME_OPACITY: Final[float | None] = None`,
  `FLIM_LIFETIME_CONTRAST_OVERRIDE: Final[tuple[float, float] | None] = None`.
- Section ordering preserved; new constants added at the end of their
  respective section blocks for diff readability.
- Test file: extend the existing `test_viewer_presets_imports_without_napari_or_qt`
  test to confirm no new forbidden symbols leaked in via the new
  constants. **Do not** add tautological per-constant `assert vp.X is None`
  scenarios — they're scope-creep on the test file (the value is in the
  diff for review; the test would become wrong the day the constant is
  intentionally edited to a numeric value). The propagation guarantee is
  covered by monkeypatch tests that land with the wiring units (U2/U3/U4/U5),
  not by sentinel-value assertions here.

**Patterns to follow:**
- Existing `# ── Section ──` headers and `Final`-typed constants in
  `viewer_presets.py`.
- `tests/test_config/test_viewer_presets.py::test_viewer_presets_imports_without_napari_or_qt`
  for the purity-guard pattern.

**Test scenarios:**
- Happy path: existing import-purity test still passes — no new
  napari/qtpy/DirectLabelColormap symbols leaked in. Re-running
  `test_viewer_presets_imports_without_napari_or_qt` is the gate.
- *(No per-constant value assertions — see Approach. Propagation
  coverage lands with the wiring units.)*

**Verification:**
- `pytest tests/test_config/test_viewer_presets.py` passes.
- `grep -nE 'OPACITY|CONTRAST_OVERRIDE|COLOR_DICT' src/percell4/config/viewer_presets.py`
  shows the new constants in their respective sections.
- The module docstring includes the `None` sentinel paragraph.

---

- U2. **Wire `ViewerWindow.add_image` and `add_labels` to read the new constants**

**Goal:** `add_image` reads `IMAGE_DEFAULT_OPACITY` and
`IMAGE_DEFAULT_CONTRAST_OVERRIDE`. `add_labels` reads
`LABELS_DEFAULT_OPACITY` and `LABELS_DEFAULT_COLOR_DICT`. Both use the
`None`-aware pop pattern: pop the kwarg, default to the `vp.*` constant,
pass to napari only if the resolved value is not `None`. `add_mask` is
unchanged — it already has full coverage.

**Requirements:** R1, R2, R3, R5

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/viewer.py`
- Create or modify: `tests/test_gui_workflows/test_viewer_presets_propagation.py`
  (or extend an existing GUI test file at the implementer's choice — see
  Test scenarios)

**Approach:**
- `add_image` (today at lines 257-275): after the existing `colormap` and
  `blending` pops, add a `None`-aware opacity pop. Replace the
  contrast-handling block with the explicit precedence chain documented
  in Key Technical Decisions: `if "contrast_limits" in kwargs: caller
  wins`; `elif vp.IMAGE_DEFAULT_CONTRAST_OVERRIDE is not None:
  kwargs["contrast_limits"] = override`; `else: <existing
  nanmin/nanmax auto-compute>`.
- `add_labels` (today at lines 277-284): add `None`-aware pops for
  `opacity` and `colormap` (mapped to `LABELS_DEFAULT_OPACITY` and
  `LABELS_DEFAULT_COLOR_DICT`). For `colormap`: caller-passed `colormap=`
  wins (caller-wins precedence per Key Technical Decisions); if no
  caller value AND `LABELS_DEFAULT_COLOR_DICT` is non-`None`, wrap the
  dict in `DirectLabelColormap` lazily (mirroring the wrap pattern in
  `add_mask`) and pass. **No defer hatch — the colormap wiring lands
  here in U2.** Dead config is not acceptable per R3.
- `add_mask` remains unchanged. It already pops blending and opacity with
  numeric defaults; no parity gap there.
- The `None`-aware pop is repeated in two methods. If repetition is
  noisy, a small file-private helper `_pop_optional(kwargs, key, default)
  -> None` returning early when both kwargs[key] and default are `None`
  is acceptable. Implementer's judgment.

**Patterns to follow:**
- Existing `kwargs.pop(name, vp.X_DEFAULT)` pattern in `add_image`,
  `add_labels`, `add_mask`. The `None`-aware variant only adds the
  conditional re-injection.
- Lazy `from napari.utils.colormaps import DirectLabelColormap` inside
  the function (the existing convention).

**Test scenarios:**
- Happy path (propagation, monkeypatch): `monkeypatch.setattr(vp,
  "IMAGE_DEFAULT_OPACITY", 0.42)`; create a `ViewerWindow` (via the
  existing test fixture pattern in `test_session_to_napari_push.py`);
  call `viewer_win.add_image(data, "x")`; assert
  `viewer_win.viewer.layers["x"].opacity == 0.42`. Confirms the constant
  flows through `add_image`'s pop pattern to napari.
- Happy path (caller wins): `monkeypatch.setattr(vp,
  "IMAGE_DEFAULT_OPACITY", 0.42)`; call
  `viewer_win.add_image(data, "x", opacity=0.9)`; assert
  `layers["x"].opacity == 0.9`. Confirms caller's explicit value wins.
- Happy path (None ships): default `vp.IMAGE_DEFAULT_OPACITY = None`;
  call `viewer_win.add_image(data, "x")`; assert
  `layers["x"].opacity == 1.0` (napari default). Confirms day-one
  preservation.
- Happy path (contrast precedence): three scenarios per the
  precedence chain documented in Key Technical Decisions: caller-wins,
  override-wins-when-no-caller, auto-compute-when-both-None.
- Happy path (`add_labels` opacity propagation): mirror the
  `add_image` opacity tests for `add_labels` with
  `LABELS_DEFAULT_OPACITY`.
- Happy path (`add_labels` color_dict propagation): monkeypatch
  `LABELS_DEFAULT_COLOR_DICT` to a concrete dict; call `add_labels`
  without `colormap=`; assert the resulting layer's colormap is the
  expected `DirectLabelColormap`. Caller-wins variant: caller passes
  `colormap=other_cmap`; assert the layer's colormap is `other_cmap`.
- Integration: `tests/test_gui_workflows/test_session_to_napari_push.py`
  (7 tests, exercise `add_labels` and `add_mask`) all still pass — they
  assert on selection state, not display, so they confirm the
  pop-conditional pattern doesn't break layer creation.
- Integration: `tests/test_gui_workflows/test_multi_select_e2e.py` and
  `test_multi_select_keystroke.py` (39 multi-select tests) all still
  pass.

**Verification:**
- `python -c "from percell4.gui.viewer import ViewerWindow"` succeeds.
- The new `None`-aware pops appear in `add_image` and `add_labels`.
- Propagation tests above pass with the new wiring.
- Behavior with all-None defaults is unchanged: existing test suites
  green at the same baseline as before this PR.

---

- U3. **Wire `threshold_qc.py` group-image preview opacity + contrast override**

**Goal:** The group-image `add_image` call at `threshold_qc.py:396-401`
reads `vp.THRESHOLD_QC_GROUP_IMAGE_OPACITY` and
`vp.THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE` via the `None`-aware pop.
Today the call site passes neither `opacity=` nor `contrast_limits=`;
with both constants set to `None`, the call site continues to omit them
(napari uses its built-in defaults). Editing either constant later
applies the new value at this call site only.

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/threshold_qc.py`

**Approach:**
- At the group-image `add_image` call, conditionally include
  `opacity=vp.THRESHOLD_QC_GROUP_IMAGE_OPACITY` and
  `contrast_limits=vp.THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE` in the
  kwargs only when the respective constants are non-`None`. Inline
  `if/else` around the call, or build a kwargs dict pre-call, whichever
  reads cleaner. (Note: this call bypasses `ViewerWindow.add_image` and
  goes directly to napari's `viewer.add_image`, so the central pop
  pattern from U2 doesn't reach it.)
- No other change to `threshold_qc.py` — the labels/yellow-cmap/ROI sites
  are already wired through other constants and need no updates here.

**Patterns to follow:**
- The just-shipped wiring pattern in `threshold_qc.py:396-401` for the
  current `colormap`/`blending` reads. The new constants live next to
  them.

**Test scenarios:**
- Happy path (propagation, monkeypatch): `monkeypatch.setattr(vp,
  "THRESHOLD_QC_GROUP_IMAGE_OPACITY", 0.42)`; trigger the group-image
  add path (via the existing threshold-QC controller test fixture if
  present, or a focused unit test that calls the relevant method); assert
  the resulting layer's `opacity == 0.42`. If the controller can't be
  test-instantiated cheaply, mock `viewer.add_image` and assert
  `mock.call_args.kwargs["opacity"] == 0.42`.
- Happy path (None ships): with default constants, the same trigger
  results in the layer using napari's defaults — opacity 1.0 and
  auto-clip from data range.
- Happy path (contrast propagation): same monkeypatch pattern with
  `THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE = (10.0, 200.0)`; assert
  `layer.contrast_limits == (10.0, 200.0)`.

**Verification:**
- `grep -n "THRESHOLD_QC_GROUP_IMAGE_OPACITY\|THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE" src/percell4/gui/threshold_qc.py`
  shows both reads at the group-image call site.
- Propagation tests above pass.
- Module imports cleanly; ruff stays at the same pre-existing
  error count for this file.

---

- U4. **Wire `flim_panel.py` lifetime opacity + contrast override**

**Goal:** The lifetime `add_image` call at `flim_panel.py:511-516` reads
`vp.FLIM_LIFETIME_OPACITY` and `vp.FLIM_LIFETIME_CONTRAST_OVERRIDE` via
the `None`-aware pop. Same shape as U3 (specialized image call,
direct napari rather than `ViewerWindow.add_image`).

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`

**Approach:**
- Mirror U3 at the lifetime `add_image` call. Conditionally include
  `opacity=vp.FLIM_LIFETIME_OPACITY` and
  `contrast_limits=vp.FLIM_LIFETIME_CONTRAST_OVERRIDE` only when the
  respective constants are non-`None`.

**Patterns to follow:**
- U3.

**Test scenarios:**
- Happy path (opacity propagation): monkeypatch `vp.FLIM_LIFETIME_OPACITY
  = 0.42`; trigger the lifetime-add path (via the FLIM panel test
  harness if present, or `mock` patching `viewer.add_image` and asserting
  on the captured kwargs); confirm the layer's opacity reflects the
  override.
- Happy path (contrast propagation): monkeypatch
  `vp.FLIM_LIFETIME_CONTRAST_OVERRIDE = (0.5, 5.0)`; same trigger;
  confirm `layer.contrast_limits == (0.5, 5.0)`.
- Happy path (None ships): with default constants, the lifetime layer
  uses napari's defaults (opacity 1.0, auto-clip).

**Verification:**
- `grep -n "FLIM_LIFETIME_OPACITY\|FLIM_LIFETIME_CONTRAST_OVERRIDE" src/percell4/interfaces/gui/task_panels/flim_panel.py`
  shows both reads at the lifetime call site.
- Propagation tests above pass.

---

- U5. **Wire yellow-ROI opacity at both shape call sites through `vp.YELLOW_ROI_OPACITY`**

**Goal:** Both `add_shapes` call sites for yellow ROI rectangles —
`threshold_qc.py:431-440` and `analysis_panel.py:387-394` — read
`vp.YELLOW_ROI_OPACITY` via the `None`-aware pop. Today neither passes
`opacity=`; with the constant `None`, both continue to omit it. The
existing call-site drift (analysis_panel omits `blending=`) is preserved
exactly — opacity is a separate axis and adding it doesn't conflict.

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/threshold_qc.py`
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py`

**Approach:**
- Both call sites: conditionally include
  `opacity=vp.YELLOW_ROI_OPACITY` only when non-`None`.
- The existing inline comment in `analysis_panel.py:388-390` ("Drift
  preserved...this call site does NOT pass blending=") gets a sibling
  line: "Same `None`-sentinel discipline for opacity — adding to
  vp.YELLOW_ROI_OPACITY (currently None) auto-applies here without
  touching this file." This documents the symmetry for the next reader.

**Patterns to follow:**
- U3.
- The existing drift-comment style at `analysis_panel.py:388-390`.

**Test scenarios:**
- Happy path (propagation, threshold-QC site): monkeypatch
  `vp.YELLOW_ROI_OPACITY = 0.42`; trigger the threshold-QC ROI add path;
  assert the resulting shapes layer's `opacity == 0.42`. Same shape as
  U3/U4 — use the controller test fixture or a `mock` patching of
  `viewer.add_shapes` and capture kwargs.
- Happy path (propagation, analysis-panel site): same monkeypatch, same
  pattern, against the analysis-panel ROI add path.
- Happy path (None ships): with default `vp.YELLOW_ROI_OPACITY = None`,
  both call sites omit the kwarg and napari's shape default 0.7 applies.

**Verification:**
- `grep -n "YELLOW_ROI_OPACITY"
  src/percell4/gui/threshold_qc.py
  src/percell4/interfaces/gui/task_panels/analysis_panel.py`
  shows the read at both call sites.
- Propagation tests above pass.

---

- U6. **Visual smoke pass + final automated checks**

**Goal:** Confirm zero behavior regression by exercising the affected
layer kinds with the constants at their day-one values (all new ones
`None`), then by flipping ONE constant per axis to a numeric override and
confirming the override propagates. Run final greps and the full test
suite.

**Requirements:** R2, R3

**Dependencies:** U1, U2, U3, U4, U5

**Files:** No source edits in this unit.

**Approach:**
- **Phase A — day-one preservation.** Launch the app:
  `source .venv/bin/activate && python main.py`. Open a real PerCell4
  experiment dataset. For each affected layer kind, confirm the on-screen
  appearance is identical to pre-PR:
  1. Load TIFFs; confirm image opacity is napari default (1.0) and
     contrast comes from data.
  2. Create a segmentation labels layer; confirm opacity is napari
     default (0.7) and the colormap is napari's random palette.
  3. Open threshold QC; confirm group-image preview opacity is napari
     default (1.0).
  4. Open FLIM analysis; confirm lifetime image opacity is napari
     default (1.0) with the turbo colormap.
  5. Trigger threshold QC ROI rectangle; confirm the yellow rectangle
     looks exactly as before (face alpha effectively 0.07).
  6. Trigger analysis-panel threshold preview; confirm the yellow
     rectangle looks exactly as before.

- **Phase B — propagation coverage is automated.** No live-app
  edit-and-revert procedure. The propagation guarantee ("editing a
  constant changes what reaches napari") is enforced by the
  monkeypatch tests added in U2–U5: each new axis has a parametrized
  test that monkeypatches the constant to a sentinel value, calls the
  wrapper, and asserts the kwarg reached napari. These tests are
  permanent regression coverage and never risk shipping a stray edit.

- **Phase C — automated checks.**
  - `grep -rnE 'opacity\s*=\s*[0-9]' src/percell4/gui/ src/percell4/interfaces/gui/` —
    every match should be a `vp.*` reference, not a numeric literal,
    except where a numeric is the canonical drift constant.
  - **Baseline-diff test capture.** Before any U-unit lands, capture
    `pytest tests/test_config/ tests/test_gui/ tests/test_gui_workflows/ --tb=no -q`
    output to a scratch file; after U6 completes, capture the same
    output and diff. Net-new failures AND failure-mode swaps within an
    existing test ID both count as regressions to investigate. "Same
    failure count" is not the gate — "same failure set" is.
  - `python3 scripts/learnings_applicability.py
    src/percell4/config/viewer_presets.py` — confirm no T1 hook warnings.

**Patterns to follow:**
- The just-shipped plan's U8 visual smoke pass is the precedent. Same
  seven UI areas, same per-area independence.
- `docs/solutions/ui-bugs/ui-theme-refactor-lessons.md` — verify each
  affected area independently; do not batch-verify.

**Test scenarios:**
- Test expectation: none for U6 itself — this is human-in-the-loop visual
  verification. Automated propagation coverage lands with U2–U5
  monkeypatch tests; this unit covers the residual visual-rendering
  question that automation can't answer (does the rectangle still look
  like a yellow ROI rectangle).

**Verification:**
- All affected UI areas in Phase A look identical to pre-PR. Any visual
  diff is a regression and must be fixed before merge.
- U2–U5 propagation tests are green (these prove the constants reach
  napari; no live-app verification is needed for the plumbing question).
- Phase C greps return the expected results; baseline-diff capture
  shows the same failure set as before this PR — no net-new failures and
  no failure-mode swaps within an existing test ID.

---

## System-Wide Impact

- **Interaction graph:** None of the existing subscribers in
  `docs/audits/subscriber-rebind-matrix.md` change their read sets. The
  new constants are read by `viewer.py`, `threshold_qc.py`,
  `flim_panel.py`, and `analysis_panel.py` only — same call-site
  geography as the just-shipped refactor. The matrix's cache columns are
  unaffected (no semantic changes to `_update_label_display` or its
  consumers).
- **Error propagation:** Unaffected. `None`-aware pops add a single
  `is not None` check; no new failure modes. An `ImportError` on a new
  constant would be caught at first app launch.
- **State lifecycle risks:** None. New constants are immutable `Final`-
  typed; helpers (if any) stay pure.
- **API surface parity:** `ViewerWindow.add_image` and `add_labels` gain
  no new positional or keyword arguments. The `None`-aware pop is purely
  internal. External callers and tests see the same signatures.
- **Integration coverage:** The same multi-select e2e + keystroke + push
  tests cover the modified paths. No new integration scenarios are
  introduced; the `None` sentinel design ensures preservation.
- **Unchanged invariants:** Selection RGBAs unchanged. Drift constants
  (`GROUPED_SEG_CLEANUP_PREVIEW_OPACITY`, `analysis_panel.py:391`
  no-`blending=` site) preserved. Classification constants
  (`PERCELL_TYPE_KEY`, `LAYER_TYPE_*`) untouched. The `CHANNEL_COLORMAPS`
  deprecation shim at `viewer.py:26` stays.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **`None`-aware pop accidentally drops the kwarg even when the caller passed it explicitly.** A bug in the pop pattern — e.g., `kwargs.pop("opacity", None)` followed by an unconditional `if value is not None: pass else skip` — would swallow caller overrides. | The pattern must pop with the `vp.*` default as fallback (not `None`), then conditionally re-inject. The default is the per-section constant; the constant being `None` only triggers skip when the caller also didn't pass. The implementer must verify with a quick mental test: caller passes `opacity=0.9` → pop returns `0.9` → `0.9 is not None` → reinjected. Constant is `None`, caller didn't pass → pop returns `None` → skipped. U2's verification step calls this out explicitly. |
| **Visual regression from inadvertent typo on an existing numeric constant.** Refactor adds new constants but the implementer might inadvertently edit an existing one (e.g., change `MASK_DEFAULT_OPACITY` from `0.75` to `0.5`). | Section ordering is preserved and new constants are added at the END of each section block (per U1 Approach). Diff review on the PR catches edits to non-target lines. Phase A of U6 is the visible safety net — any change to an existing constant produces a visible difference at smoke time. |
| **Docstring updates drift from runtime behavior.** A user reading the file might assume `LABELS_DEFAULT_OPACITY = None` means "always defer" when the actual contract is "defer when caller didn't pass `opacity=`." | The docstring update in U1 specifies the contract precisely. Implementer keeps the wording terse but unambiguous. |
| **A future contributor adds an `IMAGE_DEFAULT_COLORMAP` constant that conflicts with the per-channel auto-resolution.** | Key Technical Decision #4 documents this explicitly: do not add `IMAGE_DEFAULT_COLORMAP`. The reason lives both in the plan and in a brief comment in the `IMAGE` section of the file ("colormap is auto-resolved per channel via `_colormap_for_channel`; do not add a global `IMAGE_DEFAULT_COLORMAP`"). |
| **Day-one behavior subtly changes due to napari version update mid-refactor.** Today's defaults (image 1.0, labels 0.7, shapes 0.7) are tied to napari 0.7.0. If napari 0.8 changes them, our `None` ships start producing different output. | Acceptable risk for now: pinned to `napari>=0.5,<0.8`. If napari 0.8 is adopted later, a sweep can flip selected `None`s to the napari-0.7-equivalent numeric to lock in today's appearance — a 5-minute change in this single file. |
| **Test suite has pre-existing flake in `test_gui_workflows`** (one cross-test ordering failure documented in the just-shipped refactor). | Same workaround applies: run failing tests in isolation to confirm they pass independently. Not introduced by this refactor. |

---

## Documentation / Operational Notes

- The file's docstring update is the only doc change. The user explicitly
  wants the file itself to be self-documenting.
- No `gui/CLAUDE.md` or `src/percell4/CLAUDE.md` updates needed —
  `viewer_presets.py` is already mentioned in the just-shipped refactor's
  doc updates; this refactor only adds knobs, not new files or modules.
- No `docs/solutions/` entry expected unless an unforeseen gotcha
  surfaces during U6 (visual smoke pass). If it does, defer to
  `ce-compound` after merge.
- No rollout, monitoring, or migration concerns. Local refactor; same
  blast radius as the just-shipped refactor.

---

## Sources & References

- **Origin:** Direct user request 2026-05-05 ("plan a refactor of the
  viewer_presets config so that all configuration options are available
  for all types of layers"). No upstream brainstorm.
- **Immediate prior art:**
  `docs/plans/2026-05-05-001-refactor-napari-viewer-presets-config-plan.md`
  (the refactor that created `viewer_presets.py`; merged earlier today).
- **Module under refactor:** `src/percell4/config/viewer_presets.py`
- **Affected files:**
  - `src/percell4/gui/viewer.py` (add_image, add_labels)
  - `src/percell4/gui/threshold_qc.py` (group image, yellow ROI)
  - `src/percell4/interfaces/gui/task_panels/flim_panel.py` (lifetime)
  - `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (yellow ROI)
- **Test files:**
  - `tests/test_config/test_viewer_presets.py` (extend)
  - `tests/test_gui_workflows/test_session_to_napari_push.py` (regression
    coverage; no changes)
  - `tests/test_gui_workflows/test_multi_select_e2e.py`,
    `test_multi_select_keystroke.py` (regression coverage; no changes)
- **Critical learnings:**
  - `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`
    (selection RGBAs immutable)
  - `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
    (layer-kind taxonomy)
  - `docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`
    (peer-view → launcher seam)
  - `docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md`
    (YAGNI bar)
  - `docs/solutions/ui-bugs/ui-theme-refactor-lessons.md`
    (visual smoke-test discipline)
  - `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md`
    Issue #4 (no module-level mutable state)
- **Audit references:**
  - `docs/audits/canonical-sources-matrix.yaml` —
    `viewer_presets.py` is not in T1 scope; PreToolUse hook stays silent
    on edits.
- **External:** napari 0.7.0 layer defaults verified from installed
  package source (`.venv/lib/python3.12/site-packages/napari/layers/`).
