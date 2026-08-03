---
title: "Extending single-frame per-cell detection and classification to time-lapse (T,H,W)"
date: 2026-06-25
category: docs/solutions/architecture-patterns/
module: src/percell4/domain/measure/
problem_type: architecture_pattern
component: service_object  # closest fit; the enum is Rails-centric, this is a Python domain layer
severity: medium
applies_when:
  - "Extending a single-frame per-cell detection, classification, or segmentation feature to multi-timepoint (T,H,W) time-lapse data"
  - "Writing (T,H,W) labels, masks, or measurement stacks that must satisfy the store's leading-axis == n_timepoints contract"
  - "A per-frame step can legitimately produce zero particles and must degrade gracefully instead of aborting the whole stack"
  - "Pooling per-frame scipy.ndimage.label components into one global histogram while preserving record-to-id alignment"
  - "Adding a domain sizing constant (e.g. a LoG sizer) that changes detection behavior across all datasets"
tags:
  - time-lapse
  - rank-agnostic
  - per-cell
  - cnr-classification
  - adaptive-clip
  - typed-exception
  - global-relabeling
  - eye-validation-gate
related_components:
  - src/percell4/domain/measure/auto_extraction.py
  - src/percell4/domain/measure/cnr_classification.py
  - src/percell4/workflows/phases.py
  - src/percell4/gui/adaptive_clip_panel.py
  - src/percell4/gui/cnr_segmenter.py
---

# Extending single-frame per-cell detection and classification to time-lapse (T,H,W)

## Context

PerCell4's per-cell detection and classification math is written for one 2D frame: a raw
`(H,W)` channel, a `(H,W)` Cellpose label image, and a `(H,W)` feature mask. The core
domain functions — `adaptive_clip.detect_adaptive_per_cell`, `auto_extraction.auto_extract`,
`cnr_classification.measure_cnr` / `classify_by_cnr` / `segment_label_image` — all assume
rank 2.

A time-lapse dataset is `(T,H,W)`: the *same* analysis must run on every frame, and the
per-cell noise estimate `σ_cell = 1.4826·MAD` (`adaptive_clip.per_cell_sigma`) is
intrinsically per-frame (texture and brightness drift over time). The store enforces a hard
contract: `store.py:_validate_layer_shape` accepts a `(T,H,W)` mask/label stack only when
its leading axis equals `n_timepoints` and its trailing dims equal `native_shape` — so any
stack written back must be *exactly* `T` frames, never a frame-dropped subset.

The `(T,H,W)` store layout and the per-frame-loop precedent were established earlier by the
multi-timepoint feature-parity work (grouped thresholding was fixed to loop per frame and
stack masks into `(T,H,W)`; `read_array_frame` / per-timepoint reads already existed)
*(session history)*. Guided CNR and two-pass auto-extraction were then ported into the
headless workflow as **single-timepoint only**, with a deliberate single-timepoint abort
(`R8`) left in `phases.py` as a clean degradation point for this follow-up to lift
*(session history)*. This doc is the canonical reference for extending any single-frame
per-cell routine to `(T,H,W)` without rewriting the 2D math.

## Guidance

**1. Keep the domain math rank-agnostic; orchestrate per-frame in the caller.** Do not
teach the 2D detector/classifier about time. Add a `*_stack` wrapper (in the workflow phase,
the GUI worker, or alongside the 2D domain function) that loops `for t in range(T)` and
calls the unchanged 2D function on `image[t] / mask[t] / labels[t]`.
`cnr_classification.classify_by_cnr_stack` is the canonical shape: it calls
`classify_by_cnr(image[t], fmask[t], labels[t], …)` in a loop and stacks the results.

Some functions are *already* rank-agnostic because they index per-element rather than
slicing spatially. `segment_label_image` builds a per-component lookup `seg_of[comp]` and
fancy-indexes it back over the component image — that one fancy-index works identically
whether `comp` is `(H,W)` or `(T,H,W)`. This is why `CnrSegmenterWindow` (the interactive
"Segment by CNR" tool) needed **zero** edits for time-lapse: a `(T,H,W)` `component_labels`
flows through `segment_label_image` / `segment_masks_from_label_image` / napari
`add_labels` / `add_mask` unchanged.

**2. Pool-with-global-relabel only when one shared cross-frame artifact is required.** The
interactive segmenter shows a *single* histogram, so its foci must be pooled across all
frames into one record list. `adaptive_clip_panel.run_cnr_measure_stack` does this: it runs
`measure_cnr` + `scipy.ndimage.label` per frame, then offsets each frame's component ids
(`comp_t[comp_t > 0] += offset`) *and* the matching `record["label"]` by the same running
`offset`, so ids are globally unique across the `(T,H,W)` component image while the
record↔id alignment the window relies on is preserved. The two `label()` calls (inside
`measure_cnr` and in the wrapper) must use identical connectivity, or the offset silently
misaligns records to components.

