"""WorkflowHost — the narrow protocol a runner uses to talk to the launcher.

The runner depends on this protocol, not on ``LauncherWindow`` directly, so
that:

1. ``LauncherWindow`` does not grow a wide public API surface.
2. Unit tests can drive the runner with a ``FakeHost`` that implements only
   these six methods.
3. Future launchers / alternative hosts can plug in without touching the
   runner.

``LauncherWindow`` conforms structurally — no base class, no inheritance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from percell4.application.session import Session
    from percell4.gui.viewer import ViewerWindow


@runtime_checkable
class WorkflowHost(Protocol):
    """Methods a batch workflow runner needs from its host application.

    The host provides UI-level services (locking, window management,
    status display) and access to the shared Session and viewer. Domain
    operations go through use cases; the host is purely the GUI adapter.
    """

    def set_workflow_locked(self, locked: bool) -> None:
        """Disable (or re-enable) all main-UI actions while a workflow runs."""

    def show_workflow_status(self, phase_name: str, sub_progress: str) -> None:
        """Display a one-line status string while the runner executes."""

    def get_viewer_window(self) -> ViewerWindow:
        """Return the host's shared napari viewer window."""

    def get_session(self) -> Session:
        """Return the host's shared application Session."""

    def get_data_model(self) -> Any:
        """Return the host's CellDataModel (deprecated — use get_session).

        Kept for backward compatibility with ThresholdQCController and
        SegmentationQCController which need the Qt signal bridge.
        """

    def close_child_windows(self) -> None:
        """Close ancillary top-level windows (cell table, data plot, phasor).

        The host remembers which windows were open and restores them on
        :meth:`restore_child_windows`.
        """

    def restore_child_windows(self) -> None:
        """Reopen the ancillary windows that were closed for the workflow."""
