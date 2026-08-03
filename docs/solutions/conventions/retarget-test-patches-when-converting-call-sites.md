---
title: Retarget test patches when a call site moves onto a wrapper
module: gui
date: 2026-07-30
problem_type: convention
component: testing_framework
severity: high
canonical_source: src/percell4/gui/_dialog_utils.py
applies_when:
  - "Converting call sites from a directly-imported symbol onto a wrapper or facade function"
  - "Introducing a shared helper module that replaces third-party statics such as Qt QMessageBox or QInputDialog"
  - "Tests monkeypatch the symbol being replaced to suppress, stub, or capture its calls"
  - "A linter such as ruff will drop the now-unused import that tests were patching through"
  - "Reviewing a refactor whose test suite is green but whose assertions may have gone vacuous"
symptoms:
  - "AttributeError on the patch target after the unused import is auto-removed"
  - "Test suite hangs indefinitely because a real modal dialog opens instead of the stubbed answer"
  - "Capture-list assertions such as len(captured) == 1 pass while reading an empty list"
  - "Full-suite run times out with no failing test name to point at"
  - "Green suite that a code-review pass later shows was not exercising the intended path"
related_components:
  - development_workflow
  - tooling
tags:
  - monkeypatch
  - test-isolation
  - refactor-safety
  - qt-dialogs
  - pytest
  - wrapper-indirection
  - vacuous-assertions
---

# Retarget test patches when a call site moves onto a wrapper

## Context

PR #24 moved every **NORMAL-window-owned** GUI popup off Qt's static
convenience methods
(`QMessageBox.warning/question`, `QInputDialog.getText`,
`QFileDialog.getExistingDirectory/getOpenFileName/getSaveFileName`) onto
module-level wrappers in `src/percell4/gui/_dialog_utils.py`:
`message_box` (l.169), `progress_dialog` (l.204), `text_input` (l.227),
`open_file_name` (l.272), `open_file_names` (l.288), `save_file_name`
(l.303), `existing_directory` (l.325). The wrappers exist because a static
builds, shows and destroys its dialog in one call, leaving no handle on
which to set window flags before the first `show()` — the prerequisite for
the GNOME attach fix documented in
`docs/solutions/ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md`.
Twenty-three production modules now import from `_dialog_utils`. Statics
inside already-converted dialog classes were deliberately left in place -- a
popup whose parent is itself converted needs no wrapper -- so `QMessageBox.*`
still appears throughout `src/`.

Each converted call site is a seam that some test was already patching.
`monkeypatch.setattr(QMessageBox, "warning", ...)` intercepts a call site
only while that call site still *is* `QMessageBox.warning`. Route it through
a wrapper and the patch keeps applying cleanly to an attribute nobody reads
any more. Nothing errors. The patch is simply dead.

Three failure modes were observed on this change, in increasing order of
danger:

**LOUD.** `tests/test_gui/test_file_navigator.py` patched
`fnav_mod.QFileDialog.getExistingDirectory`. After `file_navigator.py`
converted, ruff removed the now-unused `QFileDialog` import, the patch
target ceased to exist, and the test failed with `AttributeError`. Diagnosed
in seconds (commit 96921d8, PR #24).

**HANG.** `tests/test_gui/test_data_panel_delete_rename_session_sync.py` and
`tests/test_gui_workflows/test_phasor_apply_current_phasor_as_mask.py`
patched `QMessageBox.question` / `QInputDialog.getText` as *suppression* —
return a canned answer so no dialog opens. With the patch dead, a real modal
opened under the offscreen platform and waited for a click that never came.
A full-suite run blocked with no failure message to point at, until the
harness's own command timeout killed it (ten minutes here; the repo configures
no pytest timeout, so an unattended run simply hangs). This happened twice; PR #24's commit edef8b1 records it as "Two
of these did not fail -- they HUNG".

**SILENT.** `tests_gui/test_viewer_add_mask_collision.py` patched
`QMessageBox.warning` as *capture* — append to a list, then
`assert len(captured) == 1` (l.83). With the patch dead the list stays
empty, and `_patch_warning` returns a list that records nothing. Note the
suite did not catch this: the `tests_gui/` GL tier aborts on this dev
machine for pre-existing reasons unrelated to the change (see
`docs/solutions/conventions/headless-test-suite-tiers.md` for the tier
split). It was found by a code-review pass — four reviewers independently —
and fixed in commit 2aad113, still inside PR #24.

## Guidance

**Before converting a call site, grep both test trees for patches of the
symbol you are about to stop calling.** `tests/` and `tests_gui/` are
separate trees and a bare `pytest` collects only `tests/`, so a single grep
across both is the only reliable sweep:

```bash
grep -rn "setattr(.*QMessageBox\|setattr(.*QInputDialog\|setattr(.*QFileDialog" tests/ tests_gui/
```

**Retarget in the same commit as the conversion.** Point the patch at the
name the production code now resolves at call time:

```python
monkeypatch.setattr(viewer_mod, "message_box", ...)   # not QMessageBox.warning
```

