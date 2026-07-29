---
title: "refactor: Trim dev-only controls from the Session, Adaptive Local Clipping, and CNR panels"
type: refactor
status: active
date: 2026-07-22
---

# refactor: Trim dev-only controls from the Session, Adaptive Local Clipping, and CNR panels

## Overview

Three interactive panels have accumulated exploratory controls that are no longer
used in practice. This plan removes them from the GUI, renames the surviving
labels to the terminology the user actually says out loud, and preserves the
removed dev-only UI on a `dev-features` branch cut off `main` before any deletion
lands.

The removal is **GUI-only**. The domain functions behind the removed controls
(`extract_largest_only`, the `discover` path in `classify_by_cnr`, LoG
smallest-particle autodetection) stay in `domain/measure/` untouched — `discover`
is the default code path inside `classify_by_cnr` / `classify_by_cnr_stack`, and
the worker helpers in `adaptive_clip_panel.py` remain tolerant of a `None`
smallest particle. Only the widgets, config fields, and panel wiring go away.

---

## Problem Frame

The Adaptive Local Clipping panel currently presents seven detection knobs. Four
of them exist because the detector was being tuned, not because anyone sets them
per-run:

- **Largest particle only (single pass)** — an experiment in skipping the fine pass.
- **Auto-detect smallest (LoG)** — the LoG estimate is explicitly documented as
  less defensible than the user's known optical resolution ("the smallest is
  partly confounded with noise… supplying your known optical resolution is more
  defensible", `src/percell4/domain/measure/auto_extraction.py`). The manual value
  is what the workflow actually depends on.
- **Coarse window / largest Ø (×)** and **Coarse-k false-pos. rate** — tuning
  handles for the eye-validated constants `FILL_FACTOR = 3.0` and `FDR = 0.1`.
  The validated configuration is the module default; exposing them invites drift.

The CNR panel has an analogous problem: three classification modes where only two
are used, plus a second, visually subordinate button (`Segment by CNR
(interactive)`) that is really a *third mode* of the same operation — pick a
source mask, decide how to split it. Modelling it as a button rather than a mode
makes the panel read as two unrelated tools.

The Session window's `View bin (k):` uses internal vocabulary (`k` is the
downsample factor in `store.py`) for a control the user thinks of as pixel
binning.

---

## Requirements Trace

- R1. The Session window bin selector is labelled `Pixel Binning:`.
- R2. `Largest particle only (single pass)` is gone from the Adaptive Local
  Clipping panel.
- R3. `Auto-detect smallest (LoG)` is gone; the manual smallest-particle field is
  the only behaviour and is always live.
- R4. The smallest-particle field defaults to **2** (px) and is labelled
  `Smallest Particle Diameter:`.
- R5. `Coarse window / largest Ø (×):` and `Coarse-k false-pos. rate:` are gone;
  the detector runs at the module-constant defaults.
- R6. `Min particle size:` is relabelled `Min. Particle Area:`.
- R7. `Discover (auto gap)` is gone from the CNR `Mode:` dropdown.
- R8. `Guided (CNR threshold)` reads `CNR threshold`; `Forced (always 2)` reads
  `Auto Two Groups`.
- R9. A third mode, `Interactive`, launches the existing CNR segmenter window from
  the green `Classify Mask by CNR` button.
- R10. The standalone `Segment by CNR (interactive)` button is gone.
- R11. The pre-removal UI is reachable by name on a `dev-features` branch.
- R12. No domain-layer behaviour changes; the batch workflow / CLI ALC path is
  untouched.

---

## Scope Boundaries

- **No domain deletions.** `extract_largest_only`, `measure_smallest_particle_diameter`,
  and the `discover` branch of `classify_by_cnr` stay exactly as they are, with
  their existing tests (`tests/test_measure/test_auto_extraction.py`,
  `tests/test_measure/test_cnr_classification.py`) untouched.
- **No batch-workflow changes.** `src/percell4/workflows/models.py`
  (`AutoExtractSettings`), `src/percell4/gui/workflows/single_cell/config_dialog.py`,
  and `src/percell4/interfaces/cli/batch_threshold.py` never carried
  `largest_only` / `fill_factor` / `fdr`, and their own smallest-particle
  auto-detect (`smallest_particle_um = None`) is out of scope. Only the
  interactive Analysis-tab panel changes.
- **No changes to the CNR segmenter window itself.** `gui/cnr_segmenter.py` and
  `gui/metric_segmenter_panel.py` keep their current behaviour; only how the
  segmenter is *launched* changes.
- **No new detection features.** This is subtraction and renaming only.
- **The batch surface keeps its own smallest-particle auto-detect.** The interactive
  panel drops LoG auto-detect on the grounds that a known optical resolution is more
  defensible, but `AutoExtractSettings.smallest_particle_um = None` still triggers
  the same estimator in the batch dialog and CLI. After this plan there is no
  interactive path that reproduces a batch auto-detect run for spot-checking.
  Accepted for now because batch runs supply the value in practice; if that stops
  being true, aligning the two surfaces is a follow-up, not a silent divergence.
