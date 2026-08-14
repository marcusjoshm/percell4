# README documentation audit

**Date:** 2026-08-13
**Audited artifact:** `README.md` (965 lines, ~66 KB) at `main` / `235d716`
**Method:** every checkable claim in `README.md` verified against source in `src/percell4/`, `pyproject.toml`, and `.github/workflows/ci.yml`; capability inventory built from source reading; gap analysis cross-referenced against `CHANGELOG.md`, `docs/plans/` (100 docs), and git history.
**Purpose:** ground the README overhaul planned in `docs/plans/2026-08-13-001-refactor-readme-showcase-overhaul-plan.md`.

Findings are evidence-backed. Nothing here is inferred from the README itself — every correction cites a source path.

---

## Summary

| Class | Count | Meaning |
|---|---:|---|
| **BROKEN** | 5 | Factually wrong; a user following it fails |
| **STALE** | 7 | Was true, drifted from current code |
| **MISSING (flags/commands)** | 6 | Real CLI surface the README never mentions |
| **OVERSTATED** | 3 | Claim the code does not support |
| **MISSING (capability)** | 11 | Shipped subsystems with zero README presence |
| **STRUCTURAL** | 5 | Not wrong, but defeats the README's purpose |

The README is not neglected — it is *misproportioned*. It documents the CLI in exhaustive, largely accurate detail while the project's actual identity, architecture, and scientific result are absent.

### Proportion problem

| Section | Lines | Share |
|---|---:|---:|
| Command-line Tools | 479 | 50% |
| Installation + Updating + wheel + extras + bundle + Troubleshooting | 281 | 29% |
| Workflow Protocol | 110 | 11% |
| Table of Contents | 35 | 4% |
| **Features + Tech Stack** | **32** | **3%** |
| License + Citing + Changelog | 14 | 1% |

79% of the document is reference and install material. The value proposition gets 32 lines, below 600 lines of man pages.

---

## BROKEN — factually wrong

### B1. `run_state.json` does not exist
- **Where:** `README.md:666` — *"Dataset lifecycle. Import, append, resume, close — with `run_state.json` for crash- and pause-tolerant workflows."*
- **Evidence:** zero occurrences of `run_state` anywhere under `src/`. The run-folder state file is `run_config.json` (`src/percell4/workflows/artifacts.py:556`, `_RUN_CONFIG_NAME`). `docs/plans/2026-05-20-001-feat-end-to-end-single-cell-workflow-plan.md:560`: *"`run_state.json` is not introduced (the v1 implementation merged it into `run_config.json`)."*
- **Fix:** name `run_config.json`, and drop the crash/pause-tolerance framing (see B2).

### B2. There is no pause, and no "Resume run…" control
- **Where:** `README.md:153` — a whole paragraph instructing the user to click **Resume run...** on the Workflows tab.
- **Evidence:** `src/percell4/interfaces/gui/main_window.py:378-442` builds the Workflows group with exactly five buttons: *Single-cell thresholding analysis workflow*, *Dilute phase mask generation*, *Dilute phase mask from mask*, *FLIM-FRET analysis*, *Automated phasor-masks workflow*. No Resume, no Pause. `docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md:641` records pause/resume as *"cut from v1 … V1 has Cancel only."* No resume code path exists in `src/`.
- **Fix:** delete the paragraph, or state that a run can be cancelled but not resumed.

### B3. Cellpose version pin is wrong, and the README contradicts itself
- **Where:** `README.md:643` — *"Cellpose (`>=3.0,<5.0`)"*.
- **Evidence:** `pyproject.toml:47` → `"cellpose>=4.2,<5.0"`, commented *"the 3.x Cellpose class is gone, so the wrapper is 4.x-only."* `README.md:842-845` itself says *"PerCell4 requires Cellpose 4.2 or newer."*
- **Fix:** `Cellpose (>=4.2,<5.0)`.

### B4. The dilute-phase step names buttons that do not exist
- **Where:** `README.md:135-138` — *"Click **Compute** … **Another round** … or **Done**."*
- **Evidence:** `src/percell4/gui/workflows/single_cell/dilute_queue.py:331,339,347` — the only buttons are `"Run another round"`, `"Done — save and continue"`, `"Cancel run"`. There is no **Compute** button; *Run another round* is what computes the round.
- **Fix:** use the real labels.

