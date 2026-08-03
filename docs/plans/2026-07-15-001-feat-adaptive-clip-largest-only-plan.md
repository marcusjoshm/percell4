---
title: "feat: Largest-particle-only single-pass mode for Adaptive Local Clipping"
type: feat
status: completed
date: 2026-07-15
---

# feat: Largest-particle-only single-pass mode for Adaptive Local Clipping

## Overview

The Adaptive Local Clipping GUI module currently offers one detection mode: the
two-pass `auto_extract` — a **fine pass** (window = `fill_factor × smallest
particle Ø`, k = 1, catches small puncta) OR-unioned with a **coarse pass**
(window = `fill_factor × largest particle Ø` measured by LoG, k = the per-cell
noise-symmetry floor, fills the large particles). This adds a **"Largest particle
only (single pass)"** checkbox that runs *only the coarse pass* — sized to the
largest particle, skipping the fine/small-window pass entirely.

The use case: on images where only the large features matter (or where the fine
pass adds noise/small junk the user does not want), a single coarse pass gives a
cleaner, faster result. This is a **module-only** change — the checkbox lives in
the panel, and no batch-workflow (`workflows/phases.py`) or CLI surface is
touched.

---

## Problem Frame

The two-pass routine is the right default when a field spans small *and* large
puncta, but it forces a fine pass the user cannot switch off. When a dataset has
only large features, the fine pass (a small window at k=1) is pure overhead: it
detects small-scale texture/noise the user then has to filter out, and it costs a
second detection pass plus the smallest-particle sizing (LoG autodetect or a
manual optical-resolution entry). The user wants a one-click way to say "just do
the coarse pass on the largest particle" — the exact second half of the existing
routine, run alone.

The coarse pass already exists inside `auto_extract` and is fully eye-validated
(window rule `fill_factor × largest`, per-cell noise-symmetry-floor k, per-cell
σ). This feature exposes it standalone; it invents no new thresholding math.

---

## Requirements Trace

- R1. A **"Largest particle only (single pass)"** checkbox in the Adaptive Local
  Clipping panel runs a single coarse pass sized to the LoG-measured largest
  particle (window = `fill_factor × largest Ø`, k = per-cell noise-symmetry
  floor), skipping the fine/small-window pass.
- R2. When largest-only is on, the smallest-particle controls (the "Auto-detect
  smallest (LoG)" toggle, the smallest-Ø field, and its unit) are disabled —
  there is no fine pass that consumes them. Gaussian σ and Min particle size stay
  live.
- R3. Largest-only detection **reuses** the existing eye-validated coarse-pass
  building blocks (`measure_largest_particle_diameter`, `per_cell_sigma`,
  `noise_symmetry_floor_k_per_cell`, `detect_adaptive_per_cell` with
  `fill_holes`, `_filter_by_area`). It introduces no new window/k rule (see
  origin: `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md`).
- R4. A time-lapse `(T,H,W)` channel runs largest-only **per frame**; a frame
  with no sizable particle degrades to an empty plane rather than failing the run
  (mirrors the existing auto-detect R9 behavior).
- R5. No batch-workflow or CLI change — the mode exists only in the GUI module.

---

## Scope Boundaries

- **Not** added to `workflows/phases.py` (the "Adaptive sigma clipping"
  thresholding round), the workflow config dialogs, or any CLI entry point —
  explicitly module-only per the request.
- No change to the two-pass default behavior when the checkbox is off — the
  existing fine+coarse path is untouched.
- No change to the CNR classification / interactive-segmenter tools in the same
  panel.
- No new window-finder or k-selection rule; the coarse-pass rules are reused
  verbatim.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/domain/measure/auto_extraction.py` — `auto_extract` (the two-pass
  engine). The coarse-pass block (`auto_extraction.py:423-457`) is exactly what
  largest-only runs alone: `measure_largest_particle_diameter` →
  `coarse_window = _win(fill_factor × largest)` →
  `noise_symmetry_floor_k_per_cell` → `detect_adaptive_per_cell(..., k=k_by_cell,
  fill_holes=fill_holes)`. `AutoExtractReport` is the return descriptor;
  `NoParticlesFoundError` is the recoverable "nothing to size" signal.
- `src/percell4/gui/_adaptive_clip_settings.py` — `AdaptiveClipSettingsWidget` +
  frozen `AdaptiveClipConfig`. `_apply_mode_gating` is the existing pattern for
  enabling/disabling fields off a checkbox; `set_enabled` locks the form during a
  run. Mirrors `_grouped_threshold_settings.py`.
- `src/percell4/gui/adaptive_clip_panel.py` — `AdaptiveClipPanel` (Creator).
  `run_adaptive_auto_extract` / `run_adaptive_auto_extract_stack` are the pure
  worker bodies; `_run_auto_extract_mode` is the dispatch;
  `_on_auto_extract_done` / `_print_auto_extract_report` handle the report and
  the smallest-Ø readout back-fill.

### Institutional Learnings

- `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md` —
  the canonical explanation of the window/k rules. Largest-only must **reuse**
  these (window = `fill_factor × largest`, per-cell k, per-cell σ), not invent a
  variant. Key constraint: the window is a fixed *pixel* length derived from a
  physical particle size — do not re-derive it.
- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`
  (canonical: `src/percell4/gui/threshold_qc.py`) — the panel is a Creator; the
  Run path already follows store-before-layer save via `AcceptPunctaMask`. This
  feature does not change the save path, only what mask is computed.

### Learnings-retrieval note

`domain/measure/auto_extraction.py` has no applicable canonical-source entries.
The two GUI files match `gui/**/*.py` globs (Creator-contract, adaptive-clip
window/k rules) — folded into the requirements above. Neither GUI file is a
`*Dialog.py` T1 module, so the PreToolUse hook will not fire; the learnings are
consulted here instead.

---

## Key Technical Decisions

- **Dedicated domain function `extract_largest_only`, not a flag on
  `auto_extract`.** Rationale: `auto_extract`'s identity is *two-pass* (its
  docstring, report fields, and tests are all built around fine+coarse). Threading
  a `largest_only` branch through it muddies that contract and every existing
  test's assumptions. A small standalone function composed from the same public
  helpers keeps the two-pass engine pristine, reads clearly at the call site, and
  reuses every eye-validated building block. The module already exports those
  helpers in `__all__`, so composition is the idiomatic move here.