**3. Apply one shared threshold per frame — do not re-derive it per frame.** When
populations are defined globally, the threshold is a single number passed to every frame:
`classify_by_cnr_stack(mode="guided", threshold=…)` forwards the *same* `threshold` into
every `classify_by_cnr` call; the interactive segmenter's dividers are one global list
applied via `assign_segments` to the pooled CNR. Caveat worth stating in the run-log/docs: a
fixed CNR number is a fixed *value* but not a fixed statistical *stringency* across frames,
because `σ_cell` is measured per frame — expected, not a bug.

**4. Respect the `(T,H,W)` store contract: emit exactly `T` planes.** Population/segment
stacks must have `T` frames; an empty or single-population frame is an all-zero plane, never
dropped. `classify_by_cnr_stack` builds `low_stack`/`high_stack` with
`np.stack(low_frames, axis=0)` over every `t`; the `phases.py` time-lapse branch appends
`np.zeros(labels.shape)` for a frame with no groups or no particles rather than skipping.
`store.write_mask` then validates `array.shape[0] == n_timepoints`
(`_validate_layer_shape`). napari `add_labels`/`add_mask` are rank-agnostic; a `(T,H,W)`
preview/overlay layer dim-aligns to the viewer's time slider by trailing axes (so dragging
recolors the displayed frame and the slider scrubs the preview).

**5. Distinguish recoverable from fatal per-frame degradation with a typed exception — never
a substring match.** `auto_extraction.NoParticlesFound(ValueError)` is raised when
smallest-particle autodetection finds no LoG blobs. The per-frame loop catches *that type*
and turns the frame into an empty plane (a dissolved-granule washout endpoint), while
genuine errors still abort the dataset: `adaptive_clip_panel.run_adaptive_auto_extract_stack`
does `except NoParticlesFound: mask_t = zeros`; `phases.py:_apply_auto_extract_cells` returns
the `_AUTO_EXTRACT_NO_PARTICLES` sentinel which the time-lapse loop treats as an empty frame
but the single-timepoint caller surfaces as a clean failure. Subclassing `ValueError` keeps
existing `except ValueError` handlers working. This replaced a fragile `"no blobs" in str(e)`
match that coupled multiple catch sites to a message string (caught in review).

**6. Give a non-splitting frame explicit "unclassified" semantics.** A frame that does not
split (fewer than `MIN_COMPONENTS`/4 foci, or smaller group below `MIN_FRACTION`) puts *all*
its foci in `_low`, flagged `n_subpopulations == 1` on the per-focus table and the
`per_frame` summary. `_low` on such a frame means "unclassified for that frame", **not**
"dim" — without the flag a per-population time-course would be silently contaminated by
frames that never split. Population stacks are only written when at least one frame splits
(matching the single-tp "single population → base mask stands" rule).

**7. Eye-validate domain constants that affect every dataset; pin a regression test.**
`auto_extraction.SIZE_NUM_SIGMA = 30` ships a defensible default but its final value is tuned
by eye on real data — the eye is ground truth, not any mask or score (auto memory [claude]:
the per-cell ALC engine is itself eye-validated — per-cell `σ = 1.4826·MAD`, `k=1`, presmooth
1px). This standard is hard-won: an 11-agent quantitative sweep once declared one local
threshold the "winner" over Otsu on pixel-precision scores, which the user rejected as
impossible and disproved by eye; scoring against hand-drawn ROI masks was explicitly
abandoned *(session history)*. Guard against gross drift with a pinned-tolerance test. The
diagnostic insight that produced `SIZE_NUM_SIGMA`: a *constant-looking* per-frame output
("largest = 17.5px for every timepoint") looked like accidental per-frame size-reuse but was
actually LoG-sizer **quantization** — the old 12-scale grid (`linspace(1, MAX_SIGMA, 12)`,
diameter `2√2·σ`) snapped every frame's p99 largest to the same ~5px bin. Proven *not* to be
size-reuse because the per-frame `k` still varied (e.g. 6.5→4.75). The fix was finer sizing
resolution (`SIZE_NUM_SIGMA` 12→30, ~1.85px steps), **not** a per-frame loop — that loop
already existed.

## Why This Matters

The 2D math is the eye-validated, tested core (per-cell σ, CNR geometry, the dip-test gap
logic). Keeping it rank-agnostic means time-lapse support costs a thin loop, not a fork — and
the 2D and `(T,H,W)` paths cannot drift because they call the *same* function.
Pooling-with-relabel, the typed-exception branch, and exact-`T` emission each exist to satisfy
one downstream invariant (one histogram, recoverable-vs-fatal, the store leading-axis check);
collapsing any of them produces a misaligned histogram, a dataset that aborts on a legitimate
washout endpoint, or a `LayerSizeMismatchError` on write. The quantization story is the
cautionary tale: a per-frame output that *looks* reused may be a resolution artifact in a
shared sizer — diagnose the constant (does `k` vary? then frames are independent) before
adding a loop that already exists.