**Patch the module that imported the wrapper, not `_dialog_utils`.**
`src/percell4/gui/viewer.py:15` does
`from percell4.gui._dialog_utils import message_box`, which binds a *new*
name in `viewer`'s namespace. Patching `_dialog_utils.message_box` rebinds
the definition module and leaves `viewer.message_box` pointing at the
original function.

**Follow the call through shared helpers.** The phasor tests patch
`percell4.gui._resource_name_prompt` (`rnp`) for the name prompt and its
collision warning -- that is where both popups actually live
(`_resource_name_prompt.py:56` and `:65`) -- and patch the phasor module
(`pp`) only for the empty-mask confirmation it raises itself
(`test_phasor_apply_current_phasor_as_mask.py:354`, `:370`). Whoever owns the
popup owns the patch point, and in one test file that can be two different
modules.

**A green suite is not proof the patches still intercept.** A capture-style
patch that stops intercepting makes its assertion vacuous rather than
failing; a suppression-style patch that stops intercepting may hang rather
than fail. Neither is a signal you can wait for. The check has to be done
deliberately, before conversion.

**Patching one level below the seam survives conversion.** `message_box`
ends in `return box.exec_()` (`_dialog_utils.py:201`), so a patch on
`QMessageBox.exec_` keeps intercepting whether or not the call site has been
converted. Patching the method the wrapper itself must call is strictly more
durable than patching the entry point.

Note what does *not* demonstrate this. `tests/test_gui/test_phasor_masks_dialog.py`
and `tests/test_gui/test_dilute_from_mask_dialog.py` do patch `QMessageBox.exec_`
(16 points between them -- 11 and 5, e.g. `test_phasor_masks_dialog.py:396`) and
needed no edit, but that is not evidence for the rule: those two dialogs were
never converted. They still build the box by hand and call `msg.exec_()`
directly (`phasor_masks_dialog.py:1234`, `dilute_from_mask_dialog.py:807`),
because a popup parented to an already-converted dialog is left alone by design.
Their survival is a coincidence of scope, not of patch depth.

## Why This Matters

The cost is asymmetric and inverted: the failure mode that costs the least
to fix is the one that announces itself, and the one that costs the most is
the one that says nothing. The `AttributeError` in `test_file_navigator.py`
cost minutes. The two hangs cost a 10-minute timeout each with no error text
to grep for — and the natural reaction to a hung suite is to suspect the
harness, not a patch that reads as fine. The GL-tier capture patch cost
nothing at the time and would have cost a real regression later: `add_mask`
would have silently stopped warning on a name collision and the test that
exists specifically to catch that (the CA-SiR crash regression,
`docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md`)
would still have been green.

The prevention is also asymmetric in your favour. The retargeting on PR #24
was 19 patch points across 3 files in `edef8b1` (12 + 3 + 4), plus 2 in
`test_file_navigator.py` and 1 helper in `tests_gui/`. (`edef8b1`'s own message
says "13", which its breakdown already contradicts -- count the diff, not the
commit message.) One of those points —
`_accept_default_name` in
`tests/test_gui_workflows/test_phasor_apply_current_phasor_as_mask.py:28` —
is called from 10 tests (lines 87, 145, 168, 180, 199, 217, 231, 353, 369,
437) out of 33 in the file. Shared patch helpers mean the edit count is far
below the affected-test count, so the grep-and-retarget pass is much cheaper
than the count of broken tests suggests.

## When to Apply

Apply whenever a call site stops calling the symbol a test knows about:

- Extracting a wrapper around a third-party or stdlib static/global
  (the case here).
- Moving a call from a direct import to an indirection layer, a facade, or a
  shared helper module.
- Renaming or relocating a function that tests patch by module attribute.
- Deleting an import that ruff will then garbage-collect — this is what turns
  a silent break into a loud one, and it is luck, not design.

Highest risk when the patched symbol opens a **modal** dialog or otherwise
blocks: those turn into hangs. Second-highest when the patch is
**capture-and-assert**: those turn into vacuous assertions. Lowest when the
patch target disappears entirely, which fails loudly.

Also apply the reverse check when a test hangs or a formerly meaningful
assertion goes quiet after a refactor: ask whether the patch still names
something the production code reads.

## Examples

**LOUD — `tests/test_gui/test_file_navigator.py`, before (96921d8):**

```python
monkeypatch.setattr(
    fnav_mod.QFileDialog, "getExistingDirectory", lambda *a, **k: str(sub)
)
```

**After (`test_file_navigator.py:81-84`):**

```python
# Patched on the module's own name: the folder picker now routes
# through _dialog_utils.existing_directory so it can be made
# freestanding, and GNOME no longer glues it to the launcher.
monkeypatch.setattr(fnav_mod, "existing_directory", lambda *a, **k: str(sub))
```

**HANG — `tests/test_gui/test_data_panel_delete_rename_session_sync.py`,
before (edef8b1):**