### B5. "The Cellpose defaults match the GUI Segment tab" is false on two settings
- **Where:** `README.md:202`.
- **Evidence:** CLI `--cellpose-diameter` defaults to `CellposeSettings.diameter = 30.0` (`src/percell4/workflows/models.py:103`); the GUI Segment tab seeds `CellposeSettings(diameter=300.0)` (`src/percell4/gui/segmentation_panel.py:176`) — a 10× difference. CLI `--gpu` is `store_true`, default off (`src/percell4/interfaces/cli/batch_process.py:181`); the GUI checkbox is seeded from `CellposeSettings.gpu = True` (`src/percell4/gui/_cellpose_settings_form.py:88`).
- **Fix:** state the two exceptions explicitly.

---

## STALE — drifted from current code

### S1. click and rich are advertised as the CLI stack; neither is used
- **Where:** `README.md:644` — *"**CLI:** click (`>=8.1`), rich (`>=13.0`)"*.
- **Evidence:** the pins match `pyproject.toml:60-61`, but **no module under `src/percell4/` imports `click` or `rich`**. All 14 console scripts use `argparse` (14/14 contain `ArgumentParser`). The only `click` matches in the tree are the word "click" in mouse-handling comments.
- **Fix:** drop the line from Tech Stack. Consider removing both from `pyproject.toml` — they are dead runtime dependencies.

### S2. `--cnr-threshold` is not unconditionally required
- **Where:** `README.md:517` — *"(**Required** with `--cnr-classify`)"*.
- **Evidence:** `src/percell4/interfaces/cli/batch_threshold.py:195-197` — *"REQUIRED with --cnr-classify unless --cnr-forced."* Confirmed by `CnrClassifySettings.__post_init__` (`src/percell4/workflows/models.py:416-420`), which validates `threshold > 0` only when `forced=False`.

### S3. Extras pointer omits `ocr`
- **Where:** `README.md:647` lists `gpu`, `flim`, `imagej`, `all`.
- **Evidence:** `pyproject.toml:76-82` defines `ocr`, and `all = ["percell4[gpu,flim,imagej,ocr]"]`. The extras table at `README.md:892+` does list `ocr` — only the pointer is stale.

### S4-S6. GUI label drift in the Workflow Protocol
| README | Claim | Actual label | Source |
|---|---|---|---|
| L124 | "Click **Accept**" | `Accept && Next →` | `gui/workflows/single_cell/seg_qc.py:1074` |
| L93 | "Click **Add round**" | `Add Round` | `config_dialog.py:1308` |
| L110 | "**Dilute mask name**" | `Mask name:` | `config_dialog.py:1451` |

### S7. `tools/png_to_csv/` does not produce a CSV
- **Where:** `README.md:659` — *"builds the calibration CSV by OCRing screenshots."*
- **Evidence:** the script is `tools/png_to_csv/phasor_ocr_to_xlsx.py` and writes `phasor_calibration.xlsx`. The CSV in that directory is an empty hand-fill template (`batch_tcspc_calibration_template.csv`).

---

## MISSING — CLI surface the README never mentions

### M1. `--device` on `percell4-batch-cellpose-laptrack`
`src/percell4/interfaces/cli/batch_process.py:182-188` — explicit torch device (`xpu`, `cuda:1`), overrides the launcher's Advanced panel, applies with `--gpu`. Absent from the option table at `README.md:180-200`, which weakens that section's claim to expose "the full GUI Segment-tab Cellpose controls."

### M2. `--cnr-forced` on `percell4-batch-threshold`
`src/percell4/interfaces/cli/batch_threshold.py:198-205` — forced always-2 GMM subpopulation split. This is the only path to the forced mode the Features section advertises at `README.md:658`, and it is documented nowhere.

> A programmatic diff of every `--flag` in every CLI module against its README section shows **no other gaps in either direction**. The CLI reference is otherwise accurate — which is why it is worth preserving rather than rewriting.

