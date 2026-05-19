---
date: 2026-05-14
topic: windows-via-wsl-install-path
status: requirements
---

# Windows via WSL Install Path

## Problem Frame

Native Windows installation of PerCell4 has been a recurring source of pain. The lab Windows machine at `E:\percell4` hit `OSError: [WinError 1114]` on `import torch` during Cellpose segmentation, driven by a torch 2.9.0 + Qt-imports-first DLL-order regression and aggravated by a stale MSVC Redistributable. The 584-line triage plan in `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md` documents the rabbit hole. Beyond that one bug, the native install path also has to defend against duplicate `libiomp5md.dll`, accidental CUDA wheels, Windows Defender quarantine, PATH contamination, and PowerShell execution policy — none of which are PerCell4 bugs and all of which keep coming back.

Rather than keep paying that maintenance tax, the lab is pivoting: **Windows users will run PerCell4 inside WSL2 (Ubuntu) with WSLg for the GUI**, treating Windows as a thin host rather than the runtime target. The Linux install path is already proven on the macOS dev environment and avoids every category of failure listed above.

This brainstorm captures the requirements for that pivot: the official install recipe, the bootstrap script that operationalizes it, and the doc cleanup that retires the native Windows surface.

---

## Actors

- A1. **Lee Lab Windows user.** Installs and runs PerCell4 on a personal lab Windows machine. May or may not have a GPU. Comfortable opening a terminal and running commands but is not expected to debug PATH issues or DLL loaders.
- A2. **External researcher (Phase 2 audience).** A non-lab user who picks up the repo months from now. Needs the documented install recipe to "just work" without anyone holding their hand. Will judge the project's professionalism by whether the install instructions match their machine.

---

## Key Flows