## When to Apply

- Extending any per-cell 2D detection/classification/segmentation to a `(T,H,W)` time-lapse
  channel.
- Adding a GUI worker that must run an existing 2D domain routine over a time stack (mirror
  `run_*_stack` in `adaptive_clip_panel.py`, which itself mirrors
  `segmentation_panel.run_cellpose_stack`).
- Any per-frame loop where some frames legitimately produce no result (washout, bleaching) —
  reach for a typed exception + sentinel, not a message match.
- Introducing or tuning a domain constant whose value affects every dataset and every frame
  (LoG grid resolution, fill factors, percentiles).

Do **not** pool across frames unless a genuinely shared artifact requires it (one histogram,
one global threshold); independent per-frame outputs should stay independent planes.

## Examples

**Global-relabel offset loop (pooling for one shared histogram)** —
`adaptive_clip_panel.py:run_cnr_measure_stack`:

```python
offset = 0
for t in range(image.shape[0]):
    recs = measure_cnr(image[t], feature_mask[t], labels[t])
    comp_t, n_t = label(feature_mask[t] > 0)      # SAME connectivity as measure_cnr
    comp_t[comp_t > 0] += offset                  # globally-unique component ids
    for r in recs:
        r = dict(r); r["timepoint"] = int(t)
        if int(r.get("label", 0)) > 0:
            r["label"] = int(r["label"]) + offset  # keep record<->id alignment
        records.append(r)
    comp_frames.append(comp_t)
    offset += int(n_t)
component_labels = np.stack(comp_frames, axis=0)   # (T,H,W) for the shape-agnostic window
```

**Rank-agnostic indexing — `segment_label_image` unchanged on `(T,H,W)`**
(`cnr_classification.py`). `comp` may be `(H,W)` or `(T,H,W)`; the lookup-and-fancy-index is
identical, which is why `CnrSegmenterWindow` needed no edits:

```python
seg_of = np.zeros(int(comp.max()) + 1, dtype=np.int32)
seg = assign_segments(focus_cnr, dividers)
seg_of[fl[valid]] = seg[valid]
return seg_of[comp].astype(np.int32)   # works element-wise on any rank
```

**Typed exception vs. substring match (recoverable per-frame degradation)** —
`run_adaptive_auto_extract_stack` turns the typed `NoParticlesFound` into an empty plane
while real errors propagate:

```python
try:
    mask_t, report_t = auto_extract(image[t], labels[t], smallest_particle_px=..., ...)
except NoParticlesFound:                       # NOT: if "no blobs" in str(e)
    mask_t = np.zeros(labels[t].shape, dtype=np.uint8)   # washout endpoint, not a failure
    report_t = None
frames.append(mask_t)                          # exactly T planes -> store.write_mask validates
```

## Related

- `docs/solutions/architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md`
  — the canonical **single-frame** method-addition pattern (sentinel wiring, the
  shared-default trap, headless-not-QC routing). This doc is its **time-lapse companion**:
  same code area, distinct pattern (per-frame orchestration, not method wiring). Cross-read
  both when touching the thresholding workflow.
- `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md` — the
  band-pass + per-cell z-score model; time-lapse inherits the physical-unit-driven `window`
  / `k` / presmooth rules and applies them per frame (`σ_cell` is per-frame, so a fixed `k`
  reads as drifting stringency — expected).
- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` —
  the napari Creator sequence the panel save paths follow; confirm `(T,H,W)` layer add.
- `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` — the
  per-focus CNR table lives at `/classification/<round>` (with a `timepoint` column for
  time-lapse), never `/measurements`.
- Code: `src/percell4/domain/measure/cnr_classification.py` (`classify_by_cnr_stack`,
  `StackClassificationResult`, `segment_label_image`, `assign_segments`),
  `src/percell4/domain/measure/auto_extraction.py` (`NoParticlesFound`, `SIZE_NUM_SIGMA`,
  `measure_largest_particle_diameter`, `_log_diameters` — the quantization site),
  `src/percell4/workflows/phases.py` (`_classify_and_write_cnr_stack`,
  `_AUTO_EXTRACT_NO_PARTICLES`, the `n_timepoints > 1` branch),
  `src/percell4/gui/adaptive_clip_panel.py` (`run_*_stack` workers, `_resolve_cnr_inputs`),
  `src/percell4/gui/cnr_segmenter.py` (the shape-agnostic consumer),
  `src/percell4/store.py` (`_validate_layer_shape`).
- Lineage plans: `docs/plans/2026-06-24-001-feat-alc-auto-extract-cnr-workflow-plan.md`
  (single-timepoint origin) and `docs/plans/2026-06-25-001-feat-alc-multitimepoint-autoextract-cnr-plan.md`
  (this feature).
