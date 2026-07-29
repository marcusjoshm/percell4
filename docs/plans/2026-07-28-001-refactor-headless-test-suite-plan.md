---
title: "refactor(tests): a headless, trustworthy test suite"
type: refactor
status: active
date: 2026-07-28
---

# refactor(tests): a headless, trustworthy test suite

## Overview

Three independent defects currently make `pytest` hostile to run and make a red CI email uninformative. They are usually described together as "the GUI tests are flaky," but they have different causes and different fixes:

1. **The suite mutates the user's real macOS preference store.** This is why *saved* window geometry is lost — PerCell4's windows come back in the wrong place on next launch, having been overwritten or outright cleared by a test run.
2. **The suite is not headless.** No `QT_QPA_PLATFORM` is set anywhere in the repo, so tests that call `.show()` put real windows on the desktop and steal focus while the run is in progress.

Both contribute to "the positions of all the windows change", and neither substitutes for the other: defect 1 is fixed by U1, defect 2 by U5.
3. **The CI marker gate is empty.** `gui` is declared in `pyproject.toml` but applied to zero tests, so CI's `-m 'not slow and not gui'` deselects nothing — and because a command-line `-m` overrides `addopts`, CI silently runs a *different, larger* suite than a local run.

This plan fixes all three, splits the suite into three tiers by what each test physically requires, and adds executable guards so each fix cannot silently regress.

**Outcome:** `pytest` locally runs fully headless, touches nothing outside `tmp_path`, and selects exactly the same tests CI's blocking job selects. A red CI email means code is broken.

---

## Problem Frame

### Defect 1 — tests write to the user's live preference domain

Production reads and writes window geometry through `QSettings("LeeLabPerCell4", "PerCell4")`, which on macOS resolves to the live `~/Library/Preferences/com.LeeLabPerCell4.PerCell4.plist`. Tests construct those windows and close them, so `_save_geometry()` overwrites the researcher's saved layout.

It is worse than overwriting. Three test modules — `tests/test_gui/test_threshold_qc_geometry_persistence.py:29`, `tests/test_gui/test_dilute_phase_panel_geometry.py:23`, `tests/test_gui_workflows/test_session_window.py:26` — carry an `isolated_settings` fixture whose docstring says *"Sandbox QSettings so the test doesn't bleed into user prefs."* The sandbox does not work. It uses `QSettings.setDefaultFormat(IniFormat)` + `setPath(...)`, but on macOS the two-argument `QSettings(org, app)` constructor resolves to the native CFPreferences domain regardless of `defaultFormat`. Each fixture then calls `QSettings("LeeLabPerCell4", "PerCell4").clear()` on **the real store**, before and after the test.

Verified during planning:

```
$ python -c "... QSettings.setDefaultFormat(QSettings.IniFormat)
              QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, sandbox)
              print(QSettings('LeeLabPerCell4','PerCell4').fileName())"
/Users/leelab/Library/Preferences/com.leelabpercell4.PerCell4.plist   # NOT the sandbox
```

And observed end-to-end: a 55-test run of four GUI modules took the real plist from 241 bytes to 42 bytes — the saved geometry was wiped. (This happened during planning research; see Risks.)

Seven further modules touch `QSettings` with no isolation attempt at all: `tests/test_gui_workflows/test_config_dialog.py`, `tests/test_gui/test_batch_tools_window.py`, `test_dilute_from_mask_dialog.py`, `test_per_particle_donut_dialog.py`, `test_per_particle_multichannel_dialog.py`, `test_phasor_masks_dialog.py`, `test_whole_field_intensity_dialog.py`.

### Defect 2 — the suite is not headless

`QT_QPA_PLATFORM` appears nowhere in `tests/`, `src/`, `.github/`, or `pyproject.toml`. 13 modules (56 tests) call `.show()` / `raise_()` / `activateWindow()` directly, and roughly 28 more reach the same calls through production code — `src/percell4/gui/threshold_qc.py:379-381` and `:608-610`, `src/percell4/gui/workflows/single_cell/seg_qc.py:262-263`, `src/percell4/gui/workflows/single_cell/dilute_queue.py:271-272`, `src/percell4/gui/viewer.py:237-238`, `src/percell4/interfaces/gui/main_window.py:765-766`. `MultiSelectController.show()` (`src/percell4/gui/multi_select.py:161`) does `show(); raise_(); activateWindow()`, the precise shape that steals macOS focus.

### Defect 3 — local and CI run different suites, and the CI gate is a no-op

| | selection | tests run |
|---|---|---|
| Local | `addopts = "-m 'not napari_viewer'"` | 3,851 |
| CI | `-m 'not slow and not gui' --ignore=tests/test_scripts` | 3,909 |

`pytest.mark.gui` is applied to **zero** tests, so `not gui` removes nothing; `not slow` removes 3. Because CI passes `-m` explicitly, it overrides `addopts` and therefore runs all 100 `napari_viewer` tests that are deselected locally. The CI comment claiming *"`gui` tests need an interactive display. Both are excluded here"* has never been true. `tests/test_scripts/` (39 tests) never runs on CI at all.