- **Reuse `AutoExtractReport` with a new `largest_only: bool` field** rather than
  a new report type — so the panel's existing report plumbing
  (`_on_auto_extract_done`, the stack `None`-report degradation) works with
  minimal branching. Largest-only sets `fine_window=0`, `second_pass_used=False`,
  `smallest_source="n/a (largest-only single pass)"`, `smallest_diameter_px=0.0`,
  `passes=[(coarse_window, round(k_mean, 2))]`, and populates the `coarse_k_*`
  stats + `largest_particle_px`.
- **Raise `NoParticlesFoundError` when no largest blob is found** (LoG returns
  Ø 0). This mirrors the auto-detect-smallest contract and lets the time-lapse
  stack worker degrade that frame to an empty plane via its existing `except
  NoParticlesFoundError` (R4).
- **Worker functions gain a `largest_only=False` kwarg** and dispatch internally
  to `extract_largest_only` vs `auto_extract` — one worker entry point per
  (2D / stack), so the panel's Worker wiring stays as-is.
- **Gating: largest-only disables the smallest-particle sub-controls.** With no
  fine pass, the auto-detect toggle + smallest-Ø field + unit are meaningless, so
  they are disabled (R2), and the panel skips smallest-Ø resolution and the
  post-run smallest readout back-fill.

---

## Open Questions

### Resolved During Planning

- Param on `auto_extract` vs a new function? → New function `extract_largest_only`
  (see Key Technical Decisions).
- Does largest-only still require an active segmentation? → Yes — it is per-cell
  (per-cell σ + per-cell noise floor), so the panel's existing "needs an active
  segmentation" guard in `_run_auto_extract_mode` is unchanged.
- Should `fill_holes` be on for the single coarse pass? → Yes, matching the
  two-pass coarse pass (a large particle under-windowed into a ring is closed
  before the size filter).

### Deferred to Implementation

- Exact wording of the terminal debug line for largest-only in
  `_print_auto_extract_report` — decided when the report branch is written.
- Whether to keep the disabled smallest-Ø field showing its last value or blank
  it while largest-only is active — a cosmetic call made at the widget.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review,
> not implementation specification. The implementing agent should treat it as
> context, not code to reproduce.*

```
Checkbox OFF (default, unchanged):
  auto_extract → fine pass (3×smallest, k=1)  ∪  coarse pass (3×largest, k=floor)

Checkbox ON (new):
  extract_largest_only → coarse pass ONLY
      largest_px = measure_largest_particle_diameter(img, labels)   # LoG p99
      if largest_px <= 0: raise NoParticlesFoundError               # → empty frame in stack
      coarse_window = _win(fill_factor × largest_px)
      k_by_cell     = noise_symmetry_floor_k_per_cell(work, labels, σ, coarse_window)
      mask          = detect_adaptive_per_cell(..., window=coarse_window,
                                               k=k_by_cell, fill_holes=True)
      mask          = _filter_by_area(mask, min_spot_px)
      report.largest_only = True
```

