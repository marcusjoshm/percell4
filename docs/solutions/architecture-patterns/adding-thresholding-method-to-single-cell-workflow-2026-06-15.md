---
title: "Adding a thresholding method to the single-cell workflow (and the shared-GUI-default trap)"
date: 2026-06-15
category: architecture-patterns
module: src/percell4/workflows
problem_type: architecture_pattern
component: development_workflow
severity: high
related_components:
  - "src/percell4/gui/workflows/single_cell"
  - "src/percell4/interfaces/cli"
root_cause: config_error
resolution_type: code_fix
applies_when:
  - "Adding a new thresholding/detection method to the single-cell thresholding workflow"
  - "Extending ThresholdingRound with a new mutually-exclusive method sentinel"
  - "A new per-cell method needs a parameter that overlaps a shared GUI rounds-table column"
  - "Introducing a per-cell or physical-unit (µm) method into a per-group pipeline"
symptoms:
  - "GUI adaptive round silently produced an EMPTY MASK on real noisy data, reported as success"
  - "Synthetic clean-signal tests passed while the real-data detection path collapsed"
tags:
  - thresholding
  - adaptive-clip
  - sentinel-pattern
  - frozen-dataclass
  - per-cell-detection
  - shared-default-trap
  - validated-default
  - pixel-size
---

# Adding a thresholding method to the single-cell workflow (and the shared-GUI-default trap)

## Context

The single-cell thresholding workflow runs an ordered list of `ThresholdingRound`s.
Each round picks a strategy through one of several **mutually-exclusive sentinel
fields** on `ThresholdingRound` (`src/percell4/workflows/models.py`): `puncta`,
`iterative_otsu`, and now `adaptive_clip`. When all are `None`, the apply phase
uses the legacy per-group Otsu path unchanged.

Adding the fourth method — per-cell **Adaptive Local Clipping** (the eye-validated
`detect_adaptive_by_particle_size`) — touched six layers: the settings dataclass +
sentinel (`models.py`), dispatch + apply (`phases.py`), JSON round-trip
(`artifacts.py`), the GUI rounds-table picker (`config_dialog.py`), runner routing
(`runner.py`), and the CLI (`batch_threshold.py`). It surfaced one near-invisible
ship-blocker plus four structural patterns worth reusing for the next method.

## Guidance

### Lead lesson: a new method's parameter gets its OWN field with the method's validated default — never borrow a shared GUI column whose default differs

A per-cell adaptive detector needs a presmooth sigma. The round already had a
`gaussian_sigma` field (used by the grouped-Otsu path, surfaced as a shared "σ"
column in the GUI), so the first implementation borrowed it:

```python
# BUG — borrows the shared grouped-Otsu column.
# gaussian_sigma defaults to 0 (NO smoothing) for the grouped-Otsu path.
detect_adaptive_by_particle_size(..., presmooth_sigma_px=round_spec.gaussian_sigma)
```

But the eye-validated presmooth for the adaptive engine is **1 px** (the project's
validated params: gaussian MEAN bg, per-cell σ = 1.4826·MAD, k=1, presmooth fixed
σ=1px). The grouped-Otsu default is **0**. Borrowing the shared value silently fed
`presmooth_sigma_px=0` into the detector, which collapses detection on noisy data →
**silent empty masks reported as success**. Every test passed because the synthetic
fixtures had clean, noise-free signal, where 0-px and 1-px presmooth are
indistinguishable.

The fix gives `AdaptiveClipSettings` its own field carrying the validated default,
and the apply branch threads *that* field:

```python
@dataclass(frozen=True)
class AdaptiveClipSettings:
    d_min_um: float
    k: float = 1.0
    presmooth_sigma_px: float = 1.0   # the eye-validated default, NOT round.gaussian_sigma

# apply branch (phases.py): the detector presmooths the RAW image at its OWN
# validated presmooth_sigma_px — the round's grouped-Otsu gaussian_sigma is not it.
mask = detect_adaptive_by_particle_size(
    image, labels, float(pixel_size_um), float(settings.d_min_um),
    k=float(settings.k), presmooth_sigma_px=float(settings.presmooth_sigma_px),
)
```