### M3-M4. Two console scripts have no README section at all
| Command | Module | What it is |
|---|---|---|
| `percell4-batch-validate-puncta` | `interfaces/cli/batch_validate_puncta.py` (295 L) | Dev harness: races puncta detectors against ground-truth point CSVs, locks a winner to JSON. 19 flags. |
| `percell4-window-bakeoff` | `interfaces/cli/window_bakeoff.py` (179 L) | Dev harness: scores auto-window-size finders against an `/masks/SG_mask` IoU oracle. |

Both are in `[project.scripts]`, so they install on every user's `PATH` undocumented. **Decision needed:** document them as development harnesses, or move them out of `[project.scripts]`.

### M5. Tech Stack omits five declared runtime dependencies
Not listed anywhere: `hdf5plugin>=4.0` (`pyproject.toml:34` — the Blosc filter every dataset is written with), `matplotlib>=3.8`, `diptest>=0.7,<0.12` (load-bearing for the CNR gap test), `laptrack>=0.16,<0.18` (named in Features but absent from Tech Stack), and the `PyQt5>=5.15` / `qtpy>=2.4` pins, which appear unpinned as "Qt (PyQt5 + qtpy)".

### M6. The Discovery combo has a third mode
`src/percell4/gui/compress_dialog.py:142-143` → `["Subdirectory", "Flat Directory", "Tokenless (by name)"]`. The protocol documents only the last two; **Subdirectory** is the first and default entry.

### M6b. The shared-conventions block overstates flag coverage
`README.md:165-166` presents `--quiet` as universal — `batch-threshold`, `batch-measure`, and `inspect` have none. `--verbose`/`-v` is presented as one flag — `batch_threshold.py:267` and `batch_measure.py:112` accept only `--verbose`; `percell4-inspect` has neither. The per-command tables are correct; only this summary is over-broad.

---

## OVERSTATED — claims the code does not support

### O1. Measurement staging is not in the `.h5`
`README.md:653` — *"One `.h5` … holds intensity channels, segmentation labels, masks, phasor maps, and measurement staging."* Staging is a run-folder directory: `src/percell4/workflows/artifacts.py:138` `(folder / "staging").mkdir()`. The README contradicts itself at L556: *"Measurements never go back into the `.h5`."*

### O2. XLSX export is not a PerCell4 feature
`README.md:664` — *"CSV/XLSX, parquet."* Zero `xlsx` / `to_excel` / `openpyxl` references in `src/percell4/`. The only XLSX writer is the standalone OCR helper, which produces a calibration sheet, not a measurement export.

### O3. The Sigma explanation inverts the relationship
`README.md:98` — *"Sigma sets a radius around each pixel in standard deviations (not pixels)."* `src/percell4/workflows/models.py` documents `blur_sigma` as the standard deviation of the kernel, and `batch_threshold.py:117-123` documents `--gaussian-sigma` in **px**. Sigma *is* measured in pixels.

---

## MISSING — shipped capability with zero README presence

Cross-referenced `CHANGELOG.md` + 100 plan docs + 432 `feat:` commits against the Features and Command-line Tools sections. Each row below has **zero** README hits.