```python
# Auto-accept the QMessageBox.question confirmation in _on_delete_layer.
from qtpy.QtWidgets import QMessageBox
monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
...
from qtpy.QtWidgets import QInputDialog
monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("mask_a_renamed", True))
```

**After (l.76 and l.157; the rename patch at l.178 is the same shape):**

```python
monkeypatch.setattr(dp, "message_box", lambda *a, **kw: QMessageBox.Yes)
monkeypatch.setattr(dp, "text_input", lambda *a, **kw: ("mask_a_renamed", True))
```

**HANG, shared-helper case — `_accept_default_name`, before (edef8b1):**

```python
def _accept_default_name(monkeypatch) -> None:
    """Patch QInputDialog.getText to OK whatever default it received."""
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(
            lambda *args, **kwargs: (
                kwargs.get("text", args[3] if len(args) > 3 else ""),
                True,
            )
        ),
    )
```

**After
(`tests/test_gui_workflows/test_phasor_apply_current_phasor_as_mask.py:28-44`)
— one edit, 10 callers, and note the target is `rnp`, the prompt module, not
the phasor module under test:**

```python
def _accept_default_name(monkeypatch) -> None:
    """OK whatever default the name prompt received.

    Patched on the prompt module's own ``text_input`` name rather than the
    Qt static: the prompt now routes through ``_dialog_utils`` so its popup
    can be made freestanding, and patching ``QInputDialog.getText`` would
    silently stop intercepting -- leaving a real modal dialog to hang the
    suite.
    """
    monkeypatch.setattr(
        rnp,
        "text_input",
        lambda *args, **kwargs: (
            kwargs.get("text", args[3] if len(args) > 3 else ""),
            True,
        ),
    )
```

**SILENT — `tests_gui/test_viewer_add_mask_collision.py`, before (2aad113):**

```python
def _patch_warning(monkeypatch) -> list[tuple]:
    """Capture ``QMessageBox.warning`` calls instead of opening a dialog."""
```

**After (l.48-67), against the assertion at l.83
(`assert len(captured) == 1`) that would otherwise have read an empty list:**

```python
def _patch_warning(monkeypatch) -> list[tuple]:
    """Capture the collision popup instead of opening a real dialog.

    Patched on the viewer module's own ``message_box`` name, not the Qt
    static: ``add_mask`` routes through ``_dialog_utils.message_box`` so the
    popup can be made freestanding, and patching ``QMessageBox.warning``
    would no longer intercept -- leaving a real modal to block this tier.
    """
    from qtpy.QtWidgets import QMessageBox

    from percell4.gui import viewer as viewer_mod

    captured: list[tuple] = []
    monkeypatch.setattr(
        viewer_mod,
        "message_box",
        lambda parent, title, text, *a, **kw: captured.append((title, text))
        or QMessageBox.StandardButton.Ok,
    )
    return captured
```

The corresponding production call is `src/percell4/gui/viewer.py:367`, with
the wrapper bound at module level by `viewer.py:15`
(`from percell4.gui._dialog_utils import message_box`) — which is exactly
why `viewer_mod` and not `_dialog_utils` is the correct patch target.

**Bulk retarget, mechanical case —
`tests/test_gui/test_resource_name_prompt.py` (edef8b1):** 11 occurrences of
`monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))`
became `monkeypatch.setattr(rnp, "text_input", fake)` (lines 39, 55, 71, 90,
107, 134, 138, 153, 172, 192, 212), and
`monkeypatch.setattr(rnp.QMessageBox, "warning", staticmethod(fake_warn))`
became `monkeypatch.setattr(rnp, "message_box", fake_warn)` (l.114). The
`staticmethod()` wrapper disappears with the class-attribute target: a module
global is not a descriptor, so nothing can bind `self` to it. (On the
class-attribute side `staticmethod` was belt-and-braces rather than required --
Python 3 binds `self` only on *instance* access -- but it is a harmless habit
there and simply unnecessary here.)

## Related

- [`headless-test-suite-tiers.md`](headless-test-suite-tiers.md) — the `tests/`
  vs `tests_gui/` split, and why a bare `pytest` never collects the GL tier.
  That is the enabling condition for the SILENT case below. It also already
  states the production-side mirror of this rule: an isolation mechanism is
  consulted *inside* the factory rather than monkeypatched onto it, "because
  call sites may `from ... import app_settings` and capture the function at
  import time." This doc is the test-side half of that same insight.
- [`../ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md`](../ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md)
  — the change that caused this. It is why the wrappers exist; this doc is its
  collateral damage on the test seams.
- [`../ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md`](../ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md)
  — its regression guard is `tests_gui/test_viewer_add_mask_collision.py`, the
  test that went vacuous. Between the conversion and commit `2aad113` that
  guard was asserting nothing.
- [`../ui-bugs/qt-setwindowflag-hides-visible-widget-2026-05-14.md`](../ui-bugs/qt-setwindowflag-hides-visible-widget-2026-05-14.md)
  — why the wrappers must run before the first `show()`, which is why the
  statics could not be kept.
