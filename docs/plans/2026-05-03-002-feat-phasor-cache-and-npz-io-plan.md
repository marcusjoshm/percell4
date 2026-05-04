---
title: "feat: Phasor Cache + .npz I/O"
type: feat
status: active
date: 2026-05-03
origin: docs/brainstorms/2026-05-03-phasor-cache-and-npz-io-requirements.md
---

# feat: Phasor Cache + .npz I/O

## Overview

Wire the existing `/phasor/<channel>/{g,s,g_filtered,s_filtered}` HDF5 cache into the `Compute Phasor` / `Apply Wavelet` button paths and into the Phasor window's open path so a previously computed dataset hydrates instantly. Add `.npz` export and import surfaces (Export button in IoPanel; new tab in AddLayerDialog) so the cache round-trips with external Python scripts. Hold-Shift-to-recompute is the escape hatch.

---

## Problem Frame

Cached phasor + wavelet results already persist to the `.h5` (verified: `compute_phasor.py:112-119`, `apply_wavelet.py:107-110`), but the FlimPanel button handlers always recompute from `/decay`, and the Phasor window never reads the cache on open. Re-opening a dataset forces minute-scale wavelet recomputes for no reason. Separately, the user's external scripts consume `.npz` phasor files but PerCell4 has no way to read or write that format. (See origin: `docs/brainstorms/2026-05-03-phasor-cache-and-npz-io-requirements.md`.)

---

## Requirements Trace

- R1. Opening a dataset whose phasor was previously computed → opening the Phasor window shows the histogram and wavelet result with no compute cost (under 500 ms perceived). *(origin success criterion 1)*
- R2. Round-trip: export a channel's phasor to `.npz`, delete `/phasor/<ch>` from the `.h5`, import the same `.npz` back → phasor window shows the same histogram before and after. *(origin success criterion 2)*
- R3. Shift-clicking `Compute Phasor` on a dataset with cached phasor recomputes from `/decay` and overwrites the cache; status bar reflects "Recomputed" not "Loaded cached". *(origin success criterion 3)*
- R4. Existing `test_phasor_*.py` tests still pass; new tests cover the cache-check early-out, Shift-bypass, and `.npz` round-trip. *(origin success criterion 4)*
- R5. Auto-load is **delayed** — happens on phasor-window open or active-channel switch, not on dataset-open. *(origin FR-1)*
- R6. Compute / Wavelet buttons act as "load if cached, else compute"; Shift bypasses cache. *(origin FR-2)*
- R7. Export writes one `.npz` per channel with the documented schema (`g`, `s`, `g_filtered`, `s_filtered`, `lifetime_filtered`, `metadata`); skips channels with no cached `g`/`s` silently. Intensity is intentionally not in the schema — it is reconstructed from `/decay/<ch>` on the importer side. *(origin FR-3, with intensity scope refined during planning review)*
- R8. Import is a new tab in AddLayerDialog with multi-file picker, per-row channel mapping, per-row conflict resolution (Skip default; Overwrite explicit). *(origin FR-4)*
- R9. Cache invalidation rules are unchanged; imported phasor inherits the same invalidation chain. *(origin FR-5)*

---

## Scope Boundaries

- No sidecar cache file; `/phasor/<ch>` in the `.h5` stays the canonical store.
- No parameter-keyed cache (no harmonic/calibration hash) — Shift-bypass is the only invalidation knob beyond the existing TCSPC-re-import chain.
- No multi-channel single-`.npz` bundles — one `.npz` = one channel.
- No bootstrap-from-`.npz` (import requires an active dataset).
- No auto-loading of every channel at dataset-open time — only the active channel, only when the phasor window is open.
- No new `DatasetRepository` port methods — existing `read_array` / `read_metadata` / `delete_path` cover the read path; existing `write_array` covers the write path.
- No changes to napari layer code (the recent layer-ownership fix is unaffected).

---

## Context & Research

### Relevant Code and Patterns

- **Use case authorship pattern** — `src/percell4/application/use_cases/compute_phasor.py` and `apply_wavelet.py`: constructor `__init__(self, repo: DatasetRepository, session: Session)`, `_read_fresh_metadata` helper for snapshot-staleness avoidance, `execute(...)` returns a frozen-ish dataclass result.
- **Existing repo port methods** — `src/percell4/ports/dataset_repository.py:77-101`: `write_array(handle, path, array, attrs=...)`, `read_array(handle, path)`, `read_metadata(handle)`, `delete_path(handle, path)`. All four are sufficient for this plan.
- **Conflict-signaling pattern** — `src/percell4/store.py:34-45` defines `LayerAlreadyExistsError` (alias `LayerAlreadyExists`); `append_decay_layers(force=False)` raises it. This is the documented overwrite-conflict UX shape — mirror it for `.npz` import.
- **AddLayerDialog tab construction** — `src/percell4/gui/add_layer_dialog.py:81-86` shows the `addTab(self._build_*_tab(), label)` pattern; per-tab content wrapping via `wrap_in_scroll` from `percell4.gui._dialog_utils` (mandatory — see learnings).
- **IoPanel callback injection** — `src/percell4/interfaces/gui/task_panels/io_panel.py:47-97`: constructor receives `on_export_csv` / `on_export_images` lambdas; launcher wires them in `main_window._create_io_panel`. New action follows the same shape.
- **PhasorPlotWindow showEvent** — `src/percell4/interfaces/gui/peer_views/phasor_plot.py` `showEvent` was added in commit `ac9a20e` (the recent layer-ownership fix). Natural hook for auto-load.
- **Active-channel signal** — `src/percell4/application/session.py` emits `Event.ACTIVE_CHANNEL_CHANGED` (verify name in implementation; `phasor_plot.py:307` already subscribes to `Event.DATASET_CHANGED` and shows the subscription pattern).
- **Set-phasor-data API** — `phasor_plot.py` `set_phasor_data(g_map, s_map, intensity=..., g_unfiltered=..., s_unfiltered=..., labels=...)` is the existing entry point; auto-load wiring calls it the same way `_on_apply_wavelet` does today.
- **Domain errors** — `src/percell4/domain/errors.py`: `PercellError`, `NoDatasetError`, `NoSegmentationError`, `NoMaskError`, `NoChannelError`. Add `NoCachedPhasorError` here following the convention.

