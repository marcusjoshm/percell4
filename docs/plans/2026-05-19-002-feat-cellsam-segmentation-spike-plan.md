---
title: "feat: Add Cell-SAM segmentation alongside Cellpose (spike branch)"
type: feat
status: active
date: 2026-05-19
---

# feat: Add Cell-SAM segmentation alongside Cellpose (spike branch)

## Overview

Add the [vanvalenlab/cellSAM](https://github.com/vanvalenlab/cellsam) foundation model as a second segmentation backend in PerCell4, in parallel to the existing Cellpose pipeline. The work lands on a feature branch so the model can be evaluated against real datasets before deciding whether to keep, drop, or promote it to a first-class backend. cellSAM is a SAM-derived model (ViT-H encoder + AnchorDETR bbox prompter) covering brightfield, fluorescence, H&E, and multiplexed imaging — a complementary tool to Cellpose for samples where Cellpose underperforms.

Two non-obvious constraints discovered during research drive several decisions below:

1. **cellSAM is not a discoverable napari plugin.** Its `pyproject.toml` has no `napari.manifest` entry point and no `napari.yaml`. The "napari plugin" advertised in its README is a `magicgui` `Container` widget (`cellSAM.napari_plugin._widget.CellSAMWidget`) invoked via a `cellsam napari` CLI that spawns a standalone napari window. To use it inside PerCell4's embedded napari, we instantiate the widget class directly and dock it on the existing `ViewerWindow`.
2. **The `[napari]` extra installs PyQt6.** PerCell4 is PyQt5. We install cellSAM **without** that extra and rely on the embedded napari already provided by PerCell4 + the `qtpy` shim that cellSAM uses internally.

The primary integration surface is a new **Cell-SAM** group in `segmentation_panel.py` parallel to the existing Cellpose group — same store-write/auto-select machinery via `SegmentCells.finalize()`, just a different inference call. The magicgui dock-widget path is included as a small optional unit for users who prefer the bbox-prompt UX.

---

## Problem Frame

The lab wants to evaluate cellSAM on its single-cell microscopy data without committing to a permanent backend swap. Cellpose 4.x's `cpsam` model is in production; cellSAM is a different SAM-family model with a separate weight gate (Van Valen Lab's DeepCell access portal) and a non-commercial academic license. Evaluation needs to happen in PerCell4 itself so the cells flow through the same HDF5 store, segmentation naming, view-bin upsampling, and downstream measurement layers that already exist — otherwise the comparison is unfair.

The minimum viable evaluation surface: select a channel in PerCell4, click "Run Cell-SAM", get a `/labels/<name>` segmentation in the HDF5 store, see it overlaid in napari, and let the rest of the app (measurement, plotting, multi-select) work on it unchanged.

---

## Requirements Trace

- R1. Land the work on a dedicated feature branch so `main` keeps Cellpose-only and the spike can be abandoned cleanly.
- R2. cellSAM installs successfully into PerCell4's `.venv` without contaminating it with PyQt6 or breaking the existing Cellpose 3.x/4.x install.
- R3. A user can run cellSAM on the active channel of a loaded dataset from the existing Segmentation panel and have the result land at `/labels/<name>` exactly like a Cellpose run does — same naming prompt, same bin handling, same auto-select, same finalize post-processing (edge removal, min area, sequential relabel).
- R4. cellSAM-specific failure modes (missing `DEEPCELL_ACCESS_TOKEN`, weights not yet downloaded, image with no detected cells, MPS-requested-on-Mac) surface as readable status-bar messages rather than tracebacks.
- R5. The integration does not weaken any current Cellpose behavior. Cellpose remains the default. cellSAM is opt-in at install (`pip install -e ".[cellsam]"`) and opt-in at runtime (separate UI section).
- R6. A documented offline-install path exists for users who already have weights on disk but no token / no network — the lab has multiple workstations behind a firewall.

---

## Scope Boundaries