- **The batch dialog keeps its own wording** (`d_min`, `smallest particle Ø`,
  `min-size`). The relabelling in this plan covers the interactive Analysis-tab
  panel and the Session window only, so the same two parameters are named
  differently on the two surfaces until a follow-up aligns them.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/gui/_adaptive_clip_settings.py` — the ALC settings form.
  `AdaptiveClipConfig` (frozen dataclass) + `config_changed` (0-arg aggregate
  signal) + `_apply_mode_gating()`. Four of its nine fields are being removed.
- `src/percell4/gui/adaptive_clip_panel.py` — the Creator panel. Worker bodies
  (`run_adaptive_auto_extract`, `run_adaptive_auto_extract_stack`) are pure
  module-level functions so they stay unit-testable; the panel threads
  `config.*` into them.
- `src/percell4/gui/_cnr_classify_settings.py` — the CNR form. `_MODE_LABELS` /
  `_MODE_CODES` are the single source of label↔code mapping; `_apply_mode_gating()`
  enables the threshold spinbox only for `guided`.
- `src/percell4/interfaces/gui/peer_views/session_window.py:119-135` — the canonical
  (and only) Selector for `session.active_bin`.
- `src/percell4/interfaces/gui/task_panels/data_panel.py:329` — mirrors the value
  as `"Creation bin: … | View bin: …"` but never writes it.
- Both settings widgets follow the `gui/_grouped_threshold_settings.py` pattern
  (frozen `current_config()` + aggregated `config_changed`); preserve it.

### Institutional Learnings

- Per root `CLAUDE.md` → "GUI state ownership": the ALC panel is a **Creator**
  (writes `active_mask` via `AcceptPunctaMask`); the CNR classify path and the
  interactive segmenter are also Creators. Adding an `Interactive` mode does not
  change any element's classification — the green button remains a Creator in all
  three modes. `docs/audits/gui-element-classification.yaml` should be checked for
  a row naming the removed `Segment by CNR (interactive)` button.
- Per `CLAUDE.md` → "Documentation Rules": per-module `CLAUDE.md` describes
  *current state only*. The ALC/CNR bullets in `src/percell4/gui/CLAUDE.md` and
  the largest-only note in `src/percell4/domain/measure/CLAUDE.md:191` must be
  rewritten in the same commit, not left describing the removed UI.
- Per memory `project_adaptive_clip_per_cell_params.md`: `FILL_FACTOR = 3.0` and
  `FDR = 0.1` are eye-validated across four test sets. Removing the spinboxes
  locks the validated configuration in — this is the point of R5, not a regression.

---

## Key Technical Decisions

- **User-facing text uses the user's vocabulary; code vocabulary stays in code.**
  This is the governing rule for every label, tooltip, and status string this plan
  touches. The specified labels — `Pixel Binning:`, `Smallest Particle Diameter:`,
  `Min. Particle Area:`, `CNR threshold`, `Auto Two Groups`, `Interactive`,
  `Classify Mask by CNR` — are **fixed requirements, not suggestions**, and are not
  to be "improved" during implementation. Where a control's *tooltip* still explains
  it in code terms (`k×k`, `d_min`, `discover`), the tooltip is what changes, never
  the label. Any disambiguation a reviewer might want to push into a label belongs
  in the tooltip instead.
- **Preserve via branch, not flag.** Cut `dev-features` at the current `main` HEAD
  *before* the first removal commit. Rationale: a branch keeps the removed UI
  runnable by name without adding conditional branching to the panel code, which
  would defeat the purpose of the cleanup.
- **GUI-only removal.** The `discover` code path is `classify_by_cnr`'s default
  and is reached by `classify_by_cnr_stack`; removing it would touch the
  classifier's default behaviour, the stack path, and the report contract. The
  panel simply stops offering it.
- **Worker helpers stay `None`-tolerant.** `run_adaptive_auto_extract*` keeps
  accepting `smallest_particle_px=None` and keeps the `NoParticlesFoundError`
  guard in the stack loop, even though the panel now always supplies a value.
  Rationale: the guard is cheap and the helpers remain the reusable pure surface
  they were designed as. (Note: the time-lapse test that exercises this guard is
  currently **failing on `main`** — see U4's baseline note.)
- **`Interactive` is a mode, not a button.** All three modes answer the same
  question ("how should this source mask be split?"), so they belong in one
  dropdown behind one action. This also removes the ambiguity of a disabled-looking
  secondary button sitting under a green primary one.
- **The green button's label is fixed; its tooltip carries the mode.** In
  `Interactive` mode `Classify Mask by CNR` opens a histogram window and saves
  nothing until the user commits there — a different completion contract from the
  other two modes. The label stays exactly as specified; a mode-aware tooltip is
  what tells the user what the click will do. Do not retitle the button.
- **Default mode becomes `CNR threshold` (`guided`).** With `discover` gone, index
  0 is `guided`, so the threshold spinbox is enabled at startup — the existing
  `_apply_mode_gating()` already produces this with no logic change. **This is a
  behavioural change, not just a gating convenience:** today's default (`discover`)
  splits only on a statistically significant CNR gap, while `guided` at the
  spinbox's 8.0 always splits into `_low` / `_high`. It follows necessarily from
  R7 and is accepted, but it must be surfaced in the CHANGELOG alongside the other
  behavioural changes.
- **`interactive` is a GUI-only routing value and must never reach the classifier.**
  `run_cnr_classification` and `run_cnr_classification_stack` currently end in
  `else: # discover`, so a dispatch slip would silently run a discover-mode
  classification and *save* the resulting masks and table under the user's chosen
  name — a wrong scientific result rather than an error. Both functions live in
  `gui/`, so making their mode mapping total (explicit `guided` / `forced` /
  `discover` branches, `raise ValueError` on anything else) respects the
  no-domain-changes boundary.

