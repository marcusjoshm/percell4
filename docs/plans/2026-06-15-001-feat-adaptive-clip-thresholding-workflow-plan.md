---
title: "feat: Adaptive sigma clipping as a single-cell thresholding workflow method"
type: feat
status: active
date: 2026-06-15
---

# feat: Adaptive sigma clipping as a single-cell thresholding workflow method

## Overview

Make the eye-validated per-cell **Adaptive Local Clipping** detector
(`detect_adaptive_by_particle_size`) selectable as a thresholding method inside
the single-cell thresholding analysis workflow — both headlessly (a new
`ThresholdingRound` sentinel, runnable from `run_config.json` / the batch CLI)
and interactively through the workflow **config dialog's rounds table**.

The detector is driven by one physical knob — `d_min_um`, the smallest particle
diameter to detect — and a robust per-cell `1.4826·MAD` noise floor, so a fixed
`k` transfers across cells and datasets whose intensity scale varies many-fold.
It already ships and is wired into the standalone `AdaptiveClipPanel` (a
Creator); this work brings it into the batch workflow alongside grouped Otsu,
puncta two-pass, and iterative-Otsu.

---

## Problem Frame

The single-cell workflow (`src/percell4/gui/workflows/single_cell/`, pure core
`src/percell4/workflows/`) runs ordered **thresholding rounds** to turn each
Cellpose cell's intensity into a `/masks/<round>` resource that `measure` and
`export` consume. A round's method is carried by `ThresholdingRound`: when it
holds a `PunctaDetectorSettings` the two-pass spot detector runs; when it holds
an `IterativeOtsuSettings`, iterative Otsu peeling runs; otherwise legacy
per-group Otsu runs (`workflows/phases.py:_apply_threshold_frame`).

Adaptive sigma clipping — the method the user has eye-validated across four
datasets / two condensate types — has no path into this workflow. Today it is
reachable only from the standalone `AdaptiveClipPanel`, one dataset at a time.
The goal is parity with the other alternative methods **plus** GUI selection in
the config dialog (which puncta and iterative-Otsu never received).

Three facts shape the design:

1. The per-cell detector needs `pixel_size_um` (the window is physical, in µm).
   `_apply_threshold_frame` does not currently receive it; `apply_threshold_headless`
   has the `store` and must thread it in.
2. The interactive `ThresholdQCController` only previews **per-group Otsu**
   thresholds. A per-cell, non-grouped method cannot be QC'd by it, so adaptive
   rounds must route through the headless apply path even during an interactive
   run.
3. **The apply phase is gated on grouping the per-cell method does not use.**
   The compute phase (`threshold_compute_one` → `_group_image_labels`,
   `phases.py:449-489`) runs GMM/k-means on the round's channel/metric and returns
   `DatasetFailure.THRESHOLD_EMPTY` when it yields 0 groups (or the frame has no
   groupable cells); the runner/CLI then skip apply for that (dataset, round). A
   per-cell adaptive detector needs no grouping, so it must **not** inherit that
   gate — otherwise a clustering failure on an unused metric silently drops a
   dataset/frame the detector would have thresholded.

---

## Requirements Trace

- R1. A `ThresholdingRound` can carry an adaptive-clip method as a sentinel,
  mutually exclusive with `puncta` and `iterative_otsu`.
- R2. The headless apply phase runs `detect_adaptive_by_particle_size` per cell
  and writes `/masks/<round>` + `/groups/<round>` in the same shape every other
  method produces.
- R3. A round with adaptive clipping but no usable `pixel_size_um` on the dataset
  fails that dataset gracefully (a `FailureRecord`), never crashing the run — and
  the missing pixel size is surfaced **before** a long batch run starts.
- R4. The method and its parameters (`d_min_um`, `k`, the presmooth σ) round-trip
  through `run_config.json` (so a persisted run restores them) **and** are
  expressible on the `percell4-batch-threshold` CLI. *(The config dialog is always
  constructed empty — it has no config-load path today — so "reopen the dialog and
  see the saved method" is explicitly out of scope; see Scope Boundaries.)*
- R5. The workflow config dialog lets a user pick "Adaptive sigma clipping" per
  round and enter its parameters; building the config produces a round carrying
  `AdaptiveClipSettings`.
- R6. Adaptive rounds apply headlessly even when the run is interactive (the
  per-group QC controller is skipped for them), and the run surfaces a status /
  run-log line so the user knows the round applied without a QC pause.
- R7. An adaptive round produces a mask regardless of whether the round's
  channel/metric would cluster — the per-cell detector does not depend on
  intensity grouping succeeding.
- R8. `measure` / `export` consume adaptive-round masks with no change.

---

## Scope Boundaries

- **Reuse the shipped detector verbatim.** No change to
  `domain/measure/adaptive_clip.py`'s `detect_adaptive_by_particle_size` —
  it is eye-validated and already correct. This plan only *calls* it.
- **No new interactive per-cell QC UI.** Adaptive rounds skip the per-group
  `ThresholdQCController` and apply headlessly. A bespoke accept/reject overlay
  for per-cell results is explicitly out of scope (see Deferred to Follow-Up).
- **`d_min_um`, `k`, and the presmooth σ are the GUI-exposed knobs.** The
  presmooth σ maps to the detector's `presmooth_sigma_px` (the round's
  `gaussian_sigma`, default 1 px) — this is what makes the workflow reproduce a
  standalone-panel run, which threads its own σ spinbox into the same argument
  (see Key Technical Decisions).
