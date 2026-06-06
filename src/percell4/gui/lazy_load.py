"""Lazy-first dataset display for the napari viewer.

Orchestrates the fast-load path: show timepoint 0 within ~1–2 s, then fill the
remaining timepoints into a resident buffer in the background so scrubbing
becomes instant once complete. Correctness never depends on the background
progress — navigating to a not-yet-filled frame decodes it synchronously on
demand (the resident buffer's per-frame lock makes this idempotent with the
filler).

This is the GUI-side coordinator (Qt + napari aware). The pure buffer + decode
logic lives in ``adapters/parallel_loader.py`` and the background thread in
``gui/workers.py``.
"""

from __future__ import annotations

from collections.abc import Callable

from percell4.adapters.parallel_loader import (
    LazyResidentBuffer,
    frame_contrast_limits,
    plan_resources,
)
from percell4.gui.workers import BackgroundFrameFiller


class LazyLoadController:
    """Owns the resident buffer, the background filler, and the dims hook.

    One instance is reused across loads; :meth:`load` tears down the previous
    dataset's buffer/worker/connection before building the next, giving a
    single invalidation point (also called on dataset close).
    """

    def __init__(self) -> None:
        self._buffer: LazyResidentBuffer | None = None
        self._worker: BackgroundFrameFiller | None = None
        self._viewer_win = None
        self._path: str | None = None
        self._status: Callable[[str], None] = lambda _msg: None
        self._dims_connected = False
        self._filled = 0
        self._total = 0

    @property
    def buffer(self) -> LazyResidentBuffer | None:
        return self._buffer

    # ── Lifecycle ─────────────────────────────────────────────

    def load(
        self,
        store,
        viewer_win,
        view_bin: int = 1,
        status_cb: Callable[[str], None] | None = None,
    ) -> None:
        """Populate ``viewer_win`` from ``store`` lazily.

        Adds eager (2D / non-time-lapse) layers in full, then for any
        time-stacked resources allocates a buffer, shows timepoint 0, and
        starts the background fill. Raises ``KeyError`` if the dataset has no
        ``/intensity`` (callers map this to a status message, as before).
        """
        self.teardown()
        self._viewer_win = viewer_win
        self._path = str(store.path)
        self._status = status_cb or (lambda _msg: None)

        n_timepoints, specs, eager = plan_resources(store, view_bin)

        viewer_win.clear()
        for layer in eager:
            self._add_layer(layer.kind, layer.name, layer.array, is_buffer=False)

        if not specs:
            return  # non-time-lapse: fully eager, nothing to stream

        self._buffer = LazyResidentBuffer(
            store.path, n_timepoints, specs, view_bin
        )
        self._buffer.fill_frame(0)  # ~1 s — makes the viewer usable immediately
        for spec in specs:
            self._add_layer(
                spec.kind,
                spec.layer_name,
                self._buffer.arrays[spec.layer_name],
                is_buffer=True,
            )

        self._connect_dims()
        self._start_background()

    def teardown(self) -> None:
        """Stop the filler, drop the dims hook, and free the buffer.

        Safe to call repeatedly and before any load. Waits for the worker so
        it never writes into a buffer we are about to release.
        """
        if self._worker is not None:
            try:
                self._worker.request_abort()
                self._worker.wait(5000)
            except Exception:  # pragma: no cover - defensive
                pass
            self._worker = None
        self._disconnect_dims()
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None
        self._filled = 0
        self._total = 0

    # ── Layer creation ────────────────────────────────────────

    def _add_layer(self, kind: str, name: str, arr, *, is_buffer: bool) -> None:
        vw = self._viewer_win
        if kind == "intensity":
            # Contrast from the visible plane (frame 0 for a buffer; the whole
            # 2D plane for an eager layer) so napari never scans the full stack
            # — and so a mostly-zero buffer doesn't skew the limits.
            plane = arr[0] if (is_buffer and arr.ndim == 3) else arr
            limits = frame_contrast_limits(plane)
            if limits is not None:
                vw.add_image(arr, name=name, contrast_limits=limits)
            else:
                vw.add_image(arr, name=name)
        elif kind == "labels":
            vw.add_labels(arr, name=name)
        elif kind == "mask":
            vw.add_mask(arr, name=name)

    # ── On-demand fill (correctness backstop) ─────────────────

    def ensure_frame_ready(self, t: int) -> None:
        """Decode timepoint ``t`` now if the background hasn't reached it.

        Synchronous on the caller's (main) thread; the buffer's per-frame lock
        coordinates with the filler so this never double-writes or tears.
        """
        buf = self._buffer
        if buf is None or not (0 <= t < buf.n_timepoints) or buf.is_ready(t):
            return
        buf.fill_frame(t)
        if self._viewer_win is not None:
            self._viewer_win.refresh_all_layers()

    def ensure_all_ready(self) -> None:
        """Decode every remaining timepoint now (blocking).

        Used before whole-stack interactive ops (e.g. Cellpose over a
        ``(T, H, W)`` channel) that read all frames at once. Coordinates with
        the background filler through the buffer's per-frame locks, so frames
        the filler already finished are skipped rather than re-decoded.
        """
        buf = self._buffer
        if buf is None:
            return
        for t in buf.pending_frames():
            buf.fill_frame(t)
        if self._viewer_win is not None:
            self._viewer_win.refresh_all_layers()

    def _on_dims_changed(self, event=None) -> None:
        if self._buffer is None or self._viewer_win is None:
            return
        self.ensure_frame_ready(self._viewer_win.current_timepoint())

    def _connect_dims(self) -> None:
        viewer = getattr(self._viewer_win, "_viewer", None)
        if viewer is None:
            return
        try:
            viewer.dims.events.current_step.connect(self._on_dims_changed)
            self._dims_connected = True
        except Exception:  # pragma: no cover - defensive
            self._dims_connected = False

    def _disconnect_dims(self) -> None:
        if not self._dims_connected:
            return
        viewer = getattr(self._viewer_win, "_viewer", None)
        if viewer is not None:
            try:
                viewer.dims.events.current_step.disconnect(self._on_dims_changed)
            except Exception:  # pragma: no cover - defensive
                pass
        self._dims_connected = False

    # ── Background fill ───────────────────────────────────────

    def _start_background(self) -> None:
        buf = self._buffer
        if buf is None:
            return
        pending = buf.pending_frames()
        self._total = buf.n_timepoints
        self._filled = self._total - len(pending)
        if not pending:
            self._status("Loaded")
            return
        self._worker = BackgroundFrameFiller(buf, self._path, pending)
        self._worker.frame_filled.connect(self._on_frame_filled)
        self._worker.fill_finished.connect(self._on_fill_finished)
        self._worker.error.connect(self._on_fill_error)
        self._worker.start()
        self._status(f"Ready — loading {len(pending)} more timepoints…")

    def _on_frame_filled(self, t: int) -> None:
        self._filled += 1
        # Repaint only if the user is currently looking at the just-filled frame.
        if self._viewer_win is not None and self._viewer_win.current_timepoint() == t:
            self._viewer_win.refresh_all_layers()
        if self._total:
            self._status(f"Loading timepoints… {self._filled}/{self._total}")

    def _on_fill_finished(self) -> None:
        if self._viewer_win is not None:
            self._viewer_win.refresh_all_layers()
        if self._buffer is not None and not self._buffer.pending_frames():
            self._status("Loaded (all timepoints resident)")

    def _on_fill_error(self, err) -> None:
        msg = getattr(err, "message", str(err))
        self._status(f"Background load error: {msg}")
