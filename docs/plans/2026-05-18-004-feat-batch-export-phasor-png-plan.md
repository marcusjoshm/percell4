---
title: "feat: Batch-export phasor plots as PNG across .h5 datasets"
type: feat
status: completed
date: 2026-05-18
---

# feat: Batch-export phasor plots as PNG across .h5 datasets

## Overview

Add a headless CLI command that, for one or more `.h5` datasets, renders
every cached phasor as a `.png` into a specified output directory. For
each channel under `/phasor/<ch>`, it writes a raw phasor PNG (from
`g`/`s`) and, when wavelet-filtered maps exist, a separate filtered PNG
(from `g_filtered`/`s_filtered`). Each PNG mirrors what the GUI phasor
window shows: an intensity-weighted 2D histogram (300 bins, `log1p`,
`nipy_spectral` colormap) with the universal semicircle overlay and
labeled G/S axes.

This is the phasor analogue of the existing `batch_export` (TIFF layers)
and `batch_phasor` (compute) CLIs, and the third sibling in the
`feat/cli-batch-commands` line of work.

---

## Problem Frame

PerCell4 has a batch CLI to *compute* phasors (`batch_phasor`) and a
batch CLI to *export image layers* as TIFF (`batch_export`), but no way
to get the phasor *plots themselves* out of a dataset without opening the
GUI and screenshotting per channel. A researcher with a folder of dishes
wants a flat directory of phasor PNGs they can drop into a figure or
review at a glance. The rendering must match the GUI so the exported
image is the same artifact the user would see interactively.

No upstream requirements doc exists; this plan is built directly from the
request plus the established batch-CLI patterns in the repo.

---

## Requirements Trace

- R1. A CLI command accepts one or more `.h5` files and/or directories
  (directories globbed non-recursively for `*.h5`) and a required
  `--output-dir`.
- R2. For each dataset, for every channel under `/phasor/<ch>`, write a
  raw phasor PNG from `g`/`s`.
- R3. For every channel that additionally has both `g_filtered` and
  `s_filtered`, write a separate filtered phasor PNG.
- R4. Each PNG visually mirrors the GUI phasor histogram: intensity-
  weighted 2D histogram (bins=300, fixed G/S ranges, `log1p`,
  `nipy_spectral`), universal semicircle overlay, labeled axes,
  **G on x / S on y (no axis swap), and independent axis scaling
  (`aspect="auto"`)** so the semicircle is not distorted.
- R5. Intensity weights are derived from `decay/<ch>.sum(axis=-1)` — the
  canonical alignment rule — falling back to unweighted **only** when
  `/decay/<ch>` is absent. A `decay`/`g` **shape mismatch** is a
  stale-cache signal and is treated as a per-channel failure, never a
  silent unweighted render.
- R6. Per-dataset and per-channel failures isolate: one bad channel or
  unreadable file does not abort the batch; it is reported. Per-channel
  *skips* and per-channel *errors* are reported as distinct categories.
- R7. Exit code is 0 when at least one PNG was written across the batch,
  1 when none were (every dataset failed or had no phasor data).
- R8. The CLI module imports without pulling in Qt or napari.
- R9. A channel whose phasor exists but has no valid pixels (all
  NaN/zero) is surfaced to the user as an explicit signal (count in the
  report and CLI summary), not silently reported as a plain success.

---

## Scope Boundaries