- **No change to the standalone `AdaptiveClipPanel`** or its particle-mode path.
- **No config-load path is added to the dialog.** `WorkflowConfigDialog` is
  always constructed empty today (no `config_from_dict` → dialog loader exists).
  "Reopen the dialog and see a previously-saved adaptive round" is therefore out
  of scope; persistence is delivered via `run_config.json` + the CLI (R4).
- **Time-lapse** is supported via the existing per-frame apply loop (no new
  tracking semantics).

### Deferred to Follow-Up Work

- Interactive per-cell QC (overlay the resulting mask, adjust `d_min`/`k`, redo):
  a separate plan if the headless-apply + status-line approach proves
  insufficient in practice.
- A general `config_from_dict` → config-dialog loader (would let the dialog
  reopen any saved run, not just adaptive rounds) — out of scope here.
- Auto-suggesting `d_min_um` from the data (currently a user-entered physical
  value).

---

## Context & Research

### Relevant Code and Patterns

- **Sentinel + dispatch precedent (mirror this):**
  `ThresholdingRound` (`src/percell4/workflows/models.py:284`) carries the
  mutually-exclusive `puncta` / `iterative_otsu` fields;
  `_apply_threshold_frame` (`src/percell4/workflows/phases.py:666`) dispatches on
  them. `_apply_iterative_otsu_groups` (`src/percell4/workflows/phases.py:603`)
  is the closest structural template for the new per-cell apply helper.
- **Settings dataclass precedent:** `IterativeOtsuSettings`
  (`src/percell4/workflows/models.py:213`) — frozen, validates loudly in
  `__post_init__`.
- **Detector:** `detect_adaptive_by_particle_size`
  (`src/percell4/domain/measure/adaptive_clip.py:154`) — signature
  `(image, labels, pixel_size_um, d_min_um, *, k=1.0, presmooth_sigma_px=1.0)`,
  returns a whole-frame `{0,1}` uint8 mask; does its own per-cell MAD σ and
  presmoothing. Constant/degenerate cells are skipped internally.
- **Pixel size source:** `store.metadata.get("pixel_size_um")` (nullable;
  `AdaptiveClipPanel._run_particle_mode`, `src/percell4/gui/adaptive_clip_panel.py:287`
  errors when missing/≤0 — the workflow must instead record a dataset failure).
- **run_config (de)serialization:** `artifacts.py` explicitly reconstructs
  nested dataclasses: `_puncta_to_dict`/`_from_dict`,
  `_iterative_otsu_to_dict`/`_from_dict`, threaded through `_round_to_dict`
  (`src/percell4/workflows/artifacts.py:228`) / `_round_from_dict`
  (`:249`). New nested settings need the same explicit pair.
- **Runner round loop / QC gate:** `_phase_generator`
  (`src/percell4/gui/workflows/single_cell/runner.py:438-486`) chooses
  `_make_threshold_qc_handler` (interactive, per-group) vs
  `_make_threshold_apply_headless_handler` (`:975`) on the run-wide
  `self._interactive_qc` flag.
- **Config dialog rounds table:** fixed 7-column model
  (`_ROUND_COL_*`, `src/percell4/gui/workflows/single_cell/config_dialog.py:143-160`),
  built in `_build_rounds_group` (`:750`), read into `ThresholdingRound`s by
  `_rounds_from_table` (`:~1769`). **There is no config→dialog loader:**
  `WorkflowConfigDialog` is always constructed empty; `:1387` is `_read_round_row`
  (a per-row reader), not a `_populate_rounds_table` repopulator. This is why
  "reopen the dialog to see a saved round" is out of scope (R4 / Scope Boundaries).

### Institutional Learnings

- `docs/solutions/architecture-patterns/registered-analysis-framework.md` — one
  pure core shared by every consumer; pin numeric parity with a committed
  fixture before wiring. Applies: GUI, headless apply, and CLI must all call the
  *same* `detect_adaptive_by_particle_size`.
- `domain/measure/CLAUDE.md` — the registry / `*_NAMES` drift-guard idiom. Note:
  adaptive clip enters as a **settings dataclass sentinel** (like
  `IterativeOtsuSettings`), not a flat-dict registry entry, so **no new
  `*_NAMES` tuple is required** — only `__post_init__` validation.
