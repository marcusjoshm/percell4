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
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.gui.workflows.base_runner import PhaseResult
from percell4.segment.postprocess import (
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
    ) -> None:
        super().__init__()
        self._viewer_win = viewer_win
        self._entry = entry
        self._queue_index = queue_index
        self._queue_total = queue_total
        self._on_complete = on_complete
        self._channel_idx = channel_idx

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
            self._intensity = self._store.read_channel(
                "intensity", self._channel_idx
            )
            self._labels = self._store.read_labels("cellpose_qc")
        except Exception as e:
            logger.exception("seg QC failed to load dataset %s", self._entry.name)
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"failed to load {self._entry.name}: {e}",
                )
            )
            return

        if self._labels is None or int(self._labels.max()) == 0:
            # Cellpose returned no cells for this dataset. The runner
            # already recorded the failure; auto-accept and advance.
            self._finish(
                PhaseResult(
                    success=True,
                    message="no cells to QC (auto-accept)",
                )
            )
            return

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

        layout.addWidget(self._build_edit_group())
        layout.addWidget(self._build_cleanup_group())
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

    def _on_delete_selected(self) -> None:
        layer = self._labels_layer()
        if layer is None:
            return
        selected_id = int(getattr(layer, "selected_label", 0))
        if selected_id == 0:
            return
        data = np.asarray(layer.data).copy()
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
        data = np.asarray(layer.data, dtype=np.int32)
        new_data = relabel_sequential(data)
        layer.data = new_data
        self._schedule_refresh()

    def _on_cleanup_preview(self) -> None:
        layer = self._labels_layer()
        if layer is None or self._cleanup_status_label is None:
            return
        data = np.asarray(layer.data, dtype=np.int32)
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

        removed_mask = (data > 0) & (filtered == 0)
        highlight = removed_mask.astype(np.int32)
        viewer.add_labels(
            highlight,
            name=_LAYER_CLEANUP_PREVIEW,
            opacity=0.6,
            blending="translucent",
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
        data = np.asarray(layer.data, dtype=np.int32)
        margin = self._cleanup_margin.value() if self._cleanup_margin else 0
        min_area = self._cleanup_min_area.value() if self._cleanup_min_area else 0

        filtered = data
        if margin >= 0:
            filtered, _ = filter_edge_cells(filtered, edge_margin=margin)
        if min_area > 0:
            filtered, _ = filter_small_cells(filtered, min_area=min_area)
        filtered = relabel_sequential(filtered)

        layer.data = filtered
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
            self._store.write_labels("cellpose_qc", final_labels)
        except Exception as e:
            logger.exception("seg QC write_labels failed for %s", self._entry.name)
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"write /labels/cellpose_qc failed: {e}",
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
