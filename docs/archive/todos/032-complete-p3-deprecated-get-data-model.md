---
status: complete
priority: p3
issue_id: "032"
tags: [code-review, shim, workflow, deprecation]
dependencies: ["031"]
---

# Remove deprecated get_data_model from WorkflowHost

## Problem Statement

`WorkflowHost.get_data_model()` is marked deprecated ("use get_session") but is still used by the runner for `ThresholdQCQueueEntry`. The launcher implements both `get_session()` and `get_data_model()`.

## Findings

- `workflows/host.py` line 46: deprecated method in protocol
- `interfaces/gui/main_window.py` line 1191: deprecated impl
- `gui/workflows/single_cell/runner.py` line 671: caller

## Proposed Solution

Update `ThresholdQCQueueEntry` to accept Session instead of data_model. The controller needs `set_measurements()` which is on Session. Remove `get_data_model()` from protocol and launcher.
- Effort: Small
- Risk: Low — test fakes use MagicMock(spec=WorkflowHost), will auto-adjust
