---
title: "feat: Measurement, particle export, and metric configuration improvements"
type: feat
date: 2026-03-31
deepened: 2026-03-31
brainstorm: docs/brainstorms/2026-03-31-measurement-export-improvements-brainstorm.md
---

# feat: Measurement, Particle Export, and Metric Configuration

## Enhancement Summary

**Deepened on:** 2026-03-31
**Review agents used:** Python reviewer, Simplicity reviewer, Performance oracle, Architecture strategist

### Key Improvements
1. Identified cell_table filter desync bug that would occur without handler update
2. Simplified architecture: refactor `analyze_particles()` in-place instead of creating wrapper functions
3. Per-particle detail via shared `_iter_particles()` iterator — avoids duplicate computation
4. Metric config saves UX (column clutter), not performance (~100ms vs 3-5s pipeline)
5. QSettings needs type-safe wrapper (single-item list deserializes as string)

### Reviewer Consensus
- **All agree:** Preserve filter/selection is correct; cell IDs are stable
- **Simplicity vs Python reviewer tension:** Simplicity says combine into one function; Python reviewer says separate `analyze_particles_detail()` with shared iterator. **Resolution:** Use shared `_iter_particles()` iterator consumed by both summary and detail functions — clean separation with shared computation
- **Performance insight:** The real bottleneck is `find_objects` + `regionprops`, not metrics. Single-pass multi-channel (channels inside cell loop) avoids redundant `find_objects` calls

---

## Overview

Four improvements to the measurement pipeline: per-particle CSV export (one row per particle with cell_id), multi-channel particle analysis, preserving filter/selection on measure/analyze, and a metric configuration dialog controlling which metrics are computed.

## Changes

### 1. Preserve filter/selection on measure/analyze

**File:** `src/percell4/model.py:92-101`

Remove the unconditional clearing but **intersect stale IDs** with valid labels to prune references to cells that no longer exist:

```python
def set_measurements(self, df: pd.DataFrame) -> None:
    self._df = df
    self._filtered_df_cache = None
    # Prune stale IDs but preserve the user's filter/selection intent
    if self._filtered_ids is not None and "label" in df.columns:
        valid = set(df["label"].tolist())
        self._filtered_ids &= valid
    if self._selected_ids and "label" in df.columns:
        valid = set(df["label"].tolist())
        self._selected_ids = [s for s in self._selected_ids if s in valid]
    self.state_changed.emit(StateChange(data=True))
```

**Critical:** Only emit `StateChange(data=True)`, not `filter=True` or `selection=True`.

#### Cell table handler update (mandatory)

The cell_table currently relies on `filter=True` from `set_measurements()` to re-apply filtering. After this change, it won't get that flag. Fix in `cell_table.py` `_on_state_changed`:

```python
if change.data:
    self._reload_table_data()
    if self.data_model.is_filtered:
        self._apply_filter()
    if self.data_model.selected_ids:
        self._highlight_selected_rows()
```

#### Tests to update

- `test_set_measurements_emits_state_changed` — assert `filter=False`, `selection=False`
- `test_filtered_df_cache_invalidated_by_set_measurements` — filter is preserved, not cleared

#### Gotcha

`np.isin()` silently fails with Python sets in NumPy 2.x. Verify `_apply_cell_filter()` passes `list(filtered_ids)` (it already does at launcher.py:1092 — add a comment so nobody removes the `list()` call).

### 2. Multi-channel particle analysis + per-particle export

**File:** `src/percell4/measure/particle.py`

Refactor `analyze_particles()` to accept `images: dict[str, NDArray]` and return both summary and detail DataFrames. Use a shared `_iter_particles()` iterator to avoid duplicate computation.

#### Architecture

```python
@dataclass
class _ParticleRecord:
    cell_id: int
    particle_id: int
    area: float
    centroid_y: float
    centroid_x: float
    intensities: dict[str, float]        # {channel: mean_intensity}
    integrated: dict[str, float]          # {channel: integrated_intensity}

def _iter_particles(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> Iterator[_ParticleRecord]:
    """Yield per-particle records. Single find_objects call shared across channels."""
    slices = find_objects(labels)
    for label_val in range(1, labels.max() + 1):
        sl = slices[label_val - 1]
        if sl is None:
            continue
        cell_mask = labels[sl] == label_val
        mask_crop = mask[sl] > 0
        particle_mask = cell_mask & mask_crop
        labeled, n = ndlabel(particle_mask)
        props_by_channel = {}
        for ch_name, image in images.items():
            props_by_channel[ch_name] = regionprops(
                labeled, intensity_image=image[sl]
            )
        # Iterate particles using first channel's props for geometry
        first_ch = next(iter(images))
        for pid, prop in enumerate(props_by_channel[first_ch], start=1):
            if prop.area < min_area:
                continue
            cy, cx = prop.centroid
            yield _ParticleRecord(
                cell_id=int(label_val),
                particle_id=pid,
                area=float(prop.area),
                centroid_y=float(sl[0].start + cy),
                centroid_x=float(sl[1].start + cx),
                intensities={ch: float(props_by_channel[ch][pid-1].intensity_mean) for ch in images},
                integrated={ch: float(props_by_channel[ch][pid-1].intensity_mean * prop.area) for ch in images},
            )

def analyze_particles(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> pd.DataFrame:
    """Per-cell summary (existing behavior, now multi-channel)."""
    # Aggregate from _iter_particles

def analyze_particles_detail(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> pd.DataFrame:
    """Per-particle detail rows for CSV export."""
    # Direct from _iter_particles
```

