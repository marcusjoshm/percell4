---
title: "feat: Windows via WSL install path"
type: feat
status: active
date: 2026-05-14
origin: docs/brainstorms/2026-05-14-windows-via-wsl-install-path-requirements.md
---

# Windows via WSL Install Path

## Overview

PerCell4 is deprecating native Windows installation in favor of WSL2 + Ubuntu 24.04 + WSLg. The native path has produced recurring failures (most recently `OSError: [WinError 1114]` on `import torch`, requiring a 584-line triage plan) that are categorically gone on Linux. This plan implements the pivot: a gating feasibility spike, a single bootstrap script that operationalizes the install, a README rewrite that retires the native path, archival of the two superseded native-Windows plans, and the small ripple updates needed in docstrings, CLAUDE.md, and one solutions entry to keep the docs free of contradictions.

The plan deliberately keeps the surface small. No CI, no `.wsl` rootfs distribution, no Docker path, no native fallback. One supported Windows recipe.

---

## Problem Frame

Lee Lab Windows users have been hitting native-install failures the project keeps having to defend against — torch DLL init failures, MSVC Redistributable mismatches, `libiomp5md.dll` duplicates, accidental CUDA wheels, PowerShell execution policy, Windows Defender quarantine of `c10.dll`. None of these are PerCell4 bugs; all of them keep coming back. The macOS install path has none of these problems. WSL2 + Ubuntu makes the Windows install look like the macOS install.

The pivot's audience is Lee Lab now, external researchers later. There is no requirement to support old Windows, corporate-locked machines that cannot install WSL, or non-NVIDIA GPUs. There is a requirement that the install recipe match how lab users actually work (data on local Windows D:\\/E:\\ drives, accessed under WSL as `/mnt/d` etc.) and that GPU machines can use Cellpose on GPU.

See origin: `docs/brainstorms/2026-05-14-windows-via-wsl-install-path-requirements.md`.

---

## Requirements Trace

- R1. Feasibility spike on one affected Windows machine before any code/doc changes (see origin R1).
- R2. Single bootstrap script at `scripts/install_wsl.sh` as the supported install path (origin R2–R7).
- R3. README's native Windows install path replaced with `## Windows (via WSL)` (origin R8).
- R4. README troubleshooting replaced with WSL-specific gotchas; native-Windows entries removed (origin R9).
- R5. Optional extras table updated; `gpu` no longer carries Windows-host caveat (origin R10).
- R6. Explicit "unsupported on Windows without WSL" statement in README (origin R11).
- R7. Archive `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` and `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md` with inbound references updated (origin R12).
- R8. Update or archive any `docs/solutions/` entries that reference the deprecated native path (origin R13).
- R9. Confirm `_atomic_replace` Windows branch does not exist (origin R14, already known true per research). Soften `gui/torch_error.py` copy to reference WSL (origin R14/R15).
- R10. Compound learning(s) captured before merge so no follow-up commits land on `main` (per `docs/solutions/workflow-issues/complete-branch-before-merge-2026-05-06.md`).

**Origin actors:** A1 (Lee Lab Windows user), A2 (external researcher).
**Origin flows:** F1 (fresh install), F2 (data access from D:\\), F3 (machine without WSLg / modern Windows).
**Origin acceptance examples:** AE1 (GPU detection + smoke test), AE2 (idempotency), AE3 (libxcb-cursor0 troubleshooting), AE4 (no contradictory docs survive).

---

## Scope Boundaries

- **No native Windows install path.** Removed from README, plans archived. Users who cannot run WSL are not supported on Windows.
- **No PyInstaller bundle for Windows.** Phase 4 of the archived 2026-03-27 plan is not revived. macOS `.app` bundling is unaffected.
- **No CI for the WSL path.** Smoke test runs locally during the spike and in the pytest suite (where feasible without a real WSL host). No GitHub Actions install-smoke-test job in v1.
- **No `.wsl` rootfs distribution.** No pre-baked Ubuntu image.
- **No Docker path.** WSLg is the GUI surface.
- **No multi-distro support.** Ubuntu 24.04 only.
- **No SMB-mount-from-WSL feature.** Data on Windows drives goes through `/mnt/<letter>/`; native `cifs-utils` mounts are out of scope.
- **No support for Windows without WSL.** Win 10 pre-22H2, Win 7, Win Server, corporate-locked machines that cannot install WSL are explicitly unsupported.

### Deferred to Follow-Up Work

