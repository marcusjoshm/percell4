---
title: "feat: Batch compute phasor + wavelet across .h5 datasets (CLI)"
type: feat
status: active
date: 2026-05-18
---

# feat: Batch compute phasor + wavelet across .h5 datasets (CLI)

## Overview

Add a CLI entry point that runs Compute Phasor and Apply Wavelet across a
list of `.h5` files. For each dataset, the batch processes every
available decay channel under `/decay/*`, computes `/phasor/<ch>/g` and
`/phasor/<ch>/s` (with calibration applied), then applies the DTCWT
wavelet filter to produce `/phasor/<ch>/g_filtered`, `s_filtered`, and
`lifetime_filtered`. Per-channel skip on existing `/phasor/<ch>` and on
missing calibration. Always runs at native resolution (k=1).

No GUI dialog in this iteration — invocation is from a terminal via
`python -m percell4.interfaces.cli.batch_phasor`, matching the existing
`run_pipeline.py` CLI pattern.

---

## Problem Frame

A typical experimental session produces N `.h5` files (one per dish /
condition / replicate). Today the user has to open each one in the GUI,
click Compute Phasor, click Apply Wavelet, and repeat — slow and
error-prone for batches of 8–20 datasets. The batch CLI lets the user
queue a directory of files and walk away.

This is the third batch flow in the codebase, joining batch compress
(`workflows/phases.compress_one`) and batch TCSPC append
(`application/use_cases/batch_add_decay.py`). It reuses the
single-dataset `ComputePhasor` and `ApplyWavelet` use cases unchanged,
adding only an orchestration layer + CLI surface.

---

## Requirements Trace

- R1. For each input `.h5`, iterate every channel under `/decay/*` and
  process each one independently — partial-success per dataset is
  expected.
- R2. Compute Phasor first (writes `g`, `s`), then Apply Wavelet
  (writes `g_filtered`, `s_filtered`, `lifetime_filtered`). Skip
  Lifetime as a separate operation in this iteration.
- R3. Skip a channel with a clear report line when `/phasor/<ch>/g`
  already exists. Optional `--overwrite` flag forces recompute.
- R4. Skip a channel with a clear report line when calibration is
  missing (any of `flim_cal_phase_<ch>`, `flim_cal_mod_<ch>`, or
  `flim_frequency_mhz`).
- R5. Apply a single `filter_level` (1..9) to every dataset and
  channel; CLI arg `--filter-level` with the same default the FLIM
  panel uses.
- R6. Always operate at `view_bin=1` (native). No session/view-bin
  surface — batch runs do not have an interactive Session.
- R7. Produce a structured report at the end: per-dataset status
  (succeeded / partial / skipped / failed), per-channel reasons,
  and total counts.
- R8. Never crash on a per-dataset or per-channel failure — collect
  errors and continue.
- R9. Programmatic API alongside the CLI (`batch_compute_phasor(...)`
  returns the report) so future GUI work can call it directly.

---

## Scope Boundaries

- No GUI dialog. Invocation is CLI-only for this iteration. A future
  `BatchPhasorDialog` mirroring `BatchTCSPCDialog` can be added in a
  follow-up if usage demand justifies it.
- No `compute_lifetime` step. The user wants Phasor + Wavelet only;
  `lifetime` and `lifetime_filtered` written by Apply Wavelet are
  enough for the typical workflow.
- No `view_bin` parameter. Batch runs at native; the session-level
  view bin (U9/U11 from the dataset-wide-binning feature) is a GUI
  concern and irrelevant here.
- No per-dataset channel selection. The batch processes every channel
  with a `/decay/<ch>` entry. Users who want to skip a channel can
  delete its `/decay/<ch>` first or write a thin wrapper.
- No re-import of TCSPC source files. The batch assumes `/decay/<ch>`
  is already on disk; if a file lacks decay, every channel is reported
  as skipped.