#### Performance: single-pass multi-channel

Channels are iterated inside the cell loop, sharing `find_objects` and bounding-box slicing. This avoids N-1 redundant full-image scans (~30ms each) and N-1 redundant `cell_mask` computations.

#### Column naming

Geometry columns (particle_count, total_particle_area, coverage) are channel-independent — NOT prefixed. Only intensity columns get `{channel}_` prefix: `{channel}_particle_mean`, `{channel}_particle_integrated`.

**Cleanup:** Remove duplicate `_pixels` columns (total_particle_area_pixels, max_particle_area_pixels) — identical to their non-`_pixels` counterparts.

#### Per-particle detail DataFrame columns

| Column | Source |
|--------|--------|
| `cell_id` | Parent cell label value |
| `particle_id` | Sequential within cell (1, 2, 3...) |
| `area` | `prop.area` |
| `centroid_y`, `centroid_x` | Absolute coordinates |
| `{channel}_mean_intensity` | `prop.intensity_mean` per channel |
| `{channel}_integrated_intensity` | `prop.intensity_mean * prop.area` per channel |

#### Launcher wiring

In `_on_analyze_particles`:
- Collect all image layers (same pattern as `_on_measure_cells` lines 1457-1460)
- Call both `analyze_particles()` and `analyze_particles_detail()`
- Store `self._last_particle_detail_df` for export
- Initialize both `_last_particle_df` and `_last_particle_detail_df` to `None` in `__init__`
- "Export Particle Data" button exports the detail DataFrame

### 3. Metric configuration dialog

**Files:** `src/percell4/gui/launcher.py`

**Backend is ready:** `measure_cells()` already accepts `metrics: list[str] | None`. The launcher just never passes it.

#### Dialog: just-in-time, minimal

Show the checkbox dialog when "Measure Cells" is clicked — no separate "Configure Metrics..." button, no persistence on first implementation. A `QDialog` with 7 checkboxes, all checked by default, OK/Cancel.

#### QSettings persistence (with type-safe wrapper)

```python
def load_selected_metrics() -> list[str]:
    from percell4.measure.metrics import BUILTIN_METRICS
    settings = QSettings("LeeLabPerCell4", "PerCell4")
    raw = settings.value("metrics/selected", defaultValue=None)
    if raw is None:
        return list(BUILTIN_METRICS.keys())
    if isinstance(raw, str):
        raw = [raw]  # QSettings quirk: single-item list → string
    return [m for m in raw if m in BUILTIN_METRICS]

def save_selected_metrics(metrics: list[str]) -> None:
    settings = QSettings("LeeLabPerCell4", "PerCell4")
    settings.setValue("metrics/selected", metrics)
```

**Critical:** Always validate against `BUILTIN_METRICS` on load — handles removed/renamed metrics between versions.

#### Performance note

Metric config saves ~100ms out of a 3-5s pipeline. Its value is **reducing column clutter**, not computation speed.

## Implementation Order

1. **Preserve filter/selection** — smallest change, biggest UX improvement, includes cell_table fix and test updates
2. **Refactor `analyze_particles()`** — accept `dict[str, NDArray]`, add `_iter_particles` iterator, add `analyze_particles_detail()`, clean up duplicate columns
3. **Metric config dialog** — just-in-time dialog, QSettings with type-safe wrapper

## Acceptance Criteria

- [x] Measure Cells does NOT clear filter or selection
- [x] Analyze Particles does NOT clear filter or selection
- [x] Cell table re-applies filter after measurement when filter is active
- [x] Stale IDs pruned from filter/selection when DataFrame changes
- [x] Analyze Particles measures intensity from all image layers
- [x] Particle CSV export has one row per particle with `cell_id` column
- [x] Particle CSV includes per-channel intensity columns
- [x] Duplicate _pixels columns removed from particle summary from particle summary
- [x] Metric config dialog shows checkboxes for all BUILTIN_METRICS
- [x] Selected metrics persist across sessions via QSettings
- [x] Only selected metrics are computed during Measure Cells
- [x] Model tests updated for new set_measurements() contract

## Files to Modify

| File | Changes |
|------|---------|
| `src/percell4/model.py` | Preserve filter/selection in `set_measurements()`, prune stale IDs |
| `src/percell4/measure/particle.py` | `_iter_particles()`, refactor `analyze_particles()` for multi-channel, add `analyze_particles_detail()`, remove duplicate columns |
| `src/percell4/gui/launcher.py` | Multi-channel particle handler, metric config dialog, per-particle export, pass `metrics` param, init `_last_particle*` in `__init__` |
| `src/percell4/gui/cell_table.py` | Re-apply filter/selection in `_on_state_changed` when `change.data` is True |
| `tests/test_model.py` | Update `test_set_measurements_emits_state_changed` and `test_filtered_df_cache_invalidated_by_set_measurements` |

## References

- Brainstorm: `docs/brainstorms/2026-03-31-measurement-export-improvements-brainstorm.md`
- Existing multi-channel pattern: `measurer.py:282` (`measure_multichannel`)
- Metrics registry: `metrics.py:78-86` (`BUILTIN_METRICS`)
- `metrics` param already wired: `measurer.py:129`, `measurer.py:282`, `measurer.py:312`
- Particle aggregation loop: `particle.py:84` (per-particle data exists transiently)
- QSettings pattern: `viewer.py` geometry save/restore
- np.isin set bug: `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md`
- Cell table filter sync: `cell_table.py:220` (`_on_state_changed`)
- Model tests: `tests/test_model.py:151`, `tests/test_model.py:279`