Net effect: a local green run does not predict CI, and a CI failure is plausibly attributable to the untested-locally delta — which is exactly the conditioning that makes the failure emails ignorable.

---

## Requirements Trace

- R1a. No pytest invocation in this repo — `tests/` or `tests_gui/` — may read or write the user's real `LeeLabPerCell4/PerCell4` preference store. (U1)
- R1b. Running `pytest` must not put a window on screen or take focus, so no desktop window moves during a run. (U5)
- R2. Every test under `tests/` must run headless — no window ever appears on screen.
- R3. GUI tests that cannot run headless must live outside `tests/`, in a separate suite that is not part of the default run.
- R4. A CI failure must indicate broken code, not a poorly designed test. The only selection delta between a local `pytest` and the blocking CI job must be the documented `slow` and `tests/test_scripts` exclusions, and that delta must itself be asserted by a test.
- R5. Each of the above must be enforced by an executable guard, not by convention, so it cannot silently regress.
- R6. Coverage of the napari viewer integration contract must be preserved and must still run somewhere automated.

---

## Scope Boundaries

- Not rewriting GUI tests to assert differently. Where visibility *is* the assertion, it stays — `QT_QPA_PLATFORM=offscreen` preserves those semantics while removing the desktop side effect.
- Not deleting the ~1,200 Qt-widget tests. They run fine headless and have a documented record of catching real bugs.
- Not adding new GUI feature coverage.
- Not changing the manual GUI testing practice.
- Not addressing Cellpose `slow` tests or model-download behavior.

### Deferred to Follow-Up Work

- Wiring `lint-imports` into CI. The four import-linter contracts in `pyproject.toml` are declared but executed by nothing; that is a real gap, but it is orthogonal to headlessness and belongs in its own change.
- Re-homing `tests/test_scripts/` (39 tests) so it runs somewhere. It is `--ignore`d on CI because it exercises the gitignored `scripts/` directory. Noted here so the trust audit is complete; fixing it is separate.

---

## Context & Research

### Relevant Code and Patterns

- `tests/conftest.py` — pins `QT_API=pyqt5` and `PYQTGRAPH_QT_LIB=PyQt5` before any qtpy import, and holds the autouse `_flush_pending_qt_deletions` fixture. **All three must stay at the root conftest**; the env pins must execute before *any* qtpy import in the session, and the delete-drain is what stops Qt teardown errors being misattributed to Qt-free suites.
- `src/percell4/gui/viewer.py:184` `_ensure_viewer()` — builds `napari.Viewer(show=False)`; `:223` the `viewer` property calls it. `:728-737` `_save_geometry` / `_restore_geometry` on the real QSettings domain.
- `src/percell4/gui/segmentation_panel.py:384` `_wire_paint_autosave` — reads `launcher._windows["viewer"].viewer`, which *forces* viewer construction.
- Invariant-by-inspection guard pattern: `tests/test_workflows/test_qt_free_imports.py`, `tests/test_gui/test_signal_lifetime_compliance.py`, `tests/test_gui/test_dialog_helper_compliance.py`, `tests/test_gui/test_stitching_form_consolidation.py`. New guards in this plan follow this established house style.
- Canonical-source precedent: `channel_display_name`, `StitchingForm` — this repo consolidates duplicated logic behind one helper. `app_settings()` follows that convention.

### Institutional Learnings

- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — `compress_dialog.py` was broken twice in eight days by changes whose tests passed, because tests used `setText()` / `setCheckState()` and bypassed the user-driven signal path. Mandates at least one signal-path test per dialog. **These tests need a real QApplication but no GL — they belong in the CI-blocking tier.**
- `docs/solutions/ui-bugs/qt-setwindowflag-hides-visible-widget-2026-05-14.md` — a test asserting only `windowFlags() & WindowStaysOnTopHint` **passed against the buggy code**; only `qtbot.waitExposed(win)` + `assert win.isVisible()` caught it. Named guard: `tests/test_gui_workflows/test_session_window.py::test_pin_on_top_toggle_keeps_window_visible`. Do not de-`show()` this test.
- `docs/solutions/ui-bugs/phasor-plot-deaf-to-session-after-close-reopen-2026-06-18.md` — the load-bearing assertion requires a real `close()` → `show()` cycle.
- `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md` — the two documented multi-select races are silent and *"only surface with fast clicking or window close mid-tool, exactly the cases manual smoke-testing skips."* Manual testing is explicitly ruled out as a substitute for this class.
- `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md`, `keystroke-binding-on-napari-viewer.md`, `gui-action-contract-exhaustiveness.md` — the Selector/Creator/Action audits are enforced by **grep**, not by tests, so relocating tests does not break them. But `tests/test_gui_workflows/test_session_to_napari_push.py` and `test_multi_select_keystroke.py` are the only *executable* check on those invariants; losing them downgrades a tested invariant to a grep-only one. This is why R6 exists.
- Prior attempt record (commit `fc2fdc1c`): sharing one viewer → 30 failed/50 errors; `QT_QPA_PLATFORM=offscreen` → hard segfault; vispy `gl="dummy"` → 145 errors; mocking viewers → assertions become tautologies. **This plan does not contradict that finding — it explains it.** Offscreen segfaults *because of napari viewer construction*; once the viewer-constructing tests leave `tests/`, offscreen is viable for everything that remains. Sequencing is therefore load-bearing.
- No `docs/solutions/` entry exists for test-infrastructure decisions. The knowledge currently lives only in `pyproject.toml` comments and commit messages. U8 fixes that.

