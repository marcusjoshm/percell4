"""Launcher/hub window — the main control center for PerCell4.

Sidebar with category buttons, stacked content area showing sub-options
for the selected category. Manages all other windows.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from datetime import UTC

from qtpy.QtCore import QSettings, Qt
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.config import viewer_presets as vp
from percell4.domain.io.layout import split_intensity_layers
from percell4.gui import theme
from percell4.model import CellDataModel


class LauncherWindow(QMainWindow):
    """Main hub window with sidebar navigation and content panels."""

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4")
        self.resize(700, 500)

        # Use cases (constructed lazily since viewer adapter depends on viewer window)
        from percell4.adapters.hdf5_store import Hdf5DatasetRepository

        self._repo = Hdf5DatasetRepository()
        self._use_cases_built = False

        # Window registry — all managed windows
        self._windows: dict[str, QWidget] = {}

        # Particle analysis results for export
        self._last_particle_df = None
        self._last_particle_detail_df = None

        # Batch workflow state (see set_workflow_locked / close_child_windows)
        self._workflow_locked: bool = False
        self._child_windows_to_restore: list[str] = []
        self._viewer_created_by_workflow: bool = False
        # Holds the currently-running workflow runner to prevent GC of
        # the QObject. Cleared in _on_workflow_event when the run finishes.
        self._active_workflow_runner = None
        # Holds the active dilute phase mask workflow panel (interactive
        # single-dataset workflow). Cleared in _on_dilute_workflow_finished.
        self._active_dilute_workflow_panel = None

        # Unified model state change handler
        self.data_model.state_changed.connect(self._on_state_changed)

        # Subscribe to napari-bound `M` keystrokes. The viewer's
        # `multi_select_requested` signal is connected lazily in
        # `_get_or_create_window`, but tests (and any other code path
        # that injects a ViewerWindow into `_windows` directly) bypass
        # that wiring — the module-level subscriber is the resilient
        # path.
        from percell4.gui.viewer import subscribe_multi_select_requested

        subscribe_multi_select_requested(self._on_multi_select)

        # Launcher-specific overrides (sidebar, menubar, statusbar)
        from percell4.gui import theme

        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {theme.BACKGROUND_DEEP}; }}
            QMenuBar {{
                background-color: {theme.SIDEBAR};
                color: {theme.TEXT};
            }}
            QMenuBar::item:selected {{ background-color: {theme.SIDEBAR_HOVER}; }}
            QMenu {{
                background-color: {theme.SIDEBAR};
                color: {theme.TEXT};
                border: 1px solid {theme.BORDER};
            }}
            QMenu::item:selected {{
                background-color: {theme.ACCENT};
                color: {theme.TEXT_BRIGHT};
            }}
            QStatusBar {{
                background-color: {theme.SIDEBAR_ACTIVE};
                color: #a0a0a0;
                border-top: 1px solid {theme.SURFACE};
            }}
        """)

        # Build UI
        self._create_menu_bar()
        self._create_central_widget()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        self._restore_geometry()

        # Always-visible Session window — canonical Selector for the three
        # active session fields. Sibling of the Launcher.
        from percell4.interfaces.gui.peer_views.session_window import SessionWindow

        session_win = SessionWindow(data_model=self.data_model)
        self._windows["session"] = session_win
        session_win.show()

    # ── Menu bar ──────────────────────────────────────────────

    def _create_menu_bar(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")

        open_project = QAction("&Open Project...", self)
        open_project.triggered.connect(self._on_open_project)
        file_menu.addAction(open_project)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        file_menu.addAction(quit_action)

        selection_menu = menu.addMenu("&Selection")

        self._multi_select_action = QAction("&Multi-select...", self)
        # `M` is bound via napari's `Labels.class_keymap` (in
        # `percell4.gui.viewer._on_m_keystroke`), not on the launcher
        # window — a window-scoped QAction shortcut would be shadowed by
        # napari's own `Labels` keymap.
        self._multi_select_action.setToolTip(
            "Click labels in the viewer to build a selection, then "
            "Ctrl+Return to commit (M)"
        )
        self._multi_select_action.triggered.connect(self._on_multi_select)
        selection_menu.addAction(self._multi_select_action)

    # ── Central widget: sidebar + stacked content ─────────────

    def _create_central_widget(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar — distinct dark blue-gray, white text
        sidebar = QWidget()
        sidebar.setFixedWidth(150)
        from percell4.gui import theme as _t

        sidebar.setStyleSheet(f"""
            QWidget {{ background-color: {_t.SIDEBAR}; }}
            QPushButton {{
                background-color: {_t.SIDEBAR};
                color: {_t.TEXT};
                border: none;
                padding: 14px 12px;
                text-align: left;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {_t.SIDEBAR_HOVER};
                color: {_t.TEXT_BRIGHT};
            }}
            QPushButton:checked {{
                background-color: {_t.SIDEBAR_ACTIVE};
                color: {_t.TEXT_BRIGHT};
                border-left: 3px solid {_t.ACCENT};
            }}
        """)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 8, 0, 0)
        sidebar_layout.setSpacing(0)

        # Content stack — inherits from global theme, just needs deep background
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet(
            f"QStackedWidget {{ background-color: {theme.BACKGROUND_DEEP}; }}"
        )

        # Create sidebar buttons and content panels
        categories = [
            ("I/O", self._create_io_panel),
            ("Viewer", self._create_viewer_panel),
            ("Segment", self._create_segment_panel),
            ("Analysis", self._create_analysis_panel),
            ("FLIM", self._create_flim_panel),
            ("Scripts", self._create_scripts_panel),
            ("Workflows", self._create_workflows_panel),
            ("Data", self._create_data_panel),
        ]

        self._sidebar_buttons: list[QPushButton] = []
        for i, (name, panel_factory) in enumerate(categories):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=i: self._on_sidebar_click(idx))
            sidebar_layout.addWidget(btn)
            self._sidebar_buttons.append(btn)

            panel = panel_factory()
            self._content_stack.addWidget(self._wrap_in_scroll(panel))

        sidebar_layout.addStretch()
        layout.addWidget(sidebar)
        layout.addWidget(self._content_stack, stretch=1)

        # Select first category
        self._on_sidebar_click(0)

    def _on_sidebar_click(self, index: int) -> None:
        """Switch content panel when sidebar button is clicked."""
        for i, btn in enumerate(self._sidebar_buttons):
            btn.setChecked(i == index)
        self._content_stack.setCurrentIndex(index)

    # ── Content panels ────────────────────────────────────────

    def _create_io_panel(self) -> QWidget:
        from percell4.interfaces.gui.task_panels.io_panel import IoPanel

        self._io_panel = IoPanel(
            on_import=self._on_import_dataset,
            on_load=self._on_load_dataset,
            on_add_layer=self._on_add_layer_to_dataset,
            on_batch_tcspc=self._on_batch_tcspc_append,
            on_close=self._on_close_dataset,
            on_export_csv=self._on_export_csv,
            on_export_images=self._on_export_images,
            on_export_phasor_npz=self._on_export_phasor_npz,
            show_status=lambda msg: self.statusBar().showMessage(msg),
        )
        return self._io_panel

    def _create_viewer_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("Viewer"))

        btn_open = QPushButton("Open Viewer")
        btn_open.clicked.connect(lambda: self._show_window("viewer"))
        layout.addWidget(btn_open)

        layout.addStretch()
        return panel

    def _create_segment_panel(self) -> QWidget:
        from percell4.gui.segmentation_panel import SegmentationPanel

        self._seg_panel = SegmentationPanel(
            self.data_model, launcher=self
        )
        return self._seg_panel

    def _create_analysis_panel(self) -> QWidget:
        from percell4.interfaces.gui.task_panels.analysis_panel import AnalysisPanel

        self._analysis_panel = AnalysisPanel(
            self.data_model,
            get_repo=lambda: self._repo,
            get_viewer_window=lambda: self._windows.get("viewer"),
            get_phasor_roi_names=self._get_phasor_roi_names,
            show_window=self._show_window,
            get_store=lambda: getattr(self, "_current_store", None),
            show_status=lambda msg: self.statusBar().showMessage(msg),
            repopulate_viewer=self._populate_viewer_from_store,
        )
        return self._analysis_panel

    def _create_flim_panel(self) -> QWidget:
        from percell4.interfaces.gui.task_panels.flim_panel import FlimPanel

        self._flim_panel = FlimPanel(
            self.data_model,
            get_repo=lambda: self._repo,
            get_viewer_window=lambda: self._windows.get("viewer"),
            get_phasor_window=lambda: self._windows.get("phasor_plot"),
            get_active_seg_labels=self._get_active_seg_labels,
            show_window=self._show_window,
            show_status=lambda msg: self.statusBar().showMessage(msg),
        )
        return self._flim_panel

    def _create_scripts_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("Scripts"))

        # Importing the analysis package fires every module's
        # @register_analysis side effect. Importing the dialog module
        # late-binds PerParticleDonut.dialog_class = PerParticleDonutDialog
        # so the click handler can read it generically below.
        from percell4.application.analysis import list_analyses
        from percell4.gui import (  # noqa: F401
            per_particle_donut_dialog,
            per_particle_multichannel_dialog,
            whole_field_intensity_dialog,
        )

        entries = list_analyses()
        if not entries:
            layout.addWidget(QLabel("No analyses registered."))
            layout.addStretch()
            return panel

        for info in entries:
            btn = QPushButton(info.display_name)
            if info.description:
                btn.setToolTip(info.description)
            btn.clicked.connect(
                lambda _checked=False, name=info.name: self._on_open_analysis(name)
            )
            layout.addWidget(btn)

        layout.addStretch()
        return panel

    def _on_open_analysis(self, analysis_name: str) -> None:
        """Open the dialog registered with ``analysis_name``.

        Reentrance-guarded against ``is_workflow_locked``. The analysis
        class declares its own ``dialog_class`` (set externally by the
        GUI-layer module). On accept, ``last_run_folder`` is read from the
        dialog and surfaced in the status bar.
        """
        if self.is_workflow_locked:
            self.statusBar().showMessage(
                "A workflow is already running — click Cancel to stop it first."
            )
            return

        from percell4.application.analysis import get as registry_get
        from percell4.gui import (  # noqa: F401
            per_particle_donut_dialog,
            per_particle_multichannel_dialog,
            whole_field_intensity_dialog,
        )

        try:
            cls = registry_get(analysis_name)
        except KeyError:
            self.statusBar().showMessage(
                f"Analysis {analysis_name!r} is not registered."
            )
            return
        if cls.dialog_class is None:
            self.statusBar().showMessage(
                f"{cls.display_name} has no dialog yet."
            )
            return

        dialog = cls.dialog_class(parent=self)
        try:
            dialog.exec_()
            run_folder = getattr(dialog, "last_run_folder", None)
            if run_folder is not None:
                self.statusBar().showMessage(
                    f"Analysis run complete — output at {run_folder}"
                )
        finally:
            dialog.deleteLater()

    def _create_workflows_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("Workflows"))

        self._btn_single_cell_workflow = QPushButton(
            "Single-cell thresholding analysis workflow"
        )
        self._btn_single_cell_workflow.setToolTip(
            "Batch workflow: Cellpose → seg QC → grouped thresholding → "
            "per-cell measurement → Parquet + CSV export. Opens a "
            "configuration dialog."
        )
        self._btn_single_cell_workflow.clicked.connect(
            self._on_open_single_cell_workflow
        )
        layout.addWidget(self._btn_single_cell_workflow)

        self._btn_dilute_phase_workflow = QPushButton(
            "Dilute phase mask generation"
        )
        self._btn_dilute_phase_workflow.setToolTip(
            "Interactive single-dataset workflow: iterate Grouped "
            "Threshold + dilation + NaN-subtract on the active channel; "
            "save one final mask of the in-cell dilute phase."
        )
        self._btn_dilute_phase_workflow.clicked.connect(
            self._on_open_dilute_phase_workflow
        )
        layout.addWidget(self._btn_dilute_phase_workflow)

        self._btn_flim_fret_workflow = QPushButton(
            "FLIM-FRET analysis"
        )
        self._btn_flim_fret_workflow.setToolTip(
            "Batch workflow: compare donor / donor+acceptor dataset pairs; "
            "compute mean lifetime within (mask ∩ phasor) and a FRET "
            "efficiency per pair (whole-field) or per cell (single-cell)."
        )
        self._btn_flim_fret_workflow.clicked.connect(
            self._on_open_flim_fret_workflow
        )
        layout.addWidget(self._btn_flim_fret_workflow)

        self._btn_phasor_masks_workflow = QPushButton(
            "Automated phasor-masks workflow"
        )
        self._btn_phasor_masks_workflow.setToolTip(
            "Batch workflow: fit a single-cluster GMM ellipse on phasor "
            "pixels above an intensity threshold, then apply it twice "
            "(permissive + conservative intensity thresholds) to produce "
            "two binary masks per channel, across N .h5 datasets."
        )
        self._btn_phasor_masks_workflow.clicked.connect(
            self._on_open_phasor_masks_workflow
        )
        layout.addWidget(self._btn_phasor_masks_workflow)

        layout.addStretch()
        return panel

    def _on_open_flim_fret_workflow(self) -> None:
        """Open the FLIM-FRET analysis dialog.

        Reentrance-guarded against ``is_workflow_locked``. The dialog
        drives its own ``QProgressDialog`` per-pair loop on the main
        thread (no ``BaseWorkflowRunner``), writes a single combined CSV
        atomically, and accepts on success. The button is an **Action**
        (no session mutation, no napari layer creation).
        """
        if self.is_workflow_locked:
            self.statusBar().showMessage(
                "A workflow is already running — click Cancel to stop it first."
            )
            return

        from percell4.gui.flim_fret_dialog import FlimFretDialog

        dialog = FlimFretDialog(parent=self)
        try:
            dialog.exec_()
            run_folder = dialog.last_run_folder
            if run_folder is not None:
                self.statusBar().showMessage(
                    f"FLIM-FRET run complete — CSV at {run_folder}/"
                    "flim_fret_results.csv"
                )
        finally:
            dialog.deleteLater()

    def _on_open_phasor_masks_workflow(self) -> None:
        """Open the Automated Phasor-Masks workflow dialog.

        Reentrance-guarded against ``is_workflow_locked``. The dialog drives
        its own ``QProgressDialog`` per-dataset loop on the main thread
        (no ``BaseWorkflowRunner``), emits one conditional
        ``session.refresh_resource_lists`` at end of run, and accepts on
        completion. The button is an **Action** (no session mutation except
        the one end-of-run refresh push).
        """
        if self.is_workflow_locked:
            self.statusBar().showMessage(
                "A workflow is already running — click Cancel to stop it first."
            )
            return

        from percell4.gui.phasor_masks_dialog import PhasorMasksDialog

        dialog = PhasorMasksDialog(parent=self)
        try:
            dialog.exec_()
            report = dialog.last_report
            if report is not None:
                n = len(report.items)
                self.statusBar().showMessage(
                    f"Phasor-masks workflow complete — {n} dataset(s) "
                    f"processed."
                )
        finally:
            dialog.deleteLater()

    def _on_open_single_cell_workflow(self) -> None:
        """Open the single-cell thresholding workflow config dialog.

        Reentrance-guarded against ``is_workflow_locked``. On Accepted,
        instantiates the runner, creates the run folder, wires
        progress signals to the status bar, and calls
        ``runner.start()``. Phase 4 MVP: the run executes synchronously
        on the main thread; Phase 1 (Cellpose) blocks the UI during
        each dataset's inference. Phase 8 polish will move Cellpose
        into a QThread worker.
        """
        if self.is_workflow_locked:
            self.statusBar().showMessage(
                "A workflow is already running — click Cancel to stop it first."
            )
            return

        from datetime import UTC, datetime

        from percell4.gui.workflows.single_cell.config_dialog import (
            WorkflowConfigDialog,
        )
        from percell4.gui.workflows.single_cell.runner import (
            SingleCellThresholdingRunner,
        )
        from percell4.workflows.artifacts import create_run_folder
        from percell4.workflows.models import RunMetadata

        dialog = WorkflowConfigDialog(parent=self)
        try:
            if dialog.exec_() != QDialog.Accepted:
                self.statusBar().showMessage("Workflow configuration cancelled.")
                return
            cfg = dialog.workflow_config
            # Per-dataset segmentation choices for already-segmented datasets
            # (captured before the dialog is destroyed in the finally block).
            segmentation_overrides = dialog.segmentation_overrides
        finally:
            dialog.deleteLater()

        if cfg is None:
            return

        # Build the run folder + metadata from the validated config.
        try:
            run_folder = create_run_folder(cfg.output_parent)
        except OSError as e:
            QMessageBox.warning(
                self,
                "Cannot create run folder",
                f"Failed to create run folder under {cfg.output_parent}:\n\n{e}",
            )
            return

        metadata = RunMetadata(
            run_id=run_folder.name,
            run_folder=run_folder,
            started_at=datetime.now(UTC),
            intersected_channels=sorted(
                {
                    ch
                    for ds in cfg.datasets
                    for ch in ds.channel_names
                }
            ),
        )

        # Close the current dataset before the run so the launcher UI
        # doesn't fight the workflow for control of the CellDataModel.
        if self.data_model.df is not None and not self.data_model.df.empty:
            answer = QMessageBox.question(
                self,
                "Close current dataset?",
                "Starting a workflow run will close the currently loaded "
                "dataset. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                # Run folder was already created — clean up to avoid an
                # orphan empty folder cluttering the output directory.
                try:
                    for sub in ("staging", "per_dataset"):
                        (run_folder / sub).rmdir()
                    run_folder.rmdir()
                except OSError:
                    pass
                self.statusBar().showMessage("Workflow cancelled before start.")
                return
            self.data_model.clear()

        # Build and wire the runner.
        runner = SingleCellThresholdingRunner(
            config=cfg, metadata=metadata,
            segmentation_overrides=segmentation_overrides,
        )
        # Keep a reference so the runner (a QObject) doesn't get GC'd
        # mid-run. Cleared when the run finishes.
        self._active_workflow_runner = runner
        runner.workflow_event.connect(self._on_workflow_event)

        # Start. This call returns immediately for interactive runs, but
        # in Phase 4 MVP every phase is UNATTENDED and blocks until
        # export completes.
        try:
            runner.start(config=cfg, host=self, metadata=metadata)
        except Exception as e:
            logger.exception("workflow runner raised out of start()")
            QMessageBox.warning(
                self,
                "Workflow error",
                f"The workflow runner raised an exception:\n\n{e}",
            )
            self._active_workflow_runner = None

    def _on_open_dilute_phase_workflow(self) -> None:
        """Open the dilute phase mask generation panel.

        Interactive single-dataset workflow (not generator-driven like
        the multi-dataset single_cell runner). The panel owns the
        :class:`DilutePhaseMaskController` state machine; we just gate
        re-entry via ``is_workflow_locked``, hold a strong reference so
        the panel + controller don't GC mid-flight, and unlock on
        Done / Cancel / error.
        """
        if self.is_workflow_locked:
            self.statusBar().showMessage(
                "A workflow is already running — finish or cancel it first."
            )
            return

        session = self.get_session()
        handle = session.dataset if session is not None else None
        if handle is None:
            self.statusBar().showMessage(
                "Open a dataset before launching the dilute phase workflow."
            )
            return

        viewer_win = self.get_viewer_window()
        store = getattr(self, "_current_store", None)
        if store is None or viewer_win is None:
            self.statusBar().showMessage(
                "Dilute phase workflow requires an open dataset and viewer."
            )
            return

        from percell4.gui.workflows.dilute_phase.panel import (
            DilutePhaseMaskPanel,
        )

        try:
            panel = DilutePhaseMaskPanel(
                parent=self,
                store=store,
                data_model=self.get_data_model(),
                session=session,
                viewer_win=viewer_win,
                get_active_seg_labels=self._get_active_seg_labels,
                handle=handle,
            )
        except Exception as e:
            logger.exception("failed to construct DilutePhaseMaskPanel")
            QMessageBox.warning(
                self,
                "Dilute phase workflow error",
                f"Could not open the dilute phase panel:\n\n{e}",
            )
            return

        self._active_dilute_workflow_panel = panel
        panel.workflow_done.connect(self._on_dilute_workflow_finished)
        panel.workflow_cancelled.connect(self._on_dilute_workflow_finished)
        panel.workflow_error.connect(self._on_dilute_workflow_error)

        # The panel was constructed with parent=self for Qt-lifecycle
        # ownership (closing the launcher closes the panel), but it
        # must render as its own top-level window — without the
        # Qt.Window flag, panel.show() would try to display the widget
        # inside the launcher's central-widget area where nothing adds
        # it to a layout, so the user sees the lock spinner but no
        # window. Window title + reasonable starting size make it
        # feel like a first-class workflow surface.
        panel.setWindowFlag(Qt.Window, True)
        panel.setWindowTitle("Dilute Phase Mask Generation")
        panel.resize(520, 700)

        self.set_workflow_locked(True)
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def _on_dilute_workflow_finished(self) -> None:
        """Common teardown for Done / Cancel / clean-error completion."""
        self.set_workflow_locked(False)
        panel = self._active_dilute_workflow_panel
        self._active_dilute_workflow_panel = None
        if panel is not None:
            try:
                panel.close()
            except Exception:
                logger.exception("dilute phase panel close raised")

    def _on_dilute_workflow_error(self, msg: str) -> None:
        """Surface a controller error in the status bar; release the
        lock if the panel reports a hard failure."""
        self.statusBar().showMessage(f"Dilute phase workflow: {msg}")

    def _on_workflow_event(self, event) -> None:
        """Slot for ``BaseWorkflowRunner.workflow_event`` signal.

        Updates the status bar on every phase progression and shows a
        summary dialog on terminal ``run_finished`` events.
        """
        from percell4.gui.workflows.base_runner import WorkflowEventKind

        if event.kind is WorkflowEventKind.PHASE_PROGRESS:
            bits = [event.phase_name]
            if event.total:
                bits.append(f"{event.current + 1}/{event.total}")
            if event.dataset_name:
                bits.append(event.dataset_name)
            if event.sub_progress:
                bits.append(f"({event.sub_progress})")
            self.statusBar().showMessage(" — ".join(bits))
            return

        if event.kind is WorkflowEventKind.RUN_FINISHED:
            runner = self._active_workflow_runner
            self._active_workflow_runner = None

            # Clean up the viewer: clear stale workflow layers and reset
            # the subtitle so the user doesn't see "Threshold QC —
            # Rep3_Untreated — round: SG_mask (6/6)" after the run is
            # done with only a leftover mask layer in the layer list.
            viewer_win = self._windows.get("viewer")
            if viewer_win is not None:
                try:
                    viewer = viewer_win.viewer
                    if viewer is not None:
                        viewer.layers.clear()
                except Exception:
                    pass
                try:
                    viewer_win.set_subtitle("")
                except Exception:
                    pass

            # Build a concise summary dialog. Distinguish three cases:
            # (a) no failures — fully successful run
            # (b) some failures — partial success (worth surfacing prominently)
            # (c) all datasets failed — run "completed" technically but produced
            #     no data; this MUST NOT be presented as success.
            run_folder = None
            n_failures = 0
            n_datasets = 0
            failed_phases: list[str] = []
            dataset_failures: set[str] = set()
            if runner is not None and runner._metadata is not None:
                run_folder = runner._metadata.run_folder
                n_failures = len(runner._metadata.failures)
                # Per-dataset failure count (excluding "<export>" sentinel
                # failures which represent aggregation issues, not dataset
                # failures).
                dataset_failures = {
                    f.dataset_name
                    for f in runner._metadata.failures
                    if f.dataset_name != "<export>"
                }
                failed_phases = sorted(
                    {f.phase_name for f in runner._metadata.failures}
                )
                try:
                    n_datasets = len(runner._config.datasets)
                except Exception:
                    n_datasets = 0

            all_failed = (
                n_datasets > 0
                and len(dataset_failures) >= n_datasets
            )

            if not event.success:
                header = "Workflow ended"
                body_prefix = f"Run did not complete: {event.message}"
            elif all_failed:
                header = "Workflow finished — no data produced"
                body_prefix = (
                    "Every dataset failed before measurement. "
                    "Outputs were not produced. Check the failure list below "
                    "and the run_config.json for full details."
                )
            elif n_failures > 0:
                header = "Workflow finished with failures"
                body_prefix = (
                    f"The run completed but {len(dataset_failures)} of "
                    f"{n_datasets} datasets failed and were excluded from "
                    f"measurement. Surviving datasets were exported."
                )
            else:
                header = "Workflow complete"
                body_prefix = "The workflow run finished successfully."

            body_parts = [body_prefix, "", f"Run folder: {run_folder}"]
            if n_failures > 0:
                body_parts.append(
                    f"Failures recorded: {n_failures} "
                    f"(phases: {', '.join(failed_phases) or 'unknown'})"
                )
            else:
                body_parts.append("Failures recorded: 0")
            body = "\n".join(body_parts)

            # Use warning() for failure-tinged outcomes so the icon
            # matches; information() for the all-clean case.
            if all_failed or not event.success:
                QMessageBox.critical(self, header, body)
            elif n_failures > 0:
                QMessageBox.warning(self, header, body)
            else:
                QMessageBox.information(self, header, body)
            self.statusBar().showMessage(
                f"Workflow {'complete' if event.success else 'ended'}: "
                f"{event.message}"
            )

    def _create_data_panel(self) -> QWidget:
        from percell4.interfaces.gui.task_panels.data_panel import DataPanel

        self._data_panel = DataPanel(
            self.data_model,
            get_store=lambda: getattr(self, "_current_store", None),
            get_viewer_window=lambda: self._windows.get("viewer"),
            get_h5_path=lambda: getattr(self, "_current_h5_path", None),
            show_status=lambda msg: self.statusBar().showMessage(msg),
        )
        return self._data_panel

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _wrap_in_scroll(widget: QWidget) -> QWidget:
        """Wrap a widget in a QScrollArea for panels that may exceed window height."""
        from qtpy.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(widget)
        from percell4.gui import theme

        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {theme.BACKGROUND_DEEP}; border: none; }}"
            f" QScrollArea > QWidget > QWidget {{ background-color: {theme.BACKGROUND_DEEP}; }}"
        )
        return scroll

    @staticmethod
    def _placeholder(text: str) -> QLabel:
        label = QLabel(f"  {text} — coming soon")
        label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-style: italic; padding: 4px 8px;"
        )
        return label

    # ── Window management ─────────────────────────────────────

    def _get_or_create_window(self, key: str) -> QWidget:
        """Get an existing window or create it on demand."""
        if key not in self._windows:
            from percell4.gui.viewer import ViewerWindow
            from percell4.interfaces.gui.peer_views.cell_table import CellTableWindow
            from percell4.interfaces.gui.peer_views.data_plot import DataPlotWindow
            from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow

            session = self.data_model.session
            factories = {
                "viewer": lambda: ViewerWindow(self.data_model),
                "data_plot": lambda: DataPlotWindow(session),
                "phasor_plot": lambda: PhasorPlotWindow(session, get_repo=lambda: self._repo),
                "cell_table": lambda: CellTableWindow(session),
            }
            if key in factories:
                window = factories[key]()
                self._windows[key] = window
                # Wire phasor plot signals — launcher mediates viewer access
                if key == "phasor_plot":
                    window.preview_roi_upserted.connect(self._on_phasor_preview_upserted)
                    window.preview_roi_removed.connect(self._on_phasor_preview_removed)
                    window.preview_all_cleared.connect(self._on_phasor_preview_cleared)
                    window.mask_applied.connect(self._on_phasor_mask_applied)
                    window.phasor_mask_applied.connect(
                        self._on_phasor_current_mask_applied
                    )
                # The viewer's `multi_select_requested` signal is
                # available for any callers that want a per-instance
                # subscription; the launcher itself receives `M`
                # events via the module-level subscriber registered in
                # `__init__` so this connect is not strictly needed.
        return self._windows.get(key)

    def _show_window(self, key: str) -> None:
        """Show/raise a managed window, creating it if needed."""
        window = self._get_or_create_window(key)
        if window is None:
            return

        # If opening the viewer and it's empty but we have a dataset, reload it
        if key == "viewer":
            viewer_empty = True
            try:
                viewer_empty = len(window.viewer.layers) == 0
            except Exception:
                viewer_empty = True
            if (
                viewer_empty
                and getattr(self, "_current_h5_path", None)
                and not getattr(self, "_loading_dataset", False)
            ):
                self._populate_viewer_from_store()

        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    # ── Action handlers ──────────────────────────────────────────

    def _on_open_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open Project Folder")
        if path:
            self._project_dir = path
            self.statusBar().showMessage(f"Opened project: {path}")

    def _on_multi_select(self) -> None:
        """Open the modal multi-label selection tool on the viewer.

        Per-cause feedback (Invariant I4): the status bar identifies
        which precondition refused the open, not just a generic refusal.
        """
        viewer_win = self.get_viewer_window()
        if viewer_win is None:
            self.statusBar().showMessage(
                "Multi-select unavailable: viewer not ready"
            )
            return
        viewer_win.show()
        result = viewer_win.launch_multi_select_tool(self)
        if result == "ok":
            return
        messages = {
            "no_labels_layer": "Multi-select unavailable: no labels layer",
            "workflow_locked": "Multi-select unavailable: another tool is running",
            "viewer_not_alive": "Multi-select unavailable: viewer not ready",
        }
        self.statusBar().showMessage(
            messages.get(result, "Multi-select unavailable")
        )

    def _on_import_dataset(self) -> None:
        from percell4.gui.compress_dialog import CompressDialog

        dialog = CompressDialog(
            self, project_dir=getattr(self, "_project_dir", None)
        )
        if dialog.exec_() != CompressDialog.Accepted:
            return

        # Capture config immediately (before dialog is destroyed)
        config = dialog.compress_config
        dialog.deleteLater()

        checked = [
            ds
            for ds in config.datasets
            if config.gui_states.get(ds.name, None)
            and config.gui_states[ds.name].checked
        ]
        if not checked:
            self.statusBar().showMessage("No datasets selected")
            return

        self._run_batch_compress(config, checked)

    def _run_batch_compress(self, config, datasets) -> None:
        """Compress one or more datasets with a progress dialog."""
        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QMessageBox, QProgressDialog

        from percell4.adapters.importer import import_dataset

        n = len(datasets)

        # Pre-flight collision check
        existing = [ds for ds in datasets if ds.output_path.exists()]
        if existing:
            names = ", ".join(ds.name for ds in existing[:5])
            if len(existing) > 5:
                names += f" (+{len(existing) - 5} more)"
            reply = QMessageBox.question(
                self,
                "Files Exist",
                f"{len(existing)} output file(s) already exist:\n{names}\n\nOverwrite all?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.No:
                datasets = [ds for ds in datasets if not ds.output_path.exists()]
                n = len(datasets)
                if n == 0:
                    self.statusBar().showMessage("No datasets to compress")
                    return

        # Window-modal progress dialog — blocks parent, prevents re-entrancy
        progress = QProgressDialog("Compressing...", "Cancel", 0, n, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        completed = []
        failed = []
        cancelled = False

        for i, ds in enumerate(datasets):
            if progress.wasCanceled():
                cancelled = True
                break

            progress.setValue(i)
            display_name = config.dataset_name_overrides.get(ds.name, ds.name)
            progress.setLabelText(f"({i + 1}/{n}) {display_name}")

            # Use overridden name for output path if renamed
            output_path = ds.output_path
            if ds.name in config.dataset_name_overrides:
                output_path = ds.output_path.parent / f"{config.dataset_name_overrides[ds.name]}.h5"

            try:
                n_ch = import_dataset(
                    str(ds.source_dir) if ds.source_dir else str(ds.files[0].path.parent),
                    str(output_path),
                    token_config=config.token_config,
                    tile_config=config.tile_config,
                    z_project_method=config.z_project_method,
                    selected_channels=config.selected_channels or None,
                    layer_assignments=config.layer_assignments,
                    files=ds.files,
                    creation_bin=config.creation_bin,
                )
                completed.append(display_name)
            except Exception as e:
                failed.append((ds.name, str(e)))

        progress.setValue(n)

        # Summary
        parts = []
        if completed:
            parts.append(f"{len(completed)} compressed")
        if failed:
            parts.append(f"{len(failed)} failed")
        if cancelled:
            parts.append("cancelled")
        summary = ", ".join(parts)
        self.statusBar().showMessage(f"Batch compress: {summary}")

        if failed:
            error_text = "\n".join(f"• {name}: {err}" for name, err in failed)
            QMessageBox.warning(
                self, "Compression Errors", f"Failed datasets:\n\n{error_text}"
            )

    def _on_load_dataset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Dataset", "", "HDF5 Files (*.h5);;All Files (*)"
        )
        if path:
            self._load_h5_into_viewer(path)

    def _on_add_layer_to_dataset(self) -> None:
        """Open the Add Layer dialog to import layers into the current dataset."""
        store = getattr(self, "_current_store", None)
        if store is None:
            self.statusBar().showMessage("No dataset loaded — load a dataset first")
            return

        from percell4.gui.add_layer_dialog import AddLayerDialog

        viewer_win = self._windows.get("viewer")
        dlg = AddLayerDialog(self, store, self.data_model, viewer_win)
        dlg.exec_()
        dlg.deleteLater()

    def _on_batch_tcspc_append(self) -> None:
        """Open the Batch TCSPC Append dialog.

        Operates on user-picked ``.h5`` files (does not require a loaded
        dataset). ``get_project_index`` is omitted — the launcher does not
        yet expose a ``ProjectIndex`` (the dialog's ``Add datasets…`` button
        is the entry path). See the plan's Scope Boundaries for the deferred
        project-awareness wiring.
        """
        from percell4.gui.batch_tcspc_dialog import BatchTCSPCDialog

        dlg = BatchTCSPCDialog(
            self,
            session=self.data_model.session,
            show_status=lambda msg: self.statusBar().showMessage(msg),
        )
        dlg.exec_()
        dlg.deleteLater()

    def _load_h5_into_viewer(self, h5_path: str) -> None:
        """Set the current dataset and load it into the viewer."""
        from pathlib import Path as _Path

        from percell4.adapters.hdf5_store import _build_handle_metadata
        from percell4.domain.dataset import DatasetHandle
        from percell4.store import DatasetStore

        store = DatasetStore(h5_path)
        if not store.exists():
            self.statusBar().showMessage(f"File not found: {h5_path}")
            return

        # Set as current dataset for the entire app
        self._current_store = store
        self._current_h5_path = h5_path

        # Update Session with DatasetHandle (drives channel combo + active_channel)
        handle = DatasetHandle(path=_Path(h5_path), metadata=_build_handle_metadata(store))
        self.data_model.session.set_dataset(handle)

        # Update Data tab info + dropdowns
        self._update_data_tab_from_store()

        # Show viewer and populate with data. Suppress _show_window's
        # "empty viewer -> auto-populate" safety net during the load so the
        # explicit populate below is the single decode (otherwise the dataset
        # is decoded twice — two progress dialogs, ~2x load time).
        self._loading_dataset = True
        try:
            self._show_window("viewer")
        finally:
            self._loading_dataset = False
        self._populate_viewer_from_store()

        # Show filename in viewer title bar
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None:
            viewer_win.set_subtitle(Path(h5_path).name)

        self.statusBar().showMessage(f"Loaded: {Path(h5_path).name}")

    def _populate_viewer_from_store(self, view_bin: int | None = None) -> None:
        """Populate the napari viewer from the current dataset store.

        Called when loading a dataset, re-opening the viewer, and on every
        ``change.bin`` toggle through ``_on_state_changed``.

        ``view_bin`` defaults to ``session.active_bin`` so the viewer
        always reflects the current session lens. Pass an explicit value
        to override (e.g., debugging at native while session is at k>1).
        """
        store = getattr(self, "_current_store", None)
        h5_path = getattr(self, "_current_h5_path", None)
        if store is None or h5_path is None:
            return

        viewer_win = self._windows.get("viewer")
        if viewer_win is None:
            return

        if view_bin is None:
            view_bin = self.data_model.session.active_bin

        # Intensity existence + inventory from metadata only (no decode).
        try:
            store.array_shape("intensity")
        except KeyError:
            self.statusBar().showMessage(
                f"No intensity data in {Path(h5_path).name}"
            )
            return

        meta = store.metadata
        channel_names = meta.get("channel_names", [])
        n_timepoints = int(meta.get("n_timepoints", 1) or 1)
        mask_names = list(store.list_masks())
        mask_set = set(mask_names)
        label_names = [n for n in store.list_labels() if n not in mask_set]

        viewer_win.clear()

        # Native resolution (view_bin == 1) is the heavy case — decode it in
        # parallel across processes. Binned views (k > 1) are downsampled and
        # small, so the simple serial read is fine and avoids re-implementing
        # the per-path binning on the parallel result.
        if view_bin == 1:
            self._populate_parallel(
                h5_path, viewer_win, channel_names, n_timepoints,
                label_names, mask_names,
            )
        else:
            self._populate_serial(
                store, viewer_win, view_bin, channel_names, n_timepoints,
                label_names, mask_names,
            )

    def _populate_serial(
        self, store, viewer_win, view_bin, channel_names, n_timepoints,
        label_names, mask_names,
    ) -> None:
        """Read + display every layer serially (binned-view path)."""
        with store.open_read() as s:
            intensity = s.read_array("intensity", view_bin=view_bin)
            for name, arr in split_intensity_layers(
                intensity, channel_names, n_timepoints
            ):
                viewer_win.add_image(arr, name=name)
            for label_name in label_names:
                viewer_win.add_labels(
                    s.read_labels(label_name, view_bin=view_bin), name=label_name
                )
            for mask_name in mask_names:
                viewer_win.add_mask(
                    s.read_mask(mask_name, view_bin=view_bin), name=mask_name
                )

    def _populate_parallel(
        self, h5_path, viewer_win, channel_names, n_timepoints,
        label_names, mask_names,
    ) -> None:
        """Decode every native-resolution layer with the process pool.

        gzip decode is single-threaded per HDF5 call; spreading frames across
        worker processes (writing into shared memory) is ~5x faster on a large
        timecourse. A modal progress dialog keeps the UI responsive while the
        decode runs in subprocesses. Layers are created here on the main thread
        (napari is not thread-safe); the intensity ``(T,C,H,W)`` block is split
        into per-channel layers via the same ``split_intensity_layers`` rule.
        """
        from qtpy.QtWidgets import QApplication, QProgressDialog

        from percell4.adapters.parallel_decode import (
            array_meta,
            decode_array_parallel,
            default_worker_count,
            make_executor,
        )

        # Manifest with per-array frame counts (metadata only) for progress.
        entries: list[tuple[str, str | None, str, int]] = []

        def _frames(hp: str) -> int:
            shp, _dtype, is_ts = array_meta(h5_path, hp)
            return shp[0] if is_ts else 1

        entries.append(("intensity", None, "intensity", _frames("intensity")))
        for ln in label_names:
            entries.append(("labels", ln, f"labels/{ln}", _frames(f"labels/{ln}")))
        for mn in mask_names:
            entries.append(("mask", mn, f"masks/{mn}", _frames(f"masks/{mn}")))
        total = sum(e[3] for e in entries)

        # --- DEBUG (temporary): dump load environment to the cmd window so a
        # stall ("bar freezes at 8%") on another machine is diagnosable. The
        # parallel decode allocates the full stack in shared memory up front,
        # so low-RAM / Windows shared_memory issues surface here.
        import platform as _platform
        import sys as _sys

        try:
            import psutil as _psutil

            _vm = _psutil.virtual_memory()
            # On Windows, multiprocessing.shared_memory is paging-file backed,
            # so the big up-front shm block competes for swap/pagefile, not just
            # RAM. Report both so a pagefile-commit stall is visible.
            _sw = _psutil.swap_memory()
            _mem = (
                f"RAM total={_vm.total / 1e9:.1f}GB "
                f"avail={_vm.available / 1e9:.1f}GB used={_vm.percent:.0f}% | "
                f"SWAP/pagefile total={_sw.total / 1e9:.1f}GB "
                f"free={_sw.free / 1e9:.1f}GB used={_sw.percent:.0f}%"
            )
        except Exception as _e:  # noqa: BLE001 — debug only
            _mem = f"RAM=unavailable ({_e})"
        print(
            f"[PC4-LOAD] === loading {Path(h5_path).name} ===\n"
            f"[PC4-LOAD] platform={_platform.platform()} "
            f"python={_platform.python_version()} "
            f"cpu_count={default_worker_count()} {_mem}",
            file=_sys.stderr,
            flush=True,
        )
        for _kind, _name, _hp, _nf in entries:
            print(
                f"[PC4-LOAD]   entry kind={_kind} hdf5_path={_hp} frames={_nf}",
                file=_sys.stderr,
                flush=True,
            )
        print(
            f"[PC4-LOAD] total progress frames={total}",
            file=_sys.stderr,
            flush=True,
        )

        progress = QProgressDialog("Loading dataset…", None, 0, total, self)
        progress.setWindowTitle("Loading")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        executor = make_executor(default_worker_count())
        base = 0
        parallel_exc: Exception | None = None
        try:
            for kind, name, hp, n_frames in entries:
                print(
                    f"[PC4-LOAD] -> decoding '{hp}' "
                    f"(progress base={base}/{total})",
                    file=_sys.stderr,
                    flush=True,
                )

                def _cb(done: int, _base: int = base) -> None:
                    progress.setValue(_base + done)
                    QApplication.processEvents()

                arr = decode_array_parallel(
                    h5_path, hp, executor=executor, progress_cb=_cb
                )
                if kind == "intensity":
                    for nm, plane in split_intensity_layers(
                        arr, channel_names, n_timepoints
                    ):
                        viewer_win.add_image(plane, name=nm)
                elif kind == "labels":
                    viewer_win.add_labels(arr, name=name)
                else:
                    viewer_win.add_mask(arr, name=name)
                print(
                    f"[PC4-LOAD] <- finished '{hp}'",
                    file=_sys.stderr,
                    flush=True,
                )
                base += n_frames
                del arr
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the UI
            parallel_exc = exc
            # DEBUG (temporary): full traceback to the cmd window, not just the
            # status bar (which is easy to miss when the bar appears to freeze).
            import traceback as _tb

            print(
                f"[PC4-LOAD] PARALLEL LOAD FAILED: {type(exc).__name__}: {exc}",
                file=_sys.stderr,
                flush=True,
            )
            _tb.print_exc()
            _sys.stderr.flush()
        finally:
            executor.shutdown(wait=True)
            progress.close()

        if parallel_exc is None:
            self.statusBar().showMessage(f"Loaded: {Path(h5_path).name}")
            return

        # Fallback: the parallel path uses multiprocessing.shared_memory, which
        # on Windows is pagefile-backed (SEC_COMMIT) and has a transient 2x peak
        # (the section plus the copied-out result). On a box with a small
        # pagefile the accumulating commit can hit the system limit
        # (OSError WinError 1450, "Insufficient system resources"). The serial
        # decoder reads each array once into ordinary process heap — no named
        # sections, lower peak — so it loads (slower) where the parallel path
        # cannot. Reload from scratch to avoid half-populated / duplicate layers.
        store = getattr(self, "_current_store", None)
        if store is None:
            self.statusBar().showMessage(f"Load error: {parallel_exc}")
            return
        print(
            f"[PC4-LOAD] falling back to single-process serial decode "
            f"(parallel failed: {type(parallel_exc).__name__})",
            file=_sys.stderr,
            flush=True,
        )
        self.statusBar().showMessage("Parallel load failed; loading serially…")
        QApplication.processEvents()
        viewer_win.clear()
        try:
            self._populate_serial(
                store, viewer_win, 1, channel_names, n_timepoints,
                label_names, mask_names,
            )
            print("[PC4-LOAD] serial fallback complete", file=_sys.stderr, flush=True)
            self.statusBar().showMessage(f"Loaded (serial): {Path(h5_path).name}")
        except Exception as exc2:  # noqa: BLE001 — surface, don't crash the UI
            import traceback as _tb2

            print(
                f"[PC4-LOAD] SERIAL FALLBACK FAILED: {type(exc2).__name__}: {exc2}",
                file=_sys.stderr,
                flush=True,
            )
            _tb2.print_exc()
            _sys.stderr.flush()
            self.statusBar().showMessage(f"Load error: {exc2}")


    def _update_data_tab_from_store(self) -> None:
        """Update the Data tab info label and dropdowns from the current store."""
        if hasattr(self, "_data_panel"):
            self._data_panel.refresh_dataset_info()
            self._data_panel._refresh_seg_combos()
            self._data_panel._refresh_mask_combos()
            self._data_panel.refresh_management_combos()

    def _on_close_dataset(self) -> None:
        from percell4.adapters.napari_viewer import NapariViewerAdapter
        from percell4.application.use_cases.close_dataset import CloseDataset

        viewer_win = self._windows.get("viewer")

        # Delegate to use case (clears session + viewer)
        try:
            if viewer_win is not None:
                viewer_adapter = NapariViewerAdapter(viewer_win)
                uc = CloseDataset(viewer_adapter, self.data_model.session)
                uc.execute()
            else:
                # No viewer open — just clear the session directly
                self.data_model.session.clear()
        except Exception as e:
            self.statusBar().showMessage(f"Error closing dataset: {e}")
            return

        # Update launcher UI
        if viewer_win is not None:
            viewer_win.set_subtitle("")
        self._current_store = None
        self._current_h5_path = None
        if hasattr(self, "_data_panel"):
            self._data_panel.clear_ui()
        self.statusBar().showMessage("Dataset closed")

    def _apply_cell_filter(self, labels: np.ndarray) -> np.ndarray | None:
        """Zero out non-filtered cells in the labels array.

        Returns the (possibly copied) labels array, or None if no cells remain.
        """
        import numpy as np

        filtered_ids = self.data_model.filtered_ids
        if filtered_ids is not None:
            cell_mask = np.isin(labels, list(filtered_ids))
            labels = labels.copy()
            labels[~cell_mask] = 0
            if labels.max() == 0:
                self.statusBar().showMessage("No filtered cells to process")
                return None
        return labels

    def _get_active_seg_labels(self) -> np.ndarray | None:
        """Get the active segmentation labels array from the viewer.

        Falls back to the first Labels layer whose name contains common
        segmentation keywords if active_segmentation is not set.
        """
        import napari
        import numpy as np

        viewer_win = self._windows.get("viewer")
        if viewer_win is None or not viewer_win._is_alive():
            return None

        session = self.data_model.session

        def _slice_active_frame(data) -> np.ndarray:
            """Return the 2D active-timepoint frame of a (T,H,W) labels layer.

            A 2D (time-invariant) or single-timepoint label is returned as-is.
            Without this, raveling a whole (T,H,W) stack into the phasor feed
            mixes frames.
            """
            arr = np.asarray(data, dtype=np.int32)
            if arr.ndim == 3 and session.n_timepoints > 1:
                return arr[session.active_timepoint]
            return arr

        seg_name = self.data_model.active_segmentation
        if seg_name:
            for layer in viewer_win._viewer.layers:
                if layer.name == seg_name:
                    return _slice_active_frame(layer.data)

        # Fallback: find a segmentation labels layer (skip mask layers)
        from percell4.gui.viewer import LAYER_TYPE_MASK, PERCELL_TYPE_KEY
        for layer in viewer_win._viewer.layers:
            if not isinstance(layer, napari.layers.Labels):
                continue
            if layer.name.startswith("_"):
                continue
            if layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK:
                continue
            return _slice_active_frame(layer.data)
        return None

    def _get_phasor_roi_names(self) -> dict[int, str]:
        """Get ROI label→name mapping from the phasor plot window."""
        phasor_win = self._windows.get("phasor_plot")
        if phasor_win is None:
            return {}
        return phasor_win.get_visible_roi_names()

    # ── Model state change handler ─────────────────────────────

    def _on_state_changed(self, change) -> None:
        """Handle model state changes relevant to the launcher.

        Most changes route to the per-panel handlers (AnalysisPanel,
        DataPanel, ViewerWindow). Bin toggles are a launcher concern
        because they require a wholesale viewer rebuild against the
        store -- the launcher owns the store handle and the populate
        routine.
        """
        if change.bin:
            self._rebuild_viewer_for_bin_change()

    def _rebuild_viewer_for_bin_change(self) -> None:
        """Tear down and re-populate every layer at the new view bin.

        Wrapped in viewer's ``_is_originator`` so napari's layer-list
        callbacks cannot write back into Session during the rebuild.
        After the layers land we restore the active mask/segmentation
        via the viewer's strict matcher (the same path
        ``_push_active_layer_to_napari`` takes). The selection restore
        sets ``viewer.layers.selection.active`` directly rather than
        calling ``session.set_active_*`` -- that avoids the N+1
        ``state_changed`` cascade that would otherwise re-fire on every
        toggle (each set_active_* would fire ACTIVE_*_CHANGED ->
        StateChange -> back into this handler).
        """
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or not viewer_win._is_alive():
            return

        session = self.data_model.session
        view_bin = session.active_bin

        viewer_win._is_originator = True
        try:
            self._populate_viewer_from_store(view_bin=view_bin)
            # Restore active selections via napari layer selection (NOT
            # via session.set_active_*). Session's active_* fields are
            # already correct; we just need napari to reflect them.
            if session.active_segmentation:
                from percell4.gui.viewer import LAYER_TYPE_SEGMENTATION
                viewer_win._push_active_layer_to_napari(
                    session.active_segmentation, LAYER_TYPE_SEGMENTATION
                )
            if session.active_mask:
                from percell4.gui.viewer import LAYER_TYPE_MASK
                viewer_win._push_active_layer_to_napari(
                    session.active_mask, LAYER_TYPE_MASK
                )
        finally:
            viewer_win._is_originator = False

    # ── Phasor plot signal handlers ─────────────────────────────

    # ── Phasor ROI preview layers ───────────────────────────────
    #
    # One napari Labels layer per phasor ROI, named
    # ``_phasor_roi_preview_<roi_name>``. The phasor window owns the
    # state; the launcher just maps signals to layer create/update/
    # remove. Visibility is `layer.visible` so the user can keep
    # opacity/colormap tweaks across hide/show.

    _PHASOR_PREVIEW_PREFIX = "_phasor_roi_preview_"

    def _phasor_preview_layer_name(self, roi_name: str) -> str:
        return f"{self._PHASOR_PREVIEW_PREFIX}{roi_name}"

    def _on_phasor_preview_upserted(
        self, roi_name: str, binary_mask, hex_color: str, visible: bool
    ) -> None:
        """Create or update the napari preview layer for one phasor ROI."""
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or not viewer_win._is_alive():
            return

        from napari.utils.colormaps import DirectLabelColormap

        layer_name = self._phasor_preview_layer_name(roi_name)
        color_dict = {0: "transparent", 1: hex_color, None: "transparent"}
        colormap = DirectLabelColormap(color_dict=color_dict)

        layers = viewer_win._viewer.layers
        if layer_name in layers:
            layer = layers[layer_name]
            layer.data = binary_mask
            layer.colormap = colormap
            layer.visible = visible
        else:
            viewer_win._viewer.add_labels(
                binary_mask,
                name=layer_name,
                colormap=colormap,
                opacity=vp.PHASOR_ROI_MASK_OPACITY,
                blending=vp.PHASOR_ROI_MASK_BLENDING,
                visible=visible,
            )

    def _on_phasor_preview_removed(self, roi_name: str) -> None:
        """Remove the napari preview layer for one phasor ROI, if present."""
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or not viewer_win._is_alive():
            return
        try:
            viewer_win._viewer.layers.remove(
                self._phasor_preview_layer_name(roi_name)
            )
        except (KeyError, ValueError):
            pass

    def _on_phasor_preview_cleared(self) -> None:
        """Remove every phasor-ROI preview layer (dataset switch / window close)."""
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or not viewer_win._is_alive():
            return
        layers = viewer_win._viewer.layers
        stale = [
            layer.name for layer in layers
            if layer.name.startswith(self._PHASOR_PREVIEW_PREFIX)
        ]
        for name in stale:
            try:
                layers.remove(name)
            except (KeyError, ValueError):
                pass

    def _on_phasor_mask_applied(self, roi_masks) -> None:
        """Handle per-ROI phasor masks: one binary mask per ROI.

        Each entry is (roi_name, binary_mask_uint8, hex_color).
        Store-before-layer invariant preserved for each mask. The
        per-ROI preview layers for the applied (visible) ROIs are
        removed since their mask role is taken over by the saved layer.
        """
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win._is_alive():
            for roi_name, _binary, _color in roi_masks:
                try:
                    viewer_win._viewer.layers.remove(
                        self._phasor_preview_layer_name(roi_name)
                    )
                except (KeyError, ValueError):
                    pass
            if hasattr(self, "_preview_timer"):
                self._preview_timer.stop()

        store = getattr(self, "_current_store", None)
        last_name = None

        for roi_name, binary_mask, hex_color in roi_masks:
            # Store-before-layer: write to HDF5 before adding napari layer
            if store is not None:
                store.write_mask(roi_name, binary_mask)

            if viewer_win is not None and viewer_win._is_alive():
                color_dict = {0: "transparent", 1: hex_color, None: "transparent"}
                viewer_win.add_mask(binary_mask, name=roi_name, color_dict=color_dict)

            last_name = roi_name

        if last_name:
            if store is not None:
                self.data_model.session.refresh_resource_lists(
                    mask_names=store.list_masks(),
                )
            self.data_model.set_active_mask(last_name)

    def _on_phasor_current_mask_applied(self, payload) -> None:
        """Persist the "Apply Current Phasor as Mask" payload.

        Mirrors :meth:`_on_phasor_mask_applied` for a single
        ``(name, binary)`` tuple. Writes /masks/<name>, adds a napari
        layer when a viewer is alive, persists filter-state attributes
        on the new dataset for provenance, refreshes the session's
        mask list, sets ``session.active_mask`` to the new name, and
        surfaces the auto-select via the launcher status bar.

        Filter-state attributes are read back from the live
        ``PhasorPlotWindow`` rather than carried in the payload — this
        is an explicit launcher → window coupling on a handful of
        private fields (``_intensity_threshold``, ``_ref_circle_*``,
        ``_cleared_mask``). The signal payload stays minimal at
        ``(name, binary)``; if the coupling becomes a problem, extend
        the payload with a filter-state dict.
        """
        from datetime import datetime

        from percell4.store import (
            PHASOR_MASK_ATTR_ACTIVE_CHANNEL,
            PHASOR_MASK_ATTR_ACTIVE_MASK,
            PHASOR_MASK_ATTR_CAPTURE_ISO,
            PHASOR_MASK_ATTR_CLEARED_PIXEL_COUNT,
            PHASOR_MASK_ATTR_INTENSITY_THRESHOLD,
            PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_G,
            PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_S,
            PHASOR_MASK_ATTR_REF_CIRCLE_RADIUS,
        )

        name, binary = payload

        store = getattr(self, "_current_store", None)
        if store is None:
            return

        store.write_mask(name, binary)

        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win._is_alive():
            viewer_win.add_mask(binary, name=name)

        # Sentinel values stand in for None because HDF5 attrs cannot
        # hold None: 0.0 for unset center, -1.0 for unset radius (so
        # readers can distinguish "no circle" from "radius 0").
        phasor_win = self._windows.get("phasor_plot")
        attrs: dict[str, object] = {
            PHASOR_MASK_ATTR_CAPTURE_ISO: (
                datetime.now(UTC)
                .replace(tzinfo=None)
                .isoformat()
                + "Z"
            ),
        }
        if phasor_win is not None:
            session = phasor_win._session
            threshold = phasor_win._intensity_threshold
            attrs[PHASOR_MASK_ATTR_INTENSITY_THRESHOLD] = (
                float(threshold) if threshold is not None else 0.0
            )
            center = phasor_win._ref_circle_center
            if center is not None:
                attrs[PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_G] = float(center[0])
                attrs[PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_S] = float(center[1])
            else:
                attrs[PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_G] = 0.0
                attrs[PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_S] = 0.0
            radius = phasor_win._ref_circle_radius
            attrs[PHASOR_MASK_ATTR_REF_CIRCLE_RADIUS] = (
                float(radius) if radius is not None else -1.0
            )
            attrs[PHASOR_MASK_ATTR_ACTIVE_MASK] = session.active_mask or ""
            cleared = phasor_win._cleared_mask
            attrs[PHASOR_MASK_ATTR_CLEARED_PIXEL_COUNT] = (
                int(cleared.sum()) if cleared is not None else 0
            )
            attrs[PHASOR_MASK_ATTR_ACTIVE_CHANNEL] = session.active_channel or ""
        store.set_mask_attrs(name, attrs)

        self.data_model.session.refresh_resource_lists(
            mask_names=store.list_masks(),
        )
        self.data_model.set_active_mask(name)

        self.statusBar().showMessage(
            f"Saved {name} (now active — next save will be filtered "
            f"against this mask)",
            5000,
        )

    # ── Analysis + FLIM handlers moved to task panels ──────
    # See: interfaces/gui/task_panels/analysis_panel.py
    # See: interfaces/gui/task_panels/flim_panel.py

    def _on_export_csv(self) -> None:
        if self.data_model.df.empty:
            self.statusBar().showMessage("No measurements to export")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Measurements", "measurements.csv", "CSV Files (*.csv)"
        )
        if path:
            self.data_model.df.to_csv(path, index=False)
            self.statusBar().showMessage(f"Exported to {path}")

    def _on_export_images(self) -> None:
        """Export selected layers from the loaded dataset as TIFF files."""
        store = getattr(self, "_current_store", None)
        if store is None:
            self.statusBar().showMessage("No dataset loaded")
            return

        from percell4.gui.export_images_dialog import ExportImagesDialog

        dlg = ExportImagesDialog(self, store, session=self.data_model.session)
        if dlg.exec_() != ExportImagesDialog.Accepted:
            return

        output_folder = dlg.output_folder
        channels = dlg.selected_channels
        labels = dlg.selected_labels
        masks = dlg.selected_masks
        view_bin = dlg.selected_view_bin()
        dlg.deleteLater()

        if output_folder is None:
            self.statusBar().showMessage("No output folder selected")
            return

        h5_name = Path(getattr(self, "_current_h5_path", "dataset")).stem

        try:
            from percell4.application.use_cases.export_images import (
                ExportImages,
                ExportRequest,
            )

            handle = self.data_model.session.dataset
            if handle is None:
                self.statusBar().showMessage("No dataset loaded")
                return

            uc = ExportImages(self._repo)
            # Fresh metadata read so post-import / post-TCSPC pixel-size
            # updates surface without reopening the handle.
            pixel_size_um = self._repo.read_metadata(handle).get(
                "pixel_size_um"
            )
            result = uc.execute(
                handle,
                ExportRequest(
                    output_folder=output_folder,
                    dataset_name=h5_name,
                    channels=channels,
                    labels=labels,
                    masks=masks,
                    view_bin=view_bin,
                    pixel_size_um=pixel_size_um,
                ),
            )
            bin_suffix = f" at k={view_bin}" if view_bin > 1 else ""
            self.statusBar().showMessage(
                f"Exported {result.exported_count} image(s){bin_suffix} "
                f"to {result.output_folder}"
            )
        except Exception as e:
            self.statusBar().showMessage(f"Export error: {e}")

    def _on_export_phasor_npz(self) -> None:
        """Export every channel's cached phasor data to .npz files."""
        if self.data_model.session.dataset is None:
            self.statusBar().showMessage("No dataset loaded")
            return

        out_dir = QFileDialog.getExistingDirectory(
            self, "Export Phasor (.npz) to..."
        )
        if not out_dir:
            return

        from percell4.application.use_cases.export_phasor_npz import (
            ExportPhasorNpz,
        )

        try:
            uc = ExportPhasorNpz(self._repo, self.data_model.session)
            result = uc.execute(Path(out_dir))
        except Exception as e:
            self.statusBar().showMessage(f"Export Phasor (.npz) error: {e}")
            return

        if not result.exported:
            self.statusBar().showMessage("No cached phasor to export")
            return

        msg = (
            f"Exported phasor for {len(result.exported)} channel(s) to {out_dir}"
        )
        if result.skipped:
            msg += f" ({len(result.skipped)} channel(s) skipped: no cache)"
        self.statusBar().showMessage(msg)

    # ── Batch workflow host API ───────────────────────────────
    #
    # These methods implement the percell4.workflows.host.WorkflowHost
    # protocol used by batch workflow runners. They are called from the
    # batch runner; regular launcher actions should not poke at
    # ``_workflow_locked`` directly.

    @property
    def is_workflow_locked(self) -> bool:
        """Whether a batch workflow currently owns the main UI."""
        return self._workflow_locked

    def set_workflow_locked(self, locked: bool) -> None:
        """Disable (or re-enable) the launcher's main UI.

        While locked, the sidebar, every content panel, and the File menu
        are disabled. The workflow runner is expected to host its own UI
        (config dialog, progress dialogs, QC controller windows), which
        are separate top-level windows and stay interactive.

        Calling with the same value twice is a no-op.
        """
        if locked == self._workflow_locked:
            return
        self._workflow_locked = locked

        central = self.centralWidget()
        if central is not None:
            central.setEnabled(not locked)
        self.menuBar().setEnabled(not locked)
        if locked:
            self.statusBar().showMessage("Workflow running...")
        else:
            self.statusBar().showMessage("Ready")

    def show_workflow_status(self, phase_name: str, sub_progress: str) -> None:
        """Display a live status string for the running workflow."""
        if sub_progress:
            self.statusBar().showMessage(f"{phase_name} — {sub_progress}")
        else:
            self.statusBar().showMessage(phase_name)

    def get_viewer_window(self):
        """Return the shared ``ViewerWindow``, creating and wiring it if needed.

        Lazily creates the viewer on first access. If the workflow itself
        is what caused the creation (there was no viewer before
        :meth:`close_child_windows` was called), the host will close the
        viewer automatically in :meth:`restore_child_windows` so the user
        is not left with a dangling napari window after the run.
        """
        return self._get_or_create_window("viewer")

    def get_session(self):
        """Return the shared application Session."""
        return self.data_model.session

    def get_data_model(self):
        """Deprecated — use get_session(). Kept for backward compat."""
        return self.data_model

    def close_child_windows(self) -> None:
        """Close cell_table / data_plot / phasor_plot for the run.

        Actually closes the windows (not just ``hide()``) and removes them
        from the window registry so they do not receive
        ``CellDataModel.state_changed`` signals during the run — the plan
        explicitly called out that signal thrash was the reason to close
        them. They are re-instantiated on demand in
        :meth:`restore_child_windows` via the usual factories.

        Also records whether the viewer existed before this call so that
        :meth:`restore_child_windows` can close a workflow-created viewer
        instead of leaving it dangling.
        """
        child_keys = ("cell_table", "data_plot", "phasor_plot")
        self._child_windows_to_restore = []
        for key in child_keys:
            win = self._windows.get(key)
            if win is None:
                continue
            try:
                was_visible = bool(win.isVisible())
            except Exception:
                was_visible = False
            if was_visible:
                self._child_windows_to_restore.append(key)
            win.close()
            # Remove from registry so _show_window reinstantiates a fresh
            # window on restore; this guarantees no leaked signal handlers.
            del self._windows[key]

        # Track whether the viewer existed before the run. If not, and the
        # runner later calls get_viewer_window, restore_child_windows will
        # close the viewer on cleanup.
        self._viewer_created_by_workflow = "viewer" not in self._windows

    def restore_child_windows(self) -> None:
        """Re-show the child windows that were open before the workflow.

        Preserves the original open order via the list recorded in
        :meth:`close_child_windows`. If the workflow implicitly created
        the viewer (see :meth:`get_viewer_window`), the viewer is closed
        here so the user is not left with a dangling napari window.
        """
        for key in self._child_windows_to_restore:
            self._show_window(key)
        self._child_windows_to_restore = []

        if self._viewer_created_by_workflow:
            viewer = self._windows.get("viewer")
            if viewer is not None:
                viewer.close()
                # Leave the registry entry intact so the user can still
                # open the viewer via the sidebar — it will be lazily
                # re-shown by the existing _show_window path.
            self._viewer_created_by_workflow = False

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Close all managed windows and quit.

        If a batch workflow is currently running, prompt the user to
        cancel it first — otherwise the runner would be orphaned
        mid-run with half-written h5 artifacts. On confirmation, call
        :meth:`BaseWorkflowRunner.request_cancel` so the runner unwinds
        cleanly at the next dataset boundary before we tear down the
        window.
        """
        if self.is_workflow_locked and self._active_workflow_runner is not None:
            answer = QMessageBox.question(
                self,
                "Cancel running workflow?",
                "A workflow run is currently in progress. Quit and cancel "
                "the run? The in-flight dataset will finish before the "
                "runner unwinds; any labels, masks, and staging data "
                "already written will remain on disk but the final run "
                "artifacts may not be created.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            try:
                self._active_workflow_runner.request_cancel()
            except Exception:
                logger.exception("failed to propagate cancel to runner")

        self._save_geometry()
        for window in self._windows.values():
            window.close()
        event.accept()

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "launcher/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("launcher/geometry")
        if geom:
            self.restoreGeometry(geom)