Panel dispatch:

```
config = settings.current_config()          # + config.largest_only
if config.largest_only:
    worker(run_adaptive_auto_extract[_stack], ..., largest_only=True)
    # skip smallest-Ø resolution; no readout back-fill
else:
    # existing two-pass path unchanged
```

---

## Implementation Units

- U1. **Domain: `extract_largest_only` single coarse-pass function**

**Goal:** A pure-domain function that runs only the coarse pass (largest particle)
and returns `(mask uint8, AutoExtractReport)`, reusing the existing coarse-pass
helpers.

**Requirements:** R1, R3, R4

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/measure/auto_extraction.py` (add
  `extract_largest_only`; add `largest_only: bool = False` field to
  `AutoExtractReport`; export the function in `__all__`)
- Test: `tests/test_measure/test_auto_extraction.py`

**Approach:**
- Signature mirrors the coarse half of `auto_extract`: `extract_largest_only(image,
  cell_labels, *, fill_factor=FILL_FACTOR, fdr=FDR, log_presmooth=LOG_PRESMOOTH,
  presmooth_sigma_px=1.0, min_spot_px=2, size_percentile=SIZE_PERCENTILE,
  max_sigma=MAX_SIGMA, fill_holes=True)`.
- Measure largest via `measure_largest_particle_diameter` (LoG, fixed
  `log_presmooth`). If `largest_px <= 0`, raise `NoParticlesFoundError` (nothing
  to size).
- `coarse_window = _win(fill_factor × largest_px)`; build `work =
  apply_gaussian_smoothing(img, presmooth_sigma_px)` and `sigma =
  per_cell_sigma(work, labels)`; `k_by_cell = noise_symmetry_floor_k_per_cell(work,
  labels, sigma, coarse_window, fdr=fdr, k_floor=1.0)`.
- If `k_by_cell` is empty (every cell flat/σ-less), the pass would add nothing —
  return an empty mask + a report with `n_components=0`, `area_px=0`,
  `coarse_k_n=0` (do **not** raise; an empty selection is not a sizing failure).
- Else `mask = detect_adaptive_per_cell(img, labels, window_px=coarse_window,
  min_spot_px=1, k=k_by_cell, presmooth_sigma_px=presmooth_sigma_px,
  fill_holes=fill_holes)`, then `_filter_by_area(mask, min_spot_px)`.
- Build `AutoExtractReport(largest_only=True, passes=[(coarse_window,
  round(coarse_k_mean, 2))], fine_window=0, largest_particle_px=largest_px,
  second_pass_used=False, smallest_diameter_px=0.0, smallest_source="n/a
  (largest-only single pass)", coarse_k_mean/min/max/n=…, presmooth_sigma_px,
  n_cells=len(sigma), n_components, area_px, extra={"fdr": fdr})`.

**Patterns to follow:**
- The coarse-pass block of `auto_extract` (`auto_extraction.py:423-457`) — same
  helper calls, same per-cell-k stats computation.
- `AutoExtractReport` construction at `auto_extraction.py:462-478`.

**Test scenarios:**
- Happy path: on a wide-size-range image, `extract_largest_only` returns a
  non-empty mask; `report.largest_only is True`, `report.second_pass_used is
  False`, `len(report.passes) == 1`, `report.fine_window == 0`,
  `report.largest_particle_px > 0`, `report.coarse_k_n >= 1`, and
  `report.passes[0] == (coarse_window, round(report.coarse_k_mean, 2))`.
- Happy path (fills large particle): the center pixel of a large synthetic
  particle is detected (`mask[cy, cx] == 1`) — the coarse window + `fill_holes`
  fill it solid (contrast with a small-window ring).
- Window rule: `report.passes[0][0] == max(3, round(FILL_FACTOR ×
  report.largest_particle_px))` — window follows the `fill_factor × largest`
  rule, no new constant.
- Error path: a flat (blob-free) image raises `NoParticlesFoundError` (LoG finds
  no largest particle).
- Edge case: min-size filter applied — a `min_spot_px` above the detected
  component area yields an empty mask (filter runs once at the end).
- Edge case: reuses per-cell σ — a two-cell image where cells differ in
  brightness both contribute (`report.coarse_k_n == 2`), confirming per-cell
  path (contrast with pooled).

**Verification:**
- `extract_largest_only` produces the same mask as the coarse-only portion of
  `auto_extract` on an image whose fine pass contributes nothing (i.e. the union
  reduces to the coarse pass), demonstrating it is the second pass run alone.

---

- U2. **GUI settings: "Largest particle only" checkbox + config field + gating**

**Goal:** Add the checkbox to `AdaptiveClipSettingsWidget`, surface it on
`AdaptiveClipConfig`, and disable the smallest-particle sub-controls when it is on.

**Requirements:** R1, R2

**Dependencies:** None (parallel with U1)

**Files:**
- Modify: `src/percell4/gui/_adaptive_clip_settings.py` (add `_largest_only`
  `QCheckBox`; add `largest_only: bool` to `AdaptiveClipConfig`; extend
  `_apply_mode_gating`, `_connect_change_signals`, `set_enabled`)
- Test: `tests/test_gui/test_adaptive_clip_settings_widget.py`

**Approach:**
- Add `self._largest_only = QCheckBox("Largest particle only (single pass)")`,
  unchecked by default, near the top of the form with a tooltip explaining it runs
  only the coarse pass sized to the largest particle and skips the small-window
  pass. Wire `toggled` → `_apply_mode_gating` and → `config_changed`.
- Extend `_apply_mode_gating`: when `_largest_only.isChecked()`, disable
  `_ae_smallest_auto`, `_smallest`, `_smallest_unit` (no fine pass). When off,
  fall back to the current auto-detect gating. Gaussian σ + min size always live.
- Add `largest_only=self._largest_only.isChecked()` to `current_config()`.
- Include `_largest_only` in `set_enabled`'s widget tuple and re-apply gating on
  unlock.

**Patterns to follow:**
- The existing `_ae_smallest_auto` checkbox wiring and `_apply_mode_gating`
  (`_adaptive_clip_settings.py:84-93, 164-173`).

**Test scenarios:**
- Happy path: default `current_config().largest_only is False`; the existing
  `test_default_config` frozen dataclass is updated to include the new field.
- Happy path: checking `_largest_only` sets `current_config().largest_only is
  True`.
- Gating (R2): with `_largest_only` checked, `_ae_smallest_auto`, `_smallest`,
  and `_smallest_unit` are all disabled; with it unchecked, `_smallest`'s enabled
  state follows the auto-detect toggle again (auto on → disabled, auto off →
  enabled).
- Signal: toggling `_largest_only` emits `config_changed` (qtbot `waitSignal` or
  a spy).
- `set_enabled(False)` disables `_largest_only`; `set_enabled(True)` re-enables
  and re-applies gating (smallest controls stay disabled if largest-only is on).

---

- U3. **Panel: thread `largest_only` through workers + dispatch + report**

**Goal:** Route the config flag into the worker bodies, skip smallest-Ø
resolution and the readout back-fill when largest-only is on, and print a coherent
report. Time-lapse handled per frame.

**Requirements:** R1, R4, R5

**Dependencies:** U1, U2

**Files:**
- Modify: `src/percell4/gui/adaptive_clip_panel.py`
  (`run_adaptive_auto_extract`, `run_adaptive_auto_extract_stack`,
  `_run_auto_extract_mode`, `_on_auto_extract_done`, `_print_auto_extract_report`,
  `_print_settings_debug`)
- Test: `tests/test_gui/test_adaptive_clip_panel.py`,
  `tests/test_gui/test_adaptive_clip_timelapse.py`

**Approach:**
- `run_adaptive_auto_extract(image, labels, smallest_particle_px,
  presmooth_sigma_px, min_spot_px, largest_only=False)`: if `largest_only`, call
  `extract_largest_only(image, labels, presmooth_sigma_px=presmooth_sigma_px,
  min_spot_px=min_spot_px)`; else the existing `auto_extract` call. Same for the
  `_stack` variant (loop unchanged; its existing `except NoParticlesFoundError`
  already degrades a no-particle frame to an empty plane — R4).
- `_run_auto_extract_mode`: read `config.largest_only`. When on, skip the whole
  smallest-Ø resolution branch (pass `smallest_px=None` — ignored by the
  largest-only worker path), set `self._pending_ae_auto = False` (no readout
  back-fill), and thread `largest_only=True` into the `Worker(worker_fn, …)`
  call. Still requires an active segmentation (guard unchanged). Min particle size
  resolution unchanged.
- `_on_auto_extract_done`: unchanged except the back-fill is already gated on
  `_pending_ae_auto` (False for largest-only), so no smallest readout write.
- `_print_auto_extract_report`: branch on `report.largest_only` to print a
  single-pass-coarse line (largest Ø, coarse window, per-cell k spread) instead of
  the fine-window/second-pass line.
- `_print_settings_debug`: include `largest_only` in the dumped settings.

**Patterns to follow:**
- `_run_auto_extract_mode` worker-selection (`adaptive_clip_panel.py:404-503`) and
  the `is_timelapse` worker_fn choice.
- The existing `_pending_ae_auto` back-fill guard (`adaptive_clip_panel.py:542`).

**Test scenarios:**
- Happy path (2D): with a stubbed config where `largest_only=True`, `_on_run`
  runs and saves a mask; the domain call made is `extract_largest_only` (patch/spy
  it) and **not** `auto_extract`. The saved mask is persisted via
  `AcceptPunctaMask` (existing Creator save unchanged).
- Dispatch guard: `largest_only=True` with no active segmentation shows the "needs
  an active segmentation" status and runs nothing (per-cell still required).
- No back-fill: after a largest-only run, `set_smallest_value` is **not** called
  (`_pending_ae_auto` stayed False) — the smallest-Ø readout is untouched.
- Worker plumbing: `run_adaptive_auto_extract(..., largest_only=True)` returns a
  `(mask, report)` with `report.largest_only is True` on a synthetic image.
- Time-lapse (R4): `run_adaptive_auto_extract_stack(..., largest_only=True)` on a
  `(T,H,W)` image returns a `(T,H,W)` mask; a blob-less frame degrades to an empty
  plane with a `None` report (reuse the timelapse test's degradation pattern).
- Off path unchanged: `largest_only=False` still calls `auto_extract` (regression
  guard that the default two-pass path is intact).

**Verification:**
- Launching the panel, checking "Largest particle only", and running on a dataset
  with an active segmentation produces a mask from a single coarse pass; the
  terminal report shows `largest_only` / one pass; the two-pass behavior is
  unchanged with the box unchecked.

---

## System-Wide Impact

- **Interaction graph:** The checkbox is a plain settings knob (Action-shaped,
  Session-agnostic) read at Run time; it writes no session field. The Creator save
  path (`AcceptPunctaMask` → viewer add) is unchanged.
- **Error propagation:** `extract_largest_only` raises `NoParticlesFoundError`
  (a `ValueError` subclass) on no largest blob; the 2D worker surfaces it via the
  Worker `error` signal (`_on_detect_error`), the stack worker catches it per
  frame → empty plane. Matches the existing auto-detect contract.
- **State lifecycle risks:** None new — no partial writes; the mask is saved once
  on success as today.
- **API surface parity:** Intentionally none — R5 keeps this out of
  `workflows/phases.py` and the CLI. The workflow's "Adaptive sigma clipping"
  round keeps its two-pass/`AdaptiveClipSettings` behavior.
- **Unchanged invariants:** `auto_extract`'s two-pass contract, signature, report
  semantics (minus the additive `largest_only` field default), and all existing
  tests remain valid; the panel's default (box off) path is byte-for-byte the
  current behavior.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Adding `largest_only` to `AutoExtractReport` breaks existing report construction/tests | Give it a default (`largest_only: bool = False`) so every current `AutoExtractReport(...)` call and equality/field assertion is unaffected. |
| `AdaptiveClipConfig` gaining a field breaks the frozen-dataclass equality test | Update `test_default_config` (and any config-construction in panel tests) to include `largest_only=False` — the one intended test change. |
| Largest-only on an image with only small particles yields an empty/degenerate mask (largest ≈ small) | Expected and correct — the mode is explicitly "largest only"; `NoParticlesFoundError`/empty-mask paths handle the degenerate end, and the user chose to skip the fine pass. |
| Re-deriving the window/k rule instead of reusing helpers | U1 composes the exact coarse-pass helpers; the window-rule test asserts `fill_factor × largest`, guarding against drift from the canonical convention doc. |

---

## Sources & References

- Related code: `src/percell4/domain/measure/auto_extraction.py` (`auto_extract`
  coarse pass), `src/percell4/gui/adaptive_clip_panel.py`,
  `src/percell4/gui/_adaptive_clip_settings.py`
- Convention: `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md`
- Creator contract: `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`
- Origin brainstorm (adjacent context): `docs/brainstorms/2026-06-22-multiscale-adaptive-clip-routine-requirements.md`