- `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
  — in workflow runs, masks → `/masks/<round>`; measurements are owned by the
  run folder, never `/measurements`. The headless apply path already honors this.
- **Mask hygiene** (`docs/solutions/ui-bugs/...`): masks must be strictly
  `{0,1}` uint8 — the detector already guarantees this; preserve it through the
  union into `combined`.

### Stale doc to correct

- `src/percell4/domain/measure/CLAUDE.md` references a non-existent
  `detect_adaptive_in_group`; fix while documenting the new integration (U6).

---

## Key Technical Decisions

- **Add a third sentinel, don't overload puncta.** `adaptive_clip:
  AdaptiveClipSettings | None` on `ThresholdingRound`, mutually exclusive with
  `puncta`/`iterative_otsu`. The per-cell detector's contract (`labels` +
  `pixel_size_um`, per-cell σ) does not fit the puncta per-group path, and a
  distinct sentinel keeps the dispatch readable. (Matches the established
  precedent and the learnings recommendation.)
- **Thread `pixel_size_um` as an optional param into `_apply_threshold_frame`**
  (default `None`), supplied by `apply_threshold_headless` from `store.metadata`.
  Optional default keeps every existing caller working; the adaptive branch is
  the only consumer and validates it.
- **Adaptive branch operates on the raw `image` and passes the round's
  `gaussian_sigma` as the detector's `presmooth_sigma_px`.** The detector applies
  its own presmoothing, so feeding it the raw image + σ-as-presmooth reproduces
  the standalone panel **exactly** — the panel threads its `gaussian_sigma`
  spinbox into the very same argument (`adaptive_clip_panel.py`'s particle-mode
  worker call). Feeding the round's already-smoothed `smoothed` would
  double-smooth; fixing presmooth at 1.0 and calling σ "inert" (the prior draft)
  would silently diverge from any eye-validated panel run where σ ≠ 1. A
  committed **numeric parity fixture** (U2) pins workflow-output ==
  `detect_adaptive_by_particle_size` for matched params so they cannot drift.
- **Adaptive rounds get a trivial grouping in the compute phase, bypassing the
  cluster gate.** `threshold_compute_one` short-circuits for an adaptive round to
  a single-group `GroupingResult` (every cell → group 1) instead of running
  GMM/k-means. This populates the `_grouping_cache` so the apply phase runs
  (Problem Frame fact 3), avoids spending clustering cost the method ignores, and
  makes the degenerate `/groups` table fall out naturally — a dataset whose cells
  do not cluster is still thresholded by adaptive clipping (R7).
- **Degenerate `/groups` table.** The per-cell method does not use intensity
  grouping, so the apply branch writes a single degenerate group
  (`group_df[col_name] = 1`), consistent with iterative-Otsu's non-`groups`
  scopes and with the trivial grouping above.
- **Adaptive rounds always apply headlessly, but announce it.** In the runner,
  when `round_spec.adaptive_clip is not None`, use the headless apply handler
  regardless of `self._interactive_qc` — the bounded answer to the
  "interactive-QC story" the GUI scope forces (no new QC widget). Because every
  *other* round in an interactive run pauses for a QC dialog, the adaptive round
  must emit a status / run-log line (e.g. *"round X: adaptive sigma clipping —
  applied headlessly, no QC step"* with a success/failure indicator) so the user
  is not silently handed unreviewed masks before `measure`/`export` consume them.
- **Missing pixel size is surfaced pre-flight, not just post-run.** When an
  adaptive round is configured, the config dialog validates that each selected
  dataset carries `pixel_size_um > 0` and blocks/warns before the run starts
  (the knob is in µm, so a missing pixel size is a fatal misconfiguration that is
  detectable at launch). The per-dataset `DatasetFailure` (R3) remains as the
  runtime backstop.
- **GUI: Method combo + adaptive param columns, with defined inert-state
  behavior.** Extend the rounds table with a `Method` column (`Grouped Otsu` /
  `Adaptive sigma clipping`) plus `d_min (µm)` and `k` columns (the σ column is
  reused as the adaptive presmooth). Selecting Adaptive **greys** the
  now-irrelevant GMM-max / K-means-K inputs while **retaining their last value**
  (so switching back restores prior work, rather than clearing); selecting
  Grouped Otsu greys the `d_min`/`k` columns the same way. Mirrors how the
  Algorithm combo already gates GMM vs K-means relevance. On a method switch the
  build path (`_rounds_from_table`) must **actively null the non-selected sentinel
  fields** so a row reconfigured from puncta/iterative-otsu to adaptive does not
  trip the three-way mutual-exclusion `ValueError`.

---

## Open Questions

### Resolved During Planning

- *GUI exposure or headless-only?* → Both: headless parity **and** a config-dialog
  method picker (user decision, 2026-06-15).
- *Which detector variant?* → The per-cell one-knob
  `detect_adaptive_by_particle_size` (user decision, 2026-06-15).
- *How to QC a per-cell method interactively?* → Route adaptive rounds through
  headless apply; skip the per-group controller (Key Decisions).
- *New `*_NAMES` tuple needed?* → No; a settings-dataclass sentinel with
  `__post_init__` validation, like `IterativeOtsuSettings`.
- *Does the per-cell method inherit the grouping gate?* → It must not; adaptive
  rounds get a trivial single-group `GroupingResult` in the compute phase (U7).
- *Does run_config round-trip give CLI parity?* → No. `percell4-batch-threshold`
  builds its round from argparse (`--strategy {grouped-otsu, iterative-otsu}`),
  not from a loaded `run_config.json`, so CLI parity needs a new `--strategy
  adaptive-clip` (U8).
- *Is workflow output bit-parity with the panel?* → Yes, by construction (raw
  image + round σ as presmooth) and pinned by a numeric fixture (U2).

### Deferred to Implementation

- Exact column widths / combo wiring details in the rounds table — discover
  against the live `_build_rounds_group` layout.
- Pixel size is dataset-constant, so the time-lapse apply loop threads it once
  (confirmed against `apply_threshold_headless`'s time-lapse branch).

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Compute phase short-circuit (U7) — adaptive rounds skip the cluster gate:

```
threshold_compute_one(store, round_spec, …)
    └─ round_spec.adaptive_clip ?
         └─ yes → trivial GroupingResult (every cell → group 1)   # no GMM/k-means,
         └─ no  → _group_image_labels(...)  (may return THRESHOLD_EMPTY)  # gate unchanged
    → cached in _grouping_cache so apply always runs for adaptive rounds  (R7)
