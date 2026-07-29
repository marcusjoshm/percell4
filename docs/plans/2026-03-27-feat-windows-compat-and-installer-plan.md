---
title: "feat: Windows Compatibility and Cross-Platform Installer"
type: feat
date: 2026-03-27
---

# Windows Compatibility and Cross-Platform Installer

## Overview

Make PerCell4 run on Windows and create easy-install packages for both macOS and Windows. Target audience: Lee Lab members. Some have Python/conda installed, some don't — provide both options.

## Current State

The codebase is **99% Windows-compatible** already:
- pathlib used consistently (no hardcoded `/` separators)
- No Unix-only imports (no fcntl, no subprocess calls)
- Qt/napari/pyqtgraph are all cross-platform
- All dependencies have Windows wheels
- byte-order handling is explicit in .bin reader

**One code fix needed:** `os.replace()` in `project.py` and `store.py` can fail on Windows when the target file already exists.

## Implementation Plan

### Phase 1: Code Fix for Windows (30 min)

**Fix `os.replace()` on Windows:**

`os.replace()` is atomic on POSIX but on Windows it raises `FileExistsError` if the target exists. Fix both occurrences:

**Files to modify:**
- `src/percell4/project.py:120` (`_write_atomic`)
- `src/percell4/store.py:300` (`create_atomic`)

**Fix pattern:**
```python
# Cross-platform atomic replace
import sys

def _atomic_replace(src, dst):
    """Replace dst with src atomically (cross-platform)."""
    if sys.platform == "win32":
        # Windows: remove target first, then rename
        try:
            os.unlink(dst)
        except FileNotFoundError:
            pass
    os.replace(src, dst)
```

### Phase 2: Test on Windows (1-2 hours)

- [ ] Install Python 3.12 on a Windows machine
- [ ] Clone the repo and create a venv
- [ ] `pip install -e ".[dev]"` — verify all dependencies install
- [ ] `python main.py` — verify the GUI launches
- [ ] Import a .tif dataset — verify import pipeline
- [ ] Import a .bin FLIM dataset — verify .bin reader works
- [ ] Run Cellpose segmentation
- [ ] Run measurements
- [ ] Run threshold + phasor
- [ ] Verify file dialogs, QSettings, and window geometry persistence

### Phase 3: Packaging — pip installable wheel (1 hour)

The package is already structured correctly for pip:

```bash
# For users WITH Python/conda:
pip install percell4
# or from local wheel:
pip install percell4-0.1.0-py3-none-any.whl
```

**Build the wheel:**
```bash
pip install build
python -m build
# Creates dist/percell4-0.1.0-py3-none-any.whl
```

The `pyproject.toml` already has:
- `[project.scripts] percell4 = "percell4.cli.main:cli"`
- `[project.gui-scripts] percell4-gui = "percell4.app:main"`
- All dependencies declared with version bounds

**Distribute:** Share the `.whl` file or host on a private PyPI / GitHub release.

### Phase 4: Standalone Installer — PyInstaller (2-3 hours)

For users WITHOUT Python. Creates a self-contained executable.

**Tool:** PyInstaller (works on both macOS and Windows)

**Create spec file (`percell4.spec`):**
```python
# percell4.spec
a = Analysis(
    ['src/percell4/app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'percell4', 'percell4.gui', 'percell4.io', 'percell4.segment',
        'percell4.measure', 'percell4.flim',
        'napari', 'cellpose', 'pyqtgraph', 'h5py', 'tifffile',
        'sdtfile', 'skimage', 'scipy', 'pandas',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'IPython', 'jupyter'],
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, [], name='PerCell4', console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name='PerCell4')
```

**Build commands:**
```bash
# macOS:
pyinstaller --onedir --windowed --name PerCell4 src/percell4/app.py

# Windows:
pyinstaller --onedir --windowed --name PerCell4 src\percell4\app.py
```

**Output:**
- macOS: `dist/PerCell4.app` (drag to Applications)
- Windows: `dist/PerCell4/PerCell4.exe` (zip and distribute)

**Known PyInstaller challenges:**
- napari has many hidden imports — may need `--collect-all napari`
- cellpose models download on first use — include note in README
- GPU/CUDA support won't bundle — CPU-only for standalone
- macOS may need `--codesign-identity` for Gatekeeper
- Expect ~500MB-1GB bundle size due to numpy/scipy/torch

### Phase 5: Distribution (1 hour)

**Option A: GitHub Releases (recommended)**
- Create GitHub repo for PerCell4
- Use GitHub Actions to build wheels + installers on each release tag
- Users download from Releases page

**Option B: Shared drive**
- Build locally on macOS and Windows
- Place `.whl` and standalone bundles on the lab's shared drive
- Include a README with installation instructions

**README install instructions:**

```markdown
## Installation

### Option 1: pip install (requires Python 3.12+)
```bash
pip install percell4-0.1.0-py3-none-any.whl
percell4-gui  # launch the app
```

### Option 2: Standalone (no Python needed)
- **macOS:** Download PerCell4.app, drag to Applications
- **Windows:** Download PerCell4.zip, extract, run PerCell4.exe
```

## Acceptance Criteria

- [x] `os.replace()` fixed for Windows compatibility
- [ ] App launches and runs on Windows 10/11
- [ ] Import, segment, measure, threshold, phasor all work on Windows
- [ ] `pip install percell4-*.whl` works on macOS and Windows
- [ ] PyInstaller standalone bundle works on macOS
- [ ] PyInstaller standalone bundle works on Windows
- [ ] Installation instructions in README

## Risks

| Risk | Mitigation |
|------|------------|
| PyInstaller + napari hidden imports | Use `--collect-all napari`, test iteratively |
| GPU/CUDA not bundled | Document CPU-only for standalone, pip install for GPU |
| Large bundle size (~1 GB) | Use `--onedir` not `--onefile`, compress with zip |
| macOS Gatekeeper blocks unsigned app | Provide terminal command to bypass, or sign with Apple Developer ID |
| Windows Defender flags unsigned exe | Users add exception, or sign with code signing cert |
| Cellpose model download on first run | Document in README, pre-download in installer if possible |