### Planning-time experiments (evidence for the decisions below)

| Experiment | Result |
|---|---|
| `setDefaultFormat(Ini)` + `setPath` then `QSettings(org, app).fileName()` | Still the real plist — **redirect fails on macOS** |
| `QSettings(IniFormat, UserScope, org, app)` (4-arg) + `setPath` | Redirects correctly to the sandbox |
| `HOME` override | `fileName()` reports the fake path but no file is written; unreliable (CFPreferences caching) |
| Patch `qtpy.QtCore.QSettings` in a `-p` plugin before percell4 imports, then run 3,851 tests | Real plist **byte-identical** before and after — approach validated |
| Full suite under `QT_QPA_PLATFORM=offscreen` | **Segfault (exit 139) at 39%**, in `napari/_vispy/canvas.py` `get_max_texture_sizes` |
| Each napari-referencing module in isolation under offscreen | Exactly the 16 `napari_viewer`-marked modules crash; all others pass |
| `test_cnr_segmenter.py` + `test_dilute_phase_workflow_sidebar.py` together | **Segfault**, though each passes alone |

That last row is the important one: `test_dilute_phase_workflow_sidebar.py` builds a real `LauncherWindow`, which owns a real `ViewerWindow`; a queued `_wire_paint_autosave` then fires during a *later* test's setup and lazily constructs `napari.Viewer`. The module contains no reference to napari or `ViewerWindow`, so **no static grep can find it**. The GL-dependency set must be established dynamically.

---

## Key Technical Decisions

- **Tier by physical requirement, not by directory name.** Tier 1 Qt-free (~2,500), Tier 2 Qt-but-GL-free (~1,300), Tier 3 real-GL (~100+). The `tests/test_gui*` directory names do not match these tiers today and are not a reliable proxy.
- **Redirect through a canonical `app_settings()` factory, not by patching `qtpy.QtCore.QSettings`.** Both work — the class patch was validated end-to-end during planning (3,851 tests, byte-identical plist) and is one file rather than fifteen. It was rejected for two reasons worth stating plainly, since it is the cheaper option: it silently changes the behaviour of any third-party Qt code in-process, and it is invisible to this repo's grep-guard house style, so nothing would stop a new dialog from constructing its own store and quietly escaping the sandbox. The factory makes the invariant checkable (`test_settings_isolation_compliance.py`), which is what R5 asks for. The four-argument `IniFormat` constructor is still the mechanism underneath — it is the only form macOS honours — but it lives in one place instead of leaking into every call site.
- **Enumerate GL-dependent tests dynamically.** A conftest guard that raises on `napari.Viewer.__init__` produces the true list, including transitive and deferred constructions that grep misses.
- **Fix the forced-construction bug rather than quarantining around it.** `_wire_paint_autosave` forcing viewer construction is a production side effect (loading a dataset builds the napari viewer even if the user never opened it), not merely a test artifact. Fixing it shrinks the Tier 3 set on the merits.
- **`tests_gui/` as a sibling directory, not a marker.** `testpaths = ["tests"]` then makes headlessness structural: a bare `pytest` cannot collect a GL test even by accident. A marker relies on `addopts`, which any explicit `-m` silently overrides — the exact mechanism behind Defect 3.
- **Keep the Tier 3 tests.** They are the only automated coverage of the napari integration contract, and they are green on CI under xvfb.

---

## Open Questions

### Resolved During Planning

- *Delete the viewer tests or relocate them?* Relocate to `tests_gui/`, CI-only. (User decision, 2026-07-28.)
- *Do the ~1,200 Qt-widget tests stay CI-blocking?* Yes. (User decision, 2026-07-28.)
- *Does `offscreen` work?* Only after Tier 3 leaves `tests/`. Verified: it segfaults today, and every crash traces to napari viewer construction.
- *Can QSettings be sandboxed without touching production?* Not reliably on macOS. A production-side `app_settings()` indirection is required.
- *Do the GUI audits depend on these tests?* No — they are grep-enforced.

### Deferred to Implementation