```

Dispatch seam in `_apply_threshold_frame` (precedence — adaptive first, then the
existing chain):

```
round_spec.adaptive_clip ?
    └─ yes → _apply_adaptive_clip_cells(RAW image, labels, settings, combined,
                                        pixel_size_um, round_name)
               └─ detect_adaptive_by_particle_size(image, labels, pixel_size_um,
                       d_min_um, k=settings.k,
                       presmooth_sigma_px=round_spec.gaussian_sigma)  →  {0,1} mask
               └─ union into `combined`;  /groups df = single degenerate group
    └─ no  → iterative_otsu ? → puncta ? → legacy per-group Otsu   (unchanged)
```

Data flow for the headless apply (the only path adaptive rounds take):

```
apply_threshold_headless(store, round_spec, grouping)
    ├─ pixel_size_um = store.metadata.get("pixel_size_um")
    ├─ adaptive round AND (pixel_size_um missing/≤0)
    │       → return DatasetFailure.THRESHOLD_ERROR("needs pixel size")   # R3 backstop
    └─ _apply_threshold_frame(image, labels, grouping, round_spec, pixel_size_um)
            → write /masks/<round> (uint8 {0,1}) + /groups/<round>
```

Runner QC gate (U4): `adaptive_clip is not None` forces the headless handler even
when `self._interactive_qc` is True, plus a status/run-log line. The compute phase
(U7) still runs for adaptive rounds — it produces the trivial grouping the headless
handler reads from the cache.

---

## Implementation Units

- U1. **AdaptiveClipSettings dataclass + ThresholdingRound sentinel**

**Goal:** Represent an adaptive-clip method as a frozen, validated settings
object on a round, mutually exclusive with the other two methods.

**Requirements:** R1, R4 (partial — the in-memory model)

**Dependencies:** None

**Files:**
- Modify: `src/percell4/workflows/models.py`
- Test: `tests/test_workflows/test_models.py`

**Approach:**
- Add `AdaptiveClipSettings` (frozen): `d_min_um: float`, `k: float = 1.0`.
  `__post_init__` validates `d_min_um > 0`, `k >= 0`. (The presmooth σ is **not**
  duplicated here — the apply branch sources it from the round's existing
  `gaussian_sigma` so the single GUI σ control drives both grouped and adaptive
  rounds and panel parity is automatic; see U2.)
- Add `adaptive_clip: AdaptiveClipSettings | None = None` to `ThresholdingRound`.
- Extend the mutual-exclusion check (`models.py:326`) so **at most one** of
  `puncta` / `iterative_otsu` / `adaptive_clip` is set; raise otherwise.

**Patterns to follow:** `IterativeOtsuSettings` (`models.py:213`) and the
existing two-way exclusion at `models.py:326-329`.

**Test scenarios:**
- Happy path: `AdaptiveClipSettings(d_min_um=0.40)` constructs with `k=1.0`,
  `presmooth_sigma_px=1.0` defaults.
- Edge case: `d_min_um=0` and negative `d_min_um` each raise `ValueError`.
- Edge case: negative `k` and negative `presmooth_sigma_px` raise `ValueError`.
- Happy path: `ThresholdingRound(..., adaptive_clip=AdaptiveClipSettings(d_min_um=0.4))`
  constructs and the other two sentinels are `None`.
- Error path: a round with both `adaptive_clip` and `puncta` (and the
  `adaptive_clip` + `iterative_otsu` pair) raises with the three-way message.

**Verification:** `test_models.py` passes; constructing a stale combination
fails loudly at construction, not at apply time.

---

- U2. **Per-cell adaptive apply branch + pixel-size threading**

**Goal:** Run the per-cell detector in the apply phase and persist a standard
mask, failing gracefully without a pixel size.

**Requirements:** R2, R3 (and produces the standard `/masks` + `/groups` shape R8
relies on)

**Dependencies:** U1 (U7 supplies the trivial grouping in interactive/full runs,
but the apply branch itself is independent of it)

**Files:**
- Modify: `src/percell4/workflows/phases.py`
- Test: `tests/test_workflows/test_phases.py`,
  `tests/test_workflows/test_phases_threshold_timelapse.py`

**Approach:**
- Add `_apply_adaptive_clip_cells(image, labels, settings, combined,
  pixel_size_um, presmooth_sigma_px, round_name)` mirroring
  `_apply_iterative_otsu_groups` (`phases.py:603`): call
  `detect_adaptive_by_particle_size(image, labels, pixel_size_um,
  settings.d_min_um, k=settings.k, presmooth_sigma_px=presmooth_sigma_px)`,
  `np.maximum` the `{0,1}` result into `combined`, log a one-line summary, return
  `""` or an error string.
- Add `pixel_size_um: float | None = None` param to `_apply_threshold_frame`
  (`phases.py:666`); add the adaptive branch as the highest-precedence sentinel.
  The branch passes the **raw `image`** (not `smoothed`) and
  `presmooth_sigma_px = round_spec.gaussian_sigma` — this reproduces a standalone
  panel run exactly (the panel threads its σ spinbox into the same detector
  argument), so the detector smooths once with the user's σ and no double-smooth
  occurs. Write a degenerate `/groups` df (`group_df[col_name] = 1`), matching
  iterative-Otsu's non-`groups` handling (`phases.py:733`).
- In `apply_threshold_headless` (`phases.py:741`): read
  `pixel_size_um = store.metadata.get("pixel_size_um")`; when the round carries
  `adaptive_clip` and the value is missing or `<= 0`, return
  `DatasetFailure.THRESHOLD_ERROR` with a clear message (mirror existing failure
  returns). Pass `pixel_size_um` into `_apply_threshold_frame` on both the
  single-frame and time-lapse branches (pixel size is dataset-constant — threaded
  once).

**Execution note:** Start with a failing apply test on a synthetic
labels+image before threading pixel size, so the dispatch wiring is proven by a
red test first.

**Patterns to follow:** `_apply_iterative_otsu_groups` and the dispatch block in
`_apply_threshold_frame` (`phases.py:685-728`); failure returns in
`apply_threshold_headless`.

**Test scenarios:**
- Happy path: a 2-cell synthetic frame with bright spots inside each cell and a
  known `pixel_size_um` produces a `{0,1}` uint8 mask with foreground only
  inside cells; `/groups` has one degenerate group with `col == 1`.
- Covers R3 / Error path: a round with `adaptive_clip` on a store whose metadata
  lacks `pixel_size_um` (and one where it is `0`) returns a
  `DatasetFailure.THRESHOLD_ERROR`, writes no mask, and does not raise.
- **Panel parity fixture (guards finding 3):** for a fixed synthetic image +
  labels + `(d_min_um, k, σ)`, the apply-branch mask is **bit-identical** to a
  direct `detect_adaptive_by_particle_size(image, labels, pixel_size_um,
  d_min_um, k=k, presmooth_sigma_px=σ)` call — including a σ ≠ 1 case, so a panel
  run with a raised σ cannot silently diverge from the workflow.
- Edge case: an empty-label frame (no cells) yields an all-zero mask, no error.
- Integration: time-lapse `(T,H,W)` channel produces a `(T,H,W)` mask and a
  `/groups` table with a `timepoint` column (mirror existing time-lapse test).
- Regression: a legacy Otsu round and a puncta round produce byte-identical
  masks to before (dispatch precedence unchanged).

**Verification:** New apply tests pass; existing `test_phases*` threshold tests
stay green (no regression to the other three methods).

---

- U3. **run_config.json round-trip for adaptive rounds**

**Goal:** Persist and restore the new sentinel in `run_config.json` so a saved
run (and the CLI's config writer) preserves the method. *(This does not feed the
config dialog — it has no load path; see Scope Boundaries.)*

**Requirements:** R4

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/workflows/artifacts.py`
- Test: `tests/test_workflows/test_artifacts.py`

