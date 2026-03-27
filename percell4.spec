# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PerCell4 standalone bundle.

Build with:
    pyinstaller percell4.spec

Output: dist/PerCell4/ (folder with PerCell4 executable)
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

src_dir = str(Path("src"))

# Auto-collect all percell4 submodules instead of manual listing
_hidden = collect_submodules("percell4") + [
    # GUI frameworks
    "PyQt5",
    "qtpy",
    "pyqtgraph",
    # napari and its many dependencies
    "napari",
    "napari.utils",
    "napari.layers",
    "napari.viewer",
    # Scientific
    "numpy",
    "scipy",
    "scipy.ndimage",
    "scipy.signal",
    "pandas",
    "h5py",
    "tifffile",
    "sdtfile",
    "skimage",
    "skimage.measure",
    "skimage.filters",
    "skimage.morphology",
    "cellpose",
    "cellpose.models",
    # Optional extras (include if installed)
    "dtcwt",
    "roifile",
    "click",
    "rich",
]

# Collect data files for packages that ship resources
_datas = (
    collect_data_files("napari")
    + collect_data_files("cellpose")
    + collect_data_files("skimage")
)

a = Analysis(
    [str(Path("src") / "percell4" / "app.py")],
    pathex=[src_dir],
    binaries=[],
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "sphinx",
        "docutils",
    ],
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PerCell4",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    windowed=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PerCell4",
)

# macOS: create .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PerCell4.app",
        icon=None,
        bundle_identifier="com.leelab.percell4",
        info_plist={
            "CFBundleName": "PerCell4",
            "CFBundleDisplayName": "PerCell4",
            "CFBundleVersion": "0.1.0",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
