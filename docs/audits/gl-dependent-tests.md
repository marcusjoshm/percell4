# GL-dependent tests

Tests that construct a real `napari.Viewer`, and so a real vispy OpenGL
canvas. They cannot run under `QT_QPA_PLATFORM=offscreen` on macOS — that
combination segfaults (exit 139) rather than failing — so they live in
`tests_gui/` rather than `tests/`.

Regenerate with:

```bash
PERCELL4_GL_AUDIT=1 QT_QPA_PLATFORM=offscreen pytest -o addopts="-m 'not slow'"
```

The audit patches `napari.Viewer.__init__` to record and then raise, so no GL
context is ever created and one pass enumerates everything. Recording *before*
raising is required, not defensive: `segmentation_panel.py` wraps the same
access in `try: ... except Exception: return`, which swallows a raise-only
probe at precisely the site worth finding.

## Inventory

200 constructions across 17 modules.

| Constructions | Module |
|---:|---|
| 14 | `tests/test_gui/test_dilute_phase_workflow_sidebar.py` |
| 6 | `tests/test_gui_workflows/test_label_edit_timepoint_scope.py` |
| 5 | `tests/test_gui_workflows/test_multi_select_e2e.py` |
| 4 | `tests/test_gui_workflows/test_multi_select_keystroke.py` |
| 16 | `tests/test_gui_workflows/test_seg_qc_empty_labels_recovery.py` |
| 16 | `tests/test_gui_workflows/test_seg_qc_modify_and_rerun.py` |
| 24 | `tests/test_gui_workflows/test_seg_qc_modify_channel.py` |
| 28 | `tests/test_gui_workflows/test_seg_qc_modify_channel_rerun.py` |
| 28 | `tests/test_gui_workflows/test_seg_qc_rerun.py` |
| 16 | `tests/test_gui_workflows/test_seg_qc_scroll.py` |
| 9 | `tests/test_gui_workflows/test_seg_qc_timelapse.py` |
| 7 | `tests/test_gui_workflows/test_session_to_napari_push.py` |
| 4 | `tests/test_gui_workflows/test_timepoint_slider_sync.py` |
| 7 | `tests/test_gui_workflows/test_tracks_layer.py` |
| 3 | `tests/test_gui_workflows/test_viewer_add_mask_collision.py` |
| 1 | `tests/test_gui_workflows/test_viewer_filter_colors.py` |
| 12 | `tests/test_gui_workflows/test_viewer_presets_propagation.py` |

## The one grep could not find

Sixteen of these carried `pytestmark = pytest.mark.napari_viewer` already.
The seventeenth, `tests/test_gui/test_dilute_phase_workflow_sidebar.py`, does
not mention napari or `ViewerWindow` anywhere in its source. It builds a real
`LauncherWindow`, which owns a `ViewerWindow`; a queued `_wire_paint_autosave`
then reads `.viewer` and constructs the canvas — sometimes during a *later*
test's setup, via pytest-qt's `_process_events`. It passes in isolation and
segfaults when run after `test_cnr_segmenter.py`.

This is why the set is enumerated dynamically. A static scan for `napari` or
`ViewerWindow` misses it, and per-module isolation runs miss it too, because
the construction is both transitive and deferred.

Its 14 constructions are all forced existence checks rather than genuine use,
so it is a candidate to stay in `tests/` once panels stop building a viewer
merely to ask whether one exists.