**Approach:**
- Add `_adaptive_clip_to_dict` / `_adaptive_clip_from_dict` mirroring
  `_iterative_otsu_to_dict`/`_from_dict` (`artifacts.py:203-226`).
- Emit `out["adaptive_clip"] = ...` in `_round_to_dict` (`:228`) only when
  present (additive — legacy configs without the key reconstruct unchanged), and
  reconstruct in `_round_from_dict` (`:249`).

**Patterns to follow:** the puncta / iterative-otsu to_dict/from_dict pair and
their additive emission in `_round_to_dict`.

**Test scenarios:**
- Happy path: a `WorkflowConfig` with one adaptive round survives
  `config_to_dict` → `config_from_dict` with `d_min_um`, `k` (and the round's
  `gaussian_sigma`) intact.
- Edge case: a legacy config dict with no `adaptive_clip` key reconstructs a
  round with `adaptive_clip is None` (back-compat).
- Edge case: a config mixing one adaptive round and one legacy Otsu round
  round-trips both correctly.

**Verification:** `test_artifacts.py` passes; a hand-written `run_config.json`
with an adaptive round loads via `read_run_config` without error.

---

- U4. **Runner: route adaptive rounds through headless apply**

**Goal:** Adaptive rounds skip the per-group interactive QC controller (which
cannot preview a per-cell method) and apply headlessly, even in interactive runs.