| Capability | What it is |
|---|---|
| **Batch Tools Console** | In-app terminal in a dedicated window (ANSI + CR-aware view, command history, name completion, file navigator) that runs every `percell4-batch-*` command without leaving the GUI. Resolves commands to `python -m <module>` via `interfaces/cli/catalog.py` so it runs in the current venv independent of `PATH`. |
| **Registered-analysis framework** | The project's real extension mechanism. `@register_analysis` decorator, declarative input/preset/param schema, import-time schema validation, generic loader, auto-generated launcher buttons, batch + headless runners. Three shipped modules. Author guide at `docs/writing_an_analysis.md`. |
| **Advanced GPU/device configuration** | Advanced launcher tab + Qt-free JSON settings store pinning the Cellpose compute device, plus `--device` on the batch runner. Every interactive path reports the device it resolved. |
| **Segment-by-Metric + focus/sharpness metrics** | The CNR histogram segmenter generalized to any per-particle metric, plus Laplacian-variance and Tenengrad focus metrics so out-of-focus particles can be split off. |
| **Multi-timepoint TCSPC/FLIM** | 4-D `(T_acq,H,W,T_bins)` `/decay` schema, per-timepoint TCSPC append and phasor compute, phasor viewer follows the napari timepoint. The README's FLIM bullet is single-timepoint only. |
| **Per-harmonic phasor calibration** | Harmonic-aware compute with per-harmonic calibration keys (`flim_cal_phase_<ch>_h<n>`). |
| **Launcher IA overhaul** | Nine sidebar categories; Scripts + Workflows merged, I/O tab reduced to five controls, Manual Editing + Label Cleanup merged into Edit Labels. |
| **Cellpose diameter reference circle** | Magenta disc overlay sized to the Diameter field, so the value can be judged against real cells before running. |
| **Thresholding Rounds card editor** | The rounds editor as a per-round card list showing only the selected method's fields. |
| **Grouped existing-mask picker** | Group cells by which masks they already have; two-pane segmentation builder. |
| **Dilute phase mask *from mask*** | The batch dialog that grows an existing condensed mask and inverts within cell boundaries across N datasets. |

**Under-described** (present but thinner than what shipped): detection defaults changed (ALC no longer auto-estimates smallest particle; default smallest 2 px, default CNR mode threshold @ 8.0) with no note for anyone comparing runs; Iterative Otsu appears only inside a CLI flag table, with no Features bullet and no mention of the GUI Creator panel; `.lif` calibration is covered but the binding table / Auto-match / unbound-channel blocking is not.

---

## STRUCTURAL — not wrong, but defeats the README's purpose

### X1. The project's strongest credential is absent
`docs/reference/JCB_202311105.pdf` is the lab's *Journal of Cell Biology* **Tools** article with the repo owner as co-first author:

> Fahim, L.E.\*, **Marcus, J.M.**\*, Powell, N.D., Ralston, Z.A., Walgamotte, K., Perego, E., Vicidomini, G., Rossetta, A., & Lee, J.E. "Fluorescence lifetime sorting reveals tunable enzyme interactions within cytoplasmic condensates." *J. Cell Biol.* **224**(1): e202311105 (2025). doi:10.1083/jcb.202311105

The README mentions no publication, no lab, and no scientific context beyond one passing "P-bodies/stress granules" at L92.

### X2. Publication-grade framing exists in-repo and is unused
`docs/paper/adaptive-local-clipping-section.md` and `docs/methods/` contain the problem statement, the failure analysis of the incumbent method, and a quantified result:

> ALC was eye-validated across four datasets and two condensate types… In a whole-frame detector bake-off on an arsenite + nocodazole stress-granule field with ~4,664 hand-labeled foci, the adaptive detector recovered **~19% more true foci than a manual mask while maintaining zero dilute-phase pickup.**

and, tabulated for non-specialists (`docs/methods/how-puncta-detection-processes-the-image.md:194-207`): old hand-QC mask 3,570 granules *with* a manual QC step → new automated method 4,247 granules *with no manual step*, same typical size, no dilute phase.

### X3. There is no architecture section, despite a deliberate architecture
The codebase is ports-and-adapters by design, with machine-declared contracts (`pyproject.toml:153-207`, four `[tool.importlinter]` `forbidden` contracts), a null-adapter written explicitly as proof the seam works (`adapters/null_viewer.py`), and 22 architecture documents under `docs/solutions/`. The README says nothing about any of it.

### X4. The README links four files out of ~271
Linked: `CHANGELOG.md`, `CITATION.cff`, `LICENSE`, `tools/png_to_csv/README.md`. Unreachable from the README: 100 plan docs, 65 institutional learnings, 37 brainstorms, 9 audits, `docs/methods/` (2), `docs/paper/` (1), `docs/reference/` (9), `docs/writing_an_analysis.md`, `tests_gui/README.md`, and **`CONCEPTS.md`, which is not even mentioned**.

### X5. No visual of a desktop imaging application
The only image asset in the repo is `art/percell4_logo.png`. The 35-image `puncta_mask_gallery` — a real method-comparison figure set with a 207-line explanatory README — is buried in `docs/archive/`.