- **Promote `WorkerError`/`diagnostics`/`torch_error` to a `docs/solutions/architecture-patterns/` entry + canonical-sources matrix registration.** The pattern is durable beyond Windows triage but is not registered today. Out of scope for this plan; do as a small follow-up after the pivot lands so the institutional pattern survives the archived plan it was originally documented inside.
- **Consolidate the three atomic-write implementations** (`store.DatasetStore.create_atomic`, `project.ProjectIndex._write_atomic`, `workflows/artifacts.py:write_atomic`) into a single canonical helper. Flagged `pre_canonical` in `docs/solutions/architecture-patterns/atomic-write-contract.md`. Independent of the WSL pivot; not load-bearing.
- **Split `pyproject.toml`'s `dev` extra so it does not transitively pull `gpu`.** Today `dev → all → gpu` makes `.[dev]` and `.[dev,gpu]` equivalent. The script works around this by reinstalling torch from the correct index; cleaning up the extras to make `.[dev]` actually CPU-friendly is a worthwhile follow-up, but not required for the WSL recipe to work.
- **GitHub Actions install-smoke-test + verified-machines matrix.** Approach C from the brainstorm. Reconsider once external users start filing install issues.

---

## Context & Research

### Relevant Code and Patterns

- `scripts/diagnose_bin_orientation.py` — argparse + `pathlib.Path` + `int`-returning `main()` + `sys.exit(main())` guard. This is the existing-script style template for `install_wsl.sh`'s structure, even though that script is bash, not Python.
- `scripts/learnings_applicability.py` and `scripts/claude_code_hooks/check_learnings_retrieval.py` — stdlib-only, exit-code conventions (`1` for fail, `2` for usage error). Useful as the convention to mirror.
- `tests/test_scripts/test_check_learnings_retrieval.py` — subprocess-driven test pattern; spawns the script, asserts on returncode + captured streams. The model for `install_wsl.sh`'s pytest smoke test.
- `src/percell4/workflows/diagnostics.py:10` and `src/percell4/gui/torch_error.py:9, 32` — current docstrings cite the to-be-archived c10.dll plan path. These references must update in the archive commit.
- `src/percell4/gui/torch_error.py` — `_TITLES_AND_BODIES` dict carries the Windows-native messaging. Bodies need WSL-aware copy.
- `README.md` — verified rewrite coordinates: `:21-35` macOS (untouched, useful as the "short" template), `:37-108` native Windows (replaced wholesale), `:142-149` Optional extras (gpu row text updated), `:171-179` Windows troubleshooting (replaced wholesale).
- `docs/archive/README.md` and `docs/archive/2026-03-28-refactor-atomic-state-signals-original.md:1-6` — the SUPERSEDED-banner template for plan archival.
- `pyproject.toml:55-74` — extras unchanged for this plan, but `dev` transitively pulls `gpu` via `all`; the bootstrap script accounts for this.

### Institutional Learnings

- `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md` (2026-03-27) — direct parent of the to-be-archived 2026-03-27 plan. Codifies the rule that `os.replace` is atomic on Windows since Python 3.3 (no platform branching needed) and lists the PyInstaller/upx/CLI-entry-point anti-patterns the WSL pivot structurally eliminates. **R8 target:** update this entry's `## Related Plans` section, since the plan it references is being archived.
- `docs/solutions/architecture-patterns/atomic-write-contract.md` (2026-04-30) — convention is "no `if os.name == 'nt'` branches." Reaffirms that the code-hygiene step has no Windows branch to remove.
- `docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md` (2026-04-17) — the pattern for clean hygiene sweeps. If anything Windows-shaped remains in the code, it gets deleted in this same commit, not parked.
- `docs/solutions/workflow-issues/premature-pattern-codification-2026-05-14.md` (2026-05-14) — load-bearing guardrail. **Do not write a `docs/solutions/conventions/wsl-bootstrap-pattern.md` until the spike has survived at least one design cycle.** A compound learning capturing what was learned during the spike *is* appropriate; codifying conventions before the spike validates them is not.
- `docs/solutions/workflow-issues/complete-branch-before-merge-2026-05-06.md` (2026-05-06) — load-bearing for merge hygiene. README rewrite + script + archive moves + inbound-reference updates + compound learning all land on one branch.

### External References

External research was assessed and skipped. The unresolved items the spike will answer (apt package set, NVIDIA driver floor for current CUDA-on-WSL torch wheels, exact CUDA wheel index URL) are explicitly deferred to spike validation in the origin doc, and the spike's authoritative source for each is the running Ubuntu instance and PyTorch's own selector — pre-resolving them now would just produce guesses the spike has to validate anyway.

---

## Key Technical Decisions

