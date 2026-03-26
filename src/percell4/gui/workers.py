"""Background workers for long-running tasks.

All heavy computation (Cellpose, thresholding, phasor) runs in a Worker QThread.
Workers operate on numpy arrays only — never touch GUI or HDF5 from the worker thread.
Results are emitted via signals back to the main thread.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qtpy.QtCore import QThread, Signal


class Worker(QThread):
    """Generic background worker that runs a callable in a separate thread.

    Usage::

        worker = Worker(run_cellpose, image, model_type="cyto3")
        worker.progress.connect(status_bar.showMessage)
        worker.finished.connect(on_result)
        worker.error.connect(on_error)
        worker.start()

    The caller MUST hold a reference to the Worker (e.g., as self._worker)
    to prevent garbage collection while the thread is running.
    """

    finished = Signal(object)  # emits the return value of the callable
    progress = Signal(str)  # status messages
    error = Signal(str)  # error description

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._aborted = False

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
            if not self._aborted:
                self.finished.emit(result)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")

    def request_abort(self) -> None:
        """Request cancellation. The worker checks this flag after completion."""
        self._aborted = True

    @property
    def aborted(self) -> bool:
        return self._aborted
