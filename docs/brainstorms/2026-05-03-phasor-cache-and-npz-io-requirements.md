# Phasor Cache + .npz I/O — Requirements

**Date:** 2026-05-03
**Status:** Brainstorm complete; ready for `/ce-plan`
**Scope:** Standard

## Problem

Re-opening or re-loading a dataset that already has computed phasor + wavelet results forces the user to click `Compute Phasor` and `Apply Wavelet` again, paying the full compute cost (slow for large datasets; very slow for the wavelet filter). At the same time, external Python scripts in the user's wider FLIM workflow consume `.npz` phasor files but PerCell4 has no way to read or write that format.

## Current state (verified)

- `compute_phasor.py:112-119` writes `/phasor/<channel>/g` and `/phasor/<channel>/s` to the `.h5` already.
- `apply_wavelet.py:107-110` writes `/phasor/<channel>/g_filtered`, `s_filtered`, and `lifetime_filtered`.
- `compute_phasor.py:131-140` invalidates wavelet outputs when the raw phasor is recomputed; `add_decay_to_dataset.py:329,364` invalidates `/phasor/<ch>` when `/decay/<ch>` is rewritten.
- `_on_compute_phasor` and `_on_apply_wavelet` (in `flim_panel.py`) **always recompute** from `/decay`. Cached values are never read by the click path.
- The phasor window has no auto-load from the active dataset; it only populates when `set_phasor_data` is called explicitly by the FlimPanel after a compute.
- `.npz` import/export does not exist anywhere.

## Goals

1. Skip the multi-second-to-multi-minute recompute when re-opening a dataset whose phasor + wavelet are already cached.
2. Round-trip phasor data with external `.npz`-based scripts (export and import).
3. Make the new behavior align with PerCell4's existing IO surfaces (Add Layer dialog tabs; IO-panel export buttons), not invent new ones.

## Non-goals

- Storing the phasor cache outside the `.h5` (no sidecar format for the cache itself; `.npz` is purely the interop format).
- Parameter-keyed cache invalidation beyond what already exists (re-import TCSPC clears `/phasor/<ch>`; recomputing raw phasor clears wavelet). User has accepted the simpler "load if cached, force recompute via Shift" model.
- Auto-loading every channel's cached phasor at dataset-open time — only the active channel, only when the phasor window is opened or the active channel changes.
- Importing a `.npz` to bootstrap a brand-new `.h5` from scratch (import requires an active dataset).
- Multi-channel single-`.npz` bundles. One `.npz` = one channel.

## User outcomes

- User opens an existing dataset with computed phasor → opens the Phasor window → sees the histogram and wavelet result populate instantly. No clicking `Compute Phasor` / `Apply Wavelet`.
- User has external scripts that write `.npz` phasor files → drops them into PerCell4 via the Add Layer dialog → they appear as cached phasor for the matching channel.
- User wants to feed PerCell4-computed phasor into an external script → clicks `Export Phasor (.npz)...` → gets a per-channel `.npz` file.
- User wants to recompute phasor or wavelet with new parameters → Shift-clicks the button → fresh compute, cache overwritten.

## Functional requirements

### FR-1: Auto-load cached phasor on phasor-window open or channel switch

When the Phasor Plot window opens (or while open, when the active channel changes), if the active channel has cached phasor in the active dataset:

- Read `/phasor/<active_channel>/g` and `/s` and push into the phasor window via `set_phasor_data`.
- If `/phasor/<active_channel>/g_filtered` and `/s_filtered` also exist, pass them as `g_unfiltered` / `s_unfiltered` (matching today's `_on_apply_wavelet` flow), use the filtered arrays as the displayed `g_map` / `s_map`, and default the `Filtered` checkbox to ON.
- If only raw phasor is cached (no wavelet), display raw and default `Filtered` OFF and disabled.
- Wavelet `filter_level` value, when stored in the cache attrs (it already is — `apply_wavelet.py:108`), is read into the FlimPanel's filter-level spinbox.

If no cached phasor exists for the active channel, the phasor window stays in its current empty state — the user clicks `Compute Phasor` to populate.

### FR-2: Compute / Wavelet buttons act as "load if cached, else compute"

`Compute Phasor` button (`flim_panel.py:_on_compute_phasor`):

- If `/phasor/<active_channel>/g` and `/s` exist: read them, push into phasor window, show status `Loaded cached phasor (channel: <name>)`. No recompute.
- Otherwise: run the existing compute path.
- Tooltip: `Compute Phasor (Shift+click to force recompute)`.

`Apply Wavelet` button (`_on_apply_wavelet`):

- If `/phasor/<active_channel>/g_filtered` and `/s_filtered` exist: read them, push into phasor window with `Filtered` ON. No recompute.
- Otherwise: run the existing compute path.
- Tooltip: `Apply Wavelet (Shift+click to force recompute)`.

Holding **Shift** when clicking either button bypasses the cache check and runs the existing compute path, overwriting whatever is cached. Implementation: check `QApplication.keyboardModifiers() & Qt.ShiftModifier` at click handler entry.

### FR-3: Export `.npz` from the IO panel

New button `Export Phasor (.npz)...` in the `Export` group of `io_panel.py`, alongside `Export Measurements to CSV...` and `Export Images...`.

Behavior: open a directory chooser. For each channel that has any cached phasor data in the active dataset, write one `.npz` file per channel:

```
<dataset_stem>_<channel>_phasor.npz
```

Each file contains the keys present in the cache for that channel:

| Key | Type | Source |
|---|---|---|
| `g` | float32 (H, W) | `/phasor/<ch>/g` (required) |
| `s` | float32 (H, W) | `/phasor/<ch>/s` (required) |
| `g_filtered` | float32 (H, W) | `/phasor/<ch>/g_filtered` (optional) |
| `s_filtered` | float32 (H, W) | `/phasor/<ch>/s_filtered` (optional) |
| `intensity` | float32 (H, W) | `/decay/<ch>.sum(axis=-1)` (required if `/decay/<ch>` exists) |
| `metadata` | object array | dict serialized to a 0-d numpy object array |

`metadata` includes (when available):

```python
{
    "channel": str,
    "harmonic": int,                # from /phasor/<ch>/g attrs
    "filter_level": int,            # from /phasor/<ch>/g_filtered attrs (if filtered exists)
    "flim_frequency_mhz": float,    # from dataset metadata
    "source_dataset_stem": str,     # for traceability
    "schema_version": 1,
}
```

If a channel has no cached phasor at all, skip it silently (don't write an empty file).

Status bar after export: `Exported phasor for N channel(s) to <path>`.

### FR-4: Import `.npz` as a new tab in `AddLayerDialog`

New tab in `add_layer_dialog.py`, joining the existing five tabs:

```
Single TIFF | Discover TIFFs | TCSPC (.bin) | ImageJ ROIs (.zip) | Cellpose (.npy) | Phasor (.npz)
```

UI in the new tab:

- File picker (allow multi-select — one or more `.npz` files; each = one channel).
- A small table preview showing for each selected file: filename, detected channel name (from `metadata["channel"]` or filename), shape, whether it carries `g_filtered`.
- Channel-name mapping per row: editable target channel name. Default to `metadata["channel"]` if present, otherwise infer from filename (`<stem>_<channel>_phasor.npz` → `<channel>`).
- Conflict resolution per row: if `/phasor/<target_channel>` already exists in the active dataset, mark the row with a warning chip and the action dropdown gains an `Overwrite` choice. Default action: `Skip`.
- A bottom `Import` button: processes each row's chosen action. On success, shows status `Imported phasor for N channel(s); M skipped`.

Required `.npz` shape validation:

- `g` and `s` must be present, both float, same shape.
- If `intensity` is present, must match `g.shape`.
- If `g_filtered` / `s_filtered` are present, must match `g.shape` and both must be present together.
- If `metadata` is present, must be a 0-d object array deserializable to a dict.

Validation failures are surfaced as per-row error chips before import; the row's action defaults to `Skip` and `Import` proceeds with the valid rows.

After import: write each accepted row to `/phasor/<target_channel>/g`, `s`, `g_filtered`, `s_filtered` (when present), with the same attrs that the compute use cases would have written. Do NOT write `intensity` separately — it is reconstructable from `/decay/<ch>` or accepted as input-only validation. (If the user has an `.npz` with no matching `/decay/<ch>` — meaning intensity-weighted histogram needs the `.npz`'s intensity — this is out of scope for v1; surface as a warning chip.)

### FR-5: Cache invalidation reuses existing rules

No new invalidation logic. The existing rules stand:
- TCSPC re-import → `/phasor/<ch>` cleared (`add_decay_to_dataset.py`).
- `Compute Phasor` (Shift-clicked) → wavelet outputs deleted (`compute_phasor.py:131-140`).
- `Apply Wavelet` (Shift-clicked) → overwrites filtered outputs.

Imported phasor is treated as "computed" — the same invalidation chain applies if `/decay` is later rewritten. Document this in the import tab: "Imported phasor will be cleared if you re-import TCSPC for this channel."

## UI changes summary

| Surface | Change |
|---|---|
| `flim_panel.py` `_on_compute_phasor` | Cache-check early-out; Shift bypasses |
| `flim_panel.py` `_on_apply_wavelet` | Cache-check early-out; Shift bypasses |
| `flim_panel.py` button tooltips | "(Shift+click to force recompute)" suffix |
| `phasor_plot.py` `showEvent` / `_on_active_channel_changed` (new wiring) | Lazy auto-load from cache when window opens or channel switches |
| `io_panel.py` Export group | New `Export Phasor (.npz)...` button |
| `add_layer_dialog.py` `_tabs` | New `Phasor (.npz)` tab with table-driven import |

## Success criteria

1. Opening an existing dataset whose phasor was previously computed → opening the Phasor window shows the histogram and wavelet result with no compute cost (under 500 ms perceived).
2. Round-trip: export a channel's phasor to `.npz`, delete `/phasor/<ch>` from the `.h5`, import the same `.npz` back. The phasor window shows the same histogram before and after.
3. Shift-clicking `Compute Phasor` on a dataset with cached phasor recomputes from `/decay` and overwrites the cache. The status bar reflects "Recomputed" not "Loaded cached".
4. The existing `test_phasor_*.py` tests still pass; new tests cover the cache-check early-out, Shift-bypass, and `.npz` round-trip.

## Open questions deferred to planning

- Exact channel-name inference rule when the `.npz` filename doesn't follow the export convention. (Reasonable default: use `metadata["channel"]`; if missing, prompt.)
- Whether the `.npz` import should also write `/decay/<ch>` derivation hints (probably no — keep import scope to `/phasor/<ch>` only).
- Status-bar wording for Shift-bypass ("Recomputing (Shift)" vs "Force recompute"); pick one in implementation.
- Whether to add a small `Cached` indicator chip next to the channel selector when the active channel has cached phasor (low cost, high clarity — likely yes, but UX detail for planning).

## Dependencies / assumptions

- `/phasor/<ch>` group write paths in the use cases already attach `attrs={"channel", "harmonic", "filter_level"}` — verified for `g`, `g_filtered`. (Spot-check in planning that `s`, `s_filtered` carry equivalent attrs.)
- `numpy.savez` and `numpy.load(allow_pickle=True)` are sufficient for the metadata dict round-trip. `allow_pickle=True` is required for the object-array metadata; document that import only loads `.npz` files the user trusts (consistent with how PerCell4 already loads ImageJ ROIs and Cellpose `.npy`).
- The phasor window's `showEvent` (added in the recent layer-ownership fix) is the natural hook point for auto-load on first open.