---

## Repository hygiene surfaced during the audit

These are outside the README but were found while verifying it, and two of them matter directly if the repository is going to be shared.

| Item | Finding | Recommendation |
|---|---|---|
| **`CITATION.cff`** | Ships with unfilled placeholders: `title: "TODO: exact title of the JCB tools paper"`, `authors: [{name: "TODO: paper author list, in order"}]`, author listed as `"Lee Lab"` with `Marcus, Joshua M.` commented out. The file's own header says to fill these before sharing. | **Fill before sharing.** This is the first file a careful reader opens. |
| **`install.sh`** (untracked) | Not a PerCell4 installer — it is the **Claude Code** installer (`DOWNLOAD_BASE_URL="https://downloads.claude.ai/claude-code-releases"`). A stray download. | Delete or gitignore. Do not document. |
| **`CHANGELOG.md:74`** | Claims *"The pre-cleanup interface is preserved on the `dev-features` branch."* No such branch exists locally or on `origin`. | Correct or remove the claim. |
| **Five empty packages** | `src/percell4/{flim,measure,segment,plugins,cli}/__init__.py` are all 0-byte with zero importers anywhere. The real code lives at `domain/flim/`, `domain/measure/`, `domain/segmentation/`, `interfaces/cli/`. `plugins/` is especially misleading — there is no plugin system there. | Delete, or document as reserved namespaces. Out of scope for the README work. |
| **import-linter contracts** | Four contracts declared in `pyproject.toml:153-207` but executed by nothing: not in CI, and `import-linter` is not in the `dev` extra. `docs/plans/2026-07-28-001-refactor-headless-test-suite-plan.md:86` calls this *"a real gap."* Static reading suggests the `application/` contract is currently violated by 9 `import h5py` sites, 3 of them module-level. | Do not advertise the contracts as enforced until they run in CI. |
| **Coverage** | `pytest-cov` is installed in the dev extra and both CI jobs, but there is no `[tool.coverage]` section and no `--cov` anywhere. No coverage is measured. | Do not put a coverage badge on the README. |
| **`gui-tests` merge-blocking status** | `tests_gui/README.md` says the job is merge-blocking; `.github/workflows/ci.yml` records that `main` has no branch protection, so failures surface as red checks only. | Reconcile; the CI comment is the more specific statement. |
| **Stub registry entries** | `atrous-wavelet` (detector) and `donut-surface` (background estimator) are registered names whose implementations are explicit stubs. | Do not count them in any "12 detectors / 8 estimators" claim without a footnote. |

---

## Verified accurate

Recorded so the rewrite does not "fix" things that are already correct.

All 24 table-of-contents anchors resolve. Every relative file link exists (`art/percell4_logo.png`, `tools/png_to_csv/README.md`, `CHANGELOG.md`, `CITATION.cff`, `LICENSE`, `percell4.spec`, `.github/workflows/ci.yml`, and the referenced Windows torch plan). The PyInstaller spec really does emit `PerCell4.app` / `dist/PerCell4`. Extras `gpu`/`flim`/`imagej`/`ocr`/`all`/`dev` all exist with the documented contents. Pins for napari, pyqtgraph, h5py, pandas, pyarrow, numpy, scikit-image, scipy and `requires-python >=3.12` are exact. The four Cellpose model names and the `cpsam_v2` default match `CELLPOSE_MODELS`. `CellDataModel` and its single `state_changed` signal are real. All three CNR modes exist. The `--view-bin` lens functions exist. Every output filename named in the CLI sections is real. Every stitching control named in protocol step 3 exists with those exact labels. All 14 `[project.scripts]` entry points resolve to a real `main()`.

---

## Capability inventory (reference for the rewrite)

Condensed from a full source read. Use these as the factual basis for new README content.

**Scale.** 254 Python modules / ~80,500 LOC in `src/`; 312 test files / ~87,300 LOC across `tests/` (296 files, 4,077 test functions) and `tests_gui/` (16 files, 98 test functions); 14 console entry points; 1,077 commits over 138 days by a single author; ~271 markdown documents under `docs/`.

