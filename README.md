<p align="center">
  <img src="art/percell4_logo.png" width="200" alt="PerCell4 logo">
</p>

# PerCell4

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](#installation)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#license)

**Single-cell FLIM microscopy analysis platform** — Cellpose segmentation, per-cell measurements, grouped thresholding, and phasor workflows in one Qt desktop app. Each experiment is one HDF5 file; results land as parquet + CSV ready for downstream analysis.

## Table of Contents

- [Workflow Protocol](#workflow-protocol)
  - [Step-by-step protocol](#step-by-step-protocol)
- [Command-line Tools](#command-line-tools)
  - [`percell4-batch-cellpose-laptrack` — headless compress + segment + track](#percell4-batch-cellpose-laptrack--headless-compress--segment--track)
  - [`percell4-batch-export` — TIFF export](#percell4-batch-export--tiff-export)
  - [`percell4-batch-phasor` — compute phasor + wavelet filter](#percell4-batch-phasor--compute-phasor--wavelet-filter)
  - [`percell4-batch-phasor-masks` — fit GMM ellipse + write dual-threshold masks](#percell4-batch-phasor-masks--fit-gmm-ellipse--write-dual-threshold-masks)
  - [`percell4-batch-whole-field` — whole-field segmentation](#percell4-batch-whole-field--whole-field-segmentation)
  - [`percell4-batch-rename` — rename a resource across datasets](#percell4-batch-rename--rename-a-resource-across-datasets)
  - [`percell4-batch-delete` — delete resources across datasets](#percell4-batch-delete--delete-resources-across-datasets)
  - [`percell4-batch-threshold` — headless grouped thresholding](#percell4-batch-threshold--headless-grouped-thresholding)
  - [`percell4-batch-measure` — measure + particle analysis + CSV export](#percell4-batch-measure--measure--particle-analysis--csv-export)
  - [`percell4-inspect` — print dataset metadata + layers](#percell4-inspect--print-dataset-metadata--layers)
- [Tech Stack](#tech-stack)
- [Features](#features)
- [Changelog](#changelog)
- [Installation](#installation)
  - [macOS](#macos)
  - [Linux](#linux)
  - [Windows](#windows)
- [Install from a wheel](#install-from-a-wheel)
- [Optional extras](#optional-extras)
- [Standalone bundle (PyInstaller)](#standalone-bundle-pyinstaller)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Workflow Protocol

The following protocol is a general-purpose workflow for single-cell segmentation, mask generation, and particle analysis. Image data from this workflow are saved as "datasets" in the form of HDF5 files, which can be exported as `.tiff` files for downstream analysis using Python or R scripts. Analyses are saved as `.csv` files that can also be used for graphing and statistics in Python or R.

### Step-by-step protocol

1. **Launch the app.**

   On a **Mac**, open Terminal and run:
   ```bash
   cd ~/percell4
   source .venv/bin/activate
   python main.py
   ```

   On the **Lee Lab analysis PC** (Windows), press `Windows + R`, type `cmd`, and press Enter to open Command Prompt. Then run:
   ```bat
   E:
   cd percell4
   .venv\Scripts\activate
   python main.py
   ```

   The PerCell4 launcher window opens.

2. **Open the workflow.**
   Click the **Workflows** tab in the launcher sidebar. Click the **Single-cell thresholding analysis workflow** button. A setup window opens.

3. **Add your datasets.**
   Click the **.tiff file icon** in the Datasets panel of the setup window. A new window called **Compress TIFF Dataset** opens. In the Source panel at the top, click **Browse...** next to the Directory field and select a folder containing `.tiff` files exported from LASX. The Output field defaults to one level up from the folder containing the `.tiff` files; this is where the dataset will be saved. To change the output folder, click **Browse...** next to the Output field and create or choose a different folder.

   Next, change the Discovery field from **Subdirectory** to **Flat Directory**. You should see a list of file names matching the LASX file names. Channels will be the channel tokens created by LASX at export. To rename them, switch the discovery mode to **Manual** and type your desired channel name. Z-series stacks are automatically projected to a single image; the default is `MIP` (Maximum Intensity Projection). Tiles of a tile scan can be stitched together by checking the **Tile Stitching** box. The LASX default pattern is snake-by-row starting at the top-left, but adjust the stitching orientation if needed. For overlapping tiles, set the **Overlap %** and check **Register overlapping tiles** to phase-correlate the overlap and correct for stage drift; pick a **Reference** channel for the solve (any imported channel, including one renamed in Manual mode). Choose a **Fusion** mode for the overlap regions: **None** keeps each pixel from a single tile (no intensity distortion — required and auto-selected when the dataset has FLIM decay), or **Linear Blending** for a seamless display mosaic. Click **Compress** at the bottom of the window.

   The Compress TIFF Dataset window closes and the new dataset is added to the Datasets table. Repeat for every experiment you want to include in this run.

4. **Configure Cellpose.**
   Select the channel with the strongest cytoplasmic signal as the segmentation channel. The default settings work for most datasets. The default 300 px diameter corresponds to ~30 µm at optimal resolution on a 1.4 NA objective and suits most cells. For larger- or smaller-than-average cells, adjust the diameter accordingly.

5. **Choose the edge-cell mode.** Pick one of three options for how to handle cells touching the image border:
   - **exclude** (default) — discard edge cells
   - **include_as_normal** — keep edge cells like any other cell
   - **include_as_size_normalized_cohort** — keep edge cells and analyze them together as one group, sized relative to the average non-edge cell in the same dataset

6. **Define the thresholding rounds.** Each "round" produces one mask per cell — for example, one round for P-bodies and another for stress granules. For each round you want to run:
   - Click **Add round** in the Thresholding rounds table.
   - Name the round (e.g., `P-body_mask`).
   - Pick the target channel from the dropdown list.
   - **Metric** — `median_intensity` works best for most condensate proteins.
   - **Grouping algorithm** — use `gmm` with at least 10 groups.
   - **Sigma (σ)** — applies a Gaussian blur to the image before segmentation, useful for noisy images. Sigma sets a radius around each pixel in standard deviations (not pixels).

   Add as many rounds as you need. The workflow runs them in the order shown in the table.

7. **Include particle analysis.**
   The **Include particle analysis** box is checked by default. When it is checked, the app counts and measures particles (e.g., puncta) inside each cell for every thresholding round. Set:
   - **Min particle area** — the smallest particle the app will keep. Anything smaller is treated as noise and dropped. Pick the unit on the right: **px** (pixels — the same threshold is used for every dataset) or **µm²** (square microns — converted per dataset using each TIFF's pixel size). Leave at `0` to keep every particle, including single-pixel ones.

   Uncheck the **Include particle analysis** box if you do not want particle analysis.

8. **(Optional) Enable the dilute-phase mask.**
   Check **Generate dilute-phase mask** if you want a dilute-phase mask generated in this run. Then set:
   - **Dilute mask name** — must be different from every thresholding round name (the app will not let the run start until you fix it).
   - **Dilation radius** in pixels — used every dilute round.
   - Use the same grouping and filter settings you would use for grouped thresholding.

9. **Pick output columns and the output folder.**
   In the **Output** group of the setup window, choose which measurement columns to include in your results files. Pick the output folder — the app creates a new subfolder named with the date and time of the run.

10. **Start the run.**
    Click **Start**. The app first compresses your TIFFs into datasets, then runs Cellpose to find every cell in every dataset. You do not need to do anything during this part — watch progress in the launcher status bar.

11. **Review the cell outlines (your input needed).**
    When Cellpose finishes, the Viewer window opens with the first dataset. Cell outlines are shown on top of your image. Refine them if needed:
    - Click the cell outlines layer in the layer list on the left, then use the paint, erase, and fill tools above the image.

    Click **Accept** to move on to the next dataset. Repeat for every dataset.

12. **Review each thresholding mask (your input needed).**
    For each thresholding round, a review window opens for the first dataset. The proposed mask is shown on top of the target channel. Either:
    - Click **Accept** to keep the proposed mask, or
    - Draw a circular region on the image to guide refinement, then click **Accept** — the app recalculates the mask using only that region.

    Repeat for every dataset, then for every round.

13. **(Optional) Build the dilute-phase mask (your input needed).**
    If you enabled the dilute-phase mask in step 8, the dilute window opens for the first dataset. For each dataset:
    - Click **Compute** to generate the proposed condensed mask.
    - A review window opens — look over the mask and click **Accept** to keep this round.
    - The accepted mask is automatically expanded slightly and removed from the input for the next round.
    - Click **Another round** to refine further on the same dataset, or **Done** to move on to the next dataset.

    Different datasets may need different numbers of rounds. The final mask is saved when you click **Done**.

14. **Wait for the app to measure and save your results.**
    The app measures every cell across every segmentation and mask, then saves the results. You do not need to do anything during this part.

15. **Find your results.**
    Open the output folder you chose in step 9. Inside, find a new folder named with the date and time of the run. It contains:
    - `combined.csv` — every cell from every dataset in one spreadsheet (open this in Excel, Numbers, or Google Sheets).
    - `per_dataset/<DS>.csv` — one spreadsheet per dataset.
    - `summary_groups.csv` — one row per dataset × round × group, with means, medians, standard deviations, and cell counts.
    - `summary_datasets.csv` — one row per dataset with edge-cell mode, round counts, and any failure reasons.
    - `measurements.parquet` — the same data as `combined.csv`, in a compact format for Python or R users.

**Pausing and resuming.** The app saves its progress after each step. To pick up an interrupted run, open the launcher, click the **Workflows** tab, and click **Resume run...** instead of starting a new workflow.

**Headless TIFF export.** If you only need `.tiff` files out of an existing dataset — for ImageJ, custom downstream scripts, or sharing with a colleague — use the command-line tool documented in the next section.

---

## Command-line Tools

PerCell4 ships several headless CLI tools for batch operations across `.h5` datasets. All of them install on `PATH` from `pip install -e .` and share these conventions:

- **Positional `paths`** accept one or more `.h5` files or directories. Directories are globbed non-recursively for `*.h5`.
- **`--dry-run`** (where supported) classifies each dataset as a live run would but does not mutate files. Use it on destructive operations to audit what will change.
- **`--quiet`** suppresses per-item detail lines. The per-dataset summary and final totals always print.
- **`--verbose` / `-v`** enables DEBUG logging.
- **Exit codes:** `0` if at least one dataset made progress, `1` if every dataset was skipped or failed, `2` on argparse / validation failure (no I/O performed).
- **GUI files first.** Close any open PerCell4 GUI session against the target files before running — the batch tools write to the same `.h5` files the GUI reads.

### `percell4-batch-cellpose-laptrack` — headless compress + segment + track

End-to-end headless pipeline for multi-timepoint datasets. Each source is either a **TIFF source directory** (compressed into one `.h5`) or an **already-compressed `.h5`** (the compress step is skipped). Runs Cellpose on every timepoint and tracks cells across time (unless `--no-track`). Exposes the full GUI Segment-tab Cellpose controls so headless runs reproduce interactive tuning. Designed for overnight batch runs on a remote workstation.

```bash
percell4-batch-cellpose-laptrack SOURCES [--output-dir DIR] [options]
```

**Inputs.** TIFF directory sources require `--output-dir` (each `<source_dirname>.h5` lands there). For an `.h5` source, omit `--output-dir` to **segment it in place**, or pass `--output-dir` to **copy it there first** and segment the copy (the original is left untouched).

| Option | Purpose |
|---|---|
| `sources` | One or more dataset TIFF source directories and/or `.h5` files (positional, required). |
| `--output-dir OUTPUT_DIR` | Directory for the output `.h5` files. **Required for TIFF directory sources.** For `.h5` sources: omit to segment in place, or give it to copy-then-segment. |
| `--seg-channel SEG_CHANNEL` | Channel name to segment. Default: first channel. Matched against `--channel-names` when given. |
| `--channel-names CHANNEL_NAMES` | Comma-separated names to rename the imported channels, in order (e.g. `'DAPI,GFP,RFP'`). Must match the imported channel count. |
| `--seg-name SEG_NAME` | Name for the segmentation layer. Default: `cellpose_<n_cells>`. With `--skip-segmentation`, this is the **existing** segmentation layer to track (required). |
| `--skip-segmentation` | Skip Cellpose and only run laptrack on an existing segmentation. Requires `--seg-name` (the existing `(T,H,W)` layer) and a time-lapse dataset. |
| `--cellpose-model {cpsam,cyto3,cyto2,cyto,nuclei}` | Cellpose model. Default: `cpsam`. Ignored on Cellpose 4.x (cpsam is the only model). |
| `--cellpose-diameter CELLPOSE_DIAMETER` | Cell diameter in pixels; `0` = auto-detect. Default: `30`. |
| `--gpu` | Use GPU for Cellpose (requires the `gpu` extra and a working CUDA driver). |
| `--flow-threshold FLOW_THRESHOLD` | Flow error threshold; higher = more permissive. Default: `0.4`. |
| `--cellprob-threshold CELLPROB_THRESHOLD` | Cell probability threshold. Default: `0.0`. |
| `--min-size MIN_SIZE` | Minimum cell size in pixels. Default: `15`. |
| `--saturation SATURATION` | Saturation % for an ImageJ-style Enhance Contrast LUT applied to the segmentation channel before Cellpose; `0` disables. Default: `1.0`. The on-disk `/intensity` is never modified. |
| `--blur-sigma BLUR_SIGMA` | Gaussian blur sigma applied after the saturation LUT and before Cellpose; `0` disables. Default: `0.0`. The on-disk `/intensity` is never modified. |
| `--no-remove-edge-cells` | Keep cells touching the image border (default: remove them). |
| `--edge-margin EDGE_MARGIN` | Pixels from the border counted as edge when removing edge cells. Default: `0` (strict border-touching). |
| `--no-track` | Skip tracking even for time-lapse datasets. |
| `--quiet` | Suppress per-dataset progress lines. |
| `--verbose`, `-v` | Timestamped DEBUG stream plus per-frame/timing detail; lifts the Cellpose and laptrack loggers to INFO so their native progress (device, model load, per-image cell counts and timing, linking) is surfaced. |

The Cellpose defaults match the GUI Segment tab, so the same settings produce the same segmentation interactively and headlessly.

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

### `percell4-batch-export` — TIFF export

Export dataset layers as TIFFs across one or more `.h5` files. The GUI equivalent lives at `I/O` → **Export Images**.

```bash
percell4-batch-export PATHS --output-dir DIR [options]
```

For each dataset, writes one TIFF per intensity channel, per `/labels/<name>`, and per `/masks/<name>` into `--output-dir` using a flat `<h5_stem>_<layer>.tif` layout. Existing files with matching names are overwritten — point `--output-dir` at a fresh directory to preserve prior runs. Phasor, lifetime, and decay arrays are NOT exported.

| Option | Purpose |
|---|---|
| `--output-dir DIR`, `-o DIR` | Target directory for the `.tif` outputs. Created if missing. **Required.** |
| `--view-bin N` | Bin factor applied to every layer at read time. Default `1` (native resolution). `N > 1` produces downsampled TIFFs using the same lens the GUI applies for `view_bin=N` (`sum_bin_2d` for intensity, `mode_labels` for `/labels`, `majority_vote_mask` for `/masks`). `N` must be an integer `>= 1`. Output filenames are unchanged regardless of bin — track the value yourself (e.g. `--output-dir out_bin4/`) if you mix runs. |
| `--quiet` | Suppress per-dataset error detail lines. Status headers and final totals still print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Native-resolution export of two datasets
percell4-batch-export dish_1.h5 dish_2.h5 --output-dir /tmp/exports

# Every .h5 in a directory, downsampled to match the GUI's view-bin 4 lens
percell4-batch-export /scratch/dishes/ --output-dir ~/exports/ --view-bin 4
```

### `percell4-batch-phasor` — compute phasor + wavelet filter

For every channel under `/decay/*`, computes the phasor `(g, s)` maps and applies the wavelet filter, writing `/phasor/<ch>/g`, `/s`, `g_filtered`, `s_filtered`, and `lifetime_filtered`. Channels with an existing `/phasor/<ch>/g` are skipped unless `--overwrite`. Channels missing calibration (`flim_cal_phase_<ch>`, `flim_cal_mod_<ch>`, `flim_frequency_mhz`) are skipped with a clear report line.

```bash
percell4-batch-phasor PATHS [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. |
| `--filter-level FILTER_LEVEL` | Wavelet filter level (1..9). Default: `9`. |
| `--overwrite` | Recompute channels even when `/phasor/<ch>/g` already exists. Default: skip. |
| `--remove` | Inverse mode: delete `/phasor/<ch>/` (all of g, s, g_filtered, s_filtered, lifetime_filtered) for every channel in each dataset instead of computing. Mutually exclusive with `--overwrite`. `--filter-level` is ignored when `--remove` is set. |
| `--quiet` | Suppress per-channel skip / error detail lines. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
percell4-batch-phasor dish_1.h5 dish_2.h5
percell4-batch-phasor /scratch/dishes/ --filter-level 5
percell4-batch-phasor *.h5 --overwrite --quiet
percell4-batch-phasor /scratch/dishes/ --remove
```

### `percell4-batch-phasor-masks` — fit GMM ellipse + write dual-threshold masks

For each requested channel of each dataset, fits a single-cluster GMM ellipse on the phasor cloud above `--t-fit`, then writes two intensity-thresholded ellipse-membership masks (`--t-mask-a → suffix-a`, `--t-mask-b → suffix-b`). Reads unfiltered `/phasor/<ch>/g` and `/s` — wavelet-filtered maps are never used here, matching the manual recipe. When a dataset lacks pre-computed phasor maps, they are computed on the fly using the same primitives `percell4-batch-phasor` uses.

Up-front validation: every requested channel must be present in every dataset; suffixes must be non-empty and must differ; no mask name may collide with an existing channel name in any dataset. Validation failures exit `2` without performing any I/O.

```bash
percell4-batch-phasor-masks PATHS --channels CHANNELS [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. |
| `--channels CHANNELS [CHANNELS ...]` | One or more channel names to fit. Every channel must be present (in both `metadata.channel_names` and `/decay/`) in every input dataset. **Required.** |
| `--t-fit T_FIT` | Intensity threshold defining the GMM fit subset. Default: `10.0`. |
| `--t-mask-a T_MASK_A` | Intensity threshold applied to mask-a after the ellipse-membership step. Default: `0.0`. |
| `--t-mask-b T_MASK_B` | Intensity threshold applied to mask-b after the ellipse-membership step. Default: `5.0`. |
| `--suffix-a SUFFIX_A` | Suffix appended to each channel name for mask-a. Default: `_phasor_1`. |
| `--suffix-b SUFFIX_B` | Suffix appended to each channel name for mask-b. Default: `_phasor_5`. |
| `--roi-source TARGET=SOURCE` | Use `SOURCE`'s fitted ROI for `TARGET`. Repeat the flag for multiple targets. `SOURCE` must itself be self-fitting (not appear as a target in any other `--roi-source`). Treatment-group comparisons (e.g. Untreated → As-treated) use this to share a single ROI across the cohort. |
| `--dry-run` | Print the planned operations and exit 0 without performing any phasor / mask I/O. |
| `--quiet` | Suppress per-channel skip / error detail lines. |
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

# Audit a planned run without writing
percell4-batch-phasor-masks /scratch/dishes/ --channels mNG --t-fit 20.0 --dry-run
```

### `percell4-batch-whole-field` — whole-field segmentation

Creates `/labels/whole_field` (a 2D `int32` array, every pixel = 1) in each input dataset. Useful as a baseline gating layer for whole-field measurements or as a default segmentation before per-cell Cellpose runs. Shape is taken from `/metadata.native_shape`, falling back to the first `/decay/<channel>` if absent. Pre-existing `/labels/whole_field` is silently overwritten.

```bash
percell4-batch-whole-field PATHS [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. |
| `--dry-run` | Classify each dataset as succeeded / failed exactly as a live run would, but do not mutate any file. |
| `--quiet` | Suppress per-resource detail lines. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
percell4-batch-whole-field dish_1.h5 dish_2.h5
percell4-batch-whole-field /scratch/dishes/ --dry-run
```

### `percell4-batch-rename` — rename a resource across datasets

Renames `(kind, old_name) → new_name` in each input `.h5`. Datasets that don't have the source name are reported as skipped, not failed. Datasets where the target name already exists are recorded as per-dataset errors — the batch continues to the next file.

Channel renames go through `DatasetStore.rename_channel`, which moves `/decay/<name>`, `/phasor/<name>`, and updates `/metadata.channel_names` plus the per-channel FLIM calibration attrs together. Masks and segmentations go through `DatasetStore.rename_item` against `/masks/<name>` and `/labels/<name>` respectively.

```bash
percell4-batch-rename PATHS --kind {channel,mask,segmentation} --from-name OLD --to-name NEW [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. |
| `--kind {channel,mask,segmentation}` | Resource kind to rename. **Required.** |
| `--from-name FROM_NAME` | Current name of the resource in each `.h5`. **Required.** |
| `--to-name TO_NAME` | New name to rename the resource to. **Required.** |
| `--dry-run` | Classify each dataset as succeeded / skipped / failed exactly as a live run would, but do not mutate any file. |
| `--quiet` | Suppress per-resource skip / error detail lines. |
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

### `percell4-batch-delete` — delete resources across datasets

Deletes either a single named resource (`--name`) or every resource of the given kind (`--all`) in each input `.h5`. The two flags are mutually exclusive and exactly one is required. Datasets that don't have the resource are reported as skipped, not failed.

Channel deletes go through `DatasetStore.delete_channel`, which removes `/decay/<name>`, `/phasor/<name>`, the `channel_names` metadata entry, and the per-channel FLIM calibration attrs together. Masks and segmentations go through `DatasetStore.delete_item`. Use `--dry-run` first on destructive operations to audit what would change.

```bash
percell4-batch-delete PATHS --kind {channel,mask,segmentation} (--name NAME | --all) [options]
```

| Option | Purpose |
|---|---|
| `paths` | One or more `.h5` files, or directories containing `.h5` files. |
| `--kind {channel,mask,segmentation}` | Resource kind to delete. **Required.** |
| `--name NAME` | Name of the resource to delete in each `.h5`. Mutually exclusive with `--all`. |
| `--all` | Delete every resource of the given `--kind` found in each `.h5`. Channels are enumerated from `metadata.channel_names`; masks from `/masks/*`; segmentations from `/labels/*`. Mutually exclusive with `--name`. Combine with `--dry-run` to audit which resources will be removed before running. |
| `--dry-run` | Classify each dataset as succeeded / skipped / failed exactly as a live run would, but do not mutate any file. |
| `--quiet` | Suppress per-resource skip / error detail lines. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Delete one named resource per dataset
percell4-batch-delete dish_1.h5 dish_2.h5 \
    --kind segmentation --name cellpose_qc
percell4-batch-delete /scratch/dishes/ \
    --kind mask --name thresh_488 --dry-run

# Delete EVERY resource of a kind
percell4-batch-delete /scratch/dishes/ --kind mask --all --dry-run
percell4-batch-delete *.h5 --kind channel --all
```

---

### `percell4-batch-threshold` — headless grouped thresholding

Runs one grouped-threshold round (k-means/GMM grouping + per-group Otsu) across datasets and writes `/masks/<round>` + `/groups/<round>` back into each `.h5`. Requires each dataset to already carry a segmentation (`/labels`); it does not segment, measure, or export. Pair it with `percell4-batch-measure` to get CSVs (it prints the exact follow-up command on success).

```bash
percell4-batch-threshold dish_1.h5 dish_2.h5 --channel GFP \
    --round-name GFP_bright --algorithm kmeans --kmeans-n-clusters 3
percell4-batch-threshold /scratch/dishes/ --channel RFP \
    --round-name RFP_pos --algorithm gmm --gmm-criterion bic --overwrite
```

Every round option is a flag (`--metric`, `--algorithm`, `--gmm-criterion`, `--gmm-max-components`, `--kmeans-n-clusters`, `--gaussian-sigma`, `--segmentation`). It refuses to overwrite an existing same-name mask unless `--overwrite` is passed.

---

### `percell4-batch-measure` — measure + particle analysis + CSV export

Measures per-cell metrics + particle analysis over **existing** masks and writes a timestamped run folder of CSVs/parquet (`combined.csv`, `per_dataset/*.csv`, `particles.csv`, summaries). Requires existing `/labels` + `/masks`; measurements never go back into the `.h5`.

```bash
percell4-batch-measure dish_1.h5 dish_2.h5 --segmentation cellpose \
    --mask pbody --min-particle-area 9 --output ~/runs
percell4-batch-measure /scratch/dishes/ --mask grouped --csv-preset all
```

`--mask` is repeatable (default: every mask present, with a warning). Particle filtering via `--min-particle-area` + `--particle-unit {px,um2}`; CSV columns via `--csv-preset {default,all}`. Shares its column defaults with the GUI workflow so CLI and GUI exports match.

---

### `percell4-inspect` — print dataset metadata + layers

Read-only triage: prints each dataset's file size, metadata (channels, resolution, pixel size, timepoints), and every layer (intensity, segmentations, masks, groups, tracks) with name/shape/dtype. Shapes/dtypes are read without decoding arrays, so it is fast even on multi-gigabyte stacks.

```bash
percell4-inspect dish_1.h5 dish_2.h5
percell4-inspect /scratch/dishes/ --json
```

---


## Tech Stack

- **GUI:** Qt (PyQt5 + qtpy), napari (`>=0.5,<0.8`), pyqtgraph (`>=0.13,<0.15`)
- **Data:** HDF5 via h5py (`>=3.10,<4`), pandas (`>=2.0,<3`), pyarrow (`>=14`)
- **Imaging:** numpy (`>=1.26`), scikit-image (`>=0.22`), scipy (`>=1.12`), tifffile, sdtfile (Becker & Hickl FLIM)
- **Segmentation:** Cellpose (`>=3.0,<5.0`), scikit-learn
- **CLI:** click (`>=8.1`), rich (`>=13.0`)
- **Python:** 3.12 or newer

Dependency versions are pinned in `pyproject.toml`. Optional extras (`gpu`, `flim`, `imagej`, `all`) are documented under [Optional extras](#optional-extras).

---

## Features

- **HDF5-backed projects.** One `.h5` per experiment holds intensity channels, segmentation labels, masks, phasor maps, and measurement staging — no separate database, no scattered files.
- **Overlap-aware tile stitching.** Stitch tile scans at import with phase-correlation registration on the tile *overlap region* (Fiji/ImageJ-style) — solved once on a reference channel and reused for every channel and the FLIM decay stream. Falls back to a nominal-overlap grid when a channel can't register, and offers **None** (measurement-correct, forced for FLIM) or **Linear Blending** overlap fusion.
- **Cellpose segmentation with interactive QC.** Run Cellpose batch-style across many datasets, then QC each dataset's labels in the napari viewer with paint/erase/fill shortcuts.
- **Time-lapse tracking and lineage.** Import `.tiff` series with `_tN` timepoint tokens as a single multi-timepoint dataset, scroll the timepoints in napari, segment every frame, then track cells so each keeps one ID across time. Cells that die or leave the field of view end their track; dividing cells are linked parent → daughter as lineage (powered by [laptrack](https://github.com/yfukai/laptrack)). The tracked segmentation stores the track ID as the label value, and a napari Tracks layer shows trajectories and divisions.
- **Grouped thresholding.** Cluster cells by intensity, apply per-group autothresholding, refine with a circular ROI per dataset, write the result to `/masks/<round>`. Run multiple rounds in one workflow.
- **Puncta detection & subpopulation classification.** Adaptive Local Clipping (per-cell band-pass + z-score) finds puncta inside each cell, with auto-window sizing and two-pass auto-extraction. Split a feature mask into populations by contrast-to-noise ratio (discover/guided/forced) or interactively with the CNR segmenter. Runs per-frame on time-lapse data.
- **FLIM phasor analysis.** Compute phasor maps from `.sdt` data, plot with `nipy_spectral` density on a Qt-native histogram, draw multi-ROI selections, save the union as a mask layer.
- **Per-cell measurements.** Configurable per-channel metrics across every segmentation and mask layer, exported as a tidy parquet plus CSV mirrors.
- **Multi-window UI.** Independent top-level windows for the napari viewer, pyqtgraph scatter, cell table, and phasor plot — all synchronized through a single `CellDataModel` with one `state_changed` signal.
- **Batch workflows.** End-to-end single-cell pipeline, batch TIFF compression, dataset-wide spatial binning, batch TCSPC append.
- **Dilute-phase mask generation.** Adaptive per-dataset round loop layered on top of grouped thresholding for phase-separated biology.
- **Image and measurement export.** TIFF (GUI dialog or CLI), CSV/XLSX, parquet. Round-trips pixel-size metadata.
- **Headless CLI.** Batch TIFF export, phasor compute, GMM ellipse fitting, whole-field segmentation, and resource rename/delete tooling that runs without a display — see [Command-line Tools](#command-line-tools).
- **Dataset lifecycle.** Import, append, resume, close — with `run_state.json` for crash- and pause-tolerant workflows.

---

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md) for the dated history of when each feature was implemented (grouped by month; PerCell4 is pre-release `0.1.0`).

---

## Installation

PerCell4 requires **Python 3.12 or newer**. Each OS has its own subsection below; pick yours and stop reading the others.

### macOS

Use a virtual environment (recommended).

```bash
cd /path/to/percell4
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Optional development dependencies (tests, lint, all extras):

```bash
pip install -e ".[dev]"
```

Run the app:

```bash
percell4-gui
# or, from a checkout without installing the package:
python main.py
```

### Linux

Tested on **Ubuntu 22.04 LTS** and newer. Other distros (Fedora, Arch, openSUSE) work with the equivalent system packages — names vary by distro.

**System prerequisites** (Ubuntu 22.04+):

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev build-essential
```

**Qt/X11 runtime libraries** required by PyQt5 (install once per machine):

```bash
sudo apt install -y \
  libxcb-xinerama0 libxkbcommon-x11-0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 \
  libxcb-shape0 libxcb-sync1 libxcb-xfixes0
```

**Install percell4:**

```bash
cd /path/to/percell4
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Optional development dependencies:

```bash
pip install -e ".[dev]"
```

**Run the app:**

```bash
percell4-gui
# or:
python main.py
```

**Headless / SSH use.** All `percell4-batch*` CLIs run without any display. To launch the GUI over SSH you need X11 forwarding (`ssh -X` or `-Y`) or a virtual framebuffer (`xvfb-run -- percell4-gui`). For other distros, use your package manager's equivalents for `python3.12-venv` and the `libxcb-*` libraries; the rest of the flow is identical.

### Windows

Prerequisites (do these **before** creating the venv):

1. **64-bit Python 3.12+** from [python.org](https://www.python.org/downloads/) (not the Microsoft Store build, if you hit odd `venv` or SSL issues). During setup, enable **"Add python.exe to PATH"** and **"Install launcher for all users"** so the `py` launcher works.
2. **Microsoft Visual C++ 2015–2022 x64 Redistributable, version 14.50 or newer** — required by PyTorch (which Cellpose depends on). Older copies — common on lab/corporate Windows images — cause `OSError: [WinError 1114]` when `import torch` runs. Install from [`aka.ms/vs/17/release/vc_redist.x64.exe`](https://aka.ms/vs/17/release/vc_redist.x64.exe), then reboot. Confirm with:

    ```
    reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Version
    ```

    The returned `Version` should start with `v14.50` or higher.

#### Command Prompt (`cmd.exe`)

```bat
cd C:\path\to\percell4
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

`py -3` picks the newest Python 3.x you have installed (3.12 or newer). If you do not have the launcher, use the full path to `python.exe` instead of `py -3`.

#### PowerShell

Activation uses a different script; you may need to allow scripts once:

```powershell
cd C:\path\to\percell4
py -3 -m venv .venv
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

If `Activate.ps1` is blocked, use Command Prompt and `activate.bat` instead, or run:

```powershell
cmd /c ".venv\Scripts\activate.bat && python -m pip install -e ."
```

#### Git Bash

```bash
cd /c/path/to/percell4
py -3 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

Optional development dependencies (any shell, venv active):

```bash
python -m pip install -e ".[dev]"
```

#### Windows: PyTorch / Cellpose

Cellpose segmentation depends on PyTorch. On Windows you need two things that the default `pip install` does not provide on its own:

1. **Microsoft Visual C++ 2015–2022 x64 Redistributable, version 14.50 or newer.** Download from [`aka.ms/vs/17/release/vc_redist.x64.exe`](https://aka.ms/vs/17/release/vc_redist.x64.exe). PyTorch links against this runtime; missing or stale copies manifest as `OSError: [WinError 1114]` when `import torch` runs.
2. **CPU-only torch unless you have an NVIDIA GPU.** The default PyPI wheel is the ~2.5 GB CUDA build; on a machine without a matching CUDA driver, its satellite DLLs fail to initialize and take `c10.dll` down with them. Install the CPU wheel explicitly:

    ```powershell
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
    ```

    The `--index-url` form is the only published CPU-only install path — there is no `torch[cpu]` extras syntax.

#### Run the application

After installation, from the activated environment:

```bash
percell4-gui
```

From a checkout without installing the package, you can also run:

```bash
python main.py
```

---

## Install from a wheel

If you have a built wheel (for example `dist/percell4-0.1.0-py3-none-any.whl`):

```bash
pip install path/to/percell4-0.1.0-py3-none-any.whl
percell4-gui
```

Build a wheel from the repository:

```bash
pip install build
python -m build
```

Wheels appear under `dist/`.

---

## Optional extras

| Extra   | Purpose                                      |
|---------|----------------------------------------------|
| `gpu`   | GPU-accelerated Cellpose (`cellpose[gpu]`) — pulls CUDA-tagged torch; requires a matching NVIDIA driver. Unsupported on Windows lab machines without a GPU. On Windows, if `nvidia-smi` reports a driver older than R527 (max CUDA < 12.1), install torch from the CUDA 11.8 index explicitly: `pip install --no-cache-dir --force-reinstall "torch<2.9" "torchvision<0.24" --index-url https://download.pytorch.org/whl/cu118`. Current drivers (R560+) work with default `cu126` wheels. |
| `flim`  | Additional FLIM-related dependency (`dtcwt`) |
| `imagej`| ROI I/O via `roifile`                        |
| `all`   | `gpu`, `flim`, and `imagej`                  |

Example:

```bash
pip install -e ".[gpu]"
```

---

## Standalone bundle (PyInstaller)

For a folder-based app without relying on a separate Python install, build from the repo with PyInstaller using the provided spec:

```bash
pip install pyinstaller
pyinstaller percell4.spec
```

- **macOS:** output includes `dist/PerCell4.app` (and a `PerCell4` folder under `dist/`).
- **Windows:** run `pyinstaller percell4.spec` on Windows; use `dist\PerCell4\PerCell4.exe`.

Bundled apps are large (scientific stack + napari). GPU/CUDA is not included in the bundle; use the pip install path with the `gpu` extra if you need GPU Cellpose. Cellpose downloads model weights on first use; allow network access once or pre-download models according to Cellpose docs.

---

## Troubleshooting

### Windows

- **`py` is not recognized** — Install Python from python.org and enable the launcher, or call `python` using the full path shown by the installer (e.g. `C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe -m venv .venv`).
- **`pip install` tries to compile C/C++ and fails** — Upgrade build tools: `python -m pip install --upgrade pip setuptools wheel`, then retry. If a package still builds from source, install [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) (workload "Desktop development with C++") so wheels that are missing for your platform can compile.
- **PowerShell won't run `Activate.ps1`** — Use the Command Prompt steps with `activate.bat`, or set execution policy as in the PowerShell section above.
- **`percell4-gui` is not recognized** — Activate the venv first; the script is `.venv\Scripts\percell4-gui.exe`. You can always run `python main.py` from the repo root with the venv active.
- **Qt / napari import errors** — This project pins **PyQt5** and uses **qtpy**. Avoid installing a second Qt binding (e.g. PyQt6) into the same venv unless you know you need it. If both are present and imports break, try: `set QT_API=pyqt5` before launching (`cmd`) or `$env:QT_API="pyqt5"` (`PowerShell`).
- **`OSError: [WinError 1114] ... c10.dll`** — PyTorch failed to initialize. Most common fixes, in order: (1) install the [MSVC 2015–2022 x64 Redistributable 14.50+](https://aka.ms/vs/17/release/vc_redist.x64.exe) and reboot; (2) reinstall CPU-only torch with `pip install --no-cache-dir --force-reinstall torch --index-url https://download.pytorch.org/whl/cpu`; (3) if you have `torch==2.9.0` specifically, downgrade — `pip install "torch<2.9" --index-url https://download.pytorch.org/whl/cpu` (known regression [pytorch#169429](https://github.com/pytorch/pytorch/issues/169429) with Qt import order). Full triage in `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md`.
- **Very long clone path** — If installs fail with path-related errors, clone the repo to a short path like `C:\src\percell4` or enable Windows long paths.

### Linux

- **`Qt platform plugin "xcb" not loaded`** — Install the `libxcb-*` packages listed in the Linux install section above. The most common culprit is `libxcb-xinerama0`.
- **GUI launches but is unusable over SSH** — Use `ssh -X` (or `-Y` for trusted forwarding), or run the app under `xvfb-run` for a virtual framebuffer. The batch-export CLI does not need a display.

---

## License

MIT (see `pyproject.toml`).
