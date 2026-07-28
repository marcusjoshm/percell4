# tests_gui/

Tests that construct a real `napari.Viewer`, and therefore a real vispy
OpenGL canvas.

```bash
pytest tests_gui/
```

They are not collected by a bare `pytest` — `testpaths = ["tests"]` in
`pyproject.toml` points the default run at `tests/` only. That is deliberate:
it is what lets `tests/` be headless without exception.

## Why they live here

`tests/` runs under `QT_QPA_PLATFORM=offscreen`, which has no OpenGL context
on macOS. A real viewer built under it does not fail — it segfaults, exit 139,
partway through the run and with no traceback. These tests cannot be made
headless on macOS, so they are separated by what they physically require
rather than by what they are named.

Two consequences worth knowing before you run them:

- **They open real windows and take focus.** The headless guarantee covers
  `tests/`, not this suite. Do not run it while you are working in another
  application.
- **They are unreliable on a machine with a real GPU.** Repeatedly building
  and tearing down napari canvases corrupts GL state, and napari escalates
  that to a test failure on purpose (`napari/_vispy/canvas.py` sets
  `ignore_callback_errors = False`). A local run can end in
  `GLError 1281 (invalid value)` or a segfault attributed to whichever test
  happened to be running. CI does not hit this: xvfb's software GL behaves
  differently and the suite is green there.

CI is where these actually gate. The `gui-tests` job runs them under
`xvfb-run` on every PR, and it is merge-blocking — they were inside the
blocking gate before this split and demoting them would be a real reduction
in protection.

## What they cover

The napari integration contract, and nothing else has automated coverage of
it: `napari.layers.Tracks` lineage graphs, `DirectLabelColormap` filter
colours, preset-to-`layer.opacity` propagation, the one-way session → napari
push, and the `M`-key binding surviving napari's re-registration of
`napari:new_label` on every layer add.

Two of them — `test_session_to_napari_push.py` and
`test_multi_select_keystroke.py` — are the only executable check on
invariants whose declared enforcement in `docs/audits/` is a manual grep.

## Adding a test here

Only if it builds a real viewer. Everything else belongs in `tests/`, where it
runs headless and blocks merges without needing a GPU. An autouse guard in
`tests/conftest.py` fails any test under `tests/` that constructs a
`napari.Viewer`, so if you find yourself hitting it, this is where the test
was trying to go.

Note that grep is not a reliable way to tell the two apart. A module can build
a viewer without naming one: `LauncherWindow` owns a `ViewerWindow`, and a
queued handler reading `.viewer` constructs the canvas lazily — sometimes
during a *later* test's setup. Trust the guard, not a search.