**Rule:** a shared GUI column is a *display* affordance, not a *semantic* binding. If
two methods would read the same field but their correct defaults differ, the field is
overloaded — split it. Each method owns its parameter and validates its own default in
`__post_init__`. The dialog may still render both onto one column, but the config
object keeps them distinct so a round can never silently inherit the wrong method's
default.

### Secondary patterns (reuse for the next per-cell method)

1. **Per-cell methods don't fit the per-group grouping gate — short-circuit compute to
   a trivial single group.** The compute phase clusters cells and records
   `THRESHOLD_EMPTY` when clustering yields nothing, dropping the dataset. A per-cell
   detector ignores grouping, so it must not be gated by it: `_group_image_labels`
   short-circuits adaptive rounds to `_trivial_grouping` (every cell → group 1) so the
   apply phase always runs, and `/groups` is written as a single degenerate group so it
   doesn't imply a clustering that never drove the mask.

2. **Per-cell methods can't be QC'd by the per-group controller — route to headless
   apply + emit a status line.** The interactive `ThresholdQCController` previews
   per-group thresholds; a per-cell detector has none. The runner routes adaptive rounds
   to the headless apply handler **even when `interactive_qc=True`** and emits a
   `"adaptive sigma clipping — applied headlessly (no QC step)"` status/run-log line, so
   an unreviewed mask is never silently committed in an otherwise-interactive run.

3. **A physical (µm) parameter needs the full chain: thread pixel size + pre-flight +
   runtime backstop + plausibility guard.** `d_min_um` is physical, so the window is
   derived from the dataset's `pixel_size_um`. That needs four guards: thread it from
   `store.metadata` in `apply_threshold_headless`; pre-flight the GUI run when a dataset
   lacks a pixel size (`_datasets_without_pixel_size`); fail the dataset cleanly at apply
   time when it's missing/non-positive (never default to 1 µm/px); and fail when an
   absurd-but-positive pixel size or oversized `d_min` makes the derived window exceed
   the frame (degenerating "local" clipping to global). Also `logger.warning` when 0 px
   are detected, since headless rounds get no visual QC.

4. **No new `*_NAMES` tuple when the settings is scalar-only.** `PunctaDetectorSettings`
   and `IterativeOtsuSettings` validate enumerated string fields against skimage-free
   `*_NAMES` tuples so constructing a round never imports scikit-image. `AdaptiveClipSettings`
   is scalar-only (`d_min_um`, `k`, `presmooth_sigma_px`) — no names to validate — so it
   needs no names module. Don't add the ceremony when there's no name; just range-check
   the scalars in `__post_init__`.

5. **Additive serialization.** `_adaptive_clip_to_dict`/`_from_dict` are wired into
   `_round_to_dict`/`_round_from_dict`, and the `adaptive_clip` key is emitted **only**
   when present, so legacy `run_config.json` files reconstruct as a plain Otsu round.

## Why This Matters

The shared-default trap is the kind of bug that ships. It is invisible in code review
(reusing an existing field reads as DRY), invisible in the test suite (clean fixtures
mask the 0-px vs 1-px difference), and invisible at runtime (an empty mask is a valid
output, not an exception). It surfaces only on real, noisy microscopy data — in the
researcher's hands, after the workflow reports success. For software whose ground truth
is the eye and whose default fixtures are synthetic, a silently-wrong default is strictly
worse than a crash. Encoding the validated default in the method's own field makes the
correct behavior the default behavior and makes the overload visible at the type level
(two fields, two defaults) instead of buried in a parameter pass.

The secondary patterns matter because a per-cell method violates three assumptions baked
into a per-group pipeline: that compute produces meaningful groups, that apply can be
previewed per group, and that parameters are unitless pixels. Each has a gate, a
controller, or a default that will silently do the wrong thing unless explicitly handled.

