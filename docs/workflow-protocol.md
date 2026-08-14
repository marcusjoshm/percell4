# Workflow protocol

The following protocol is a general-purpose workflow for single-cell segmentation, mask generation, and particle analysis. Image data from this workflow are saved as "datasets" in the form of HDF5 files, which can be exported as `.tiff` files for downstream analysis using Python or R scripts. Analyses are saved as `.csv` files that can also be used for graphing and statistics in Python or R.

The launcher's **Workflows** tab offers five multi-step batch workflows, and this page documents the first of them: the **Single-cell thresholding analysis workflow**, which runs Cellpose segmentation, segmentation QC, grouped thresholding, per-cell measurement, and parquet + CSV export in one pass. The other four are **Dilute phase mask generation** (an interactive single-dataset round loop producing one dilute-phase mask), **Dilute phase mask from mask** (grows an existing condensed-phase mask and inverts it within cell boundaries, across many datasets), **FLIM-FRET analysis** (compares donor / donor+acceptor dataset pairs and computes a FRET efficiency per pair or per cell), and the **Automated phasor-masks workflow** (fits a GMM ellipse on the phasor cloud and applies it at two intensity thresholds to make two masks per channel). Each opens its own configuration dialog from the same tab. If you are new to the app, the protocol below is the one to start with — the others assume datasets that already carry segmentations, masks, or phasor data.

## Step-by-step protocol

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
   Click **Add .tiff files...** in the Datasets panel of the setup window — it sits alongside **Add .h5 files...**, **Add folder of .h5...**, and **Remove**. A new window called **Compress TIFF Dataset** opens. In the Source panel at the top, click **Browse...** next to the Directory field and select a folder containing `.tiff` files exported from LASX. The Output field defaults to one level up from the folder containing the `.tiff` files; this is where the dataset will be saved. To change the output folder, click **Browse...** next to the Output field and create or choose a different folder.

   Next, set the **Discovery** field. Use **Subdirectory** (the first entry and the default) when each child folder of the source directory is its own dataset. Use **Flat Directory** when the file names carry LASX channel tokens (`_ch00`, `_ch01`, …) — channels will be those tokens. Use **Tokenless (by name)** when the channel is a *name* at the end of the file name instead of a `chXX` token (e.g. `..._DNA.tif`, `..._G3BP1.tif`, `..._SG_mask.tif`): the shared leading part of the file name becomes the dataset (`.h5`) name and the trailing name becomes the channel, so files like `CellProfiler_U2OS_60min_As_3x4_{cells,DNA,G3BP1,SG_mask}.tif` group into one dataset with four named channels. To rename a channel or assign it as a mask or segmentation instead of an intensity channel, set the **Mode** field (next to Discovery) from **Auto** to **Manual** and edit the channel's name and type. Z-series stacks are automatically projected to a single image; the default is `MIP` (Maximum Intensity Projection). Tiles of a tile scan can be stitched together by checking the **Tile Stitching** box. The LASX default pattern is snake-by-row starting at the top-left, but adjust the stitching orientation if needed. For overlapping tiles, set the **Overlap %** and check **Register overlapping tiles** to phase-correlate the overlap and correct for stage drift; pick a **Reference** channel for the solve (any imported channel, including one renamed in Manual mode). Choose a **Fusion** mode for the overlap regions: **None** keeps each pixel from a single tile (no intensity distortion — required and auto-selected when the dataset has FLIM decay), or **Linear Blending** for a seamless display mosaic. Click **Compress** at the bottom of the window.

   The Compress TIFF Dataset window closes and the new dataset is added to the Datasets table. Repeat for every experiment you want to include in this run.

4. **Configure Cellpose.**
   Select the channel with the strongest cytoplasmic signal as the segmentation channel. The default settings work for most datasets. The default 300 px diameter corresponds to ~30 µm at optimal resolution on a 1.4 NA objective and suits most cells. For larger- or smaller-than-average cells, adjust the diameter accordingly. The default model is **`cpsam_v2`** (the improved CellposeSAM — most robust for low-contrast fluorescence); `cpsam` (original), `cpdino`, and `cpdino-vitb` are also selectable from the Model dropdown.

5. **Choose the edge-cell mode.** Pick one of three options for how to handle cells touching the image border:
   - **exclude** (default) — discard edge cells
   - **include_as_normal** — keep edge cells like any other cell
   - **include_as_size_normalized_cohort** — keep edge cells and analyze them together as one group, sized relative to the average non-edge cell in the same dataset

