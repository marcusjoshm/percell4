"""QProcess-backed runner for the Batch Tools Console.

Runs one batch-tool argv as a child process, streaming merged stdout/stderr
live on the main thread — QProcess emits its signals on the event loop, so
there is no manual threading and no cross-thread GUI writes (the class of bug
documented in ``docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md``).

Output bytes are decoded through a *stateful* incremental UTF-8 decoder, so a
multibyte glyph (e.g. the ``█`` progress-bar block) split across QProcess read
boundaries is not corrupted into replacement characters.

Cancellation reaps the whole process group: the child is launched as its own
session leader (``os.setsid`` via the Qt child-process modifier where the
binding supports it), so torch/Cellpose worker grandchildren are signalled
too rather than orphaned.
"""

from __future__ import annotations

import codecs
import os
import signal

from qtpy.QtCore import QObject, QProcess, QTimer, Signal


class BatchCommandRunner(QObject):
    """Run a batch-tool argv, streaming decoded output; cancellable."""

    started = Signal()
    output = Signal(str)
    finished = Signal(int)  # child exit code
    cancelled = Signal()  # emitted when the user cancels, before ``finished``

    _KILL_GRACE_MS = 3000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._decoder: codecs.IncrementalDecoder | None = None
        self._pgid: int | None = None

    @property
    def is_running(self) -> bool:
        return (
            self._proc is not None
            and self._proc.state() != QProcess.ProcessState.NotRunning
        )

    def run(self, argv: list[str], cwd: str | None = None) -> None:
        """Start ``argv`` (``[program, *args]``) in ``cwd`` (default: inherit)."""
        if self.is_running or not argv:
            return
        program, *args = argv
        self._pgid = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

        proc = QProcess(self)
        self._proc = proc
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if cwd:
            proc.setWorkingDirectory(cwd)
        proc.setProgram(program)
        proc.setArguments(args)
        # Launch the child in its own session/process group so cancel can
        # signal the whole tree. Available on Qt6 bindings; harmless no-op
        # where the modifier API is absent (we verify success in _on_started
        # before ever using killpg — see there).
        set_modifier = getattr(proc, "setChildProcessModifier", None)
        if set_modifier is not None:
            set_modifier(lambda: os.setsid())

        proc.readyReadStandardOutput.connect(self._on_ready_read)
        proc.started.connect(self._on_started)
        proc.finished.connect(self._on_finished)
        proc.start()

    def cancel(self) -> None:
        """Terminate the running command (process group), then hard-kill."""
        if not self.is_running:
            return
        self.cancelled.emit()
        self._signal_group(signal.SIGTERM)
        QTimer.singleShot(self._KILL_GRACE_MS, self._hard_kill)

    # ── QProcess slots ──────────────────────────────────────────────────

    def _on_started(self) -> None:
        proc = self._proc
        if proc is None:
            return
        # Close the child's stdin so a hypothetical prompt hits EOF and aborts
        # rather than hanging (catalog tools are non-interactive by assumption).
        proc.closeWriteChannel()
        # Only adopt a process group for killpg when we can PROVE the child is
        # its own group leader (setsid ran → getpgid == pid). Otherwise the
        # child shares our group, and killpg would signal the whole parent
        # process group (up to and including this app). In that case leave
        # _pgid None and fall back to terminating just the child.
        pid = int(proc.processId())
        if pid:
            try:
                if os.getpgid(pid) == pid:
                    self._pgid = pid
            except OSError:
                self._pgid = None
        self.started.emit()

    def _on_ready_read(self) -> None:
        if self._proc is None or self._decoder is None:
            return
        data = self._proc.readAllStandardOutput().data()
        if data:
            text = self._decoder.decode(data)
            if text:
                self.output.emit(text)

    def _on_finished(self, *args: object) -> None:
        # QProcess.finished emits (exitCode, exitStatus); tolerate either arity.
        exit_code = int(args[0]) if args else 0
        if self._decoder is not None:
            tail = self._decoder.decode(b"", final=True)
            if tail:
                self.output.emit(tail)
        self._proc = None
        self._decoder = None
        self._pgid = None
        self.finished.emit(exit_code)

    # ── process-group signalling ────────────────────────────────────────

    def _signal_group(self, sig: int) -> None:
        if self._pgid is not None:
            try:
                os.killpg(self._pgid, sig)
                return
            except OSError:
                pass
        proc = self._proc
        if proc is not None:
            if sig == signal.SIGKILL:
                proc.kill()
            else:
                proc.terminate()

    def _hard_kill(self) -> None:
        if self.is_running:
            self._signal_group(signal.SIGKILL)