### Institutional Learnings

- **`docs/solutions/architecture-patterns/atomic-write-contract.md`** — `.npz` export MUST use `tempfile.mkstemp + np.savez + os.replace`, not direct write. `export_images.py` is currently called out for violating this (`canonical-sources-matrix.yaml:428`); do not echo that mistake.
- **`docs/solutions/ui-bugs/dialog-scroll-when-tall.md`** — wrap the new Phasor tab content with `wrap_in_scroll(content)`. AST compliance test (`tests/test_gui/test_dialog_helper_compliance.py`) fails CI if skipped.
- **`docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`** — `Compute Phasor`, `Apply Wavelet`, `Export Phasor (.npz)...` all stay Actions. They read session, push data into the phasor window, never write `session.active_*` / `filter_ids` / `selection`.
- **`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`** (vectors 2 + 5) — use `read_metadata(handle)` not `handle.metadata` for fresh attrs; trust upstream invalidation (TCSPC re-import → `/phasor/<ch>` cleared) so cache reads need no re-validation against `/decay`.
- **`docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`** — exported `intensity` MUST come from `decay.sum(axis=-1)`, NOT from `/intensity[ch_idx]`. Storing a separately-sourced intensity reintroduces the silent-misalignment hazard.
- **`docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md`** — when iterating per-row in the import tab, do NOT re-scan a parent directory inside the loop; each row's resolved file path is the source of truth.
- **`docs/solutions/architecture-patterns/channel-deletion-permanence.md`** — `/phasor/<ch>/...` payloads obey "one payload type per HDF5 group" — no schema changes needed; reuse the existing keys.
- **`docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`** — IoPanel is Tier 1 (pure action callbacks). Add `on_export_phasor_npz: Callable[[], None]` constructor kwarg; do NOT reach into a launcher reference from inside IoPanel.
- **No prior precedent** for Shift-click force-recompute or `.npz` I/O in `docs/solutions/`. Capture both via `/ce-compound` after this lands.

### External References

External research skipped — local patterns dense and well-documented for every surface this plan touches.

---

## Key Technical Decisions

- **`LoadCachedPhasor` use case is the single read path.** Both the FlimPanel buttons (U2) and the Phasor window auto-load (U3) call this same use case. Eliminates duplicate read code; keeps cache semantics in one tested place.
- **Intensity is decay-derived AND not stored in the `.npz`.** `LoadCachedPhasor` reads `/decay/<ch>` and computes `decay.sum(axis=-1).astype(np.float32)`. Export does the same at write time. Per the cross-layer-alignment learning, never persist intensity separately in `/phasor/<ch>` — it belongs to `/decay/<ch>`. The `.npz` does NOT carry intensity (see schema below); on import, the target dataset's `/decay/<target>` is the canonical source.
- **Shift bypass uses `QApplication.keyboardModifiers() & Qt.ShiftModifier` at handler entry.** Single-line check before the cache-existence check. Tooltip on each button: `(Shift+click to force recompute)`.
- **`.npz` schema is fixed at v1.** Top-level keys: `g`, `s` (required, float32); `g_filtered`, `s_filtered`, `lifetime_filtered` (optional but always written together when wavelet has been applied); `metadata` (a 1-d uint8 array of UTF-8 JSON bytes containing a dict). `metadata["schema_version"] = 1`. Intensity is intentionally NOT in the schema (decay-derived; lives in `/decay/<ch>`). Round-trippable across PerCell4 instances when both source and target hold `/decay/<ch>` for the channel.
- **`metadata` as JSON bytestring (security)** — `np.savez(file_obj, ..., metadata=np.frombuffer(json.dumps(d).encode("utf-8"), dtype=np.uint8))`. Loaded with `np.load(path)` — **no `allow_pickle=True`**. This intentionally avoids np.savez's object-array path, which would require allow_pickle on load and would expose pickle-based RCE on collaborator-supplied files. Decoded via `json.loads(bytes(data["metadata"]).decode("utf-8"))`.
- **Atomic write uses an explicit file object, not a path argument.** `np.savez` auto-appends `.npz` to a path argument, which would silently produce `<tmp>.tmp.npz` instead of writing to `<tmp>.tmp` and break the `os.replace` step. Use `with open(tmp_path, 'wb') as f: np.savez(f, ...)` so savez writes exactly to the tmp path.
- **Import writes go through the existing `DatasetStore.write_array`** via the repo port — never open `h5py.File` directly inside the dialog (canonical-matrix line 569 already flags `_tcspc_write_bin_intensity_debug_layers` as a drift surface; do not add a second).
- **Conflict resolution mirrors decay-append.** `ImportPhasorNpz` use case raises `LayerAlreadyExistsError` when target `/phasor/<ch>/g` exists and `force=False`. Dialog catches it, sets per-row conflict chip, requires explicit `Overwrite` to proceed.
- **Channel-name inference order**: (1) `metadata["channel"]` from the npz, sanitized via a strict regex (see next point); (2) regex on filename `<stem>_<channel>_phasor.npz` capturing the second-to-last underscore-segment; (3) fall back to dataset's first channel + visible warning chip; user can always override the dropdown per row.
- **Channel-name sanitization is mandatory before HDF5 path construction.** `_validate_npz` rejects any `metadata["channel"]` that does not match `^[A-Za-z0-9_\-\.]{1,64}$`. Without this, a malicious `.npz` with `metadata["channel"] = "../segmentation/cellpose"` could overwrite arbitrary HDF5 groups via `delete_path` / `write_array`. The dialog's per-row Target channel cell is similarly bound to the regex.
- **Status-bar wording** — `Loaded cached phasor (channel: <name>)` for button cache hits, `Auto-loaded cached phasor (channel: <name>)` for Phasor-window auto-load (intentional differentiation from button click — auto-load is window-driven, not user-driven), `Recomputed phasor (Shift)` for force-recompute, `Computed phasor (channel: <name>)` for first-time compute. Same shape for wavelet (`Loaded cached wavelet ...`, `Auto-loaded cached wavelet ...`, `Recomputed wavelet (Shift)`).

