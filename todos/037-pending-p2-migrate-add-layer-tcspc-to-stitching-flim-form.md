# Migrate AddLayerDialog's TCSPC tab to use StitchingFlimForm

**Status:** pending
**Priority:** P2
**Filed:** 2026-05-12 (PR #9 review)

## Context

`feat/batch-tcspc-append` (PR #9) extracted the canonical TCSPC stitching /
rotate-flip / raw-`.bin` geometry widget set into
`src/percell4/gui/_stitching_flim_form.py::StitchingFlimForm`. The batch
dialog (`src/percell4/gui/batch_tcspc_dialog.py`) consumes it.

`src/percell4/gui/add_layer_dialog.py`'s TCSPC tab still owns the original
inline widget construction at lines 845-975 (stitching + rotation/flip +
FLIM `.bin` parameters). Until those are migrated, any future fix to the
widget set has to be applied twice — exactly the drift class that
triggered every regression in PR #9.

## Acceptance criteria

1. `AddLayerDialog` instantiates `StitchingFlimForm` and reads its
   `tile_config()`, `rotation_k()`, `flip_axis()`, and the bin-geometry
   half of `flim_config(...)`.
2. Existing widget attributes the dialog code reads from elsewhere
   (`self._tcspc_stitch_rows`, `self._tcspc_bin_dtype`, etc.) either stay
   as thin properties pointing at the shared form's widgets, or every
   call site is migrated to the new accessors.
3. The dialog's `_tcspc_stitching_user_edited` flag continues to work
   (it gates whether re-Scan recomputes default stitching from the
   dataset's TIFF compress config). Wire it up via the shared form's
   `changed` signal.
4. The dialog's own `flim_freq` spinbox + per-channel-calibration
   sub-widget stay separate — they're not part of the shared form by
   design (`BatchTCSPCDialog` reads frequency + calibration from the
   CSV, not from inline widgets).
5. All existing `tests/test_gui/test_add_layer_*` tests pass.
6. The shared form is now used by both dialogs, so any future fix to
   the widget set is propagated automatically.

## Out of scope

- Refactoring the per-channel calibration UI (it's add_layer-specific by
  design).
- Changing `AddLayerDialog`'s scan / matching / replace-checkbox logic.

## Risk

`AddLayerDialog` is 2117 lines with deep coupling to the TCSPC table,
scan flow, and replace-state machine. The widget attribute names are
referenced from dozens of call sites in the same file. A find-and-replace
migration with focused tests is safer than a structural rewrite.