---

## Open Questions

### Resolved During Planning

- *Should `extract_largest_only` be deleted from the domain?* No — GUI-only
  removal (user decision). It keeps its tests and stays callable.
- *Should the removed features be flag-gated or branched?* Branched —
  `dev-features` cut off `main` (user decision).
- *Does removing auto-detect make `NoParticlesFoundError` unreachable?* From the
  panel, yes: `auto_extract` only raises it in the `smallest_particle_px is None`
  branch. The guard stays anyway (see Key Technical Decisions).
- *Does anything outside the panel read `largest_only` / `fill_factor` / `fdr`?*
  No. A repo-wide grep finds them only in `_adaptive_clip_settings.py`,
  `adaptive_clip_panel.py`, `domain/measure/auto_extraction.py`, and the
  corresponding tests. `AutoExtractSettings` in `workflows/models.py` never had them.

### Deferred to Implementation

- The exact `DataPanel` wording for the `active_bin` / `creation_bin` pair. Both
  must be reworded together (they sit in one status string), and the right phrasing
  is best judged against the rendered line. The Session window's `Pixel Binning:`
  label is fixed and is the anchor the readout matches.
- The exact final wording of the `Mode:` tooltip once `discover` is gone — it
  currently describes all three old modes in one sentence and must be rewritten to
  cover threshold / two-group / interactive.
- Whether the `Interactive` mode should also reword the green button's *tooltip*
  (the button label stays `Classify Mask by CNR` per the request, but a
  mode-aware tooltip would help). Best judged with the panel on screen.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

The CNR panel's control flow is the one structural change. Today:

```
[Mode: discover|guided|forced] ──► [ Classify Mask by CNR ] ──► name prompt ─► classify worker ─► save masks
                                   [ Segment by CNR (interactive) ] ──────────► measure worker ─► CnrSegmenterWindow
```

After:

```
                                        ┌─ guided ──────┐
[Mode: CNR threshold | Auto Two Groups  ├─ forced ──────┼─► name prompt ─► classify worker ─► save masks
       | Interactive ] ─► [ Classify ]  └─ interactive ─┴─► measure worker ─► CnrSegmenterWindow
```

`_on_classify` becomes a two-way dispatch on `cfg.mode` taken *after* the shared
`_resolve_cnr_inputs(allow_timelapse=True)` pre-flight, which both paths already
call identically. The interactive branch skips the resource-name prompt (the
segmenter window owns naming at save time) and hands off to the existing
`_on_segment_cnr` body.

The ALC settings form has no structural change — it loses four widgets and its
`_apply_mode_gating()` becomes unnecessary, since the smallest-particle field is
now unconditionally live.

---

## Implementation Units

- U1. **Cut the `dev-features` preservation branch**

**Goal:** Make the pre-cleanup UI reachable by name before any deletion lands.

**Requirements:** R11

**Dependencies:** None — must land first.

**Files:** None (git operation only).

**Approach:**
- Create `dev-features` at the current `main` HEAD (`47cbfb6f`) and push it, so
  the branch point is the last commit that still contains all the removed controls.
- Add a one-line note to the branch's purpose somewhere durable — the plan file
  itself plus the CHANGELOG entry in U6 are sufficient; do not add a README stub
  that will drift.
- Confirm the working tree is clean before branching so `dev-features` is exactly
  the shipped-today state.

**Test scenarios:**
- Test expectation: none — git branch creation, no code or behaviour change.

**Verification:**
- `dev-features` exists locally and on the remote, pointing at the `main` HEAD
  that precedes U2.
- Checking out `dev-features` and launching the app shows all removed controls.

---

- U2. **Rename the Session window bin selector to `Pixel Binning:`**

**Goal:** Replace internal `k` vocabulary with the user-facing term.

