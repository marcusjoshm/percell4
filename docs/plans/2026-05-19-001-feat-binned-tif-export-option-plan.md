---
title: "feat: Optionally export TIFFs at session.active_bin"
type: feat
status: active
date: 2026-05-19
---

# feat: Optionally export TIFFs at session.active_bin

## Overview

Today, both the **Export Images** GUI dialog and the `percell4-batch-export`
CLI write TIFF files at the dataset's native resolution regardless of
`session.active_bin`. This is the right default — TIFFs should preserve
the dataset's full information by default — but users running a dataset
under view-bin>1 sometimes want to export at the same downsampled
resolution they're seeing in napari (e.g. for sharing a low-res working
view, or for downstream tools that expect the binned shape).

This plan adds an opt-in: a checkbox in the **Export Images** dialog
labelled with the current active bin, and a `--view-bin N` flag on the
batch CLI. Both surfaces default to native (no behavior change for
existing users).

---

## Problem Frame

When a dataset has been opened with `session.active_bin > 1`, every
read in the GUI flows through the bin lens (`read_array(view_bin=k)`,
`read_labels(view_bin=k)`, etc.) and the user sees downsampled images
in napari. But the TIFF exporter unconditionally calls `view_bin=1`
(see `src/percell4/application/use_cases/export_images.py:49,61,68`
and `src/percell4/application/use_cases/batch_export_images.py:203-204`:
`# Read intensity shape via the store (read-only, no view_bin needed
for the batch -- export always at native).`).

That divergence is intentional — TIFFs are an archive, not a view —
but it also means a user who has chosen a binning view has no way to
export at that view without manually re-binning after the fact. The
ask is a single opt-in toggle that respects the existing "default to
native" contract while letting the user override per export run.

---

## Requirements Trace

