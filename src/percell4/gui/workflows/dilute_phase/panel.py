"""DilutePhaseMaskPanel — the Qt widget driving the dilute-phase workflow.

Hosts two stacked blocks:

* **Setup block** (visible at start, frozen once Round 1 begins):
  read-out fields (active dataset / channel / segmentation), mask-name
  ``QLineEdit``, dilation-radius ``QSpinBox``, the shared
  :class:`GroupedThresholdSettingsWidget` (U4), and the **Start** button.
* **Iteration block** (hidden until Start, then visible while the round
  loop runs): a round-counter status label and three buttons —
  **Run another round**, **Done — Save dilute phase mask**, **Cancel**.

The panel drives a :class:`DilutePhaseMaskController` (U6). It performs
all up-front validation locally — three-namespace name uniqueness
(masks, labels, channels), active-channel / active-segmentation
presence, and a successful read of the active segmentation labels —
so the controller can assume its inputs are well-formed once Start
fires.

The Done button uses the established Accept-button styling (green
background + bold weight) so the irreversible final-write action
visually pops.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.application.session import Event
from percell4.gui import theme
from percell4.gui._grouped_threshold_settings import GroupedThresholdSettingsWidget
from percell4.gui.settings import app_settings
from percell4.gui.workflows.dilute_phase.controller import (
    DilutePhaseMaskController,
)

if TYPE_CHECKING:
    from percell4.application.session import Session
    from percell4.domain.dataset import DatasetHandle
    from percell4.gui.viewer import ViewerWindow
    from percell4.model import CellDataModel
    from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


_DEFAULT_MASK_NAME = "dilute_phase"
_DEFAULT_DILATION_RADIUS = 5
_MIN_DILATION_RADIUS = 0
_MAX_DILATION_RADIUS = 50

_DILATION_TOOLTIP = (
    "Pixels to grow each round's condensed mask before subtracting"
    " from the working buffer"
)

# Geometry persistence — same QSettings org / app namespace as every
# other persisted-geometry window in the codebase (SessionWindow,
# PhasorPlot, ThresholdQC, ...). Distinct key so moving the dilute
# panel doesn't reposition the QC windows or vice versa.
_GEOMETRY_KEY = "dilute_phase_panel/geometry"


class DilutePhaseMaskPanel(QWidget):
    """Setup + iteration UI for the dilute-phase mask generation workflow."""

    # Re-emitted from the inner controller so the launcher can wire
    # workflow_locked release without reaching into _controller.
    workflow_done = Signal()
    workflow_cancelled = Signal()
    workflow_error = Signal(str)

    def __init__(
        self,
        parent: QWidget | None,
        store: DatasetStore,
        data_model: CellDataModel,
        session: Session,
        viewer_win: ViewerWindow,
        get_active_seg_labels: Callable[[], NDArray[np.int32] | None],
        handle: DatasetHandle,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._data_model = data_model
        self._session = session
        self._viewer_win = viewer_win
        self._get_active_seg_labels = get_active_seg_labels
        self._handle = handle

        # Becomes the live U6 controller once Start fires.
        self._controller: DilutePhaseMaskController | None = None

        # Flag for one-shot geometry restore in showEvent. The launcher
        # calls panel.resize(520, 700) BEFORE show(), so restoring in
        # __init__ would be overwritten by that resize. Restoring on
        # first show overrides it when a saved geometry exists.
        self._geometry_restored = False

        self._build_ui()
        self._wire_signals()
        self._subscribe_session_events()
        self._refresh_readout_labels()
        self._revalidate()

    # ── UI construction ─────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(10)
        outer.setAlignment(Qt.AlignTop)

        # ── Setup block ─────────────────────────────────────────
        self._setup_block = QFrame()
        setup_layout = QVBoxLayout(self._setup_block)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(8)

        # Read-out fields. Disabled QLabels (not editable; we just want
        # the user to see what they're working on).
        self._dataset_label = QLabel("Dataset: —")
        self._channel_label = QLabel("Channel: (none)")
        self._segmentation_label = QLabel("Segmentation: (none)")
        for lbl in (
            self._dataset_label, self._channel_label, self._segmentation_label,
        ):
            lbl.setStyleSheet(f"color: {theme.TEXT_LABEL};")
            setup_layout.addWidget(lbl)

        # Mask name lineedit.
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Mask name:"))
        self._name_edit = QLineEdit(_DEFAULT_MASK_NAME)
        name_row.addWidget(self._name_edit)
        setup_layout.addLayout(name_row)

        # Dilation radius spinbox.
        dil_row = QHBoxLayout()
        dil_label = QLabel("Dilation radius (px):")
        dil_label.setToolTip(_DILATION_TOOLTIP)
        dil_row.addWidget(dil_label)
        self._dilation_spin = QSpinBox()
        self._dilation_spin.setRange(
            _MIN_DILATION_RADIUS, _MAX_DILATION_RADIUS,
        )
        self._dilation_spin.setValue(_DEFAULT_DILATION_RADIUS)
        self._dilation_spin.setToolTip(_DILATION_TOOLTIP)
        dil_row.addWidget(self._dilation_spin)
        setup_layout.addLayout(dil_row)

        # Shared U4 settings widget — metric / algo / GMM-or-K-means / σ.
        self._settings_widget = GroupedThresholdSettingsWidget(self._setup_block)
        setup_layout.addWidget(self._settings_widget)

        # Start button.
        self._start_btn = QPushButton("Start")
        self._start_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN};"
            f" color: white; padding: 8px; font-weight: bold;"
            f" border-radius: 4px; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
            f" QPushButton:disabled {{ background-color: {theme.SURFACE};"
            f" color: {theme.TEXT_MUTED}; }}"
        )
        setup_layout.addWidget(self._start_btn)

        # Inline validation status.
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {theme.WARNING};")
        setup_layout.addWidget(self._status_label)

        outer.addWidget(self._setup_block)

        # ── Iteration block (initially hidden) ──────────────────
        self._iteration_block = QFrame()
        iter_layout = QVBoxLayout(self._iteration_block)
        iter_layout.setContentsMargins(0, 0, 0, 0)
        iter_layout.setSpacing(8)

        self._round_label = QLabel("Round 0 — ready")
        self._round_label.setStyleSheet(
            f"color: {theme.TEXT_BRIGHT}; font-size: 14pt; font-weight: bold;"
        )
        iter_layout.addWidget(self._round_label)

        self._run_btn = QPushButton("Run another round")
        iter_layout.addWidget(self._run_btn)

        self._done_btn = QPushButton("Done — Save dilute phase mask")
        # Accept-button convention: green background + bold weight (mirrors
        # the Accept button in threshold_qc.py:542-545).
        self._done_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN};"
            f" color: white; padding: 6px; font-weight: bold; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
        )
        iter_layout.addWidget(self._done_btn)

        self._cancel_btn = QPushButton("Cancel")
        iter_layout.addWidget(self._cancel_btn)

        outer.addWidget(self._iteration_block)
        self._iteration_block.setVisible(False)

        outer.addStretch()

    # ── Signal wiring ───────────────────────────────────────────

    def _wire_signals(self) -> None:
        """Wire setup-block edits to ``_revalidate`` and buttons to slots.

        Every user-edit signal on the setup-block widgets feeds the single
        ``_revalidate()`` slot per
        ``docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md``.
        """
        # Setup block — drives Start enable-state.
        self._name_edit.textChanged.connect(self._revalidate)
        self._dilation_spin.valueChanged.connect(self._revalidate)
        self._settings_widget.config_changed.connect(self._revalidate)
        self._start_btn.clicked.connect(self._on_start_clicked)

        # Iteration block.
        self._run_btn.clicked.connect(self._on_run_another_round_clicked)
        self._done_btn.clicked.connect(self._on_done_clicked)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

    def _subscribe_session_events(self) -> None:
        """Subscribe to session events that affect read-out labels + validation.

        Mirrors the pattern in
        ``src/percell4/interfaces/gui/peer_views/session_window.py:53-77``.
        """
        self._unsubs: list[Callable[[], None]] = [
            self._session.subscribe(
                Event.ACTIVE_CHANNEL_CHANGED, self._on_session_active_changed,
            ),
            self._session.subscribe(
                Event.ACTIVE_SEGMENTATION_CHANGED,
                self._on_session_active_changed,
            ),
        ]

    def _on_session_active_changed(self) -> None:
        """Refresh read-out labels + re-validate when session selection changes.

        Only takes effect while the setup block is live; once Round 1 has
        started the read-outs are frozen (they no longer reflect the
        live session, which would be confusing).
        """
        if self._controller is not None:
            return
        self._refresh_readout_labels()
        self._revalidate()

    def _refresh_readout_labels(self) -> None:
        """Refresh the three disabled read-out labels from session + handle."""
        dataset_text = (
            str(self._handle.path.name)
            if self._handle is not None
            else "—"
        )
        self._dataset_label.setText(f"Dataset: {dataset_text}")
        self._channel_label.setText(
            f"Channel: {self._session.active_channel or '(none)'}"
        )
        self._segmentation_label.setText(
            f"Segmentation: "
            f"{self._session.active_segmentation or '(none)'}"
        )

    # ── Validation ──────────────────────────────────────────────

    def _channel_names(self) -> list[str]:
        """Best-effort list of channel names for collision checking."""
        if self._handle is not None:
            md = getattr(self._handle, "metadata", None) or {}
            names = md.get("channel_names")
            if names is not None:
                return list(names)
        ds = getattr(self._session, "dataset", None)
        if ds is not None:
            md = getattr(ds, "metadata", None) or {}
            return list(md.get("channel_names", []))
        return []

    def _revalidate(self) -> None:
        """Re-run the up-front validation pipeline and update Start state.

        Status label and Start enable-state are the only side effects.
        Failure messages mirror what the user can act on (which session
        field needs to be set, which namespace the name collides with).
        """
        # 1. Active channel must be present.
        if not self._session.active_channel:
            self._set_invalid(
                "Select an active channel in the Session window."
            )
            return

        # 2. Active segmentation must be present.
        if not self._session.active_segmentation:
            self._set_invalid(
                "Select an active segmentation in the Session window."
            )
            return

        # 3. The active segmentation's labels must be readable.
        try:
            seg = self._get_active_seg_labels()
        except Exception:  # pragma: no cover - defensive
            seg = None
        if seg is None:
            self._set_invalid(
                "Could not read the active segmentation's labels."
            )
            return

        # 4. Mask name: non-empty and unique across all three namespaces.
        name = self._name_edit.text().strip()
        if not name:
            self._set_invalid("Enter a mask name.")
            return

        existing_masks = self._safe_list(self._store, "list_masks")
        existing_labels = self._safe_list(self._store, "list_labels")
        existing_channels = self._channel_names()

        if name in existing_masks:
            self._set_invalid(
                f"Name '{name}' already exists as a mask."
                f" Try '{name}_2'."
            )
            return
        if name in existing_labels:
            self._set_invalid(
                f"Name '{name}' already exists as a label."
                f" Try '{name}_2'."
            )
            return
        if name in existing_channels:
            self._set_invalid(
                f"Name '{name}' already exists as a channel."
                f" Try '{name}_2'."
            )
            return

        # All clear.
        self._status_label.setText("")
        self._start_btn.setEnabled(True)

    @staticmethod
    def _safe_list(store, method_name: str) -> list[str]:
        """Best-effort wrapper around ``store.list_*`` (tolerates mocks)."""
        method = getattr(store, method_name, None)
        if method is None:
            return []
        try:
            return list(method())
        except Exception:  # pragma: no cover - defensive
            return []

    def _set_invalid(self, msg: str) -> None:
        """Render an invalid-state status message and disable Start."""
        self._status_label.setText(msg)
        self._start_btn.setEnabled(False)

    # ── Setup → Iteration transition ────────────────────────────

    def _capture_channel_image(self) -> NDArray[np.float32] | None:
        """Read the active channel's image from the viewer.

        Mirrors :class:`GroupedSegPanel`'s capture path: iterate
        ``viewer_win.viewer.layers``, find the Image-classed layer whose
        ``.name`` matches the active channel, and return its ``.data``
        coerced to ``float32``.

        Returns ``None`` when the channel layer can't be located (the
        panel surfaces this via the status label).
        """
        viewer = getattr(self._viewer_win, "existing_viewer", None)
        if viewer is None:
            return None
        channel = self._session.active_channel
        if not channel:
            return None
        for layer in viewer.layers:
            cls_name = layer.__class__.__name__
            if cls_name == "Image" and getattr(layer, "name", None) == channel:
                data = np.asarray(layer.data)
                return data.astype(np.float32, copy=True)
        return None

    def _on_start_clicked(self) -> None:
        """Capture inputs, build the controller, switch to the iteration block."""
        channel_image = self._capture_channel_image()
        if channel_image is None:
            self._set_invalid(
                f"Could not read channel '{self._session.active_channel}'"
                f" from the viewer."
            )
            return

        seg_labels = self._get_active_seg_labels()
        if seg_labels is None:
            self._set_invalid(
                "Could not read the active segmentation's labels."
            )
            return
        seg_labels = np.asarray(seg_labels, dtype=np.int32)

        # Time-lapse: the dilute controller is single-frame; operate on the
        # active timepoint (a (T,H,W) channel/labels stack is sliced to the
        # displayed frame). Full per-frame (T,H,W) dilute is deferred.
        if self._session.n_timepoints > 1:
            t = self._session.active_timepoint
            if channel_image.ndim == 3:
                channel_image = channel_image[t]
            if seg_labels.ndim == 3:
                seg_labels = seg_labels[t]

        locked_config = self._settings_widget.current_config()
        mask_name = self._name_edit.text().strip()
        dilation = int(self._dilation_spin.value())

        try:
            self._controller = DilutePhaseMaskController(
                viewer_win=self._viewer_win,
                data_model=self._data_model,
                store=self._store,
                session=self._session,
                channel_image=channel_image,
                seg_labels=seg_labels,
                locked_config=locked_config,
                dilation_radius_px=dilation,
                final_mask_name=mask_name,
                handle=self._handle,
                channel=self._session.active_channel or "",
            )
        except Exception as exc:
            logger.exception("Failed to construct DilutePhaseMaskController")
            self._set_invalid(f"Failed to start workflow: {exc}")
            return

        # Wire controller signals → panel slots.
        self._controller.round_complete.connect(self._on_round_complete)
        self._controller.workflow_done.connect(self._on_workflow_done)
        self._controller.workflow_cancelled.connect(
            self._on_workflow_cancelled,
        )
        self._controller.error.connect(self._on_error)

        # Lock the setup block (keep visible so the user sees what was
        # captured, but disable edits). Hide the Start button so there's
        # no ambiguity that it can no longer fire.
        self._name_edit.setEnabled(False)
        self._dilation_spin.setEnabled(False)
        self._settings_widget.set_enabled(False)
        self._start_btn.setVisible(False)
        self._status_label.setText("")

        # Reveal the iteration block.
        self._iteration_block.setVisible(True)
        self._update_round_label(0)

        # Kick off Round 1.
        self._controller.start_round()

    # ── Iteration-block slots ───────────────────────────────────

    def _on_run_another_round_clicked(self) -> None:
        if self._controller is None:
            return
        self._controller.start_round()

    def _on_done_clicked(self) -> None:
        if self._controller is None:
            return
        self._controller.finish()

    def _on_cancel_clicked(self) -> None:
        if self._controller is None:
            return
        self._controller.cancel()

    # ── Controller signal handlers ──────────────────────────────

    def _on_round_complete(self, round_n: int) -> None:
        self._update_round_label(round_n)

    def _on_workflow_done(self) -> None:
        self._round_label.setText(
            f"Saved '{self._name_edit.text().strip()}' — workflow complete."
        )
        self.workflow_done.emit()

    def _on_workflow_cancelled(self) -> None:
        self._round_label.setText("Cancelled.")
        self.workflow_cancelled.emit()

    def _on_error(self, msg: str) -> None:
        self._status_label.setText(msg)
        self.workflow_error.emit(msg)

    def showEvent(self, event) -> None:
        """Restore saved geometry on first show (after the launcher's
        baseline ``resize(520, 700)`` has run). Subsequent shows do
        nothing — Qt keeps the current geometry."""
        super().showEvent(event)
        if self._geometry_restored:
            return
        self._geometry_restored = True
        try:

            geom = app_settings().value(
                _GEOMETRY_KEY,
            )
            if geom:
                self.restoreGeometry(geom)
        except Exception:
            logger.exception(
                "dilute panel: failed to restore geometry"
            )

    def closeEvent(self, event) -> None:
        """Treat OS-level window close (the X button) as Cancel.

        Without this, the user could dismiss the panel window while a
        controller is still alive, leaving the launcher
        ``is_workflow_locked`` and the working buffer dangling. Routing
        through ``controller.cancel()`` runs ``_teardown`` (removes
        transient layers, drops state) and emits ``workflow_cancelled``,
        which the launcher uses to release the lock.

        Also persists window geometry so the next open lands at the
        user's last position.
        """
        # Save geometry BEFORE running the cancel chain — same
        # save-before-close discipline as the threshold_qc windows
        # (saveGeometry on an already-closing widget can return stale
        # data on some platforms).
        try:

            app_settings().setValue(
                _GEOMETRY_KEY, self.saveGeometry(),
            )
        except Exception:
            logger.exception(
                "dilute panel: failed to save geometry"
            )

        if self._controller is not None:
            try:
                self._controller.cancel()
            except Exception:
                logger.exception("dilute panel cancel-on-close raised")
        else:
            # Pre-Start close — nothing to clean up, but still emit so
            # the launcher releases the lock if it was acquired before
            # Start ran.
            self.workflow_cancelled.emit()
        super().closeEvent(event)

    # ── Helpers ─────────────────────────────────────────────────

    def _update_round_label(self, round_n: int) -> None:
        """Render the round-counter status line.

        Format per the U7 spec:
            ``Round N — condensed pixels removed: P% of in-cell area``

        where P is the fraction of in-cell pixels currently covered by
        the controller's cumulative-condensed union. Round 0 (panel just
        revealed, no rounds completed yet) shows ``"Round 0 — ready"``.
        """
        if round_n <= 0 or self._controller is None:
            self._round_label.setText("Round 0 — ready")
            return

        cumulative = self._controller._cumulative_condensed
        seg = self._controller._seg_labels
        in_cell = seg > 0
        in_cell_count = int(in_cell.sum())
        if in_cell_count == 0:
            pct = 0.0
        else:
            pct = float(cumulative[in_cell].mean() * 100.0)
        self._round_label.setText(
            f"Round {round_n} — condensed pixels removed: "
            f"{pct:.1f}% of in-cell area"
        )