**Requirements:** R1

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/session_window.py`
- Modify (optional, consistency): `src/percell4/interfaces/gui/task_panels/data_panel.py`
- Test (only if the DataPanel mirror is reworded): `tests/test_gui/test_data_panel_bin.py`

**Approach:**
- Change the `QLabel` text at the bin-selector row from `"View bin (k):"` to
  `"Pixel Binning:"`. The spinbox, its range, and `_on_bin_spin_changed` are
  unchanged — this is the canonical `active_bin` Selector and stays one.
- **De-jargon the tooltip in the same edit.** It currently reads "every store read
  downsamples by k×k at this setting… Native (k=1) storage is unchanged" — the
  exact code vocabulary the rename exists to remove, sitting one hover away from
  the new label. Reword it in the label's terms (no `k`), and use it to carry the
  distinction the label deliberately does not: this setting affects *display and
  measurement reads only*; the stored data and the binning the dataset was
  imported at are untouched.
- Update the neighbouring comment that says "View-bin selector" to match.
- Reword the `DataPanel` mirror readout so the two surfaces name one value the
  same way. Note that `DataPanel` shows this value **next to** `creation_bin`
  (`"Creation bin: … | View bin: …"`), so its wording must keep the two readouts
  distinguishable from each other while still matching the Session window's
  label — e.g. the import-time value stays clearly labelled as such.

**Naming constraint:** `Pixel Binning:` is the specified label and is not open to
revision during implementation. A reviewer flagged that it does not by itself
distinguish `active_bin` from `creation_bin`; per the Key Technical Decision on
user-facing text, that distinction is the tooltip's and the `DataPanel` readout's
job, not the label's.

**Patterns to follow:**
- The other selector labels in the same row (`Channel:`, `Mask:`,
  `Segmentation:`) — plain title-case noun plus colon.

**Test scenarios:**
- Test expectation: none for the Session-window label itself — static text, and
  no test asserts on that string.
- If the `DataPanel` mirror is also reworded, update the three `"View bin: N"`
  assertions in `tests/test_gui/test_data_panel_bin.py` (lines 81, 89, 93) to the
  new string. This is the only test coupling in U2.

**Verification:**
- The Session window row reads `Pixel Binning:` and the spinbox still writes
  `session.set_active_bin` on change.

---

- U3. **Strip the dev-only detection controls from the ALC settings form**

**Goal:** Reduce the Adaptive Local Clipping form to the four knobs that are
actually set per-run, with the new labels and default.

**Requirements:** R2, R3, R4, R5, R6

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/_adaptive_clip_settings.py`
- Test: `tests/test_gui/test_adaptive_clip_settings_widget.py`

**Approach:**
- Remove the `_largest_only` checkbox, the `_ae_smallest_auto` checkbox, the
  `_fill_factor` row, and the `_fdr` row from `_build_ui`, plus their entries in
  `_connect_change_signals` and `set_enabled`.
- Drop `largest_only`, `auto_extract_smallest_auto`, `fill_factor`, and `fdr` from
  `AdaptiveClipConfig`. The surviving fields are `gaussian_sigma`,
  `min_size_value`, `min_size_unit`, `smallest_particle_value`,
  `smallest_particle_unit`.
- Relabel `"Smallest particle Ø:"` → `"Smallest Particle Diameter:"` and set its
  default value to `2.0` (unit default stays `px`). Rewrite its tooltip: it is no
  longer "the field you use when auto-detect is off" but simply the optical
  resolution limit the fine window is 3× of. Drop the `Ø` glyph and any reference
  to the removed auto-detect toggle.
- Relabel `"Min particle size:"` → `"Min. Particle Area:"`. Value/range/unit combo
  unchanged.
- Delete `_apply_mode_gating`, `_on_ae_smallest_auto_toggled`,
  `_on_largest_only_toggled`, and `set_smallest_value` — with no auto-detect
  there is no readout mode and no gating; the smallest field and its unit are
  always enabled. `set_enabled` becomes a plain loop over the remaining widgets
  with no re-gating tail.
- Rewrite the module docstring and class docstring, which currently open by
  describing the auto-detect toggle as "the mode's one toggle".

**Patterns to follow:**
- `gui/_grouped_threshold_settings.py` — frozen `current_config()` snapshot plus a
  single aggregated `config_changed`. Preserve the signal-to-signal forwarding
  comment explaining why the 0-arg re-emit is safe.

**Test scenarios:**
- Happy path: a fresh widget's `current_config()` returns
  `smallest_particle_value == 2.0`, `smallest_particle_unit == "px"`,
  `gaussian_sigma == 1.0`, `min_size_value == 3.0`, `min_size_unit == "px"` — and
  the dataclass has no `largest_only` / `auto_extract_smallest_auto` /
  `fill_factor` / `fdr` attribute.
- Happy path: the smallest-particle spinbox and its unit combo are **enabled** on
  construction (previously disabled by the auto-detect gate).
- Happy path: setting the smallest value to `5.0` and the unit to `µm` reaches
  `current_config()` as `smallest_particle_value == 5.0`,
  `smallest_particle_unit == "um"`.
- Happy path: `set_enabled(False)` disables every remaining widget;
  `set_enabled(True)` re-enables **all** of them including the smallest-particle
  field and unit (the old gating no longer suppresses them).
- Edge case: editing each surviving widget emits `config_changed` exactly once per
  user edit.
- Delete the tests covering removed behaviour: `test_largest_only_*`,
  `test_auto_extract_gates_smallest_field`,
  `test_auto_extract_uncheck_auto_enables_manual_smallest`,
  `test_auto_extract_set_smallest_value_readout`,
  `test_set_enabled_respects_smallest_gate`, and any assertion on
  `_fill_factor` / `_fdr`. Do not leave them skipped.

**Verification:**
- The ALC form shows exactly three rows: Smallest Particle Diameter (+ unit),
  Gaussian σ, Min. Particle Area (+ unit), and nothing else.
- `tests/test_gui/test_adaptive_clip_settings_widget.py` passes with no reference
  to a removed attribute.

---

- U4. **Adapt the ALC panel to the trimmed config**

**Goal:** Remove the panel-side wiring for the deleted knobs without changing what
a default run does.