- R1. The GUI Export Images dialog gains a checkbox labelled with the
  current `session.active_bin` value (e.g. "Apply current view bin
  (k=4) to exports") that opts in to bin-aware export.
- R2. When the checkbox is checked, intensity / labels / masks TIFFs
  are written at `session.active_bin` resolution (delegating to the
  established `view_bin` parameter on the repository reads). When
  unchecked, all exports remain at native (R-default).
- R3. The label updates live if `session.active_bin` changes while the
  dialog is open (e.g. user changes the SessionWindow spinbox).
- R4. When `session.active_bin == 1`, the checkbox is disabled (or
  visibly explained as a no-op) so the user doesn't think they checked
  something that did nothing.
- R5. The batch CLI gains an optional `--view-bin N` flag (positive
  integer, default 1) that threads through to the same read path.
- R6. Both surfaces preserve the existing filename pattern
  `<dataset_stem>_<layer>.tif`. The bin factor is NOT encoded in the
  filename — the user opts in knowingly per export run and is
  responsible for tracking the bin.
- R7. Zero behavior change for existing callers: every test that
  doesn't explicitly opt in must still pass unchanged.

---

## Scope Boundaries

- No per-layer bin choice — one bin factor applies to all exported
  channels, labels, and masks in a given run.
- No per-channel TIFF metadata describing the bin factor. (Sidecar
  metadata could be a future iteration.)
- No automatic bin reconciliation between dialog open and Export
  click — the export uses whatever `session.active_bin` is at the
  moment Export is clicked.
- The bin factor is NOT encoded in output filenames.
- No new spinbox in the dialog; the bin value comes from the session,
  not a dialog-local widget.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/application/use_cases/export_images.py`
  — `ExportRequest` dataclass + `ExportImages.execute`; reads via
  `self._repo.read_array("intensity")`, `read_labels(name)`,
  `read_mask(name)` (all with implicit `view_bin=1` default).
- `src/percell4/application/use_cases/batch_export_images.py:200-238`
  — wraps `ExportImages` per dataset; intensity-shape probe at
  `:207-210` reads h5py directly but is bin-independent (only uses
  the channel-axis dimension).
- `src/percell4/adapters/hdf5_store.py:103,124,143,182`
  — `Hdf5DatasetRepository.read_array / read_labels / read_mask` all
  accept `view_bin: int = 1` and forward to the store's view-bin
  dispatch. No new repo surface required.
- `src/percell4/gui/export_images_dialog.py`
  — picker dialog; `_build_ui` lays out three QCheckBox groups
  (channels / labels / masks) plus output-folder browse + Export
  button.
- `src/percell4/interfaces/gui/main_window.py:1400-1450`
  — `_on_export_images` is the dialog caller; constructs the
  `ExportRequest` and calls `ExportImages.execute`.
- `src/percell4/interfaces/cli/batch_export.py:79-168`
  — argparse-driven CLI; established pattern for adding flags
  (see existing `--quiet`, `--verbose`, `--output-dir`).
- `src/percell4/application/session.py`
  — `Event.ACTIVE_BIN_CHANGED` event for the live-label requirement
  in R3; `session.active_bin` property; mutated only via
  `session.set_active_bin(k)`.

### Institutional Learnings

- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`
  — caller-side `view_bin` forwarding parity. When a new read path
  is parameterized on `view_bin`, every caller must forward the
  session value; emit-side parity (the read accepts the kwarg) is
  necessary-but-not-sufficient. Applies directly here: the export use
  case must accept `view_bin` AND every caller (GUI + batch + CLI)
  must thread it through.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  — Vector 4: every code path that mutates the primary input must
  apply the same view_bin invalidation. In this case, the export use
  case has no caches, so Vector 4 reduces to "always read with the
  passed view_bin, never hardcode 1". The test scenarios pin this.

### External References

None — this is mechanical caller-side parameter threading inside an
established pattern. The repo already has six other read sites
forwarding `view_bin`; this plan adds three more (intensity, labels,
masks export) plus a CLI flag.

---

## Key Technical Decisions

- **`view_bin` lives on `ExportRequest`, not as a separate `execute`
  arg.** Keeps the use-case signature stable (`execute(handle,
  request)`) and lets the batch wrapper carry the value per dataset.
  Mirrors how `channels`, `labels`, `masks` are already bundled.
- **Default `view_bin = 1` on `ExportRequest`** so every existing
  caller compiles and behaves unchanged. The R7 zero-regression
  invariant is structural, not test-only.
- **GUI dialog reads `session.active_bin` at Export-click time, not
  at dialog-construct time.** Matches the existing
  "active_bin is live" semantic from
  `phasor-view-bin-not-forwarded-from-gui-callers` — the user's
  intent is "use whatever bin I'm currently viewing".
- **Checkbox is disabled when `session.active_bin == 1`** rather than
  hidden, so the user can SEE the option exists and learn why it's
  inert. The label still shows "(k=1)" in that state.
- **Live label refresh via `Event.ACTIVE_BIN_CHANGED`**. The dialog
  subscribes for the duration of its lifetime and unsubscribes in
  `closeEvent` / `accept` / `reject`. Pattern mirrors the
  SessionWindow read-out subscription.
- **CLI flag is `--view-bin N` (positive int, default 1).** Argparse
  type=int + a custom validator that rejects N<1. No short form;
  the GUI is the primary surface and the flag is rarely used.
- **Filename pattern unchanged.** Encoding `_binN` in filenames would
  make script-driven downstream pipelines fragile against the flag's
  absence/presence. Users who want bin-tagged filenames can pass
  `--output-dir <name>_binN/` themselves.

---

## Open Questions

### Resolved During Planning

- **Where does the bin value come from when the checkbox is checked?**
  → `session.active_bin` at Export-click time. No dialog-local
  spinbox; one source of truth.
- **What surfaces get this?** → Both GUI dialog and batch CLI per
  the answered clarifying question.
- **Is the bin factor encoded in TIFF filenames?** → No (see Key
  Technical Decisions).
- **Does this change how `_enumerate_channels` probes shape?** → No.
  The probe uses `len(shape)` and the leading dim, both of which are
  bin-independent. Only the read inside `ExportImages.execute` needs
  the `view_bin` value.

### Deferred to Implementation

- **Checkbox precise wording and styling.** "Apply current view bin
  (k=4) to exports" reads cleanly when k>1; when k=1 the parenthetical
  label and disabled state need to compose without looking broken
  ("Apply current view bin (k=1) to exports — no effect at native").
  Implementer to pick the exact disabled-state copy.
- **TIFF dtype after binning.** `read_array(view_bin=k)` for intensity
  goes through `sum_bin_2d` which preserves the input dtype; labels
  go through `mode_labels` (int32); masks go through
  `majority_vote_mask` (uint8). All three are already established and
  `tifffile.imwrite` handles them. If any dtype edge case surfaces
  (e.g. uint16 overflow when summing a 4× region of bright pixels),
  flag it in implementation rather than pre-solving.

---

## Implementation Units

- U1. **Thread `view_bin` through `ExportRequest` + `ExportImages.execute`**

**Goal:** Add `view_bin: int = 1` to `ExportRequest` and forward it
to every repository read inside `ExportImages.execute`. Default
unchanged behavior for all existing callers.

**Requirements:** R2, R7.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/application/use_cases/export_images.py`
- Test: `tests/test_application/test_export_images_view_bin.py`

**Approach:**
- Add `view_bin: int = 1` as the last field of the `ExportRequest`
  dataclass (defaulted so existing constructions compile unchanged).
- In `ExportImages.execute`, pass `view_bin=request.view_bin` to all
  three `self._repo.read_*` calls.
- No changes to `ExportResult` or to the per-file write loop —
  `tifffile.imwrite` already handles whatever shape and dtype the
  read returns.

**Execution note:** Test-first. The change is one parameter wired
through three callsites; tests should pin the forwarding contract
before the implementation lands.

**Patterns to follow:**
- `src/percell4/application/use_cases/load_cached_phasor.py` —
  established `view_bin: int = 1` use-case parameter from the
  Cycle-2 fix; mirror its plumbing.
- `src/percell4/application/use_cases/measure_cells.py` and other
  view_bin-aware use cases for parameter naming and defaulting.

**Test scenarios:**
- Happy path: `ExportRequest(..., view_bin=1)` (default) on a `(2,32,32)`
  intensity produces two 32×32 TIFFs.
- Happy path: `ExportRequest(..., view_bin=2)` on the same data
  produces two 16×16 TIFFs (intensity, sum-binned).
- Happy path: `view_bin=2` on a labels layer produces a 16×16 TIFF
  whose dtype is the mode_labels result (int32 or similar).
- Happy path: `view_bin=2` on a mask layer produces a 16×16 uint8
  TIFF (majority-vote).
- Edge case: `view_bin=1` (default) with all three layer types
  produces TIFFs whose shape equals the native shape. Pinned to
  guard against silent regression of the R7 default.
- Edge case: `view_bin=4` on a 32×32 intensity produces 8×8 TIFFs.
- Integration: real `Hdf5DatasetRepository` over `tmp_path`; the
  on-disk TIFF shape matches `repo.read_array(handle, "intensity",
  view_bin=k).shape` exactly.

**Verification:** Tests pass. The grep `grep -n "read_array\|read_labels\|read_mask" src/percell4/application/use_cases/export_images.py` shows all three calls forwarding `view_bin=request.view_bin`. Existing `tests/test_application/test_export_images.py` continues to pass without modification.

---

- U2. **Wire the GUI dialog checkbox + launcher caller**

**Goal:** Add the "Apply current view bin" checkbox to the export
dialog and thread the resolved bin value through `_on_export_images`
into `ExportRequest`. Live label refresh via `ACTIVE_BIN_CHANGED`.

**Requirements:** R1, R2, R3, R4.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/gui/export_images_dialog.py`
- Modify: `src/percell4/interfaces/gui/main_window.py` (the
  `_on_export_images` handler at `:1400-1450`)
- Test: `tests/test_gui/test_export_images_dialog_view_bin.py`

**Approach:**
- `ExportImagesDialog.__init__` accepts the current `active_bin`
  value (caller passes `session.active_bin`) and a callable to
  subscribe to future changes (mirrors the
  `get_active_seg_labels` callback pattern from
  `DilutePhaseMaskPanel`). Alternative: pass the session and let
  the dialog subscribe directly via `session.subscribe(
  Event.ACTIVE_BIN_CHANGED, ...)` — implementer's call between
  the two. Either way, the dialog must NOT cache the bin at
  construction time.
- Add a `QCheckBox` labelled "Apply current view bin (k=N) to exports"
  to `_build_ui`, positioned above the Export button. Initial state:
  unchecked. When `session.active_bin == 1`, the checkbox is
  disabled and the label includes "— no effect at native".
- Wire a slot to `Event.ACTIVE_BIN_CHANGED` that updates the
  checkbox label and enabled state. Unsubscribe on close/accept/reject.
- Expose a public method `selected_view_bin() -> int` that returns
  `active_bin` when the box is checked, else `1`.
- In `main_window._on_export_images`:
  - Pass `session.active_bin` (or the session itself) into the dialog
    constructor.
  - After `dlg.exec_()`, read `dlg.selected_view_bin()` and set it
    on the `ExportRequest`.
- Status-bar message after a successful bin-aware export should
  include the bin (e.g. "Exported 3 image(s) at k=4 to /path") so
  the user sees confirmation.

**Patterns to follow:**
- `src/percell4/interfaces/gui/peer_views/session_window.py` —
  established `session.subscribe(Event.ACTIVE_*_CHANGED, ...)` and
  `_unsubs` teardown pattern.
- `src/percell4/gui/workflows/dilute_phase/panel.py:_subscribe_session_events` —
  recent precedent for the subscribe-on-construct / unsubscribe-on-close
  shape on a dialog/panel.

**Test scenarios:**
- Happy path: open dialog with `active_bin=4`; checkbox label reads
  "Apply current view bin (k=4) to exports"; checkbox enabled.
- Happy path: checkbox unchecked → `selected_view_bin()` returns 1.
- Happy path: checkbox checked → `selected_view_bin()` returns 4.
- Happy path: open dialog with `active_bin=1`; checkbox disabled,
  label contains "no effect at native"; `selected_view_bin()` returns
  1 regardless of check state.
- Edge case: open dialog with `active_bin=2`, check the box, change
  `session.active_bin` to 4 via `session.set_active_bin(4)`, click
  Export. `selected_view_bin()` returns 4 (live, not cached).
- Edge case: open dialog with `active_bin=2`, check the box, change
  `session.active_bin` to 1 via the session; the checkbox is
  auto-disabled and the label updates to k=1.
- Integration: end-to-end through `_on_export_images` —
  monkeypatched `ExportImages.execute` records the `view_bin` field
  on the `ExportRequest`; checked dialog with `active_bin=4` →
  `ExportRequest.view_bin == 4`; unchecked → `view_bin == 1`.

**Verification:** Tests pass. The dialog renders without errors;
the label updates live when `session.active_bin` changes.

---

- U3. **Add `view_bin` to the batch export use case**

**Goal:** `batch_export_images(...)` accepts an optional `view_bin:
int = 1` kwarg and passes it into every per-dataset `ExportRequest`.
No filename change.

**Requirements:** R2, R5, R7.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/application/use_cases/batch_export_images.py`
- Test: `tests/test_application/test_batch_export_images.py`
  (extend existing file with new scenarios)

**Approach:**
- Add `view_bin: int = 1` as a keyword-only parameter on the
  `batch_export_images` orchestrator (consistent with the existing
  `output_dir`, `overwrite`, `progress_callback` kwargs).
- Pass it into the `ExportRequest(...)` construction at line ~229.
- Update the existing intensity-shape probe comment at `:203-204`
  to clarify that the probe stays bin-independent (only counts
  channels), while the export itself now honors the requested bin.

**Patterns to follow:**
- The existing `batch_compute_phasor.py` (Cycle-2 batch CLI feature)
  for how a per-dataset use case wrapper forwards a bin/filter
  parameter consistently across all datasets.

**Test scenarios:**
- Happy path: `batch_export_images(paths, output_dir=out)` (default)
  produces TIFFs at native (current behavior unchanged).
- Happy path: `batch_export_images(paths, output_dir=out,
  view_bin=2)` on two datasets each with `(2,32,32)` intensity
  produces 4 TIFFs of shape `(16,16)` each.
- Edge case: `view_bin=2` with one dataset having `(2,32,32)` and
  another `(2,30,30)` produces correctly-shaped outputs per dataset
  (shape doesn't have to divide evenly — the binning kernel handles
  remainder per the existing `view_bin.py` contract).
- Error path: one dataset fails to read at `view_bin=2`; the other
  datasets in the batch still succeed (per-dataset error isolation
  is unchanged).

**Verification:** Tests pass. All 13 pre-existing
`test_batch_export_images.py` scenarios still pass (R7 zero-regression).

---

- U4. **Add `--view-bin N` flag to the batch CLI**

**Goal:** `percell4-batch-export ... --view-bin 4` produces TIFFs
at view_bin=4. Default behavior (no flag) writes at native, matching
today.

**Requirements:** R5, R6, R7.

**Dependencies:** U3.

**Files:**
- Modify: `src/percell4/interfaces/cli/batch_export.py`
- Test: `tests/test_cli_batch_export.py` (extend existing file)

**Approach:**
- Add a `parser.add_argument("--view-bin", type=int, default=1, ...)`
  declaration alongside the existing flags. Validate `>= 1` via a
  small custom `type=` callable that raises `argparse.ArgumentTypeError`
  on N<1.
- Pass `view_bin=args.view_bin` into `batch_export_images(...)`.
- Update the help text: short description plus a one-line note in
  the epilog explaining "Default: 1 (native); pass to downsample
  exports to match a GUI view-bin setting." Mention that no filename
  changes — the user is responsible for tracking the bin.

**Patterns to follow:**
- The existing `--filter-level` flag on
  `src/percell4/interfaces/cli/batch_phasor.py` (Cycle-2 feature) —
  same pattern: argparse type=int with a positive-int validator,
  threaded through to a use-case kwarg.

**Test scenarios:**
- Happy path: `cli.main([str(h5), "-o", str(out)])` with no
  `--view-bin` → `batch_export_images` is called with `view_bin=1`
  (default; all 13 existing scenarios pass unchanged).
- Happy path: `cli.main([str(h5), "-o", str(out), "--view-bin", "2"])`
  → `batch_export_images` is called with `view_bin=2`. On-disk TIFF
  shape is half the native dim.
- Error path: `cli.main([str(h5), "-o", str(out), "--view-bin", "0"])`
  exits with non-zero status; stderr includes the argparse error.
- Error path: `cli.main([str(h5), "-o", str(out), "--view-bin",
  "-1"])` exits non-zero similarly.
- Edge case: `cli.main([str(h5), "-o", str(out), "--view-bin", "1"])`
  is a no-op equivalent to omitting the flag — TIFFs are at native.
- Help: `cli.main(["--help"])` exits 0 and stdout mentions
  `--view-bin` with its default.

**Verification:** Tests pass. The CLI binary
`percell4-batch-export --view-bin 2 --output-dir out/ *.h5`
produces correctly-binned TIFFs end-to-end on a real `tmp_path`
dataset.

---

## System-Wide Impact

- **Interaction graph:** GUI dialog → `_on_export_images` →
  `ExportImages.execute` → `Hdf5DatasetRepository.read_array /
  read_labels / read_mask` (with `view_bin`). Batch CLI → `main` →
  `batch_export_images` → same `ExportImages.execute` per dataset.
  Two new caller-side forwardings; one new use-case parameter.
- **Error propagation:** `view_bin < 1` rejected at CLI argparse
  layer. `view_bin > native_shape // 2` (or similar edge case) is
  the repo's existing concern, not this plan's — `read_array`
  already validates against `view_bin > native_shape // 2` per the
  Cycle-1 binning contract and surfaces a clean error.
- **State lifecycle risks:** None new. The GUI dialog is modal and
  uses `session.active_bin` live; no caching means no staleness.
- **API surface parity:** `ExportRequest` gains one default kwarg
  (additive, non-breaking). `batch_export_images` gains one
  keyword-only kwarg (additive, non-breaking). CLI gains one
  optional flag (additive, non-breaking).
- **Integration coverage:** U1's repo-backed integration test and
  U2's monkeypatched-execute test together prove the GUI →
  use-case → repo chain. U3's per-dataset shape assertion and U4's
  end-to-end CLI test prove the batch path.
- **Unchanged invariants:**
  - Native-default behavior of both export surfaces (R7 — pinned
    by every existing test plus the U1/U3 default-path scenarios).
  - TIFF filename pattern `<dataset_stem>_<layer>.tif` (R6).
  - `/intensity`, `/labels`, `/masks` storage shape (native) —
    binning is read-time only.
  - `session.active_bin` mutator remains `session.set_active_bin`
    only; the dialog reads but does not write.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| User checks the box, exports, later forgets the TIFFs are at k=4, and re-imports them as native. | Status-bar message after export includes "at k=N"; same string is also printed by the CLI. Filename-bin-tag rejection is a deliberate trade-off — would be additive in a follow-up. |
| `view_bin > native // 2` edge case raises in the middle of a batch run. | Per-dataset error isolation in `batch_export_images` already catches it (existing `test_nonexistent_h5_isolates_as_failed` proves the pattern); add one scenario for the bin-too-large case. |
| Dialog subscription leaks if a future change moves the dialog construction off the main path. | The unsubscribe path is wired in `closeEvent` and the existing `dlg.deleteLater()` in `main_window._on_export_images:1417`. U2 test pins the subscribe + unsubscribe round-trip. |
| A future change adds a fourth layer type (e.g. phasor maps) to the export path and forgets to forward `view_bin`. | The `phasor-view-bin-not-forwarded-from-gui-callers` learning surfaces this exact class. The export use case's tests should make adding a new layer require adding a new `view_bin` assertion. |

---

## Documentation / Operational Notes

- Update the `percell4-batch-export --help` epilog with one
  Examples line: `percell4-batch-export *.h5 --output-dir out/
  --view-bin 4`.
- No README/docs change beyond CLI help; the feature is discoverable
  via the dialog checkbox.

---

## Sources & References

- Existing export use case: `src/percell4/application/use_cases/export_images.py`
- Existing batch wrapper: `src/percell4/application/use_cases/batch_export_images.py`
- Existing CLI: `src/percell4/interfaces/cli/batch_export.py`
- Existing dialog: `src/percell4/gui/export_images_dialog.py`
- Repo read sites: `src/percell4/adapters/hdf5_store.py:103,124,143,182`
- Session bin source: `src/percell4/application/session.py`
- Caller-side forwarding learning: `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md`