- The exact final membership of Tier 3. U2 produces it mechanically; it is at least the 16 marked modules plus the 10 that build a real `LauncherWindow`, but U3 may shrink it and only a full run confirms.
- Whether `tests/test_gui_workflows/conftest.py` needs splitting. It imports `PhasorPlotWindow` at module scope, so it may need to move alongside relocated modules — visible only once U4 starts.
- Whether the CI `os._exit(pytest.main(...))` workaround can be dropped from the blocking job. It exists for napari/vispy atexit core dumps; if the blocking job no longer imports napari it becomes removable, but that must be observed, not assumed.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
                    pytest            (testpaths = ["tests"])
                      │
      ┌───────────────┴───────────────┐
      │   tests/   — always headless  │        tests_gui/  — real GL
      │   QT_QPA_PLATFORM=offscreen   │        not in testpaths
      │   QSettings → tmp sandbox     │        invoked explicitly
      ├───────────────────────────────┤        ├──────────────────────┤
      │ Tier 1  Qt-free      ~2,500   │        │ Tier 3  napari.Viewer│
      │ Tier 2  Qt, no GL    ~1,300   │        │         ~100 tests   │
      └───────────────────────────────┘        └──────────────────────┘
              │                                        │
       CI job "test"  (blocking)              CI job "gui-tests"
       same selection as local                xvfb, own job
```

Settings indirection (U1) — one factory, one patch point:

```
percell4.gui.settings
    APP_ORG = "LeeLabPerCell4"
    APP_NAME = "PerCell4"
    app_settings() -> QSettings          # sole construction site

  production:  app_settings().setValue("viewer/geometry", ...)
  tests:       autouse fixture redirects app_settings -> IniFormat @ tmp_path
  guard:       no `QSettings(` outside settings.py