**Requirements:** R2, R3, R5, R12

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/gui/adaptive_clip_panel.py`
- Test: `tests/test_gui/test_adaptive_clip_panel.py`
- Test: `tests/test_gui/test_adaptive_clip_timelapse.py`

**Approach:**
- Drop the `largest_only`, `fill_factor`, and `fdr` parameters from
  `run_adaptive_auto_extract` and `run_adaptive_auto_extract_stack`, along with
  the `extract_largest_only` import and branch. The remaining body calls
  `auto_extract` and lets `FILL_FACTOR` / `FDR` come from the module defaults.
- Keep `smallest_particle_px` as a parameter that tolerates `None`, and keep the
  `except NoParticlesFoundError` degradation guard in the stack loop — the helpers
  stay reusable and the existing blank-frame test stays valid.
- In `_run_auto_extract_mode`, collapse the three-way smallest resolution to the
  manual path only: convert µm→px via the dataset pixel size (keeping the existing
  "µm needs a known pixel size" guard) or take px as-is, then the `> 0` guard.
- Remove `self._pending_ae_auto` (init, assignment, and the `set_smallest_value`
  back-fill in `_on_auto_extract_done`), and the `if config.largest_only:` status
  branch in the run path.
- Trim `_print_settings_debug` to the surviving fields and drop the
  `report.largest_only` branch from `_print_auto_extract_report`.
- Update the module docstring and `run_adaptive_auto_extract`'s docstring, which
  currently lead with the two-mode description.

**Patterns to follow:**
- The existing off-thread `Worker(...)` + `finished`/`error` wiring and the
  `set_enabled(False)` lock-during-run convention — unchanged, only the argument
  list shrinks.

**Baseline note — the time-lapse suite is RED on `main` today.** At `47cbfb6f`,
`tests/test_gui/test_adaptive_clip_timelapse.py` fails three tests with
`TypeError: fake_auto_extract() got an unexpected keyword argument 'fill_factor'`:
`..._loops_and_stacks`, `..._blank_frame_degrades`, and `..._largest_only`. Commit
47cbfb6f added the `fill_factor` / `fdr` kwargs to the `auto_extract` call without
updating the test doubles. Consequences the implementer must know:
- "Adjusting call signatures only" on the surviving two tests is wrong — it is the
  *production-side* kwarg removal in this unit that turns them green. Expect two
  failures to become passes, and do not read that as an accidental fix.
- A green suite is therefore **not** a valid no-behaviour-change signal for this
  unit. Record the three-failure baseline before starting.
- `dev-features` (U1) is cut at a commit with a red GUI suite. "The shipped-today
  state" includes that breakage; the branch is an archive, not a known-good build.

**Test scenarios:**
- Happy path: a run with the default config calls the worker with the manual
  smallest value in px and no `largest_only` / `fill_factor` / `fdr` kwargs.
- Happy path: with unit `µm` and a known `pixel_size_um`, the worker receives
  `value / pixel_size_um` as the px smallest.
- Error path: unit `µm` with missing or non-positive `pixel_size_um` sets the
  "needs a known pixel size" status and starts no worker.
- Error path: a smallest value that resolves to `<= 0` sets the "must be > 0"
  status and starts no worker.
- Integration: a `(T,H,W)` channel with `n_timepoints > 1` routes to
  `run_adaptive_auto_extract_stack` and saves a `(T,H,W)` mask; a 2D channel
  routes to `run_adaptive_auto_extract`.
- Integration: `_on_auto_extract_done` saves via `AcceptPunctaMask`, adds the
  layer, refreshes the CNR source list, and no longer writes back into the
  smallest-particle spinbox.
- **Test inventory to delete or update** (all verified present):
  from `tests/test_gui/test_adaptive_clip_panel.py`, delete the six largest-only
  tests — `test_largest_only_dispatches_to_extract_largest_only`,
  `test_default_off_path_still_calls_auto_extract`,
  `test_largest_only_needs_segmentation`, `test_largest_only_no_smallest_backfill`,
  `test_largest_only_prints_mode_to_terminal`,
  `test_run_adaptive_auto_extract_largest_only_flag` — plus
  `test_auto_extract_auto_backfills_smallest_readout` (asserts the
  `set_smallest_value` back-fill being removed); and update
  `test_run_prints_all_settings_to_terminal` to the trimmed debug lines.
  From `tests/test_gui/test_adaptive_clip_timelapse.py`, delete
  `test_run_adaptive_auto_extract_stack_largest_only`.
  Do not leave any of these skipped.

**Verification:**
- **Controlled comparison, not an equality assertion.** With the smallest particle
  set to the *same explicit value* on both sides, an ALC run before and after this
  unit produces an identical mask — this is what proves removing `fill_factor` /
  `fdr` was inert. Do **not** verify by comparing default-to-default: the default
  changes on purpose (auto-detect → fixed 2 px), so a difference there is the
  intended outcome, not a regression.
- Separately, run one real dataset on `dev-features` (auto-detect on, today's
  default) and on the trimmed `main`, and record how the mask differs. This is the
  number that belongs in the CHANGELOG entry — the user is the judge of whether the
  new default is acceptable, and they cannot judge it without seeing it.
- No symbol named `largest_only`, `fill_factor`, or `fdr` remains under
  `src/percell4/gui/`.

---

- U5. **Rework the CNR mode dropdown and fold the segmenter into it**

**Goal:** Three modes behind one green button, with the dev-only `discover` mode
and the secondary button gone.

**Requirements:** R7, R8, R9, R10

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/_cnr_classify_settings.py`
- Modify: `src/percell4/gui/adaptive_clip_panel.py`
- Test: `tests/test_gui/test_cnr_classify_settings_widget.py`
- Test: `tests/test_gui/test_adaptive_clip_panel.py`

