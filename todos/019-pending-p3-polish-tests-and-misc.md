---
status: pending
priority: p3
issue_id: "019"
tags: [code-review, tests, polish]
dependencies: []
---

# Miscellaneous test and docstring polish

## Problem Statement

Low-priority polish items collected from multiple reviewers:

1. **`test_strenum_round_trip_through_value`** in `test_models.py:233` uses `or` with a tautology — the `or` short-circuits on the first clause, so the second is dead code. Simplify to a single explicit assertion.

2. **`test_no_qt_imports_in_workflows_source`** in `test_qt_free_imports.py:57-66` uses substring search (`f"import {bad}"`, `f"from {bad}"`). False-positive risk: a comment like `# never import qtpy here` trips it. An AST walk is more robust.

3. **`FailureRecord`** in `failures.py:26` is a mutable `@dataclass(kw_only=True, slots=True)` but it's semantically an immutable audit record. Making it `frozen=True` would match the other immutable types in `models.py`.

4. **`_json_default` for dead code after simplicity fix** (see todo #013).

5. **`RunLog.__init__`** creates the parent directory but not the file. A reader calling `RunLog(folder).path` and expecting `.exists()` will be surprised. One-line docstring note.

6. **`show_workflow_status`** `sub_progress` parameter can be the empty string (not `None`) — the `WorkflowHost` Protocol docstring should say so.

7. **Comment at launcher `/measurements` write sites** (`launcher.py:1669, 1768`): `# user-action path; batch workflows own their own measurement persistence`.

## Findings

- **kieran-python-reviewer** (S4 `FailureRecord` immutability, S12 docstring, N10 tautology, N11 regex brittleness, N9 RunLog docstring, N21 launcher comments)

## Proposed Solutions

### Option A — Apply all seven (Recommended)

1. Simplify the strenum test:
   ```python
   def test_strenum_round_trip_through_value():
       assert ThresholdAlgorithm("gmm") is ThresholdAlgorithm.GMM
       assert str(ThresholdAlgorithm.GMM) == "gmm"
       assert DatasetSource("h5_existing") is DatasetSource.H5_EXISTING
   ```

2. Replace substring search with AST walk:
   ```python
   import ast

   def test_no_qt_imports_in_workflows_source():
       src = ...
       for py in src.rglob("*.py"):
           tree = ast.parse(py.read_text())
           for node in ast.walk(tree):
               if isinstance(node, (ast.Import, ast.ImportFrom)):
                   names = [
                       node.module or "",
                       *(alias.name for alias in node.names),
                   ]
                   for name in names:
                       for bad in _FORBIDDEN_MODULES:
                           assert not name.startswith(bad), f"{py.name}: {name}"
   ```

3. Freeze `FailureRecord`:
   ```python
   @dataclass(frozen=True)  # or (kw_only=True, slots=True, frozen=True), per todo #005
   class FailureRecord:
       ...
   ```

4. See todo #013.

5. `RunLog.__init__` docstring: "Creates the parent folder but not the log file itself — the file is created on first `log()` call."

6. `show_workflow_status` Protocol docstring: "`sub_progress` may be the empty string when there is no sub-step to report."

7. Add comments at launcher `/measurements` write sites.

- **Pros**: Small polish items, all independent.
- **Cons**: None meaningful.
- **Effort**: Small.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `tests/test_workflows/test_models.py:233`
- `tests/test_workflows/test_qt_free_imports.py:57-66`
- `src/percell4/workflows/failures.py:26`
- `src/percell4/workflows/run_log.py:34`
- `src/percell4/workflows/host.py:31`
- `src/percell4/gui/launcher.py:1669, 1768`

## Acceptance Criteria

- [ ] `test_strenum_round_trip_through_value` has no dead-code tautology
- [ ] `test_no_qt_imports_in_workflows_source` uses AST walk
- [ ] `FailureRecord` is frozen
- [ ] `RunLog.__init__` docstring mentions file vs folder creation
- [ ] `WorkflowHost.show_workflow_status` docstring mentions empty-string semantics
- [ ] Launcher `/measurements` write sites have explanatory comments

## Work Log

- 2026-04-10 — Collected polish items.

## Resources

- Review source: kieran-python-reviewer S4, S12, N9, N10, N11, N21
