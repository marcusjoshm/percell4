"""Background workers for long-running tasks.

All heavy computation (Cellpose, thresholding, phasor) runs in a Worker QThread.
Workers operate on numpy arrays only — never touch GUI or HDF5 from the worker thread.
Results are emitted via signals back to the main thread.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from qtpy.QtCore import QThread, Signal

from percell4.workflows.diagnostics import WorkerError


class Worker(QThread):
    """Generic background worker that runs a callable in a separate thread.

    Usage::

        worker = Worker(run_cellpose, image, model_type="cyto3")
        worker.progress.connect(status_bar.showMessage)
        worker.finished.connect(on_result)
        worker.error.connect(on_error)  # receives WorkerError
        worker.start()

    The caller MUST hold a reference to the Worker (e.g., as self._worker)
    to prevent garbage collection while the thread is running.

    ``error`` emits a :class:`percell4.workflows.diagnostics.WorkerError` so
    callers have structured access to exception type, message, Windows
    ``winerror`` code, and the full traceback instead of a pre-stringified
    summary.
    """

    finished = Signal(object)  # emits the return value of the callable
    progress = Signal(str)  # status messages
    error = Signal(object)  # emits WorkerError

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
            self.error.emit(
                WorkerError(
                    exc_type=type(e).__name__,
                    message=str(e),
                    is_import_error=isinstance(e, (ImportError, OSError)),
                    winerror=getattr(e, "winerror", None),
                    traceback=traceback.format_exc(),
                )
            )

    def request_abort(self) -> None:
        """Request cancellation. The worker checks this flag after completion."""
        self._aborted = True

    @property
    def aborted(self) -> bool:
        return self._aborted


class BackgroundFrameFiller(QThread):
    """Decode a dataset's remaining timepoints into a resident buffer.

    Unlike :class:`Worker`, this thread *does* touch HDF5 — it is the
    deliberate exception to the "no HDF5 from the worker thread" rule, made
    safe by opening its **own** :class:`DatasetStore` session (h5py handles are
    not shareable across threads). It only writes into pre-allocated numpy
    slots of ``buffer`` and never touches Qt/napari; the main thread refreshes
    layers in response to the ``frame_filled`` signal.

    Cancellation is cooperative and checked between frames, so abort takes
    effect within one frame-decode (~1–2 s). The caller MUST hold a reference
    to the thread and should ``request_abort()`` + ``wait()`` before releasing
    the buffer.
    """

    frame_filled = Signal(int)  # timepoint index just decoded
    fill_finished = Signal()  # all requested frames done (or aborted)
    error = Signal(object)  # WorkerError

    def __init__(self, buffer: Any, path: str, frames: list[int]) -> None:
        super().__init__()
        self._buffer = buffer
        self._path = str(path)
        self._frames = list(frames)
        self._aborted = False

    def run(self) -> None:
        from percell4.store import DatasetStore

        try:
            store = DatasetStore(self._path)
            with store.open_read() as s:
                for t in self._frames:
                    if self._aborted:
                        break
                    self._buffer.fill_frame(t, store=s)
                    self.frame_filled.emit(t)
            self.fill_finished.emit()
        except Exception as e:  # pragma: no cover - defensive
            self.error.emit(
                WorkerError(
                    exc_type=type(e).__name__,
                    message=str(e),
                    is_import_error=isinstance(e, (ImportError, OSError)),
                    winerror=getattr(e, "winerror", None),
                    traceback=traceback.format_exc(),
                )
            )

    def request_abort(self) -> None:
        self._aborted = True

    @property
    def aborted(self) -> bool:
        return self._aborted