`docs/audits/gui-element-classification.yaml` needs no change: it classifies only
`adaptive_clip_panel.run_button` from this panel — the classify and segment
buttons were never catalogued.

**Approach:**
- In `_cnr_classify_settings.py`, set `_MODE_LABELS` to
  `("CNR threshold", "Auto Two Groups", "Interactive")` and `_MODE_CODES` to map
  them to `"guided"`, `"forced"`, `"interactive"`. `discover` disappears from both.
  `_apply_mode_gating()` needs no logic change — it already enables the threshold
  spinbox only for `guided`, which is now index 0 and therefore live at startup.
- Rewrite the `Mode:` tooltip to describe the three surviving modes, and update the
  `CnrClassifyConfig` docstring's list of valid `mode` values.
- **Also rewrite the CNR-threshold spinbox tooltip.** It currently ends "A starting
  value is printed by a Discover run (the candidate threshold)" — pointing at a mode
  the user can no longer select. `report['candidate_cnr_threshold']` is still
  computed and printed by `_print_cnr_report`, so point at that, or at the
  `Interactive` histogram, as the way to find a starting value.
- **Give the green button a mode-aware tooltip.** Its label is fixed, but in
  `Interactive` mode the click opens a window and saves nothing, while the other two
  prompt for a name and write masks. The tooltip is the only in-panel signal of that
  difference — update it when the mode changes.
- In `adaptive_clip_panel.py`, delete `self._segment_btn` and its construction
  block.
- **Lock and unlock the same set of widgets in every mode.** `_on_classify` today
  disables four things (`_classify_btn`, `_run_btn`, `self._settings`,
  `self._cnr_settings`) and `_unlock_after_classify()` re-enables exactly those
  four. The interactive branch must use the **same four-widget lock** before
  starting the measure worker, and `_on_measure_done` / `_on_measure_error` must
  both call `_unlock_after_classify()`. Locking only two while unlocking four would
  let a measure worker finishing mid-ALC-run re-enable `Run Adaptive Clipping`,
  after which a second detection run reassigns `self._worker` and
  `self._pending_name` while the first `QThread` is still alive — a dropped result
  or a crash.
- Make `_on_classify` dispatch on `cfg.mode` right after `_resolve_cnr_inputs`
  succeeds: `"interactive"` hands off to the segmenter path (no resource-name
  prompt, no classify worker); everything else keeps today's prompt → worker →
  save flow. `_on_segment_cnr` should be refactored to accept the already-resolved
  inputs rather than re-running the pre-flight, so the shared validation happens
  exactly once per click.
- **Make the mode mapping total in both GUI worker bodies.** Replace
  `run_cnr_classification`'s and `run_cnr_classification_stack`'s trailing
  `else: # discover` with explicit `guided` / `forced` / `discover` branches and a
  `raise ValueError(f"unknown CNR mode {mode!r}")` fallthrough. `"interactive"` is a
  GUI-only routing value; if a dispatch slip ever let it reach these functions, the
  current fallthrough would run a discover classification and **save** the masks and
  `/classification/<base>` table under the user's chosen name with no error. Both
  functions live in `gui/`, so this respects the no-domain-changes boundary.

**Technical design:** *(directional — see High-Level Technical Design above for the flow diagram; not implementation specification)*

**Patterns to follow:**
- The existing `_resolve_cnr_inputs(allow_timelapse=True)` shared pre-flight — both
  branches must go through it, unchanged, so time-lapse handling stays identical.
- The `_unlock_after_classify()` helper — extend or mirror it for the interactive
  path rather than scattering `setEnabled` calls.

**Test scenarios:**
- Happy path: a fresh `CnrClassifySettingsWidget`'s `current_config()` returns
  `mode == "guided"` with `threshold == 8.0`, and the threshold spinbox is
  **enabled** on construction.
- Happy path: selecting `Auto Two Groups` yields `mode == "forced"` and disables
  the threshold spinbox; selecting `Interactive` yields `mode == "interactive"`
  and also disables it; returning to `CNR threshold` re-enables it.
- Edge case: `"Discover (auto gap)"` is not present in the combo's items, and
  `_MODE_CODES` has no `"discover"` entry.
- Edge case: `set_enabled(True)` after a lock restores the threshold spinbox only
  when the mode is `CNR threshold`.
- Integration: clicking `Classify Mask by CNR` in `CNR threshold` mode still
  prompts for a base name and starts the classification worker with
  `mode="guided"` and the spinbox threshold.
- Integration: clicking `Classify Mask by CNR` in `Interactive` mode starts the
  **measure** worker (not the classification worker), shows no name prompt, and on
  completion opens `CnrSegmenterWindow` with the measured records.