---

## Open Questions

### Resolved During Planning

- **Channel-name inference rule**: three-tier fallback documented above (metadata → filename regex → first-channel + warning).
- **Should import write `/decay/<ch>` derivation hints?** No. Import is `/phasor/<ch>`-only; the `/decay/<ch>` group is owned by the TCSPC import path and adding cross-cutting writes here would violate the "one payload type per HDF5 group" rule.
- **Status-bar wording for Shift-bypass**: `Recomputed phasor (Shift)` (concise, attribution clear).
- **Cached indicator chip next to channel selector**: defer to a follow-up. Adds clutter to the FlimPanel; the auto-load behavior is the primary signal that cache is working.

### Deferred to Implementation

- Exact regex pattern for filename channel inference (depends on what unusual channel names look like in practice — `mTQ2`, `CA-SiR` already in test fixtures).
- Whether `np.load(allow_pickle=True)` raises a security warning in the user's numpy version (numpy ≥ 1.16.3 prints a deprecation note on unpickled object arrays in some configurations) — confirm at implementation time and decide whether to suppress or document.

### From 2026-05-03 review (judgment calls surfaced by ce-doc-review)

These are decisions the implementer must resolve as they reach each unit. Each was flagged by one or more review personas (coherence, design, feasibility, security, scope, adversarial). When making a call, document it as a comment near the change so the reasoning survives.

**Data-integrity / correctness**

- **Shape-mismatch import behavior.** When the imported `.npz` `(H, W)` differs from the active dataset's `/decay/<target>`, should `_validate_npz` hard-reject (one `read_array` + shape compare), or accept with a warning chip per current spec? Three reviewers (security, scope, adversarial) flag this as a silent-corruption path for the intensity-weighted histogram. Recommendation: hard-reject if `/decay/<target>` exists and shape mismatches.
- **Cache invariant under crash / multi-process.** Plan trusts upstream invalidation, but a crash mid-write of `compute_phasor`'s 3 sequential ops can leave asymmetric cache. Auto-load now exposes that. Decide: add asymmetric-cache detection in `LoadCachedPhasor` (if `g` present but `s` missing → treat as no cache and surface a status warning), or add a Scope Boundary "single-writer assumed". Adversarial reviewer.
- **Active-channel switch to uncached channel** while phasor window is open: clear the histogram or retain previous channel's display? Currently deferred to implementer in U3 test scenarios. Three reviewers flagged. Recommendation: clear (consistent with per-channel caching mental model).

**Cross-window communication**

- **`filter_level` push from phasor window → FlimPanel spinbox.** U3 says "via a thin signal (or read directly)" but no concrete path; direct widget access would violate the windows-don't-talk-to-each-other rule. Decide: new session field (`Session.flim_filter_level`), new Qt signal routed through launcher, or skip auto-syncing the spinbox (let user re-set if they care). Feasibility + scope reviewers.
- **Source of "active channel" in U3** — clarified in Approach to read `self._session.active_channel` (not event payload). If implementer chooses event payload, document why.

**Import tab UX (design-lens, all manual)**

- **Empty state of import tab** before any files are selected: placeholder text + disabled Import button, or empty table that's confusing? Decide and implement.
- **Per-row error chip text + recovery path.** Where does the validation error message surface (chip text, tooltip, separate column)? Can the user remove a bad row and re-add a fixed file? Spec the chip content and the row-removal affordance.
- **Multi-file re-add on second "Add files..." click:** additive (append + dedup by path) or destructive (replace all rows)? Affects multi-directory workflows. Recommendation: additive with dedup.
- **Import button enabled/disabled state** as a function of table contents: disable when table empty, disable when all rows are Skip, or always enabled with silent no-op? Recommendation: disable when empty AND when all-Skip.

**Robustness**

- **Channel-name regex ambiguity for underscored channel names** (e.g., `mTQ2_v2`): "second-to-last underscore segment" rule fails. Round-trip via `metadata["channel"]` works; user-edited filenames break silently. Decide: tighten the regex (greedy alternation), require metadata, or document the constraint in import help text.
- **Conflict probe-vs-write race.** Per-row probe sets Conflict chip at file-add time; row 1 importing into channel X then row 3 also targeting X with stale "no conflict" chip silently overwrites or skips. Decide: re-probe per row at Import time (recommended), or document the race as accepted.
- **Shift modifier global query race.** `QApplication.keyboardModifiers()` reads at slot-dispatch time (not click time); user can release Shift before handler fires. Pattern in `data_plot.py:37` uses `ev.modifiers()` from press event. Decide: subclass button to capture modifier at press, or accept the race.
- **`allow_pickle=True` threat model.** Two reviewers (security, adversarial) note the precedent argument is weaker than stated since the new surface extends trust boundary to external scripts. Decide: keep object-array dict (current spec), switch to JSON bytestring under a `metadata_json` key, or use individual top-level scalar arrays — the latter two eliminate `allow_pickle=True` from the phasor `.npz` path entirely.
- **`set_phasor_data` argument shape for raw-only cache.** Plan says "same shape `_on_apply_wavelet` uses today" but apply_wavelet always passes `g_unfiltered`/`s_unfiltered`; raw-only cache has no unfiltered counterpart. Decide: mirror compute_phasor call shape (omit unfiltered args) or pass None.

**Scope refinement**

- **`CachedPhasorResult` overspecified.** Includes `harmonic` and `flim_frequency_mhz` with no stated consumer (only `filter_level` is consumed). Decide: trim fields to actual consumers, or document anticipated future use.

---

## Implementation Units

- U1. **Add `NoCachedPhasorError` and `LoadCachedPhasor` use case**

**Goal:** Provide a single tested code path for reading `/phasor/<channel>/g`, `s`, `g_filtered`, `s_filtered` from the active dataset, plus the decay-derived intensity, plus relevant metadata. Two callers downstream (FlimPanel buttons U3, Phasor window auto-load U4) consume this use case.