- **Spike gates every other unit.** No README rewrite, no archival, no script polish before the spike confirms WSLg renders the GUI acceptably and Cellpose completes on the target machine. If the spike fails, the brainstorm reopens. This is non-negotiable (origin R1).
- **Single bash script, idempotent, in `scripts/install_wsl.sh`.** First shell script in the repo, sets the convention. `#!/usr/bin/env bash`, `set -euo pipefail`, plain `echo` for progress (mirroring the diagnose script's stdout style), short `--help` flag, `--dry-run` flag for the pytest smoke test.
- **GPU vs CPU branch happens at torch reinstall, not via extras.** Today `pip install -e ".[dev]"` already pulls `cellpose[gpu]` via `dev → all → gpu`. The script always runs `pip install -e ".[dev]"`, then *conditionally reinstalls torch* from `--index-url https://download.pytorch.org/whl/cpu` or the appropriate CUDA index based on `nvidia-smi` exit code. This avoids a `pyproject.toml` change for the pivot. Rationale: keeps the pivot's surface small; the extras cleanup is a worthwhile follow-up but not load-bearing.
- **Archive moves change file paths; inbound references update in the same commit.** Four current inbound references to the to-be-archived plans: `src/percell4/workflows/diagnostics.py:10`, `src/percell4/gui/torch_error.py:9, 32`, `README.md:178`. All update in the archival commit per the branch-before-merge rule.
- **Archive uses `git mv` + SUPERSEDED banner.** Matches `docs/archive/2026-03-28-refactor-atomic-state-signals-original.md:1-6`. No frontmatter changes; banner explains what replaced the plan and points back to this plan + the brainstorm.
- **`gui/torch_error.py` copy is softened, not deleted.** The classifier still fires on `winerror` codes (only possible on Windows-native Python), so its content path is unreachable under WSL. Keeping the dialog as a "you appear to be running PerCell4 on native Windows; that is no longer supported — install via WSL2" pointer makes the deprecated path fail loudly with guidance, rather than crashing silently. The whole module stays; only `_TITLES_AND_BODIES` strings change.
- **CLAUDE.md gets a one-line platform-support clause.** "Supported platforms: macOS native, Linux native, Windows via WSL2 + Ubuntu 24.04 + WSLg." Prevents a future agent from reintroducing native-Windows assumptions.
- **Pytest smoke test for the script uses `--dry-run`.** A real install can't run in unit tests (would mutate the developer's environment). The script's `--dry-run` mode prints the planned steps and exits 0; pytest verifies the dry-run output mentions the expected sections (apt, venv, pip, torch override, smoke test).

---

## Open Questions

### Resolved During Planning

- **Does an `_atomic_replace` Windows branch exist that needs removing?** No (confirmed by repo research). R14 narrows to "no work; reconfirm in a comment on the relevant atomic-write code, if anywhere."
- **Are the structured worker errors built?** Yes — `workflows/diagnostics.py`, `gui/workers.py`, `gui/torch_error.py`. They stay; only copy changes.
- **Are there other inbound references to the archived plans?** Yes, four — listed in Key Technical Decisions.
- **Which solutions entries need touching?** One: `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md`.
- **How do we archive plans here?** `git mv` + SUPERSEDED banner per `docs/archive/README.md`.
- **Should `dev` and `all` extras be restructured?** Not in this plan — deferred to follow-up. The bootstrap script works around the transitivity at install time.
- **How should the script encode CPU/GPU branching?** At the torch reinstall step (after `pip install -e ".[dev]"`), not at the extras key.

### Deferred to Implementation

- **Exact apt package set for PyQt5 + napari + pyqtgraph + Qt platform plugins under WSLg on Ubuntu 24.04.** Resolved during the U1 spike by deliberately omitting packages and capturing the failure messages. Plan seeds the script with a reasonable starting list (qtbase platform plugins, EGL/GL, X11/xcb cursor and keyboard libs) but the spike confirms.
- **Exact CUDA wheel index URL** (`cu121`, `cu126`, etc.) and the NVIDIA-driver-floor on the Windows host. Pinned at spike time against PyTorch's current "Get Started" selector and the actual lab GPU machine's host driver. Recorded in the script as a comment naming the cutoff.
- **Whether the script's `--dry-run` mode warrants a richer test surface** (e.g., asserting on the actual apt list, not just "an apt step exists"). Acceptable starting point: assert the section headers in dry-run output are present and exit code is 0. Tighten if regressions appear.
- **Whether `tests/test_scripts/` is the right home** for the smoke test or if it should live elsewhere. Mirror existing convention unless something obviously breaks.
- **Whether to keep `gui/torch_error.py`'s `show_msvc_redist_warning` startup probe.** Probably retire it (no MSVC Redist concern under WSL), but confirm during U6 that nothing currently wires it into app startup in a way that's load-bearing.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```mermaid
sequenceDiagram
    participant U as Lee Lab user
    participant W as Windows host
    participant L as WSL2 Ubuntu 24.04
    participant S as install_wsl.sh
    participant P as PerCell4 (.venv)

    U->>W: wsl --install -d Ubuntu-24.04
    W->>L: provision rootfs, start shell
    U->>L: git clone <repo> ~/percell4
    U->>L: cd ~/percell4 && ./scripts/install_wsl.sh
    S->>L: apt-get install <Qt+napari deps>
    S->>L: python3.12 -m venv .venv
    S->>P: pip install -e ".[dev]"
    S->>L: nvidia-smi (probe)
    alt GPU detected
        S->>P: pip install --force-reinstall torch --index-url cu<ver>
    else CPU only
        S->>P: pip install --force-reinstall torch --index-url whl/cpu
    end
    S->>P: python -c "from percell4.app import main; print('install_wsl: ok')"
    U->>P: source .venv/bin/activate && python main.py
    P->>W: render Qt windows via WSLg
```