- Not migrating Cellpose to a unified "Segmenter chooser" UI. Two parallel sections in the panel is fine for the spike.
- Not abstracting the `Segmenter` Protocol in `src/percell4/ports/segmenter.py` to fit both Cellpose's `model_type` / `diameter` and cellSAM's `bbox_threshold` / `fast` / device kwargs. The current Protocol is Cellpose-shaped; broadening it is a separate refactor if cellSAM stays.
- Not adding Cell-SAM to the batch workflow runner (`src/percell4/gui/workflows/`) or to `grouped_seg_panel.py`. Single-image, ad-hoc runs only.
- Not training, fine-tuning, or shipping custom weights. We use `cellsam_general` and `cellsam_extra` as published.
- Not patching the upstream `device='mps'` `assert torch.cuda.is_available()` bug. On Apple Silicon we run on CPU; if that's too slow we revisit.
- Not building a generic "third-party segmentation napari plugin registry." cellSAM is the only target.

### Deferred to Follow-Up Work

- Promoting cellSAM to a first-class backend (broadening `Segmenter` Protocol, adding it to the workflow runner, surfacing in batch UI) — a separate plan once we know whether cellSAM produces results worth keeping.
- Patching cellSAM upstream (issue #98 None-check, MPS support) — only if we keep it; otherwise contribute fixes from a future branch.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/adapters/cellpose.py` — canonical "external segmenter as a pure function plus a Protocol-conforming class" pattern. The new `adapters/cellsam.py` mirrors this exactly: lazy import, pure function (`run_cellsam`), optional `model=` for batch reuse, error-tolerant wrapper.
- `src/percell4/ports/segmenter.py` — Segmenter Protocol. The spike does **not** conform to it (signature mismatch) — `CellSAMSegmenter` is a sibling class, not a Protocol implementation.
- `src/percell4/application/use_cases/segment_cells.py:73-154` — `SegmentCells.finalize()`. Takes raw masks, handles post-processing + view-bin upsample + HDF5 write + session refresh. Reused unchanged by the Cell-SAM path; cellSAM only differs in how raw masks are produced.
- `src/percell4/gui/segmentation_panel.py:106-142` (Cellpose section build) and `:368-481` (run + done handlers) — the template for the new Cell-SAM section. Note the captured `_cellpose_pending_name` / `_cellpose_pending_bin` pattern that locks the user's choices at queue time so a mid-flight session change doesn't corrupt the result. Replicate verbatim with `_cellsam_pending_*` names.
- `src/percell4/gui/_resource_name_prompt.py` (via `prompt_for_resource_name`) — name-collision-guarded prompt. Reused so cellSAM results are named consistently with Cellpose results (`cellsam` / `cellsam_2` / etc., with `_bin{N}` suffix when running at a view bin).
- `src/percell4/gui/workers.py` — `Worker(QThread)` wrapper used for Cellpose. Same pattern for Cell-SAM.
- `src/percell4/gui/torch_error.py` — `handle_worker_error` already catches torch import / device errors. Reusable for the cellSAM worker.

### Institutional Learnings

The repo's CLAUDE.md flags `compound-engineering:ce-learnings-researcher` as the audit-driven retrieval hook for `src/percell4/adapters/` edits (a T1 module). Per the canonical-sources matrix, the existing `cellpose.py` is the canonical reference for "external segmenter adapter shape" — re-implementing the adapter in a different shape would re-invent established conventions. The new file mirrors the structure.

### External References

- [cellSAM repo](https://github.com/vanvalenlab/cellSAM) — install instructions, README example (which is broken — see Key Decisions).
- [cellSAM documentation](https://vanvalenlab.github.io/cellSAM/) — programmatic API reference.
- [DeepCell access portal](https://users.deepcell.org/) — token registration. Manual approval, multi-day delay reported for non-`.edu`/`.org` email domains.
- [Issue #98 (prediction-None bug)](https://github.com/vanvalenlab/cellSAM/issues/98) — open as of late 2025. Triggers on images with zero detected cells. Wrap in adapter with empty-mask fallback.
- [Issue #118 (token gating issues)](https://github.com/vanvalenlab/cellSAM/issues/118) — recurring registration problems.

---

## Key Technical Decisions

- **Install via PEP 508 direct-URL VCS dependency**, declared in `pyproject.toml` under an optional-extras group `cellsam`: `cellSAM @ git+https://github.com/vanvalenlab/cellSAM.git`. **Why:** there is no PyPI release. Pinning to `master` is acceptable for a spike; if cellSAM is kept long-term, pin to a specific commit SHA in a follow-up.
- **Do not install the `[napari]` extra.** It pulls PyQt6, which conflicts with PerCell4's PyQt5. **Why:** PerCell4 already supplies napari at the right Qt binding. cellSAM's widget code uses `qtpy`, which adapts to whichever Qt is loaded first — PyQt5 in our process. The CellSAMWidget runs fine without PyQt6 once you instantiate it manually inside our process.
- **CellSAMSegmenter is a sibling class, not a Protocol conformant.** **Why:** the current Segmenter Protocol bakes in Cellpose's `model_type` and `diameter` kwargs. Broadening the Protocol for a spike that may be deleted creates churn. If cellSAM stays, R2 of the follow-up plan is "generalize the Segmenter port."
- **Bypass the README example, follow the documentation API.** The README shows `segment_cellular_image(img, device='cuda')` with no `model=` arg — that signature is broken in current code. Correct call: `model = get_model(model='cellsam_general', version='1.2'); segment_cellular_image(img, model=model, device=...)`.
- **Wrap `segment_cellular_image` in our adapter to catch the empty-cells crash (issue #98).** **Why:** on images with no detected cells, the upstream code returns a 4-tuple of `None`s and the subsequent `preds is None` check raises. Our adapter catches the resulting `TypeError`/`AttributeError`, logs a debug line, and returns an empty `int32` mask shaped like the input.
- **CPU on Apple Silicon, CUDA on Linux/Windows, no MPS.** **Why:** cellSAM has `assert torch.cuda.is_available()` if `device='cuda'`, no `mps` branch. UI exposes a CPU/CUDA radio; "CUDA" is greyed out if `torch.cuda.is_available()` is False. We do **not** patch cellSAM for MPS in this spike.
- **Weights live at `~/.deepcell/models/cellsam_v1.2/`.** First-call auto-download requires `DEEPCELL_ACCESS_TOKEN`. We surface a clear error if the token env var is unset, and document the manual-extract offline path in the README.
- **Two cellSAM models in the dropdown: `cellsam_general` and `cellsam_extra`.** Both supplied by the v1.2 weights archive. `cellsam_general` is the paper-faithful model; `cellsam_extra` covers more domains. Default to `cellsam_general` to match the paper.

---

## Open Questions

### Resolved During Planning

- *Does PerCell4's pyproject.toml accept a `git+https://` URL in an extras group?* Yes — PEP 508 direct-URL syntax (`name @ git+https://...`) is supported by setuptools and pip. Installing via `pip install -e ".[cellsam]"` works without `--use-pep517` flags.
- *Should we add cellSAM to `[project.optional-dependencies].all`?* No. The token gate makes it a poor default. Keep it isolated under `[cellsam]`. Mention it in `[all]` only after weights distribution is figured out.
- *Should cellSAM run on the same Worker class as Cellpose?* Yes. `Worker(QThread)` in `src/percell4/gui/workers.py` is generic over the inference function. Pass `run_cellsam` instead of `run_cellpose`.
- *Where does the new adapter live — `adapters/` or `segment/`?* `adapters/cellsam.py`. The repo's actual layout puts external-tool adapters in `adapters/` (despite the stale `segment/CLAUDE.md` mentioning `cellpose.py`). Mirror the actual code, not the stale doc.
- *Does the user actually want the cellSAM dock widget, or "cellSAM as a segmentation option"?* The request says "add the napari plugin" but cellSAM has no discoverable napari plugin. The Cell-SAM section in `SegmentationPanel` is the PerCell4-native answer; the optional U7 adds the dock widget for users who want the bbox-prompting UX.

### Deferred to Implementation

- *Exact torch version range cellSAM is happy with.* No pins in upstream `pyproject.toml`. Pip will resolve against whatever cellpose already requires; if there's a conflict at install time, surface the error and decide between downgrading cellpose or pinning torch.
- *Whether `cellsam_extra` belongs in the model dropdown for the spike or only after we've validated `cellsam_general`.* Keep both; document `cellsam_general` as default.
- *Whether the magicgui CellSAMWidget renders cleanly under PyQt5/qtpy when instantiated outside the `cellsam napari` CLI entry point.* Verify during U7. If it doesn't, U7 is dropped and the panel-section path (U4) is the only entry point.
- *Whether cellSAM weights cache directory should be configurable per-user.* The `~/.deepcell/models/` default is hardcoded upstream. Document it; revisit only if it's a blocker.

---

## Implementation Units

- U1. **Create the spike branch**

**Goal:** Land all subsequent work on a dedicated branch so `main` keeps Cellpose-only and the spike is cleanly revertable.

**Requirements:** R1

**Dependencies:** None

**Files:** none modified; new branch created

**Approach:**
- Branch off current `main` with name `feat/cellsam-spike`. (Convention check: recent feature work appears to merge directly to `main` without a stable naming scheme; `feat/<topic>-spike` makes the exploratory nature explicit.)
- The plan file itself stays on `main` as a planning artifact; the implementation commits land on the spike branch.

**Test scenarios:** Test expectation: none — branch creation is not feature-bearing.

**Verification:**
- `git branch --show-current` reports `feat/cellsam-spike`.
- `git log --oneline main..HEAD` is empty immediately after branch creation.

---

- U2. **Declare cellSAM as an optional dependency and install it**

**Goal:** Add a `cellsam` optional-extras group to `pyproject.toml`, install the package into `.venv`, and verify there's no PyQt6 contamination or torch version conflict with the existing Cellpose install.

**Requirements:** R2, R5, R6

**Dependencies:** U1

**Files:**
- Modify: `pyproject.toml`

**Approach:**
- Add a new optional-extras group:

  ```
  [project.optional-dependencies]
  cellsam = [
      "cellSAM @ git+https://github.com/vanvalenlab/cellSAM.git",
  ]
  ```

  Place it after `imagej` and before `all`. Do **not** include the `[napari]` cellSAM extra — pulls PyQt6.
- Do **not** add cellSAM to the `all` extras group for now. The token gate makes it inappropriate as a default.
- Install in dev mode: `pip install -e ".[cellsam]"` against the existing `.venv`.
- After install, verify:
  - `python -c "import cellSAM; print(cellSAM.__version__)"` prints `0.0.dev1` (the pre-1.0 string).
  - `pip list | grep -i pyqt6` returns nothing.
  - `python -c "from cellpose import models; print(models.__file__)"` still imports — Cellpose unaffected.
  - `python -c "import napari, qtpy; print(qtpy.API_NAME)"` still reports `pyqt5`.

**Patterns to follow:** None directly; this is package-level configuration.

**Test scenarios:**
- Happy path: `pip install -e ".[cellsam]"` succeeds. `import cellSAM` from inside `.venv` works without warnings.
- Negative: `pip list` after install does not include `PyQt6`.
- Compatibility: existing `pytest tests/ -m "not slow"` suite still passes (no test changes in this unit).

**Verification:**
- Editable reinstall completes without errors.
- All four import probes above succeed.
- Existing test suite is green.

---

- U3. **Add `adapters/cellsam.py` — pure-function inference wrapper**

**Goal:** Mirror `adapters/cellpose.py`'s shape with a cellSAM equivalent that takes a numpy image and returns an `int32` label array. Handle the upstream gotchas (`model=` required, issue #98 empty-cells crash, missing token).

**Requirements:** R3, R4

**Dependencies:** U2

**Files:**
- Create: `src/percell4/adapters/cellsam.py`
- Test: `tests/adapters/test_cellsam.py`

**Approach:**
- Lazy-import `cellSAM` at function-entry so the module loads cheaply when the extras are not installed (matches Cellpose adapter pattern).
- Public surface: a single function plus a class.
  - `build_cellsam_model(model_name: str = "cellsam_general", version: str = "1.2", device: str = "cpu") -> nn.Module` — wraps `cellSAM.get_model(...)`. Useful for batch reuse later.
  - `run_cellsam(image, model_name="cellsam_general", device="cpu", bbox_threshold=0.4, fast=False, normalize=True, postprocess=False, model=None) -> np.ndarray[int32]` — wraps `segment_cellular_image`.
  - `class CellSAMSegmenter` — exposes `.run(image, **kwargs)` for symmetry with `CellposeSegmenter`. Not declared as conforming to the `Segmenter` Protocol; signature differs.
- Error handling inside `run_cellsam`:
  - If `os.environ.get("DEEPCELL_ACCESS_TOKEN")` is unset **and** the weights file at `~/.deepcell/models/cellsam_v1.2/{model_name}.pt` is absent, raise `RuntimeError` with a message naming the env var and the expected weights path. Surface to the GUI status bar via the existing torch_error handler path; the message must read like documentation, not a stack frame.
  - If `device == "cuda"` and `torch.cuda.is_available()` is False, raise `RuntimeError("CUDA requested but not available; choose CPU.")`. Do not attempt MPS — the upstream code asserts on it.
  - Wrap the `segment_cellular_image` call in a try/except that catches `TypeError` and `AttributeError` (the two failure shapes seen in issue #98 when zero cells are predicted). On either, return `np.zeros(image.shape[:2], dtype=np.int32)`. Log a debug-level message naming the bbox_threshold so the user can lower it on retry.
- Return shape: `np.asarray(mask, dtype=np.int32)`. Always 2D `(H, W)`. cellSAM is 2D-only; no `z` handling needed (matches Cellpose adapter).

**Execution note:** Implement the adapter and its smoke test together — the issue-#98 empty-cells branch is easy to forget and easy to test with a `np.zeros` input.

**Patterns to follow:**
- `src/percell4/adapters/cellpose.py:53-123` — shape of `run_cellpose` (kwargs forwarding, lazy import, optional pre-built `model=`, `np.asarray(..., dtype=np.int32)` return).
- `src/percell4/adapters/cellpose.py:126-139` — `CellposeSegmenter` class wrapping the function.

**Test scenarios:**
- Happy path: importing the module without `cellSAM` installed does not raise (the import lazy-loads inside the function body, so the module-level import succeeds). Validate by skipping the test if cellSAM is not importable.
- Edge case: `run_cellsam(np.zeros((128, 128), dtype=np.float32), bbox_threshold=0.99)` returns a `(128, 128)` `int32` array of zeros without raising (covers issue #98 empty-cells path). Skip if no token / no weights.
- Error path: with `DEEPCELL_ACCESS_TOKEN` unset and no cached weights, `run_cellsam(...)` raises `RuntimeError` whose message names both the env var and the expected weights path. Use `monkeypatch.delenv("DEEPCELL_ACCESS_TOKEN", raising=False)` and a fake home directory.
- Error path: `run_cellsam(image, device="cuda")` on a machine where `torch.cuda.is_available()` is False raises `RuntimeError` with the "CUDA requested but not available" message. Use a `monkeypatch` of `torch.cuda.is_available`.
- Skip-marked happy-path full-inference test guarded by `@pytest.mark.slow` and a `@pytest.mark.skipif(not has_token_or_weights, ...)` — synthetic 256×256 image with one bright disc, expect `int32` mask with at least one nonzero region. Acceptable to leave this xfail-ish for the spike.

**Verification:**
- `python -c "from percell4.adapters.cellsam import run_cellsam, CellSAMSegmenter, build_cellsam_model; print('ok')"` prints `ok`.
- All adapter tests pass or are correctly skipped on machines without the token.

---

- U4. **Add a "Cell-SAM" group to `SegmentationPanel`**

**Goal:** Surface cellSAM in the existing Segmentation panel as a sibling section to Cellpose. Same name-prompt → Worker → `SegmentCells.finalize()` flow so the result lands at `/labels/<name>` indistinguishable from a Cellpose run.

**Requirements:** R3, R4, R5

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/gui/segmentation_panel.py`

**Approach:**
- Insert a `QGroupBox("Cell-SAM")` between the existing Cellpose `QGroupBox` and `Manual Editing` group, with these controls:
  - `Model:` `QComboBox` with items `cellsam_general` (default) and `cellsam_extra`.
  - `Bbox threshold:` `QDoubleSpinBox`, range `0.05`–`0.95`, step `0.05`, default `0.4`.
  - `Device:` `QComboBox` with `CPU` (default) and `CUDA`. Disable the `CUDA` option at panel-build time if `torch.cuda.is_available()` is False — call the check lazily inside the build so importing the panel does not import torch.
  - `Remove edge cells` `QCheckBox`, checked by default (parallel to Cellpose section).
  - `Fast mode` `QCheckBox`, unchecked by default — maps to `fast=True` in `run_cellsam`.
  - `Run Cell-SAM` `QPushButton`.
- Handler `_on_run_cellsam` mirrors `_on_run_cellpose` (lines 368–439): channel must be active, viewer must be open, image layer must match active channel, `prompt_for_resource_name` (default `"cellsam"`) collision-check against existing `/labels/`, capture `active_bin` at queue time, launch `Worker(run_cellsam, image, ...)`.
- Use **fresh attribute names** on `self`: `_cellsam_pending_name`, `_cellsam_pending_bin`, `_cellsam_worker`. Do **not** alias the Cellpose ones — concurrent runs of both segmenters must not collide on these stashes.
- Handler `_on_cellsam_done` mirrors `_on_cellpose_done` (lines 447–481): construct `SegmentCells(repo, session)` use case, call `.finalize(masks, remove_edge_cells=..., name=..., view_bin=...)`, add labels layer to viewer, status-bar the result. The use case is reused unchanged.
- Handler `_on_cellsam_error` mirrors `_on_cellpose_error` (lines 441–445): delegate to `handle_worker_error` from `torch_error.py` with `context="Cell-SAM"`.
- The `cleanup_status` / save-to-HDF5 / auto-save machinery already in this panel works on any Labels layer — cellSAM-produced layers benefit for free.

**Patterns to follow:**
- `src/percell4/gui/segmentation_panel.py:106-142` — Cellpose group construction. Replicate field-by-field.
- `src/percell4/gui/segmentation_panel.py:368-481` — Cellpose run / done / error handlers. Copy with `_cellsam_*` renames and the kwargs swap.

**Test scenarios:**
- Happy path (manual smoke test, GUI): load a dataset, pick a channel, click Run Cell-SAM, type a name, observe Worker progress in status bar, see a new Labels layer appear and `/labels/<name>` exist in the HDF5 store.
- Edge case (GUI): clicking Run Cell-SAM with no channel active surfaces "Select a channel in the Session window first" in the status bar (mirrors Cellpose path).
- Edge case (GUI): clicking Run Cell-SAM at `view_bin > 1` — captured bin travels with the worker; `finalize` upsamples to native shape; the stored seg name carries `_bin{N}` suffix.
- Error path (GUI): with `DEEPCELL_ACCESS_TOKEN` unset and no cached weights, the Worker's error path triggers a status-bar message naming the env var.
- Integration: a name collision (existing `/labels/cellsam`) triggers the same refuse-and-reprompt UX as Cellpose.
- Note: no automated pytest is added for this unit — `SegmentationPanel` has no existing test coverage and adding it is out of spike scope.

**Verification:**
- Cell-SAM group visible in the panel between Cellpose and Manual Editing.
- A successful run produces a Labels layer and a `/labels/<name>` group in the dataset HDF5.
- A Cellpose run still works unchanged after the panel changes.

---

- U5. **Document setup + offline weights path**

**Goal:** Give the user a single place to find the token instructions, install command, and offline-weights workflow.

**Requirements:** R4, R6

**Dependencies:** U2, U3, U4

**Files:**
- Create: `docs/setup/cellsam-spike.md`

**Approach:**
- Single short markdown file with these sections:
  - **Install** — `pip install -e ".[cellsam]"` from the project root inside `.venv`.
  - **Access token** — link to https://users.deepcell.org/, registration steps, the multi-day approval caveat. Show the `export DEEPCELL_ACCESS_TOKEN=…` line and where to put it (`~/.zshrc`).
  - **First run** — what to expect (weight download on first `get_model` call, ~1 GB+, cached at `~/.deepcell/models/cellsam_v1.2/`).
  - **Offline workflow** — how to copy `cellsam-models_v1.2.tar.gz` from a token-having machine, extract to the same path on the target machine.
  - **Apple Silicon** — CPU only; CUDA option in UI is auto-disabled; MPS is not supported by cellSAM upstream.
  - **Known issues** — link to upstream #98 (empty-cells crash, handled in our adapter) and #118 (token gating).
  - **Removing the spike** — pip uninstall + revert the `cellsam` extras line if dropping the integration.

**Patterns to follow:** Other lightweight setup docs under `docs/setup/` (if any exist). Otherwise keep it terse; no template needed.

**Test scenarios:** Test expectation: none — documentation file, no behavior to test.

**Verification:** File exists, renders cleanly when opened with `subl`, all linked GitHub URLs resolve.

---

- U6. **(Optional) Surface CellSAMWidget as a dock widget on the embedded ViewerWindow**

**Goal:** Give power users access to the upstream magicgui CellSAMWidget (with its bbox-prompt UI) inside PerCell4's napari viewer, without spawning a second napari instance.

**Requirements:** R3 (alternative entry point), R5 (must not interfere with Cellpose flow)

**Dependencies:** U2 (cellSAM installed)

**Files:**
- Modify: `src/percell4/gui/viewer.py` (add a `_add_cellsam_dock_widget` method and a single menu/action entry to open it)

**Approach:**
- On first invocation (lazy), `from cellSAM.napari_plugin._widget import CellSAMWidget; w = CellSAMWidget(self.viewer); self.viewer.window.add_dock_widget(w, name="Cell-SAM")`.
- Guard the import with try/except `ImportError`; if cellSAM is not installed, surface "Install cellSAM via `pip install -e \".[cellsam]\"`" in the status bar and bail.
- The widget mutates layers directly; its output Labels layer is named `"Segmentation Overlay"` upstream. PerCell4's auto-save infrastructure (`_subscribe_labels_layer`) will pick it up automatically and persist it to HDF5 once the user assigns a real name (Save Labels to HDF5 button), so no extra wiring is needed.
- Verify under PyQt5: `qtpy.API_NAME == "pyqt5"` is set before any cellSAM import. If the widget fails to render under PyQt5 (its imports may force PyQt6), drop this unit and document the failure in `docs/setup/cellsam-spike.md` — the U4 panel section is sufficient on its own.

**Patterns to follow:**
- `src/percell4/gui/viewer.py` already adds dock widgets for the file-tree and other panels; mirror that addition pattern.

**Test scenarios:**
- Happy path (manual smoke test, GUI): open viewer, trigger the dock-widget action, see the CellSAMWidget render in a side dock, draw a bbox on a Shapes layer, click Run, observe the `Segmentation Overlay` Labels layer fill in.
- Negative path (manual smoke test, GUI): with cellSAM uninstalled, triggering the action surfaces the install hint in the status bar without raising.
- Compatibility (manual smoke test): instantiating CellSAMWidget does not break the existing Cellpose run path (run Cellpose after instantiating the widget; verify it still works).

**Verification:**
- The CellSAMWidget renders inside the PerCell4 viewer without forcing PyQt6.
- The existing segmentation panel still works after U6 lands.
- If the widget cannot render under PyQt5, this unit is dropped and the omission is documented.

---

## System-Wide Impact

- **Interaction graph:** The Cell-SAM section uses the same `prompt_for_resource_name` → `Worker` → `SegmentCells.finalize` → `Session.set_active_segmentation` chain as Cellpose. No new signals or subscribers. The CellSAMWidget (U6) hooks into napari layer events that PerCell4 already subscribes to (`layers.events.inserted` for auto-save) — its output layer becomes auto-save eligible without code change.
- **Error propagation:** New error shapes — `DEEPCELL_ACCESS_TOKEN` missing, weights file missing, CUDA-not-available — flow through `Worker.error` → `handle_worker_error` → status bar. No new error-handling layer.
- **State lifecycle risks:** Concurrent Cellpose + Cell-SAM runs would collide on the panel's pending-name stash if shared. The plan uses separate `_cellsam_pending_*` attributes to avoid this. The button-disable / re-enable pattern is identical to Cellpose's.
- **API surface parity:** The Segmenter Protocol in `ports/segmenter.py` is **not** broadened in this spike. Consumers of `Segmenter` (only `SegmentCells.run_inference`) keep their Cellpose-shaped contract.
- **Integration coverage:** The view-bin lifecycle (capture-on-worker-construction → upsample at finalize) is reused unchanged — the Cell-SAM Worker writes its `_cellsam_pending_bin` at queue time, same pattern.
- **Unchanged invariants:** `SegmentCells.finalize()`, `Session.set_active_segmentation`, `Hdf5DatasetRepository.write_labels`, `Worker(QThread)`, `prompt_for_resource_name`. The spike adds calls to these; it does not modify them.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `DEEPCELL_ACCESS_TOKEN` registration takes days or is denied for the lab's email domain | Use a lab member's `.edu` address for registration. Offline-weights workflow in U5 lets one approved machine seed the others. |
| cellSAM's `master` branch breaks between install and use (no PyPI release means no version pinning yet) | Capture the installed commit SHA in `docs/setup/cellsam-spike.md`. If `master` breaks, replace `git+https://…@master` with `git+https://…@<sha>` in `pyproject.toml`. |
| CellSAMWidget fails to render under PyQt5 (U6) | U6 is optional. If it fails, drop it; U4's panel section is the primary path and unaffected. |
| Issue #98 manifests in shapes the adapter's try/except doesn't catch | Smoke test in U3 covers the empty-mask path; if a different exception class surfaces during real-data testing, broaden the except clause and add a regression test. |
| CPU inference on Apple Silicon is too slow to be useful for evaluation | Document the slowness in U5; the lab can run on a CUDA workstation for the benchmark and use the Mac for review only. Outside this plan's scope to fix. |
| The optional `[cellsam]` extras group pulls a torch version that conflicts with installed cellpose | Catch at install time (U2 verification step). If conflict, pin torch explicitly in `pyproject.toml` `[project.dependencies]` to a range both backends accept; flag for follow-up. |
| The non-commercial-use clause on cellSAM weights | Lab is academic, so usage is fine. Document the clause in U5 so anyone forking PerCell4 is aware. |

---

## Documentation / Operational Notes

- `docs/setup/cellsam-spike.md` (created in U5) is the single source of truth for installation, token, and offline workflow.
- `CLAUDE.md`, `src/percell4/segment/CLAUDE.md`, and `src/percell4/gui/CLAUDE.md` describe current state only — they should **not** be updated for the spike. They only get an update if the spike is promoted to a permanent backend, at which point a follow-up plan handles the docs migration.
- No rollout / monitoring concerns — local desktop app, no deployment.
- No GitHub issue or PR tracker integration noted in the repo; do not auto-create issues.

---

## Sources & References

- Origin: user request "I want to test a new cell segmentation model https://github.com/vanvalenlab/cellsam. Create a branch, install the dependency and add the napari plugin."
- External research summary (cellSAM install path, API, napari-plugin status, model weights gating, known issues #98 and #118, license) — conducted during planning, cited inline above.
- Related code: `src/percell4/adapters/cellpose.py`, `src/percell4/application/use_cases/segment_cells.py`, `src/percell4/gui/segmentation_panel.py`, `src/percell4/ports/segmenter.py`, `src/percell4/gui/workers.py`, `src/percell4/gui/torch_error.py`, `src/percell4/gui/_resource_name_prompt.py`, `src/percell4/gui/viewer.py`.
- Upstream repo: https://github.com/vanvalenlab/cellSAM
- Token portal: https://users.deepcell.org/
- Upstream issues: #98 (empty-cells crash), #118 (token gating).
