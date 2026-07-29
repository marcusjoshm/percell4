---
title: Batch rename / delete CLIs for channels, masks, and segmentations
type: feat
status: active
date: 2026-05-26
---

# Batch rename / delete CLIs for channels, masks, and segmentations

## Overview

Two new CLI entry points that apply a single rename or delete
operation across a batch of ``.h5`` datasets. Both operate on the
three resource kinds the GUI's Layer Management surface already
covers:

- **`percell4-batch-rename`** — renames a single `(kind, old_name)`
  pair to `new_name` in every ``.h5`` in the input paths.
- **`percell4-batch-delete`** — deletes a single `(kind, name)`
  in every ``.h5`` in the input paths.

`kind` is one of `channel`, `mask`, or `segmentation`. The
underlying domain operations already exist on
`percell4.store.DatasetStore` — these CLIs are thin orchestration
wrappers that iterate file paths, isolate per-dataset failures, and
emit the same `BatchPhasorReport`-shaped status output the existing
`percell4-batch-phasor` CLI uses.

A small refactor extracts the channel-delete logic (currently inlined
in `backend/main.py`'s `/delete` endpoint) into a
`DatasetStore.delete_channel` method so both the CLI and the FastAPI
sidecar call one canonical implementation.

---

## Problem Frame

Layer cleanup across many datasets is a routine maintenance task:

- A researcher batch-renames channels after a calibration convention
  changes (e.g. `ch00` → `mScar`) across a folder of dishes.
- A researcher batch-deletes a stale segmentation (e.g.
  `cellpose_qc_v1`) before re-running with new parameters.
- A researcher drops an unused mask (e.g. a failed thresholding
  attempt) from a project's worth of `.h5` files.

Today this is per-dataset via the GUI's Data hub Layer Management
(Rename / Delete buttons added in the seg-QC recovery work, backed
by the FastAPI `/rename` and `/delete` endpoints). Opening N
datasets one-by-one in the GUI to apply the same rename is exactly
the kind of toil the CLI surface already addresses for other
operations (`percell4-batch-phasor` for FLIM compute, `percell4-
batch-export` for image export, `percell4-batch` for the full
single-cell workflow).

The domain operations are already wired:

- `DatasetStore.delete_item(hdf5_path)` deletes any HDF5 group or
  dataset by path.
- `DatasetStore.rename_item(old_path, new_path)` moves any HDF5
  path; raises if the target already exists.
- `DatasetStore.rename_channel(old, new)` is the canonical
  per-channel rename — moves `/decay/<old>` and `/phasor/<old>`,
  updates the `channel_names` metadata list, and renames per-channel
  FLIM calibration attrs.
- Channel **delete** is the missing canonical method: today it's
  inlined in `backend/main.py:delete_resource`. The CLI needs the
  same logic, so it deserves to be promoted to a method on
  `DatasetStore`.

---

## Requirements Trace

- R1. A new `percell4-batch-rename` CLI renames one `(kind, old_name) → new_name` triple in every `.h5` file passed in the positional paths. Directory args are expanded non-recursively via `*.h5`. Same path-resolution conventions as `percell4-batch-phasor`.
- R2. A new `percell4-batch-delete` CLI deletes one `(kind, name)` pair in every `.h5` in the input paths. Same path semantics as R1.
- R3. Per-dataset failures isolate. A missing target name on one `.h5` is reported as skipped, not as a fatal failure for the batch. A genuinely-broken file (cannot open, IO error) is reported as failed and the batch continues.
- R4. Channel kind goes through the canonical per-channel logic: rename uses `DatasetStore.rename_channel` (moves decay + phasor + metadata together); delete uses a new `DatasetStore.delete_channel` (removes `/decay/<name>`, `/phasor/<name>`, prunes `channel_names`, drops per-channel FLIM calibration attrs).
- R5. Mask / segmentation kinds go through the generic `DatasetStore.rename_item` and `DatasetStore.delete_item` against `/masks/<name>` and `/labels/<name>` paths respectively.
- R6. Both CLIs emit per-dataset progress lines and a final totals summary in the same shape as `percell4-batch-phasor` (`[status] file.h5 -- N processed, N skipped, N errors`). Exit code: 0 if any progress was made; 1 otherwise.
- R7. Both CLIs accept a `--dry-run` flag that lists what would happen without writing to disk. Default is off (apply changes).
- R8. The existing FastAPI `/delete` endpoint's inline channel-delete logic is replaced with a call to the new `DatasetStore.delete_channel` method. The endpoint's externally-observable behavior is unchanged; this is a refactor for canonicalization.

---

## Scope Boundaries

- **Single rename pair per invocation.** No mapping-file (CSV/JSON) mode for renaming many resources at once — the user can run the CLI multiple times or shell-loop for that. Resolved in the planning question.
- **One resource kind per invocation.** No mixed-kind batches (e.g., rename one channel + one mask in the same run).
- **Single-resource per invocation for delete.** No `--names a,b,c` comma-list — the user can shell-loop. Keeps the contract simple and rollback-friendly (one resource per CLI run = one logical change to audit).
- **No `--overwrite` for rename.** If the target name already exists on a dataset, that dataset is reported as a per-dataset error (not silently overwritten). The user must delete the conflict first if they really want it gone.
- **No GUI changes.** The Layer Management Rename/Delete buttons already cover the per-dataset case. The CLIs are strictly the batch surface.
- **No transactional rollback across the batch.** Each dataset is mutated independently. If dataset 5 of 10 fails after datasets 1–4 succeeded, the first four stay renamed/deleted. The status report makes this clear; the `--dry-run` flag is the audit mechanism for "would this work cleanly".
- **No structured (JSON) output mode.** Plain-text progress lines + totals only. Machine-parseable output is deferred until a real downstream consumer asks for it.

### Deferred to Follow-Up Work

- **Mapping-file mode for rename** (CSV/JSON of old→new pairs in one invocation): deferred. Will surface as a separate `--map FILE` flag if multi-rename workflows become common.
- **Multi-kind batch operations** (e.g. delete a mask AND a segmentation in one run): deferred. Run the CLIs twice.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/store.py:DatasetStore.delete_item` (around line 580) — generic HDF5 group/dataset delete. Used for masks and segmentations.
- `src/percell4/store.py:DatasetStore.rename_item` (around line 588) — generic HDF5 move; raises on target-exists.
- `src/percell4/store.py:DatasetStore.rename_channel` (around line 700) — canonical per-channel rename. Already correct; reuse as-is.
- `backend/main.py:delete_resource` — current `/delete` endpoint. Its `kind == "channel"` branch (~lines 1015–1050 today) is what U1 extracts into `DatasetStore.delete_channel`.
- `src/percell4/application/use_cases/batch_compute_phasor.py` — orchestrator pattern: iterate paths, isolate per-dataset failures, return a `BatchPhasorReport` whose items are `BatchPhasorItemResult(h5_path, status, processed, skipped, errors, error)`. The new use cases mirror this shape (different dataclass names but identical structure) so the CLI report-printing helpers in `batch_phasor.py` apply directly with minor tweaks.
- `src/percell4/interfaces/cli/batch_phasor.py` — CLI adapter pattern: positional `paths` (files or dirs glob `*.h5`), `--verbose`, `--quiet`, progress callback wired to per-dataset print, final totals, exit-code-0-on-any-progress. The two new CLIs are near-identical shells with different verbs and a different orchestrator import.
- `src/percell4/application/use_cases/batch_compute_phasor.py` ITEM_STATUSES tuple — `("succeeded", "partial", "skipped_no_changes", "failed")` — reused for both new use cases.
- `tests/test_application/test_batch_compute_phasor.py` and `tests/test_cli_batch_phasor.py` — test patterns for the use case + CLI layers. New tests mirror those structures (`_make_h5` fixture, monkeypatched dependencies, progress-callback capture).

### Institutional Learnings

- `docs/solutions/architecture-patterns/atomic-write-contract.md` — `delete_item` / `rename_item` already write atomically via h5py's transactional mode. No new write-safety story needed.
- `docs/solutions/architecture-patterns/decay-write-path.md` — channel renames must move `/decay/<name>` AND `/phasor/<name>` AND update `channel_names`. `DatasetStore.rename_channel` already does this; the new `delete_channel` method mirrors the same surfaces for the symmetric op.

### External References

None needed. `argparse`, `h5py`, and the existing patterns cover everything.

---

## Key Technical Decisions

- **Two separate CLIs, not one with subcommands.** Matches the existing convention (`percell4-batch-phasor`, `percell4-batch-export`, `percell4-batch`). Each CLI does one verb; argparse stays flat and the entry-point names are self-documenting.
- **Channel delete extracted to `DatasetStore.delete_channel(name) -> bool`.** Returns `True` if anything was deleted, `False` if the channel was already absent (mirrors `delete_item`'s contract). Both the CLI orchestrator and the FastAPI `/delete` endpoint call it. The backend's inline h5py logic is replaced with one method call.
- **Rename's target-exists policy: error per dataset, batch continues.** A `ValueError` raised by `rename_item` becomes a per-dataset entry in `errors`, not a fatal exit. Symmetric with how `_phasor_exists` skips compute when the target is occupied — both are "this dataset is in an unexpected state; record it and move on".
- **Delete's no-op policy: skip, not error.** When the target name doesn't exist on a particular `.h5`, that's expected for a batch operation across heterogeneous datasets (not every file had the resource). Report as skipped, same shape as `batch_compute_phasor` skips "phasor exists, no overwrite".
- **`--dry-run` prints the same status lines but does no writes.** Implementation: the orchestrator takes a `dry_run: bool = False` kwarg; when True, the per-channel/mask/seg call site checks existence and classifies into `processed` / `skipped` / `errors` without invoking the mutation method. CLI default is off.
- **Status taxonomy reused.** `succeeded`/`partial`/`skipped_no_changes`/`failed` from `ITEM_STATUSES`. No new statuses — keeps the report shape consistent across batch CLIs.
- **Reuse of `_format_item_line` / `_print_item_status` from `batch_phasor.py` is OUT.** Importing helpers from sibling CLI files creates a fan-out that makes the CLI tree harder to read. Instead, a tiny shared helper module (`src/percell4/interfaces/cli/_batch_report.py`) holds the print + format helpers used by both new CLIs. Existing `batch_phasor.py` is NOT refactored to use it in this plan (out of scope; can land later).

---

## Open Questions

### Resolved During Planning

- **Single pair vs mapping file for rename:** single pair per invocation. (Confirmed with user.)
- **Channel delete location:** new `DatasetStore.delete_channel` method, called from both the CLI and the FastAPI endpoint. (See Key Technical Decisions.)
- **Target-exists during rename:** per-dataset error, batch continues. (See Key Technical Decisions.)

### Deferred to Implementation

- **Exact dataclass naming.** `BatchRenameItemResult` / `BatchDeleteItemResult` versus reusing `BatchPhasorItemResult`. Reuse is tempting (same fields) but the name leaks phasor semantics into rename/delete. Decide at implementation time; if the existing dataclass is renamed to something generic (`BatchOperationItemResult`), all three use cases can share it.
- **Whether to log to a `RunLog` or just stdout.** The existing batch CLIs don't write `RunLog` files because they're stateless one-shots. Match that until a real audit need surfaces.
- **Concurrency.** Both CLIs are single-threaded over the path list. Parallelization would mean N concurrent h5py file handles on the same disk — likely IO-bound. Deferred.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```text
                            user
                             |
        +--------------------+--------------------+
        |                                         |
  percell4-batch-rename               percell4-batch-delete
        |                                         |
        v                                         v
  batch_rename_resource()                 batch_delete_resource()
  (application use case)                  (application use case)
        |                                         |
        v                                         v
  per-h5 loop                            per-h5 loop
   - DatasetStore.rename_channel          - DatasetStore.delete_channel  (NEW, U1)
   - DatasetStore.rename_item             - DatasetStore.delete_item
        |                                         |
        v                                         v
  BatchOperationItemResult            BatchOperationItemResult
  (status / processed / skipped /     (status / processed / skipped /
   errors)                             errors)
        |                                         |
        +----+ shared print helper +---+
             |                         |
        _batch_report.format_item_line()
        _batch_report.print_item_status()
             |
             v
         stdout
```

The shape is identical to `batch_compute_phasor` → `percell4-batch-phasor`. The only novel piece is U1's `delete_channel` method; everything else is composition of existing parts.

---

## Implementation Units

- U1. **Extract `DatasetStore.delete_channel(name)` from inline backend logic**

**Goal:** Promote the channel-delete logic currently inlined inside `backend/main.py:delete_resource` into a canonical method on `DatasetStore`. Replace the inline implementation with a call to the new method. No externally-observable behavior change.

**Requirements:** R4, R8

**Dependencies:** None

**Files:**
- Modify: `src/percell4/store.py`
- Modify: `backend/main.py`
- Test: `tests/test_io/test_store_delete_channel.py`

**Approach:**
- Add `DatasetStore.delete_channel(name: str) -> bool` next to the existing `delete_item`, `rename_channel`, and `rename_item` methods. Returns `True` if anything was deleted on disk (any of: `/decay/<name>`, `/phasor/<name>`, presence in `channel_names`, FLIM calibration attrs). Returns `False` if nothing matched (consistent with `delete_item`).
- Implementation moves the existing h5py block from `backend/main.py:delete_resource` (`kind == "channel"` branch) verbatim, opens the file in `'a'` mode once, performs all four deletions, returns the accumulated `deleted_any` flag.
- Update `backend/main.py:delete_resource` to call `store.delete_channel(name)` instead of opening h5py directly. The HTTP-level 404 stays at the endpoint layer (raise `HTTPException(404, ...)` when the method returns `False`).

**Execution note:** Test-first — write the failing test for the new method first, port the logic, then update the backend caller.

**Patterns to follow:**
- `DatasetStore.rename_channel` for the channel-aware method shape (handles decay + phasor + metadata together).
- `DatasetStore.delete_item` for the `bool`-return contract on "did anything happen".

**Test scenarios:**
- Happy path — `delete_channel removes /decay/<name>`: dataset with `/decay/ch0` only, call `delete_channel("ch0")`, assert returns `True` and `/decay/ch0` is gone.
- Happy path — `delete_channel removes /phasor/<name>` too: dataset with `/decay/ch0` and `/phasor/ch0/g`, call delete, assert both gone, returns `True`.
- Happy path — `delete_channel prunes channel_names metadata`: dataset with `channel_names = ['ch0', 'ch1']`, delete `ch0`, assert metadata is `['ch1']`.
- Happy path — `delete_channel drops FLIM calibration attrs`: dataset with `flim_cal_phase_ch0` + `flim_cal_mod_ch0`, delete `ch0`, assert both attrs are gone.
- Edge case — `delete_channel returns False when nothing matches`: dataset has no `ch0` anywhere, call `delete_channel("ch0")`, assert returns `False` and no other state mutated.
- Edge case — `delete_channel preserves unrelated channels`: dataset with `ch0` and `ch1`, delete `ch0`, assert `/decay/ch1`, `/phasor/ch1/*`, and `channel_names = ['ch1']` are untouched.
- Integration — `backend /delete endpoint still works after the refactor`: a single existing backend test (or a new one if absent) for the channel-delete path passes against the post-refactor code.

**Verification:**
- The HTTP endpoint's externally-observable behavior is identical pre- and post-refactor. The new method covers all four side effects (decay, phasor, channel_names, calibration attrs) in one call.

---

- U2. **`batch_rename_resource` use case + `percell4-batch-rename` CLI**

**Goal:** A new application use case that renames `(kind, old_name) → new_name` across many `.h5` paths, plus a CLI adapter exposing it as `percell4-batch-rename`.

**Requirements:** R1, R3, R4, R5, R6, R7

**Dependencies:** None (U1 only affects delete; this unit uses the existing `rename_channel` and `rename_item` methods)

**Files:**
- Create: `src/percell4/application/use_cases/batch_rename_resource.py`
- Create: `src/percell4/interfaces/cli/batch_rename_resource.py`
- Create: `src/percell4/interfaces/cli/_batch_report.py`
- Modify: `pyproject.toml` (add `[project.scripts]` entry)
- Test: `tests/test_application/test_batch_rename_resource.py`
- Test: `tests/test_cli_batch_rename_resource.py`

**Approach:**
- The use case `batch_rename_resource(h5_paths, *, kind, old_name, new_name, dry_run=False, progress_callback=None) -> BatchOperationReport` iterates `h5_paths` and calls `_rename_one_dataset` per file. Each per-dataset call opens the store, calls the appropriate domain method (`rename_channel` for `kind="channel"`, `rename_item("labels/<old>", "labels/<new>")` for `segmentation`, `rename_item("masks/<old>", "masks/<new>")` for `mask`), and classifies the outcome.
- Per-dataset classification:
  - **processed**: the rename succeeded on this file.
  - **skipped**: the `old_name` didn't exist on this file (for channels: not in `channel_names` AND no `/decay/<old>`; for masks/segs: the source path didn't exist). Skip is silent and not a failure.
  - **errors**: an unexpected error (target name exists, IO error from h5py, etc.). The dataset is recorded as errored but the batch continues.
- `dry_run=True` short-circuits the mutation call but still performs the existence check, so the report classifies channels into processed/skipped/errors based on what *would* happen.
- The CLI accepts `paths...` (positional, files or directories), `--kind {channel,mask,segmentation}` (required), `--from-name NAME` (required), `--to-name NAME` (required), `--dry-run`, `--quiet`, `--verbose`. Argparse validates that `--kind` is one of the three legal values; bare paths are resolved via the shared `_resolve_paths` helper (extracted from `batch_phasor.py` into the new `_batch_report.py` module).
- The shared `_batch_report.py` module holds `_resolve_paths`, `_format_item_line` (verb-parameterized so "1 renamed" vs "1 deleted" vs "1 processed" all work), and `_print_item_status`. `batch_phasor.py` is NOT refactored in this plan to use it (deferred).
- New entry in `pyproject.toml`: `percell4-batch-rename = "percell4.interfaces.cli.batch_rename_resource:main"`.

**Execution note:** Test-first — the orchestrator's classification logic is exactly what tests need to pin down before implementation.

**Patterns to follow:**
- `src/percell4/application/use_cases/batch_compute_phasor.py` for the per-dataset isolation pattern, `BatchPhasorItemResult` shape, and `_classify_status` helper. Generic-rename the dataclass to `BatchOperationItemResult` if doing so cleanly (otherwise create a parallel one — implementation-time decision).
- `src/percell4/interfaces/cli/batch_phasor.py` for the argparse shape, `_resolve_paths` semantics, and progress-callback wiring.

**Test scenarios:**
- Happy path — `rename a channel across two files`: two `.h5` each with `/decay/mScar`, run `batch_rename_resource(paths, kind="channel", old_name="mScar", new_name="mScarlet")`, assert both files now have `/decay/mScarlet` and neither has `/decay/mScar`; `channel_names` is updated; report status is `succeeded` for each.
- Happy path — `rename a mask across two files`: two `.h5` each with `/masks/thresh_old`, rename to `thresh_new`, assert `/masks/thresh_new` present and `/masks/thresh_old` absent in both.
- Happy path — `rename a segmentation across two files`: same shape, against `/labels/<name>`.
- Happy path — `progress_callback fires once per dataset in input order`: two paths, callback records the order, assert it matches the input.
- Edge case — `mixed: present on file A, absent on file B`: A renames cleanly (processed); B has no source path (skipped). Final report has one `succeeded` and one `skipped_no_changes` item; totals say partial=0, succeeded=1, skipped=1.
- Edge case — `target name already exists on one file`: A has both `old` and `new`, so rename raises `ValueError` (from `rename_item`); A is recorded as `errors[<channel>] = "target exists: ..."`, item status is `failed`. B (no collision) succeeds. Batch continues.
- Edge case — `dry-run with mixed state`: same setup as the previous edge case, but with `dry_run=True`. No file is mutated. Report classifies the same as the live run would (processed / skipped / errors).
- Error path — `missing or unreadable file`: one path is to a non-existent file; that item is reported as `failed` with `error="open failed: ..."`; the rest of the batch continues.
- Edge case — `same old_name as new_name`: `rename_channel` already returns early in that case; assert the batch records it as skipped (no-op), not as an error.
- Integration (CLI layer) — `percell4-batch-rename --kind channel --from-name mScar --to-name mScarlet dish_*.h5` end-to-end via `cli.main(argv)`: monkeypatch the use case to a stub that captures args, assert the CLI passed through `kind="channel"`, `old_name="mScar"`, `new_name="mScarlet"`, and the resolved list of paths.
- Integration (CLI) — `--dry-run` flag: CLI propagates `dry_run=True` into the use case; stub captures the kwarg.
- Integration (CLI) — `missing --kind / --from-name / --to-name`: argparse exits non-zero before any work.
- Integration (CLI) — `--kind bogus`: argparse rejects.
- Integration (CLI) — `--help` mentions the three kinds and the `--dry-run` flag.
- Exit code — `any dataset processed -> exit 0`; `all skipped or all failed -> exit 1` (same convention as `percell4-batch-phasor`).

**Verification:**
- A directory of mixed `.h5` files where the target resource exists in some but not others yields a clean partial report; the present files are renamed correctly, the absent files are silently skipped, and the user can re-run with `--dry-run` to audit before applying.

---

- U3. **`batch_delete_resource` use case + `percell4-batch-delete` CLI**

**Goal:** Symmetric to U2 for deletion. Removes `(kind, name)` in every `.h5` in the input paths. Uses U1's `DatasetStore.delete_channel` for the channel case; `DatasetStore.delete_item("labels/<name>")` and `delete_item("masks/<name>")` for the other two.

**Requirements:** R2, R3, R4, R5, R6, R7

**Dependencies:** U1 (for `DatasetStore.delete_channel`)

**Files:**
- Create: `src/percell4/application/use_cases/batch_delete_resource.py`
- Create: `src/percell4/interfaces/cli/batch_delete_resource.py`
- Modify: `pyproject.toml` (add `[project.scripts]` entry)
- Modify: `src/percell4/interfaces/cli/_batch_report.py` (created in U2 — no change beyond reuse)
- Test: `tests/test_application/test_batch_delete_resource.py`
- Test: `tests/test_cli_batch_delete_resource.py`

**Approach:**
- The use case `batch_delete_resource(h5_paths, *, kind, name, dry_run=False, progress_callback=None) -> BatchOperationReport` iterates `h5_paths` and calls `_delete_one_dataset` per file. Each per-dataset call opens the store and:
  - `kind == "channel"` → `store.delete_channel(name)`; `True` is processed, `False` is skipped.
  - `kind == "mask"` → `store.delete_item(f"masks/{name}")`; same return contract.
  - `kind == "segmentation"` → `store.delete_item(f"labels/{name}")`; same.
- `dry_run=True` short-circuits the mutation but still performs the existence check (`store.list_groups("labels")`, `store.list_groups("masks")`, or peek at `/decay/<name>` for channels) and classifies accordingly.
- CLI shape mirrors `percell4-batch-rename`: `paths...`, `--kind {channel,mask,segmentation}`, `--name NAME`, `--dry-run`, `--quiet`, `--verbose`. No `--from-name`/`--to-name` — single resource name.
- New entry in `pyproject.toml`: `percell4-batch-delete = "percell4.interfaces.cli.batch_delete_resource:main"`.

**Execution note:** Test-first — same rationale as U2.

**Patterns to follow:**
- U2's use case + CLI for the orchestration shape. The delta from U2 is: one fewer argument (no `new_name`), one more domain method (`delete_channel`).
- `src/percell4/interfaces/cli/batch_phasor.py:--remove` path (the inverse-of-compute mode added recently) for the verb-parameterized output and "0 progress → exit 1" semantic.

**Test scenarios:**
- Happy path — `delete a channel across two files`: both files have `/decay/ch0`, run the use case with `kind="channel"`, `name="ch0"`, assert both files' `/decay/ch0`, `/phasor/ch0/*` are gone; `channel_names` no longer lists `ch0`.
- Happy path — `delete a mask across two files`: both have `/masks/thresh_488`, assert both gone after the run.
- Happy path — `delete a segmentation across two files`: both have `/labels/cellpose_qc`, assert both gone.
- Edge case — `name absent on one file, present on the other`: A is processed (deleted); B is skipped (no match). Partial status.
- Edge case — `dry-run`: nothing on disk changes; report classifies the same as a live run would.
- Error path — `missing or unreadable file`: item is `failed` with `error="open failed: ..."`; batch continues.
- Edge case — `channel delete leaves other channels intact`: dataset with `ch0` and `ch1`; delete `ch0`; assert `/decay/ch1`, `/phasor/ch1/*`, and `channel_names = ['ch1']` are unchanged.
- Integration (CLI) — `percell4-batch-delete --kind segmentation --name cellpose_qc dish_*.h5` end-to-end via `cli.main(argv)`: monkeypatch the use case, assert the CLI passed through the args.
- Integration (CLI) — `--dry-run` propagates through to the use case.
- Integration (CLI) — missing required args / bogus `--kind` → argparse error.
- Integration (CLI) — `--help` lists the three kinds and the `--dry-run` flag.
- Exit code — same convention: any progress → 0; otherwise 1.

**Verification:**
- A directory of mixed `.h5` files where the target name exists in some but not others yields a clean partial report; the present files are stripped correctly, the absent files are silently skipped, and `--dry-run` previews the same shape without writing.

---

## System-Wide Impact

- **Interaction graph:** The new CLIs are leaf-level entry points. They depend only on the application use cases, which depend only on `DatasetStore` methods. No GUI, viewer, runner, or peer-view code is touched by U2 / U3.
- **Backend parity:** U1's refactor moves channel-delete logic out of `backend/main.py` into `DatasetStore.delete_channel`. The FastAPI `/delete` endpoint's externally-observable behavior is unchanged; the channel-delete branch becomes a one-liner method call. Any other consumer that previously inlined this logic would also benefit — none exist today.
- **Error propagation:** Per-dataset errors travel as `errors` entries in the per-item report. Dataset-level fatal errors (open failed, decay enumeration failed for channel kind) travel as `error: str`. Both shapes match `batch_compute_phasor`'s report; no new error taxonomy.
- **State lifecycle risks:** None new. Each per-file mutation is a single `h5py.File(..., 'a')` open / mutate / close cycle. There is no cross-file transaction — the user accepts that a batch that fails midway leaves some files mutated and others not. The status report and `--dry-run` are the mitigations.
- **API surface parity:** Two new console entry points in `pyproject.toml`. Both follow the existing naming convention. No GUI changes, no FastAPI endpoint additions.
- **Integration coverage:** U1's test scenarios cover the method's per-file invariants; U2 / U3's integration tests at the CLI layer cover argparse-to-use-case-to-domain plumbing end-to-end.
- **Unchanged invariants:**
  - `DatasetStore.delete_item` / `rename_item` / `rename_channel` are not modified — only consumed.
  - `BatchPhasorItemResult` / `BatchPhasorReport` shapes are not modified; the new use cases produce structurally-identical reports (potentially under different dataclass names, decided at implementation time).
  - On-disk HDF5 contract (`/decay/<ch>`, `/phasor/<ch>/{g,s,...}`, `/labels/<name>`, `/masks/<name>`, `metadata.channel_names`) is unchanged. The CLIs operate on existing paths via existing methods.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| User runs `percell4-batch-delete --kind channel --name DAPI dish_*.h5` against the wrong directory and loses real data | The `--dry-run` flag plus the per-file progress lines make a destructive run visible before it happens. Document the affordance prominently in the `--help` epilog and the README CLI section. |
| Partial-batch failure leaves the project in an inconsistent state (5 of 10 datasets renamed, 5 not) | The status report calls out which datasets succeeded; the user can re-run the CLI to retry only the failed/skipped subset (idempotent for delete; rename already-renamed datasets become skips). Document this in the CLI help. |
| Channel rename / delete via `DatasetStore.rename_channel` / `delete_channel` writes to `metadata.attrs` and may race with a concurrent GUI session reading the same file | The existing GUI's per-operation HDF5 open/close discipline already handles this — the file lock prevents two writers from interleaving. Batch CLIs are typically run on closed datasets (GUI not open). No new risk beyond what the GUI / API already manage. |
| `delete_channel` could be called against a dataset that's currently the active channel in an open GUI session | Out of scope for the CLI — the GUI's launcher refreshes resource lists from disk on focus return, so a deletion underneath it is observable but not crash-inducing. Document the recommendation: close the GUI before batch ops. |
| The shared `_batch_report.py` helper drifts in semantics from `batch_phasor.py`'s in-file helpers | Out of scope to refactor `batch_phasor.py` to use the shared helper now (deferred); the duplication is small and stable. Capture the drift risk explicitly so a future pass can unify. |

---

## Documentation / Operational Notes

- Add a short CLI section to `README.md` (or to whatever doc lists `percell4-batch-phasor` today) covering the two new commands and the `--dry-run` flag. Mirror the format of the existing batch-phasor section.
- Capture the recommendation "close the GUI before batch rename / delete" in the CLI's `--help` epilog so first-time users see it.
- No new `docs/solutions/` entry warranted; the change reuses existing patterns and the new domain method is a small extraction.

---

## Sources & References

- Related code:
  - `src/percell4/store.py:DatasetStore` (`rename_channel`, `rename_item`, `delete_item`)
  - `backend/main.py:rename_resource` and `:delete_resource` (existing per-dataset HTTP API; U1 refactors the inline channel-delete logic)
  - `src/percell4/application/use_cases/batch_compute_phasor.py` (orchestrator pattern to mirror)
  - `src/percell4/interfaces/cli/batch_phasor.py` (CLI adapter pattern to mirror)
- Related plans / brainstorms:
  - `docs/plans/2026-05-26-001-feat-seg-qc-recovery-options-plan.md` — added the FastAPI `/rename` and `/delete` endpoints whose channel-delete logic U1 promotes to a domain method
  - `docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md` — the per-dataset Rename / Delete affordances on the Layer Management surface
- Related tests: `tests/test_cli_batch_phasor.py`, `tests/test_application/test_batch_compute_phasor.py`