The script's CPU/GPU branch sits *after* the editable install. This is the load-bearing design choice — it lets `.[dev]` stay as-is, and the post-install torch override does the platform-specific work cleanly.

---

## Output Structure

The plan does not create a new directory hierarchy. New files land in existing directories:

- `scripts/install_wsl.sh` (new)
- `tests/test_scripts/test_install_wsl.py` (new)
- `docs/install/wsl-spike-notes.md` (new — spike record)
- `docs/archive/2026-03-27-feat-windows-compat-and-installer-plan.md` (moved from `docs/plans/`)
- `docs/archive/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md` (moved from `docs/plans/`)

Everything else is in-place edits.

---

## Implementation Units

- U1. **WSL feasibility spike**

**Goal:** Confirm WSL2 + Ubuntu 24.04 + WSLg can run PerCell4 acceptably on one target Lee Lab Windows machine before the rest of the plan executes. Produce a written record of the exact apt package set, CUDA wheel URL, and NVIDIA driver version that worked.

**Requirements:** R1.

**Dependencies:** None — this gates every other unit.

**Files:**
- Create: `docs/install/wsl-spike-notes.md`

**Approach:**
- On the target machine, install WSL2 (`wsl --install -d Ubuntu-24.04`), confirm `wsl --version` reports WSLg.
- Inside Ubuntu: install Python 3.12, system deps, manually run the macOS-style install (`pip install -e ".[dev]"`), then conditionally swap torch to the right wheel.
- Run the full happy path: launch `python main.py`, open an existing `/mnt/d/...` HDF5 dataset, run one Cellpose pass, run one phasor pass.
- Record outcomes in `docs/install/wsl-spike-notes.md`: machine spec (CPU, RAM, GPU model, Windows version), NVIDIA driver version on the host, Ubuntu kernel + WSL version, the final working apt package list (the one that made `python main.py` launch cleanly), the final torch wheel index URL, any WSLg quirks observed, and a clear pass/fail verdict.
- If the spike fails (WSLg cannot render napari acceptably, or Cellpose fails non-recoverably), stop the plan and reopen the brainstorm.

**Execution note:** Exploratory by nature — no test scaffolding upfront. The artifact is the spike-notes doc, not code. Treat this as the discovery pass that feeds U2.

**Patterns to follow:**
- The doc structure should mirror `docs/solutions/*` entries (frontmatter with `title`, `category: install`, `tags: [windows, wsl, wslg, cellpose, install]`, `date`) so it can be cross-referenced cleanly even though it lives under `docs/install/`.

**Test scenarios:**
- Test expectation: none — manual feasibility spike with documented outcome. The doc artifact is the deliverable.

**Verification:**
- `docs/install/wsl-spike-notes.md` exists and includes the working apt list, working torch wheel URL, NVIDIA driver version, and a pass verdict.
- On the spike machine: `python main.py` launches the GUI, opens a real dataset from `/mnt/d/...`, completes one Cellpose pass and one phasor pass without errors.

---

- U2. **`scripts/install_wsl.sh` bootstrap script**

**Goal:** A single idempotent script that takes a fresh Ubuntu 24.04 WSL instance to a working PerCell4 install. CPU/GPU torch wheel auto-detected via `nvidia-smi`.

**Requirements:** R2 (origin R2–R7).

**Dependencies:** U1 (the apt list and torch URL the script encodes are confirmed by the spike).

**Files:**
- Create: `scripts/install_wsl.sh`
- Create: `tests/test_scripts/test_install_wsl.py`