**Requirements:** R5, R6, R9.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/domain/errors.py`
- Create: `src/percell4/application/use_cases/load_cached_phasor.py`
- Create: `tests/test_application/test_load_cached_phasor.py`

**Approach:**
- Add `class NoCachedPhasorError(PercellError)` to `domain/errors.py` following the existing `NoSegmentationError` shape.
- New use case `LoadCachedPhasor(repo: DatasetRepository, session: Session)` with method `execute(channel: str) -> CachedPhasorResult`.
- `CachedPhasorResult` dataclass fields: `g_map`, `s_map` (required, float32 (H,W)); `g_filtered`, `s_filtered` (Optional, both-or-neither); `intensity` (Optional float32 (H,W) — None when `/decay/<channel>` missing); `harmonic`, `filter_level`, `flim_frequency_mhz` (Optional, from attrs/metadata); `channel` (str).
- Read order: `read_array(handle, f"phasor/{channel}/g")` — KeyError → raise `NoCachedPhasorError(channel)`. Then `s` (paired with `g`). Then attempt `g_filtered` / `s_filtered` together (treat single-side missing as no-filtered case). Then `decay = read_array(handle, f"decay/{channel}")`, `intensity = decay.sum(axis=-1).astype(np.float32)` — KeyError → `intensity = None`. Then `_read_fresh_metadata(handle)` for `flim_frequency_mhz`; pull `harmonic` / `filter_level` from the array attrs the writers attached.
- Trust the upstream cache-invalidation chain — do NOT re-validate cached arrays against `/decay/<ch>` shape. Document this assumption in the docstring (referencing `flim-phasor-cross-layer-alignment-2026-04-29.md`).

**Patterns to follow:**
- `src/percell4/application/use_cases/apply_wavelet.py` — constructor signature, `_read_fresh_metadata` helper, error-raising convention.
- `src/percell4/application/use_cases/compute_phasor.py` — attrs writing convention (mirror what's read here).

**Test scenarios:**
- Happy path: full cache (g, s, g_filtered, s_filtered, intensity) → result has all fields populated; `intensity.shape == g_map.shape`; dtypes are float32.
- Raw-only cache (no g_filtered): result has `g_filtered is None and s_filtered is None`; `g_map`/`s_map` still populated.
- No cache: `read_array(.../g)` raises KeyError → use case raises `NoCachedPhasorError("<channel>")`.
- Edge case: `/decay/<channel>` missing → result has `intensity is None`; other fields still populated.
- Edge case: `g_filtered` present but `s_filtered` missing → treat as no-filtered (return both as None) and log a warning. (Asymmetric cache should not happen given current invalidation, but defend the use case.)
- Metadata: `flim_frequency_mhz` from `/metadata` is read fresh (not from `handle.metadata`); attrs `harmonic` and `filter_level` round-trip from the values written by `compute_phasor` / `apply_wavelet`.

**Verification:**
- `pytest tests/test_application/test_load_cached_phasor.py` passes.
- The use case has zero direct `h5py` imports — all I/O goes through the repo port.

---

- U2. **Wire `flim_panel.py` button handlers to load-from-cache; Shift bypass**

**Goal:** `Compute Phasor` and `Apply Wavelet` buttons load cached results when present; Shift-click forces a fresh recompute via the existing use cases.

**Requirements:** R1, R3, R6.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
- Test: `tests/test_gui_workflows/test_flim_panel_cache.py` (new)

**Approach:**
- In `_on_compute_phasor`: at handler entry, check `bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)`. If Shift NOT held, instantiate `LoadCachedPhasor`, try `execute(active_channel)`. On success, push to phasor window via `set_phasor_data`, set status `"Loaded cached phasor (channel: <name>)"`, return. On `NoCachedPhasorError`, fall through to existing compute path. On Shift held, skip the cache check, run existing compute path, set status `"Recomputed phasor (Shift)"`.
- Same shape for `_on_apply_wavelet`: cache check looks for `g_filtered` (use the same `LoadCachedPhasor` result — `result.g_filtered is not None`). On hit, call `set_phasor_data(result.g_filtered, result.s_filtered, intensity=result.intensity, g_unfiltered=result.g_map, s_unfiltered=result.s_map, labels=...)`. On miss, fall through. Shift bypasses.
- Update tooltips on both buttons (in `_build_ui` or wherever the buttons are constructed): append `\n(Shift+click to force recompute)`.
- Status messages: `"Loaded cached phasor (channel: <name>)"`, `"Loaded cached wavelet (channel: <name>)"`, `"Recomputed phasor (Shift)"`, `"Recomputed wavelet (Shift)"`. Existing post-compute messages stay.

**Patterns to follow:**
- Existing `_on_compute_phasor` and `_on_apply_wavelet` for status-bar wording shape and the `set_phasor_data` call pattern.
- `gui-action-contract-exhaustiveness.md` — both handlers stay Actions (no `session.set_active_*` writes).

**Test scenarios:**
- Happy path: dataset has cached `/phasor/<ch>/g,s` → click `Compute Phasor` (no modifier) → status shows `"Loaded cached phasor"`; phasor window receives `set_phasor_data` with cached arrays; the underlying compute use case is NOT invoked.
- Shift bypass: same dataset → Shift-click `Compute Phasor` → underlying `ComputePhasor.execute` IS invoked; status shows `"Recomputed phasor (Shift)"`.
- No cache: dataset has no `/phasor/<ch>` → click `Compute Phasor` → falls through to compute path; status reflects fresh compute.
- Wavelet happy path: cached `/phasor/<ch>/g_filtered` exists → click `Apply Wavelet` → status `"Loaded cached wavelet"`; phasor window receives both filtered (as `g_map`/`s_map`) and unfiltered (as `g_unfiltered`/`s_unfiltered`).
- Wavelet no-filtered: only raw cached → click `Apply Wavelet` → falls through to wavelet compute path.
- Tooltip assertion: both buttons' `toolTip()` contains the substring `"Shift+click"`.

**Verification:**
- New tests pass; existing `test_flim_panel*.py` (if any) still pass.
- Manual: open a dataset with cached phasor, click `Compute Phasor` — no perceptible delay, status reflects "Loaded cached".

---

- U3. **Phasor window auto-load on showEvent and active-channel switch**

**Goal:** When the Phasor window opens or the active channel changes (while open) and cached phasor exists for the active channel, auto-populate the window without requiring a button click. Defaults `Filtered` ON when wavelet cache exists.

**Requirements:** R1, R5.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_gui_workflows/test_phasor_window_auto_load.py` (new)

