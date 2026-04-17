---
status: complete
priority: p2
issue_id: "022"
tags: [code-review, architecture, duplication]
dependencies: []
---

# domain/ duplicates original modules (2,680 LOC) — FALSE POSITIVE

## Resolution

The simplicity reviewer incorrectly described this as "verbatim copies." In reality:
- `domain/` has the canonical code (e.g., `domain/measure/measurer.py` = 572 lines)
- Original files are 9-line re-export shims (e.g., `measure/measurer.py` = `from percell4.domain.measure.measurer import *`)

There is NO code duplication. The shims exist so that ~40 files across `gui/`, `workflows/`, `task_panels/` don't need their imports rewritten in this branch. The shims are 3-9 lines each and carry negligible maintenance cost.

**Decision:** Keep the shim structure. The shims will be removed incrementally as files are touched (covered by #024 "two parallel architectures" which is now P3).