**Architecture.** Ports-and-adapters. `ports/` holds only `typing.Protocol` definitions (`DatasetRepository` — a 30-method protocol, `Segmenter`, `Tracker`, `ViewerPort`); `adapters/` holds the concrete implementations (`Hdf5DatasetRepository`, `CellposeSegmenter`, `LaptrackTracker`, `NapariViewerAdapter`, `NullViewerAdapter`); `domain/` (66 modules) contains zero imports of Qt, napari, or h5py — empirically verified. Four import-linter contracts declare the boundaries (see hygiene note above re: enforcement). `NullViewerAdapter` exists as the stated proof the viewer seam works.

**State.** `application/session.py` is a Qt-free, napari-free state hub with a plain observer pattern over a 12-member `Event` enum. `model.py`'s `CellDataModel` is a Qt signal bridge over it, re-emitting one `state_changed` signal carrying an 11-field `StateChange` dataclass. `CellDataModel` is documented as transitional and slated for deletion.

**Storage.** One HDF5 file per dataset. `/intensity` (shape `(H,W)`, `(C,H,W)`, `(T,H,W)` or `(T,C,H,W)`, T-vs-C disambiguated by metadata not shape), `/decay/<ch>`, `/labels/<name>`, `/masks/<name>`, `/phasor/<ch>/{g,s,g_filtered,s_filtered,lifetime_filtered}`, `/tracks/<name>`, `/groups/<name>`, `/measurements`, `/metadata`. Blosc/zstd/bitshuffle for images and labels, lzf for TCSPC decay. Per-path view-bin rules (sum for intensity, mode for labels, majority vote for masks, mean for phasor). Six named consistency-guard exceptions.

**Detection.** 12 registered puncta detectors (one, `atrous-wavelet`, a stub), 8 background estimators (one, `donut-surface`, a stub), 7 iterative-Otsu stopping criteria across 3 scopes, 2 auto-window finders, 9 built-in per-cell metrics, Hartigan's dip test for CNR gap detection. Adaptive Local Clipping (794 L) is the flagship, with whole-frame, per-cell, by-particle-size and multiscale variants.

**FLIM.** Phasor compute (chunked straight from HDF5), per-channel and per-harmonic calibration, DTCWT wavelet denoising implementing Wang et al. 2021 (`domain/flim/wavelet_filter.py`), GMM cluster fitting with eigenstructure → ROI geometry, phasor-ROI → spatial mask, FLIM-FRET pairing. Input via Becker & Hickl `.sdt`, raw `.bin`, and Leica `.lif` calibration extraction.

**Engineering decisions worth surfacing.** Atomic writes with a stated contract in four places (tmp sibling → fsync → `os.replace` → fsync parent; never unlink first; Windows-aware `r+b`). Parallel HDF5 decode into shared memory — only frame indices cross the process boundary, never pixels; measured ~5.3× (60s → 11s at 10 workers). Compression chosen by measurement (Blosc matches gzip-4's ratio while decoding ~3× faster). Metadata-only inspection so multi-GB stacks inspect instantly. A generator-driven workflow state machine chosen over nested `QEventLoop.exec_()`, with the footgun named in the docstring. A monkeypatched `napari.Viewer.__init__` guard that dynamically enforces the headless test boundary because *"grep cannot police that boundary."* Fail-at-import schema validation for registered analyses. Single-source-of-truth name tuples with import-time drift assertions. An explicit domain exception hierarchy. A per-dataset failure taxonomy that records rather than aborts. macOS AppleDouble sidecar defence. `pa.unify_schemas` across staging fragments before dataset assembly.

> Performance figures (5.3×, ~3×, ~3.4×) are source-comment claims. No benchmark script or CI perf gate exists in the repo. Cite them as measured-during-development, or re-measure before publishing them.

---

## What this audit did not verify

- Runtime behavior of anything. All claims are from source reading; no code was executed beyond parsing.
- import-linter contract pass/fail — the tool is not installed, so `lint-imports` could not be run.
- Test pass rate and coverage — the suite was not run.
- PDF figures and layout in `docs/reference/` — text was extracted via the Spotlight importer; pages were not rendered.
- `percell4.spec` beyond its output paths.
