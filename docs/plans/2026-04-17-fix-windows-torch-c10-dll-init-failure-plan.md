---
title: "fix: Windows torch c10.dll init failure during Cellpose segmentation"
type: fix
date: 2026-04-17
---

# Fix Windows `c10.dll` initialization failure during Cellpose segmentation

## Enhancement Summary

**Deepened on:** 2026-04-17

**Key improvements over the initial draft:**

1. **Named the most likely root cause.** Upstream PyTorch issues
   [#166628](https://github.com/pytorch/pytorch/issues/166628) and
   [#169429](https://github.com/pytorch/pytorch/issues/169429) describe a torch
   **2.9.0 regression on Windows** where DLL search-order tightening collides
   with Qt's DLL directory registration. If Qt is imported *before* torch (the
   PerCell4 pattern — `app.py` constructs `QApplication` long before any
   worker lazy-imports cellpose), `c10.dll` picks up Qt-adjacent MSVC runtime
   copies and `WinError 1114` results. **Known good: torch ≤ 2.8.x** or a
   bumped MSVC 14.50+ Redistributable. This moves from "possible cause" to
   "prime suspect" in triage order.

2. **Replaced stringly-typed error classification with structured data.**
   Architect + Python reviewer both called for `Worker.error` to emit a
   `WorkerError` dataclass (carrying `winerror`, `exc_type`, `traceback`,
   `is_import_error`) instead of `f"{type(e).__name__}: {e}"`. Classification
   then keys off `e.winerror in (126, 193, 1114)` — Windows-deterministic —
   rather than fragile substring matches against DLL names.

3. **Split Qt-agnostic classifier from Qt dialog.** Pure-Python classification
   lives in `src/percell4/workflows/diagnostics.py` (reusable from CLI and
   batch runners). The `QMessageBox` dialog stays in `gui/torch_error.py`.
   This respects the importlinter contracts in `pyproject.toml` and the
   `workflows/` / `gui/workflows/` split documented in `src/percell4/CLAUDE.md`.

4. **Counted the call sites correctly.** Pattern-recognition found **four**
   worker-error handlers, not three: `grouped_seg_panel.py` has *two*
   (`:297` Measure, `:372` Grouping). Collapsing the fix to a single shared
   handler wired into `workers.py`'s generic emission path covers all of
   them without per-panel code.

5. **Cut three pieces of scope:** the `scripts/windows_install.ps1`
   convenience script, the `cpu-windows = []` placeholder extras entry, and
   the aspirational Success Metrics section. All three earned "simplicity
   bomb" flags from the simplicity reviewer.

6. **Deleted `KMP_DUPLICATE_LIB_OK=TRUE` as a documented workaround.** Intel
   and PyTorch maintainers both classify it as "unsafe, unsupported,
   undocumented" and known to silently produce incorrect numerical results.
   For a scientific measurement app that does heavy numpy/scipy math
   downstream of segmentation, blessing it — even with a caveat — is a
   footgun.

7. **Aligned with existing house style.** README already has a Windows
   section (`:37-80`) and a Windows Troubleshooting block (`:149-156`); we
   *extend* those rather than adding a new top-level section.
   `QMessageBox.warning` matches convention (see `interfaces/gui/main_window.py:359`,
   `gui/add_layer_dialog.py:134`) — not `.critical`. Module name drops the
   leading underscore to match sibling helpers.

8. **Added the real preflight pattern.** PyTorch itself ships
   `_load_dll_libraries()` in `torch/__init__.py` — Spyder and DeepLabCut
   use `ctypes.WinDLL(...)` probes. If Track 3 ever grows a preflight, the
   prior art is clear and callable.

## Overview

On the Windows install at `E:\percell4`, clicking Run in the Cellpose
segmentation panel surfaces this error in the launcher status bar:

> Error: OSError: [WinError 1114] A dynamic link library (DLL) initialization
> routine failed. Error loading E:\percell4\.venv\Lib\site-packages\torch\lib\c10.dll
> or one of its dependencies

`c10.dll` is the PyTorch core runtime. Cellpose is the first code path that
lazy-imports `torch`, so this surfaces here, but it is really a torch import
failure — `python -c "import torch"` in that venv will show the same error.
This plan is a triage + harden pass. The root cause lives in the Windows
environment, not our source.

## Problem Statement

`WinError 1114` means the loader found `c10.dll` but one of its DLL
dependencies' `DllMain` returned failure. Real-world causes, in 2024–2026
revised order of likelihood for a Qt-before-torch import pattern:

1. **PyTorch 2.9.0 Windows regression when Qt imports first.** Issues
   [#166628](https://github.com/pytorch/pytorch/issues/166628),
   [#169429](https://github.com/pytorch/pytorch/issues/169429). The 2.9.0
   change to `torch/__init__.py`'s `os.add_dll_directory`/`LOAD_LIBRARY_SEARCH_*`
   flags means Qt's DLL directories (registered earlier by `QApplication`
   construction) shadow torch's. `c10.dll` then resolves a vcruntime/msvcp
   pair from a Qt-adjacent location that mismatches what torch was built
   against. Workarounds: pin `torch<2.9` or require MSVC Redistributable
   **14.50.35719 or newer**. PerCell4 imports Qt before torch by design, so
   this is the prime suspect.
2. **Missing or stale Microsoft Visual C++ 2015–2022 x64 Redistributable.**
   PyTorch's official docs do not name a minimum version; empirically
   14.50.35719+ is required for torch 2.9.x. On clean Windows images the
   redistributable may not be present at all.
3. **Intel OpenMP / MKL DLL conflict.** A stray `libiomp5md.dll` elsewhere
   on the DLL search path (Anaconda base env, Intel OneAPI, a second
   numpy/scipy install, system PATH) is loaded first and trips c10's OpenMP
   init. Stderr shows `OMP: Error #15: Initializing libiomp5md.dll, but
   found libiomp5md.dll already initialized` *before* the OSError.
4. **CUDA runtime mismatch.** Default PyPI `pip install torch` on Windows
   pulls the **GPU wheel (~2.5 GB)** even if no NVIDIA hardware is present.
   CUDA satellite DLLs (`cublas64_*.dll`, `cudnn*.dll`) then fail to
   initialize and take `c10.dll` with them. On CPU-only wheels these
   satellites are absent, so if the error message names one of them the
   wheel is wrong.
5. **Corrupted / partial torch install** from an interrupted `pip install`
   on `E:\` (USB/network-backed drives or AV interference can truncate DLLs).
6. **Antivirus / Defender quarantine** of `c10.dll` or a dependency.
7. **Conflicting DLLs on PATH** (MSYS2, MinGW, old CUDA toolkits, legacy
   Python installs) ahead of the venv's `site-packages\torch\lib`.
8. **Python / torch ABI mismatch** — e.g. a torch wheel for a different
   Python minor version dropped into the venv via manual download.

The error also reveals a UX issue: a torch import failure bubbles up as a
terse stringified line in the status bar. Users with no Python background
have no next step. Any fix should also make the launcher surface a diagnosis
and point to the install docs.

### Where the error surfaces

- Lazy `from cellpose import models` — the adapter at
  `src/percell4/adapters/cellpose.py:17` and `src/percell4/segment/cellpose.py`
  both do this inside functions, which transitively imports torch.
- Caught by the generic worker: `src/percell4/gui/workers.py:52–53` emits
  `f"{type(e).__name__}: {e}"` via a `Signal(str)` — the structured
  exception is discarded at that boundary.
- Rendered by four worker-error handlers (pattern-recognition confirmed):
  - `src/percell4/gui/segmentation_panel.py:269`
  - `src/percell4/gui/grouped_seg_panel.py:297` (Measure)
  - `src/percell4/gui/grouped_seg_panel.py:372` (Grouping)
  - `src/percell4/gui/workflows/single_cell/runner.py:473`
- Grouped-segmentation Measure and Grouping workers don't hit torch
  themselves, but improving the generic worker-error path covers them for
  free and aligns behavior across the app.

### Windows error-code discriminator

PyTorch does **not** raise different exception types for the three common
Windows failures — all surface as `OSError` from `ctypes.WinDLL` inside
`torch/__init__.py`. The deterministic signal is `e.winerror`:

| `winerror` | Meaning | Common cause |
|---|---|---|
| 126 | Module not found | Missing DLL or broken PATH |
| 193 | Invalid Win32 application | Bitness mismatch (32/64) |
| 1114 | DLL init routine failed | Transitive dependency broken (usually MSVC runtime, or 2.9.0 Qt-order regression) |

Combine the code with the failing DLL name in the message (`vcruntime140.dll`,
`c10.dll`, `cublas64_*.dll`, `libiomp5md.dll`) to narrow to a root cause.

## Proposed Solution

Three tracks. Track 1 unblocks the user today. Track 2 hardens our install
so the next Windows machine doesn't hit it. Track 3 improves the error UX
so future native-dep failures are self-service.

### Track 1 — Environment triage (unblock the affected machine)

Execute in order, stopping at the first step that makes
`python -c "import torch"` succeed in the venv.

1. **Minimal repro** (strip PerCell4 out of the picture):
   ```powershell
   E:\percell4\.venv\Scripts\activate
   python -c "import torch; print(torch.__version__)"
   ```
   Confirm the same `WinError 1114`. This proves it is not our code.

2. **Check torch version — pin to 2.8.x if 2.9.x.** New in the deepened
   plan. If `python -c "import torch; print(torch.__version__)"` reports
   `2.9.0` (before the repro throws), or `pip show torch` reports 2.9.x:
   ```powershell
   pip install --no-cache-dir --force-reinstall "torch<2.9" --index-url https://download.pytorch.org/whl/cpu
   ```
   Re-run the minimal repro. Per PyTorch #166628/#169429, torch 2.9.x broke
   Windows when Qt imports first — exactly PerCell4's pattern.

3. **Install / update the MSVC 2015–2022 x64 Redistributable.**
   `https://aka.ms/vs/17/release/vc_redist.x64.exe`. Must be 14.50.35719 or
   newer for torch 2.9 compatibility. Install, reboot, retry repro.

4. **Check for duplicate `libiomp5md.dll`:**
   ```powershell
   where.exe libiomp5md.dll
   ```
   If the first hit is outside `E:\percell4\.venv\Lib\site-packages\torch\lib\`
   (e.g. a system Anaconda or Intel OneAPI), remove that directory from the
   user/system `PATH`. **Do not** set `KMP_DUPLICATE_LIB_OK=TRUE` — Intel
   and PyTorch maintainers both document it as unsafe and potentially
   silently wrong. For this codebase (heavy numpy/scipy downstream of
   segmentation) it is a correctness bomb.

5. **Reinstall torch as CPU-only** (avoids CUDA satellite DLL issues):
   ```powershell
   pip uninstall -y torch torchvision
   pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
   ```
   `--no-cache-dir` matters — a stale `+cu12x` wheel in `~/.cache/pip` will
   otherwise be preferred.

6. **Check Windows Defender / AV quarantine** for `c10.dll` and siblings in
   `E:\percell4\.venv\Lib\site-packages\torch\lib\`. Restore and exclude the
   venv if flagged.

7. **Rule out PATH contamination:**
   ```powershell
   $env:Path -split ';'
   ```
   Temporarily minimize PATH (`System32`, venv `Scripts`, venv `Library\bin`
   only) and retry. If the repro now succeeds, bisect the original PATH to
   find the bad entry.

8. **Deterministic fallback — dependency dump.** From a "Developer Command
   Prompt for VS 2022":
   ```powershell
   dumpbin /dependents E:\percell4\.venv\Lib\site-packages\torch\lib\c10.dll
   ```
   This lists every DLL `c10.dll` needs. Cross-reference against
   `where.exe <name>` for each to identify exactly which one fails to
   resolve. Short-circuits the whole triage when the probabilistic steps
   don't converge.

### Track 2 — Harden the Windows install path

We already have `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md`
but neither it nor the current README covers torch on Windows. Add it.

**`pyproject.toml`:** no change. A preemptive `torch<2.9` pin would force
a downgrade on everyone (the dev macOS is on torch 2.11 and working). The
2.9.0 regression is only known-bad for the 2.9.x line; 2.10+ ships the fix.
Keep the version guidance in README Troubleshooting and the runtime dialog,
not in package metadata.

Also:
- Do **not** add a `cpu-windows = []` placeholder extras entry. `pip
  extras` cannot express per-extra index URLs; a placeholder that does
  nothing is noise. Put the `--index-url` guidance in README instead.
- Future-looking: migrating to uv with `[tool.uv.sources]` PyTorch
  integration (`https://docs.astral.sh/uv/guides/integration/pytorch/`)
  would give us per-extra index URLs and hash-locked torch CPU wheels.
  Out of scope for this plan; flag for the installer-plan revisit.

**`README.md`:**

- Section `### Windows` at line 37 already documents the happy path.
  **Add** a subsection **Windows: PyTorch / Cellpose** that calls out:
  - The MSVC 2015–2022 x64 Redistributable (14.50+) is a hard prereq.
  - The recommended torch install line:
    ```powershell
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
    ```
  - The `--index-url` form is the only published CPU-only path; there is
    no `torch[cpu]` extras syntax (PyTorch docs confirm as of 2026).
- `## Troubleshooting (Windows)` at line 149 already has a C++ Build Tools
  bullet. **Add** a bullet:
  - **`OSError: [WinError 1114] ... c10.dll`** — PyTorch failed to
    initialize. See
    `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md`
    for a full triage; most common fixes are (a) install the MSVC
    Redistributable 14.50+, and (b) reinstall CPU-only torch with the
    command above.

**Cellpose `[gpu]` extras:** document that `pip install -e ".[gpu]"` pulls
CUDA-tagged torch and is unsupported on Windows lab machines without a
matching NVIDIA driver. Add a one-line note in the Optional extras table at
README `:120`.

**Do not ship a `scripts/windows_install.ps1`.** Another surface to rot
when torch URLs or pip semantics change; README already covers it in four
lines.

### Track 3 — Structured worker errors + actionable dialog

The current `Worker.error = Signal(str)` at `gui/workers.py:33` is the
architectural root of the UX problem: every downstream handler re-parses a
string the worker already had structured. Track 3 is one small load-bearing
refactor plus a shared dialog, *not* per-panel fan-out.

**Step 3.1 — Emit structured error data from the worker.**

File: `src/percell4/gui/workers.py`.

```python
# workers.py — sketch
from dataclasses import dataclass
import traceback

@dataclass(frozen=True, slots=True)
class WorkerError:
    exc_type: str           # type(e).__name__
    message: str            # str(e)
    is_import_error: bool   # isinstance(e, (ImportError, OSError))
    winerror: int | None    # getattr(e, "winerror", None) — Windows only
    traceback: str          # traceback.format_exc()

class Worker(QThread):
    error = Signal(object)  # emits WorkerError

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
            if not self._aborted:
                self.finished.emit(result)
        except Exception as e:
            self.error.emit(WorkerError(
                exc_type=type(e).__name__,
                message=str(e),
                is_import_error=isinstance(e, (ImportError, OSError)),
                winerror=getattr(e, "winerror", None),
                traceback=traceback.format_exc(),
            ))
```

Callers change from `worker.error.connect(lambda msg: self._show_status(f"... {msg}"))`
to `worker.error.connect(self._on_worker_error)` with a typed handler.

**Step 3.2 — Pure-Python diagnosis classifier.**

File: `src/percell4/workflows/diagnostics.py` (new). Qt-agnostic so CLI and
batch runners can reuse it without dragging in qtpy.

```python
# src/percell4/workflows/diagnostics.py — sketch
from enum import Enum

class ErrorKind(Enum):
    GENERIC = "generic"
    TORCH_DLL_INIT = "torch_dll_init"      # WinError 1114
    TORCH_DLL_MISSING = "torch_dll_missing" # WinError 126
    TORCH_BITNESS = "torch_bitness"         # WinError 193
    IMPORT_FAILED = "import_failed"         # ImportError, non-native

def classify(err: "WorkerError") -> ErrorKind:
    if err.winerror == 1114 and _looks_like_torch(err.message):
        return ErrorKind.TORCH_DLL_INIT
    if err.winerror == 126 and _looks_like_torch(err.message):
        return ErrorKind.TORCH_DLL_MISSING
    if err.winerror == 193:
        return ErrorKind.TORCH_BITNESS
    if err.exc_type == "ImportError" and "torch" in err.message.lower():
        return ErrorKind.IMPORT_FAILED
    return ErrorKind.GENERIC

def _looks_like_torch(msg: str) -> bool:
    low = msg.lower()
    return "torch" in low or "c10.dll" in low
```

Classification keys on `winerror` codes (deterministic) + `exc_type` +
minimal substring. No DLL-name whack-a-mole.

**Step 3.3 — Shared Qt dialog.**

File: `src/percell4/gui/torch_error.py` (no leading underscore — matches
sibling `workers.py`, `theme.py` convention).

```python
# src/percell4/gui/torch_error.py — sketch
from qtpy.QtWidgets import QMessageBox
from percell4.workflows.diagnostics import ErrorKind, classify
from percell4.gui.workers import WorkerError

_MESSAGES = {
    ErrorKind.TORCH_DLL_INIT: (
        "PyTorch failed to initialize",
        "PyTorch's c10.dll could not load on this machine.\n\n"
        "Most common fix on Windows:\n"
        "1. Install Microsoft Visual C++ 2015-2022 x64 Redistributable 14.50+\n"
        "   (https://aka.ms/vs/17/release/vc_redist.x64.exe)\n"
        "2. If the error persists, reinstall CPU-only torch:\n"
        "   pip install --no-cache-dir --force-reinstall "
        "\"torch<2.9\" --index-url https://download.pytorch.org/whl/cpu\n\n"
        "Full triage: docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md"
    ),
    ErrorKind.TORCH_DLL_MISSING: (
        "PyTorch DLL missing",
        "A PyTorch DLL could not be found. Reinstall CPU-only torch:\n"
        "  pip install --no-cache-dir --force-reinstall torch "
        "--index-url https://download.pytorch.org/whl/cpu"
    ),
    ErrorKind.TORCH_BITNESS: (
        "PyTorch architecture mismatch",
        "The installed PyTorch does not match this Python's architecture "
        "(32-bit vs 64-bit). Recreate the venv with 64-bit Python."
    ),
}

def handle_worker_error(parent, err: WorkerError, *, context: str = "") -> None:
    """Classify a WorkerError and show the matching dialog, or fall back."""
    kind = classify(err)
    if kind in _MESSAGES:
        title, body = _MESSAGES[kind]
        QMessageBox.warning(
            parent, title,
            (f"[{context}]\n\n" if context else "") + body
            + f"\n\nRaw error: {err.exc_type}: {err.message}"
        )
        return
    # Fall back to status bar (caller decides)
    raise UnhandledWorkerError(err)

class UnhandledWorkerError(Exception):
    pass
```

Note: `QMessageBox.warning` (not `.critical`) matches house style — see
`interfaces/gui/main_window.py:359` and `gui/add_layer_dialog.py:134`.

**Step 3.4 — Wire it into the four error sites.**

Each site becomes two lines:

```python
from percell4.gui.torch_error import handle_worker_error, UnhandledWorkerError

def _on_worker_error(self, err):
    try:
        handle_worker_error(self, err, context="Cellpose")
    except UnhandledWorkerError:
        self._show_status(f"Error: {err.exc_type}: {err.message}")
```

Sites:
- `gui/segmentation_panel.py:269` — `context="Cellpose"`
- `gui/grouped_seg_panel.py:297` — `context="Measure"`
- `gui/grouped_seg_panel.py:372` — `context="Grouping"`
- `gui/workflows/single_cell/runner.py:473` — `context="Workflow"`

Only the Cellpose / Workflow sites will ever trigger a dialog in practice
(Measure and Grouping don't import torch), but sharing the helper keeps
behavior uniform and future-proofs against other native-dep errors.

**Preflight probe — still rejected for startup.** Paying `import torch`
latency at every launcher startup is worse than the current one-user bug
report. If we ever add it, the reference implementation is PyTorch's own
`_load_dll_libraries()` in `torch/__init__.py` (ctypes probes of
`vcruntime140.dll`, `msvcp140.dll`, then `c10.dll` with `WinError`
classification). Spyder does the same for `vcruntime140_1.dll` (see
[spyder#25824](https://github.com/spyder-ide/spyder/issues/25824)). Note the
pattern in the adapter comment for future maintainers:

```python
# src/percell4/adapters/cellpose.py — comment only, no preflight yet
# If `from cellpose import models` starts failing for more users, add
# a ctypes.WinDLL("c10.dll") preflight here per PyTorch's
# _load_dll_libraries() and raise a typed TorchUnavailable exception
# so handle_worker_error can classify it without stringly-typed parsing.
```

## Technical Considerations

- **We cannot reproduce this on the development Mac.** Every diagnostic
  must be runnable from the Windows machine with a clear expected output.
  Track 1 instructions are written for that constraint.
- **CPU-only torch is the right default for the lab.** No NVIDIA GPU in
  day-to-day use; the `[gpu]` extra exists for a future workstation.
- **Do not silently set `KMP_DUPLICATE_LIB_OK=TRUE`.** Delete from the plan
  entirely. Ultralytics has a canonical writeup
  ([ultralytics#16652](https://github.com/ultralytics/ultralytics/issues/16652))
  explaining why it silently corrupts numerical results.
- **PyInstaller bundle will recreate this differently.** `hook-torch` ships
  in `pyinstaller-hooks-contrib` so no custom hook is needed, but the
  frozen entrypoint must `import torch` **before** `qtpy`/`PyQt5` to sidestep
  the 2.9.x regression. Add that note when revisiting
  `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` Phase 4.
- **Keep the lazy-import pattern** in `src/percell4/adapters/cellpose.py`.
  Do not move `import torch` to module top-level — launcher must stay
  startable on machines where torch is broken, which is exactly this bug.
  A `@functools.cache`-memoized `torch_import_error()` probe in the adapter
  is a clean future refinement but not load-bearing for this plan.
- **`Worker.error` signal change is technically a breaking API change**
  inside the package. It is a small enough change (one signal, four
  callers) to land in one commit with the callers, so no deprecation shim.

## Acceptance Criteria

### Environment (Track 1)

- [ ] `python -c "import torch; print(torch.__version__)"` succeeds in the
      `E:\percell4\.venv` venv.
- [ ] Cellpose segmentation completes on at least one dataset on the
      Windows machine, with masks written back to the store.
- [ ] The fix that worked is recorded in
      `docs/solutions/build-errors/windows-torch-c10-dll-init-failure.md`
      using the `numpy2-dtcwt-removed-functions.md` frontmatter template
      (title, category, tags, module, symptom, root_cause, severity, date).

### Code (Tracks 2 and 3)

- [x] `README.md` Windows section (line 37) gains a PyTorch/Cellpose
      subsection; Troubleshooting (line 149) gains a `WinError 1114` bullet.
- [x] `Worker.error` emits `WorkerError` dataclass (`Signal(object)`).
- [x] `src/percell4/workflows/diagnostics.py` classifies errors on
      `winerror` codes without Qt imports.
- [x] `src/percell4/gui/torch_error.py` exports `handle_worker_error` using
      `QMessageBox.warning`, matching house style.
- [x] All four worker-error handlers (`segmentation_panel.py:269`,
      `grouped_seg_panel.py:297` and `:372`, `workflows/single_cell/runner.py:473`)
      route through the shared helper (runner uses structured fields for
      failure-record logging; no mid-batch dialog).
- [x] `ruff` and `lint-imports` pass — the diagnostics module does not
      violate the `application`/`workflows` contracts in `pyproject.toml`
      (3 contracts KEPT; new modules pass ruff clean; pre-existing ruff
      issues in the three edited panels are unchanged — not in scope).

### Non-goals

- Not bundling MSVC Redistributable into the repo (licensing / size).
- Not switching Cellpose to a non-torch backend.
- Not adding a startup preflight probe (documented pattern reference only).
- Not writing a Windows CI job (no runner available).
- Not shipping `scripts/windows_install.ps1`.

## Dependencies & Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Pinning `torch<2.9` blocks a future Cellpose release that requires 2.9+ | Low today, rising | Revisit once PyTorch #169429 is fixed in 2.9.x — monitor the issue |
| `Worker.error` signal-type change breaks an un-inventoried caller | Low | Grep for `.error.connect`: four hits per pattern-recognition; landing the callers in the same commit is safe |
| Error-classifier false positive fires the torch dialog on an unrelated OSError | Low | `_looks_like_torch` guard + the raw error is always shown alongside the guidance |
| Corporate Defender re-quarantines c10.dll on every pull | Low | Document "add venv to exclusions" as the permanent fix in README Troubleshooting |
| MSVC 14.50+ unavailable on a locked-down machine | Low | `aka.ms` URL is stable and the MSI is small; IT can deploy |
| PyInstaller bundle silently regresses on a clean Windows | Medium | Cross-referenced in the 2026-03-27 installer plan — import order note |

## References

### Internal

- `src/percell4/adapters/cellpose.py:17` — lazy torch import site.
- `src/percell4/gui/workers.py:33,52-53` — `Signal(str)` to change to
  `Signal(object)` emitting `WorkerError`.
- `src/percell4/gui/segmentation_panel.py:269` — worker-error handler.
- `src/percell4/gui/grouped_seg_panel.py:297,372` — two worker-error
  handlers (Measure, Grouping).
- `src/percell4/gui/workflows/single_cell/runner.py:473` — worker-error
  handler.
- `src/percell4/interfaces/gui/main_window.py:359`,
  `src/percell4/gui/add_layer_dialog.py:134` — existing dialog style
  (`QMessageBox.warning`, short two-part body).
- `README.md:37-80` (Windows install), `:149-156` (Windows Troubleshooting),
  `:120-127` (Optional extras table) — sections to extend.
- `docs/solutions/build-errors/numpy2-dtcwt-removed-functions.md` —
  frontmatter template for the post-resolution solutions entry.
- `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` —
  companion plan; add torch-before-Qt import-order note to Phase 4.
- `pyproject.toml:91-136` — importlinter contracts (`workflows` must stay
  Qt-agnostic; `diagnostics.py` belongs there for that reason).

### External

- [pytorch/pytorch#169429](https://github.com/pytorch/pytorch/issues/169429) —
  torch 2.9.0 Windows DLL init failure.
- [pytorch/pytorch#166628](https://github.com/pytorch/pytorch/issues/166628) —
  WinError 1114 after PyQt import (direct match to PerCell4 pattern).
- [pytorch/pytorch#78490](https://github.com/pytorch/pytorch/issues/78490) —
  libiomp5 duplicate init.
- [pytorch/pytorch#126507](https://github.com/pytorch/pytorch/issues/126507) —
  MSVC Redistributable detection discussion.
- [spyder-ide/spyder#25824](https://github.com/spyder-ide/spyder/issues/25824) —
  Spyder `WinError 1114` with ctypes DLL probe pattern.
- [ultralytics/ultralytics#16652](https://github.com/ultralytics/ultralytics/issues/16652) —
  `KMP_DUPLICATE_LIB_OK` correctness warning.
- [PyTorch Windows FAQ](https://docs.pytorch.org/docs/stable/notes/windows.html).
- [PyTorch Get Started (selector)](https://pytorch.org/get-started/locally/) —
  canonical `--index-url https://download.pytorch.org/whl/cpu`.
- [uv + PyTorch integration guide](https://docs.astral.sh/uv/guides/integration/pytorch/) —
  per-extra index URL support for the future installer revisit.
- [Microsoft: Redistribute Visual C++ Files](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files) —
  canonical registry probe paths.
- [`aka.ms/vs/17/release/vc_redist.x64.exe`](https://aka.ms/vs/17/release/vc_redist.x64.exe) —
  current MSVC 2015-2022 x64 Redistributable download.
- [MouseLand/cellpose README](https://github.com/mouseland/cellpose) —
  Cellpose 3.x/4.x install guidance (no Windows-specific DLL notes).
- [pyinstaller-hooks-contrib CHANGELOG](https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/CHANGELOG.rst) —
  `hook-torch` updates for the installer plan.