## When to Apply

- Adding any new strategy sentinel to `ThresholdingRound` (the fifth method and beyond).
- Any time a new method's parameter overlaps a field/column an existing method uses **and**
  their correct defaults differ — split the field.
- Adding a per-cell/per-dataset method to a pipeline whose phases assume per-group
  semantics (grouping gate, QC controller, group serialization).
- Introducing a physical-unit (µm, s) parameter into a pipeline that otherwise speaks
  pixels/frames — wire the full thread + pre-flight + backstop + plausibility-guard chain.

## Examples

### Regression test for the shared-default trap

The original tests passed because fixtures were noise-free. The regression test must
(a) use a structured/noisy background so per-cell MAD > 0 (otherwise 0-px and 1-px
presmooth are identical), and (b) assert the adaptive round presmooths at 1 px **even
when the round's `gaussian_sigma` is 0**, and that the result differs from a genuine
no-presmooth run:

```python
def test_adaptive_presmooths_at_1px_independent_of_round_sigma():
    image, labels = noisy_cell_fixture()          # structured background → MAD(work) > 0
    round_spec = ThresholdingRound(
        ..., gaussian_sigma=0.0,                   # the trap value (grouped-Otsu default)
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.12),  # presmooth defaults to 1.0
    )
    mask_default = run_adaptive(image, labels, round_spec)        # presmooth = 1px
    mask_no_presmooth = run_adaptive(                            # presmooth = 0
        image, labels, replace_presmooth(round_spec, 0.0))
    assert mask_default.sum() > 0                                # catches silent empty mask
    assert not np.array_equal(mask_default, mask_no_presmooth)   # catches field defaulting to 0
```

Both assertions are load-bearing: `sum() > 0` catches the silent-empty-mask failure, the
inequality catches the field silently being 0.

### Scalar-only settings need no names module

```python
# puncta / iterative-otsu validate enumerated names against skimage-free tuples:
if self.detector_name not in DETECTOR_NAMES: raise ValueError(...)
if self.scope not in SCOPE_NAMES: raise ValueError(...)

# AdaptiveClipSettings is scalar-only — just range-check in __post_init__:
if self.d_min_um <= 0: raise ValueError(f"d_min_um must be > 0 µm, got {self.d_min_um}")
if self.k < 0: raise ValueError(...)
if self.presmooth_sigma_px < 0: raise ValueError(...)
```

## Related

- Sibling method additions (the direct lineage of this pattern):
  `docs/plans/2026-06-03-002-feat-headless-puncta-thresholding-plan.md` (puncta two-pass,
  established the `*Settings`-on-`ThresholdingRound` sentinel + `_NAMES` drift-guard) and
  `docs/plans/2026-06-08-002-feat-iterative-otsu-thresholding-plan.md` (iterative Otsu).
- This work's plan: `docs/plans/2026-06-15-001-feat-adaptive-clip-thresholding-workflow-plan.md`.
- The standalone detector this promotes into the batch workflow:
  `docs/plans/2026-06-05-001-feat-adaptive-clipping-gui-module-plan.md` (source of the
  validated detector and the presmooth=1px value at the center of the trap).
- `docs/solutions/architecture-patterns/registered-analysis-framework.md` — shares the
  single-source-of-truth + drift-guard + dialog-as-Action methodology, but a **different
  framework** (the Scripts-tab `@register_analysis` registry, not the `ThresholdingRound`
  sentinel path). See it for the registry idiom; this doc for the sentinel path and the
  shared-default trap.
- `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md` — same workflow
  lineage; mask-vs-segmentation naming and the `{0,1}` binary-mask convention.

**Validation principle (project-specific):** the eye is ground truth, not any mask or
numeric score. Validate adaptive-clip parameter changes visually on real data, not by
scoring against hand-drawn masks. `k` stays a user-tunable knob (do not bake it in);
`presmooth` is the fixed validated 1 px and is not user-facing in the workflow GUI.
