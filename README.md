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
- [Batch TIFF Export (CLI)](#batch-tiff-export-cli)
- [Tech Stack](#tech-stack)
- [Features](#features)
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

   Next, change the Discovery field from **Subdirectory** to **Flat Directory**. You should see a list of file names matching the LASX file names. Channels will be the channel tokens created by LASX at export. To rename them, switch the discovery mode to **Manual** and type your desired channel name. Z-series stacks are automatically projected to a single image; the default is `MIP` (Maximum Intensity Projection). Non-overlapping tiles of a tile scan can be stitched together by checking the **Tile Stitching** box. The LASX default pattern is snake-by-row starting at the top-left, but adjust the stitching orientation if needed. Click **Compress** at the bottom of the window.

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

## Batch TIFF Export (CLI)

Export dataset layers as TIFFs across one or more `.h5` files without opening the GUI. From the activated environment:

```bash
python -m percell4.interfaces.cli.batch_export INPUTS --output-dir DIR [options]
```

`INPUTS` is one or more `.h5` files, or directories containing `.h5` files (directories are globbed non-recursively for `*.h5`). For each dataset it writes one TIFF per intensity channel, per `/labels/<name>`, and per `/masks/<name>` into `--output-dir` using a flat `<h5_stem>_<layer>.tif` layout. Existing files with matching names are overwritten — point `--output-dir` at a fresh directory to preserve prior runs. Phasor, lifetime, and decay arrays are not exported.

| Option | Purpose |
|---|---|
| `--output-dir DIR`, `-o DIR` | Target directory for the `.tif` outputs. Created if missing. **Required.** |
| `--view-bin N` | Bin factor applied to every layer at read time. Default `1` (native resolution). `N > 1` produces downsampled TIFFs using the same lens the GUI applies for `view_bin=N` (`sum_bin_2d` for intensity, `mode_labels` for `/labels`, `majority_vote_mask` for `/masks`). `N` must be an integer `>= 1`. Output filenames are unchanged regardless of bin — track the value yourself (e.g. `--output-dir out_bin4/`) if you mix runs. |
| `--quiet` | Suppress per-dataset error detail lines. Status headers and final totals still print. |
| `--verbose`, `-v` | Enable DEBUG logging. |

Examples:

```bash
# Native-resolution export of two datasets
python -m percell4.interfaces.cli.batch_export dish_1.h5 dish_2.h5 --output-dir /tmp/exports

# Every .h5 in a directory, downsampled to match the GUI's view-bin 4 lens
python -m percell4.interfaces.cli.batch_export /scratch/dishes/ --output-dir ~/exports/ --view-bin 4
```

The GUI equivalent lives at `I/O` → **Export Images**; the workflow protocol section above explains when to reach for which.

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
- **Cellpose segmentation with interactive QC.** Run Cellpose batch-style across many datasets, then QC each dataset's labels in the napari viewer with paint/erase/fill shortcuts.
- **Grouped thresholding.** Cluster cells by intensity, apply per-group autothresholding, refine with a circular ROI per dataset, write the result to `/masks/<round>`. Run multiple rounds in one workflow.
- **FLIM phasor analysis.** Compute phasor maps from `.sdt` data, plot with `nipy_spectral` density on a Qt-native histogram, draw multi-ROI selections, save the union as a mask layer.
- **Per-cell measurements.** Configurable per-channel metrics across every segmentation and mask layer, exported as a tidy parquet plus CSV mirrors.
- **Multi-window UI.** Independent top-level windows for the napari viewer, pyqtgraph scatter, cell table, and phasor plot — all synchronized through a single `CellDataModel` with one `state_changed` signal.
- **Batch workflows.** End-to-end single-cell pipeline, batch TIFF compression, dataset-wide spatial binning, batch TCSPC append.
- **Dilute-phase mask generation.** Adaptive per-dataset round loop layered on top of grouped thresholding for phase-separated biology.
- **Image and measurement export.** TIFF (GUI dialog or CLI), CSV/XLSX, parquet. Round-trips pixel-size metadata.
- **Headless CLI.** Batch TIFF export and (where applicable) workflow tooling that runs without a display — see [Batch TIFF Export (CLI)](#batch-tiff-export-cli).
- **Dataset lifecycle.** Import, append, resume, close — with `run_state.json` for crash- and pause-tolerant workflows.

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

**Headless / SSH use.** The batch-export CLI (`python -m percell4.interfaces.cli.batch_export ...`) runs without any display. To launch the GUI over SSH you need X11 forwarding (`ssh -X` or `-Y`) or a virtual framebuffer (`xvfb-run -- percell4-gui`). For other distros, use your package manager's equivalents for `python3.12-venv` and the `libxcb-*` libraries; the rest of the flow is identical.

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