**Approach:**
- Bash, `#!/usr/bin/env bash`, `set -euo pipefail`, `IFS=$'\n\t'`.
- Top-level structure as numbered echo'd sections: (1) preflight, (2) apt deps, (3) venv, (4) pip install, (5) torch override, (6) smoke test.
- Preflight: assert Ubuntu 24.04 (read `/etc/os-release`); refuse other distros with a clear message. Assert Python 3.12 available; install via `apt` if not.
- Apt deps: single `apt-get install -y` call with the list confirmed by the spike. Each line in the list carries a `# prevents <failure mode>` comment.
- Venv: create at `.venv/` in the repo root if missing; otherwise reuse.
- pip install: `pip install --upgrade pip setuptools wheel` then `pip install -e ".[dev]"`.
- Torch override: if `nvidia-smi` exits 0 inside WSL, run `pip install --no-cache-dir --force-reinstall torch torchvision --index-url <CUDA URL>`; otherwise `pip install --no-cache-dir --force-reinstall torch --index-url https://download.pytorch.org/whl/cpu`. The chosen branch is echo'd to stdout.
- Smoke test: `python -c "from percell4.app import main; print('install_wsl: ok')"`. Non-zero exits the script with a clear failure message.
- Flags: `--help` (print usage and exit 0), `--dry-run` (print planned steps and exit 0 without executing — used by the pytest smoke test).
- Idempotency: re-running on the same Ubuntu instance succeeds without prompting. apt is `-y`; venv reuses; pip install upgrades in place; torch override re-runs harmlessly.

**Execution note:** Use `--dry-run` from the start so the test scaffold can land alongside the script.

**Patterns to follow:**
- Exit code convention from `scripts/diagnose_bin_orientation.py`: 0 success, 1 runtime failure, 2 usage error.
- Stdout-as-progress style from `scripts/diagnose_bin_orientation.py:198-211` (plain `print(...)` with section headers).
- Test idiom from `tests/test_scripts/test_check_learnings_retrieval.py:13-50` (subprocess.run, captured stdout/stderr, assert on returncode + substrings).

**Test scenarios:**
- Happy path. `bash scripts/install_wsl.sh --dry-run` exits 0 and stdout contains each section header ("Preflight", "Apt dependencies", "Virtual environment", "Pip install", "Torch (CPU|GPU)", "Smoke test"). Covers AE2 indirectly (re-runnability is verified by the dry-run completing cleanly twice in a row).
- Edge case. `bash scripts/install_wsl.sh --help` exits 0 and stdout includes the script's name and the `--dry-run` and `--help` flags.
- Error path. Running the script with an unknown flag (e.g. `--bogus`) exits 2 and stderr names the unknown flag.
- Error path. Running the script on a non-Ubuntu-24.04 system (simulated by setting an env var the script consults for its `/etc/os-release` check, e.g. `PERCELL4_OS_RELEASE_PATH=/tmp/fake-os-release`) exits 1 with a message naming Ubuntu 24.04 as the supported version. (Only if the preflight is implemented test-friendly enough to support this; otherwise drop and rely on the spike-machine validation.)
- Integration. **Covers AE1.** Bash syntax check: `bash -n scripts/install_wsl.sh` succeeds (zero exit, no output). Verifies the script parses; catches obvious typos without executing.
- Integration. **Covers AE2.** Two consecutive `bash scripts/install_wsl.sh --dry-run` invocations both exit 0 and produce identical stdout. Idempotency at the dry-run level (full idempotency is verified on the spike machine in U1 follow-up).