**Approach:**
- In `showEvent` (added in commit `ac9a20e` for the layer-ownership fix), add an auto-load step **after** the existing preview-restore call. Guard: only run when `self._g_map is None` (don't clobber an in-progress compute) and an active dataset + active channel exist.
- Subscribe to `Event.ACTIVE_CHANNEL_CHANGED` in `__init__` alongside the existing `DATASET_CHANGED` subscription. Handler: if window is visible and the new channel has cached phasor, swap displayed data via the same auto-load helper.
- New private method `_try_auto_load_cached() -> None`: read the active channel from `self._session.active_channel` (current session truth — matches the existing subscription pattern at `phasor_plot.py:307`, NOT the event payload). Instantiate `LoadCachedPhasor` via `self._get_repo`, try `execute(active_channel)`. On `NoCachedPhasorError`, do nothing (window stays empty — same UX as today). On success, call `self.set_phasor_data(...)` with the same argument shape `_on_apply_wavelet` uses today; set the `Filtered` checkbox checked when `result.g_filtered is not None`; push `result.filter_level` into the FlimPanel's filter-level spinbox via a thin signal (or read directly — see Open Questions).
- Status bar after auto-load: `"Auto-loaded cached phasor (channel: <name>)"`.

**Patterns to follow:**
- `phasor_plot.py:295-306` for the existing subscription pattern (`Event.DATASET_CHANGED`).
- `flim_panel.py._on_apply_wavelet` (lines around 367-372) for the `set_phasor_data` call shape.

**Test scenarios:**
- Open phasor window with cached `/phasor/<ch>/{g,s,g_filtered,s_filtered}` → window populates; `_g_map is not None`; `Filtered` checkbox is checked.
- Open phasor window with raw-only cache → window populates; `Filtered` checkbox unchecked AND disabled.
- Open phasor window with no cache → window stays empty (current behavior); no error.
- Active-channel switch while window open: switch from cached channel A to cached channel B → window re-hydrates with B's cache.
- Active-channel switch from cached channel A to uncached channel B → window goes empty (or stays on A — assert which; pick the less surprising behavior in implementation).
- Edge case: `_g_map` already set by an in-progress `_on_compute_phasor` → auto-load is a no-op (no clobber).
- Show / hide / show cycle: hide window (recent fix emits `preview_all_cleared`); reopen → auto-load fires again, preview layers come back via the existing preview-timer flow.

**Verification:**
- New tests pass.
- Manual: open a dataset with cached phasor, then open the phasor window — histogram appears within ~500 ms with no button clicks.

---

- U4. **Add `ExportPhasorNpz` use case**

**Goal:** Iterate the active dataset's channels, write one `.npz` per channel that has cached phasor. Atomic write per file.

**Requirements:** R2, R7.

**Dependencies:** None (parallelizable with U1–U3).

**Files:**
- Create: `src/percell4/application/use_cases/export_phasor_npz.py`
- Create: `tests/test_application/test_export_phasor_npz.py`

**Approach:**
- Constructor `ExportPhasorNpz(repo: DatasetRepository, session: Session)`.
- Method `execute(out_dir: Path) -> ExportPhasorResult` where the result has `exported: list[tuple[str, Path]]` (channel name + written file path) and `skipped: list[str]` (channels with no cache).
- Read fresh metadata once at the top of `execute`: `meta = self._read_fresh_metadata(handle)`. Iterate channels via `meta.get("channel_names", [])` — NOT `handle.metadata.get(...)`, which is the snapshot-staleness vector that the cited learning explicitly forbids.
- For each channel in `meta["channel_names"]`:
  - Try `repo.read_array(handle, f"phasor/{ch}/g")`. KeyError → append to `skipped`, continue.
  - Read `s` (paired). Read `g_filtered` / `s_filtered` / `lifetime_filtered` (optional, all-three-or-none — wavelet writes all three together; treat asymmetric presence as "no filtered cache" with a warning).
  - Build `metadata` dict: `{schema_version: 1, channel: ch, harmonic: ..., filter_level: ..., flim_frequency_mhz: ..., source_dataset_stem: handle.path.stem}` (read from `meta` and array attrs).
  - Filename: `f"{handle.path.stem}_{ch}_phasor.npz"` joined with `out_dir`.
  - Atomic write — explicit file object to defeat `np.savez`'s `.npz` suffix auto-append:
    - `fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=out_dir)`
    - `os.close(fd)` (we will reopen via `open` for binary write so np.savez writes exactly to `tmp_path`)
    - `with open(tmp_path, 'wb') as f: np.savez(f, g=..., s=..., g_filtered=..., s_filtered=..., lifetime_filtered=..., metadata=np.array(metadata_dict, dtype=object))`
    - `os.replace(tmp_path, final_path)`
    - Wrap the whole sequence in try/except; on any exception, attempt `os.unlink(tmp_path)` (suppress FileNotFoundError) and re-raise.
  - Append `(ch, final_path)` to `exported`.
- **Intentionally NOT included in the `.npz`:** `intensity`. Per cross-layer-alignment learning, intensity is decay-derived and the importer reconstructs it from its own `/decay/<ch>`. Storing intensity in the `.npz` would re-create the silent-misalignment hazard.

**Patterns to follow:**
- `src/percell4/store.py` `create_atomic` for the `tmp + os.replace` pattern (or copy the 5-step idiom inline if `percell4/io/atomic.py` doesn't exist yet).
- `apply_wavelet.py._read_fresh_metadata` for the metadata-staleness pattern.

**Execution note:** Test-first. The atomic-write path has subtle failure modes (orphan tmp file on crash, partial write on disk-full); failing tests for those scenarios should land before the implementation.

**Test scenarios:**
- Happy path: dataset with full cache for one channel → one file written; `np.load(file, allow_pickle=True)` round-trips all keys (`g`, `s`, `g_filtered`, `s_filtered`, `lifetime_filtered`, `metadata`); `metadata["schema_version"] == 1`; `intensity` key is NOT present.
- Multi-channel: dataset with two channels, one cached, one not → result has 1 exported, 1 skipped; only one file written.
- Raw-only export: cached `g`, `s` but no `g_filtered` → file written with `g`, `s`, `metadata` only; `g_filtered` / `s_filtered` / `lifetime_filtered` keys absent.
- Atomic write failure: monkeypatch `np.savez` to raise mid-write → no `.tmp` file remains in `out_dir` (cleanup) AND no destination file partially written.
- `np.savez` path-suffix verification: assert the final filename is exactly `<stem>_<ch>_phasor.npz`, NOT `<stem>_<ch>_phasor.npz.npz` (regression guard against the path-vs-file-object trap).
- Stale-metadata regression guard: monkeypatch `handle.metadata` to return a different `channel_names` list than the live `/metadata` group; assert the use case iterates the LIVE list, not the snapshot.
- Empty dataset (no cache anywhere) → result has 0 exported, len(skipped) == len(channel_names); no files written.
- Filename sanitation: channel name `CA-SiR` → file written as `<stem>_CA-SiR_phasor.npz` (hyphens preserved, no escape needed).

**Verification:**
- `pytest tests/test_application/test_export_phasor_npz.py` passes.
- No `tifffile.imwrite`-style direct write — all whole-file outputs go through the atomic-write idiom.

---

- U5. **Add `Export Phasor (.npz)...` button to IoPanel**

**Goal:** Surface the export use case in the IO panel's Export group via the established callback-injection pattern.

**Requirements:** R7.

**Dependencies:** U4.

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/io_panel.py`
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Test: `tests/test_gui_workflows/test_io_panel_export_phasor.py` (new)

**Approach:**
- IoPanel: add `on_export_phasor_npz: Callable[[], None]` to constructor signature; store on `self`; add a new `QPushButton("Export Phasor (.npz)...")` to the Export group (after `btn_export_images`); wire `clicked` to `lambda: self._on_export_phasor_npz()`.
- LauncherWindow (`main_window.py`): in `_create_io_panel` (or wherever IoPanel is instantiated), pass `on_export_phasor_npz=self._on_export_phasor_npz`. Add a new `_on_export_phasor_npz` handler that: opens `QFileDialog.getExistingDirectory(self, "Export Phasor .npz to...")`; on cancel, return; instantiate `ExportPhasorNpz` use case; call `execute(Path(out_dir))`; show status `"Exported phasor for N channel(s) to <path>"` (or `"No cached phasor to export"` when 0).

**Patterns to follow:**
- `io_panel.py:88-97` for the Export-group layout shape.
- `main_window._on_export_images` for the FileDialog → use case → status pattern.

**Test scenarios:**
- IoPanel: button is present in the Export group with the right label.
- IoPanel: clicking the button invokes the injected callback (mock the callback, assert called).
- Launcher handler: `_on_export_phasor_npz` calls `ExportPhasorNpz.execute` with the user-selected directory; status message reflects the export count.
- User cancels file dialog → use case NOT invoked; status unchanged.
- Use case raises `NoDatasetError` (no dataset loaded) → status shows `"No dataset loaded"`; no crash.

**Verification:**
- New test passes; existing IoPanel tests still pass.
- Manual: click `Export Phasor (.npz)...` → directory picker opens → choose dir → files appear; reload externally with `np.load(..., allow_pickle=True)`.

---

- U6. **Add `ImportPhasorNpz` use case**

**Goal:** Validate a single `.npz` payload and write its arrays to `/phasor/<target_channel>/{g,s,g_filtered,s_filtered,lifetime_filtered}` in the active dataset. Conflict signaling mirrors `LayerAlreadyExistsError`. Channel name is sanitized before any HDF5 path is constructed.

**Requirements:** R2, R8.

**Dependencies:** `delete_path`, `LayerAlreadyExistsError` from `store.py:34-45,392`. No new repo port methods. (Parallelizable with U1-U5.)

**Files:**
- Create: `src/percell4/application/use_cases/import_phasor_npz.py`
- Create: `tests/test_application/test_import_phasor_npz.py`

**Approach:**
- Constructor `ImportPhasorNpz(repo: DatasetRepository, session: Session)`.
- Method `execute(npz_path: Path, target_channel: str, force: bool = False) -> ImportPhasorResult` where result has `wrote_filtered: bool`, `target_channel: str`.
- **Channel-name sanitization (runs FIRST, before any HDF5 access):** `_sanitize_channel_name(target_channel)` validates against `^[A-Za-z0-9_\-\.]{1,64}$`. Rejects null bytes, path separators (`/`, `\`), parent traversal (`..`), and any other characters that would let a malicious `.npz` perform HDF5 path traversal via `delete_path`/`write_array`. On mismatch, raise `ValueError("Channel name '<name>' must match [A-Za-z0-9_-.]{1,64}")`.
- Steps: `data = np.load(npz_path, allow_pickle=True)`; validate (helpers below); if `force=False` and `repo.read_array(handle, f"phasor/{target_channel}/g")` succeeds, raise `LayerAlreadyExistsError(target_channel)`; otherwise `delete_path` the existing `/phasor/<target_channel>` (clean overwrite) then `write_array` for `g`, `s` (and `g_filtered`, `s_filtered`, `lifetime_filtered` when present — all-three-or-none), reusing the attrs schema that `compute_phasor` / `apply_wavelet` use.
- Validation helper `_validate_npz(data) -> None`:
  - `g` and `s` keys present, both float, both same shape `(H, W)`.
  - If `g_filtered` present: `s_filtered` and `lifetime_filtered` must ALL be present together; all three shapes must match `g.shape`. (Reflects how `apply_wavelet` writes them.)
  - If `metadata` present: must be a 0-d object array deserializable to a dict; if it carries `metadata["channel"]`, that value is sanitized via the same regex above.
  - **No extra top-level keys:** validate that `set(data.files)` is a subset of `{g, s, g_filtered, s_filtered, lifetime_filtered, metadata}`. Multi-channel single-`.npz` files (declared a non-goal) carrying keys like `g_ch0` would otherwise silently pass and import only the `g`/`s` slice.
- The `.npz` schema does NOT include `intensity` (per Key Technical Decisions). Validation does not look for or accept it; if a third-party file carries an `intensity` key, the no-extra-keys check above rejects it with a clear error.

**Patterns to follow:**
- `compute_phasor.py:112-119` and `apply_wavelet.py:106-110` for the `write_array` attrs schema.
- `store.py:392` for the `LayerAlreadyExistsError(name)` raise pattern.

**Execution note:** Test-first. Validation has many failure modes; tests should be written before the implementation.

**Test scenarios:**
- Happy path: valid full-bundle .npz → writes `/phasor/<target>/g,s,g_filtered,s_filtered,lifetime_filtered`; `wrote_filtered is True`; attrs include `channel`, `harmonic`, `filter_level`.
- Raw-only .npz: `g_filtered`/`s_filtered`/`lifetime_filtered` absent → writes only `g`, `s`; `wrote_filtered is False`.
- Conflict, force=False: target `/phasor/<ch>/g` already exists → raises `LayerAlreadyExistsError(target_channel)`; nothing written.
- Conflict, force=True: target exists → existing `/phasor/<target>` group cleared via `delete_path`; new arrays written.
- **Channel-name traversal attack:** `target_channel = "../segmentation/cellpose"` → raises `ValueError` BEFORE any `read_array` / `delete_path` / `write_array` is called; nothing in the dataset is touched. (Regression guard against HDF5 path-traversal exploit via untrusted metadata.)
- **Channel-name null byte:** `target_channel = "ch\x00name"` → raises `ValueError`.
- **Metadata-channel sanitization:** `.npz` with `metadata["channel"] = "../etc"` → if the dialog passes that as `target_channel`, the use case rejects.
- Validation: missing `g` → raises `ValueError("`.npz` missing required key 'g'")`.
- Validation: shape mismatch between `g` and `s` → raises `ValueError(...)`.
- Validation: `g_filtered` present but `s_filtered` or `lifetime_filtered` missing → raises `ValueError("g_filtered/s_filtered/lifetime_filtered must be present together")`.
- Validation: extra top-level key (e.g., `intensity`, `g_ch0`) → raises `ValueError("unexpected keys: {...}")`.
- `metadata` round-trip: source .npz's metadata dict's `harmonic` and `filter_level` are written to attrs on the imported arrays.

**Verification:**
- `pytest tests/test_application/test_import_phasor_npz.py` passes.
- Round-trip integration test: export a dataset's channel via U4, delete `/phasor/<ch>`, re-import via U6 → `LoadCachedPhasor` from U1 returns the original arrays.

---

- U7. **Add "Phasor (.npz)" tab to AddLayerDialog**

**Goal:** Surface the import use case as a new tab in the Add Layer dialog with multi-file support, per-row preview/channel mapping, and per-row conflict resolution.

**Requirements:** R2, R8, R9.

**Dependencies:** U6.

**Files:**
- Modify: `src/percell4/gui/add_layer_dialog.py`
- Test: `tests/test_gui/test_add_layer_phasor_npz_tab.py` (new)

**Approach:**
- New `_build_phasor_npz_tab(self) -> QWidget` method following the shape of `_build_roi_tab` / `_build_cellpose_tab`.
- Tab contents:
  - "Add files..." button → `QFileDialog.getOpenFileNames(self, "Select Phasor .npz files", filter="NumPy archives (*.npz)")`, multi-select.
  - `QTableWidget` with columns: `File`, `Detected channel`, `Shape`, `Filtered?`, `Target channel` (editable QLineEdit), `Conflict?`, `Action` (QComboBox: Skip / Overwrite — disabled unless conflict). Add a row per selected file.
  - Per-row probe on add: open the .npz with `np.load(allow_pickle=True)`, peek at `metadata["channel"]` if present, infer from filename otherwise, populate table cells. If validation fails, mark row with red error chip in `Detected channel` column and set `Action = Skip`. If `repo.read_array(handle, f"phasor/{target}/g")` succeeds, set `Conflict = Yes`, enable Action dropdown, default to Skip.
  - "Import" button at the bottom: for each row whose action is not Skip, call `ImportPhasorNpz.execute(file_path, target_channel, force=(action == "Overwrite"))`. Collect successes and failures into a status message: `"Imported phasor for N channel(s); M skipped; K errored"`.
  - Help text below the table: `"Note: .npz files must be from a trusted source — import uses np.load(allow_pickle=True). See exported files for the expected schema."`
- **Wrap the entire tab content with `wrap_in_scroll(content)` from `percell4.gui._dialog_utils`** before returning.
- Register tab: in `_build_ui` (around line 81-86), add `self._tabs.addTab(self._build_phasor_npz_tab(), "Phasor (.npz)")`.

**Patterns to follow:**
- `_build_cellpose_tab` and `_build_roi_tab` for the "pick files → table preview → action button" UX shape.
- `_build_batch_tiff_tab` and `_build_tcspc_tab` for `wrap_in_scroll(content)` use.
- `add-layer-flat-discovery-duplicate-import.md` learning — per-row file path is the source of truth; do NOT re-scan parent directories inside the import loop.

**Test scenarios:**
- Tab construction: tab is registered with label `"Phasor (.npz)"` at the expected position; root widget is wrapped in a `QScrollArea` (compliance test catches this if missing).
- Single-file happy path: pick one valid .npz → table shows one row with detected channel; click Import → use case invoked once with `force=False`; status shows `"Imported phasor for 1 channel(s)"`.
- Multi-file mix: pick three files (one valid, one validation-failing, one conflicting) → table shows three rows; Import processes the valid one, skips the failing one (with error chip in red), prompts the conflicting one (defaulting to Skip) → final status reflects 1 imported, 2 skipped.
- Conflict with Overwrite: row with `Conflict = Yes`, user changes Action to Overwrite → use case invoked with `force=True`; succeeds.
- Channel inference: filename `Dish_2_TAOK2_KO_As_60min_mTQ2_phasor.npz` with no metadata.channel → detected channel is `mTQ2` (regex on second-to-last underscore segment).
- Channel inference fallback: filename doesn't match the export pattern AND metadata.channel missing → detected channel is the dataset's first channel + warning chip; user can edit Target channel cell.
- Cancel file dialog → no rows added; tab remains usable.

**Verification:**
- New test passes; existing AddLayerDialog tests still pass; `tests/test_gui/test_dialog_helper_compliance.py` still passes (the AST check for `wrap_in_scroll`).
- Manual: open AddLayerDialog → switch to Phasor (.npz) tab → import a file from U4's export → phasor window shows the same histogram as before export.

---

## System-Wide Impact

- **Interaction graph:** FlimPanel buttons + PhasorPlotWindow.showEvent + PhasorPlotWindow active-channel handler → `LoadCachedPhasor` (U1) → repo. IoPanel button → launcher → `ExportPhasorNpz` (U4) → repo. AddLayerDialog tab → `ImportPhasorNpz` (U6) → repo. No napari layer code is touched.
- **Error propagation:** `NoCachedPhasorError` (new) is raised inside `LoadCachedPhasor` and caught at every call site (button handlers fall through to compute; auto-load is a no-op). `LayerAlreadyExistsError` (existing) is raised inside `ImportPhasorNpz` when `force=False` and surfaced as a per-row conflict chip in the import tab. `NoDatasetError` is raised by all three new use cases when no dataset is loaded; existing handler patterns surface this in the status bar.
- **State lifecycle risks:** Atomic-write contract for export prevents orphan `.tmp` files. Import's `delete_path` before re-write prevents partial overwrites (existing `/phasor/<ch>/lifetime_filtered` would be orphaned by a bare `write_array` on `g`/`s`).
- **API surface parity:** Three new use cases; one new domain error; no new repo port methods. Existing callers of `compute_phasor` / `apply_wavelet` are untouched.
- **Integration coverage:** End-to-end round-trip test (U4 + U6 + U1) is the load-bearing assertion that the .npz schema is consistent.
- **Unchanged invariants:** napari layer code (recent fix), session selection-field ownership rules, the existing TCSPC-re-import → `/phasor/<ch>` invalidation chain, `compute_phasor` / `apply_wavelet` write paths and their attrs schema, the `viewer.add_labels` / `add_mask` wrappers and the `_phasor_roi_preview_<name>` layer naming convention.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `np.load(allow_pickle=True)` exposes pickle deserialization on user-supplied files | Surface in import-tab help text; PerCell4 already accepts the same risk for ImageJ ROIs and Cellpose `.npy`. Document in the .npz schema section that import expects trusted sources. |
| Auto-load on showEvent races with an in-progress compute | Guard auto-load behind `self._g_map is None`; explicit no-op when a compute is mid-flight. |
| Atomic-write tmp file orphans on a crash mid-export | Wrap `np.savez` in try/finally; on exception, `os.unlink(tmp_path)` before re-raising. Tested via monkeypatched `np.savez` failure scenario in U4. |
| `metadata` object-array deserialization triggers a numpy deprecation warning | Confirmed at implementation time; if it surfaces, suppress with `warnings.catch_warnings()` inside `ImportPhasorNpz` for the specific category. |
| Channel-name mismatch between .npz source and target dataset (e.g., importing CA-SiR data into a dataset that doesn't have CA-SiR) | Per-row Target channel cell is editable; user maps explicitly. Use case is target-name-agnostic — writes to whatever channel is requested. |
| Importing an .npz whose `(H, W)` doesn't match the active dataset's `/decay/<ch>` shape | Validation passes (we don't check against dataset shape), but `LoadCachedPhasor` later returns intensity from `/decay` which won't align. Surface a warning chip in the import tab if dataset's `/decay/<target>` exists and shape mismatches. Defer hard rejection to a follow-up. |

---

## Documentation / Operational Notes

- After this lands, run `/ce-compound` twice (or once with both topics):
  1. Capture the **Shift-click "force-recompute" UX pattern** as a new convention in `docs/solutions/conventions/` — no prior precedent in the codebase.
  2. Capture the **`.npz` I/O schema** (file shape, dtype, metadata object-array layout, atomic-write requirement) as a new convention so future external-tool integrations follow the same shape.
- The auto-load behavior changes the perceived semantics of the `Compute Phasor` and `Apply Wavelet` buttons. Update any user-facing docs (README screenshots, FLIM workflow guide) to reflect "load if cached" semantics.
- No migration needed — existing `.h5` files already carry `/phasor/<ch>` for any dataset that was ever processed.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-03-phasor-cache-and-npz-io-requirements.md](../brainstorms/2026-05-03-phasor-cache-and-npz-io-requirements.md)
- **Related code:** `src/percell4/application/use_cases/compute_phasor.py`, `apply_wavelet.py`, `src/percell4/store.py`, `src/percell4/ports/dataset_repository.py`, `src/percell4/gui/add_layer_dialog.py`, `src/percell4/interfaces/gui/task_panels/{flim_panel,io_panel}.py`, `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- **Institutional learnings:** `docs/solutions/architecture-patterns/atomic-write-contract.md`, `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`, `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`, `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`, `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`, `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`, `docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md`, `docs/solutions/architecture-patterns/channel-deletion-permanence.md`
- **Recent related work:** Commit `ac9a20e` (per-ROI phasor preview layers + clickable visibility checkboxes) — added the `showEvent` hook this plan depends on.