6. **Define the thresholding rounds.** Each "round" produces one mask per cell — for example, one round for P-bodies and another for stress granules. For each round you want to run:
   - Click **Add Round** in the Thresholding rounds table.
   - Name the round (e.g., `P-body_mask`).
   - Pick the target channel from the dropdown list.
   - **Metric** — `median_intensity` works best for most condensate proteins.
   - **Grouping algorithm** — use `gmm` with at least 10 groups.
   - **Sigma (σ)** — applies a Gaussian blur to the image before segmentation, useful for noisy images. Sigma *is* the standard deviation of the Gaussian kernel, measured in pixels: the larger the value, the wider the area each pixel is smoothed over.

   Add as many rounds as you need. The workflow runs them in the order shown in the table.

7. **Include particle analysis.**
   The **Include particle analysis** box is checked by default. When it is checked, the app counts and measures particles (e.g., puncta) inside each cell for every thresholding round. Set:
   - **Min particle area** — the smallest particle the app will keep. Anything smaller is treated as noise and dropped. Pick the unit on the right: **px** (pixels — the same threshold is used for every dataset) or **µm²** (square microns — converted per dataset using each TIFF's pixel size). Leave at `0` to keep every particle, including single-pixel ones.

   Uncheck the **Include particle analysis** box if you do not want particle analysis.

8. **(Optional) Enable the dilute-phase mask.**
   Check **Generate dilute-phase mask** if you want a dilute-phase mask generated in this run. Then set:
   - **Mask name** — must be different from every thresholding round name (the app will not let the run start until you fix it).
   - **Dilation radius** in pixels — used every dilute round.
   - Use the same grouping and filter settings you would use for grouped thresholding.

9. **Pick output columns and the output folder.**
   In the **Output** group of the setup window, choose which measurement columns to include in your results files. Pick the output folder — the app creates a new subfolder named with the date and time of the run.

10. **Start the run.**
    Click **Start**. The app first compresses your TIFFs into datasets, then runs Cellpose to find every cell in every dataset. You do not need to do anything during this part — watch progress in the launcher status bar.

11. **Review the cell outlines (your input needed).**
    When Cellpose finishes, the Viewer window opens with the first dataset. Cell outlines are shown on top of your image. Refine them if needed:
    - Click the cell outlines layer in the layer list on the left, then use the paint, erase, and fill tools above the image.

    Click **Accept & Next →** to move on to the next dataset. Repeat for every dataset.

12. **Review each thresholding mask (your input needed).**
    For each thresholding round, a review window opens for the first dataset. The proposed mask is shown on top of the target channel. Either:
    - Click **Accept** to keep the proposed mask, or
    - Draw a circular region on the image to guide refinement, then click **Accept** — the app recalculates the mask using only that region.

    Repeat for every dataset, then for every round.

13. **(Optional) Build the dilute-phase mask (your input needed).**
    If you enabled the dilute-phase mask in step 8, the dilute window opens for the first dataset. For each dataset:
    - Click **Run another round** to compute the proposed condensed mask.
    - A review window opens — look over the mask and click **Accept** to keep this round.
    - The accepted mask is automatically expanded slightly and removed from the input for the next round.
    - Click **Run another round** again to refine further on the same dataset, **Done — save and continue** to move on to the next dataset, or **Cancel run** to abandon the whole workflow.

    Different datasets may need different numbers of rounds. The final mask is saved when you click **Done — save and continue**.

14. **Wait for the app to measure and save your results.**
    The app measures every cell across every segmentation and mask, then saves the results. You do not need to do anything during this part.

15. **Find your results.**
    Open the output folder you chose in step 9. Inside, find a new folder named with the date and time of the run. It contains:
    - `combined.csv` — every cell from every dataset in one spreadsheet (open this in Excel, Numbers, or Google Sheets).
    - `per_dataset/<DS>.csv` — one spreadsheet per dataset.
    - `summary_groups.csv` — one row per dataset × round × group, with means, medians, standard deviations, and cell counts.
    - `summary_datasets.csv` — one row per dataset with edge-cell mode, round counts, and any failure reasons.
    - `measurements.parquet` — the same data as `combined.csv`, in a compact format for Python or R users.

**Cancelling a run.** A run in progress can be cancelled with **Cancel run**; it cannot be paused and resumed. An interrupted run is restarted from the beginning.

**Headless TIFF export.** If you only need `.tiff` files out of an existing dataset — for ImageJ, custom downstream scripts, or sharing with a colleague — use the command-line tool documented in the [CLI reference](cli.md).

---

Back to the [README](../README.md).
