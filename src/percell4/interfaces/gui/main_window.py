"""Launcher/hub window — the main control center for PerCell4.

Sidebar with category buttons, stacked content area showing sub-options
for the selected category. Manages all other windows.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from qtpy.QtCore import QSettings, Qt
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

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

        # Unified model state change handler
        self.data_model.state_changed.connect(self._on_state_changed)

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
            on_close=self._on_close_dataset,
            on_export_csv=self._on_export_csv,
            on_export_images=self._on_export_images,
            show_status=lambda msg: self.statusBar().showMessage(msg),
        )
        return self._io_panel

    def _create_viewer_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(self._section_label("Viewer"))

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
            show_status=lambda msg: self.statusBar().showMessage(msg),
            launcher=self,  # transitional: only for GroupedSegPanel
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

        layout.addWidget(self._section_label("Scripts"))

        btn_run = QPushButton("Run Script...")
        btn_run.clicked.connect(self._on_run_script)
        layout.addWidget(btn_run)

        layout.addWidget(self._placeholder("Macro System"))
        layout.addStretch()
        return panel

    def _create_workflows_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(self._section_label("Workflows"))

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

        layout.addStretch()
        return panel

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
        runner = SingleCellThresholdingRunner(config=cfg, metadata=metadata)
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

            # Build a concise summary dialog.
            if event.success:
                header = "Workflow complete"
                body_prefix = "The workflow run finished successfully."
            else:
                header = "Workflow ended"
                body_prefix = f"Run did not complete successfully: {event.message}"

            run_folder = getattr(runner, "_metadata", None)
            if run_folder is not None:
                run_folder = run_folder.run_folder
            n_failures = 0
            if runner is not None and runner._metadata is not None:
                n_failures = len(runner._metadata.failures)

            body = (
                f"{body_prefix}\n\n"
                f"Run folder: {run_folder}\n"
                f"Failures recorded: {n_failures}"
            )
            QMessageBox.warning(self, header, body)
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
    def _section_label(text: str) -> QLabel:
        from percell4.gui import theme

        label = QLabel(text)
        label.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT};"
            f" margin-bottom: 12px; border: none; background: transparent;"
        )
        return label

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
            from percell4.gui.cell_table import CellTableWindow
            from percell4.gui.data_plot import DataPlotWindow
            from percell4.gui.phasor_plot import PhasorPlotWindow
            from percell4.gui.viewer import ViewerWindow

            session = self.data_model.session
            factories = {
                "viewer": lambda: ViewerWindow(self.data_model),
                "data_plot": lambda: DataPlotWindow(session),
                "phasor_plot": lambda: PhasorPlotWindow(session),
                "cell_table": lambda: CellTableWindow(session),
            }
            if key in factories:
                window = factories[key]()
                self._windows[key] = window
                # Wire phasor plot signals — launcher mediates viewer access
                if key == "phasor_plot":
                    window.preview_mask_ready.connect(self._on_phasor_preview)
                    window.mask_applied.connect(self._on_phasor_mask_applied)
        return self._windows.get(key)

    def _wire_viewer_layer_selection(self) -> None:
        """Connect napari's active layer change to the channel label.

        Re-wires if the viewer was recreated (old Qt window was deleted).
        """
        viewer_win = self._windows.get("viewer")
        if viewer_win is None:
            return
        try:
            viewer_id = id(viewer_win.viewer)
        except Exception:
            return

        if getattr(self, "_wired_viewer_id", None) == viewer_id:
            return  # already wired to this viewer instance

        def _on_layer_selection_changed(event):
            self._update_active_channel_label()
            if hasattr(self, "_seg_panel"):
                self._seg_panel.update_channel_label()
            if hasattr(self, "_analysis_panel") and hasattr(self._analysis_panel, "_grouped_seg_panel"):
                self._analysis_panel._grouped_seg_panel.update_channels()
            # Update active segmentation/mask from napari layer selection
            self._sync_active_layers_from_viewer()

        viewer_win.viewer.layers.selection.events.active.connect(
            _on_layer_selection_changed
        )
        self._wired_viewer_id = viewer_id

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
            if viewer_empty and getattr(self, "_current_h5_path", None):
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
        from qtpy.QtWidgets import QApplication, QMessageBox, QProgressDialog

        from percell4.io.importer import import_dataset

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

    def _load_h5_into_viewer(self, h5_path: str) -> None:
        """Set the current dataset and load it into the viewer."""
        from pathlib import Path as _Path

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
        handle = DatasetHandle(path=_Path(h5_path), metadata=store.metadata)
        self.data_model.session.set_dataset(handle)

        # Update Data tab info + dropdowns
        self._update_data_tab_from_store()

        # Show viewer and populate with data
        self._show_window("viewer")
        self._populate_viewer_from_store()

        # Show filename in viewer title bar
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None:
            viewer_win.set_subtitle(Path(h5_path).name)

        self.statusBar().showMessage(f"Loaded: {Path(h5_path).name}")

    def _populate_viewer_from_store(self) -> None:
        """Populate the napari viewer from the current dataset store.

        Called when loading a dataset and when re-opening the viewer.
        """
        store = getattr(self, "_current_store", None)
        h5_path = getattr(self, "_current_h5_path", None)
        if store is None or h5_path is None:
            return

        viewer_win = self._windows.get("viewer")
        if viewer_win is None:
            return

        # Clear viewer layers
        viewer_win.clear()

        # Read and display intensity data
        try:
            with store.open_read() as s:
                intensity = s.read_array("intensity")
                meta = s.metadata
                channel_names = meta.get("channel_names", [])

                if intensity.ndim == 2:
                    name = channel_names[0] if channel_names else "Intensity"
                    viewer_win.add_image(intensity, name=name)
                elif intensity.ndim == 3 and intensity.shape[0] <= 20:
                    for i in range(intensity.shape[0]):
                        name = (
                            channel_names[i]
                            if i < len(channel_names)
                            else f"ch{i}"
                        )
                        viewer_win.add_image(intensity[i], name=name)
                else:
                    viewer_win.add_image(intensity, name="Intensity")

                # Load existing labels (skip names that are also masks)
                mask_names = set(s.list_masks())
                for label_name in s.list_labels():
                    if label_name not in mask_names:
                        labels = s.read_labels(label_name)
                        viewer_win.add_labels(labels, name=label_name)

                # Load existing masks
                for mask_name in s.list_masks():
                    mask = s.read_mask(mask_name)
                    viewer_win.add_mask(mask, name=mask_name)

        except KeyError:
            self.statusBar().showMessage(
                f"No intensity data in {Path(h5_path).name}"
            )
            return

        # Wire napari layer selection events
        self._wire_viewer_layer_selection()
        self._update_active_channel_label()
        if hasattr(self, "_analysis_panel") and hasattr(self._analysis_panel, "_grouped_seg_panel"):
            self._analysis_panel._grouped_seg_panel.update_channels()

    def _update_data_tab_from_store(self) -> None:
        """Update the Data tab info label and dropdowns from the current store."""
        if hasattr(self, "_data_panel"):
            self._data_panel.refresh_dataset_info()
            self._data_panel.refresh_active_combos()
            self._data_panel.refresh_management_combos()

    def _on_close_dataset(self) -> None:
        from percell4.application.use_cases.close_dataset import CloseDataset
        from percell4.adapters.napari_viewer import NapariViewerAdapter

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

    def _update_active_channel_label(self) -> None:
        """No-op. Channel labels are now Session-backed.

        Data tab: QComboBox populated from Session on dataset load.
        Seg panel: subscribes to state_changed and reads Session.
        Analysis panel: reads Session.active_channel on state_changed.
        """
        pass

    def _sync_active_layers_from_viewer(self) -> None:
        """When user clicks a layer in napari, update the active seg/mask in the model."""
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return

        active = viewer_win.viewer.layers.selection.active
        if active is None:
            return

        import napari
        if not isinstance(active, napari.layers.Labels):
            return

        name = active.name

        # 1. Check layer metadata first (fastest, survives renames)
        from percell4.gui.viewer import PERCELL_TYPE_KEY, LAYER_TYPE_MASK, LAYER_TYPE_SEGMENTATION
        percell_type = active.metadata.get(PERCELL_TYPE_KEY)
        if percell_type == LAYER_TYPE_MASK:
            logger.debug("_sync_active_layers: metadata → set_active_mask(%r)", name)
            self.data_model.set_active_mask(name)
            return
        if percell_type == LAYER_TYPE_SEGMENTATION:
            logger.debug("_sync_active_layers: metadata → set_active_segmentation(%r)", name)
            self.data_model.set_active_segmentation(name)
            return

        # 2. Fall back to store lookup for untagged layers
        store = getattr(self, "_current_store", None)
        if store is not None:
            mask_names = store.list_masks()
            label_names = store.list_labels()
            if name in mask_names:
                logger.debug("_sync_active_layers: store → set_active_mask(%r)", name)
                self.data_model.set_active_mask(name)
                return
            if name in label_names:
                logger.debug("_sync_active_layers: store → set_active_segmentation(%r)", name)
                self.data_model.set_active_segmentation(name)
                return

        # 3. Unknown layer — do nothing (safe default)
        logger.debug("_sync_active_layers: unknown layer %r, ignoring", name)

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

        seg_name = self.data_model.active_segmentation
        if seg_name:
            for layer in viewer_win._viewer.layers:
                if layer.name == seg_name:
                    return np.asarray(layer.data, dtype=np.int32)

        # Fallback: find a segmentation labels layer (skip mask layers)
        from percell4.gui.viewer import PERCELL_TYPE_KEY, LAYER_TYPE_MASK
        for layer in viewer_win._viewer.layers:
            if not isinstance(layer, napari.layers.Labels):
                continue
            if layer.name.startswith("_"):
                continue
            if layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK:
                continue
            return np.asarray(layer.data, dtype=np.int32)
        return None

    def _get_phasor_roi_names(self) -> dict[int, str]:
        """Get ROI label→name mapping from the phasor plot window."""
        phasor_win = self._windows.get("phasor_plot")
        if phasor_win is None:
            return {}
        return phasor_win.get_visible_roi_names()

    # ── Model state change handler ─────────────────────────────

    def _on_state_changed(self, change) -> None:
        """Handle model state changes relevant to the launcher."""
        # Filter changes handled by AnalysisPanel
        # Segmentation/mask changes handled by DataPanel
        pass  # Launcher no longer needs to handle state changes directly

    # ── Phasor plot signal handlers ─────────────────────────────

    def _on_phasor_preview(self, mask, colormap) -> None:
        """Forward phasor ROI preview mask to the viewer."""
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or not viewer_win._is_alive():
            return
        preview_name = "_phasor_roi_preview"
        try:
            layer = viewer_win._viewer.layers[preview_name]
            layer.data = mask
            layer.colormap = colormap
        except KeyError:
            viewer_win._viewer.add_labels(
                mask, name=preview_name,
                colormap=colormap, opacity=0.4,
                blending="translucent",
            )

    def _on_phasor_mask_applied(self, roi_masks) -> None:
        """Handle per-ROI phasor masks: one binary mask per ROI.

        Each entry is (roi_name, binary_mask_uint8, hex_color).
        Store-before-layer invariant preserved for each mask.
        """
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win._is_alive():
            try:
                viewer_win._viewer.layers.remove("_phasor_roi_preview")
            except ValueError:
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
            self.data_model.set_active_mask(last_name)

    # ── Analysis + FLIM handlers moved to task panels ──────
    # See: interfaces/gui/task_panels/analysis_panel.py
    # See: interfaces/gui/task_panels/flim_panel.py

    def _on_run_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Python Script", "", "Python Files (*.py);;All Files (*)"
        )
        if path:
            self.statusBar().showMessage(f"Run script: {path} — not yet implemented")

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

        dlg = ExportImagesDialog(self, store)
        if dlg.exec_() != ExportImagesDialog.Accepted:
            return

        output_folder = dlg.output_folder
        channels = dlg.selected_channels
        labels = dlg.selected_labels
        masks = dlg.selected_masks
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
            result = uc.execute(
                handle,
                ExportRequest(
                    output_folder=output_folder,
                    dataset_name=h5_name,
                    channels=channels,
                    labels=labels,
                    masks=masks,
                ),
            )
            self.statusBar().showMessage(
                f"Exported {result.exported_count} image(s) to {result.output_folder}"
            )
        except Exception as e:
            self.statusBar().showMessage(f"Export error: {e}")

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
