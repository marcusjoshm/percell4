---
title: "Cross-Platform Packaging and Windows Compatibility Review Fixes"
category: build-errors
tags: [windows, pyinstaller, atomic-writes, pyproject, documentation-drift, packaging]
module: [project.py, store.py, percell4.spec, pyproject.toml, CLAUDE.md]
date: 2026-03-27
symptom: "Multiple packaging issues discovered during Windows installer preparation: unnecessary os.unlink workaround introducing data loss risk, fragile PyInstaller spec with manual imports, broken CLI entry point, undocumented dependency in docs"
root_cause: "Cargo-cult Windows workaround (os.unlink before os.replace when Python 3.12 handles it natively), manual dependency tracking in PyInstaller spec, declared CLI entry point with no backing module, documentation drifting from actual imports"
---

# Cross-Platform Packaging and Windows Compatibility Review Fixes

## Symptoms

1. `os.replace()` Windows workaround introduced a data loss window between `os.unlink` and `os.replace`
2. PyInstaller spec had 33 manually-listed hidden imports (fragile, missed `io/models.py`)
3. PyInstaller spec used deprecated `block_cipher` parameter (removed in PyInstaller 6.x)
4. PyInstaller spec had `datas=[]` — missing napari/cellpose/skimage runtime data files
5. PyInstaller spec had `upx=True` — triggers Windows antivirus false positives
6. `pyproject.toml` declared CLI entry point `percell4.cli.main:cli` but `cli/main.py` didn't exist
7. CLAUDE.md listed `ptufile (PicoQuant)` in tech stack but nothing imports it

## Root Causes

### 1. Unnecessary `os.replace()` Workaround

A Stack Overflow-era pattern was applied without checking the Python version. `os.replace()` has been atomic and overwriting on all platforms since Python 3.3 — it calls `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING` on Windows. The `os.unlink` before `os.replace` creates a window where the target file doesn't exist. A crash between the two calls loses data, defeating the entire purpose of atomic writes.

### 2. Fragile PyInstaller Spec

Hidden imports were added one at a time as import errors surfaced during testing, growing to 33 manual entries. Nobody stepped back to use `collect_submodules()`. The `block_cipher` parameter was copied from an older PyInstaller template. Data files were not collected because the initial build appeared to work (data files are only needed at runtime for specific features).

### 3. Phantom CLI Entry Point

`pyproject.toml` was written with a planned CLI entry point before the module existed. The module was never created, but the entry point was never removed. Nobody ran `pip install -e .` followed by `percell4 --help` to verify.

### 4. Documentation Drift

CLAUDE.md was written during architecture planning when `ptufile` support was intended. The library was never integrated, but the docs were not updated. Documentation and code have independent lifecycles.

## Fixes

### Fix 1: Remove the `os.unlink` Workaround

**Files:** `src/percell4/project.py`, `src/percell4/store.py`

Removed the platform check and `os.unlink` call. Bare `os.replace()` is correct on all platforms:

```python
# Before (WRONG — data loss risk on crash)
if sys.platform == "win32":
    try:
        os.unlink(target_path)
    except FileNotFoundError:
        pass
os.replace(tmp_path, target_path)

# After (CORRECT — atomic on all platforms)
os.replace(tmp_path, target_path)
```

Also removed `import sys` from both files (no longer needed).

### Fix 2: PyInstaller Spec Overhaul

**File:** `percell4.spec`

```python
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Auto-collect all percell4 submodules (self-maintaining)
_hidden = collect_submodules("percell4") + [
    "PyQt5", "qtpy", "pyqtgraph",
    "napari", "napari.utils", "napari.layers", "napari.viewer",
    "scipy.ndimage", "scipy.signal",
    "skimage.measure", "skimage.filters", "skimage.morphology",
    "cellpose", "cellpose.models",
    "dtcwt", "roifile", "click", "rich",
]

# Collect runtime data files
_datas = (
    collect_data_files("napari")
    + collect_data_files("cellpose")
    + collect_data_files("skimage")
)
```

Changes:
- `collect_submodules("percell4")` replaces 33 manual entries
- `collect_data_files()` for napari, cellpose, skimage
- Removed `block_cipher` and all `cipher=` references
- Set `upx=False` to avoid Windows antivirus false positives

### Fix 3: Remove Broken CLI Entry Point

**File:** `pyproject.toml`

Removed the entire `[project.scripts]` section. The GUI entry point `percell4-gui = "percell4.app:main"` remains.

### Fix 4: Remove Phantom Dependency from Docs

**File:** `CLAUDE.md`

Changed `tifffile, sdtfile (Becker&Hickl), ptufile (PicoQuant)` to `tifffile, sdtfile (Becker&Hickl)`.

## Prevention Patterns

### Do Not "Help" stdlib Functions

`os.replace()` is atomic on all platforms since Python 3.3. Before adding a platform workaround, check the Python docs for your minimum supported version. Any workaround should cite the Python version where the issue exists.

**Code smell:** `os.unlink()` immediately followed by `os.replace()` or `os.rename()` in the same function.

### Use `collect_submodules` in PyInstaller Specs

Manual hidden import lists rot as modules are added. Use:
```python
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
```
Only manually list third-party submodules that PyInstaller's default hooks miss.

### Verify Entry Points in CI

Any declared entry point is an executable contract. After `pip install -e .`, invoke every entry point:
```python
import importlib
for module_path in entry_points:
    importlib.import_module(module_path.split(":")[0])
```

### Keep Docs in Sync with Code

Follow the project rule: "Active docs contain ONLY what IS, not what WAS or MIGHT BE." When removing a dependency, grep CLAUDE.md files in the same commit.

### Avoid UPX for Windows Distribution

UPX packing triggers antivirus false positives. Set `upx=False` in PyInstaller specs. If binary size is a concern, use `--onedir` mode or strip debug symbols instead.

## Related Documentation

- [Windows Compat Plan](../../plans/2026-03-27-feat-windows-compat-and-installer-plan.md) — The plan that prompted this work
- [Code Review Findings Phases 0-6](../architecture-decisions/percell4-code-review-findings-phases-0-6.md) — Earlier review that validated atomic writes as crash-safe
