---
title: "feat: Full Cellpose settings + .h5 input for the batch CLI (renamed percell4-batch-cellpose-laptrack)"
type: feat
status: completed
date: 2026-06-05
---

# feat: Full Cellpose settings + .h5 input for the batch CLI (renamed `percell4-batch-cellpose-laptrack`)

## Overview

The headless batch CLI (`percell4-batch`) compresses TIFF directories, segments every timepoint with Cellpose, and tracks. It currently exposes only three of the ten Cellpose controls the GUI Segment tab offers (`--cellpose-model`, `--cellpose-diameter`, `--gpu`) and hardcodes the rest at `finalize()` defaults. It also only accepts TIFF source **directories**, forcing a re-compress even when an `.h5` already exists.

This plan brings the CLI to full parity with the GUI `CellposeSettingsForm` (all eight `CellposeSettings` fields plus edge-cell removal and edge margin), lets a source argument be an already-compressed `.h5` (segment in place, or copy into `--output-dir` first), and renames the command to `percell4-batch-cellpose-laptrack` (hard rename, no alias).

---

## Problem Frame

`percell4-batch` runs overnight on large multi-timepoint experiments before the interactive single-cell workflow. Researchers who tune Cellpose in the GUI (flow threshold, cellprob threshold, min cell size, saturation %, blur sigma, edge handling) cannot reproduce those settings headlessly — the batch path silently uses different values (e.g. `model=cyto3` and auto-diameter vs the GUI's `cpsam`/`300`, and always `remove_edge_cells=True, min_area=15` with no preprocessing). Results diverge between interactive tuning and batch production.

Separately, the batch always starts from TIFFs. When a dataset has already been compressed to `.h5` (by the GUI or a prior batch run), re-running segmentation with new settings requires re-importing the TIFFs, which is slow and needs the original TIFF tree on disk.

The fix has three parts: (1) thread the full `CellposeSettings` + edge options + saturation/blur preprocessing through the batch path so it mirrors the GUI Segment panel; (2) accept `.h5` sources; (3) rename the command to describe what it does (Cellpose + laptrack).

---

## Requirements Trace

- R1. Every control in the GUI `CellposeSettingsForm` is exposed as a `percell4-batch-cellpose-laptrack` flag: model, diameter, GPU, flow threshold, cellprob threshold, min cell size, saturation %, blur sigma — plus the Segment-panel edge controls (remove edge cells, edge margin).
- R2. CLI defaults match the GUI defaults (`CellposeSettings()` / the Segment panel): `model=cpsam`, `diameter=300`, `flow=0.4`, `cellprob=0.0`, `min_size=15`, `saturation=1.0%`, `blur=0.0`, remove-edge-cells on, edge-margin 0.
- R3. The batch segmentation result is identical (within Cellpose nondeterminism) to running the GUI Segment panel with the same settings on the same channel — saturation LUT and Gaussian blur are applied per-frame before inference, exactly as the GUI/`phases.segment_one` do.
- R4. A source argument may be an already-compressed `.h5` file; the compress/import step is skipped for it.
- R5. With no `--output-dir`, an `.h5` source is segmented + tracked **in place**. With `--output-dir`, the `.h5` is copied to `<output-dir>/<name>.h5` first and the copy is processed; the original is untouched.
- R6. TIFF directory sources continue to require `--output-dir` (import needs a destination); a clear error is raised if it is missing while any TIFF source is present.
- R7. The console-script entry point is renamed to `percell4-batch-cellpose-laptrack`; the old `percell4-batch` name is removed (hard rename). README and help text reflect the new name.
- R8. Existing batch behavior (TIFF in, segment all timepoints, track unless `--no-track`, per-dataset failure isolation, exit codes, `--channel-names`/`--seg-channel`/`--seg-name`) is preserved.

---

## Scope Boundaries

- Not adding a config-file (YAML/JSON) settings input — flags only, matching the other `percell4-batch-*` CLIs.
- Not changing the GUI Segment panel, `CellposeSettingsForm`, or `CellposeSettings`; this plan **consumes** them as the source of truth, it does not modify them.
- Not adding per-dataset Cellpose overrides — settings are batch-wide, as they are today.
- Not changing tracking (laptrack) parameters or exposing tracking flags beyond the existing `--no-track`.
- Not adding a backward-compat `percell4-batch` alias (user chose hard rename).
- Not renaming the module file `src/percell4/interfaces/cli/batch_process.py` or the use-case function — only the user-facing console-script name changes. (Keeps the diff small and avoids churning every test/doc that imports the module path. Revisit only if it causes confusion.)

---

## Context & Research

### Relevant Code and Patterns

- **CLI to modify:** `src/percell4/interfaces/cli/batch_process.py` — `main()` builds `argparse`, parses `--channel-names`, calls `batch_process_datasets`, prints a summary. `_build_specs()` turns source dirs into `DatasetSpec`s.
- **Use case to modify:** `src/percell4/application/use_cases/batch_process_datasets.py` — `batch_process_datasets(...)` and `DatasetSpec`. Per-dataset try/except isolation; calls `import_dataset` → `LoadDataset` → `SegmentCells.run_inference[_stack]` → `finalize` → `TrackCells`.
- **Canonical full-settings segmentation path (mirror this):** `src/percell4/gui/segmentation_panel.py::_on_run_cellpose` — reads `CellposeSettings` from the shared form, applies a per-frame saturation LUT then Gaussian blur to an in-memory copy, calls `run_cellpose` / `run_cellpose_stack` with `flow_threshold`/`cellprob_threshold`/`min_size`, then `SegmentCells.finalize(...)`. This is the behavior R3 demands the batch reproduce.
- **Workflow segmentation (same preprocessing contract):** `src/percell4/workflows/phases.py::segment_one` and `_postprocess_labels` — `_preprocess(plane)` = saturation LUT then blur, per-frame; edge filter conditional on `EdgeMode`, then `filter_small_cells(min_area=cfg.min_size)`.
- **Settings source of truth:** `src/percell4/workflows/models.py::CellposeSettings` (fields + `__post_init__` validation) and `src/percell4/gui/_cellpose_settings_form.py` (`CELLPOSE_MODELS`, ranges, tooltips, defaults). Diameter default in the GUI Segment panel is seeded at `300.0` (`segmentation_panel.py` constructs `CellposeSettingsForm(initial=CellposeSettings(diameter=300.0))`).
- **Preprocessing primitives:** `src/percell4/domain/segmentation/preprocess.py` — `apply_saturation_lut`, `apply_gaussian_blur` (pure numpy; testable without Cellpose).
- **Inference adapter:** `src/percell4/adapters/cellpose.py` — `run_cellpose` already accepts `flow_threshold`, `cellprob_threshold`, `min_size`; `CellposeSegmenter.run` does **not** forward them yet (only `model_type`, `diameter`, `gpu`).
- **Inference seam:** `src/percell4/ports/segmenter.py::Segmenter.run` and `SegmentCells.run_inference` / `run_inference_stack` — both currently stop at `model_type`/`diameter`/`gpu`.
- **Post-processing:** `SegmentCells.finalize(min_area, remove_edge_cells, name, view_bin, edge_margin)` already supports the edge + min-size knobs; the batch currently calls it with defaults only.
- **Importer:** `src/percell4/adapters/importer.py::import_dataset(source_dir, output_h5, ...)` — TIFF dir in, `.h5` out.
- **Console scripts:** `pyproject.toml` `[project.scripts]` (line 83). **Docs:** `README.md` (TOC line 18; section lines 160–186).
- **Test patterns:** `tests/test_application/test_batch_process_cli.py` (monkeypatches `batch_process_datasets` to a stub, asserts forwarded kwargs/specs/exit codes); `tests/test_application/test_batch_process_datasets.py` (`FakeSegmenter.run(self, image, **kwargs)` — already absorbs extra kwargs, so extending the port is non-breaking; `FakeTracker`, real repo on `tmp_path`).

### Institutional Learnings

- Run the `compound-engineering:ce-learnings-researcher` agent before editing the T1 files in scope — `src/percell4/adapters/` (cellpose adapter), `src/percell4/application/use_cases/` (segment_cells, batch_process_datasets), and the importer — per CLAUDE.md's audit-driven retrieval rule. The `PreToolUse` hook will also warn on these.
- Seg-name collision in the flat HDF5 namespace crashes the GUI on load — the use case already guards this (`seg_name in existing`); preserve that guard on the `.h5`-input path too (an in-place `.h5` already has channels/labels to collide with).

### External References

- None needed — Cellpose parameters are already wired through `run_cellpose`; this is internal threading and CLI surface work. No external research.

---

## Key Technical Decisions

- **Mirror the GUI Segment panel, not a new path.** The batch use case will apply the same per-frame saturation LUT + Gaussian blur preprocessing and pass the same inference params, so R3 (parity) holds by construction. Preprocessing lives in the use case (pure numpy, testable without Cellpose), before the inference seam.
- **Extend the `Segmenter` port rather than bypass it.** Add `flow_threshold`, `cellprob_threshold`, `min_size` (with `run_cellpose`'s defaults) to `Segmenter.run`, `CellposeSegmenter.run`, and `SegmentCells.run_inference`/`run_inference_stack`. This keeps the existing injection seam used by tests (`FakeSegmenter.run(**kwargs)` already absorbs them) and by `batch_process_datasets`. Bypassing the port to call `run_cellpose` directly would lose that seam.
- **Pass a `CellposeSettings` into `batch_process_datasets`.** Replace the three loose `cellpose_*`/`gpu` params with a single `settings: CellposeSettings` plus `remove_edge_cells: bool` and `edge_margin: int`. One validated object, no field drift, `__post_init__` enforces invariants. (Edge controls stay separate because they are Segment-panel-level, not `CellposeSettings` fields.)
- **`min_size` flows to both inference and finalize.** `run_cellpose` uses `min_size` to drop tiny masks during inference; `finalize` re-applies it as `min_area` via `filter_small_cells`. The GUI does the same (settings → inference, and finalize). Pass `settings.min_size` to `finalize(min_area=...)` so the two stay consistent.
- **`.h5` input detected by suffix; in-place vs copy decided by `--output-dir`.** `DatasetSpec` gains a way to express "source is already `.h5`" (e.g. `source` may be a dir or a file; `output_h5 == source` ⇒ in place). The use case skips `import_dataset` for `.h5` sources, optionally `shutil.copy2`-ing to the output path first.
- **`--output-dir` becomes optional, validated by source kind.** Required iff any source is a TIFF directory (import needs a destination). All-`.h5` with no `--output-dir` ⇒ in place. Mixed inputs allowed.
- **Hard rename, module path unchanged.** Only `[project.scripts]` and `prog=`/docs change; `batch_process.py` and `batch_process_datasets` keep their import paths to minimize churn.

---

## Open Questions

### Resolved During Planning

- Where do `.h5`-input results go? — In place by default; copy into `--output-dir` when given (user decision).
- Keep an old-name alias? — No; hard rename (user decision).
- Should CLI defaults match the GUI? — Yes; defaults become `CellposeSettings()` values with `diameter=300` (user decision).
- Where does saturation/blur preprocessing belong on the batch path? — In `batch_process_datasets`, per-frame, before the inference seam, mirroring `segmentation_panel._on_run_cellpose` and `phases.segment_one`.

### Deferred to Implementation

- Exact `DatasetSpec` shape for expressing dir-vs-`.h5` source (add a field/property vs infer from suffix at use-case time) — pick whichever reads cleanest once editing; both satisfy R4/R5.
- Whether to reuse a single shared `_preprocess(plane, settings)` helper (extracted from the GUI panel / phases) vs inlining the two-line LUT+blur loop in the use case — decide at edit time; if extracting, place it next to `apply_saturation_lut`/`apply_gaussian_blur` in `domain/segmentation/preprocess.py` and have all three callers use it. Inlining is acceptable (it is two guarded calls) but note the drift risk in a comment.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Input-handling decision per source argument:

| Source argument        | `--output-dir` given? | Compress/import? | Output `.h5`                  |
|------------------------|-----------------------|------------------|-------------------------------|
| TIFF directory         | yes                   | yes (`import_dataset`) | `<output-dir>/<dirname>.h5` |
| TIFF directory         | no                    | — (error R6)     | — (validation failure)        |
| `.h5` file             | no                    | no               | the source `.h5`, in place    |
| `.h5` file             | yes                   | no (copy first)  | `<output-dir>/<name>.h5` (copy of source) |

Per-dataset flow inside `batch_process_datasets` (unchanged spine, new settings threaded):

```
for spec in specs:
    h5 = resolve_input(spec)              # import TIFFs | copy .h5 | use .h5 in place
    handle = LoadDataset(...).execute(h5)
    apply channel_names override (existing)
    resolve seg_channel, n_timepoints (existing)
    guard seg_name collision (existing)

    image|stack = repo.read_channel_images(...)[ch]
    preprocessed = per-frame: saturation_lut -> gaussian_blur   # NEW, mirrors GUI
    raw = SegmentCells.run_inference[_stack](
              preprocessed,
              model_type=settings.model, diameter=settings.diameter, gpu=settings.gpu,
              flow_threshold=settings.flow_threshold,               # NEW
              cellprob_threshold=settings.cellprob_threshold,       # NEW
              min_size=settings.min_size)                           # NEW
    seg = SegmentCells.finalize(
              raw, name=seg_name,
              min_area=settings.min_size,                           # NEW (was default 15)
              remove_edge_cells=remove_edge_cells,                  # NEW (was default True)
              edge_margin=edge_margin)                              # NEW (was default 0)
    if n_timepoints > 1 and track: TrackCells(...).execute(seg.seg_name)   # existing
```

---

## Implementation Units

- U1. **Extend the segmentation inference seam to carry flow/cellprob/min_size**

**Goal:** Let the full Cellpose inference parameters flow through the `Segmenter` port to `run_cellpose`, so the batch (and any future caller) can set flow threshold, cellprob threshold, and min size.

**Requirements:** R1, R3

**Dependencies:** None

**Files:**
- Modify: `src/percell4/ports/segmenter.py`
- Modify: `src/percell4/adapters/cellpose.py` (`CellposeSegmenter.run`)
- Modify: `src/percell4/application/use_cases/segment_cells.py` (`run_inference`, `run_inference_stack`)
- Test: `tests/test_application/test_segment_cells.py` (extend; create if absent)
- Test: `tests/test_adapters/test_cellpose.py` (extend if present; otherwise assert forwarding via a fake `model`)

**Approach:**
- Add `flow_threshold: float = 0.4`, `cellprob_threshold: float = 0.0`, `min_size: int = 15` to `Segmenter.run`, `CellposeSegmenter.run`, and both `SegmentCells.run_inference`/`run_inference_stack`. Defaults equal `run_cellpose`'s current defaults so all existing callers are unaffected.
- `CellposeSegmenter.run` forwards the three new args to `run_cellpose`. `run_inference_stack` forwards them per frame.
- Keep `model_type`/`diameter`/`gpu` exactly as-is.

**Patterns to follow:**
- `run_cellpose` parameter names and defaults in `src/percell4/adapters/cellpose.py` (use the identical names/defaults).
- `FakeSegmenter.run(self, image, **kwargs)` in `tests/test_application/test_batch_process_datasets.py` (the seam already tolerates extra kwargs).

**Test scenarios:**
- Happy path: `SegmentCells.run_inference(img, flow_threshold=0.7, cellprob_threshold=-1.0, min_size=40)` calls the injected segmenter with those exact values (spy/fake records kwargs).
- Happy path: `run_inference_stack` on a `(T,H,W)` stack forwards the three params on every frame.
- Edge case: omitting the new args yields the current defaults (`0.4`, `0.0`, `15`) — proves backward compatibility.
- Integration: `CellposeSegmenter.run(..., flow_threshold=x, cellprob_threshold=y, min_size=z, model=<fake>)` forwards to `run_cellpose` (inject a fake `model` whose `.eval` records kwargs, so no real Cellpose needed).

**Verification:**
- Existing segment_cells/cellpose tests still pass; new forwarding assertions pass; no signature break for current callers.

---

- U2. **Thread full `CellposeSettings` + edge options + preprocessing + `.h5` input through `batch_process_datasets`**

**Goal:** The use case applies the GUI's preprocessing and full settings, writes edge/min-size-correct labels, and accepts `.h5` sources (in place or copied into the output dir).

**Requirements:** R1, R2, R3, R4, R5, R6, R8

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/application/use_cases/batch_process_datasets.py`
- Test: `tests/test_application/test_batch_process_datasets.py` (extend)

**Approach:**
- Replace the `cellpose_model`/`cellpose_diameter`/`gpu` params with `settings: CellposeSettings` (default `CellposeSettings()` is fine; the CLI will pass an explicit one) plus `remove_edge_cells: bool = True` and `edge_margin: int = 0`.
- Before inference, build the preprocessed image/stack per-frame: `apply_saturation_lut` when `settings.saturation_pct > 0`, then `apply_gaussian_blur` when `settings.blur_sigma > 0` — mirroring `segmentation_panel._on_run_cellpose` and `phases.segment_one._preprocess`. Single-frame and `(T,H,W)` paths both preprocess per frame.
- Pass `settings.model`/`diameter`/`gpu` + `flow_threshold`/`cellprob_threshold`/`min_size` (from U1) to `run_inference`/`run_inference_stack`.
- Call `finalize(raw, name=seg_name, min_area=settings.min_size, remove_edge_cells=remove_edge_cells, edge_margin=edge_margin)`.
- Input resolution: detect `.h5` source by suffix. If `.h5` and `output_h5 == source` ⇒ skip import, operate in place. If `.h5` and `output_h5 != source` ⇒ `shutil.copy2(source, output_h5)` then operate on the copy. If TIFF dir ⇒ `import_dataset` as today. Encode the dir-vs-`.h5` distinction on `DatasetSpec` (field or property) — see deferred question.
- Preserve all existing behavior: channel-name override + count-mismatch failure, seg-channel resolution, seg-name collision guard (now also relevant for in-place `.h5`), tracking gate, per-dataset try/except isolation, `progress_callback`, report shape.

**Patterns to follow:**
- `src/percell4/gui/segmentation_panel.py::_on_run_cellpose` (preprocessing + run + finalize ordering).
- `src/percell4/workflows/phases.py::segment_one` / `_preprocess` (per-frame preprocessing contract).
- Existing try/except + `BatchProcessItemResult` recording in the same file.

**Test scenarios:**
- Happy path (TIFF dir, unchanged): with a `FakeSegmenter`/`FakeTracker`, a TIFF source still imports, segments, tracks; report counts unchanged from today's tests.
- Happy path (settings forwarded): a spy segmenter records that `flow_threshold`/`cellprob_threshold`/`min_size` from the passed `CellposeSettings` reach `.run`.
- Happy path (preprocessing applied): with `saturation_pct>0` and `blur_sigma>0`, the array handed to the segmenter differs from the raw channel (and is unchanged when both are 0) — assert against the in-memory image, and assert on-disk `/intensity` is **not** modified.
- Happy path (edge/min-size to finalize): `remove_edge_cells=False` leaves border-touching labels; `edge_margin=N` widens the band; `min_area=settings.min_size` drops small labels (use a hand-built raw mask via the FakeSegmenter return).
- `.h5` in place (R5): source is an `.h5`, no `output_h5` redirect ⇒ no `import_dataset` call (monkeypatch/spy), segmentation written into the same file; original channels preserved.
- `.h5` copied (R5): source `.h5` with a distinct `output_h5` ⇒ file copied, copy segmented, **original byte-unchanged** (compare mtime/size or a checksum).
- Edge case (seg-name collision on in-place `.h5`): a `seg_name` colliding with an existing channel/label in the `.h5` is rejected as that dataset's failure; batch continues.
- Time-lapse (R3): a `(T,H,W)` `.h5` preprocesses and segments every timepoint and tracks unless `track=False`.
- Failure isolation (R8): one bad dataset (e.g. channel-name count mismatch) is recorded as failed; siblings still succeed; exit semantics unchanged.

**Verification:**
- All existing `test_batch_process_datasets.py` tests pass after the signature change (update call sites to pass `settings=`); new scenarios pass; on-disk intensity never mutated by preprocessing.

---

- U3. **Add the CLI flags, build `CellposeSettings`, make `--output-dir` optional, rename `prog`**

**Goal:** `percell4-batch-cellpose-laptrack` exposes every GUI Cellpose control, defaults to the GUI values, accepts `.h5` and TIFF sources, and validates `--output-dir` by source kind.

**Requirements:** R1, R2, R4, R5, R6, R7, R8

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/interfaces/cli/batch_process.py`
- Test: `tests/test_application/test_batch_process_cli.py` (extend)

**Approach:**
- New flags (defaults = GUI): keep `--cellpose-model` (default now `cpsam`), `--cellpose-diameter` (default `300.0`), `--gpu`; add `--flow-threshold` (0.4), `--cellprob-threshold` (0.0), `--min-size` (15), `--saturation` (1.0, percent), `--blur-sigma` (0.0), `--no-remove-edge-cells` (store_false → `remove_edge_cells`), `--edge-margin` (0). Help text mirrors the `CellposeSettingsForm` tooltips.
- Build a `CellposeSettings(...)` from parsed args (let `__post_init__` validate; surface a `ValueError` as a clean CLI error + nonzero exit rather than a traceback).
- Make `--output-dir` optional. `_build_specs`: for each source, if it is an existing `.h5` file → `DatasetSpec` whose output is `<output-dir>/<name>.h5` when `--output-dir` given else the source itself (in place); if it is a directory → require `--output-dir` (error R6 if missing); skip non-existent paths with a stderr note (as today for non-dirs).
- Only `mkdir` the output dir when one is given.
- Pass `settings=`, `remove_edge_cells=`, `edge_margin=` to `batch_process_datasets`. Preserve `--seg-channel`/`--channel-names`/`--seg-name`/`--no-track`/`--quiet`/`--verbose`.
- Update `prog="percell4-batch-cellpose-laptrack"`, the module docstring usage lines, and `description`.

**Patterns to follow:**
- Existing `argparse` block and `_build_specs`/`_progress` in `batch_process.py`.
- Flag ranges/defaults from `src/percell4/gui/_cellpose_settings_form.py` and `CellposeSettings`.
- CLI-test stub pattern in `tests/test_application/test_batch_process_cli.py` (monkeypatch the use case, assert forwarded kwargs).

**Test scenarios:**
- Happy path: full flag set parses and the constructed `CellposeSettings` (forwarded to the stubbed use case) carries every value.
- Defaults (R2): with no Cellpose flags, the forwarded `settings` equals `CellposeSettings(diameter=300.0)` and `remove_edge_cells=True`, `edge_margin=0`.
- `--no-remove-edge-cells` ⇒ `remove_edge_cells=False` forwarded.
- `.h5` source, no `--output-dir` (R5): spec output == source path (in place); no error.
- `.h5` source, with `--output-dir` (R5): spec output == `<output-dir>/<name>.h5`.
- TIFF dir, no `--output-dir` (R6): exits nonzero with a clear message; use case not called.
- Mixed sources: one `.h5` + one TIFF dir with `--output-dir` both produce specs.
- Error path: an out-of-range value (e.g. `--saturation 99`) surfaces the `CellposeSettings` `ValueError` as a clean nonzero-exit message, not a traceback.
- Preserved flags: `--no-track`/`--seg-channel`/`--channel-names`/`--seg-name` still forwarded (existing assertions kept).

**Verification:**
- `percell4-batch-cellpose-laptrack --help` lists all controls; existing CLI tests pass after rename; new scenarios pass.

---

- U4. **Rename the console-script entry and update docs**

**Goal:** The installed command is `percell4-batch-cellpose-laptrack`; `percell4-batch` no longer exists; README documents the new name and the new capabilities.

**Requirements:** R7, R1, R4, R5

**Dependencies:** U3

**Files:**
- Modify: `pyproject.toml` (`[project.scripts]`, line 83)
- Modify: `README.md` (TOC line 18; section + examples lines 160–186)
- Test: none (packaging/docs) — see expectation below

**Approach:**
- `pyproject.toml`: replace `percell4-batch = "percell4.interfaces.cli.batch_process:main"` with `percell4-batch-cellpose-laptrack = "percell4.interfaces.cli.batch_process:main"`. Reinstall (`pip install -e ".[dev]"`) so the new entry point is generated and the old one removed.
- `README.md`: rename the TOC entry and the section heading/anchor; update the synopsis and examples to the new command; document `.h5` sources (in-place vs `--output-dir` copy) and the new Cellpose/edge flags. Update the "Headless / SSH use" note only if it names the command (it uses the `percell4-batch*` glob, which still matches).
- Grep for any other live references to the bare `percell4-batch` command in docs that describe *this* CLI (not `-export`/`-phasor`/etc.) and update prose; leave historical plan/brainstorm docs untouched (they are dated records).

**Patterns to follow:**
- Sibling CLI doc sections in `README.md` (`percell4-batch-export`, `percell4-batch-phasor`) for synopsis/example formatting.

**Test scenarios:**
- Test expectation: none — packaging/doc change. Manually verify post-reinstall that `percell4-batch-cellpose-laptrack --help` resolves and `percell4-batch` is gone (`command -v percell4-batch` returns nonzero).

**Verification:**
- After `pip install -e ".[dev]"`, the new command runs and the old one is absent; README has no stale `percell4-batch ` (trailing-space) command references for this CLI.

---

## System-Wide Impact

- **Interaction graph:** `Segmenter` port signature change (U1) touches every implementer/caller — only `CellposeSegmenter` (real) and `FakeSegmenter` (test, absorbs `**kwargs`) implement it; the GUI panel calls `run_cellpose` directly (unaffected). New params are defaulted, so `SegmentCells` callers that don't pass them are unaffected.
- **Error propagation:** `CellposeSettings.__post_init__` validation should surface as a clean CLI error (nonzero exit), not a traceback. Per-dataset failures in the use case remain isolated and recorded.
- **State lifecycle risks:** In-place `.h5` segmentation mutates the user's file — the seg-name collision guard must run on this path; the copy path must leave the original byte-unchanged. Preprocessing must never touch on-disk `/intensity` (only an in-memory copy is preprocessed).
- **API surface parity:** CLI is now the parity surface for the GUI Segment panel — defaults and flag semantics must track `CellposeSettings`/`CellposeSettingsForm`. Note the drift risk if a future field is added to `CellposeSettings` without a matching flag.
- **Integration coverage:** The `.h5`-in-place, `.h5`-copy, and TIFF-dir paths each need a use-case-level test with a real repo on `tmp_path` (mocks alone won't prove the import-skip / copy / collision behavior).
- **Unchanged invariants:** Module import paths (`percell4.interfaces.cli.batch_process`, `batch_process_datasets`), tracking behavior, exit codes, `--channel-names`/`--seg-channel`/`--seg-name`/`--no-track` semantics, and the `BatchProcessReport`/`BatchProcessItemResult` shapes are unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Default change (`cyto3`→`cpsam`, auto→300) silently alters results for existing batch scripts | Intentional per user (true parity); call it out in README and the PR description so users re-confirm settings. |
| In-place `.h5` segmentation mutates a user's source file | Default documented clearly; collision guard prevents namespace corruption; copy path (`--output-dir`) offered for non-destructive runs and verified to leave originals byte-unchanged. |
| Preprocessing path drifts from the GUI/`phases` over time | Mirror the exact ordering and per-frame contract; reference the canonical functions in code comments; optionally extract one shared `_preprocess` helper (deferred question). |
| `Segmenter` port change breaks an unseen implementer | Only two implementers exist; new params are defaulted; U1 ships before consumers. |
| Cellpose 3.x vs 4.x model handling (`model_type` ignored on v4) | No change — `run_cellpose`/`build_cellpose_model` already branch on version; the CLI just passes `settings.model` through as today. |

---

## Documentation / Operational Notes

- README is the primary doc surface (U4). No migration scripts; reinstall regenerates the entry point.
- Mention in the PR description that `percell4-batch` is removed (hard rename) and that batch Cellpose defaults now equal the GUI's.

---

## Sources & References

- Related code: `src/percell4/interfaces/cli/batch_process.py`, `src/percell4/application/use_cases/batch_process_datasets.py`, `src/percell4/application/use_cases/segment_cells.py`, `src/percell4/adapters/cellpose.py`, `src/percell4/ports/segmenter.py`, `src/percell4/gui/segmentation_panel.py`, `src/percell4/gui/_cellpose_settings_form.py`, `src/percell4/workflows/models.py`, `src/percell4/workflows/phases.py`, `src/percell4/domain/segmentation/preprocess.py`
- Related plan: `docs/plans/2026-06-03-001-feat-segment-tab-cellpose-settings-plan.md` (the GUI Segment-tab settings work this plan brings to parity)
- Related tests: `tests/test_application/test_batch_process_cli.py`, `tests/test_application/test_batch_process_datasets.py`