```

---

## Implementation Units

- U1. **Canonical `app_settings()` factory and a working test sandbox**

**Goal:** No test can read or write the user's real preference store. Delivers R1a on its own, independent of every other unit. It does **not** deliver R1b — windows still appear until U5.

**Requirements:** R1a, R5

**Dependencies:** None — land this first.

**Files:** the authoritative list is whatever the compliance guard reports; as built it was **15 source files (35 construction sites)** and **11 test modules**, roughly double the initial estimate.
- Create: `src/percell4/gui/settings.py`
- Modify (source): `gui/viewer.py`, `gui/threshold_qc.py`, `gui/analysis_widgets.py`, `gui/flim_fret_dialog.py`, `gui/dilute_from_mask_dialog.py`, `gui/phasor_masks_dialog.py`, `gui/workflows/dilute_phase/panel.py`, `gui/workflows/single_cell/config_dialog.py`, `interfaces/gui/main_window.py`, `interfaces/gui/task_panels/analysis_panel.py`, `interfaces/gui/peer_views/session_window.py`, `data_plot.py`, `phasor_plot.py`, `cell_table.py`, `batch_tools_window.py` (all under `src/percell4/`)
- Modify: `tests/conftest.py`
- Modify (remove the three non-functional `isolated_settings` fixtures and their 13 call-site parameters): `tests/test_gui/test_threshold_qc_geometry_persistence.py`, `tests/test_gui/test_dilute_phase_panel_geometry.py`, `tests/test_gui_workflows/test_session_window.py`
- Modify (repoint onto `app_settings()`): `tests/test_gui_workflows/test_config_dialog.py`, `tests/test_gui/test_phasor_masks_dialog.py`, `test_dilute_from_mask_dialog.py`, `test_whole_field_intensity_dialog.py`, `test_per_particle_donut_dialog.py`, `test_per_particle_multichannel_dialog.py`, `test_batch_tools_window.py` (the last fakes the factory, not `QSettings`)
- Test: `tests/test_gui/test_settings_isolation_compliance.py`

**Note on the test-side migration:** `test_config_dialog.py` reads the store to assert what the dialog wrote. Migrating production without migrating that test makes it read the real plist while the dialog writes the sandbox — it fails, and still touches the user's store. The test side is not optional.

**Approach:**
- `settings.py` owns `APP_ORG` / `APP_NAME` and an `app_settings()` factory. Modules currently defining their own `_QSETTINGS_ORG` / `_QSETTINGS_APP` constants import from here instead; the literal `QSettings("LeeLabPerCell4", "PerCell4")` in `viewer.py:730,735` goes too.
- A **function-scoped** autouse fixture in `tests/conftest.py` (via `tmp_path_factory.mktemp`) redirects to a four-argument `IniFormat` `QSettings`. The four-argument form is required — `setDefaultFormat` does not redirect on macOS. Function-scoped, not session-scoped, so a dialog reading a remembered value sees only what the current test wrote; that is what the various `_clear_qsettings` fixtures were hand-rolling, and it removes their order dependence.
- The redirect must be consulted *inside* `app_settings()` rather than monkeypatched onto it. Call sites may write `from percell4.gui.settings import app_settings`, which captures the function object at import time — patching the module attribute would miss exactly those, and a redirect that silently covers only some call sites is the bug being fixed.
- Delete the three `isolated_settings` fixtures. They do not work, and their `.clear()` calls are what actively destroy the researcher's saved layout. Removing them is a bug fix, not a coverage loss.
- Keep the geometry *behaviour* tests; they assert round-tripping, which works identically against the sandbox.

**Execution note:** Write the compliance guard first — it should fail against today's tree, listing every unmigrated call site, and that list becomes the migration checklist.

**Patterns to follow:** `tests/test_gui/test_signal_lifetime_compliance.py` for the regex-over-source guard; `channel_display_name` for the canonical-helper shape.

**Test scenarios:**
- Happy path: `app_settings()` returns a `QSettings` whose `organizationName`/`applicationName` are `APP_ORG`/`APP_NAME`.
- Integration: with the autouse fixture active, `app_settings().fileName()` is under `tmp_path` and not under `~/Library/Preferences`.
- Integration: write a key via `app_settings()`, assert the real `com.LeeLabPerCell4.PerCell4` domain is unchanged — the direct regression test for R1.
- Happy path: geometry round-trip through the sandbox still restores the saved value (preserves what `test_threshold_qc_geometry_persistence` and `test_dilute_phase_panel_geometry` were testing).
- Edge case: `app_settings()` called twice returns equivalent stores; a value written by one is visible to the other.
- Error path / guard: the compliance test fails when a synthetic `QSettings(` call is introduced outside `settings.py` (self-check, mirroring the third test in `test_signal_lifetime_compliance.py`).

**Verification:** Snapshot the real plist's mtime and size, run the whole suite, confirm both are unchanged. This is the exact check that caught the problem during planning.

---

- U2. **Dynamic inventory of GL-dependent tests**

**Goal:** Produce the true list of tests that construct a real `napari.Viewer`, including transitive and deferred constructions.

**Requirements:** R3

**Dependencies:** U1. The enumeration method is a full unsandboxed GUI run; without U1's redirect in place it repeats the exact preference-store damage the plan opens with.

**Files:**
- Create: `tests/conftest.py` guard hook (temporary, opt-in via an env var) or a throwaway `-p` plugin
- Create: `docs/audits/gl-dependent-tests.md` (the resulting inventory)

**Approach:**
- Patch `napari.Viewer.__init__` to **record then raise**: append `(nodeid, stack summary)` to a process-global registry *before* raising, and dump the registry in `pytest_sessionfinish`.
- Recording first is not belt-and-braces, it is required. `segmentation_panel.py:382-386` is `try: napari_viewer = viewer_win.viewer / except Exception: return` — a raise propagates straight into that handler and is discarded, so a raise-only guard would report clean for `test_dilute_phase_workflow_sidebar.py`, the plan's own motivating example. Do not rely on the exception surfacing as a test failure.
- Raising in addition to recording is what makes this enumerable in one pass — a segfault ends the process and reveals one offender per run.
- Record both the *constructing* test and, where they differ, the test during whose setup the deferred construction fired. `_wire_paint_autosave` fires inside `pytest-qt`'s `_process_events` during a later test's setup, so the two are not the same and the distinction matters for U3.

**Test scenarios:** `Test expectation: none -- this unit produces an inventory document and throwaway tooling, not shipped behavior.`

**Verification:** The inventory contains at least the 16 `napari_viewer`-marked modules and the 10 modules that construct a real `LauncherWindow` (`tests/test_gui/test_dilute_phase_workflow_sidebar.py`, `test_launcher_batch_console_tab.py`, `test_phasor_masks_dialog.py`, `test_scripts_panel.py`, `test_workflows_panel_dilute_from_mask_wiring.py`, `test_workflows_panel_flim_fret_wiring.py`, `tests/test_gui_workflows/test_launcher_opens_session_window.py`, `test_launcher_view_bin_rebuild.py`, `test_multi_select_keystroke.py`, `test_phasor_apply_current_phasor_as_mask.py`). If any is missing, the guard is not catching deferred construction.

---

- U3. **Stop panels forcing napari viewer construction**

**Goal:** Reading launcher state must not build a viewer as a side effect. Shrinks the Tier 3 set and removes a real production side effect.

**Requirements:** R3, R6

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/gui/viewer.py` (add a non-constructing accessor alongside the existing `viewer` property)
- Modify: `src/percell4/gui/segmentation_panel.py` — `_wire_paint_autosave`, the `viewer_win.viewer.layers` reads at `:1079`, `:1156`, **and every `X.viewer is None` existence guard** at `:467, 806, 822, 867, 887, 896, 1074, 1153`
- Modify: `src/percell4/gui/metric_segmenter_panel.py` (`:217`, `:237`)
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (`:350`, `:469`), `src/percell4/interfaces/gui/task_panels/data_panel.py` (`:429`, `:637`)
- Test: `tests/test_gui/test_viewer_lazy_construction.py`

**Approach:**
- Add something like `ViewerWindow.existing_viewer` returning `None` when `_ensure_viewer()` has never run. Call sites whose job is *cleanup or refresh* — removing a stale layer, rewiring subscriptions — should use it and no-op when there is no viewer. There is nothing to clean up in a viewer that does not exist.
- The dominant forcing pattern is a null-check that can never be null: `if viewer_win is None or viewer_win.viewer is None:` builds a whole napari canvas purely to test for absence, because `.viewer` calls `_ensure_viewer()` and then cannot return `None`. Every one of those is a pure existence check and should become `existing_viewer`. Note which are silent cleanup and which are user-facing — `segmentation_panel.py:467`'s "Open a dataset in the viewer first" message is currently *unreachable* and becomes reachable once construction stops, which is a behaviour change worth confirming rather than discovering.
- Leave the `viewer` property untouched for genuine "show the user something now" call sites.
- This is a behavioural improvement in its own right: today, loading a dataset constructs the napari viewer even when the researcher never opened the viewer window.

**Execution note:** Characterization-first. Before changing anything, add a test asserting the *current* behaviour (a `data` state change forces construction), then invert it. That keeps the change honest about what it alters.

**Patterns to follow:** the existing lazy `_ensure_viewer` idiom in `src/percell4/gui/viewer.py:184-218`.

**Test scenarios:**
- Happy path: `existing_viewer` is `None` on a fresh `ViewerWindow`; after `.viewer` is touched it returns the same object.
- Integration: emitting a `data` `StateChange` at a `SegmentationPanel` whose launcher holds an untouched `ViewerWindow` does **not** construct a viewer. This is the U2 crash path, inverted into an assertion.
- Integration: when a viewer *does* exist, paint-autosave still wires up and a painted label still persists to HDF5 — the existing behaviour in `test_segmentation_panel_autosave.py` must not regress.
- Edge case: layer-removal cleanup in `analysis_panel` / `data_panel` no-ops silently when no viewer exists, rather than raising.
- Error path: an exception from `_ensure_viewer` is still swallowed by the existing `try/except` at `segmentation_panel.py:382-386` (behaviour unchanged for the real-viewer path).

**Verification:** Re-run the U2 guard. The inventory shrinks — modules that merely build a `LauncherWindow` should drop out entirely.

---

- U4. **Relocate the remaining GL-dependent modules to `tests_gui/`**

**Goal:** `tests/` contains nothing that needs a GL context.

**Requirements:** R3, R6

**Dependencies:** U2, U3

**Files:**
- Create: `tests_gui/__init__.py`, `tests_gui/conftest.py`, `tests_gui/README.md`
- Move: the post-U3 inventory (expected: the 16 `napari_viewer` modules from `tests/test_gui_workflows/`)
- Modify: `pyproject.toml` (drop the now-unused `napari_viewer` marker and the `-m` clause from `addopts`)
- Modify: `tests/test_gui_workflows/conftest.py` if relocated modules depend on its fixtures

**Approach:**
- `tests_gui/conftest.py` re-declares three things, all load-bearing: the `QT_API` / `PYQTGRAPH_QT_LIB` pins, `_flush_pending_qt_deletions`, and **the U1 `app_settings` sandbox**. These are duplicated, not moved — the pins must run before any qtpy import in whichever session is active. Omitting the sandbox would mean `pytest tests_gui/` rebuilds real `ViewerWindow`s that `_save_geometry()` into the researcher's live plist on close, reproducing defect 1 in the one suite still allowed to open windows (R1a covers *any* invocation, not just `tests/`).
- Because `testpaths = ["tests"]`, a bare `pytest` does not collect `tests_gui/`. An explicit path argument still can (`pytest .`, `pytest tests tests_gui`, and most IDE "run all tests" buttons), and a combined run is actively dangerous: `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` process-wide at import, so the GL tests would run under offscreen and segfault. Add a `pytest_collection_modifyitems` guard in `tests_gui/conftest.py` that fails collection when the platform resolves to `offscreen`, pointing the reader at `pytest tests_gui/`.
- With Tier 3 gone from `tests/`, `addopts` no longer needs a `-m` clause at all — removing it is what makes local and CI selection identical (R4).
- `tests_gui/README.md` states the requirement plainly: needs a real GL context, does not run on macOS offscreen, runs on CI under xvfb.

**Test scenarios:**
- Integration: `pytest --collect-only` from the repo root collects zero tests from `tests_gui/`.
- Integration: `pytest tests_gui/ --collect-only` collects the expected count (~100).
- Happy path: relocated modules still import and collect cleanly with no fixture-resolution errors — the main risk of the move.

**Verification:** `pytest` at the repo root completes with no `napari.Viewer` ever constructed (assert via the U6 guard).

---

- U5. **Make the default run headless**

**Goal:** `pytest` never puts a window on screen. Delivers R2.

**Requirements:** R2, R5

**Dependencies:** U4 — offscreen segfaults until Tier 3 is gone. This ordering is the whole reason the previous attempt (`fc2fdc1c`) concluded offscreen was unusable.

**Files:**
- Modify: `tests/conftest.py`
- Test: `tests/test_gui/test_headless_platform.py`

**Approach:**
- `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` alongside the existing binding pins, before any qtpy import. `setdefault` for the same reason the others use it — an explicit value still wins, so a developer can deliberately run windowed.
- Expect `WARNING: QOpenGLWidget is not supported on this platform.` on stderr; it is benign for Tier 2 and worth a comment so it is not mistaken for a failure.
- Do not de-`show()` any test. Offscreen preserves `isVisible()` semantics, which two documented regression guards depend on.

**Test scenarios:**
- Happy path: `QApplication.platformName()` is `"offscreen"` during a test run.
- Integration: a test that calls `.show()` still observes `isVisible() is True` — the `qt-setwindowflag-hides-visible-widget` guard keeps working.
- Integration: `test_pin_on_top_toggle_keeps_window_visible` and the phasor close/reopen filter test both still pass, unmodified.
- Edge case: an explicit `QT_QPA_PLATFORM=cocoa` in the environment is respected rather than overridden.

**Verification:** Run the full suite and watch the screen — no window appears. Test count matches the pre-change local baseline plus the tests that were previously deselected.

---

- U6. **Executable guards against regression**

**Goal:** Each fix stays fixed without relying on anyone remembering.

**Requirements:** R5

**Dependencies:** U1, U4, U5

**Files:**
- Create: `tests/test_gui/test_headless_invariants.py`
- Modify: `tests/conftest.py`

**Approach:**
- Autouse guard: fail loudly if any test under `tests/` constructs a `napari.Viewer`. Same **record-then-raise** shape as U2 — a raise-only guard is swallowed by `segmentation_panel.py`'s `except Exception` and reports green while a viewer is being built. This converts the U2 throwaway tooling into a permanent invariant and is what stops Tier 3 creeping back in.
- Guard: `QSettings(` appears nowhere outside `src/percell4/gui/settings.py`. Scope it to **`src/percell4/**` plus `tests/**` and `tests_gui/**`**, with an allowlist for `settings.py`, the conftest redirect fixtures, and the named R1a regression test. A src-only scan would leave test-side leaks — the seven modules found in U1 — invisible forever.
- Guard: `QT_QPA_PLATFORM` is `offscreen` for the session — but skip when the variable was explicitly set in the parent environment. U5 deliberately uses `setdefault` so `QT_QPA_PLATFORM=cocoa pytest` stays a supported way to debug visually; an unconditional assertion would turn that documented workflow into a red suite.
- Each guard gets a self-check test proving it still detects a synthetic offender, mirroring `test_signal_lifetime_compliance.py`.

**Test scenarios:**
- Happy path: all three guards pass on the post-U5 tree.
- Error path: each guard fails against a synthetic offender (a stub `QSettings("a","b")` call; a stub viewer construction).
- Edge case: the napari guard does not fire for tests that merely `import napari` without constructing a `Viewer`.
- Edge case: the QSettings guard does not flag `settings.py` itself, nor comments/docstrings mentioning `QSettings`.

**Verification:** Temporarily reintroduce each defect; the corresponding guard fails with a message naming the offending file.

---

- U7. **Correct the CI workflow**

**Goal:** The blocking CI job runs exactly the local suite; GL tests run in their own job. Delivers R4.

**Requirements:** R4, R6

**Dependencies:** U4, U5

**Files:**
- Modify: `.github/workflows/ci.yml`

**Approach:**
- Move the shared selection into **one** source rather than restating it per caller: put `-m 'not slow'` and the `tests/test_scripts` exclusion in `pyproject.toml` (`addopts` / `norecursedirs`), and have the blocking job invoke a bare `pytest` with no `-m` and no `--ignore` at all. Keeping the expression on the CI command line is what created defect 3; leaving it there in reduced form would leave local and CI selection different (39 `test_scripts` tests plus 3 `slow`) and make U7's own parity assertion unsatisfiable.
- New `gui-tests` job: `xvfb-run` over `tests_gui/`, same dependency install. **Make it merge-blocking.** These ~100 tests are inside the blocking gate today (CI's explicit `-m` overrides `addopts`, which is why they run at all); landing them as informational would be a strict reduction in protection dressed up as a reorganisation, and an advisory job is precisely the signal class the researcher already ignores. R6's value depends on this.
- Extend the lint job to `ruff check src tests tests_gui`, and add `"tests_gui/**/*.py"` to `[tool.ruff.lint.per-file-ignores]` alongside the existing `tests/**` entry (`N802, N803, N806`) — relocated modules such as `test_seg_qc_modify_and_rerun.py` use `H = W = 48` and would fail immediately otherwise. Without both changes the moved files silently stop being linted.
- Remove the `gui` marker from `pyproject.toml` — it has never selected anything and its presence is what made the CI expression look meaningful.
- Keep the `os._exit(pytest.main(...))` wrapper on the `gui-tests` job (napari/vispy atexit core dumps). Try removing it from the blocking job once that job no longer imports napari; verify rather than assume.

**Test scenarios:**
- Integration: local `pytest` and the blocking job's invocation report the same collected count. Worth asserting in a small test that reads the workflow file, given this exact drift is Defect 3.
- Happy path: the `gui-tests` job passes on CI.
- Error path: a deliberately broken Tier 2 test fails the blocking job.
- Edge case: a deliberately broken Tier 3 test fails `gui-tests` and does not affect the blocking job.

**Verification:** Open a scratch PR with one deliberately broken test in each tier and confirm the right job goes red.

---

- U8. **Document the decisions**

**Goal:** The reasoning survives; it currently lives only in commit messages and `pyproject.toml` comments.

**Requirements:** R5

**Dependencies:** U7

**Files:**
- Create: `docs/solutions/conventions/headless-test-suite-tiers.md`
- Modify: `CLAUDE.md` (a short Testing section), `tests_gui/README.md`
- Delete: `docs/audits/gl-dependent-tests.md` if U2's inventory is fully absorbed by the U6 guard

**Approach:** Record the three tiers and how to tell them apart; that macOS offscreen has no GL context, so real-viewer tests are CI-only by physics; that both binding pins are needed and why; and that `QSettings` must be constructed only via `app_settings()`. Per the repo's documentation rules, describe only what *is* — no history, no plan narrative.

**Test scenarios:** `Test expectation: none -- documentation only.`

**Verification:** A reader can determine which tier a new test belongs in without reading this plan.

---

## System-Wide Impact

- **Interaction graph:** U3 changes when `napari.Viewer` is constructed, affecting `segmentation_panel`, `analysis_panel`, `data_panel`, and anything reading `launcher._windows["viewer"]`. The Selector/Creator/Action contract is unaffected — no session field changes ownership.
- **Error propagation:** The U6 napari guard raises inside a test rather than segfaulting, converting a process kill into an attributable failure. This is the same class of improvement as commit `c7139f6c`.
- **State lifecycle risks:** `_flush_pending_qt_deletions` must keep running for every test in both `tests/` and `tests_gui/`. Deferred deletion is precisely how the crash crossed module boundaries in the planning experiments.
- **API surface parity:** `app_settings()` becomes the sole `QSettings` construction site. Any new dialog persisting a preference must use it or U6 fails.
- **Integration coverage:** The local-vs-CI selection delta disappears; that gap is what let a segfault hide 72% of the suite for three days in July.
- **Unchanged invariants:** Test *assertions* are unchanged throughout. No test is rewritten to assert something weaker — the only test-code deletions are the three `isolated_settings` fixtures, which are non-functional and actively destructive. Tier 3 tests move unmodified.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Offscreen changes widget sizing and breaks geometry assertions (commit `139e9e37` hit this: "Qt clamps width up to the font-dependent minimum, wider headless") | U5 runs the full suite before landing; fix assertions to compare against *granted* geometry rather than requested literals, the fix already applied in `dac32ed7` |
| U1's migration misses a `QSettings` call site | The U1 guard is written first and fails with the full list of offenders; U6 makes it permanent |
| The U2 inventory is incomplete, so offscreen still segfaults | U6's autouse guard converts any missed case into a named test failure instead of a process kill; iterate until clean |
| Relocated modules break on fixtures left behind in `tests/test_gui_workflows/conftest.py` | U4 collects `tests_gui/` before landing; fixture-resolution errors surface at collection |
| Tier 3 drifts into disrepair once it is off the default path | It runs in its own CI job on every PR, not on a schedule |
| **Already realized:** planning research overwrote the saved window geometry in `com.LeeLabPerCell4.PerCell4.plist` (241 → 42 bytes) | Reported to the user; the layout needs re-setting once. U1 makes it unrepeatable |

---

## Documentation / Operational Notes

- `README.md` has no "running the tests" section. U8 adds one covering `pytest` (headless, default) and `pytest tests_gui/` (needs real GL).
- After landing, `/ce-compound` is worth running — there is no `docs/solutions/` entry for test-infrastructure decisions, and this plan generates several durable ones.
- Land as a single branch. `docs/solutions/workflow-issues/complete-branch-before-merge-2026-05-06.md` names "a refactor changed a constant or signature that tests in the same module reference" as its trigger, which is this refactor's exact shape.

---

## Sources & References

- Related code: `tests/conftest.py`, `pyproject.toml` `[tool.pytest.ini_options]`, `.github/workflows/ci.yml`, `src/percell4/gui/viewer.py`, `src/percell4/gui/segmentation_panel.py`
- Related commits: `c7139f6c` (Qt segfault masking the CI suite), `dac32ed7` (deferred-delete containment, WM-dependent assertion), `fc2fdc1c` (napari_viewer marker; records the failed offscreen attempt), `139e9e37` (headless widget-width clamping)
- Learnings: `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`, `docs/solutions/ui-bugs/qt-setwindowflag-hides-visible-widget-2026-05-14.md`, `docs/solutions/ui-bugs/phasor-plot-deaf-to-session-after-close-reopen-2026-06-18.md`, `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`, `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md`