**Verification:**
- Script exists at `scripts/install_wsl.sh` with executable bit set (`chmod +x` documented in U2's diff if needed).
- `bash -n` passes; `--help` and `--dry-run` both exit 0.
- pytest at `tests/test_scripts/test_install_wsl.py` passes against the dry-run modes.
- On the spike machine (re-verifying U1's manual recipe), running the script on a fresh Ubuntu 24.04 produces an importable PerCell4 install in under 15 minutes.

---

- U3. **README rewrite — replace native Windows install with WSL section**

**Goal:** Replace the native-Windows install path with a single concise WSL section, replace the Windows troubleshooting section with WSL-specific gotchas, update the Optional extras table's `gpu` row, and state explicitly that PerCell4 is unsupported on Windows without WSL.

**Requirements:** R3, R4, R5, R6 (origin R8–R11).

**Dependencies:** U1 (the troubleshooting section names the apt packages and WSLg quirks the spike surfaced).

**Files:**
- Modify: `README.md`

**Approach:**
- Replace lines 37–108 (current `### Windows` + subshells + "Windows: PyTorch / Cellpose") with one `## Windows (via WSL)` section that contains: prerequisites (Win 11 or Win 10 22H2+, virtualization in BIOS, ≥30 GB free, NVIDIA driver ≥ R<N>+ on host for GPU), three-line install (`wsl --install -d Ubuntu-24.04`, `git clone`, `./scripts/install_wsl.sh`), launch instruction (`source .venv/bin/activate && python main.py`), and a one-line "PerCell4 is unsupported on Windows without WSL — see [reasoning]" pointer with a link to this plan.
- Replace lines 171–179 (`## Troubleshooting (Windows)`) with `## Troubleshooting (Windows via WSL)` covering at minimum: WSLg blank window, missing Qt apt libs (linked to the script that installs them), `/mnt/d` perf and the "copy active `.h5` to `~/percell4-scratch`" workaround, NVIDIA driver too old on the Windows host, and "what to do when `wsl --install` itself fails."
- Update the `gpu` row in the Optional extras table (`:142–149`): drop the Windows-host caveat about "unsupported on Windows lab machines without a GPU"; replace with NVIDIA-host-driver-floor guidance.
- The current line 178's link to the c10.dll plan is consumed by the wholesale section rewrite above. The new `## Troubleshooting (Windows via WSL)` does not need that link at all — the c10.dll triage is no longer the canonical Windows-troubleshooting target.
- Confirm `### macOS` section at `:21-35` is left untouched.

**Test scenarios:**
- Test expectation: none (pure documentation change). Manual review on a markdown renderer confirms link integrity and section coherence.

**Verification:**
- `grep -E 'PowerShell|cmd\.exe|Git Bash|MSVC|vc_redist|c10\.dll|WinError|KMP_DUPLICATE_LIB_OK' README.md` returns nothing — all native-Windows-specific guidance is gone.
- Reading the new section top-to-bottom, a fresh user knows: what Windows version is required, what to type, what to do if it fails.
- Internal links to `docs/archive/...` resolve.

---

- U4. **Archive the two superseded native-Windows plans + update inbound references**

**Goal:** Move `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` and `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md` into `docs/archive/` with SUPERSEDED banners, and update every inbound reference in the codebase in the same commit.

**Requirements:** R7 (origin R12).

**Dependencies:** U3 (so README's link can be updated to point at the new archive paths).

**Files:**
- Move: `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` → `docs/archive/2026-03-27-feat-windows-compat-and-installer-plan.md`
- Move: `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md` → `docs/archive/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md`
- Modify: `src/percell4/workflows/diagnostics.py` (line 10 docstring reference)
- Modify: `src/percell4/gui/torch_error.py` (lines 9 and 32 docstring references)

**Approach:**
- `git mv` both files into `docs/archive/`.
- Insert at the top of each archived file (immediately after the `---` frontmatter closer): a blockquote banner matching `docs/archive/2026-03-28-refactor-atomic-state-signals-original.md:1-6`. Banner text: `> **SUPERSEDED — 2026-05-14.** PerCell4 deprecated native Windows install in favor of WSL2 + Ubuntu 24.04 + WSLg. See \`docs/plans/2026-05-14-001-feat-windows-via-wsl-install-path-plan.md\` and \`docs/brainstorms/2026-05-14-windows-via-wsl-install-path-requirements.md\`.`
- Update the three remaining inbound references (README's c10.dll link is consumed by U3's wholesale section rewrite — no separate update needed in U4):
  - `src/percell4/workflows/diagnostics.py:10` — replace path from `docs/plans/2026-04-17-...` to `docs/archive/2026-04-17-...`.
  - `src/percell4/gui/torch_error.py:9` and `:32` — same path update.

**Patterns to follow:**
- `docs/archive/README.md` rule: "Banner enforced; rename only if absolutely necessary."
- `docs/archive/2026-03-28-refactor-atomic-state-signals-original.md:1-6` for banner shape.

**Test scenarios:**
- Test expectation: none (file moves + textual updates). Verification is the grep below.

**Verification:**
- `grep -rln 'docs/plans/2026-03-27-feat-windows-compat\|docs/plans/2026-04-17-fix-windows-torch'` returns no results outside `docs/archive/` (the archived files may reference each other) and outside the requirements doc itself (which legitimately cites the originals as the *reason* for the pivot).
- `git log --diff-filter=R --name-status` for the commit shows both plan files as renames into `docs/archive/`, preserving git history.
- The two archived files render the SUPERSEDED banner above their original content.

---

- U5. **Update `docs/solutions/` and CLAUDE.md to remove contradictions with WSL-only stance**

**Goal:** Ensure no other doc surface recommends installing on native Windows or describes the deprecated path as canonical. Specifically: `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md` (the surviving learning whose `## Related Plans` link points at an archived plan), and the root `CLAUDE.md` (which currently says nothing about supported platforms).

**Requirements:** R8 (origin R13).

**Dependencies:** U4 (paths must already be archived so the references can point at the new locations).

**Files:**
- Modify: `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md`
- Modify: `CLAUDE.md` (root)

**Approach:**
- In `cross-platform-packaging-review-fixes.md`: update the `## Related Plans` link from `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md` to `docs/archive/2026-03-27-feat-windows-compat-and-installer-plan.md`, and add a one-line note: "Plan archived 2026-05-14; PerCell4's supported Windows path is now WSL2 — see `docs/plans/2026-05-14-001-feat-windows-via-wsl-install-path-plan.md`." The learning itself (the `os.replace` rule, the PyInstaller/upx/CLI-entry-point lessons) remains valid and stays as-is.
- In root `CLAUDE.md`: add a single line under "Tech Stack" or near the top, exact wording TBD during implementation but along the lines of "Supported platforms: macOS native, Linux native, Windows via WSL2 + Ubuntu 24.04 + WSLg." This is small enough to commit verbatim during implementation.
- Survey other per-module CLAUDE.md files (`src/percell4/CLAUDE.md`, `src/percell4/gui/CLAUDE.md`, etc.) for any platform-specific language. Research found zero such language — confirm with a fresh `grep -rli 'windows\|wsl' src/percell4/**/CLAUDE.md` and update only if surprises appear. No editing if grep is empty.

**Test scenarios:**
- Test expectation: none (documentation change).

**Verification:**
- `grep -rln '2026-03-27-feat-windows-compat\|2026-04-17-fix-windows-torch' docs/solutions/` returns paths only updated to point at `docs/archive/`.
- Root `CLAUDE.md` has a platform-support clause and reading top-to-bottom, no contradictions with the WSL stance remain.

---

- U6. **Soften `gui/torch_error.py` copy to point at the WSL install path**

**Goal:** Replace Windows-specific install advice in the torch-error dialog with a WSL-aware message. Keep the classifier and dataclass shape unchanged — only `_TITLES_AND_BODIES` strings change. The dialog now serves as a loud-failure guide for users who somehow ended up running PerCell4 on native Windows despite the docs saying not to.

**Requirements:** R9 (origin R14/R15).

**Dependencies:** U3 (so the README path referenced in the dialog text is the post-rewrite path).

**Files:**
- Modify: `src/percell4/gui/torch_error.py`
- Modify: `tests/` — check whether existing tests for `torch_error.py` need string updates. If tests assert on specific substrings of the dialog body, they update here.

**Approach:**
- Rewrite each entry in `_TITLES_AND_BODIES` so the body text does not name `vc_redist.x64.exe` or "Microsoft Visual C++ 2015-2022 x64 Redistributable" as the fix. Replace with: "PerCell4 is no longer supported on native Windows. Install via WSL2 — see the *Windows (via WSL)* section of `README.md`." Keep enough detail that the user knows which error they hit (i.e., name `c10.dll` in the title for `TORCH_DLL_INIT` so the dialog is still identifiable).
- The startup probe `show_msvc_redist_warning` is unreachable under WSL (it short-circuits on non-Windows in `check_msvc_redist_version`). Decide whether to retain it as dead defensive code or delete it. Recommendation: retain — it harms nothing and the Windows-native code path now exists only as the "you're on the deprecated platform" surface.
- The classifier (`classify` in `workflows/diagnostics.py`) and dataclass (`WorkerError`) are unchanged. Their docstrings (line 10 in `diagnostics.py`, line 9 in `torch_error.py`) update in U4 because they cite the archived plan path.

**Test scenarios:**
- Happy path. Given a `WorkerError(winerror=1114, message="...c10.dll...")`, `classify()` still returns `ErrorKind.TORCH_DLL_INIT` (no change). Existing test passes.
- Happy path. The `_TITLES_AND_BODIES[ErrorKind.TORCH_DLL_INIT]` body string contains the phrase "WSL" and the README pointer, and does NOT contain "vc_redist" or "Microsoft Visual C++". Add a thin unit test asserting these substrings.
- Edge case. `handle_worker_error` for a non-classified error (`ErrorKind.GENERIC`) returns `False` (caller falls back to status bar) — behavior unchanged.
- Integration. **Covers AE3 indirectly.** Loading `gui/torch_error.py` does not regress the WSL spike machine (no import-time errors from removed strings, no broken Qt imports). Manual verification on the spike machine.

**Verification:**
- `grep -E 'vc_redist|Microsoft Visual C\+\+|aka\.ms/vs/17/release' src/percell4/gui/torch_error.py` returns nothing.
- Existing pytest run for `torch_error.py` (or `diagnostics.py`) passes with updated string assertions.

---

## System-Wide Impact

- **Interaction graph:** The structured-worker-error pattern (`Worker.error → WorkerError → classify → handle_worker_error`) is unchanged in shape. Only string content shifts in U6. The four worker-error handler sites (`segmentation_panel.py:269`, `grouped_seg_panel.py:297, 372`, `workflows/single_cell/runner.py:473`) continue to call through the same helper.
- **Error propagation:** No behavioral change — torch import failures on the unsupported (native Windows) path still raise via the same OSError → WorkerError chain. The dialog now tells the user the path is unsupported instead of suggesting a fix.
- **State lifecycle risks:** None directly from this plan. WSL is a new runtime environment; file-system semantics on `/mnt/d/...` are still POSIX-shaped to Python.
- **API surface parity:** No public API change. `WorkerError` and `classify()` are internal contracts; no external consumers.
- **Integration coverage:** Cellpose, napari, pyqtgraph, h5py all need to keep working under WSLg. U1 verifies this end-to-end on a real machine; no unit-test surface change.
- **Unchanged invariants:** `os.replace`-based atomic writes (no platform branches anywhere); `Worker.error` signal contract; the lazy-import-of-cellpose pattern in `src/percell4/adapters/cellpose.py`. These all stay as they are — the plan deliberately does not refactor them.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Spike fails: WSLg cannot render napari acceptably on the target hardware | The plan stops at U1. Brainstorm reopens. No README rewrite, no archival — the existing native path stays intact. U1 is the hedge against committing to a doomed pivot. |
| Spike succeeds on one machine but fails on a second lab machine | U1's spike notes record machine spec + driver; if another machine fails the same recipe, treat as a Troubleshooting entry update, not a plan reopen. Variance in NVIDIA driver versions and Windows builds is expected. |
| Inbound references missed during archival; broken links land on `main` | U4's verification step is a literal grep that fails the unit if anything outside `docs/archive/` (and the requirements doc) still references the old paths. |
| `pyproject.toml` extras transitivity confuses script's CPU/GPU branch | The script does the torch override after `.[dev]`. Tested by U2's pytest dry-run output asserting both branches print their intended index URL. |
| A future agent re-introduces native-Windows guidance after merge | U5 adds the platform-support line to root `CLAUDE.md`, which gets loaded into every future agent's context. Prevents drift back. |
| Compound learnings get written *after* merge | Per `complete-branch-before-merge-2026-05-06.md`, the learning(s) — the WSL-bootstrap insights from the spike — are drafted in-branch as part of U1 or as a final commit before merge. |

---

## Phased Delivery

### Phase 1 — Feasibility validation
- U1 only. Spike on one machine, write `docs/install/wsl-spike-notes.md`, decide pass/fail.

### Phase 2 — Implementation (only if Phase 1 passes)
- U2 → U3 → U4 → U5 → U6, in that order. U2 has no doc dependency; U3 can begin in parallel after U1 if dispatched separately, but U3's troubleshooting section needs U1's findings to be specific. U4 needs U3 (README path lands cleanly). U5 needs U4 (archive paths are correct). U6 needs U3 (its dialog text points at the new README path).

---

## Documentation / Operational Notes

- `docs/install/` is a new directory (`wsl-spike-notes.md` is the first file). One-time create; no precedent to mirror — follow the `docs/solutions/` frontmatter shape so it can be cross-referenced cleanly.
- The compound learning from the spike (whatever "we learned WSL+napari does X on Y" turns into) should be written *in-branch* and committed before merge, per the institutional rule about complete-branch-before-merge. This is plan-output, not deferred work.
- Lab announcement is the user's call; the plan does not require one.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-14-windows-via-wsl-install-path-requirements.md](../brainstorms/2026-05-14-windows-via-wsl-install-path-requirements.md)
- Plans being superseded: `docs/plans/2026-03-27-feat-windows-compat-and-installer-plan.md`, `docs/plans/2026-04-17-fix-windows-torch-c10-dll-init-failure-plan.md`
- Direct precedent learning: `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md`
- Convention learnings (load-bearing for this plan): `docs/solutions/architecture-patterns/atomic-write-contract.md`, `docs/solutions/architecture-decisions/eliminating-shims-and-temp-fixes.md`, `docs/solutions/workflow-issues/premature-pattern-codification-2026-05-14.md`, `docs/solutions/workflow-issues/complete-branch-before-merge-2026-05-06.md`
- Style template for the script: `scripts/diagnose_bin_orientation.py`
- Test template for the smoke test: `tests/test_scripts/test_check_learnings_retrieval.py`
- Archive convention: `docs/archive/README.md`, `docs/archive/2026-03-28-refactor-atomic-state-signals-original.md`
- Code paths touched: `src/percell4/workflows/diagnostics.py`, `src/percell4/gui/workers.py`, `src/percell4/gui/torch_error.py`, `README.md`, `CLAUDE.md`
