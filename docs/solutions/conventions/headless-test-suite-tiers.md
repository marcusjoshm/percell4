---
module: tests
tags: [testing, qt, napari, ci, headless, qsettings]
problem_type: convention
applies_to:
  - "tests/**"
  - "tests_gui/**"
  - "pyproject.toml"
  - ".github/workflows/ci.yml"
canonical_source:
  - "tests/conftest.py"
  - "src/percell4/gui/settings.py"
---

# The test suite is split by what each test physically requires

`pytest` is headless, touches nothing outside `tmp_path`, and selects exactly
the tests the blocking CI job selects.

## Two trees

**`tests/`** — everything that runs without an OpenGL context. Collected by a
bare `pytest` via `testpaths = ["tests"]`. Runs under
`QT_QPA_PLATFORM=offscreen`. Most of it needs a `QApplication` (dialog
validation, panel wiring, workflow-runner phase ordering); that is fine, Qt
widgets do not need GL.

**`tests_gui/`** — the ~100 tests that build a real `napari.Viewer`, and so a
real vispy canvas. Run with `pytest tests_gui/`. Not in `testpaths`, so a bare
`pytest` cannot collect them.

The split is by physical requirement, not by name. `tests/test_gui/` is full of
tests that belong in `tests/`.

## Why a directory and not a marker

A marker relies on `addopts`, and any explicit `-m` on the command line
silently replaces it. That is not hypothetical: CI passed
`-m 'not slow and not gui'`, which overrode the `addopts` exclusion and ran 100
napari tests that no local run ever executed — while the `gui` half of the
expression matched zero tests and excluded nothing, because the marker was
declared and never applied. Local green did not imply CI green, in either
direction.

Keep all test selection in `pyproject.toml`'s `addopts`. CI runs a bare
`pytest`. A declared marker that nothing carries is worse than no marker: it
reads like a gate.

## Ordering: quarantine before offscreen

macOS offscreen has no GL context, so a real viewer built under it segfaults —
exit 139, no traceback, partway through the run. An earlier attempt at
`QT_QPA_PLATFORM=offscreen` hit this and concluded offscreen was unusable. It
is usable; it just cannot come first. Relocate the GL tests, then enable
offscreen.

## Finding GL-dependent tests

Not by grep. `test_dilute_phase_workflow_sidebar.py` named neither napari nor
`ViewerWindow` yet constructed 14 viewers: it built a real `LauncherWindow`,
which owns a `ViewerWindow`, and a queued handler read `.viewer` — sometimes
during a *later* test's setup, so it passed alone and segfaulted in company.

An always-on guard in `tests/conftest.py` patches `napari.Viewer.__init__` to
record and then raise. It records *before* raising because `segmentation_panel`
wraps the same access in `except Exception: return`, which would swallow a
raise-only guard exactly where it matters.

## Ask whether a viewer exists with `existing_viewer`

`ViewerWindow.viewer` constructs one on access, so `if win.viewer is None:` can
never be true and builds a canvas to find that out. Use
`ViewerWindow.existing_viewer` for existence checks and cleanup paths; reserve
`.viewer` for "show the user something now". This is also correct outside
tests — loading a dataset used to spawn the napari window whether or not the
researcher had opened it.

## Preferences must go through `app_settings()`

`percell4.gui.settings.app_settings()` is the only place a `QSettings` is
constructed, enforced by
`tests/test_gui/test_settings_isolation_compliance.py` across `src/`, `tests/`
and `tests_gui/`.

On macOS the two-argument `QSettings(org, app)` constructor resolves to the
live CFPreferences domain and ignores both `setDefaultFormat` and `setPath` —
so three test fixtures that believed they had sandboxed settings were reading,
writing and `clear()`-ing the researcher's real saved window layout. Running
the suite rearranged their desktop. The only redirect macOS honours is the
four-argument `IniFormat` constructor, which lives in `settings.py`.

The redirect is consulted *inside* `app_settings()` rather than monkeypatched
onto it, because call sites may `from ... import app_settings` and capture the
function at import time. An isolation mechanism that silently covers only some
call sites is the original bug.

`tests_gui/conftest.py` duplicates the sandbox, the binding pins and the
delete-drain rather than importing them. The pins must run before any `qtpy`
import in whichever session is active, and a root `conftest.py` is the only
place that ordering holds.

## Font metrics differ by platform

Qt assumes 96 DPI offscreen; macOS lays out on a 72pt basis, so identical
widgets measure ~25% wider offscreen and any pixel-budget assertion changes
meaning with the platform. `tests/conftest.py` sets `QT_FONT_DPI=72`. An
environment variable, not a fixture: it must be set before the `QApplication`
exists, which is earlier than any autouse fixture can run.

## Both Qt binding pins are needed

`QT_API` selects qtpy's binding, `qt_api` in `pyproject.toml` selects
pytest-qt's, and they select independently. `pyqtgraph` honours neither and
runs its own detection, so it needs `PYQTGRAPH_QT_LIB`. Two bindings in one
process abort with SIGABRT the moment both try to own the event loop.
