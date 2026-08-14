# Command-line Tools

PerCell4 ships 14 headless console scripts for batch operations across `.h5` datasets. All of them install on `PATH` from `pip install -e .`.

The twelve batch and inspection tools documented below share these conventions (the [two development harnesses](#development-harnesses) at the end of this page are dev-time tools and follow their own, noted there):

- **Positional `paths`** accept one or more `.h5` files or directories. Directories are globbed non-recursively for `*.h5`.
- **`--dry-run`** (where supported) classifies each dataset as a live run would but does not mutate files. Use it on destructive operations to audit what will change.
- **`--quiet`** (where supported) suppresses per-item detail lines. The per-dataset summary and final totals always print. `percell4-batch-threshold`, `percell4-batch-measure`, and `percell4-inspect` have no `--quiet`.
- **`--verbose`** enables DEBUG logging, with a `-v` short form everywhere except `percell4-batch-threshold` and `percell4-batch-measure`, which accept only the long form. `percell4-inspect` has neither.
- **Exit codes:** `0` if at least one dataset made progress, `1` if every dataset was skipped or failed, `2` on argparse / validation failure (no I/O performed).
- **GUI files first.** Close any open PerCell4 GUI session against the target files before running — the batch tools write to the same `.h5` files the GUI reads.

Every tool also runs as a module (`python -m percell4.interfaces.cli.<module>`), which is what the in-app Batch Tools Console uses — see [Modules without an entry point](#modules-without-an-entry-point).

## Command index

| Command | What it does | |
|---|---|---|
| `percell4-batch-cellpose-laptrack` | Compress TIFFs, segment every timepoint with Cellpose, track with laptrack. | [↓](#percell4-batch-cellpose-laptrack--headless-compress--segment--track) |
| `percell4-batch-export` | Export dataset layers as TIFFs into a target directory. | [↓](#percell4-batch-export--tiff-export) |
| `percell4-batch-phasor` | Compute FLIM phasor maps and the wavelet filter in place. | [↓](#percell4-batch-phasor--compute-phasor--wavelet-filter) |
| `percell4-batch-phasor-masks` | Fit a GMM phasor ellipse and write two dual-threshold masks per channel. | [↓](#percell4-batch-phasor-masks--fit-gmm-ellipse--write-dual-threshold-masks) |
| `percell4-batch-export-phasor` | Render the cached phasors to publication-style PNGs. | [↓](#percell4-batch-export-phasor--export-cached-phasors-as-pngs) |
| `percell4-batch-whole-field` | Create a `/labels/whole_field` baseline gating layer. | [↓](#percell4-batch-whole-field--whole-field-segmentation) |
| `percell4-batch-rename` | Rename a channel, mask, or segmentation across many datasets. | [↓](#percell4-batch-rename--rename-a-resource-across-datasets) |
| `percell4-batch-delete` | Delete one named resource, or every resource of a kind, across datasets. | [↓](#percell4-batch-delete--delete-resources-across-datasets) |
| `percell4-batch-describe` | Set, append to, or clear the in-file experiment description. | [↓](#percell4-batch-describe--set-the-experiment-description-across-datasets) |
| `percell4-batch-threshold` | Run one thresholding round and write `/masks` + `/groups` back. | [↓](#percell4-batch-threshold--headless-grouped-thresholding) |
| `percell4-batch-measure` | Measure existing masks and export a timestamped run folder of CSVs. | [↓](#percell4-batch-measure--measure--particle-analysis--csv-export) |
| `percell4-inspect` | Print (or JSON-dump) each dataset's metadata, description, and layers. | [↓](#percell4-inspect--print-dataset-metadata--layers) |
| `percell4-batch-validate-puncta` | **Dev harness.** Race puncta detectors against ground truth and lock a winner. | [↓](#percell4-batch-validate-puncta--race-puncta-detectors-against-ground-truth) |
| `percell4-window-bakeoff` | **Dev harness.** Score auto-window-size finders against the SG-mask IoU oracle. | [↓](#percell4-window-bakeoff--score-auto-window-size-finders-against-the-sg-mask-oracle) |

## `percell4-batch-cellpose-laptrack` — headless compress + segment + track

End-to-end headless pipeline for multi-timepoint experiments: compress TIFFs, run Cellpose on every timepoint, then track cells across time with laptrack (unless `--no-track`). Each source is either a **TIFF source directory** (imported to `<output-dir>/<source_dirname>.h5`) or an **already-compressed `.h5`** (the compress step is skipped). It exposes the full GUI Segment-tab Cellpose controls so headless runs reproduce interactive tuning, and is designed for overnight batch runs on a remote workstation.

**Inputs and output.** TIFF directory sources require `--output-dir` (each `<source_dirname>.h5` lands there). For an `.h5` source, omit `--output-dir` to **segment it in place**, or pass `--output-dir` to **copy it there first** and segment the copy (the original is left untouched). Time-lapse datasets are tracked unless `--no-track`; `--skip-segmentation` instead runs laptrack on an existing segmentation only (no Cellpose).

```bash
percell4-batch-cellpose-laptrack SOURCES [--output-dir DIR] [options]
```

| Option | Purpose |
|---|---|
| `sources` | One or more dataset TIFF source directories and/or `.h5` files (positional, **required**). |
| `--output-dir OUTPUT_DIR` | Directory for the output `.h5` files. **Required for TIFF directory sources.** For `.h5` sources: omit to segment in place, or give it to copy-then-segment. |
| `--seg-channel SEG_CHANNEL` | Channel name to segment. Default: first channel. Matched against `--channel-names` when given. |
| `--channel-names CHANNEL_NAMES` | Comma-separated names to rename the imported channels, in order (e.g. `'DAPI,GFP,RFP'`). Must match the imported channel count. |
| `--seg-name SEG_NAME` | Name for the segmentation layer. Default: `cellpose_<n_cells>`. With `--skip-segmentation`, this is the **existing** segmentation layer to track (required). |
| `--skip-segmentation` | Skip Cellpose and only run laptrack on an existing segmentation. Requires `--seg-name` (the existing `(T,H,W)` layer) and a time-lapse dataset. |
| `--cellpose-model {cpsam_v2,cpsam,cpdino,cpdino-vitb}` | Cellpose 4.x model. Default: `cpsam_v2` (improved CellposeSAM — better in low-contrast regions). `cpsam` = original; `cpdino` / `cpdino-vitb` = DINOv3 backbones (vitb is smaller). Requires cellpose >= 4.2. |
| `--cellpose-diameter CELLPOSE_DIAMETER` | Cell diameter in pixels; `0` = auto-detect. Default: `30`. |
| `--gpu` | Use GPU for Cellpose. |
| `--device DEVICE` | Explicit torch device for Cellpose (e.g. `xpu`, `cuda:1`). Overrides the device stored in the launcher's Advanced panel; omit to use that stored setting. Only applies with `--gpu`. An unusable device falls back to CPU with a warning on stderr. |
| `--flow-threshold FLOW_THRESHOLD` | Flow error threshold; higher = more permissive. Default: `0.4`. |
| `--cellprob-threshold CELLPROB_THRESHOLD` | Cell probability threshold. Default: `0.0`. |
| `--min-size MIN_SIZE` | Minimum cell size in pixels. Default: `15`. |
| `--saturation SATURATION` | Saturation % for an ImageJ-style Enhance Contrast LUT applied to the segmentation channel before Cellpose; `0` disables. Default: `1`. The on-disk `/intensity` is never modified. |
| `--blur-sigma BLUR_SIGMA` | Gaussian blur sigma applied after the saturation LUT and before Cellpose; `0` disables. Default: `0`. The on-disk `/intensity` is never modified. |
| `--no-remove-edge-cells` | Keep cells touching the image border (default: remove them). |
| `--edge-margin EDGE_MARGIN` | Pixels from the border counted as edge when removing edge cells. Default: `0` (strict border-touching). |
| `--no-track` | Skip tracking even for time-lapse datasets. |
| `--quiet` | Suppress per-dataset progress lines. |
| `--verbose`, `-v` | Enable DEBUG logging. |

The Cellpose defaults match the GUI Segment tab with two exceptions, so pass them explicitly when you want a headless run to reproduce an interactive one: `--cellpose-diameter` defaults to `30`, where the GUI Segment tab seeds `300`; and `--gpu` is off unless passed, where the GUI's **Use GPU** checkbox starts checked.

Examples:

```bash
percell4-batch-cellpose-laptrack /scratch/tiffs/dish_1/ /scratch/tiffs/dish_2/ --output-dir /scratch/h5/
percell4-batch-cellpose-laptrack /scratch/tiffs/timelapse_a/ --output-dir /scratch/h5/ --gpu --cellpose-diameter 240
# Re-segment an already-compressed .h5 in place with tuned thresholds:
percell4-batch-cellpose-laptrack /scratch/h5/dish_1.h5 --cellprob-threshold -1.0 --saturation 2.0
# Copy an .h5 elsewhere, then segment the copy (original untouched):
percell4-batch-cellpose-laptrack /scratch/h5/dish_1.h5 --output-dir /scratch/h5_reseg/
# Track-only: re-run laptrack on an existing segmentation, no Cellpose:
percell4-batch-cellpose-laptrack /scratch/h5/movie.h5 --skip-segmentation --seg-name cellpose_88
# Verbose: surface Cellpose/laptrack native logs + per-frame timing:
percell4-batch-cellpose-laptrack /scratch/h5/movie.h5 --verbose
```

## `percell4-batch-export` — TIFF export

Batch-export dataset layers as TIFFs across one or more `.h5` files. For each input dataset it writes one TIFF per intensity channel, per `/labels/<name>`, and per `/masks/<name>` into `--output-dir`, using a flat `<h5_stem>_<layer>.tif` layout (no per-dataset subfolders). The GUI equivalent lives at `I/O` → **Export Images**.

Exports are written into the target directory rather than in place; the source `.h5` files are never modified. The output directory is created if missing, and existing files with matching names are overwritten silently — point `--output-dir` at a fresh directory to preserve prior runs. Phasor, lifetime, and decay arrays are **not** exported (use the phasor-npz export for those). Per-dataset status headers and final totals always print.

```bash
percell4-batch-export PATHS --output-dir DIR [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). Positional, **required**. |
| `--output-dir OUTPUT_DIR`, `-o OUTPUT_DIR` | Target directory for the `.tif` outputs. Created if missing; existing files with matching names are overwritten. **Required.** |
| `--quiet` | Suppress per-dataset error detail lines. Per-dataset status headers and final totals always print. |
| `--view-bin N` | Bin factor applied to every layer at read time. Default `1` (native resolution — the established export contract). `N > 1` produces downsampled TIFFs using the same lens the GUI applies for `view_bin=N` (`sum_bin_2d` for intensity, `mode_labels` for `/labels`, `majority_vote_mask` for `/masks`). Output filenames are unchanged regardless of bin — track the value yourself (e.g. `--output-dir out_bin4/`) if you mix runs. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Native-resolution export of two datasets
percell4-batch-export dish_1.h5 dish_2.h5 --output-dir /tmp/exports

# Every .h5 in a directory, suppressing per-dataset error detail
percell4-batch-export *.h5 --output-dir out/ --quiet

# Globbed directory, downsampled to match the GUI's view-bin 4 lens
percell4-batch-export /scratch/dishes/ --output-dir ~/exports/ --view-bin 4
```

## `percell4-batch-phasor` — compute phasor + wavelet filter

Batch-computes phasor and applies the wavelet filter across one or more `.h5` datasets. For every channel under `/decay/*` it computes the phasor `(g, s)` maps and applies the wavelet filter, writing `/phasor/<ch>/g`, `/s`, `g_filtered`, `s_filtered`, and `lifetime_filtered` in place into each dataset. Paths may be individual `.h5` files or directories, which are globbed non-recursively (`*.h5`).

Channels with an existing `/phasor/<ch>/g` are skipped unless `--overwrite` is set. Channels missing calibration (`flim_cal_phase_<ch>`, `flim_cal_mod_<ch>`, `flim_frequency_mhz`) are skipped with a clear report line. With `--remove`, the tool runs in inverse mode and deletes `/phasor/<ch>/` instead of computing. The per-dataset summary line and final totals always print, even under `--quiet`.

```bash
percell4-batch-phasor PATHS [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). **Required.** |
| `--filter-level FILTER_LEVEL` | Wavelet filter level (1..30). Default: `9`. |
| `--overwrite` | Recompute channels even when `/phasor/<ch>/g` already exists. Default: skip channels with existing phasor. |
| `--remove` | Inverse mode: delete `/phasor/<ch>/` (all of g, s, g_filtered, s_filtered, lifetime_filtered) for every channel in each dataset instead of computing. Mutually exclusive with `--overwrite`. `--filter-level` is ignored when `--remove` is set. |
| `--quiet` | Suppress per-channel skip / error detail lines. The per-dataset summary line and final totals always print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
percell4-batch-phasor dish_1.h5 dish_2.h5
percell4-batch-phasor /scratch/dishes/ --filter-level 5
percell4-batch-phasor *.h5 --overwrite --quiet
percell4-batch-phasor /scratch/dishes/ --remove
```

## `percell4-batch-phasor-masks` — fit GMM ellipse + write dual-threshold masks

Batch-fits a phasor ellipse and writes two dual-threshold phasor masks per channel across one or more `.h5` datasets. For each requested channel of each dataset, it fits a single-cluster GMM ellipse on the phasor cloud above `--t-fit`, then writes two intensity-thresholded ellipse-membership masks (`--t-mask-a → suffix-a`, `--t-mask-b → suffix-b`) directly into that input file in place (there is no copy / `--output-dir` mode). It reads unfiltered `/phasor/<ch>/g` and `/s` — wavelet-filtered maps are never used here, matching the manual recipe. When a dataset lacks pre-computed phasor maps, they are computed on the fly using the same primitives `percell4-batch-phasor` uses.

Up-front validation: every requested channel must be present in every dataset; suffixes must be non-empty and must differ; no mask name may collide with an existing channel name in any dataset. Validation failures exit `2` without performing any I/O. Close any open PerCell4 GUI session against the target files before running, and use `--dry-run` to audit the planned writes first.

```bash
percell4-batch-phasor-masks PATHS --channels CHANNELS [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). |
| `--channels CHANNELS [CHANNELS ...]` | One or more channel names to fit. Every channel must be present (in both `metadata.channel_names` and `/decay/`) in every input dataset. **Required.** |
| `--t-fit T_FIT` | Intensity threshold defining the GMM fit subset. Default: `10.0`. |
| `--t-mask-a T_MASK_A` | Intensity threshold applied to mask-a after the ellipse-membership step. Default: `0.0`. |
| `--t-mask-b T_MASK_B` | Intensity threshold applied to mask-b after the ellipse-membership step. Default: `5.0`. |
| `--suffix-a SUFFIX_A` | Suffix appended to each channel name for mask-a. Default: `_phasor_1`. |
| `--suffix-b SUFFIX_B` | Suffix appended to each channel name for mask-b. Default: `_phasor_5`. |
| `--roi-source TARGET=SOURCE` | Use `SOURCE`'s fitted ROI for `TARGET`. Repeat the flag for multiple targets. `SOURCE` must itself be self-fitting (not appear as a target in any other `--roi-source`). Treatment-group comparisons (e.g. Untreated → As-treated) use this to share a single ROI across the cohort. |
| `--dry-run` | Print the planned operations and exit `0` without performing any phasor / mask I/O. |
| `--quiet` | Suppress per-channel skip / error detail lines. The per-dataset summary line and final totals always print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Self-fit ellipse per dataset, default thresholds + suffixes
percell4-batch-phasor-masks dish_1.h5 dish_2.h5 --channels mNG mScarlet

# Shared ROI across a treatment cohort
percell4-batch-phasor-masks untreated_a.h5 AsTreated_a.h5 AsTreated_b.h5 \
    --channels mNG \
    --roi-source AsTreated_a.h5=untreated_a.h5 \
    --roi-source AsTreated_b.h5=untreated_a.h5

# Custom thresholds, quiet output
percell4-batch-phasor-masks *.h5 --channels DAPI --t-mask-a 1.0 --t-mask-b 10.0 --quiet

# Audit a planned run without writing
percell4-batch-phasor-masks /scratch/dishes/ --channels mNG --t-fit 20.0 --dry-run
```

## `percell4-batch-export-phasor` — export cached phasors as PNGs

Renders the phasor cache to PNG images across one or more `.h5` datasets. For each dataset it writes one raw PNG per channel under `/phasor/<ch>` (`<h5_stem>_<ch>_phasor.png`) and, for every channel that also has `g_filtered` + `s_filtered`, one filtered PNG (`<h5_stem>_<ch>_phasor_filtered.png`). Each image mirrors the GUI phasor window: intensity-weighted 2D histogram, universal semicircle overlay, labeled G/S axes. This is the tool for pulling publication-ready phasor plots out of a batch without opening each dataset in the GUI.

Read-only with respect to the `.h5` files — nothing is written back into the datasets, so it is safe to run against files a GUI session has open. It does **not** compute phasors: channels with no `/phasor/<ch>/g` are reported as skipped, so run `percell4-batch-phasor` first. Outputs use a flat layout (no per-dataset subfolders) and existing files at those paths are overwritten silently — point `--output-dir` at a fresh directory to preserve prior runs. The output directory is probed for writability up front, so a bad path fails fast before any dataset is processed.

```bash
percell4-batch-export-phasor PATHS --output-dir DIR [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). **Required.** |
| `--output-dir OUTPUT_DIR`, `-o OUTPUT_DIR` | Target directory for the `.png` outputs. Created if missing. Existing files with matching names are overwritten. **Required.** |
| `--quiet` | Suppress per-channel error / skip / empty detail lines. Per-dataset status headers and final totals always print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
percell4-batch-export-phasor dish_1.h5 dish_2.h5 --output-dir /tmp/phasors
percell4-batch-export-phasor /scratch/dishes/ --output-dir ~/phasors/
percell4-batch-export-phasor *.h5 --output-dir out/ --quiet
```

## `percell4-batch-whole-field` — whole-field segmentation

Creates `/labels/whole_field` (every pixel = 1) in each input `.h5` dataset, mutating each file in place. Useful as a baseline gating layer for whole-field measurements or as a default segmentation before per-cell Cellpose runs. Shape is taken from `/metadata.native_shape`, falling back to the first `/decay/<channel>` if absent. Pre-existing `/labels/whole_field` is silently overwritten.

Each dataset is classified as succeeded or failed and tallied into a final totals line. Use `--dry-run` to get that same classification without touching any file. Close any open PerCell4 GUI session against the target files before running.

```bash
percell4-batch-whole-field PATHS [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). **Required.** |
| `--dry-run` | Classify each dataset as succeeded / failed exactly as a live run would, but do not mutate any file. |
| `--quiet` | Suppress per-resource detail lines. The per-dataset summary line and final totals always print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
percell4-batch-whole-field dish_1.h5 dish_2.h5
percell4-batch-whole-field /scratch/dishes/ --dry-run
```

## `percell4-batch-rename` — rename a resource across datasets

Renames `(kind, old_name) → new_name` in place in each input `.h5`. Datasets that don't have the source name are reported as skipped, not failed. Datasets where the target name already exists are recorded as per-dataset errors — the batch continues to the next file rather than aborting. Close any open PerCell4 GUI session against the target files first; the batch CLI writes to the same `.h5` files the GUI reads.

Channel renames go through `DatasetStore.rename_channel`, which moves `/decay/<name>`, `/phasor/<name>`, and updates `/metadata.channel_names` plus the per-channel FLIM calibration attrs together. Masks and segmentations go through `DatasetStore.rename_item` against `/masks/<name>` and `/labels/<name>` respectively.

```bash
percell4-batch-rename PATHS --kind {channel,mask,segmentation} --from-name FROM_NAME --to-name TO_NAME [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). **Required.** |
| `--kind {channel,mask,segmentation}` | Resource kind to rename. Choices: `channel`, `mask`, `segmentation`. **Required.** |
| `--from-name FROM_NAME` | Current name of the resource in each `.h5`. **Required.** |
| `--to-name TO_NAME` | New name to rename the resource to. **Required.** |
| `--dry-run` | Classify each dataset as succeeded / skipped / failed exactly as a live run would, but do not mutate any file. |
| `--quiet` | Suppress per-resource skip / error detail lines. The per-dataset summary line and final totals always print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
percell4-batch-rename dish_1.h5 dish_2.h5 \
    --kind channel --from-name mScar --to-name mScarlet
percell4-batch-rename /scratch/dishes/ \
    --kind mask --from-name thresh_old --to-name thresh_new
percell4-batch-rename *.h5 \
    --kind segmentation --from-name cellpose_qc \
    --to-name cp_mask --dry-run
```

## `percell4-batch-delete` — delete resources across datasets

Deletes either a single named resource (`--name`) or every resource of the given kind (`--all`) — a channel, mask, segmentation, or FLIM phasor/wavelet resource — in each input `.h5`. The two flags are mutually exclusive and exactly one is required. Edits happen in place; datasets that don't have the resource are reported as skipped, not failed. Use `--dry-run` first on destructive operations to classify each dataset (succeeded / skipped / failed) exactly as a live run would without mutating any file.

Channel deletes go through `DatasetStore.delete_channel`, which removes `/decay/<name>`, `/phasor/<name>`, the `channel_names` metadata entry, and the per-channel FLIM calibration attrs together. Masks and segmentations go through `DatasetStore.delete_item` against `/masks/<name>` and `/labels/<name>`.

FLIM phasor resources are keyed by CHANNEL name: `--kind phasor` removes the whole `/phasor/<channel>` group (base g/s and the wavelet output), while `--kind wavelet` removes only the wavelet output (`g_filtered`/`s_filtered`/`lifetime_filtered`), keeping the base phasor. Close any open PerCell4 GUI session against the target files before running — the batch CLI writes to the same `.h5` files the GUI reads.

```bash
percell4-batch-delete PATHS --kind {channel,mask,segmentation,phasor,wavelet} (--name NAME | --all) [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). |
| `--kind {channel,mask,segmentation,phasor,wavelet}` | Resource kind to delete. For `phasor`/`wavelet` the resource is keyed by channel name. **Required.** |
| `--name NAME` | Name of the resource to delete in each `.h5`. For `--kind phasor`/`wavelet` this is the CHANNEL name (e.g. `mNG`). Mutually exclusive with `--all`; exactly one of `--name` / `--all` is required. |
| `--all` | Delete every resource of the given `--kind` found in each `.h5`. Channels are enumerated from `metadata.channel_names`; masks from `/masks/*`; segmentations from `/labels/*`. Mutually exclusive with `--name`. Combine with `--dry-run` to audit which resources will be removed before running. |
| `--dry-run` | Classify each dataset as succeeded / skipped / failed exactly as a live run would, but do not mutate any file. |
| `--quiet` | Suppress per-resource skip / error detail lines. The per-dataset summary line and final totals always print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Delete one named resource per dataset
percell4-batch-delete dish_1.h5 dish_2.h5 \
    --kind segmentation --name cellpose_qc
percell4-batch-delete /scratch/dishes/ \
    --kind mask --name thresh_488 --dry-run

# Delete a FLIM phasor (by channel name); wavelet keeps the base phasor
percell4-batch-delete *.h5 --kind phasor --name mNG --dry-run
percell4-batch-delete *.h5 --kind wavelet --all

# Delete EVERY resource of a kind
percell4-batch-delete /scratch/dishes/ --kind mask --all --dry-run
percell4-batch-delete *.h5 --kind channel --all
```

## `percell4-batch-describe` — set the experiment description across datasets

Sets, appends to, or clears the free-text **experiment description** stored inside each input `.h5`. The description is where the sample, its preparation, the experimental condition, and anything else worth recognising later actually lives — the filename can't hold a sentence. Because it is stored in the file, it travels with the dataset through copies and moves.

Exactly one verb is required, so a run can never write without saying what it is doing. `--set` replaces whatever is there; `--append` adds the new text below the existing text separated by a blank line; `--clear` removes the description. Setting or appending empty text clears rather than storing a blank placeholder. Datasets with no description to clear are reported as skipped, not failed. Close any open PerCell4 GUI session against the target files first; the batch CLI writes to the same `.h5` files the GUI reads.

The `--append` verb is what makes a whole experiment cheap to label: run it once over the folder with the prep and condition every dish shares, then add each dish's own detail on top.

```bash
percell4-batch-describe PATHS (--set TEXT | --append TEXT | --clear) [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. Directories are globbed non-recursively (`*.h5`). **Required.** |
| `--set TEXT` | Replace each dataset's description with `TEXT`. Mutually exclusive with `--append` / `--clear`; exactly one verb is required. |
| `--append TEXT` | Add `TEXT` below each dataset's existing description, separated by a blank line. Appending to a dataset with no description writes `TEXT` alone. |
| `--clear` | Remove each dataset's description entirely. |
| `--dry-run` | Classify each dataset as succeeded / skipped / failed exactly as a live run would, but do not mutate any file. |
| `--quiet` | Suppress per-dataset detail lines. The per-dataset summary line and final totals always print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Label one dish
percell4-batch-describe dish_1.h5 \
    --set 'HeLa p14, fixed 4% PFA 15min, permeabilized 0.1% TX-100'

# Add the shared prep note to every dish in an experiment
percell4-batch-describe /scratch/experiment_7/ \
    --append '2h 10uM drug at 37C, 5% CO2'

# See which files would be touched, without touching them
percell4-batch-describe /scratch/experiment_7/ --append 'shared notes' --dry-run

# Then add what is unique to one dish
percell4-batch-describe /scratch/experiment_7/dish_3.h5 \
    --append 'bubble in the upper-left quadrant'

# Remove a description
percell4-batch-describe dish_3.h5 --clear
```

## `percell4-batch-threshold` — headless grouped thresholding

Runs one grouped-threshold round across datasets and writes `/masks/<round>` + `/groups/<round>` back into each `.h5` in place. Requires each dataset to already carry a segmentation (`/labels`); it does not segment, measure, or export — pair it with `percell4-batch-measure` to get CSVs (it prints the exact follow-up command on success). It refuses to overwrite an existing same-name `/masks/<round>` unless `--overwrite` is passed, and auto-picks the segmentation per dataset when `--segmentation` is omitted.

`--strategy` selects the thresholding engine: `grouped-otsu` (default) groups cells by `--metric` with `--algorithm` (k-means/GMM) then runs per-group Otsu; `iterative-otsu` peels the brightest layer each round (see the iterative-otsu options); and the per-cell `adaptive-clip` and `auto-extract` detectors find puncta inside each cell, ignoring the `--algorithm`/`--metric` grouping. `adaptive-clip` requires `--d-min-um`. Opt into `--cnr-classify` (ALC strategies only) to additionally split foci by contrast-to-noise ratio into `<round>_low` / `<round>_high` masks plus a `/classification/<round>` table.

```bash
percell4-batch-threshold DATASETS --round-name ROUND_NAME --channel CHANNEL \
    [--strategy {grouped-otsu,iterative-otsu,adaptive-clip,auto-extract}] \
    [--algorithm {gmm,kmeans}] [--segmentation SEGMENTATION] [--overwrite] [options]
```

| Option | Purpose |
|---|---|
| `datasets` | One or more `.h5` files, or directories (every `*.h5` within, non-recursive). **Required.** |
| `--round-name ROUND_NAME` | Mask/group name to write (the round name). **Required.** |
| `--channel CHANNEL` | Channel name to threshold. **Required.** |
| `--metric METRIC` | Per-cell metric used to group cells before thresholding (default `mean_intensity`). |
| `--algorithm {gmm,kmeans}` | Grouping algorithm (default `kmeans`). |
| `--gmm-criterion {bic,silhouette}` | GMM component-count criterion (default `bic`). |
| `--gmm-max-components GMM_MAX_COMPONENTS` | Max GMM components (default `4`). |
| `--kmeans-n-clusters KMEANS_N_CLUSTERS` | K-means cluster count (default `3`). |
| `--gaussian-sigma GAUSSIAN_SIGMA` | Pre-threshold Gaussian sigma (default `1.0`). For `--strategy adaptive-clip` or `auto-extract` this is the detector's per-cell presmooth sigma (px). |
| `--strategy {grouped-otsu,iterative-otsu,adaptive-clip,auto-extract}` | Thresholding strategy (default `grouped-otsu`, the legacy per-group Otsu). `iterative-otsu` peels the brightest layer each round; `adaptive-clip` runs the per-cell single-window adaptive sigma-clipping detector; `auto-extract` runs the per-cell two-pass auto-extraction detector. The two per-cell strategies ignore `--algorithm`/`--metric` grouping. |
| `--d-min-um D_MIN_UM` | Smallest particle diameter to detect, as a value in `--d-min-unit` (**Required** for `--strategy adaptive-clip`). Sets the local-background window and size filter; the noise floor is a robust per-cell MAD. |
| `--d-min-unit {um,px}` | Unit for `--d-min-um` (default `um`). `um` resolves via the dataset pixel size; `px` is used directly, for datasets without a pixel size. |
| `--k K` | Adaptive-clip sigma multiplier (default `1.0`; raise to be more conservative). |
| `--smallest-particle-um SMALLEST_PARTICLE_UM` | Smallest particle diameter (in `--smallest-particle-unit`) to override auto-detection. Omit to auto-detect it from the image; the largest particle is always measured (LoG). |
| `--smallest-particle-unit {um,px}` | Unit for `--smallest-particle-um` (default `um`). `um` resolves via the dataset pixel size; `px` is used directly, for datasets without a pixel size. |
| `--cnr-classify` | After the feature mask is produced, split its foci by contrast-to-noise ratio at `--cnr-threshold` into `<round>_low` / `<round>_high` masks plus a per-focus CNR table at `/classification/<round>`. Guided mode only; valid only with `--strategy adaptive-clip` or `auto-extract`. Time-lapse data is classified per timepoint (masks gain a T axis; the table gains a timepoint column). |
| `--cnr-threshold CNR_THRESHOLD` | Guided CNR split threshold (**Required** with `--cnr-classify` unless `--cnr-forced` is passed). |
| `--cnr-forced` | Forced always-2 subpopulation classification (GMM two-group split). Overrides `--cnr-threshold`: the boundary is placed by a data-driven `GaussianMixture` two-group fit regardless of the threshold value. |
| `--iterative-scope {groups,per-cell,whole-field}` | Iteration unit (default `per-cell`). `groups` reuses `--algorithm` grouping. |
| `--dilation-radius DILATION_RADIUS` | Guard-ring radius (px) removed around each captured layer (default `5`). |
| `--max-rounds MAX_ROUNDS` | Hard cap on peel iterations per dataset (default `10`). |
| `--iterations ITERATIONS` | Run EXACTLY this many peel iterations per unit, ignoring the stop criteria (blocks `--max-rounds` / `--stop-criteria` / `--stop-combine`). Omit to use the criteria-driven mode. |
| `--stop-criteria STOP_CRITERIA` | Comma-separated stopping criteria (default `bg-floor,positive-fraction-high`). One of: `bg-floor`, `separability`, `positive-fraction-high`, `min-positive`, `diminishing-returns`, `peak-prominence`, `min-area-components`. |
| `--stop-combine {any,all}` | Stop a unit when ANY (default) or ALL active criteria fire. |
| `--stop-param CRITERION.KEY=VALUE` | Override a stopping-criterion parameter (repeatable), e.g. `--stop-param bg-floor.k=2.5 --stop-param positive-fraction-high.max_frac=0.6`. |
| `--segmentation SEGMENTATION` | Existing `/labels` layer to group against. Auto-picked per dataset if omitted. |
| `--overwrite` | Overwrite an existing `/masks/<round>` instead of erroring. |
| `--verbose` | Verbose logging. |

Examples:

```bash
# Grouped Otsu (default) with k-means grouping
percell4-batch-threshold dish_1.h5 dish_2.h5 --channel GFP \
    --round-name GFP_bright --algorithm kmeans --kmeans-n-clusters 3

# GMM grouping, overwrite an existing round
percell4-batch-threshold /scratch/dishes/ --channel RFP \
    --round-name RFP_pos --algorithm gmm --gmm-criterion bic --overwrite

# Per-cell adaptive sigma-clipping puncta detector (1 µm smallest particle)
percell4-batch-threshold /scratch/dishes/ --channel GFP \
    --round-name puncta --strategy adaptive-clip --d-min-um 1.0 --k 1.0

# Iterative Otsu, peel the brightest layer per cell
percell4-batch-threshold dish_1.h5 --channel GFP \
    --round-name peel --strategy iterative-otsu --iterative-scope per-cell

# Auto-extraction detector, then split foci into low/high CNR subpopulations
percell4-batch-threshold /scratch/dishes/ --channel GFP \
    --round-name puncta --strategy auto-extract \
    --cnr-classify --cnr-threshold 2.0
```

## `percell4-batch-measure` — measure + particle analysis + CSV export

Measures per-cell metrics + particle analysis over **existing** masks and exports a timestamped run folder of CSVs/parquet (`combined.csv`, `per_dataset/*.csv`, `particles.csv`, summaries). Requires each dataset to already carry a segmentation (`/labels`) and at least one mask (`/masks`) — it does not segment or threshold. Measurements never go back into the `.h5`: results are written beneath `--output` (default cwd), and a fresh `run_<timestamp>_<id>/` subfolder is always created there.

`--mask` is repeatable and defaults to every `/masks` layer present (with a warning). Particle filtering is controlled by `--min-particle-area` + `--particle-unit`, edge-touching-cell handling by `--edge-mode` + `--edge-margin`, and the exported column set by `--csv-preset`. Column defaults are shared with the GUI workflow so CLI and GUI exports match.

```bash
percell4-batch-measure DATASETS... [--segmentation SEGMENTATION] [--mask MASKS] \
    [--min-particle-area MIN_PARTICLE_AREA] [--particle-unit {px,um2}] \
    [--edge-mode {exclude,include_as_normal,include_as_size_normalized_cohort}] \
    [--edge-margin EDGE_MARGIN] [--csv-preset {default,all}] [--output OUTPUT] [--verbose]
```

| Option | Purpose |
|---|---|
| `datasets` | One or more `.h5` files, or directories (every `*.h5` within, non-recursive). **Required.** |
| `--segmentation SEGMENTATION` | Existing `/labels` layer to measure against. Auto-picked per dataset if omitted. |
| `--mask MASKS` | Mask name to measure; repeatable. Default: every `/masks` layer present. |
| `--min-particle-area MIN_PARTICLE_AREA` | Minimum particle area; components below it are dropped. Default `0` (keep all). |
| `--particle-unit {px,um2}` | Unit for `--min-particle-area`. Choices `px`, `um2`; default `px`. |
| `--edge-mode {exclude,include_as_normal,include_as_size_normalized_cohort}` | How edge-touching cells are handled at measurement. Choices `exclude`, `include_as_normal`, `include_as_size_normalized_cohort`; default `exclude`. |
| `--edge-margin EDGE_MARGIN` | Pixel margin for the edge-cell test. Default `0` (strict border). |
| `--csv-preset {default,all}` | CSV columns: `default` (area/integrated/mean + count/total-area/mean-intensity) or `all` (every metric). |
| `--output OUTPUT` | Parent directory for the timestamped run folder (default cwd). A new `run_<timestamp>_<id>/` subfolder is always created beneath it. |
| `--verbose` | Verbose logging. |

Examples:

```bash
percell4-batch-measure dish_1.h5 dish_2.h5 --segmentation cellpose \
    --mask pbody --min-particle-area 9 --output ~/runs
percell4-batch-measure /scratch/dishes/ --mask grouped --csv-preset all

# Filter particles in microns² and keep edge cells as a size-normalized cohort
percell4-batch-measure /scratch/dishes/ --mask grouped \
    --min-particle-area 0.5 --particle-unit um2 \
    --edge-mode include_as_size_normalized_cohort --edge-margin 5
```

## `percell4-inspect` — print dataset metadata + layers

Read-only triage: prints each dataset's file size, metadata (channels, resolution, pixel size, timepoints), the free-text [experiment description](#dataset-descriptions), and every layer (intensity, segmentations, masks, groups, tracks) with name/shape/dtype. Shapes and dtypes are read straight from the HDF5 headers without decoding arrays, so it stays fast even on multi-gigabyte stacks.

It mutates nothing — no file is opened for writing and nothing is staged back into the `.h5`. Directory arguments expand to every `*.h5` they contain (non-recursive). Pass `--json` to emit a machine-readable JSON array of per-dataset records instead of the human-readable text report.

`--grep` turns it into a search: only datasets whose description contains the given text are reported, so you can find the right dataset in a folder of many without opening any of them in the launcher. Matching is case-insensitive and matches anywhere in the description; datasets with no description never match. A filter that matches nothing exits `1`.

```bash
percell4-inspect DATASETS [DATASETS ...] [--json] [--grep TEXT]
```

| Option | Purpose |
|---|---|
| `datasets` | One or more `.h5` files, or directories (every `*.h5` within, non-recursive). **Required** — at least one. |
| `--json` | Emit a JSON array of per-dataset records instead of human-readable text. |
| `--grep TEXT` | Report only datasets whose description contains `TEXT` (case-insensitive substring). Applies to both output modes. Exits `1` when nothing matches. |

Examples:

```bash
# Human-readable inventory for two datasets
percell4-inspect dish_1.h5 dish_2.h5

# JSON records for every .h5 in a directory
percell4-inspect /scratch/dishes/ --json

# Which of these dishes were PFA-fixed?
percell4-inspect /scratch/dishes/ --grep PFA
```

## Dataset descriptions

Every `.h5` dataset can carry one free-text **description** — the sample, how it was prepared, the experimental condition, or anything else that makes the dataset recognisable weeks later. It is stored inside the file, so it survives copying and moving, and it is the answer to "which of these twelve dishes am I looking at?" when the filename no longer tells you.

Three surfaces read and write it:

- **The launcher's Data tab** shows the loaded dataset's description read-only under **Dataset Info**, and the **Description → Edit…** control in **Dataset Management** opens an editor for it.
- **[`percell4-batch-describe`](#percell4-batch-describe--set-the-experiment-description-across-datasets)** sets, appends to, or clears it across one file or a whole folder.
- **[`percell4-inspect`](#percell4-inspect--print-dataset-metadata--layers)** prints it, and `--grep` searches a folder by it.

---

## Development harnesses

Two of the fourteen console scripts are **development and validation harnesses, not analysis commands**. They install on `PATH` alongside everything else — which is why they are documented here rather than left to be discovered by accident — but they answer method-development questions ("which detector should this project trust?", "what multiplier should the auto-window finder use?") rather than producing measurements for a paper. Nothing you would run over a folder of dishes on a Friday night.

They exist because the choice of a detector and of a window-size heuristic are the two places where an ad-hoc decision would silently propagate into every downstream number. Turning both choices into a scored, reproducible race — against exhaustively hand-labeled ground truth in one case and an IoU oracle in the other — is what lets the project state a detector benchmark instead of an opinion. The puncta harness below is how that benchmark was produced (see `docs/methods/headless-puncta-thresholding.md`).

Both harnesses differ from the batch tools above: they take **one dataset argument set and no `--dry-run` / `--quiet`**, they accept `--verbose`, `-v`, and their exit codes report a *decision* rather than per-dataset progress. Both also run as modules (`python -m percell4.interfaces.cli.batch_validate_puncta …`) without reinstalling.

## `percell4-batch-validate-puncta` — race puncta detectors against ground truth

Races puncta-detection methods against hybrid ground truth over a grid of parameters, ranks them by F-beta, and — when a method clears the bar — locks the winning `PunctaDetectorSettings` to JSON so the exact operating point is reproducible.

Inputs are one dataset `.h5` (whose `/labels/<seg-name>` and channel feed the detection path) plus a directory of **Tier-A** napari-point CSVs (`y, x` columns, optional `field`): the exhaustive recall ceiling and the only source of precision. An optional approved **Tier-B** `/masks/<name>` supplies a recall *floor*, scored at the first `--tol` value. The sweep is the Cartesian product of `--detectors` × `--backgrounds` × `--k` × `--tol` × `--threshold-rel` × `--min-spot-px`.

The harness is a guardrail, not the selector: its score is centroid-based, so it confirms a method finds foci in roughly the right places and is stable, but it cannot see per-granule pixel shape or dilute-phase pickup. It narrows the field; the final operating point is chosen by visual spot-test over the candidate `/masks`.

**Exit codes:** `0` when a method qualified and was locked, `1` when nothing cleared the bar (keep interactive QC) or on a load error.

```bash
percell4-batch-validate-puncta DATASET --gt-dir DIR --channel CHANNEL [options]
```

| Option | Purpose |
|---|---|
| `dataset` | Path to the dataset `.h5` file (positional, **required**). |
| `--gt-dir GT_DIR` | Directory of Tier-A napari-point CSVs (`y,x` columns). **Required.** |
| `--channel CHANNEL` | Channel name to detect on (must exist in `/metadata.channel_names`). **Required.** |
| `--seg-name SEG_NAME` | Segmentation label set name. Default: `cellpose_qc`. |
| `--field-name FIELD_NAME` | Tier-A field name this dataset corresponds to. Default: the dataset file stem. |
| `--tier-b-mask TIER_B_MASK` | Existing approved `/masks/<name>` to use as the recall floor, scored at the first `--tol` value. |
| `--detectors DETECTORS [DETECTORS ...]` | Detector names to sweep. Default: `log`. |
| `--backgrounds BACKGROUNDS [BACKGROUNDS ...]` | Background-estimator names to sweep. Default: `gaussian-peak`. |
| `--seed-detector SEED_DETECTOR` | Detector for the permissive pass-1 seed step. Default: `log`. Set to `otsu` to run Otsu on both passes. |
| `--k K [K ...]` | Sigma multiplier(s) for the gate / `bg-k-sigma` / h-maxima. Default: `2.5`. |
| `--threshold-rel THRESHOLD_REL [THRESHOLD_REL ...]` | `log`/`dog` `threshold_rel` values to sweep — the multiscale recall knob (lower = more detections = higher recall). Default: `0.1`. |
| `--tol TOL [TOL ...]` | Matching tolerance (px) values to sweep. Default: `4.0`. Keep it fixed for an apples-to-apples lock. |
| `--scale-min SCALE_MIN` | `min_sigma` of the scale range. Default: `1.0`. |
| `--scale-max SCALE_MAX` | `max_sigma` of the scale range. Default: `4.0`. |
| `--min-spot-px MIN_SPOT_PX [MIN_SPOT_PX ...]` | Minimum spot area in pixels to sweep (filters noise specks). Default: `2`. |
| `--precision-floor PRECISION_FLOOR` | Minimum Tier-A precision required to lock. Default: `0.9`. |
| `--beta BETA` | F-beta beta (recall weight). Default: `2.0`. |
| `--out OUT` | Write the locked `PunctaDetectorSettings` JSON here when a method locks. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Minimal race: default log detector, Tier-A ground truth only
percell4-batch-validate-puncta DS1.h5 --gt-dir labels/ --channel GFP

# Add a Tier-B recall floor and write the locked settings
percell4-batch-validate-puncta DS1.h5 --gt-dir labels/ --channel GFP \
    --tier-b-mask old_qc --out locked.json

# The sweep that produced the project's detector benchmark
percell4-batch-validate-puncta DS1.h5 \
    --gt-dir labels/ --channel mNG --seg-name cp_mask \
    --tier-b-mask old_qc \
    --detectors log dog --backgrounds gaussian-peak \
    --threshold-rel 0.04 0.05 0.06 --tol 4 \
    --scale-min 1.0 --scale-max 4.0 --out locked.json
```

## `percell4-window-bakeoff` — score auto-window-size finders against the SG-mask oracle

Bakes off the auto-window-size finders used by the adaptive-clipping detector. For every dataset that carries a hand-approved `/masks/SG_mask` ground truth, the IoU-argmax window over `--window-grid` is the **oracle** target; each finder's `auto_window` is then scored by `|auto − ideal|` plus its own mask IoU and recall. `k` is pinned for the whole run and recorded in the report, so a bake-off compares window choice and nothing else.

`--c` overrides the `granule-size` finder's multiplier, which is how that constant gets calibrated rather than guessed. `--holdout` names the fields reserved for validation; every non-holdout score is flagged `in_sample` and the printed table says so, because a finder calibrated and scored on the same field is not evidence. A flat IoU peak is flagged too — it means the oracle itself did not discriminate.

**Exit codes:** `0` when at least one labeled field was scored, `1` when no `SG_mask` was found anywhere or a dataset failed to load.

```bash
percell4-window-bakeoff DATASETS --channel CHANNEL [options]
```

| Option | Purpose |
|---|---|
| `datasets` | One or more dataset `.h5` files (positional, **required**). |
| `--channel CHANNEL` | Channel name to detect on (e.g. `G3BP1`). **Required.** |
| `--finders FINDERS [FINDERS ...]` | Finder names to bake off. Default: every registered finder. An unknown name exits `1` and lists the known ones. |
| `--window-grid WINDOW_GRID [WINDOW_GRID ...]` | Window sizes for the oracle sweep (forced odd). Default: `15 31 51 71 91 111 131`. |
| `--k K` | Pinned detector `k` for the whole run. Default: `3.0`. |
| `--gaussian-sigma GAUSSIAN_SIGMA` | Pre-smooth sigma applied when loading each field. Default: `1.0`. |
| `--sg-mask-name SG_MASK_NAME` | Ground-truth mask name under `/masks`. Default: `SG_mask`. |
| `--cp-name CP_NAME` | Cell-labels name used to restrict scoring to in-cell pixels. Default: none (whole field). |
| `--holdout [HOLDOUT ...]` | Field names held out for validation; non-holdout scores are flagged in-sample. |
| `--c C` | Override the `granule-size` finder's multiplier `c` — the calibration knob. |
| `--out OUT` | Write the full report (oracles, IoU/recall curves, per-field scores, ranking) as JSON to this path. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Sweep the default grid on one labeled dataset and keep the full report
percell4-window-bakeoff DS.h5 --channel G3BP1 --k 3.0 \
    --window-grid 15 31 51 71 91 111 131 --out report.json

# Two datasets, in-cell scoring, one held out, with a calibrated multiplier
percell4-window-bakeoff A.h5 B.h5 --channel G3BP1 --cp-name cp_mask \
    --finders otsu-mean granule-size --holdout B --c 4.5
```

---

## Modules without an entry point

Two modules in `src/percell4/interfaces/cli/` are not console scripts, and are not meant to be:

- **`run_pipeline.py`** — an importable headless pipeline (`from percell4.interfaces.cli.run_pipeline import run_pipeline`) that drives load → segment → threshold → measure through the same `Session`, use cases, and repository the GUI uses, with a `NullViewerAdapter` standing in for napari. It is deliberately **not** wired to a console script: its job is to prove the hexagonal seam is real — no Qt and no napari anywhere in its import chain — rather than to be a user-facing command. Run it as `python -m percell4.interfaces.cli.run_pipeline dataset.h5 …` if you want to exercise it.
- **`catalog.py`** — the runtime catalog behind the in-app **Batch Tools Console**. It enumerates the installed `percell4-*` console entry points (dropping any whose module no longer imports, so a stale install never lists a phantom tool) and resolves a typed command line into `[sys.executable, "-m", <module>, *args]`. That is why the console runs batch tools in the *current* virtual environment regardless of what is on `PATH`, and why every command on this page has an equivalent `python -m percell4.interfaces.cli.<module>` form.

---

Back to the [README](../README.md).