**Requirements:** R6

**Dependencies:** U2, U7

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/runner.py`
- Test: `tests/test_gui_workflows/test_single_cell_runner.py`

**Approach:**
- In `_phase_generator`'s round loop (`runner.py:455-486`), when
  `round_spec.adaptive_clip is not None`, yield the
  `_make_threshold_apply_headless_handler` phase
  (`threshold_apply:<round>`) regardless of `self._interactive_qc`; otherwise
  keep the existing interactive-vs-headless branch unchanged. The UNATTENDED
  compute phase (Phase 3) still runs for adaptive rounds — it produces the trivial
  grouping (U7) the headless handler reads from `_grouping_cache`.
- Emit a status / run-log line for the adaptive apply phase so an interactive run
  (where every other round pauses for QC) tells the user the round applied
  without a QC step, e.g. `host.show_status("round X: adaptive sigma clipping —
  applied headlessly (no QC)")` plus a `RunLog` entry with the outcome.

**Patterns to follow:** the existing `if self._interactive_qc:` branch at
`runner.py:459-486`; existing `host.show_status` / `RunLog` calls in the runner.

**Test scenarios:**
- Happy path: with `interactive_qc=True` and a single adaptive round, the
  generated phase sequence contains `threshold_apply:<round>` and **not**
  `threshold_qc:<round>`.
- Happy path: with `interactive_qc=True` and a legacy Otsu round, the sequence
  still contains `threshold_qc:<round>` (no regression).
- Integration: a config with one adaptive round and one Otsu round yields
  `threshold_apply` for the first and `threshold_qc` for the second, and the
  compute phase runs for both.
- Happy path: the adaptive apply emits a status/run-log line naming the round and
  the "no QC" outcome (assert the message is recorded).

**Verification:** `test_single_cell_runner.py` passes; an interactive run with an
adaptive round does not open `ThresholdQCController` and records the headless-apply
status line.

---

- U5. **Config dialog: method picker, adaptive params, and pixel-size pre-flight**

**Goal:** Let a user choose "Adaptive sigma clipping" per round, enter
`d_min_um` / `k`, and be warned before launch if a selected dataset lacks a pixel
size. (Build only — the dialog has no config-load path, so there is no
"repopulate from a saved round" to implement here; see Scope Boundaries.)

**Requirements:** R5, R3 (pre-flight half)

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: new `tests/test_gui/test_single_cell_config_dialog_methods.py`

**Approach:**
- Add a `Method` combo column (`Grouped Otsu` / `Adaptive sigma clipping`) and
  `d_min (µm)` + `k` spin columns to the rounds table; bump `_ROUND_COL_*` and
  `_ROUND_COL_HEADERS` (`config_dialog.py:143-160`) and the width map (`:759`).
  The existing σ column is reused as the adaptive presmooth (no new column).
- **Inert-column behavior (defined, not left to the implementer):** selecting
  Adaptive **greys** the GMM-max / K-means-K inputs while **retaining their last
  values**; selecting Grouped Otsu greys the `d_min`/`k` columns the same way.
  Mirror how the Algorithm combo toggles GMM vs K-means relevance.
- In `_rounds_from_table` (`:~1769`), when method is Adaptive build
  `ThresholdingRound(..., adaptive_clip=AdaptiveClipSettings(d_min_um=…, k=…))`
  **and explicitly set `puncta=None`, `iterative_otsu=None`** so a row switched
  from another method does not trip the three-way exclusion `ValueError`.
- **`d_min` validation presentation:** surface an invalid `d_min` (e.g. 0) using
  the dialog's existing round-validation mechanism (the same
  `QMessageBox`/status-label path other bad round inputs use — locate it in the
  current `_try_build_config` flow and reuse it), not a new ad-hoc popup.
- **Pixel-size pre-flight:** when any round is Adaptive, on Run (or in
  `_try_build_config`) check each selected dataset's `store.metadata["pixel_size_um"]`
  and block/warn if missing or `<= 0`, naming the offending dataset(s), before the
  run starts.

**Patterns to follow:** existing per-row cell widgets, the Algorithm-combo gating
logic, and the existing round-validation / error-surfacing path in
`_try_build_config`.

**Test scenarios:**
- Happy path: add a row, set Method = Adaptive, set `d_min=0.40`, `k=1.0`; build
  config → the round has `adaptive_clip.d_min_um == 0.40`, `puncta is None`,
  `iterative_otsu is None`.