- No parallelism across datasets. Sequential processing keeps the
  report deterministic and avoids HDF5 concurrent-access concerns. A
  future iteration could add a worker pool if throughput becomes a
  bottleneck.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/application/use_cases/batch_add_decay.py` — the canonical
  in-codebase batch-orchestrator pattern. Per-item iteration,
  per-channel failure collection, structured `BatchAppendReport`,
  progress callback shape. The new use case mirrors this exactly.
- `src/percell4/application/use_cases/compute_phasor.py` —
  `ComputePhasor.execute(channel, harmonic, view_bin)` is the
  single-dataset operation reused per channel. Already handles
  calibration via `_read_fresh_metadata`, writes g/s, deletes stale
  derived layers, raises `NoDatasetError` on no-dataset, returns
  `PhasorResult`.
- `src/percell4/application/use_cases/apply_wavelet.py` —
  `ApplyWavelet.execute(channel, filter_level, view_bin)` is the
  wavelet step. Reads g, s, decay; runs `denoise_phasor`; writes
  `g_filtered`, `s_filtered`, `lifetime_filtered`.
- `src/percell4/interfaces/cli/run_pipeline.py` — the established CLI
  pattern. `argparse`-driven; programmatic `run_pipeline(...)` function
  callable from tests; per-step error handling; final result struct.
  Mirror this shape for `batch_phasor.py`.
- `src/percell4/adapters/hdf5_store.py` — `Hdf5DatasetRepository`
  caches store instances by path. The batch can reuse one repo
  instance across datasets.
- `src/percell4/application/session.py` — `Session.set_dataset(handle)`
  is how the use cases pick up the active dataset. Per-iteration:
  construct a fresh `Session`, call `set_dataset`, run the use cases.
  No active_*/filter_*/measurement state needed; the use cases only
  read `session.dataset`.
- `src/percell4/store.py` — `DatasetStore.metadata` gives the
  calibration check inputs (`flim_cal_phase_<ch>`, `flim_cal_mod_<ch>`,
  `flim_frequency_mhz`). `store.list_groups("decay")` enumerates the
  channels under `/decay/*`.

### Institutional Learnings

- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  — Vector 2 (`read_metadata` fresh read) and Vector 3 (in-place
  metadata mutation after writes) are about in-session GUI usage where
  the handle's metadata snapshot can drift. The batch runs in a
  short-lived per-dataset session, so the staleness vectors don't
  apply — but ComputePhasor's `_read_fresh_metadata` path still does
  the right thing on every call.
- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`
  — The new caller-wiring learning. The batch caller must pass
  `view_bin=1` explicitly to `ComputePhasor.execute` and
  `ApplyWavelet.execute` to be unambiguous (even though 1 is the
  default). Cheap to do and inoculates against a future default
  change.
- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`
  — The intensity used by the wavelet filter must come from
  `/decay/<ch>.sum(axis=-1)`, not from `/intensity[ch_idx]`. Already
  enforced inside `ApplyWavelet.execute`; the batch caller doesn't
  need to do anything extra.

### External References

None. Internal CLI + use-case orchestration; no new external
dependency.

---

## Key Technical Decisions

- **Reuse `ComputePhasor` and `ApplyWavelet` unchanged.** No new domain
  logic; the batch is purely an orchestrator that mirrors what a user
  would do clicking through the FLIM panel for each file.
- **Sequential, single-process.** No worker pool, no QThread, no
  multiprocessing. HDF5 concurrent-write semantics are tricky, and at
  expected batch sizes (10–50 datasets) sequential time is acceptable
  (~minutes). Parallel can land later as a follow-up if needed.
- **Per-iteration Session.** Construct a fresh `Session` per dataset,
  call `set_dataset(handle)`, run the two use cases, discard. Mirrors
  what the GUI does on dataset switch. Avoids any cross-dataset state
  leakage.
- **Pre-flight skip checks.** Before calling `ComputePhasor.execute`
  for a channel, the orchestrator (a) checks `/phasor/<ch>/g` exists
  → skip unless `overwrite=True`, (b) reads calibration attrs → skip
  if any are missing. This keeps the use case itself clean; the batch
  layer owns the "should we run?" decision.
- **Report shape mirrors `BatchAppendReport`.** Top-level `items: list`,
  per-item `status: str` ("succeeded" / "partial" / "skipped_no_changes"
  / "failed"), per-item `processed: tuple[str, ...]`, `skipped: dict[str, str]`
  (channel → reason), `errors: dict[str, str]` (channel → exception).
  Cancelled-from-N field omitted (no cancellation in CLI; Ctrl-C raises).
- **`view_bin=1` passed explicitly.** Even though it's the default, pass
  it at every call site so the orchestrator's posture is grep-visible.
  Per the U14 caller-wiring learning.

---

## Open Questions

### Resolved During Planning

- **Channel enumeration source:** `store.list_groups("decay")`.
  Returns the H5 group children under `/decay/`, which is exactly the
  set of channels with TCSPC data.
- **Default filter level:** Match the FLIM panel's
  `self._wavelet_level.value()` default (9). Hardcode `9` in the CLI;
  document it in `--help`.
- **Report verbosity:** Per-dataset summary line + per-channel skip
  reasons on stdout. Full structured `BatchPhasorReport` returned from
  the programmatic API. No `--verbose` flag in this iteration; the
  output is already compact.
- **Calibration check policy:** Required keys are
  `flim_cal_phase_<ch>`, `flim_cal_mod_<ch>`, `flim_frequency_mhz`. If
  ANY is missing → skip with a single message ("ch00 skipped: missing
  calibration (flim_frequency_mhz)").

### Deferred to Implementation

- **Whether `wavelet` failure inside an iteration should cascade or be
  per-channel.** If `apply_wavelet` raises but `compute_phasor`
  succeeded, the channel ends up with valid `/phasor/<ch>/{g,s}` but
  no `g_filtered`. Decide at implementation time based on what
  `denoise_phasor` actually raises: treat as a per-channel error
  (most likely) vs. a per-dataset fatal.
- **Whether to emit a final exit code based on dataset failures.**
  Probably `0` if any dataset succeeded, `1` if all failed.
- **Logging level via CLI flag.** `run_pipeline.py` doesn't expose one;
  decide whether to inherit that posture or add `--verbose` /
  `--quiet`.

---

## Implementation Units

- U1. **`batch_compute_phasor` use case**

**Goal:** Orchestrate ComputePhasor + ApplyWavelet across N datasets ×
M channels, collecting per-channel results into a structured report.

**Requirements:** R1, R2, R3, R4, R5, R6, R7, R8, R9

**Dependencies:** None — reuses existing `ComputePhasor` and
`ApplyWavelet` unchanged.

**Files:**
- Create: `src/percell4/application/use_cases/batch_compute_phasor.py`
- Test: `tests/test_application/test_batch_compute_phasor.py`

**Approach:**
- Public function: `batch_compute_phasor(h5_paths: list[Path], *,
  filter_level: int = 9, overwrite: bool = False, progress_callback=None)
  -> BatchPhasorReport`.
- `BatchPhasorReport` dataclass: `items: list[BatchPhasorItemResult]`,
  `total_succeeded: int`, `total_failed: int`, `total_skipped: int`.
- `BatchPhasorItemResult` dataclass: `h5_path: Path`, `status: str`
  ("succeeded" | "partial" | "skipped_no_changes" | "failed"),
  `processed: tuple[str, ...]`, `skipped: dict[str, str]`,
  `errors: dict[str, str]`, `error: str | None` (dataset-level).
- Per-dataset loop:
  1. Build a fresh `Session`; instantiate
     `Hdf5DatasetRepository` once outside the loop; call
     `repo.open(h5_path)` to get the handle.
  2. `session.set_dataset(handle)`.
  3. Enumerate channels via `store.list_groups("decay")`.
  4. Per channel: pre-flight skip checks (existing
     `/phasor/<ch>/g`; calibration triple present). Skip with reason
     in `skipped[<ch>]`.
  5. Per remaining channel: `ComputePhasor(repo, session).execute(
     channel, harmonic=1, view_bin=1)` then
     `ApplyWavelet(repo, session).execute(channel, filter_level,
     view_bin=1)`. Catch exceptions per channel; record in
     `errors[<ch>]`; continue.
  6. Classify dataset status from `processed`/`skipped`/`errors`.
- `progress_callback(item: BatchPhasorItemResult)` invoked once per
  dataset after classification, before the next dataset starts.
- Top-level dataset-open exceptions go to `error` on a `failed`
  item; subsequent datasets continue.

**Patterns to follow:**
- `src/percell4/application/use_cases/batch_add_decay.py` — same
  item/result/report shape, same per-item classification logic, same
  cancellation/progress hooks (skip cancellation here).

**Test scenarios:**
- Happy path: two datasets each with two channels, no existing phasor,
  full calibration → status `"succeeded"` for both, `processed` lists
  every channel, `skipped` and `errors` empty.
- Skip existing: a dataset where `/phasor/ch0/g` already exists and
  `overwrite=False` → status `"skipped_no_changes"` for that channel
  (or `"partial"` if other channels processed); `skipped["ch0"]`
  contains the reason "phasor exists".
- Overwrite flag: same setup with `overwrite=True` → channel
  processed, `processed` includes it.
- Skip missing calibration: a dataset where `flim_cal_phase_ch0` is
  absent → status reflects per-channel skip with reason mentioning
  the missing key.
- Skip missing frequency: a dataset where `flim_frequency_mhz` is
  absent → every channel skipped with reason mentioning frequency.
- Per-channel error: monkeypatch `ApplyWavelet.execute` to raise on
  ch1 only → item status `"partial"`, `processed` contains ch0,
  `errors["ch1"]` contains the exception message; the run continues.
- Dataset-level error: nonexistent `.h5` path → item status
  `"failed"`, `error` populated with the open exception; the run
  continues to subsequent datasets.
- No decay channels: dataset with empty `/decay` group →
  `"skipped_no_changes"`, with a single top-level skip reason ("no
  decay channels").
- Progress callback: capture order; assert called exactly once per
  dataset, with the final per-dataset result.
- view_bin=1 passed explicitly: monkeypatch `ComputePhasor.execute`
  and `ApplyWavelet.execute` to capture kwargs; assert `view_bin=1`
  appears in every call.

**Verification:**
- `tests/test_application/test_batch_compute_phasor.py` passes.
- A manual smoke test on a real two-dataset two-channel scratch
  directory produces `/phasor/<ch>/{g,s,g_filtered,s_filtered,
  lifetime_filtered}` in every channel and a clean summary to stdout
  when wired up via U2.

---

- U2. **CLI entry point `batch_phasor.py`**

**Goal:** Wire `batch_compute_phasor` to an argparse-driven CLI that
the user can invoke from a terminal.

**Requirements:** R5, R7, R9

**Dependencies:** U1.

**Files:**
- Create: `src/percell4/interfaces/cli/batch_phasor.py`
- Test: `tests/test_cli_batch_phasor.py`

**Approach:**
- `main(argv: list[str] | None = None) -> int` entry point.
- Args:
  - Positional: one or more `.h5` paths (or directory globbed for
    `*.h5`).
  - `--filter-level INT` (default 9, range 1..9 enforced via
    `type=int` + post-parse check).
  - `--overwrite` flag.
  - `--quiet` flag (suppresses per-channel skip lines; final summary
    always prints).
- Resolve argv into a `list[Path]`. If a directory was passed, glob
  `*.h5` non-recursively. Refuse to run if no files resolve.
- Call `batch_compute_phasor(paths, filter_level=..., overwrite=...,
  progress_callback=_print_item_status)`.
- `_print_item_status(item)` prints one line per dataset:
  `[succeeded] dish_3.h5 — 4 processed, 0 skipped`
  with a multi-line indent under it for any skip reasons or errors.
- At the end, print a totals line:
  `Totals: 2 succeeded, 1 partial, 0 failed, 0 skipped`.
- Exit code: `0` if any dataset succeeded or any channel was
  processed; `1` if every dataset failed or was skipped (deferred
  decision in Open Questions — pick the conservative
  "0 on any progress" default and document it).

**Patterns to follow:**
- `src/percell4/interfaces/cli/run_pipeline.py` — `argparse` setup,
  programmatic `run_pipeline(...)` callable from tests via
  `main(argv=...)`, dataclass return shape, exit code convention.

**Test scenarios:**
- Happy path: invoke via `main(["dish_1.h5", "dish_2.h5", "--filter-level", "5"])`
  against a tmp_path tree with the expected metadata → exit 0,
  stdout contains both dataset summary lines, every channel landed
  in the .h5.
- Directory glob: invoke with a directory containing two `.h5` files
  → both processed.
- No matches: invoke with a nonexistent path / empty directory →
  exit 1, error message to stderr.
- Filter-level out of range: invoke with `--filter-level 0` → exit
  1, argparse error.
- `--overwrite` flag: re-invoke after a successful run with
  `--overwrite` → channels re-processed, no `skipped` entries.
- `--quiet` flag: stdout contains only the final totals line and
  per-dataset header lines, no per-channel skip reasons.

**Verification:**
- `tests/test_cli_batch_phasor.py` passes.
- Manual run: `python -m percell4.interfaces.cli.batch_phasor
  /path/to/dish_*.h5 --filter-level 7` works end-to-end on a real
  scratch tree.

---

- U3. **Documentation + `--help` polish**

**Goal:** A user who has never seen the feature can run `python -m
percell4.interfaces.cli.batch_phasor --help` and understand what it
does, what the flags mean, and what files it expects. No marketing
prose; just argparse `description` and `help` strings.

**Requirements:** R7 (indirectly: helps users interpret the report).

**Dependencies:** U2.

**Files:**
- Modify: `src/percell4/interfaces/cli/batch_phasor.py` (the
  argparse setup from U2 — adding the `description` and `--help`
  strings is part of writing U2 well; this unit is a final polish
  pass).
- Modify: `src/percell4/interfaces/cli/CLAUDE.md` (if it exists;
  otherwise skip).

**Approach:**
- `ArgumentParser(description=...)` with a multi-line description
  matching `run_pipeline.py`'s style: 1-line summary, 1 sentence on
  what gets written, and a usage example.
- Per-flag `help=` strings that name the default and the constraint
  (e.g., "Wavelet filter level (1..9, default 9)").

**Patterns to follow:**
- `run_pipeline.py` module docstring + argparse setup. Mirror the
  shape so future CLI work has a consistent surface.

**Test scenarios:**
- Test expectation: none — pure docstring/argparse polish, no
  behavioral change. Verified by manual `--help` invocation.

**Verification:**
- `python -m percell4.interfaces.cli.batch_phasor --help` shows a
  description, all flags, and at least one usage example.

---

## System-Wide Impact

- **Interaction graph:** None at runtime — the CLI is a separate
  process, no Qt event loop, no Session bridge, no napari. The
  programmatic API is callable from future GUI code if a batch
  dialog ever lands.
- **Error propagation:** Per-channel errors collect into the report;
  per-dataset errors collect into `BatchPhasorItemResult.error`. The
  CLI exits with `0` on any progress, `1` on total failure. No
  exceptions escape `main`.
- **State lifecycle risks:** None — each dataset gets a fresh
  `Session` that's discarded after the use cases run. No
  cross-dataset cache.
- **API surface parity:** The programmatic `batch_compute_phasor`
  function mirrors `batch_add_decay`'s call shape so future code
  that abstracts over batch flows can treat them uniformly.
- **Integration coverage:** A single end-to-end CLI test that walks
  the full chain (two .h5 files on disk → invoke main() → assert
  on-disk phasor + wavelet outputs) is the right integration
  surface. Unit tests cover the orchestration logic with mocked
  `ComputePhasor` / `ApplyWavelet`.
- **Unchanged invariants:** `ComputePhasor.execute` and
  `ApplyWavelet.execute` signatures and behavior are unchanged.
  This plan adds an orchestrator that consumes them; it does not
  modify their internals.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `denoise_phasor` (DTCWT) has a known numpy-2 / `asfarray` compat issue (surfaced in U14 testing). | Catch the exception per channel in U1; record in `errors[<ch>]`. Document the dep issue in the plan so the implementer doesn't chase it as a regression. |
| Long batch runs (50+ files × 4+ channels) take many minutes; user has no progress visibility beyond per-dataset lines. | The progress callback fires once per dataset. If a single dataset is slow internally, that's a `ComputePhasor` / `ApplyWavelet` problem out of scope. Document the per-dataset granularity in `--help`. |
| HDF5 file locking if the user is also editing the same `.h5` in the GUI. | Document in `--help` that the CLI assumes exclusive access; recommend closing the GUI dataset before running. h5py raises a clear error on locked files; the per-dataset error handler surfaces it as a `failed` item. |
| Per-channel error from `apply_wavelet` after `compute_phasor` succeeded leaves the channel in a half-written state (g/s present, g_filtered absent). | Document this as a known partial-state outcome. The user can re-run with `--overwrite` to retry. A future iteration could roll back the phasor write on wavelet failure, but the cost (transaction wrapper around two unrelated use cases) isn't justified by the rarity of this case. |

---

## Documentation / Operational Notes

- No README changes — the feature is self-documenting via `--help`.
- No CLAUDE.md update needed — the CLI is one file in an existing
  category (`interfaces/cli/`).
- No release notes / changelog convention in this repo.

---

## Sources & References

- Pattern source: `src/percell4/application/use_cases/batch_add_decay.py`
- CLI pattern source: `src/percell4/interfaces/cli/run_pipeline.py`
- Single-dataset operations:
  `src/percell4/application/use_cases/compute_phasor.py`,
  `src/percell4/application/use_cases/apply_wavelet.py`
- Related learning:
  `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`
  (caller-wiring discipline this plan honors by passing `view_bin=1`
  explicitly at every call)