- Integration: in `Interactive` mode with a `(T,H,W)` channel, the pooled-stack
  worker (`run_cnr_measure_stack`) is chosen — the same selection the old button
  made.
- Error path: a failed pre-flight (no source mask selected, shape mismatch,
  unreadable mask) sets a status and starts no worker in **all three** modes.
- Error path: a measure-worker error re-enables the classify button and the CNR
  settings form rather than leaving the panel locked.
- Error path: passing `mode="interactive"` (or any unknown string) to
  `run_cnr_classification` / `run_cnr_classification_stack` raises `ValueError`
  rather than silently classifying and saving.
- Integration: while the measure worker is in flight all four widgets
  (`_classify_btn`, `_run_btn`, ALC settings, CNR settings) are disabled, and all
  four are re-enabled after it finishes **and** after it errors.
- **Test migration inventory** (all verified present): five tests in
  `tests/test_gui/test_adaptive_clip_panel.py` call `panel._on_segment_cnr()`
  directly — `test_segment_cnr_timelapse_pools_and_opens_window`,
  `test_segment_without_segmentation_aborts`,
  `test_segment_without_source_mask_aborts`,
  `test_segment_dispatches_measure_and_opens_window`,
  `test_segment_no_foci_shows_no_window`. Re-point them at `panel._on_classify()`
  with the mode set to `Interactive`; the two abort tests in particular must move,
  since the pre-flight now lives in `_on_classify`. Separately, there are **seven**
  `setCurrentText("Guided (CNR threshold)")` / `setCurrentText("Forced (always 2)")`
  call sites across `tests/test_gui/test_adaptive_clip_panel.py` and
  `tests/test_gui/test_cnr_classify_settings_widget.py` — update all of them, plus
  the widget test asserting the `"discover"` default.

**Verification:**
- The CNR group shows Source mask, Mode (three entries), CNR threshold, and one
  green button — no secondary button.
- Every mode routes through one click of `Classify Mask by CNR`.
- No reference to `_segment_btn` remains in the panel or its tests.

---

- U6. **Sync documentation to the trimmed UI**

**Goal:** Leave no active doc describing a control that no longer exists.

**Requirements:** R2, R3, R5, R7, R10 (documentation side)

**Dependencies:** U2, U3, U4, U5

**Files:**
- Modify: `src/percell4/gui/CLAUDE.md`
- Modify: `src/percell4/domain/measure/CLAUDE.md`
- Modify: `src/percell4/gui/metric_segmenter_panel.py` (module docstring lines 6
  and 14 — name the `Interactive` CNR mode, not the removed button)
- Modify: `src/percell4/gui/cnr_segmenter.py` (module docstring line 13 — drop
  `discover` from the `(discover/guided/forced)` list)
- Modify: `src/percell4/gui/adaptive_clip_panel.py` (`run_cnr_classification`
  docstring — it documents `"discover" → defaults` as a GUI mode)
- Modify: `CHANGELOG.md`

The three source edits above are **docstring-only** and do not violate the
"no changes to the CNR segmenter window itself" boundary in Scope Boundaries.

**Approach:**
- Rewrite the `adaptive_clip_panel.py` bullet in `src/percell4/gui/CLAUDE.md`: it
  currently enumerates the largest-only checkbox, the auto-detect toggle, the
  fill-factor spinbox, and the FDR spinbox as the panel's settings, and describes
  `Segment by CNR (interactive)` as a button. Replace with the four surviving
  controls and the three-mode dropdown.
- Rewrite the `cnr_segmenter.py` bullet's opening parenthetical (`the "Segment by
  CNR (interactive)" button`) to name the `Interactive` mode instead.
- Fix `src/percell4/domain/measure/CLAUDE.md:191`, which describes
  `NoParticlesFoundError` in terms of the removed GUI checkbox. The domain
  behaviour is unchanged — only the sentence that points at a GUI control needs
  rewording.
- Add an `Unreleased → Changed` (and/or `Removed`) CHANGELOG entry covering all
  three panels, and note that the pre-cleanup UI lives on `dev-features`.
- Give the behavioural changes their **own distinct CHANGELOG lines**, separate
  from the removal list, per the Risks table mitigations: (a) the Smallest
  Particle Diameter default moves from 3 px to 2 px, and (b) the default ALC run
  no longer LoG-measures the smallest particle.
- Per the repo's documentation rules, these files describe **current state only** —
  do not add "formerly known as" notes; the CHANGELOG carries the history.

**Test scenarios:**
- Test expectation: none — documentation only, no behavioural change.

**Verification:**
- `grep -rn "Largest particle only\|Auto-detect smallest\|Discover (auto gap)\|Segment by CNR (interactive)\|Coarse-k\|View bin (k)"` returns hits only under
  `docs/plans/` and `docs/brainstorms/` (historical artifacts, correctly untouched)
  and `CHANGELOG.md`.
- The broader mode grep `grep -rn 'discover/guided/forced\|"discover"' src/percell4/gui/`
  is also clean — the exact-label grep alone passes while stale `discover`
  references survive in module docstrings.

---

## System-Wide Impact