- F1. **Fresh-install flow (new Windows machine, lab user)**
  - **Trigger:** A1 has a Windows 11 machine (or Win 10 22H2+) without PerCell4 and wants to start using it.
  - **Actors:** A1.
  - **Steps:**
    1. From an elevated PowerShell, run `wsl --install -d Ubuntu-24.04` and reboot when prompted.
    2. Open Ubuntu, set Linux username/password.
    3. (GPU machines only) Confirm Windows-side NVIDIA driver is R555+ and `nvidia-smi` runs inside Ubuntu.
    4. `git clone <repo> ~/percell4 && cd ~/percell4`
    5. Run `./scripts/install_wsl.sh`. Script installs apt deps, creates `.venv`, installs PerCell4 (auto-detecting GPU vs CPU torch), runs a non-GUI smoke test.
    6. Run `source .venv/bin/activate && python main.py`. WSLg renders the launcher window on the Windows desktop.
  - **Outcome:** PerCell4 launches and runs Cellpose against a test dataset in `/mnt/d/…` (or the user's chosen path).
  - **Covered by:** R3, R4, R5, R6, R7, R8.

- F2. **Data-access flow (existing dataset on D:\\)**
  - **Trigger:** A1 wants to open an HDF5 project at `D:\experiments\2026-05-12.h5`.
  - **Actors:** A1.
  - **Steps:**
    1. From PerCell4's File → Open dialog, browse to `/mnt/d/experiments/`.
    2. Select the `.h5` file and open it.
    3. For heavy passes (Cellpose segmentation, large phasor recomputes) where I/O over `/mnt/d` is noticeably slower than ext4, optionally copy the active `.h5` to `~/percell4-scratch/` and reopen.
  - **Outcome:** The project loads and analyses run. The user has been told once, in the docs, why and when to copy to WSL home.
  - **Covered by:** R5, R10.

- F3. **First-run on a machine without WSLg or modern Windows**
  - **Trigger:** A1 or A2 attempts F1 on Windows 10 pre-22H2, Windows 11 with virtualization disabled in BIOS, or a corporate-locked machine that cannot install WSL.
  - **Actors:** A1 / A2.
  - **Steps:**
    1. User runs `wsl --install` and hits an error (or has no WSLg, blank windows on launch).
    2. Troubleshooting section in the README names the most common causes (enable virtualization in BIOS, run `wsl --update`, ensure Windows is up to date, check `wsl --version` reports WSLg) and points to Microsoft's WSL docs.
    3. If unsupportable (e.g., locked-down Win 10 LTSB), the user is told plainly: PerCell4 does not support this Windows configuration. Use a different machine or a macOS / Linux host.
  - **Outcome:** User either fixes the prerequisite or knows the project will not run on this machine — no time sunk on a futile native install attempt.
  - **Covered by:** R3, R11.

---

## Requirements

**Feasibility validation (gating)**
- R1. Before any code or doc changes, run a feasibility spike on one affected Windows machine: install WSL2 + Ubuntu 24.04, install PerCell4 by hand following the macOS recipe (adapted for apt), launch the GUI via WSLg, open an existing `/mnt/d/...` dataset, run one Cellpose pass, run one phasor pass. If WSLg cannot render napari acceptably on that machine's GPU/driver combination, the brainstorm reopens before any docs are rewritten. The spike's outcome (pass/fail + machine spec + driver versions) is recorded in `docs/install/wsl-spike-notes.md` and referenced from this doc.

**Bootstrap script**
- R2. A single script at `scripts/install_wsl.sh` performs the end-to-end install inside a fresh Ubuntu 24.04 WSL instance. The script is the supported install path; manual step-by-step instructions exist in the README only as fallback documentation, not as the primary recipe.
- R3. The script requires Ubuntu 24.04 (or another version validated by the spike). On unsupported Ubuntu versions it exits early with a clear message naming the supported version.
- R4. The script installs the apt-side system dependencies needed by PyQt5, napari, and pyqtgraph under WSLg in a single `apt-get install` call. The set must include the libraries that produce non-obvious failures when missing (Qt platform plugins, EGL/GL stack, X11/xcb cursor and keyboard libs). The exact list is finalized during the spike and pinned in the script with a comment naming what each library prevents.
- R5. The script creates a `.venv` at the repo root with Python 3.12 and installs PerCell4 via `pip install -e ".[dev]"`. If `nvidia-smi` succeeds inside WSL, it additionally appends the `gpu` extra and reinstalls torch from the CUDA wheel index. If `nvidia-smi` does not exist or fails, it explicitly installs torch from the CPU wheel index (`--index-url https://download.pytorch.org/whl/cpu`). The CPU/GPU branch chosen is logged.
- R6. The script ends with a non-GUI smoke test: `python -c "from percell4.app import main; print('install_wsl: ok')"`. Non-zero exit fails loudly with the captured stderr.
- R7. The script is idempotent — running it twice in a row on the same WSL instance succeeds without side effects, and re-running it after editing dependencies upgrades the environment correctly.

**README rewrite**
- R8. The README's current `### Windows`, `#### Command Prompt`, `#### PowerShell`, `#### Git Bash`, and `#### Windows: PyTorch / Cellpose` subsections (`README.md:37` through `:108`) are replaced by a single `## Windows (via WSL)` section. The new section names the prerequisites (Windows 11 or Windows 10 22H2+; virtualization enabled in BIOS; ≥30 GB free; for GPU, NVIDIA driver R555+ on the Windows host), the three-line install (`wsl --install -d Ubuntu-24.04`, `git clone …`, `./scripts/install_wsl.sh`), and how to launch (`python main.py`).
- R9. The README's `## Troubleshooting (Windows)` section (`README.md:171`) is replaced with `## Troubleshooting (Windows via WSL)` covering the WSL-specific failure modes the spike surfaces. The native-Windows entries (`py is not recognized`, PowerShell execution policy, `Activate.ps1`, `percell4-gui` not recognized, the `c10.dll` WinError 1114 walkthrough, MSVC Redistributable guidance) are removed because they are no longer reachable on the supported path. The entries that remain include at minimum: WSLg blank window / no display, missing Qt apt libs, `/mnt/d` performance and the "copy active `.h5` to `~/percell4-scratch`" workaround, NVIDIA driver too old on the Windows host, and "what to do when `wsl --install` itself fails."
- R10. The Optional extras table at `README.md:142` is updated: the `gpu` extra's note no longer carries the Windows-host caveat (since GPU is now expected to work on WSL + CUDA passthrough when the host driver is current) and instead documents the NVIDIA host driver floor.
- R11. The README states explicitly that PerCell4 is **unsupported on Windows without WSL**. There is no native Windows install path documented. A short pointer in the new `## Windows (via WSL)` section explains what changed and why, so users coming from older docs don't re-derive the deprecated recipe.

**Archival**
- R12. `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` and `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md` are moved to `docs/archive/` with a one-line preamble noting that PerCell4 deprecated native Windows in favor of WSL on 2026-05-14 and pointing back to this requirements doc.
- R13. Any per-module CLAUDE.md or solution doc that references the native Windows install — including `docs/solutions/` entries tagged `windows`, if any exist — is updated or archived so that no contradictory recommendation survives in the loaded-context surface. (Per the project's context-poisoning rule: no two docs giving incompatible install instructions.)

**Code hygiene**
- R14. Code paths that exist only to defend against Windows-native quirks are reviewed and may be removed or simplified once the WSL-only stance lands. Specifically: the `_atomic_replace` Windows branch in `src/percell4/project.py:120` and `src/percell4/store.py:300` (from the windows-compat plan), if it exists in the current code, becomes dead code — because WSL is Linux from the file-system semantics standpoint — and either gets removed or earns a comment justifying retention. The `pyproject.toml` `gpu` extra and its CUDA notes stay (they help WSL users).
- R15. The `gui/torch_error.py` shared dialog, `workflows/diagnostics.py` classifier, and `WorkerError` dataclass from the c10.dll plan (Track 3) are kept — they harden the error UX for *any* torch import failure, including the rarer ones that can still occur under WSL (e.g., wrong CUDA wheel for the host driver). Only the Windows-specific `WinError` branches and message strings are softened to talk in WSL terms.

---

## Acceptance Examples

- AE1. **Covers R5.** On a Windows 11 machine with an NVIDIA GPU and host driver R555, `./scripts/install_wsl.sh` detects the GPU via `nvidia-smi`, installs torch with CUDA support, and the smoke test `python -c "from percell4.app import main; print('install_wsl: ok')"` prints `install_wsl: ok`. On the same hardware with the NVIDIA driver uninstalled, the same script falls back to the CPU torch wheel without manual intervention and the smoke test still passes.

- AE2. **Covers R7.** Running `./scripts/install_wsl.sh` twice consecutively on a freshly-bootstrapped WSL instance results in two successful runs. The second run completes in well under half the time of the first (apt cache + already-installed pip wheels) and does not re-run the `apt install` step interactively — i.e., it does not prompt for `[Y/n]`.

- AE3. **Covers R9.** On WSL, attempting to launch PerCell4 with missing `libxcb-cursor0` produces a recognizable Qt-platform-plugin error. The Troubleshooting section names the exact apt package and the one-line fix (which is normally a no-op because `install_wsl.sh` installs it). The user resolves the issue in under five minutes without external search.

- AE4. **Covers R11, R12.** A user searching the repo for "Windows install" finds the README's `## Windows (via WSL)` section and this requirements doc. They do not find a current plan, README section, or solutions entry that recommends installing on native Windows. The two archived plans appear under `docs/archive/` with a preamble pointing at the WSL path.

---

## Success Criteria

- **Human outcome (lab).** A Lee Lab member who has never used WSL can go from a fresh Windows 11 machine to PerCell4 running their first Cellpose pass in under 90 minutes, following the README plus running `install_wsl.sh`, with no hands-on help from the maintainer.
- **Human outcome (external).** An external researcher cloning the repo six months later follows the same README path and gets the same result without surfacing any contradictory or stale install advice elsewhere in the docs.
- **Failure-mode retirement.** The four failure classes that drove this pivot — WinError 1114 (torch DLL init), MSVC Redistributable mismatch, duplicate `libiomp5md.dll`, accidental CUDA wheel on a non-GPU box — are unreachable on the supported path. The maintainer's Windows-debugging time per week drops to zero on average.
- **Downstream agent handoff.** A planning agent invoked on `-> /ce-plan` has enough product detail in this doc to scope the spike, the script, the README rewrite, and the archival without re-asking the user any product decisions. Implementation choices (exact apt package set, exact WSL Ubuntu version, exact CUDA wheel index URL) are correctly deferred to planning and resolved during the spike.

---

## Scope Boundaries

- **No native Windows install path.** Explicitly deprecated and removed. Not parked, not "kept as fallback for users without WSL." Users who cannot run WSL are not supported on Windows.
- **No PyInstaller standalone bundle for Windows.** Phase 4 of `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` (PyInstaller `.exe` for non-technical users) is archived with the rest of that plan and not revived under WSL. macOS `.app` bundling is unaffected and continues to be the right answer for non-technical Mac users; the analogous Windows surface stays empty for now.
- **No Docker / containerization path.** Docker Desktop on Windows uses WSL2 underneath but adds a worse GUI story than direct WSLg. Not pursued.
- **No web/server frontend pivot.** Running napari/Qt remains the way users interact with PerCell4. Refactoring toward a browser frontend is a different product, not a Windows-install fix.
- **No CI for the WSL install path in v1.** Approach C (GitHub Actions install-smoke-test job + verified-machines matrix) was considered and explicitly deferred. Revisit once external users start filing install issues, or once the script changes frequently enough to warrant regression coverage.
- **No `.wsl` rootfs distribution.** Building and hosting a pre-baked Ubuntu image with PerCell4 inside it was considered and rejected for v1 (host/update overhead too large for current audience).
- **No accommodation for old Windows.** Windows 10 pre-22H2, Windows 7, Windows Server, and corporate-locked Windows that cannot install WSL are out of scope. Users on these machines see a clear "not supported" pointer, not a workaround.
- **No multi-distro support.** Ubuntu 24.04 is the only documented and scripted target. Debian, Arch, Fedora WSL distros may work but are not supported in v1.
- **No SMB / fileserver direct-mount feature.** Lab fileserver data accessed via a Windows drive letter goes through `/mnt/<letter>/` like any other Windows drive. Native `cifs-utils` mounts inside WSL are out of scope for v1 (most lab use stays on local D:\\ / E:\\).

---

## Key Decisions

- **WSL replaces native Windows; it does not supplement it.** Keeping both paths would re-create the context-poisoning problem the move is designed to solve. Single canonical install path per OS.
- **One script, idempotent, not many small ones.** A single `install_wsl.sh` covers apt + venv + pip + GPU detection + smoke test. Rejected splitting into `install_deps.sh`, `install_torch.sh`, etc. — the failure mode this script defends against is "user runs them out of order or skips one."
- **GPU auto-detection happens in the script, not via `pip extras` toggles.** Users do not have to know in advance whether to type `.[dev]` or `.[dev,gpu]`. `nvidia-smi`-presence is the source of truth.
- **Ubuntu 24.04 is the target.** Not "any distro the user has." A single target reduces the apt-package matrix and the spike's surface. Revisit if Microsoft changes the default distro or if 24.04 drops out of support.
- **Data stays on Windows drives by default; `/mnt/d` slowness is documented, not solved.** No tooling to automate copying data into WSL home. Users who feel the cliff can copy manually; the docs name the symptom and the workaround.
- **Track 3 of the c10.dll plan (structured worker errors + classifier dialog) survives the pivot.** It hardens any future torch import failure, not just the Windows-specific class. Only the message strings change.

---

## Dependencies / Assumptions

- **Assumption (to verify in spike R1):** WSLg renders napari + pyqtgraph + Qt without GPU-driver-specific artifacts on the affected lab machine(s). Known historical issues (Intel iGPU OpenGL quirks under WSLg, HiDPI scaling) are believed resolved on current Win 11 + recent NVIDIA / Intel drivers but have not been confirmed for this codebase.
- **Assumption (to verify in spike R1):** Cellpose segmentation completes successfully under WSL on at least one dataset of the size lab users typically run. Specifically that no large-memory or large-file step fails inside the WSL VM's default memory limit. If it does, document `.wslconfig` `memory=` and `processors=` tuning in the README troubleshooting section.
- **Assumption:** The current `pip install -e ".[dev]"` and `".[gpu]"` commands continue to work on Ubuntu 24.04 as they do on macOS — i.e., no Linux-specific dependency is missing from `pyproject.toml`. This is a low-risk assumption (most deps are pure-Python or have manylinux wheels), but the spike will surface any gap.
- **Dependency:** Microsoft's WSL2 + WSLg + CUDA-on-WSL support on the target Windows versions. Documented but not under our control. If Microsoft regresses, our docs become incorrect — we accept that risk because the platform is unlikely to remove these capabilities at this point.
- **Dependency:** NVIDIA's WSL CUDA driver path. R555+ on the Windows host is the recommended floor; older drivers may technically work but are out of scope to support.

---

## Outstanding Questions

### Resolve Before Planning

*(none — the brainstorm resolved the product-level decisions.)*

### Deferred to Planning

- [Affects R4][Needs research] Exact apt-package set needed for PyQt5 + napari + pyqtgraph under WSLg on Ubuntu 24.04. Discoverable during the spike (R1) by deliberately omitting deps and capturing the failure messages. The script comments will name what each entry prevents.
- [Affects R5][Technical] Which torch CUDA wheel index URL to pin for the GPU path (`cu121`, `cu126`, etc.). Depends on the Windows host driver floor decided in R10 and the torch version Cellpose currently supports. Resolved in the spike.
- [Affects R7][Technical] How `install_wsl.sh` detects "fresh run vs re-run" for idempotency — naive approach is "if `.venv/` exists, skip venv creation and just `pip install -U`." Acceptable for v1; revisit if it bites.
- [Affects R8][Technical] Where the NVIDIA driver floor sits exactly (R495 is Microsoft's documented minimum for CUDA-on-WSL; R555+ is recommended for current PyTorch CUDA wheels). Pin during the spike against the actual lab GPU machine.
- [Affects R13][Technical] Whether any current `docs/solutions/` entries (e.g., a Windows-tagged Cellpose or torch install learning) need updating or archiving alongside R12. A `grep -ril windows docs/solutions/` during planning will surface the candidate list.

---

## Next Steps

-> `/ce-plan` for structured implementation planning. The plan should treat the R1 spike as its first phase and gate the subsequent script + README + archival phases on the spike's outcome.