- Happy path: a Grouped Otsu row still builds a legacy round
  (`adaptive_clip is None`) — no regression.
- Edge case: switching a row's Method toggles which columns are greyed AND the
  greyed columns retain their prior values (assert both enabled-state and value).
- Edge case (guards the exclusion bug): a row first configured as
  iterative-otsu, then switched to Adaptive, builds with `iterative_otsu is None`
  (no raised `ValueError`).
- Error path: Method = Adaptive with `d_min = 0` is rejected at build time via the
  dialog's standard validation surface (assert the user-facing rejection, not a
  raw exception).
- Pre-flight: with an Adaptive round selected and a dataset whose metadata lacks
  `pixel_size_um`, the dialog blocks/​warns naming that dataset before any run
  starts.

**Verification:** Config-dialog tests pass; selecting the method in the running
app produces a round that the headless apply (U2) executes, and a pixel-size-less
dataset is caught before launch.

---

- U6. **Docs: module CLAUDE.md updates + stale-reference fix**

**Goal:** Keep the living module docs accurate.

**Requirements:** —

**Dependencies:** U1, U2, U5, U7, U8

**Files:**
- Modify: `src/percell4/workflows/CLAUDE.md` (describe the now three-way
  `ThresholdingRound` sentinel and the adaptive apply path)
- Modify: `src/percell4/domain/measure/CLAUDE.md` (note the workflow integration;
  fix the `detect_adaptive_in_group` reference)
- Modify: `src/percell4/gui/workflows/CLAUDE.md` if it enumerates round methods

**Approach:** Current-state only, no history (per the project's documentation
rules). One-to-three sentences per file. **First `grep` the codebase for
`detect_adaptive_in_group`** to confirm whether it was renamed (vs. never
existed) before editing the doc — correct the reference to the function that
actually exists (the cell-restricted detector used by `window_k_sweep`), do not
blindly delete it.

**Test scenarios:** Test expectation: none — documentation only.

**Verification:** The three CLAUDE.md files describe adaptive clipping as a
selectable round method and reference only functions that exist.

---

- U7. **Compute-phase trivial grouping for adaptive rounds**

**Goal:** Adaptive rounds populate the grouping cache without running (and without
being gated by) GMM/k-means clustering, so the apply phase always runs (R7).

