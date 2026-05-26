"""Interactive per-dataset segmentation QC controller.

Built after :class:`ThresholdQCController` rather than as a ``QDialog``,
per the pattern-consistency review: long-running interactive flows in
PerCell4 are ``QObject`` controllers that build their own ``QMainWindow``
on the shared :class:`ViewerWindow`, with a narrow tool dock on the side.

The controller is spawned by
:class:`SingleCellThresholdingRunner` as an ``INTERACTIVE``
``PhaseRequest`` per dataset: the runner builds the controller, calls
:meth:`start`, and the controller fires its ``on_complete`` callback
when the user clicks Accept & Next or Cancel run. The runner then
advances to the next dataset (or unwinds on Cancel).

Tool dock
---------

- Delete selected label — pick a cell in napari and delete it
- Draw new label — switch napari to polygon mode; new polygon becomes
  a new cell
- Edge-margin cleanup (preview + apply) — runs ``filter_edge_cells`` +
  ``filter_small_cells`` with live preview of what would be removed
- Nav bar: [Cancel run]  [Accept & Next →]  (index / total : name)
- Keyboard shortcuts: Ctrl+Enter → Accept, Esc → Cancel

On Accept, the current label array is written back to
``/labels/cellpose_qc`` in the dataset's h5 (overwriting the raw
Cellpose output). On Cancel, nothing is written and the runner unwinds.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
from qtpy.QtCore import QObject, Qt, QTimer
from qtpy.QtGui import QKeySequence, QShortcut
from qtpy.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.config import viewer_presets as vp
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.domain.segmentation.postprocess import (
    filter_edge_cells,
    filter_small_cells,
    relabel_sequential,
)
from percell4.store import DatasetStore
from percell4.workflows.models import WorkflowDatasetEntry

logger = logging.getLogger(__name__)


_LAYER_IMAGE = "_workflow_seg_qc_image"
_LAYER_LABELS = "_workflow_seg_qc_labels"
_LAYER_CLEANUP_PREVIEW = "_workflow_seg_qc_cleanup_preview"


class SegmentationQCController(QObject):
    """Per-dataset interactive segmentation QC.

    Reuses a single image + labels layer across datasets to avoid napari
    layer churn; at teardown, the helper layers are cleaned up and any
    previously-hidden viewer layers are restored.
    """

    def __init__(
        self,
        *,
        viewer_win,
        entry: WorkflowDatasetEntry,
        queue_index: int,
        queue_total: int,
        on_complete: Callable[[PhaseResult], None],
        channel_idx: int = 0,
        seg_name: str = "cellpose_qc",
        cellpose_settings=None,
        edge_mode=None,
        edge_margin_px: int = 0,
    ) -> None:
        super().__init__()
        self._viewer_win = viewer_win
        self._entry = entry
        self._queue_index = queue_index
        self._queue_total = queue_total
        self._on_complete = on_complete
        self._channel_idx = channel_idx
        self._seg_name = seg_name
        # CellposeSettings + edge_mode are used by the in-QC Re-run
        # group to seed defaults that match the workflow's main run.
        # Optional so existing test fixtures + the legacy non-runner
        # invocation path keep working with sensible CellposeSettings()
        # defaults.
        self._cellpose_settings = cellpose_settings
        self._edge_mode = edge_mode
        self._edge_margin_px = edge_margin_px

        # Loaded per-dataset state
        self._store: DatasetStore | None = None
        self._intensity: np.ndarray | None = None
        self._labels: np.ndarray | None = None

        # Workflow window (holds tool dock)
        self._window: QMainWindow | None = None
        self._nav_dataset_label: QLabel | None = None
        self._cleanup_status_label: QLabel | None = None
        self._cleanup_apply_btn: QPushButton | None = None
        self._cleanup_margin: QSpinBox | None = None
        self._cleanup_min_area: QSpinBox | None = None
        self._cleanup_min_area_dbl: QDoubleSpinBox | None = None  # placeholder

        # Visibility save/restore for napari layers that were open before
        # the QC window took over.
        self._hidden_layers: dict[str, bool] = {}

        # ── Re-run Cellpose group (U2) ─────────────────────────────
        # Widgets and worker state for the in-QC Cellpose re-run flow.
        # All None until the QC window is built.
        self._rerun_group: QGroupBox | None = None
        self._rerun_toggle_btn: QPushButton | None = None
        self._rerun_diameter: QSpinBox | None = None
        self._rerun_channel: QComboBox | None = None
        self._rerun_flow: QDoubleSpinBox | None = None
        self._rerun_cellprob: QDoubleSpinBox | None = None
        self._rerun_model: QComboBox | None = None
        self._rerun_min_size: QSpinBox | None = None
        self._rerun_button: QPushButton | None = None
        # Worker + progress dialog references; replaced on each Re-run
        # so a stale worker can't accidentally signal a torn-down
        # controller.
        self._rerun_worker = None
        self._rerun_progress: QProgressDialog | None = None
        # Cached Cellpose model reused across Re-runs to avoid the
        # multi-second construction cost on every iteration. Lazy:
        # built on the first Re-run if not already provided.
        self._cellpose_model_cached = None

        # Coalesced refresh (signal coalescing per
        # docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md)
        self._refresh_pending = False

        # Tracks whether on_complete has already been fired; guards
        # against double-fire (e.g. X-button + Cancel button racing).
        self._finished = False

    # ── Public API ────────────────────────────────────────────

    def start(self) -> None:
        """Load the dataset into the viewer and show the QC window."""
        # Clear the viewer subtitle from any prior phase (e.g. a stale
        # "Segmenting ..." message left by the Cellpose worker handler).
        try:
            self._viewer_win.set_subtitle(
                f"Seg QC — {self._entry.name}"
            )
        except Exception:
            pass

        try:
            self._store = DatasetStore(self._entry.h5_path)
            n_timepoints = int(self._store.metadata.get("n_timepoints", 1) or 1)
            if n_timepoints > 1:
                # Time-lapse: read the seg channel as a (T,H,W) stack via the
                # per-frame reader (read_channel raises on the 4D layout).
                from percell4.workflows.phases import (
                    _read_segmentation_channel_stack,
                )

                self._intensity = _read_segmentation_channel_stack(
                    self._store, self._channel_idx, n_timepoints
                )
            else:
                self._intensity = self._store.read_channel(
                    "intensity", self._channel_idx
                )
            self._labels = self._store.read_labels(self._seg_name)
        except Exception as e:
            logger.exception("seg QC failed to load dataset %s", self._entry.name)
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"failed to load {self._entry.name}: {e}",
                )
            )
            return

        if self._labels is None:
            # No /labels/<seg_name> on disk at all — this is an upstream
            # state error, not a recoverable empty-Cellpose result.
            # segment_one is supposed to persist labels (possibly empty)
            # before this controller is invoked.
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"no /labels/{self._seg_name} found on disk",
                )
            )
            return

        # Empty Cellpose results (labels.max() == 0) used to auto-skip
        # here. They now fall through to the normal load+show path so
        # the user can draw / re-run / modify channel inside the QC
        # window to recover.

        self._hide_existing_layers()
        self._load_into_viewer()
        self._build_window()
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    # ── UI construction ───────────────────────────────────────

    def _build_window(self) -> None:
        window = QMainWindow()
        window.setWindowTitle(
            f"Segmentation QC — {self._entry.name} "
            f"({self._queue_index + 1}/{self._queue_total})"
        )
        window.resize(320, 520)
        window.setWindowFlag(Qt.Window)

        # Tool dock — a single widget with all the edit controls.
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel(
            f"<b>Segmentation QC</b><br>"
            f"Dataset {self._queue_index + 1} of {self._queue_total}"
        )
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        self._nav_dataset_label = QLabel(self._entry.name)
        self._nav_dataset_label.setStyleSheet("color: #4ea8de; font-weight: bold;")
        layout.addWidget(self._nav_dataset_label)

        # Empty-Cellpose recovery hint — visible only when this dataset
        # entered QC with zero cells. The Re-run and Modify Channel
        # affordances mentioned here are added in U2/U3 of the plan.
        if self._labels is not None and int(self._labels.max()) == 0:
            hint = QLabel(
                "Cellpose found 0 cells on this dataset. "
                "Draw labels with the napari tools, re-run Cellpose with "
                "different settings, or modify the channel to recover."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #f0b020; font-style: italic;")
            layout.addWidget(hint)

        layout.addWidget(self._build_edit_group())
        layout.addWidget(self._build_cleanup_group())
        layout.addWidget(self._build_rerun_group())
        layout.addStretch()
        layout.addWidget(self._build_nav_bar())

        window.setCentralWidget(central)

        # Keyboard shortcuts on the window itself.
        accept_sc = QShortcut(QKeySequence("Ctrl+Return"), window)
        accept_sc.activated.connect(self._on_accept_clicked)
        accept_sc_enter = QShortcut(QKeySequence("Ctrl+Enter"), window)
        accept_sc_enter.activated.connect(self._on_accept_clicked)
        cancel_sc = QShortcut(QKeySequence("Esc"), window)
        cancel_sc.activated.connect(self._on_cancel_clicked)

        # Trap X-button close → cancel with confirmation.
        window.closeEvent = self._on_close_event  # type: ignore[assignment]

        self._window = window

    def _build_edit_group(self) -> QGroupBox:
        box = QGroupBox("Label Tools")
        layout = QVBoxLayout(box)

        btn_delete = QPushButton("Delete Selected Label")
        btn_delete.setToolTip(
            "Click a cell in the viewer, then press this to delete it."
        )
        btn_delete.clicked.connect(self._on_delete_selected)
        layout.addWidget(btn_delete)

        btn_draw = QPushButton("Draw New Label")
        btn_draw.setToolTip(
            "Switches napari into polygon mode — draw around a missed cell."
        )
        btn_draw.clicked.connect(self._on_draw_new_label)
        layout.addWidget(btn_draw)

        btn_relabel = QPushButton("Relabel Sequentially")
        btn_relabel.setToolTip(
            "After manual edits, compact the label IDs to 1..N."
        )
        btn_relabel.clicked.connect(self._on_relabel)
        layout.addWidget(btn_relabel)

        return box

    def _build_cleanup_group(self) -> QGroupBox:
        box = QGroupBox("Cleanup")
        layout = QVBoxLayout(box)

        margin_row = QHBoxLayout()
        margin_row.addWidget(QLabel("Edge margin:"))
        self._cleanup_margin = QSpinBox()
        self._cleanup_margin.setRange(0, 50)
        self._cleanup_margin.setValue(0)
        self._cleanup_margin.setToolTip("Cells touching within N pixels of the border are removed.")
        margin_row.addWidget(self._cleanup_margin)
        margin_row.addStretch()
        layout.addLayout(margin_row)

        min_row = QHBoxLayout()
        min_row.addWidget(QLabel("Min area (px):"))
        self._cleanup_min_area = QSpinBox()
        self._cleanup_min_area.setRange(0, 10000)
        self._cleanup_min_area.setValue(0)
        min_row.addWidget(self._cleanup_min_area)
        min_row.addStretch()
        layout.addLayout(min_row)

        btn_row = QHBoxLayout()
        btn_preview = QPushButton("Preview")
        btn_preview.clicked.connect(self._on_cleanup_preview)
        btn_row.addWidget(btn_preview)

        self._cleanup_apply_btn = QPushButton("Apply")
        self._cleanup_apply_btn.setEnabled(False)
        self._cleanup_apply_btn.clicked.connect(self._on_cleanup_apply)
        btn_row.addWidget(self._cleanup_apply_btn)
        layout.addLayout(btn_row)

        self._cleanup_status_label = QLabel("")
        self._cleanup_status_label.setStyleSheet("color: #888;")
        self._cleanup_status_label.setWordWrap(True)
        layout.addWidget(self._cleanup_status_label)

        return box

    def _build_rerun_group(self) -> QGroupBox:
        """Re-run Cellpose group (U2).

        Collapsible. Knobs are seeded from ``self._cellpose_settings``
        (the workflow's CellposeSettings) plus the dataset's channel
        names. Clicking Re-run spawns a Worker(QThread) — pattern
        mirrors ``gui/segmentation_panel.py:_on_run_cellpose``. On
        completion, replaces the in-QC labels layer (no merge, no
        store write — Accept still owns persistence).
        """
        from percell4.workflows.models import CellposeSettings

        cfg = self._cellpose_settings or CellposeSettings()

        # Plain (non-checkable) QGroupBox + an in-header toggle button.
        # QGroupBox.setCheckable(True) propagates its checked-state to
        # children's enabled-state, which fights with our worker-driven
        # button enable/disable cycle. Manual toggle keeps the
        # button.setEnabled lifecycle clean.
        box = QGroupBox("Re-run Cellpose")

        outer = QVBoxLayout(box)
        toggle_btn = QPushButton("▶ Show settings")
        toggle_btn.setStyleSheet("text-align: left;")
        toggle_btn.setCheckable(True)
        outer.addWidget(toggle_btn)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

        body.setVisible(False)

        def _on_toggle(checked: bool) -> None:
            body.setVisible(checked)
            toggle_btn.setText("▼ Hide settings" if checked else "▶ Show settings")

        toggle_btn.toggled.connect(_on_toggle)
        self._rerun_toggle_btn = toggle_btn

        # Channel — seeded from the controller's current channel_idx.
        ch_row = QHBoxLayout()
        ch_row.addWidget(QLabel("Channel:"))
        self._rerun_channel = QComboBox()
        ch_names = list(getattr(self._entry, "channel_names", []) or [])
        if not ch_names:
            # Fall back to whatever the store says when entry didn't
            # carry names (legacy single-cell test fixtures).
            try:
                ch_names = [str(n) for n in self._store.metadata.get("channel_names", [])]
            except Exception:
                ch_names = []
        if not ch_names:
            ch_names = [f"ch{i}" for i in range(max(1, self._channel_idx + 1))]
        self._rerun_channel.addItems(ch_names)
        if 0 <= self._channel_idx < len(ch_names):
            self._rerun_channel.setCurrentIndex(self._channel_idx)
        ch_row.addWidget(self._rerun_channel)
        ch_row.addStretch()
        body_layout.addLayout(ch_row)

        # Diameter
        diam_row = QHBoxLayout()
        diam_row.addWidget(QLabel("Diameter (px):"))
        self._rerun_diameter = QSpinBox()
        self._rerun_diameter.setRange(0, 2000)
        self._rerun_diameter.setSpecialValueText("Auto")
        self._rerun_diameter.setValue(int(cfg.diameter))
        diam_row.addWidget(self._rerun_diameter)
        diam_row.addStretch()
        body_layout.addLayout(diam_row)

        # Flow threshold
        flow_row = QHBoxLayout()
        flow_row.addWidget(QLabel("Flow threshold:"))
        self._rerun_flow = QDoubleSpinBox()
        self._rerun_flow.setRange(0.0, 3.0)
        self._rerun_flow.setSingleStep(0.05)
        self._rerun_flow.setDecimals(2)
        self._rerun_flow.setValue(float(cfg.flow_threshold))
        flow_row.addWidget(self._rerun_flow)
        flow_row.addStretch()
        body_layout.addLayout(flow_row)

        # Cellprob threshold
        cp_row = QHBoxLayout()
        cp_row.addWidget(QLabel("Cellprob threshold:"))
        self._rerun_cellprob = QDoubleSpinBox()
        self._rerun_cellprob.setRange(-6.0, 6.0)
        self._rerun_cellprob.setSingleStep(0.1)
        self._rerun_cellprob.setDecimals(2)
        self._rerun_cellprob.setValue(float(cfg.cellprob_threshold))
        cp_row.addWidget(self._rerun_cellprob)
        cp_row.addStretch()
        body_layout.addLayout(cp_row)

        # Model
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._rerun_model = QComboBox()
        self._rerun_model.addItems(["cpsam", "cyto3", "cyto2", "cyto", "nuclei"])
        idx = self._rerun_model.findText(cfg.model)
        if idx >= 0:
            self._rerun_model.setCurrentIndex(idx)
        model_row.addWidget(self._rerun_model)
        model_row.addStretch()
        body_layout.addLayout(model_row)

        # Min cell size
        min_row = QHBoxLayout()
        min_row.addWidget(QLabel("Min cell size (px):"))
        self._rerun_min_size = QSpinBox()
        self._rerun_min_size.setRange(0, 10000)
        self._rerun_min_size.setValue(int(cfg.min_size))
        min_row.addWidget(self._rerun_min_size)
        min_row.addStretch()
        body_layout.addLayout(min_row)

        # Re-run button
        self._rerun_button = QPushButton("▶ Re-run")
        self._rerun_button.setToolTip(
            "Re-runs Cellpose with these settings and REPLACES the current "
            "labels in the QC viewer. Accept still owns the on-disk write."
        )
        self._rerun_button.clicked.connect(self._on_rerun_clicked)
        body_layout.addWidget(self._rerun_button)

        self._rerun_group = box
        return box

    def _cellpose_input_image(self) -> np.ndarray:
        """Return the image to feed Cellpose for an in-QC Re-run.

        Today this returns the raw segmentation channel (or the
        current single-frame view for time-lapse). In U4 it switches
        to the napari channel layer's current ``.data``, which lets
        the Modify Channel group's clipped/stretched preview flow
        through naturally.
        """
        if self._intensity is None:
            raise RuntimeError("intensity not loaded; QC window not started")
        return np.asarray(self._intensity)

    def _on_rerun_clicked(self) -> None:
        if self._finished or self._rerun_button is None:
            return
        if self._intensity is None or self._store is None:
            return

        from percell4.adapters.cellpose import (
            build_cellpose_model,
            run_cellpose,
        )
        from percell4.gui.workers import Worker

        # Snapshot the in-flight kwargs so a mid-flight UI tweak can't
        # corrupt the in-flight Cellpose call. The user can edit and
        # press Re-run again — that spawns a new worker.
        diameter = self._rerun_diameter.value()
        diameter_arg = diameter if diameter > 0 else None
        flow = float(self._rerun_flow.value())
        cellprob = float(self._rerun_cellprob.value())
        model_type = self._rerun_model.currentText()
        min_size = int(self._rerun_min_size.value())
        # Channel switch: if the user picked a different channel here
        # than the one originally loaded, re-read from the store.
        new_ch_idx = self._rerun_channel.currentIndex()
        if new_ch_idx != self._channel_idx and new_ch_idx >= 0:
            try:
                channel_image = self._store.read_channel("intensity", new_ch_idx)
            except (KeyError, IndexError, ValueError) as e:
                self._set_rerun_status(f"channel read failed: {e}")
                return
        else:
            channel_image = self._cellpose_input_image()

        gpu = bool(self._cellpose_settings.gpu) if self._cellpose_settings else True

        # Lazy-build the Cellpose model on first Re-run and reuse it.
        if self._cellpose_model_cached is None:
            try:
                self._cellpose_model_cached = build_cellpose_model(
                    model_type=model_type, gpu=gpu,
                )
            except Exception as e:
                logger.exception("build_cellpose_model failed in QC Re-run")
                self._set_rerun_status(f"model build failed: {e}")
                return

        self._rerun_button.setEnabled(False)
        progress = QProgressDialog(
            "Running Cellpose…", "Cancel", 0, 0, self._window,
        )
        progress.setWindowTitle("Re-run Cellpose")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        self._rerun_progress = progress

        worker = Worker(
            run_cellpose,
            channel_image,
            model_type=model_type,
            diameter=diameter_arg,
            gpu=gpu,
            flow_threshold=flow,
            cellprob_threshold=cellprob,
            min_size=min_size,
            model=self._cellpose_model_cached,
        )
        worker.finished.connect(self._on_rerun_finished)
        worker.error.connect(self._on_rerun_error)
        # If the user cancels via the progress dialog, just hide it —
        # Cellpose itself won't honor a cancel and forcibly killing
        # the QThread risks corrupting torch state. The worker will
        # finish naturally and its result will simply be ignored if
        # we're torn down by then.
        progress.canceled.connect(lambda: progress.close())
        self._rerun_worker = worker
        # On channel switch, remember the new active idx so Accept
        # persists labels against the right channel association.
        if new_ch_idx >= 0:
            self._channel_idx = new_ch_idx
        worker.start()

    def _on_rerun_finished(self, new_labels) -> None:
        if self._finished:
            return
        try:
            if self._rerun_progress is not None:
                try:
                    self._rerun_progress.close()
                except Exception:
                    pass
                self._rerun_progress = None

            labels_arr = np.asarray(new_labels, dtype=np.int32)

            # Replace the in-QC labels layer (always replace, never merge).
            try:
                layer = self._labels_layer()
                if layer is not None:
                    layer.data = labels_arr
                self._labels = labels_arr
            except Exception as e:
                logger.exception("Re-run finished but label replace failed")
                self._set_rerun_status(f"replace failed: {e}")
            else:
                n_cells = int(labels_arr.max())
                self._set_rerun_status(
                    f"Re-run found {n_cells} cells (replaced in viewer)"
                )
        finally:
            # Button re-enable must always happen so a stuck slot
            # doesn't leave the UI permanently locked. Same shape as
            # _on_rerun_error's recovery path.
            if self._rerun_button is not None:
                self._rerun_button.setEnabled(True)

    def _on_rerun_error(self, err) -> None:
        if self._finished:
            return
        if self._rerun_progress is not None:
            try:
                self._rerun_progress.close()
            except Exception:
                pass
            self._rerun_progress = None
        msg = getattr(err, "message", None) or str(err)
        self._set_rerun_status(f"Re-run failed: {msg}")
        if self._rerun_button is not None:
            self._rerun_button.setEnabled(True)

    def _set_rerun_status(self, text: str) -> None:
        """Surface a Re-run status message via the cleanup status label
        (shared real estate to avoid a separate dock widget for this).
        Best-effort: silently drops the message if the label hasn't been
        built yet (e.g., during early start failures).
        """
        if self._cleanup_status_label is not None:
            self._cleanup_status_label.setText(text)

    def _build_nav_bar(self) -> QWidget:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)

        btn_cancel = QPushButton("✕ Cancel run")
        btn_cancel.setToolTip("Cancel the running workflow (Esc)")
        btn_cancel.clicked.connect(self._on_cancel_clicked)
        row.addWidget(btn_cancel)

        row.addStretch()

        btn_accept = QPushButton("Accept && Next →")
        btn_accept.setDefault(True)
        btn_accept.setToolTip(
            "Persist the edited labels and advance to the next dataset (Ctrl+Enter)"
        )
        btn_accept.clicked.connect(self._on_accept_clicked)
        row.addWidget(btn_accept)

        return row_widget

    # ── Viewer load / unload ──────────────────────────────────

    def _hide_existing_layers(self) -> None:
        """Snapshot and hide existing viewer layers for the QC session.

        Anything the user had open before the workflow started stays
        resident in the viewer but invisible. On finish, we restore the
        original visibility.
        """
        try:
            viewer = self._viewer_win.viewer
        except Exception:
            return
        if viewer is None:
            return
        self._hidden_layers.clear()
        for layer in list(viewer.layers):
            if layer.name in (_LAYER_IMAGE, _LAYER_LABELS, _LAYER_CLEANUP_PREVIEW):
                continue
            self._hidden_layers[layer.name] = bool(getattr(layer, "visible", True))
            try:
                layer.visible = False
            except Exception:
                pass

    def _load_into_viewer(self) -> None:
        viewer = self._viewer_win.viewer
        if viewer is None:
            return

        # Reuse layers if possible to avoid napari layer churn.
        if _LAYER_IMAGE in viewer.layers:
            viewer.layers[_LAYER_IMAGE].data = self._intensity
        else:
            self._viewer_win.add_image(self._intensity, name=_LAYER_IMAGE)

        if _LAYER_LABELS in viewer.layers:
            layer = viewer.layers[_LAYER_LABELS]
            layer.data = self._labels.copy()
            layer.refresh()
        else:
            self._viewer_win.add_labels(
                self._labels.copy(), name=_LAYER_LABELS
            )

        self._select_labels_layer()
        self._viewer_win.show()

    def _select_labels_layer(self) -> None:
        viewer = self._viewer_win.viewer
        if viewer is None or _LAYER_LABELS not in viewer.layers:
            return
        layer = viewer.layers[_LAYER_LABELS]
        try:
            viewer.layers.selection.clear()
            viewer.layers.selection.add(layer)
        except Exception:
            pass

    def _labels_layer(self):
        viewer = self._viewer_win.viewer
        if viewer is None:
            return None
        if _LAYER_LABELS not in viewer.layers:
            return None
        return viewer.layers[_LAYER_LABELS]

    # ── Edit handlers ─────────────────────────────────────────

    def _current_timepoint(self) -> int:
        """Displayed timepoint (napari dims axis 0), or 0 if not time-lapse."""
        viewer = self._viewer_win.viewer
        if viewer is None:
            return 0
        dims = viewer.dims
        if dims.ndim >= 3 and len(dims.current_step) > 0:
            return int(dims.current_step[0])
        return 0

    def _on_delete_selected(self) -> None:
        layer = self._labels_layer()
        if layer is None:
            return
        selected_id = int(getattr(layer, "selected_label", 0))
        if selected_id == 0:
            return
        data = np.asarray(layer.data).copy()
        # Time-lapse: delete only within the displayed timepoint (the same
        # label id is a different physical cell in other frames).
        if data.ndim == 3:
            t = max(0, min(self._current_timepoint(), data.shape[0] - 1))
            frame = data[t]
            frame[frame == selected_id] = 0
        else:
            data[data == selected_id] = 0
        layer.data = data
        layer.selected_label = 0
        self._schedule_refresh()

    def _on_draw_new_label(self) -> None:
        layer = self._labels_layer()
        if layer is None:
            return
        self._select_labels_layer()
        next_id = int(np.asarray(layer.data).max()) + 1
        layer.selected_label = next_id

        def _activate_polygon():
            try:
                layer.mode = "polygon"
            except Exception:
                pass

        QTimer.singleShot(100, _activate_polygon)

    def _on_relabel(self) -> None:
        layer = self._labels_layer()
        if layer is None:
            return
        data = np.asarray(layer.data, dtype=np.int32).copy()
        # Time-lapse: renumber only the displayed frame (seg-QC runs before
        # tracking, so per-frame raw ids are independent anyway).
        if data.ndim == 3:
            t = max(0, min(self._current_timepoint(), data.shape[0] - 1))
            data[t] = relabel_sequential(data[t])
        else:
            data = relabel_sequential(data)
        layer.data = data
        self._schedule_refresh()

    def _on_cleanup_preview(self) -> None:
        layer = self._labels_layer()
        if layer is None or self._cleanup_status_label is None:
            return
        full = np.asarray(layer.data, dtype=np.int32)
        # Time-lapse: preview removals only in the displayed frame.
        tp = None
        if full.ndim == 3:
            tp = max(0, min(self._current_timepoint(), full.shape[0] - 1))
            data = full[tp]
        else:
            data = full
        margin = self._cleanup_margin.value() if self._cleanup_margin else 0
        min_area = self._cleanup_min_area.value() if self._cleanup_min_area else 0

        filtered = data
        edge_removed = 0
        small_removed = 0
        if margin >= 0:
            filtered, edge_removed = filter_edge_cells(filtered, edge_margin=margin)
        if min_area > 0:
            filtered, small_removed = filter_small_cells(filtered, min_area=min_area)

        total = edge_removed + small_removed

        viewer = self._viewer_win.viewer
        if viewer is None:
            return

        # Remove stale preview layer.
        for existing in list(viewer.layers):
            if existing.name == _LAYER_CLEANUP_PREVIEW:
                viewer.layers.remove(existing)
                break

        if total == 0:
            self._cleanup_status_label.setText("Nothing to remove at these settings.")
            if self._cleanup_apply_btn is not None:
                self._cleanup_apply_btn.setEnabled(False)
            return

        removed_2d = ((data > 0) & (filtered == 0)).astype(np.int32)
        # Build a slider-aligned overlay; only the displayed frame is marked.
        if tp is not None:
            highlight = np.zeros(full.shape, dtype=np.int32)
            highlight[tp] = removed_2d
        else:
            highlight = removed_2d
        viewer.add_labels(
            highlight,
            name=_LAYER_CLEANUP_PREVIEW,
            opacity=vp.GROUPED_SEG_CLEANUP_PREVIEW_OPACITY,
            blending=vp.LABELS_OVERLAY_DEFAULT_BLENDING,
        )
        parts = []
        if edge_removed:
            parts.append(f"{edge_removed} edge")
        if small_removed:
            parts.append(f"{small_removed} small")
        self._cleanup_status_label.setText(
            f"{total} cells to remove ({', '.join(parts)})"
        )
        if self._cleanup_apply_btn is not None:
            self._cleanup_apply_btn.setEnabled(True)

    def _on_cleanup_apply(self) -> None:
        layer = self._labels_layer()
        if layer is None:
            return
        full = np.asarray(layer.data, dtype=np.int32).copy()
        # Time-lapse: clean up only the displayed frame.
        tp = None
        if full.ndim == 3:
            tp = max(0, min(self._current_timepoint(), full.shape[0] - 1))
            data = full[tp]
        else:
            data = full
        margin = self._cleanup_margin.value() if self._cleanup_margin else 0
        min_area = self._cleanup_min_area.value() if self._cleanup_min_area else 0

        filtered = data
        if margin >= 0:
            filtered, _ = filter_edge_cells(filtered, edge_margin=margin)
        if min_area > 0:
            filtered, _ = filter_small_cells(filtered, min_area=min_area)
        filtered = relabel_sequential(filtered)

        if tp is not None:
            full[tp] = filtered
            out_data = full
        else:
            out_data = filtered
        layer.data = out_data
        self._schedule_refresh()

        # Clear the preview layer.
        viewer = self._viewer_win.viewer
        if viewer is not None:
            for existing in list(viewer.layers):
                if existing.name == _LAYER_CLEANUP_PREVIEW:
                    viewer.layers.remove(existing)
                    break

        if self._cleanup_status_label is not None:
            n_remaining = int(filtered.max())
            self._cleanup_status_label.setText(f"{n_remaining} cells remaining.")
        if self._cleanup_apply_btn is not None:
            self._cleanup_apply_btn.setEnabled(False)

    def _schedule_refresh(self) -> None:
        """Coalesce refresh requests via QTimer.singleShot(0, ...)."""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._do_refresh)

    def _do_refresh(self) -> None:
        self._refresh_pending = False
        layer = self._labels_layer()
        if layer is not None:
            try:
                layer.refresh()
            except Exception:
                pass

    # ── Terminal actions ──────────────────────────────────────

    def _on_accept_clicked(self) -> None:
        if self._finished:
            return
        layer = self._labels_layer()
        if layer is None or self._store is None:
            self._finish(
                PhaseResult(
                    success=False,
                    message="seg QC: no labels layer to persist",
                )
            )
            return

        # Persist the edited labels back to the h5.
        try:
            final_labels = np.asarray(layer.data, dtype=np.int32)
            if int(final_labels.max()) == 0:
                # User deleted every label. Treat as auto-skip (the
                # runner will later mark this dataset as having no cells).
                answer = QMessageBox.question(
                    self._window,
                    "No cells left",
                    "You have deleted every label in this dataset. "
                    "Accept anyway? The dataset will have no cells in "
                    "the output and will be skipped by downstream phases.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
            self._store.write_labels(self._seg_name, final_labels)
        except Exception as e:
            logger.exception("seg QC write_labels failed for %s", self._entry.name)
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"write /labels/{self._seg_name} failed: {e}",
                )
            )
            return

        self._finish(
            PhaseResult(
                success=True,
                message=f"accepted {int(final_labels.max())} cells",
            )
        )

    def _on_cancel_clicked(self) -> None:
        if self._finished:
            return
        answer = QMessageBox.question(
            self._window,
            "Cancel workflow run?",
            "Cancel the running workflow? Any labels, masks, and "
            "per-dataset data already written to the h5 files will "
            "remain; the final run-folder artifacts will not be created.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._finish(
            PhaseResult(success=False, message="user cancelled during seg QC")
        )

    def _on_close_event(self, event) -> None:
        """Trap the X button on the QC window — treat as cancel."""
        if self._finished:
            event.accept()
            return
        # Route through the regular cancel confirmation.
        self._on_cancel_clicked()
        if self._finished:
            event.accept()
        else:
            event.ignore()

    def _finish(self, result: PhaseResult) -> None:
        """Single exit point. Idempotent. Restores hidden layers and closes."""
        if self._finished:
            return
        self._finished = True

        # Clean up ALL workflow-specific layers (the seg QC image, the
        # labels layer, and the cleanup-preview layer). These MUST be
        # removed before the next phase (threshold QC) adds its own
        # layers; leaving them behind confuses the ThresholdQCController's
        # group-preview visualization.
        try:
            viewer = self._viewer_win.viewer
            if viewer is not None:
                for layer_name in (
                    _LAYER_IMAGE,
                    _LAYER_LABELS,
                    _LAYER_CLEANUP_PREVIEW,
                ):
                    if layer_name in viewer.layers:
                        viewer.layers.remove(layer_name)
        except Exception:
            pass

        # Drop Python references so numpy arrays can be freed.
        self._intensity = None
        self._labels = None

        # Restore any layers we hid on entry.
        try:
            viewer = self._viewer_win.viewer
            if viewer is not None:
                for name, was_visible in self._hidden_layers.items():
                    if name in viewer.layers:
                        viewer.layers[name].visible = was_visible
        except Exception:
            pass
        self._hidden_layers.clear()

        # Close the QC window; its close will no longer recurse because
        # self._finished is True.
        if self._window is not None:
            try:
                self._window.close()
            except Exception:
                pass
            self._window = None

        cb = self._on_complete
        self._on_complete = None  # drop the reference; single-fire
        if cb is not None:
            try:
                cb(result)
            except Exception:
                logger.exception("seg QC on_complete callback raised")
