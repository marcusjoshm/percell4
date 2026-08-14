# Installing PerCell4

PerCell4 requires **Python 3.12 or newer**. Each OS has its own subsection below; pick yours and stop reading the others.

## Table of Contents

- [Installation](#installation)
  - [macOS](#macos)
  - [Linux](#linux)
  - [Windows](#windows)
- [Updating](#updating)
- [Install from a wheel](#install-from-a-wheel)
- [Optional extras](#optional-extras)
- [Standalone bundle (PyInstaller)](#standalone-bundle-pyinstaller)
- [Troubleshooting](#troubleshooting)
  - [Windows](#windows-1)
  - [Linux](#linux-1)

---

## Installation

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

## Updating

PerCell4 requires **Cellpose 4.2 or newer** for the current segmentation models
(`cpsam_v2`, `cpsam`, `cpdino`, `cpdino-vitb` — see the [changelog](../CHANGELOG.md)). To
update an existing install, from your checkout with the virtual environment
active:

```bash
git pull
pip install -e .                    # resolves the new cellpose>=4.2 pin
# or upgrade Cellpose on its own:
pip install -U "cellpose>=4.2,<5"
```

- **The model downloads on first use.** Cellpose 4.x weights (`cpsam_v2` is a
  large SAM-ViTL model, several hundred MB) are fetched from Hugging Face the
  first time you segment with them — allow network access once, then they are
  cached. Cellpose 4.x also pulls `segment_anything` and `opencv-python-headless`
  automatically; `pip install` handles both.
- **Windows / PyTorch is not affected.** Cellpose 4.2 only requires
  `torch>=1.6`, so updating Cellpose does **not** change your existing PyTorch
  install — the CPU-only torch and MSVC Redistributable setup from
  [Windows](#windows) stays exactly as-is. (Only in the rare case that `pip`
  upgrades torch on a machine **without** an NVIDIA GPU would you re-apply the
  CPU wheel: `pip install --no-cache-dir --force-reinstall torch --index-url https://download.pytorch.org/whl/cpu`.)
- A fresh install needs no extra steps — `pip install -e .` already pulls
  Cellpose 4.2+; follow the [Installation](#installation) section for your OS.

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
| `ocr`   | Phasor-calibration OCR helper [`tools/png_to_csv/`](../tools/png_to_csv/README.md): `pytesseract`, `Pillow`, `openpyxl`. **Also needs the Tesseract OCR engine** — a system binary pip cannot install (see below). |
| `all`   | `gpu`, `flim`, `imagej`, and `ocr`           |

Example:

```bash
pip install -e ".[gpu]"
```

The `ocr` extra additionally requires the **Tesseract** OCR engine (not a Python
package), installed once per machine:

```bash
pip install -e ".[ocr]"          # python deps for tools/png_to_csv/
# Tesseract OCR engine:
#   macOS:   brew install tesseract
#   Linux:   sudo apt install tesseract-ocr
#   Windows: https://github.com/UB-Mannheim/tesseract/wiki  (add it to PATH)
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
- **Blank napari canvas plus `OpenGL.error.Error: Attempt to retrieve context when no valid context`** — Qt and PyOpenGL disagreed about the GL backend. On GNOME Wayland, Qt5 runs on XWayland (a GLX context) while `WAYLAND_DISPLAY` makes PyOpenGL pick EGL. PerCell4 reconciles the two automatically at startup; if you have set `PYOPENGL_PLATFORM` yourself, unset it (the manual override is respected, so a wrong value sticks) or set it to match — `PYOPENGL_PLATFORM=glx` on XWayland, `egl` on native Wayland.

---

Back to the [README](../README.md).