- Does **not** compute phasors. Channels with no `/phasor/<ch>/g` are
  reported as having nothing to export; the user runs `batch_phasor`
  first. (Mirrors `batch_export`'s "compute is a separate CLI" stance.)
- Does **not** export lifetime maps, decay, or NPZ. Only the G–S phasor
  scatter/histogram as PNG.
- No GUI filters applied (cell selection, active mask, intensity
  threshold, reference circle, cleared-mask). The export is the full
  dataset phasor with only the always-on validity filter (finite,
  non-zero `g`). GUI-parity of *interactive filters* is explicitly out of
  scope for this iteration.
- No per-dataset subfolders. Flat output layout, matching `batch_export`.
- Existing PNGs at target paths are overwritten silently (matplotlib
  `savefig` behavior), matching `batch_export`'s documented contract.

### Deferred to Follow-Up Work

- Optional `--filtered-only` / `--raw-only` selector flags: future
  iteration if users want to halve output volume. Default emits both.
- A `--dpi` / figure-size flag: future iteration; fixed sensible default
  for now.
- Extract `LoadCachedPhasor`'s pure read + asymmetric-cache logic into a
  shared `Session`-free helper consumed by both the GUI use case and the
  batch use cases (`batch_export_phasor`, and retrofit
  `batch_compute_phasor`). Deferred because it touches the GUI-critical
  read path; tracked as the durable fix for the cross-use-case
  duplication this plan's replication accepts.

---

## Context & Research

### Relevant Code and Patterns

- **CLI pattern source:** `src/percell4/interfaces/cli/batch_export.py`
  and `src/percell4/interfaces/cli/batch_phasor.py` — `main(argv)`,
  `_resolve_paths`, `_format_item_line`, `_print_item_status`,
  `--quiet`/`--verbose`, exit-code convention. Every sibling is
  registered as a `[project.scripts]` console entry point and is also
  runnable via `python -m percell4.interfaces.cli.<name>`.
- **Use-case pattern source:**
  `src/percell4/application/use_cases/batch_export_images.py` and
  `batch_compute_phasor.py` — frozen `…ItemResult` / `…Report`
  dataclasses, `_process_one_dataset` per-item isolation, sequential
  single-process orchestration, `progress_callback`.
- **Canonical phasor read:**
  `src/percell4/application/use_cases/load_cached_phasor.py` —
  authoritative logic for which HDF5 paths map to raw vs filtered, the
  asymmetric-cache guard (g without s → treat as no cache), and the
  rule that intensity MUST come from `decay.sum(axis=-1)`, not
  `/intensity[ch_idx]`. The batch use case replicates this read logic
  against `DatasetStore` directly (no `Session`), preserving the
  alignment rule.
- **GUI render math to mirror:**
  `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
  `_refresh_histogram` (lines ~1694–1786) and the semicircle
  construction (lines ~426–434): `theta = linspace(0, pi, 200)`,
  `semi_g = 0.5 + 0.5*cos(theta)`, `semi_s = 0.5*sin(theta)`;
  `np.histogram2d(g, s, bins=300, range=[(-0.005, 1.005), (0.0, 0.7)],
  weights=intensity)`; `np.log1p`; `nipy_spectral` colormap.
- **Reusable Qt-free validity filter:**
  `src/percell4/domain/flim/phasor_display.py`
  `compute_valid_phasor_pixels` — call with only `g_flat`, `s_flat` and
  all optional filters `None` to get exactly the always-on validity
  mask the GUI applies before binning.
- **Store API:** `DatasetStore.list_groups("phasor")` → channel names;
  `DatasetStore.read_array("phasor/<ch>/g")` etc.;
  `DatasetStore.read_decay(ch)` for intensity weights.
- **matplotlib** is already a hard dependency (`matplotlib>=3.8`,
  pyproject line 30) — currently only a colormap source for pyqtgraph.
  Headless rendering uses the Agg backend, no Qt.

### Institutional Learnings

- Cross-layer alignment learning (cited verbatim in
  `load_cached_phasor.py`): intensity for the phasor histogram must be
  `decay.sum(axis=-1)`, never `/intensity[ch_idx]`. This plan honors it
  in U2/U3.
- Asymmetric-cache guard: `g_filtered` present without `s_filtered`
  (or vice versa) is treated as *no filtered cache*, not an error.
  Mirrored in U2.
- Per `CLAUDE.md` audit-driven retrieval (R15/R16): the
  `application/use_cases/` files are T1 — invoke
  `compound-engineering:ce-learnings-researcher` against the unit file
  paths before non-trivial edits during execution.

### External References

- None required. matplotlib 2D-histogram + `imshow` is well-patterned;
  the GUI is the authoritative spec for visual parity.

---

## Key Technical Decisions

- **matplotlib Agg, not pyqtgraph offscreen:** Headless, no Qt event
  loop, keeps R8 (CLI imports without Qt/napari) trivially true. The
  same *intent* as the existing `test_cli_module_imports_without_qt`
  seam test in `tests/test_cli_batch_export.py`, with a stronger
  `sys.modules` assertion (that existing test only does
  `importlib.reload` + `hasattr`). matplotlib is already a dep.
- **GUI render parity is specified, not deferred:** "Mirror the GUI"
  is decomposed into concrete, testable choices because the dominant
  parity factors are *not* the binning constants but the axis
  treatment: (a) transpose — `imshow(hist.T, origin="lower", …)`
  because the GUI relies on pyqtgraph col-major and does not itself
  transpose; (b) `aspect="auto"` because matplotlib's `imshow`
  defaults to `aspect="equal"`, which would distort the semicircle
  the GUI draws undistorted. An orientation-discriminating test (U1)
  is the correctness gate — a file-exists assertion cannot catch a
  G/S swap, which is scientifically wrong but visually plausible.
- **Renderer lives in the application layer, not `domain/flim`:** The
  import-linter forbids `domain` from importing infrastructure and
  `flim/CLAUDE.md` declares domain flim "pure numpy". A module that
  builds a Figure and writes a file is not pure numpy, so the renderer
  is `src/percell4/application/phasor_render.py`. The pure validity math
  stays reused from `domain/flim/phasor_display.py`. This mirrors how
  `batch_export_images` keeps `tifffile.imwrite` in the application
  layer rather than domain.
- **Replicate `LoadCachedPhasor` read logic instead of reusing the
  class:** `LoadCachedPhasor` requires a `Session` (`session.dataset`).
  The batch use cases (`batch_export_images`, `batch_compute_phasor`)
  deliberately work `Session`-free against `DatasetStore`. The plan
  follows that established precedent and replicates the small read +
  asymmetric-cache logic, citing `load_cached_phasor.py` as the
  canonical source. *Alternative weighed:* extract
  `LoadCachedPhasor`'s pure read + asymmetric-cache logic into a
  `Session`-free helper consumed by both the GUI use case and this
  batch use case — eliminating the duplication the R15/R16
  drift-prevention posture exists to prevent. **Rejected for this
  iteration** because refactoring the GUI-critical `LoadCachedPhasor`
  read path is out of scope and higher-risk than a cited replication;
  the replication is narrow (path mapping + asymmetric guard) and U2's
  explicit alignment-precondition enforcement (below) actually makes
  the batch path *stricter* than `LoadCachedPhasor`, not a silent
  copy. A follow-up to extract the shared helper is noted in Scope
  Boundaries.
- **Batch enforces the phasor/decay alignment invariant that
  `LoadCachedPhasor` only trusts:** `LoadCachedPhasor` documents that a
  non-empty `/phasor/<ch>` is *by invariant* aligned with
  `/decay/<ch>`, maintained by the live recompute/reimport chain. That
  invariant does not hold for arbitrary on-disk files a batch CLI
  ingests. U2 therefore treats a `decay`/`g` shape mismatch as a
  per-channel error (run `batch_phasor`), never a silent unweighted
  render — see the U2 alignment precondition.
- **Both raw and filtered PNGs, separate files** (user decision):
  `<stem>_<ch>_phasor.png` (raw) and `<stem>_<ch>_phasor_filtered.png`
  (filtered, only when both filtered maps exist).
- **Mirror the GUI look** (user decision): intensity-weighted,
  `log1p`, `nipy_spectral`, semicircle overlay, labeled G/S axes.
- **Flat layout, silent overwrite:** consistency with `batch_export`'s
  documented contract; one fewer concept for users to learn.

---

## Open Questions

### Resolved During Planning

- Which phasor representation? → Both raw and filtered, separate PNGs
  per channel (user).
- Render style? → Mirror the GUI (user).
- Intensity source? → `decay.sum(axis=-1)` with unweighted fallback,
  per the canonical alignment learning.
- Renderer placement? → application layer (`phasor_render.py`), not
  domain, to satisfy the domain-purity ("pure numpy") convention. Note:
  this does *not* make the use case import-linter-compliant — see the
  Risks table; the `application/` use case reading `DatasetStore`/h5py
  is a pre-existing contract violation shared by the sibling batch use
  cases, and this plan follows that precedent knowingly.
- Asymmetric filtered cache (`g_filtered` without `s_filtered`)? →
  Treat as no filtered cache (per `load_cached_phasor.py`) **and**
  record a structured `skipped` entry for the filtered output, not a
  log-only note. Resolves the earlier "skipped or logged" ambiguity.
- `decay`/`g` shape mismatch (stale phasor)? → Per-channel **error**
  with a run-`batch_phasor` reason; no PNG for that channel. Not a
  silent unweighted render.
- All-NaN/zero phasor channel? → Still render the (empty) PNG, but
  record the output in `rendered_empty` and surface a count in the
  report + CLI `Totals:` line (R9).

### Deferred to Implementation

- Exact figure size / DPI default: pick a sensible value during
  implementation (e.g. square figure ≈ 6in, ≈150 dpi) and assert the
  file is a valid non-trivial PNG rather than pixel-locking it.
- Whether to add a thin colorbar: implement to match the GUI; if the
  GUI shows none, omit. Decide against the live GUI during execution,
  not in the plan.

---

## Implementation Units

- U1. **Headless phasor PNG renderer**

**Goal:** A pure-ish function that turns `(g, s, intensity, out_path)`
into a PNG matching the GUI phasor histogram.

**Requirements:** R4, R5 (consumes pre-derived intensity), R8, R9
(returns the empty/with-data outcome enum U2 propagates)

**Dependencies:** None

**Files:**
- Create: `src/percell4/application/phasor_render.py`
- Test: `tests/test_application/test_phasor_render.py`

**Approach:**
- `render_phasor_png(g, s, *, intensity=None, out_path, title=None)`.
- Set matplotlib backend to `Agg` at import (module-level
  `matplotlib.use("Agg")` before `pyplot` import) so no Qt is touched.
- Flatten `g`, `s`; compute the always-on validity mask by calling
  `compute_valid_phasor_pixels(g_flat, s_flat, None, None, None)` from
  `domain/flim/phasor_display.py` (reuse, do not reimplement).
- Weights: `intensity.ravel()[valid]` when `intensity` is not None and
  `intensity.size == g.size`; else unweighted (`np.ones`). Note: the
  *unweighted fallback in U1 is only correct for the legitimate
  "no decay present" case*. A decay/`g` **shape mismatch** is a
  stale-cache signal and is the caller's (U2's) responsibility to
  detect and treat as a per-channel failure **before** calling this
  renderer — U1 must not paper over a misalignment by silently
  unweighting (see U2 alignment precondition).
- `np.histogram2d` with **exactly** the GUI constants: `bins=300`,
  `range=[(-0.005, 1.005), (0.0, 0.7)]`; `np.log1p` the counts.
- **Orientation (factual correction):** `np.histogram2d(g, s, …)`
  returns an array of shape `(n_g_bins, n_s_bins)`. The GUI does **not**
  transpose — pyqtgraph's `ImageItem` defaults to col-major
  (`axis0 → x`, `axis1 → y`), so G already maps to x there. matplotlib
  `imshow` is row-major (`axis0 → rows/y`), so the headless renderer
  **must** transpose: `imshow(hist.T, origin="lower",
  extent=[g_min, g_max, s_min, s_max], cmap="nipy_spectral")`. Mirror
  the GUI's *result* (G on x, S on y), not its code path.
- **Aspect (parity-critical):** set `ax.set_aspect("auto")` explicitly.
  The GUI's pyqtgraph plot scales the G axis (range ≈ 1.01) and S axis
  (range = 0.7) independently; matplotlib `imshow` defaults to
  `aspect="equal"`, which would squash the semicircle into an ellipse
  and skew the cloud even though every binning/colormap constant
  matches. `aspect="auto"` reproduces the GUI's independent axis
  scaling. This is the dominant visual-parity factor — it is a stated
  decision here, not a deferred implementation detail.
- Overlay the universal semicircle: `theta = linspace(0, pi, 200)`,
  `g = 0.5 + 0.5*cos(theta)`, `s = 0.5*sin(theta)`.
- Label axes ("G", "S"), set the same axis limits as the histogram
  range, set the title when provided. `savefig(out_path)`; close the
  figure to avoid the Agg memory leak. Create parent dirs.
- Empty-after-validity input (no finite non-zero pixels): still emit a
  valid PNG showing just the empty plot + semicircle, and **return an
  explicit outcome enum** (`RENDERED_WITH_DATA` vs `RENDERED_EMPTY`)
  that the caller (U2) is required to consume — not an advisory
  boolean the caller may ignore. Do not raise.

**Patterns to follow:**
- GUI math: `phasor_plot.py` `_refresh_histogram` + semicircle block.
- Validity reuse: `phasor_display.compute_valid_phasor_pixels`.

**Test scenarios:**
- Happy path: synthetic `g`, `s` clustered on the semicircle + matching
  `intensity` → PNG file exists, is non-empty, and is a decodable PNG
  (read back header / open with PIL or imghdr); function returns
  `RENDERED_WITH_DATA`.
- Happy path (unweighted): `intensity=None` → still writes a valid PNG;
  no exception; returns `RENDERED_WITH_DATA`.
- Orientation (axis-swap guard, mandatory): synthetic data with pixels
  clustered at **G≈0.9, S≈0.1** and none elsewhere → load the saved
  PNG, sample pixel color in the figure's bottom-right data region vs
  the top-left, and assert the bright (high-density) bin is
  **bottom-right** (high G, low S), not top-left. A
  file-exists/bbox-present assertion cannot detect a G/S swap; this
  scenario is the correctness gate for the transpose decision.
- Edge case: all-zero / all-NaN `g` → validity mask empty → PNG still
  written, function returns `RENDERED_EMPTY`; no exception.
- Edge case: `intensity` shape-mismatched vs `g` → falls back to
  unweighted (no crash), PNG written. (Note: in the batch path U2
  prevents this case from reaching U1 for the stale-cache reason — see
  U2 alignment precondition — so this test documents U1's defensive
  behavior for the standalone-call contract only.)
- Edge case: `out_path` in a not-yet-existing nested dir → parent dirs
  created, file written.
- Error path: `g` and `s` shape mismatch → raises `ValueError` with a
  clear message (caller treats as per-channel failure).
- Aspect: render a known dataset and assert the Axes aspect resolves to
  `"auto"` (guards against a future regression to matplotlib's
  `imshow` `"equal"` default that would distort the semicircle).
- Integration: no Qt/napari import — in a fresh interpreter, `import
  percell4.application.phasor_render` then assert
  `"PyQt5" not in sys.modules` and `"napari" not in sys.modules`. This
  is a *stronger* check than the existing
  `test_cli_module_imports_without_qt` in `test_cli_batch_export.py`
  (which only does `importlib.reload` + `hasattr` and makes no
  `sys.modules` assertion) — it is the same *intent*, not a copy of an
  existing assertion.

**Verification:** Rendering a known synthetic dataset produces a PNG
whose high-density region is at the **correct G/S position** (the
orientation guard, not merely "a bbox is present") with an undistorted
semicircle (`aspect="auto"`); the function returns the correct
outcome enum (`RENDERED_WITH_DATA` / `RENDERED_EMPTY`) and never raises
on degenerate but well-typed input; importing the module pulls in no
Qt/napari (`sys.modules` assertion).

---

- U2. **`batch_export_phasor` use case (orchestrator)**

**Goal:** Iterate datasets and channels, read raw/filtered phasor +
intensity, drive `render_phasor_png`, and produce a structured report.

**Requirements:** R2, R3, R5, R6, R7, R9

**Dependencies:** U1

**Files:**
- Create:
  `src/percell4/application/use_cases/batch_export_phasor.py`
- Test:
  `tests/test_application/test_batch_export_phasor.py`

**Alignment precondition (load-bearing — state in the module docstring):**
`LoadCachedPhasor` *trusts but does not enforce* that a non-empty
`/phasor/<ch>` is spatially aligned with the current `/decay/<ch>` —
that invariant is normally maintained by the live GUI/Session
recompute + TCSPC-reimport-clears-phasor chain. A batch CLI reads
arbitrary on-disk `.h5` files (older writers, interrupted writes,
hand-edited datasets) where the invariant can be violated. This use
case therefore **enforces** it: when `decay.sum(-1)` and `g` have
mismatched pixel counts, that is a stale-cache signal, recorded as a
per-channel **error** with a clear reason ("decay/phasor shape mismatch
— phasor likely stale, run batch_phasor"), **not** a silent unweighted
render. Restate this precondition verbatim in the module docstring the
way `load_cached_phasor.py` documents its own.

**Approach:**
- Frozen dataclasses mirroring siblings (note the **`skipped` /
  `errors` split**, matching `batch_phasor`'s
  `BatchPhasorItemResult`, so U3 can print the two categories
  distinctly):
  `BatchPhasorExportItemResult(h5_path, status, files_written,
  channels_exported: tuple[str, ...], skipped: dict[str, str],
  errors: dict[str, str], rendered_empty: tuple[str, ...],
  error: str | None)` and `BatchPhasorExportReport(items)` with
  `total_succeeded / total_failed / total_skipped /
  total_files_written / total_rendered_empty` properties.
  `skipped[<ch-or-output-name>]` = a channel/output deliberately not
  produced (no phasor, asymmetric filtered cache → no filtered PNG);
  `errors[...]` = a channel that should have produced output but failed
  (read raised, **decay/g shape mismatch**, render raised);
  `rendered_empty` = output names whose phasor existed but had zero
  valid pixels (R9). Status vocabulary mirrors `batch_export_images`:
  `succeeded`, `skipped_no_changes`, `failed`.
- `batch_export_phasor(h5_paths, *, output_dir, progress_callback=None)`
  → loop `_process_one_dataset`, append result, fire callback.
- `_process_one_dataset`: open `DatasetStore(h5_path)`; enumerate
  channels via `store.list_groups("phasor")`. If none →
  `skipped_no_changes`, 0 files.
- Per channel (isolated `try/except`, never aborts the dataset):
  - Read `phasor/<ch>/g`, `phasor/<ch>/s` (missing/asymmetric g-without-s
    → record in `skipped`, continue).
  - Read `phasor/<ch>/g_filtered` + `s_filtered`; **both required**
    together (asymmetric → treat as no filtered cache per the
    `load_cached_phasor.py` guard, and record a `skipped` entry for the
    filtered output so the omission is visible — resolves the prior
    "skipped or logged" ambiguity in favor of structured tracking).
  - Intensity: `store.read_decay(ch).sum(axis=-1).astype(float32)`;
    `KeyError`/missing decay → `intensity=None` (legitimate unweighted
    render). Cite the canonical alignment rule in a comment.
  - **Alignment check:** if `intensity` is not None and
    `intensity.size != g.size` → record an `errors` entry with the
    stale-cache reason and **skip both PNGs for this channel** (do not
    fall through to an unweighted render). Per the alignment
    precondition above.
  - Call `render_phasor_png` for raw →
    `<output_dir>/<stem>_<ch>_phasor.png`; if filtered present, again →
    `<stem>_<ch>_phasor_filtered.png`. Count files written. If the
    renderer returns `RENDERED_EMPTY`, append the output name to
    `rendered_empty` (the PNG still counts as written — it exists — but
    the empty signal is recorded for R9).
  - A render raising → `errors` entry, continue to the next channel.
- Dataset status: `succeeded` if ≥1 file written; `failed` only when
  the dataset could not be opened/enumerated at all;
  `skipped_no_changes` when there were channels but every one was
  skipped/errored and zero files resulted. Per-channel skip reasons
  travel in `skipped`, per-channel failures in `errors`.
- Header docstring explicitly states pattern source
  (`batch_compute_phasor.py`) and canonical read source
  (`load_cached_phasor.py`), restates the alignment precondition, and
  matches the sibling files' docstring convention.

**Execution note:** Before editing, this is a T1
`application/use_cases/` file — invoke
`compound-engineering:ce-learnings-researcher` with the unit's file
paths per CLAUDE.md R15/R16.

**Patterns to follow:**
- `batch_export_images.py` structure (`_process_one_dataset`, report
  properties, status classification).
- `load_cached_phasor.py` for path mapping + asymmetric-cache handling
  + intensity-from-decay rule.

**Test scenarios:**
- Happy path: dataset with two channels each having `g`/`s` and
  `g_filtered`/`s_filtered` + `/decay/<ch>` → 4 PNGs written (raw +
  filtered × 2); status `succeeded`; `total_files_written == 4`;
  filenames match `<stem>_<ch>_phasor[_filtered].png`.
- Happy path (raw only): channel with `g`/`s` but no filtered maps →
  1 PNG (`<stem>_<ch>_phasor.png`), no `_filtered` file.
- Edge case: no `/phasor` group at all → `skipped_no_changes`,
  0 files, no exception.
- Edge case: asymmetric filtered cache (`g_filtered` present,
  `s_filtered` absent) → raw PNG only; filtered treated as absent; a
  `skipped` entry recorded for the filtered output (structured, not
  log-only); dataset still `succeeded`.
- Edge case: channel with `/phasor/<ch>` but no `/decay/<ch>` →
  unweighted raw PNG still written; `succeeded`; nothing in `errors`.
- Edge case (alignment enforcement): channel where `/decay/<ch>` sums
  to a different pixel count than `g` (stale phasor) → an `errors`
  entry with the stale-cache reason; **no PNG written for that
  channel** (not a silent unweighted render); other channels in the
  dataset still export.
- Edge case (R9): channel whose `/phasor/<ch>/g` is all-NaN/all-zero →
  PNG still written, output name appears in `rendered_empty`,
  `total_rendered_empty` reflects it; dataset `succeeded` but the empty
  signal is visible in the report.
- Error path: unreadable/missing `.h5` path → item `failed` with an
  `error` message; loop continues to the next path.
- Error path: one channel's read raises mid-dataset → that channel in
  `errors` (not `skipped`), other channels still exported, dataset
  `succeeded`.
- Error path: renderer raises for one output → `errors` entry for that
  output, the channel's other output (if any) still attempted.
- Integration: `progress_callback` invoked exactly once per input path,
  after the item result is finalized (assert call count + payload type).
- Integration: report aggregate properties
  (`total_succeeded/failed/skipped/files_written/rendered_empty`) sum
  correctly across a mixed batch (one good, one all-empty-phasor, one
  stale-misaligned, one missing file).

**Verification:** Running against a real HDF5 written via
`DatasetStore` produces the expected PNG set on disk; per-channel and
per-dataset failures are isolated and surfaced in the report;
intensity weighting path uses `decay.sum(-1)`.

---

- U3. **CLI entry point `batch_export_phasor`**

**Goal:** Headless front-end mirroring `batch_export` for the new use
case.

**Requirements:** R1, R6, R7, R8, R9

**Dependencies:** U2

**Files:**
- Create:
  `src/percell4/interfaces/cli/batch_export_phasor.py`
- Test: `tests/test_cli_batch_export_phasor.py`

**Approach:**
- Copy the shape of `interfaces/cli/batch_export.py` +
  `batch_phasor.py`: `_resolve_paths` (files + non-recursive `*.h5`
  dir glob), `_format_item_line`, `_print_item_status`, `main(argv)`
  returning the exit code.
- `_print_item_status` prints, indented under the dataset header and
  suppressed by `--quiet`, **three distinct categories** mirroring
  `batch_phasor.py`'s printer: `errors` (`    <ch> error: <msg>`),
  `skipped` (`    <ch> skipped: <reason>`), and `rendered_empty`
  (`    <output> rendered empty (no valid phasor pixels)`). The
  per-dataset summary header and final `Totals:` line always print.
- `Totals:` line includes a `rendered empty: N` count alongside
  succeeded/skipped/failed/files-written so the R9 signal is visible
  without `--verbose`.
- Args: positional `paths` (`nargs="+"`), required `--output-dir`/`-o`,
  `--quiet`, `--verbose`/`-v`. `argparse` description + epilog examples
  in the same RawDescription style, stating: one raw PNG per channel,
  one filtered PNG per channel when filtered maps exist, flat layout,
  silent overwrite, phasor-compute-is-a-separate-CLI note.
- **Up-front output-dir writability probe:** before iterating datasets,
  create `--output-dir` (parents=True) and verify it is writable
  (create + delete a temp probe file). On failure, print a clear
  stderr message and return exit `1` immediately — environmental
  write failures (missing perms, read-only mount, full disk) are not
  per-channel data faults and should fail fast rather than be absorbed
  dozens of times in the per-channel `errors` map. (Mid-batch write
  failures after the probe still route through per-channel `errors`;
  the probe catches the common up-front case.)
- `import percell4._compat` for the NumPy 2.0 shims, matching siblings.
- Exit `0` when `report.total_files_written > 0`, else `1`.
- No Qt/napari imports anywhere in the module chain (the matplotlib
  Agg backend in U1 guarantees this).

**Patterns to follow:**
- `interfaces/cli/batch_export.py` (closest sibling: required
  `--output-dir`, files-written exit-code rule).
- `interfaces/cli/batch_phasor.py` (per-sub-item skip/error indented
  printing under the dataset header).

**Test scenarios:**
- Happy path: real `.h5` with cached phasor → exit 0; stdout has
  `[succeeded] <name>`, the per-dataset summary line, and
  `Totals:`; PNG files exist on disk with expected names.
- Path resolution: pass-through `.h5` files; non-recursive directory
  glob; mixed files + directory — assert resolved order (mirror the
  three `_resolve_paths` tests in `test_cli_batch_export.py`).
- Edge case: empty directory → stderr `no .h5 files matched`, exit 1.
- Edge case: dataset with no `/phasor` → `[skipped_no_changes]`,
  `0 files written`, exit 1.
- Edge case: one missing file + one good file → exit 0 (partial
  progress), both headers printed in input order.
- `--output-dir` required: omitting it raises `SystemExit` (argparse).
- `--output-dir` short form `-o` recognized.
- `--quiet` suppresses all three indented per-channel categories
  (`error:`, `skipped:`, `rendered empty`) but keeps headers +
  `Totals:`; non-quiet prints all three. Use a dataset that produces
  one of each (a stale-misaligned channel → error, an asymmetric
  filtered cache → skipped, an all-NaN channel → rendered empty) and
  assert the distinct line prefixes appear / are suppressed.
- `Totals:` line includes the `rendered empty: N` count.
- Edge case (writability probe): `--output-dir` pointing at a
  non-writable location (e.g., a path under a read-only dir, or a file
  where a dir is expected) → clear stderr message, exit 1, **no
  dataset processed** (probe fails fast before the loop).
- `--help`: exit 0, description mentions raw + filtered PNG, flat
  layout, overwrite, and includes an `Examples:` block.
- Seam: in a fresh interpreter, `import
  percell4.interfaces.cli.batch_export_phasor` then assert
  `"PyQt5"`/`"napari"` not in `sys.modules`. Same intent as
  `test_cli_module_imports_without_qt` but a stronger assertion (that
  test does not check `sys.modules`).
- Creates a missing nested `--output-dir`.

**Verification:** End-to-end `main([...])` against real HDF5 files
under `tmp_path` writes the expected PNGs and returns the documented
exit codes; the module imports cleanly without Qt/napari; `--help`
documents the contract.

---

## System-Wide Impact

- **Interaction graph:** New, additive surface. No existing CLI, use
  case, GUI window, or `Session`/`CellDataModel` path is modified. The
  GUI phasor window is untouched — U1 only *reads* its render math as a
  spec.
- **Error propagation:** Per-channel `try/except` inside
  `_process_one_dataset`; per-dataset `try/except` inside the
  orchestrator loop; the CLI converts the report to an exit code. No
  exception escapes `main`.
- **State lifecycle risks:** None — read-only over HDF5; only new PNG
  files written. matplotlib figures explicitly closed after `savefig`
  to avoid the Agg figure-accumulation leak across a large batch.
- **API surface parity:** This *is* the parity move — it brings the
  phasor plot to the batch-CLI surface alongside `batch_export`
  (TIFF layers) and `batch_phasor` (compute). Naming, flags, exit-code
  rule, and stdout shape deliberately match `batch_export`.
- **Integration coverage:** Cross-layer behaviors unit mocks won't
  prove — real `DatasetStore` HDF5 round-trip, real matplotlib PNG on
  the filesystem, the `decay.sum(-1)` intensity path, asymmetric-cache
  handling — are covered by U2/U3 end-to-end tests against real `.h5`
  files (the established no-mock pattern in `test_cli_batch_export.py`).
- **Unchanged invariants:** `LoadCachedPhasor` and the GUI phasor
  window are unchanged. The `domain/ must not import infrastructure`
  contract stays satisfied (renderer is in `application/`, not
  `domain/`, and the "pure numpy" flim convention is preserved). The
  `application/ must not import h5py/adapters` contract is **not newly
  satisfied** — it is already failing for `batch_export_images` and
  `batch_compute_phasor` (verifiable via `lint-imports`), and this use
  case knowingly joins that set. This plan does not claim contract
  compliance for the application layer; see the Risks table.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Visual drift from the GUI — **axis swap or aspect distortion**, not just constants | U1 copies the exact GUI constants AND specifies the two dominant parity factors explicitly: `imshow(hist.T, origin="lower", …)` for orientation and `aspect="auto"` for independent axis scaling. A mandatory orientation-discriminating test (sample a known high-G/low-S cluster's rendered position) is the correctness gate — bbox/exists assertions cannot catch a scientifically-wrong G/S swap. |
| matplotlib pulling a Qt backend transitively | Force `matplotlib.use("Agg")` before any `pyplot` import; explicit `sys.modules` no-Qt/no-napari seam test in U1 and U3. |
| Figure memory growth over a large multi-dataset batch | `plt.close(fig)` after every `savefig`; assert in a multi-channel test that many renders complete without unbounded growth (smoke-level). |
| **Stale phasor silently mis-rendered** — `/phasor/<ch>` not aligned with `/decay/<ch>` on an arbitrary on-disk file (older writer, interrupted write) | `LoadCachedPhasor`'s alignment invariant is *trusted*, not enforced; the batch enforces it. U2 treats a `decay`/`g` pixel-count mismatch as a per-channel **error** ("run batch_phasor"), never a silent unweighted render. Stated as a U2 precondition + covered by an alignment-enforcement test. |
| **Application layer import-linter contract is already broken** and this adds a third violator | Accepted, not hidden: `application/ must not import h5py/adapters` already fails for `batch_export_images` and `batch_compute_phasor` (confirmed via `lint-imports`). This use case follows that established (non-compliant) precedent deliberately; the durable fix (route reads through a port / shared `Session`-free helper) is captured under Scope Boundaries → Deferred to Follow-Up Work. The plan does not assert compliance. |
| Asymmetric / partial phasor cache crashing a dataset | Replicate `load_cached_phasor.py`'s asymmetric-cache guard; record a structured `skipped` entry; per-channel isolation ensures one bad channel never aborts the dataset. |
| Output dir unwritable (no perms, read-only mount, full disk) | Up-front writability probe in U3 fails fast with a clear stderr message + exit 1 before any dataset is processed, rather than absorbing the same environmental failure into the per-channel `errors` map for every channel. Mid-batch write failures after the probe still isolate per-channel. |

---

## Documentation / Operational Notes

- Update `src/percell4/interfaces/cli/CLAUDE.md` (or the parent module
  CLAUDE.md that lists CLI entry points) to add the new
  `batch_export_phasor` command alongside `batch_export` and
  `batch_phasor`, current-state only.
- Register `percell4-batch-export-phasor` in `[project.scripts]`. This
  is required, not cosmetic: the Batch Tools Console
  (`interfaces/cli/catalog.py`) enumerates tools *exclusively* from
  `console_scripts` entry points, so an unregistered CLI is invisible
  in-app. (This plan originally said no entry was needed — true when it
  was written, superseded once the console shipped.)
- No migration, no rollout flag, no monitoring concerns (additive
  read-only CLI).

---

## Sources & References

- Pattern source: `src/percell4/interfaces/cli/batch_export.py`,
  `src/percell4/interfaces/cli/batch_phasor.py`
- Use-case pattern: `src/percell4/application/use_cases/batch_export_images.py`,
  `batch_compute_phasor.py`
- Canonical phasor read + alignment rule:
  `src/percell4/application/use_cases/load_cached_phasor.py`
- GUI render spec: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
  (`_refresh_histogram`, semicircle construction)
- Reusable validity filter:
  `src/percell4/domain/flim/phasor_display.py`
- Import-linter contracts: `pyproject.toml` `[tool.importlinter]`
- Related work: commits on `feat/cli-batch-commands`;
  `docs/plans/2026-05-18-002-feat-batch-compute-phasor-wavelet-plan.md`,
  `docs/plans/2026-05-18-003-feat-batch-export-images-plan.md`