**Requirements:** R7

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/workflows/phases.py` (`threshold_compute_one` /
  `_group_image_labels`, `:449-489`)
- Test: `tests/test_workflows/test_phases.py`

**Approach:**
- When `round_spec.adaptive_clip is not None`, `threshold_compute_one`
  short-circuits to a trivial `GroupingResult` placing every cell in group 1
  (built from the segmentation's label ids), instead of calling
  `_group_image_labels`. This skips the `THRESHOLD_EMPTY` gate that would
  otherwise drop a dataset/frame whose unused channel/metric fails to cluster.
- Mirror the same short-circuit in the time-lapse compute path (one trivial
  `GroupingResult` per frame that has cells).

**Patterns to follow:** the `GroupingResult` shape returned by
`_group_image_labels`; the time-lapse `dict[int, GroupingResult]` builder.

**Test scenarios:**
- Happy path: an adaptive round on a dataset returns a single-group
  `GroupingResult` (all cells → group 1), `failure is None`, without invoking
  GMM/k-means.
- Covers R7 / Error path that must NOT happen: a dataset whose channel/metric
  would yield 0 groups (the grouped-Otsu `THRESHOLD_EMPTY` case) still returns a
  valid trivial grouping for an adaptive round (assert no `THRESHOLD_EMPTY`).
- Regression: a grouped-Otsu / iterative-otsu / puncta round still runs the real
  `_group_image_labels` and still returns `THRESHOLD_EMPTY` on 0 groups.
- Integration: time-lapse adaptive round returns one trivial grouping per
  cell-bearing frame.

**Verification:** `test_phases.py` passes; an adaptive round on a non-clustering
dataset reaches the apply phase and produces a mask (paired with U2).

---

- U8. **Batch CLI: `--strategy adaptive-clip`**

**Goal:** Deliver the headless/CLI parity R4 promises — `percell4-batch-threshold`
can run an adaptive round (it builds rounds from argparse, not `run_config.json`).

**Requirements:** R4 (CLI half)

**Dependencies:** U1, U2, U7

**Files:**
- Modify: `src/percell4/interfaces/cli/batch_threshold.py`
- Test: `tests/test_cli/test_batch_threshold.py` (or the existing batch-threshold
  CLI test module)

**Approach:**
- Add `adaptive-clip` to the `--strategy` choices (`batch_threshold.py:123`) and
  an argument group with `--d-min-um` (required for that strategy) and `--k`
  (default 1.0), mirroring the existing `--strategy iterative-otsu` option group.
- When `--strategy adaptive-clip`, construct the `ThresholdingRound` with
  `adaptive_clip=AdaptiveClipSettings(d_min_um=…, k=…)` (and the existing
  `--gaussian-sigma` flows through as the presmooth, matching U2).

**Patterns to follow:** the `iterative-otsu` strategy wiring already in
`batch_threshold.py` (argparse group → `IterativeOtsuSettings` → `ThresholdingRound`).

**Test scenarios:**
- Happy path: `--strategy adaptive-clip --d-min-um 0.40 --k 1.0` builds a round
  with `adaptive_clip.d_min_um == 0.40` and runs `apply_threshold_headless`.
- Error path: `--strategy adaptive-clip` without `--d-min-um` exits with a clear
  argparse error.
- Error path: a dataset lacking `pixel_size_um` is reported as a per-dataset
  failure in the CLI summary, not a crash (R3 backstop on the headless path).

**Verification:** The CLI runs an adaptive round end-to-end on a fixture dataset
and writes `/masks/<round>`.

---

## System-Wide Impact

- **Interaction graph:** new dispatch branch in `_apply_threshold_frame`
  (highest precedence); runner round loop gains an adaptive→headless gate; config
  dialog rounds table gains columns. The detector itself is unchanged.
- **Error propagation:** a missing/invalid pixel size becomes a per-dataset
  `FailureRecord` (`DatasetFailure.THRESHOLD_ERROR`), never a run crash; the
  detector's internal per-cell skips (degenerate σ) need no surfacing.
- **State lifecycle risks:** masks are written `{0,1}` uint8 by the detector and
  unioned with `np.maximum` then clamped — preserve that so no 255-valued mask
  reaches the store. `/groups` stays honest via the degenerate single group.
- **API surface parity:** `_apply_threshold_frame` gains an optional
  `pixel_size_um=None` param. Its callers are `apply_threshold_headless`
  (single-frame + time-lapse) and `puncta_validation.py:166`; the optional default
  keeps the latter working untouched (only `apply_threshold_headless` passes the
  new arg). The interactive `ThresholdQCController` is intentionally left untouched
  (adaptive rounds never reach it).
- **Integration coverage:** unit tests alone won't prove the runner→headless
  routing or the dialog→config→apply→mask chain — U4 and U5 each carry a
  cross-layer integration scenario, and U2's parity fixture pins workflow output
  against the bare detector.
- **Unchanged invariants:** legacy Otsu, puncta, and iterative-Otsu rounds
  produce byte-identical masks and phase sequences (regression scenarios in U2,
  U4, U7 assert this). `measure` / `export` read `/masks/<round>` +
  `/groups/<round>` agnostically and need no change (R8) — the degenerate
  single-group `/groups` satisfies their schema.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Workflow silently diverges from the eye-validated panel when σ ≠ 1 | Branch passes the round's `gaussian_sigma` as the detector's `presmooth_sigma_px` on the raw image (exact panel parity); U2's bit-identical parity fixture (incl. a σ ≠ 1 case) guards it |
| Adaptive round dropped when its unused channel/metric fails to cluster | U7 short-circuits adaptive rounds to a trivial single-group grouping, bypassing the `THRESHOLD_EMPTY` gate (R7) |
| Datasets without `pixel_size_um` produce no mask | Pre-flight dialog check (U5) catches it before launch; per-dataset `DatasetFailure` (U2/R3) is the runtime backstop |
| Per-cell method can't be QC'd interactively, surprising users mid-run | Adaptive rounds apply headlessly (U4) **and emit a status/run-log line** so the user knows; documented in CLAUDE.md (U6); interactive overlay deferred |
| Row switched from puncta/iterative-otsu to adaptive trips the three-way exclusion | U5 build path explicitly nulls the non-selected sentinels; a dedicated U5 test covers the switch |
| `_apply_threshold_frame` signature change ripples to callers | Optional `pixel_size_um=None` default; callers are `apply_threshold_headless` (×2) and `puncta_validation.py:166`, all tolerant of the default |
| CLI parity assumed but not delivered | `percell4-batch-threshold` builds rounds from argparse, not `run_config.json`; U8 adds the `--strategy adaptive-clip` flags so headless parity is real |

---

## Sources & References

- Related code: `src/percell4/workflows/models.py`,
  `src/percell4/workflows/phases.py` (`_apply_threshold_frame`,
  `_apply_iterative_otsu_groups`, `threshold_compute_one`, `_group_image_labels`),
  `src/percell4/workflows/artifacts.py`,
  `src/percell4/gui/workflows/single_cell/runner.py`,
  `src/percell4/gui/workflows/single_cell/config_dialog.py`,
  `src/percell4/interfaces/cli/batch_threshold.py`,
  `src/percell4/domain/measure/adaptive_clip.py`
  (`detect_adaptive_by_particle_size`),
  `src/percell4/gui/adaptive_clip_panel.py` (panel σ→presmooth parity reference)
- Prior-method precedents:
  `docs/plans/2026-06-03-002-feat-headless-puncta-thresholding-plan.md`,
  `docs/plans/2026-06-08-002-feat-iterative-otsu-thresholding-plan.md`
- Learnings: `docs/solutions/architecture-patterns/registered-analysis-framework.md`,
  `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
