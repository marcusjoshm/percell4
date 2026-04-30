---
title: "Batch Compress Development Lessons"
category: logic-errors
tags: [batch-import, discovery, masks, napari, tile-stitching, qt-dialog, hdf5]
module: io, gui
symptom: "Multiple bugs during batch compress feature: blank masks, identical .h5 outputs, API mismatch, unusable UX"
root_cause: "Boundary mismatches between image libraries and napari/HDF5, shared source_dir in flat discovery, wrong abstraction level for UI, function signature drift"
date: 2026-04-04
---

# Batch Compress Development Lessons

Lessons from building the batch compress feature (branch `feat/batch-compress-tiff-datasets`). Covers six distinct bugs and three design pivots.

## Bug 1: Mask Layers Blank in Napari (0/255 vs 0/1)

**Symptom:** Imported mask TIFFs showed pixel values 0/255 on cursor hover but rendered as completely blank in the napari viewer.

**Root cause:** External tools (ImageJ, OpenCV, PIL) produce masks with foreground = 255. The `store.write_mask()` casts to `uint8` but does not normalize. Napari's `DirectLabelColormap` only maps `{0: transparent, 1: yellow}`. Label value 255 has no color mapping and renders as transparent.

**Fix:** Binarize before writing: `(array > 0).astype(np.uint8)` at the importer boundary.

```python
# In _write_layer and importer.py mask writing:
binary = (array > 0).astype(np.uint8)
store.write_mask(name, binary)
```

**Prevention:** Always binarize masks at the write boundary. The semantic contract is 0/1 boolean, regardless of what the source format uses. Add this normalization in `_write_layer()` (the centralized dispatch point) so all import paths are covered.

**Related:** `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`

## Bug 2: assemble_tiles() Signature Mismatch

**Symptom:** `assemble_tiles() missing 1 required positional argument: 'grid_cols'`

**Root cause:** `assemble_tiles()` takes separate keyword arguments (`grid_rows`, `grid_cols`, `grid_type`, `order`), but the caller passed a `TileConfig` dataclass object as the second positional argument. The function never accepted a `TileConfig` — it predated the dataclass.

**Fix:** Unpack the dataclass at the call site:

```python
array = assemble_tiles(
    tile_groups,
    grid_rows=tile_config.grid_rows,
    grid_cols=tile_config.grid_cols,
    grid_type=tile_config.grid_type,
    order=tile_config.order,
)
```

**Prevention:** When a function takes N related parameters that have a corresponding dataclass, either: (a) update the function to accept the dataclass, or (b) always unpack explicitly at the call site. Never assume a function accepts a dataclass just because one exists for its parameters. Pin with tests.

## Bug 3: Batch Compress Produces Identical .h5 Files for All Datasets

**Symptom:** Compressing multiple datasets discovered from a flat directory produced N .h5 files with different names but identical content — all containing the same data from the first dataset.

**Root cause:** In flat directory discovery (`discover_flat`), all `DatasetSpec` objects share the same `source_dir` (the flat directory root). The launcher passed `ds.source_dir` to `import_dataset()`, which called `FileScanner.scan(path=source_dir)` — scanning the entire directory every time. Every dataset got all files from all groups.

**The critical data flow bug:**
```
discover_flat("/data/FLIM_mNG-mask")
  → DatasetSpec(name="1hr_Ars_1A_...", source_dir="/data/FLIM_mNG-mask", files=(420 files for 1A))
  → DatasetSpec(name="1hr_Ars_1B_...", source_dir="/data/FLIM_mNG-mask", files=(420 files for 1B))

# Launcher passes source_dir, NOT files:
import_dataset(source_dir="/data/FLIM_mNG-mask", ...)  # scans ALL 840 files
import_dataset(source_dir="/data/FLIM_mNG-mask", ...)  # scans ALL 840 files again
```

**Fix:** Add a `files` parameter to `import_dataset()`. When provided, the scanner uses the pre-discovered file list instead of re-scanning the directory. The launcher now passes `ds.files` for each dataset.

```python
# importer.py — use file list when provided
if files is not None:
    result = scanner.scan(files=[str(f.path) if hasattr(f, "path") else str(f) for f in files])
else:
    result = scanner.scan(path=source_dir)

# launcher.py — pass per-dataset files
import_dataset(..., files=ds.files)
```

**Prevention:** When a discovery layer groups files and passes them to a processing layer, always pass the specific file list — never re-derive it from the shared parent directory. The `DatasetSpec.files` tuple exists precisely for this purpose. Any code that ignores it and re-scans `source_dir` will produce duplicates in flat directory mode.

**General pattern:** Discovery produces scoped subsets. Processing must consume those subsets, not re-derive them from the original scope. This is a classic "function re-derives its input instead of using what was given" bug.

## Bug 4: Token Regex Discovery Unusable

**Symptom:** The "Group by Token" discovery mode required users to enter a regex with a capture group (e.g., `(sample\d+)`). Non-programmer users cannot write regex.

**Root cause:** Wrong abstraction — the grouping logic doesn't need user input at all. The dataset name is simply "everything in the filename that isn't a known token." The token patterns (channel, tile, z-slice, timepoint) are already configured.

**Fix:** Replace `discover_by_token(root, group_token_regex)` with `discover_flat(root, token_config)`. The `_derive_dataset_name()` helper strips all matched token patterns from each filename stem, and files are grouped by the remaining string.

```python
def _derive_dataset_name(stem: str, config: TokenConfig) -> str:
    result = stem
    for field_name in ("channel", "timepoint", "z_slice", "tile"):
        pattern = getattr(config, field_name)
        if pattern is None:
            continue
        result = re.sub(pattern, "", result)
    result = result.strip("_- ")
    result = re.sub(r"_{2,}", "_", result)
    return result or stem
```

