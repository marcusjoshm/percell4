---
status: pending
priority: p3
issue_id: "028"
tags: [code-review, patterns, cleanup]
dependencies: ["024"]
---

# Inconsistent re-export shim patterns

## Problem Statement

`gui/launcher.py` uses a clean single-class re-import. `gui/cell_table.py`, `gui/data_plot.py`, `gui/phasor_plot.py` use star import + explicit re-import. Domain shims (measure/measurer.py, flim/phasor.py) also use star + explicit. The launcher shim deviates from all others.

## Acceptance Criteria

- [ ] All re-export shims follow the same pattern
- [ ] Or: all shims are deleted (see todo 024)