- **Interaction graph:** The green `Classify Mask by CNR` button gains a second
  downstream path (measure worker → `CnrSegmenterWindow`). Both paths already
  existed; only the entry point is shared. `_refresh_cnr_masks()` is still called
  on panel show, after an ALC save, and on segmenter destroy — unchanged.
- **Error propagation:** Three worker channels now share two buttons instead of
  three. The lock/unlock pairs must stay balanced: a measure-worker error has to
  re-enable the classify button and the CNR settings, or the panel deadlocks. This
  is the highest-value test in U5.
- **State lifecycle risks:** `AdaptiveClipConfig` is a frozen dataclass constructed
  only inside `current_config()`; removing fields cannot orphan persisted state
  because it is never serialised. Confirm no HDF5 attribute or run-log writes the
  removed keys before deleting them.
- **API surface parity:** The batch/workflow ALC path (`AutoExtractSettings` →
  `phases.py` → `auto_extract`) is a separate surface that never exposed these
  knobs. It stays as-is; the interactive panel and the batch dialog now differ in
  that the batch dialog still offers smallest-particle auto-detect
  (`smallest_particle_um = None`). That divergence is accepted and out of scope.
- **Integration coverage:** The mode→worker dispatch in `_on_classify` is the one
  place where a unit test on the settings widget alone would not prove correct
  behaviour — it needs a panel-level test asserting which worker starts.
- **Unchanged invariants:** No domain function signature, default, or return
  contract changes. **For a fixed smallest-particle value and a fixed mode**, ALC
  and `guided`/`forced` CNR produce byte-identical results before and after this
  plan — removing the `fill_factor` / `fdr` spinboxes genuinely changes nothing,
  because their defaults equal `FILL_FACTOR` and `FDR`.
- **Changed defaults (the three real behavioural changes):** (1) the *default* ALC
  run no longer LoG-measures the smallest particle — today `auto_extract_smallest_auto`
  is `True`, so `_run_auto_extract_mode` passes `smallest_px = None` and the fine
  window adapts per dataset; after R3 it is always `3 × the supplied value`;
  (2) the supplied value's default moves from 3 px to 2 px (R4), so the default
  fine window becomes a fixed 6 px; (3) the default CNR mode moves from `discover`
  to `guided` at 8.0 (R7). All three are intended, and all three must appear in the
  CHANGELOG as behavioural — not buried in the list of removed controls.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A default run silently changes because a removed spinbox default did not match the module constant | The spinbox defaults (`3.0`, `0.1`) are exactly `FILL_FACTOR` and `FDR` — verified. Removing those two is genuinely inert; prove it in U4 with a *fixed* smallest-particle value on both sides. |
| **The default ALC detector changes: auto-detect (LoG, per-dataset) → fixed 2 px fine window.** Anyone relying on the default gets different puncta. | Intended (R3 + R4), but it is the largest behavioural change in the plan and the one most easily mistaken for a regression. U4 requires a real before/after mask comparison, and the CHANGELOG gets a dedicated line. Do not verify by diffing default-against-default. |
| **The default CNR mode changes: `discover` (split only on a significant gap) → `guided` at 8.0 (always splits).** | Follows necessarily from R7. Surface it in the CHANGELOG next to the ALC default change so both land in front of the user at once. |
| Panel deadlock, or worse — a measure worker unlocking the ALC Run button mid-detection | U5 mandates a symmetric four-widget lock/unlock via `_unlock_after_classify()`, with an explicit test asserting all four states in both the done and error paths. |
| `"interactive"` reaching the classifier and silently saving a discover-mode result | U5 makes the GUI mode mapping total with a `ValueError` fallthrough, plus an error-path test. |
| The time-lapse GUI suite is already red on `main`, so "tests pass" is not a valid no-change signal for U4 | U4 records the three-failure baseline explicitly and names which failures this unit is expected to turn green. |
| `dev-features` drifts and becomes unrunnable | Accepted. Its purpose is archival reachability; the plan does not commit to maintaining it. |
| Removing `auto_extract_smallest_auto` breaks a caller outside the panel | Verified by grep: the field exists only in the settings widget and the panel. Re-run the grep before deleting. |

---

## Documentation / Operational Notes

- No migration, no persisted-state change, no user data touched.
- Per `project_ci_and_local_gui_tests` memory: the GUI tests changed here
  (`tests/test_gui/*`) run on **CI only** — the local venv segfaults on mixed Qt.
  Expect to validate the widget-level changes by pushing, and to validate the
  visual result by launching the app manually.
- The `dev-features` branch should be pushed to the remote in U1, not left local.

---

## Sources & References

- Related code: `src/percell4/gui/_adaptive_clip_settings.py`,
  `src/percell4/gui/adaptive_clip_panel.py`,
  `src/percell4/gui/_cnr_classify_settings.py`,
  `src/percell4/interfaces/gui/peer_views/session_window.py`
- Prior plans (historical, now partly superseded by this cleanup):
  `docs/plans/2026-07-15-001-feat-adaptive-clip-largest-only-plan.md`,
  `docs/plans/2026-06-23-003-feat-interactive-cnr-segmenter-plan.md`,
  `docs/plans/2026-06-23-001-feat-cnr-subpopulation-classification-plan.md`