**Example:**
```
1hr_Ars_1A_IDR_Dcp1A-Dcp2_Sensor_Capture_s00_ch00.tif
  → strip _s00 and _ch00
  → dataset: "1hr_Ars_1A_IDR_Dcp1A-Dcp2_Sensor_Capture"
```

**Prevention:** Before exposing regex to users, ask: can the system derive the answer automatically from information it already has? In this case, the token config already contained all the patterns needed to identify and strip the variable parts of filenames.

## Design Pivot 1: Semantic Selection Instead of File Tree

**Problem:** Initial design used a `QTreeWidget` with checkboxes on individual files. With 30 tiles x 14 channels x N datasets, checking individual files (e.g., only ch00, ch08, ch09 from each dataset) was prohibitively tedious.

**Solution:** Replace file-level tree with two side-by-side `QListWidget`s:
- **Left (Datasets):** Checkbox per discovered dataset group
- **Right (Channels):** Checkbox per discovered channel, with name edit + layer type dropdown in manual mode

Channel selection is global — check ch00, ch08, ch09 once and it applies to every checked dataset. Tiles, z-slices, and timepoints are shown as informational summaries (always processed as complete sets).

**Lesson:** Match the UI abstraction to how users think about their data. Microscopists think "I want channels 0, 8, 9 from datasets A and B" — not "I want files s00_ch00.tif, s01_ch00.tif, s02_ch00.tif, ..."

## Design Pivot 2: Frozen Discovery + Mutable GUI State

**Pattern:** Separate immutable discovery results from mutable user selections.

- `DatasetSpec` (frozen) — what was found on disk. Never changes after discovery.
- `DatasetGuiState` (mutable) — user's checkbox state, layer assignment overrides. Lives in the dialog.

This prevents GUI interactions from corrupting discovery data, and allows re-running discovery without losing the user's selections (guarded by `_discovery_generation` counter).

## Design Pivot 3: Consolidated Add Layer Dialog

**Problem:** Import sources were scattered: TIFF import in launcher, ImageJ ROIs and Cellpose in segmentation panel. Users had to know which button to click for which file type.

**Solution:** Tabbed `AddLayerDialog` with four tabs:
- Single TIFF (channel/segmentation/mask)
- Discover TIFFs (full discovery with dataset + channel selection)
- ImageJ ROIs (.zip)
- Cellpose (.npy)

Single entry point: "Add Layer to Dataset..." in the I/O panel.

## Pattern: Layer Write Dispatch

The `_write_layer(name, layer_type, array)` helper centralizes all type-dependent HDF5 writing:

```python
if layer_type == "Channel":
    # Stack onto /intensity, update channel_names metadata
elif layer_type == "Segmentation":
    store.write_labels(name, array)  # → /labels/<name> as int32
elif layer_type == "Mask":
    binary = (array > 0).astype(np.uint8)
    store.write_mask(name, binary)   # → /masks/<name> as uint8
```

All import paths (single TIFF, batch discovery, future sources) route through this helper, ensuring consistent dtype handling and HDF5 group placement.

### Update 2026-04-29: TCSPC decay shares `write_decay_streaming`

The same dispatch principle now extends to `/decay/<ch>` writes. `adapters/importer.py` exposes a shared `write_decay_streaming(h5_path, channel_name, tile_bins, bin_dims, ...)` helper. Both the compress flow and the add-layer (TCSPC append) flow route through it, producing byte-identical `/decay` layers from the same source `.bin` tiles + TileConfig — verified by synthetic harness (`np.array_equal == True`).

This matters because Bug 3's "discovery scopes, processing consumes" rule has a structural twin at the consumer side: when a consumer (FLIM analysis) reads two HDF5 layers and uses them pointwise, the consumer must derive what it needs from one of those layers, not assume sibling alignment. See [`flim-phasor-cross-layer-alignment-2026-04-29.md`](flim-phasor-cross-layer-alignment-2026-04-29.md) — the multi-day phasor-debugging session where every plausible producer-side fix (dtype matching, stitch metadata persistence, byte-identical writer refactor) failed because the bug lived in three FLIM consumers reading `/intensity[ch_idx]` against `(g, s)` from `/decay/<ch>`.

**Generalized rule:** Producer-side: one writer per layer type (Layer Write Dispatch). Consumer-side: derive aligned quantities from the array you're already reading; never read a sibling layer and assume it lines up.

## Pattern: Matcher-Refactor Scoping Collapse (Three Occurrences)

Three documented cases where a matcher / discovery refactor silently collapsed per-input scope:

1. **Bug 3 above** (compress): pre-fix discovery built one combined token list across all datasets, so every `.h5` got the same channels.
2. **`add-layer-flat-discovery-duplicate-import.md`**: flat-discovery refactor traversed all subdirectories under the picked root and matched every `.tif` once per dataset, producing duplicate channels.
3. **2026-04-29 bin-only import regression**: the cross-format matcher refactor (extracting `match_bin_to_intensity` from importer.py) silently returned every `.bin` as `unmatched` when no TIFF intensity files were present, collapsing all bins under an empty channel key. Fixed by an explicit bin-only fallback that parses each `.bin`'s `_ch(\d+)` token directly. See [`flim-phasor-cross-layer-alignment-2026-04-29.md`](flim-phasor-cross-layer-alignment-2026-04-29.md) "Adjacent fixes" for details.

**Pattern: matchers and discovery routines must defend their per-input scope explicitly.** When extracting matching logic into a pure helper, walk every code path that previously reached it (TIFF-only, bin-only, mixed, no-input) and confirm each still returns the correct per-scope result. A unit test for "bin-only with no TIFFs" would have caught the third occurrence; a unit test for "empty channel list" would have caught the second.
