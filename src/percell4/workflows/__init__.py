"""Qt-agnostic building blocks for batch analysis workflows.

This subpackage contains the pure-Python core used by batch workflow runners:
configuration dataclasses, run-folder I/O, channel intersection helpers, a
``WorkflowHost`` protocol, and a jsonl run-log helper. The Qt driver that
actually executes a workflow lives under ``percell4.gui.workflows``.

Rule: this subpackage must not import ``qtpy``, ``PyQt5``, ``napari``, or any
other GUI module. It is importable — and unit-testable — without a running
``QApplication``.
"""

from __future__ import annotations
