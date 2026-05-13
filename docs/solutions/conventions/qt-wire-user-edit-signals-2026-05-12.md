---
title: Wire every user-edit signal on interactive Qt widgets
date: 2026-05-12
category: conventions
module: gui
problem_type: convention
component: development_workflow
severity: high
applies_when:
  - adding a checkable QListWidget / QTreeWidget / QTableWidget item
  - adding a non-read-only QLineEdit / QPlainTextEdit / QSpinBox / QComboBox
  - adding any widget that lets the user mutate state the dialog reads later
  - reviewing a new Qt dialog before merge
tags:
  - qt
  - dialog
  - signals
  - itemchanged
  - editingfinished
  - regression-tests
---

# Wire every user-edit signal on interactive Qt widgets

## Context

PerCell4's `compress_dialog.py` has been fixed twice in eight days for
bugs of the same shape:

- **2026-05-04 (commit `8eb12d6`)** — The Output `QLineEdit` had no
  `editingFinished` / `textChanged` connection. Discovery baked
  `output_path` into each `DatasetSpec` at scan time, and subsequent
  user edits to the Output field never propagated. `.h5` files always
  landed at the auto-fill location. *Worked around* by re-resolving in
  `compress_config` materialization.
- **2026-05-12 (commit `b584520`)** — `_ds_list` and `_ch_list`
  `QListWidget` items were created with `Qt.ItemIsUserCheckable` but
  their `itemChanged` signal was never connected to
  `_update_compress_button`. Single-checkbox toggles fired
  `itemChanged` into the void; Compress button stayed at whatever the
  last Select All / Deselect All / discovery / mode-change set it to.
  User saw "I can only compress everything." *Fixed* by connecting the
  signal at widget construction.

Both bugs share one shape: a Qt widget is created interactive
("checkable" or "editable"), looks correct to a developer reading the
code, **and passes existing tests** — because tests use `setText()` /
`setCheckState()` programmatically, which bypasses the user-driven
signal path. At runtime, real user interactions go through signals, and
those signals fire into nothing.

## Guidance

When you mark a Qt widget interactive (or accept its default
interactivity), immediately wire its user-edit signal to whatever
depends on the resulting state. The rule applies in proportion to
*what reads the state*: an enable-state slot, a downstream computation,
a Run-button gate, a re-validation trigger.

The signal you need depends on the widget:

| Widget | User-edit signal | Wire to |
|---|---|---|
| `QListWidget` item with `Qt.ItemIsUserCheckable` | `list.itemChanged` | enable-state slot / consumer |
| `QLineEdit` (not read-only) | `editingFinished` (focus-loss commit) *and/or* `textChanged` (live) | re-validation slot / consumer |
| `QPlainTextEdit` / `QTextEdit` | `textChanged` | consumer |
| `QSpinBox` / `QDoubleSpinBox` | `valueChanged` | consumer |
| `QComboBox` | `currentIndexChanged` | consumer |
| `QCheckBox` / `QRadioButton` | `toggled` | consumer |
| `QGroupBox` (checkable) | `toggled` | consumer |
| `QTableWidget` editable cell | `cellChanged` *or* `itemChanged` | consumer |

If the widget feeds a Run-button enable-state, a validation re-run, or
any "if X then Y" rule, the signal **must** be wired at construction
time. Otherwise the test-time programmatic-set path passes while the
runtime user-typing path silently no-ops.

A single regression test per dialog is sufficient: use
`item.setCheckState(...)` / `lineedit.insert(...)` (which fire the
signal) and assert that the dependent state changed. `setText("")` on a
read-only field or `clear()` followed by `insert(text)` is closer to a
typed flow than a bare `setText(text)` and is the right testing
primitive for signal-path coverage.

## Why This Matters

The class of bug is silent. Symptoms are user-facing UX failures
("button stays grey when I click", "my edit doesn't take effect"), but
the code reads correct, the tests pass, and grep doesn't surface the
missing line. The only way to catch it is by reading
construction + connection sites together: every interactive widget's
construction site should be visually adjacent to its signal wire.

The cost compounds: the May 4 fix patched the *symptom* (re-resolve at
materialization) rather than wiring the signal, which meant the May 12
fix had to be discovered independently. Both bugs landed in production
because the unit tests didn't exercise the signal path.

The rule is cheap. Connecting `itemChanged.connect(...)` is one line.
Adding a signal-path regression test is ~10 lines. Both are negligible
relative to the cost of a user-visible bug that hides until real-data
testing.

## When to Apply

- Any new Qt dialog or panel that lets the user mutate state.
- Refactoring an existing dialog: if you make a widget user-editable
  (`setReadOnly(False)`, add `Qt.ItemIsUserCheckable`, etc.), wire the
  signal in the same commit.
- Code review: grep for `ItemIsUserCheckable`, `setReadOnly(False)`,
  and bare `addItems(`/`addItem(`. For each match, check there's an
  `itemChanged.connect`, `textChanged.connect`, `editingFinished.connect`,
  `valueChanged.connect`, or `currentIndexChanged.connect` on the same
  widget. Missing wires are the bug.
- Writing tests: a dialog test suite that only uses `setCheckState` /
  `setText` is incomplete. Add at least one test per dialog that
  exercises the signal path via `insert()` or qtbot-driven interaction.

## Examples

**Wrong (the May 12 bug — `compress_dialog.py` before commit b584520):**

```python
self._ds_list = QListWidget()
ds_layout.addWidget(self._ds_list)
# ... later ...
for ds in self._datasets:
    item = QListWidgetItem(ds.name)
    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)  # interactive!
    item.setCheckState(Qt.Checked)
    self._ds_list.addItem(item)

def _update_compress_button(self):
    any_ds = any(self._ds_list.item(i).checkState() == Qt.Checked ...)
    self._btn_compress.setEnabled(any_ds and any_ch)
# ⚠ No connection from itemChanged → _update_compress_button.
# User toggles a checkbox → Qt fires itemChanged → nothing listens.
```

**Right (after commit b584520):**

```python
self._ds_list = QListWidget()
# Wire the user-edit signal at construction time. Without this, single-
# checkbox toggles fire itemChanged into the void and dependent state
# (e.g., Compress button enablement) never refreshes.
self._ds_list.itemChanged.connect(self._update_compress_button)
ds_layout.addWidget(self._ds_list)
```

**Right (signal-path regression test):**

```python
def test_single_dataset_checkbox_toggle_enables_compress(qtbot, ...):
    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    dlg._source_edit.setText(str(source_tree))
    dlg._run_discovery()
    dlg._on_deselect_all_datasets()
    assert not dlg._btn_compress.isEnabled()

    # setCheckState fires itemChanged, exercising the signal path.
    dlg._ds_list.item(0).setCheckState(Qt.Checked)

    # Without the itemChanged wire this assertion fails.
    assert dlg._btn_compress.isEnabled()
```

Same shape applies to `QLineEdit` consumers — use `lineedit.insert(text)`
rather than `setText(text)` when the test is specifically about the
signal path.

## Related

- `src/percell4/gui/compress_dialog.py` — the affected dialog; both fixes live here.
- `tests/test_gui/test_compress_dialog_checkbox_signal.py` — May 12 regression test (3 cases).
- `tests/test_gui/test_compress_dialog_output_path.py` — May 4 regression tests (3 cases).
- Commit `8eb12d6` — May 4 fix (Output `QLineEdit`, worked around at materialization).
- Commit `b584520` — May 12 fix (`itemChanged` connection).
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md` — adjacent learning: when widget construction is duplicated across sibling dialogs, drift is inevitable. Wiring up signals at the *shared widget's* construction site is the strongest defense.
