---
title: FLIM phasor cross-layer alignment — derive intensity from /decay, never read it from a sibling stack
date: 2026-04-29
category: logic-errors
module: percell4.application.use_cases.apply_wavelet, percell4.interfaces.gui.task_panels.flim_panel, percell4.interfaces.gui.peer_views.phasor_plot
problem_type: logic_error
component: tooling
symptoms:
  - "Compute Phasor produced visually wrong (g, s) histograms only when /decay was rewritten by the add-layer / TCSPC append flow, not by compress."
  - "The 'Filtered' wavelet view diverged dramatically from compress's reference output even though raw per-pixel decay was byte-identical."
  - "Multiple plausible-looking fixes (remove rotation, pre-fill calibration, persist stitch metadata, byte-identical write_decay_streaming refactor) each helped something else but did not fix the phasor."
  - "The bug was silent — the rendered phasor looked plausible but in the wrong region of the universal-semicircle plot, leading to incorrect ROI selections."
root_cause: wrong_api
resolution_type: code_fix
severity: critical
related_components: [flim, phasor, wavelet, hdf5, gui]
tags: [flim, phasor, dtcwt, wavelet, hdf5, intensity-weighting, cross-layer-alignment, derived-quantities, append-flow]
---

# FLIM phasor cross-layer alignment — derive intensity from `/decay`, never read it from a sibling stack

## Problem

PerCell4's FLIM phasor view rendered drastically different `(g, s)` histograms between two import flows for **the same `.bin` source files**: compress (initial-import) produced a tight, calibrated arc on the universal semicircle; add-layer (append) produced a smeared, off-axis distribution. The unfiltered phasor was wrong; the wavelet "Filtered" view was even further off. Because the math LOOKED plausible, the user trusted the resulting ROI selections — which is the worst kind of bug.

After verifying via direct numerical inspection that `compress(/decay/ch01)` and `add-layer(/decay/mNG)` were byte-identical (and even when not byte-identical, were related by a clean `np.rot90` permutation that preserves per-pixel decay curves), and that `compute_phasor` produced **identical 2D histograms** of `(g, s)` from both — the bug had to be downstream of the per-pixel computation.

## Symptoms

- Phasor histograms differed dramatically between flows even though `/decay` content was byte-identical or a clean spatial permutation.
- Removing rotation didn't help. Pre-filling calibration didn't help. Persisting stitching metadata didn't help. Refactoring add-layer to use compress's exact `write_decay_streaming` helper for byte-identical output didn't help.
- The wavelet-filtered view was always more wrong than the unfiltered view, suggesting spatial smoothing was amplifying something.
- The bug only appeared when `/decay` was rewritten by add-layer; compress + Compute Phasor on its own output was always correct.

## What Didn't Work

Each of these wrong trails was tried during the investigation. None fixed the phasor; each is documented because they reflect plausible mental models that do NOT explain this bug:

1. **Remove rotation** — `np.rot90` on the stitched `/decay` is mathematically a whole-image permutation, T-axis preserved per pixel, so the *raw* `(g, s)` histogram is rotation-invariant. Removing rotation didn't change phasor output meaningfully.
2. **Match `compress`'s float32 dtype** for `/decay` writes — fixed an unrelated minor inconsistency, didn't touch phasor.
3. **Persist stitching configuration in `/metadata`** so add-layer reads the same Pattern/Start as compress — useful for byte-identity but doesn't address the consumer-side bug.
4. **Pre-fill FLIM calibration spinboxes from `/metadata.flim_cal_*`** — fixed a separate bug where the calibration was being overwritten with `(0.0, 1.0)` defaults, but the phasor was still wrong with calibration restored.
5. **Refactor add-layer to use compress's exact `write_decay_streaming` helper** — produced byte-identical `/decay` output (verified by synthetic harness: `np.array_equal == True`), but the user's filtered phasor still diverged.
6. **Invalidate stale `/phasor/<ch>` when `/decay/<ch>` is rewritten** — necessary correctness fix (otherwise cached phasor maps reflect old decay), but didn't explain a *fresh* compute-phasor producing wrong output.
7. **Rotate `/intensity[ch_idx]` to match the rotated `/decay`** — actually wrong (mutates user's TIFF intensity); reverted. The right answer is the opposite: don't read `/intensity` for FLIM at all.

The lesson encoded in this list: when each plausible producer-side fix doesn't help, the bug is in a **consumer** that reads two arrays and assumes they line up.

## Solution

The bug was in three FLIM analysis sites that all read `/intensity[channel_index]` and used it pointwise against `(g, s)` maps derived from `/decay/<channel>`. When `/intensity` and `/decay` had drifted out of spatial alignment (different stitching, rotation, append-flow that rewrote only `/decay`), the per-pixel multiplication scrambled the result.

**Fix (single, repeated pattern at every site)**: derive intensity from `/decay/<channel>` itself via `decay.sum(axis=-1)` — by construction aligned with the `(g, s)` maps because both come from the same decay tensor.

### `compute_phasor.py` (was already correct)

```python
# domain/flim/phasor.py + application/use_cases/compute_phasor.py:60
intensity_sum = decay.sum(axis=-1).astype(np.float32)
low_signal = intensity_sum <= 0
g_map[low_signal] = 0.0
s_map[low_signal] = 0.0
```

This already derived from decay. Confirmed correct; left alone.

### `apply_wavelet.py` (the silent killer)

```python
# BEFORE — read from a separately-stored stack that could drift
intensity_data = self._repo.read_array(handle, "intensity")
intensity = intensity_data[channel_names.index(channel)]

# AFTER — derive from the same /decay tensor we already have
decay = self._repo.read_array(handle, f"decay/{channel}")
intensity = decay.sum(axis=-1).astype(np.float64)
```

Why this matters numerically: the DTCWT Wiener-shrinkage filter does (`domain/flim/wavelet_filter.py:256-257`):

```python
f_real = g * intensity        # POINTWISE
f_imag = s * intensity        # POINTWISE
# ...filter f_real, f_imag, intensity in wavelet domain...
g_filtered = f_real_filtered / intensity_filtered
s_filtered = f_imag_filtered / intensity_filtered
```

For this to recover sensible `(g, s)`, the `intensity` term **MUST** equal the per-pixel photon count from the same decay that produced `(g, s)`. Pre-fix, intensity came from a separate HDF5 layer that could be at a different orientation (TIFF source, prior compress run, prior add-layer run). Post-fix, it's `decay.sum(axis=-1)` — by construction the correct denominator.

### `flim_panel.py` (compute_phasor and apply_wavelet handlers)

Both `_on_compute_phasor` and `_on_apply_wavelet` previously read `intensity_data[channel_names.index(channel)]` and passed it as the weight for the phasor plot's intensity-weighted 2D histogram (`peer_views/phasor_plot.py:594-606`):

```python
weights = self._intensity.ravel()[valid]
hist, _, _ = np.histogram2d(g_flat, s_flat, bins=300, weights=weights)
```

Same misalignment hazard. Fixed identically — both handlers now read the relevant `/decay/<channel>` and pass `decay.sum(axis=-1)` as the histogram weight.

## Why This Works

The phasor coordinates are mathematically defined as **normalized** Fourier transforms of the per-pixel decay curve:

```
G(h, w) = sum_t [ I(h, w, t) * cos(omega * t) ] / sum_t I(h, w, t)
S(h, w) = sum_t [ I(h, w, t) * sin(omega * t) ] / sum_t I(h, w, t)
intensity_weight(h, w) = sum_t I(h, w, t)
```

The numerator integrals and the denominator (intensity) are computed from **the same decay tensor**. Authoritative references — Ranjit et al. 2018 (Nat. Prot.), the Jameson Lab phasor primer, Vallmitjana et al. 2021 (PMC8221971), the CWF wavelet paper (PMC8221945) — all encode this implicitly in the math. None of them documents the failure mode where a consumer pulls "intensity" from a different array than the one that produced `(g, s)`. It's a structural gap in the conventional FLIM pipeline literature: the alignment requirement is mathematical, not editorial.

PhasorPy and napari-phasors store `mean`, `real`, and `imag` as separate arrays and have no API-level guard linking them. OME-NGFF supports co-located derived arrays but provides no provenance contract between them. The pattern documented here generalizes to those tools as well.

## Prevention

**Named pattern: derive cross-layer dependencies; don't assume sibling alignment.**

When a consumer reads two arrays from the same store and uses them pointwise, ask whether one is derivable from the other. If yes, derive — don't read both. The cost of recomputation (`decay.sum(axis=-1)` is cheap on already-loaded decay) is far less than the cost of a silent misalignment that produces visually plausible but scientifically wrong results.

Concrete rules now in PerCell4:

1. **Every FLIM consumer derives intensity from `/decay/<ch>`** — never reads from `/intensity[ch_idx]`. Three call sites enforced: `compute_phasor.py`, `apply_wavelet.py`, `flim_panel.py` (×2 handlers).
2. **`/phasor/<ch>` is invalidated whenever `/decay/<ch>` is rewritten** — `write_decay_streaming` and `_rotate_decay_in_place` both `del f[phasor_path]`. Stale cached phasor cannot be displayed against fresh decay. **This rule is one of five vectors in the broader pattern documented in [`in-session-hdf5-staleness-multi-vector-2026-04-30.md`](in-session-hdf5-staleness-multi-vector-2026-04-30.md)** — see that doc for handle-snapshot, h5py library cache, in-memory cache, and Qt-event-emission vectors that compound with this one.
3. **Append flows never mutate sibling layers as a "convenience"** — `add_decay_to_dataset` only writes `/decay/<ch>` and `/provenance/decay/<ch>` (plus `/metadata.cross_format_rule` once). It does NOT touch `/intensity` or any other channel's data, even if it would "make alignment easier". An earlier attempt to rotate `/intensity[idx]` alongside `/decay/<ch>` was reverted because it destroys user-supplied TIFF intensity.
4. **`compress` and `add-layer` share the exact same writer** (`write_decay_streaming` in `adapters/importer.py`) — verified byte-identity via a synthetic harness comparing the two flows on identical input and TileConfig. No room for divergence in the write path.
5. **Test the alignment invariant**, not just byte-identity: a regression test should run the full pipeline through compress AND add-layer on the same source, run Compute Phasor + Apply Wavelet on each, and assert the resulting `(g, s, g_filtered, s_filtered)` arrays match modulo any documented permutation.

### Adjacent fixes from the same investigation (sub-bugs that surfaced en route)

These are tracked in the same branch but address different bug classes:

- **Bin-only import regression (`adapters/importer.py`)** — third occurrence of the "matcher refactor silently collapses per-input scope" pattern (after `add-layer-flat-discovery-duplicate-import.md` and `batch-compress-development-lessons.md`). When no TIFF intensity files were present, `match_bin_to_intensity` had no `intensity_channels` to match against and returned every `.bin` as `unmatched` — collapsing all bins into a single empty channel key. Fix: explicit bin-only fallback that parses each `.bin`'s `_ch(\d+)` token directly.
- **Permanent channel deletion (`task_panels/data_panel.py`)** — handler previously removed only the napari layer; channels reappeared on reload. Now writes through to `/intensity` (slice removal), `/metadata.channel_names` rewrite, and FLIM cleanup. Generalizes the rule from `napari-mask-layer-misclassified-as-segmentation.md` (write store before adding layer) to its mirror: write store deletion before/with layer removal.
- **Orphan `ch<N>` slice removal** — when `/intensity` has more slices than `channel_names` entries (drift from previous partial deletes), the launcher names slices past `len(channel_names)` as `f"ch{i}"`. Delete handler now parses that fallback name and slices `/intensity` at the resolved index.
- **Stale `/phasor/<ch>` invalidation** — both `write_decay_streaming` and `_rotate_decay_in_place` now `del f[phasor_path]` so cached phasor maps can't be displayed against rewritten decay.
- **Rotation + flip composition** — TCSPC tab gained a rotation dropdown (4 options) and flip dropdown (3 options). Order of application: rotation FIRST, then flip. Applied only to `/decay/<ch>`; `/intensity` is never touched. The 3×3 median filter in `compute_phasor` is rotation-equivariant so the raw histogram is preserved; the wavelet's spatial smoothing IS direction-sensitive but operates on the rotated decay's own derived intensity, so it's internally consistent.
- **TCSPC tab UX** — `.bin` token dropdown populated from actually-discovered tokens (not a free-text field), per-channel calibration pre-fill from `/metadata`, channel-grouped mapping table (one row per existing TIFF channel, not one row per tile), Tile Stitching controls matching the Compress dialog's conventions exactly, scrollable layout to keep Append button reachable when FLIM Parameters group expands.

## Related Issues

- [`docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md`](../ui-bugs/percell4-flim-phasor-troubleshooting.md) — the foundational FLIM pipeline correctness work (omega normalization, calibration math, dtype handling, axis rendering). All necessary prerequisites; none addressed cross-layer alignment. The new alignment invariant should be added there as a Stage 7 prerequisite.
- [`docs/solutions/logic-errors/batch-compress-development-lessons.md`](batch-compress-development-lessons.md) — defines the `_write_layer` central dispatch and the "discovery scopes / processing consumes" rule (Bug 3). The new compound generalizes that rule from input-scoping to HDF5-layer-consumption: "consumers derive what they need from the layer they're reading, not from a sibling assumed to be aligned." `write_decay_streaming` is now the shared writer reused by both compress and add-layer.
- [`docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md`](add-layer-flat-discovery-duplicate-import.md) — second occurrence of the matcher-refactor scoping-collapse pattern. The bin-only import regression here is the third occurrence; worth promoting to a named rule.
- [`docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`](tiff-pending-channel-name-prefix-mismatch-2026-05-21.md) — the `f"ch{i}"` orphan-slice fallback documented here is one application of the importer's broader convention: `import_dataset` writes `default_name = f"ch{ch_key}"` for *every* unnamed channel, not just orphan-slice repair. Workflow-side code that constructs channel names must mirror that producer-side string shape to keep downstream lookups (e.g., `_channel_index`) consistent.
- [`docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`](../ui-bugs/napari-mask-layer-misclassified-as-segmentation.md) — Item 6 ("Stale HDF5 data") is the precedent for the channel-deletion bug. The new rule extends "Write store before adding layer" with "Write store deletion before/with layer removal."
- [`docs/solutions/build-errors/numpy2-dtcwt-removed-functions.md`](../build-errors/numpy2-dtcwt-removed-functions.md) — adjacent (same file modified — `domain/flim/wavelet_filter.py`); the NumPy 2.0 shims at the top of that file must remain intact alongside any change to where `intensity` is sourced.
- External: PhasorPy ([phasorpy.io API docs](https://www.phasorpy.org/docs/stable/api/io/)) and OME-NGFF ([Glencoe blog 2022](https://www.glencoesoftware.com/blog/2022/04/01/Beyond-images-with-OME-NGFF.html)) — both libraries store `mean` / `real` / `imag` as separately-writable arrays with no alignment contract. The pattern documented here applies upstream too.
