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
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(self._section_label("Import / Export"))

        # ── Import ──
        import_group = QGroupBox("Import")
        import_layout = QVBoxLayout(import_group)

        btn_import = QPushButton("Compress TIFF Dataset...")
        btn_import.clicked.connect(self._on_import_dataset)
        import_layout.addWidget(btn_import)

        btn_load = QPushButton("Load Dataset...")
        btn_load.clicked.connect(self._on_load_dataset)
        import_layout.addWidget(btn_load)

        btn_add_layer = QPushButton("Add Layer to Dataset...")
        btn_add_layer.clicked.connect(self._on_add_layer_to_dataset)
        import_layout.addWidget(btn_add_layer)

        btn_close = QPushButton("Close Dataset")
        btn_close.clicked.connect(self._on_close_dataset)
        import_layout.addWidget(btn_close)

        layout.addWidget(import_group)

        # ── Export ──
        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout(export_group)

        btn_export_csv = QPushButton("Export Measurements to CSV...")
        btn_export_csv.clicked.connect(self._on_export_csv)
        export_layout.addWidget(btn_export_csv)

        btn_export_images = QPushButton("Export Images...")
        btn_export_images.clicked.connect(self._on_export_images)
        export_layout.addWidget(btn_export_images)

        layout.addWidget(export_group)

        # ── Placeholders ──
        layout.addWidget(self._placeholder("Prism Export"))
        layout.addWidget(self._placeholder("Batch Export"))
        layout.addStretch()
        return panel

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
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(self._section_label("Analysis"))

        # ── Cell Filter group ──
        filter_group = QGroupBox("Cell Filter")
        filter_layout = QVBoxLayout(filter_group)

        sel_btn_row = QHBoxLayout()
        btn_clear_sel = QPushButton("Clear Selection")
        btn_clear_sel.setToolTip("Deselect all cells and restore viewer to normal")
        btn_clear_sel.clicked.connect(self._on_clear_selection)
        sel_btn_row.addWidget(btn_clear_sel)
        filter_layout.addLayout(sel_btn_row)

        filter_btn_row = QHBoxLayout()
        btn_filter = QPushButton("Filter to Selection")
        btn_filter.setToolTip("Show only the currently selected cells in all windows")
        btn_filter.clicked.connect(self._on_filter_to_selection)
        filter_btn_row.addWidget(btn_filter)

        self._clear_filter_btn = QPushButton("Clear Filter")
        self._clear_filter_btn.setEnabled(False)
        self._clear_filter_btn.clicked.connect(self._on_clear_filter)
        filter_btn_row.addWidget(self._clear_filter_btn)
        filter_layout.addLayout(filter_btn_row)

        self._filter_status_label = QLabel("No filter active")
        self._filter_status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        filter_layout.addWidget(self._filter_status_label)

        layout.addWidget(filter_group)

        # ── Whole Field Thresholding group ──
        thresh_group = QGroupBox("Whole Field Thresholding")
        thresh_layout = QVBoxLayout(thresh_group)

        # Channel display (from napari active layer)
        thresh_chan_row = QHBoxLayout()
        thresh_chan_row.addWidget(QLabel("Channel:"))
        self._thresh_channel_label = QLabel("None selected")
        self._thresh_channel_label.setStyleSheet(
            "color: #4ea8de; font-weight: bold;"
        )
        thresh_chan_row.addWidget(self._thresh_channel_label)
        thresh_chan_row.addStretch()
        thresh_layout.addLayout(thresh_chan_row)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:"))
        self._thresh_method = QComboBox()
        self._thresh_method.addItems(["Otsu", "Triangle", "Li", "Adaptive", "Manual"])
        method_row.addWidget(self._thresh_method)
        thresh_layout.addLayout(method_row)

        # Threshold value display (auto-computed or manual)
        val_row = QHBoxLayout()
        val_row.addWidget(QLabel("Threshold:"))
        self._thresh_value_spin = QDoubleSpinBox()
        self._thresh_value_spin.setRange(0.0, 100000.0)
        self._thresh_value_spin.setValue(0.0)
        self._thresh_value_spin.setDecimals(1)
        self._thresh_value_spin.setToolTip(
            "Auto-computed threshold value. Edit to override manually."
        )
        val_row.addWidget(self._thresh_value_spin)
        thresh_layout.addLayout(val_row)

        sigma_row = QHBoxLayout()
        sigma_row.addWidget(QLabel("Gaussian σ:"))
        self._thresh_sigma = QDoubleSpinBox()
        self._thresh_sigma.setRange(0.0, 20.0)
        self._thresh_sigma.setValue(0.0)
        self._thresh_sigma.setSpecialValueText("None")
        self._thresh_sigma.setSingleStep(0.5)
        sigma_row.addWidget(self._thresh_sigma)
        thresh_layout.addLayout(sigma_row)

        thresh_layout.addWidget(QLabel(
            "1. Preview computes threshold and shows mask.\n"
            "2. Draw ROI in viewer to recalculate from a region.\n"
            "3. Accept to save the mask."
        ))

        btn_preview = QPushButton("Preview Threshold")
        btn_preview.setToolTip(
            "Compute threshold and show preview mask in viewer.\n"
            "Draw/move an ROI to recalculate from that region."
        )
        btn_preview.clicked.connect(self._on_threshold_preview)
        thresh_layout.addWidget(btn_preview)

        btn_accept = QPushButton("Accept && Save Mask to HDF5")
        btn_accept.clicked.connect(self._on_threshold_accept)
        thresh_layout.addWidget(btn_accept)

        # Threshold result display
        self._thresh_result_label = QLabel("")
        self._thresh_result_label.setWordWrap(True)
        thresh_layout.addWidget(self._thresh_result_label)

        layout.addWidget(thresh_group)

        # ── Grouped Thresholding ──
        from percell4.gui.grouped_seg_panel import GroupedSegPanel

        self._grouped_seg_panel = GroupedSegPanel(
            self.data_model, launcher=self
        )
        grouped_group = QGroupBox("Grouped Thresholding")
        grouped_layout = QVBoxLayout(grouped_group)
        grouped_layout.addWidget(self._grouped_seg_panel)
        layout.addWidget(grouped_group)

        # ── Measurements group ──
        meas_group = QGroupBox("Measurements")
        meas_layout = QVBoxLayout(meas_group)

        meas_layout.addWidget(QLabel(
            "Measures per-cell metrics using the active\n"
            "channel, segmentation, and mask from Data tab."
        ))

        self._meas_result_label = QLabel("")
        self._meas_result_label.setWordWrap(True)
        meas_layout.addWidget(self._meas_result_label)

        btn_measure = QPushButton("Measure Cells")
        btn_measure.clicked.connect(self._on_measure_cells)
        meas_layout.addWidget(btn_measure)

        btn_row = QHBoxLayout()
        btn_plot = QPushButton("Open Data Plot")
        btn_plot.clicked.connect(lambda: self._show_window("data_plot"))
        btn_row.addWidget(btn_plot)

        btn_table = QPushButton("Open Cell Table")
        btn_table.clicked.connect(lambda: self._show_window("cell_table"))
        btn_row.addWidget(btn_table)
        meas_layout.addLayout(btn_row)

        layout.addWidget(meas_group)

        # ── Particle Analysis ──
        particle_group = QGroupBox("Particle Analysis")
        particle_layout = QVBoxLayout(particle_group)

        particle_layout.addWidget(QLabel(
            "Counts particles within each cell using\n"
            "the active mask as the particle source."
        ))

        min_area_row = QHBoxLayout()
        min_area_row.addWidget(QLabel("Min particle area (px):"))
        self._particle_min_area = QSpinBox()
        self._particle_min_area.setRange(1, 10000)
        self._particle_min_area.setValue(1)
        min_area_row.addWidget(self._particle_min_area)
        particle_layout.addLayout(min_area_row)

        btn_particle = QPushButton("Analyze Particles")
        btn_particle.clicked.connect(self._on_analyze_particles)
        particle_layout.addWidget(btn_particle)

        btn_export_particle = QPushButton("Export Particle Data to CSV...")
        btn_export_particle.clicked.connect(self._on_export_particle_csv)
        particle_layout.addWidget(btn_export_particle)

        self._particle_result_label = QLabel("")
        self._particle_result_label.setWordWrap(True)
        particle_layout.addWidget(self._particle_result_label)

        layout.addWidget(particle_group)

        # ── Placeholders ──
        layout.addWidget(self._placeholder("Image Calculator"))
        layout.addWidget(self._placeholder("Background Subtraction"))

        layout.addStretch()
        return panel

    def _create_flim_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(self._section_label("FLIM"))

        # ── Phasor Analysis ──
        phasor_group = QGroupBox("Phasor Analysis")
        phasor_layout = QVBoxLayout(phasor_group)

        harm_row = QHBoxLayout()
        harm_row.addWidget(QLabel("Harmonic:"))
        self._phasor_harmonic = QComboBox()
        self._phasor_harmonic.addItems(["1", "2", "3"])
        harm_row.addWidget(self._phasor_harmonic)
        phasor_layout.addLayout(harm_row)

        btn_phasor = QPushButton("Compute Phasor")
        btn_phasor.clicked.connect(self._on_compute_phasor)
        phasor_layout.addWidget(btn_phasor)

        btn_open_phasor = QPushButton("Open Phasor Plot")
        btn_open_phasor.clicked.connect(lambda: self._show_window("phasor_plot"))
        phasor_layout.addWidget(btn_open_phasor)

        layout.addWidget(phasor_group)

        # ── Wavelet Filter ──
        wavelet_group = QGroupBox("Wavelet Filter")
        wavelet_layout = QVBoxLayout(wavelet_group)

        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("Filter Level:"))
        self._wavelet_level = QSpinBox()
        self._wavelet_level.setRange(1, 15)
        self._wavelet_level.setValue(9)
        level_row.addWidget(self._wavelet_level)
        wavelet_layout.addLayout(level_row)

        btn_wavelet = QPushButton("Apply Wavelet Filter")
        btn_wavelet.clicked.connect(self._on_apply_wavelet)
        wavelet_layout.addWidget(btn_wavelet)

        layout.addWidget(wavelet_group)

        # ── Lifetime ──
        lifetime_group = QGroupBox("Lifetime Map")
        lifetime_layout = QVBoxLayout(lifetime_group)
        btn_lifetime = QPushButton("Compute Lifetime")
        btn_lifetime.clicked.connect(self._on_compute_lifetime)
        lifetime_layout.addWidget(btn_lifetime)
        layout.addWidget(lifetime_group)

        # ── Placeholders ──
        layout.addWidget(self._placeholder("FRET Analysis"))
        layout.addWidget(self._placeholder("Multi-Harmonic"))

        layout.addStretch()
        return panel

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
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)

        layout.addWidget(self._section_label("Data"))

        # ── Active layers ──
        layers_group = QGroupBox("Active Layers")
        layers_layout = QVBoxLayout(layers_group)

        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Active Channel:"))
        self._data_channel_label = QLabel("None selected")
        self._data_channel_label.setStyleSheet(
            "color: #4ea8de; font-weight: bold;"
        )
        chan_row.addWidget(self._data_channel_label)
        chan_row.addStretch()
        layers_layout.addLayout(chan_row)

        seg_row = QHBoxLayout()
        seg_row.addWidget(QLabel("Active Segmentation:"))
        self._active_seg_combo = QComboBox()
        self._active_seg_combo.setPlaceholderText("None")
        self._active_seg_combo.currentTextChanged.connect(
            self._on_active_seg_combo_changed
        )
        seg_row.addWidget(self._active_seg_combo)
        layers_layout.addLayout(seg_row)

        mask_row = QHBoxLayout()
        mask_row.addWidget(QLabel("Active Mask:"))
        self._active_mask_combo = QComboBox()
        self._active_mask_combo.setPlaceholderText("None")
        self._active_mask_combo.currentTextChanged.connect(
            self._on_active_mask_combo_changed
        )
        mask_row.addWidget(self._active_mask_combo)
        layers_layout.addLayout(mask_row)

        # Model state changes are handled by _on_state_changed (unified handler)

        layout.addWidget(layers_group)

        # ── Layer Management ──
        mgmt_group = QGroupBox("Layer Management")
        mgmt_layout = QVBoxLayout(mgmt_group)

        # Segmentation management
        mgmt_layout.addWidget(QLabel("Segmentations:"))
        seg_mgmt_row = QHBoxLayout()
        self._mgmt_seg_combo = QComboBox()
        self._mgmt_seg_combo.setPlaceholderText("Select segmentation")
        seg_mgmt_row.addWidget(self._mgmt_seg_combo)
        btn_rename_seg = QPushButton("Rename")
        btn_rename_seg.clicked.connect(lambda: self._on_rename_layer("labels"))
        seg_mgmt_row.addWidget(btn_rename_seg)
        btn_delete_seg = QPushButton("Delete")
        btn_delete_seg.clicked.connect(lambda: self._on_delete_layer("labels"))
        seg_mgmt_row.addWidget(btn_delete_seg)
        mgmt_layout.addLayout(seg_mgmt_row)

        # Mask management
        mgmt_layout.addWidget(QLabel("Masks:"))
        mask_mgmt_row = QHBoxLayout()
        self._mgmt_mask_combo = QComboBox()
        self._mgmt_mask_combo.setPlaceholderText("Select mask")
        mask_mgmt_row.addWidget(self._mgmt_mask_combo)
        btn_rename_mask = QPushButton("Rename")
        btn_rename_mask.clicked.connect(lambda: self._on_rename_layer("masks"))
        mask_mgmt_row.addWidget(btn_rename_mask)
        btn_delete_mask = QPushButton("Delete")
        btn_delete_mask.clicked.connect(lambda: self._on_delete_layer("masks"))
        mask_mgmt_row.addWidget(btn_delete_mask)
        mgmt_layout.addLayout(mask_mgmt_row)

        # Channel management
        mgmt_layout.addWidget(QLabel("Channels:"))
        chan_mgmt_row = QHBoxLayout()
        self._mgmt_chan_combo = QComboBox()
        self._mgmt_chan_combo.setPlaceholderText("Select channel")
        chan_mgmt_row.addWidget(self._mgmt_chan_combo)
        btn_rename_chan = QPushButton("Rename")
        btn_rename_chan.clicked.connect(self._on_rename_channel)
        chan_mgmt_row.addWidget(btn_rename_chan)
        btn_delete_chan = QPushButton("Delete")
        btn_delete_chan.clicked.connect(self._on_delete_channel)
        chan_mgmt_row.addWidget(btn_delete_chan)
        mgmt_layout.addLayout(chan_mgmt_row)

        layout.addWidget(mgmt_group)

        # ── Dataset Info ──
        info_group = QGroupBox("Dataset Info")
        info_layout = QVBoxLayout(info_group)
        self._info_label = QLabel("No dataset loaded")
        self._info_label.setWordWrap(True)
        info_layout.addWidget(self._info_label)
        layout.addWidget(info_group)

        # ── Placeholders ──
        layout.addWidget(self._placeholder("Project Browser"))
        layout.addStretch()
        return panel

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
            if hasattr(self, "_grouped_seg_panel"):
                self._grouped_seg_panel.update_channels()
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
        from percell4.store import DatasetStore

        store = DatasetStore(h5_path)
        if not store.exists():
            self.statusBar().showMessage(f"File not found: {h5_path}")
            return

        # Set as current dataset for the entire app
        self._current_store = store
        self._current_h5_path = h5_path

        # Clear previous model state
        self.data_model.clear()

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
        if hasattr(self, "_grouped_seg_panel"):
            self._grouped_seg_panel.update_channels()

    def _update_data_tab_from_store(self) -> None:
        """Update the Data tab info label and dropdowns from the current store."""
        store = getattr(self, "_current_store", None)
        h5_path = getattr(self, "_current_h5_path", None)

        if store is None or h5_path is None:
            if hasattr(self, "_info_label"):
                self._info_label.setText("No dataset loaded")
            return

        # Read shape for info display
        try:
            with store.open_read() as s:
                intensity = s.read_array("intensity")
                shape = intensity.shape
        except Exception:
            shape = "unknown"

        if hasattr(self, "_info_label"):
            n_labels = len(store.list_labels())
            n_masks = len(store.list_masks())
            self._info_label.setText(
                f"File: {Path(h5_path).name}\n"
                f"Shape: {shape}\n"
                f"Labels: {n_labels}  |  Masks: {n_masks}"
            )

        # Populate active layers dropdowns (exclude masks from segmentation list)
        mask_set = set(store.list_masks())
        if hasattr(self, "_active_seg_combo"):
            self._active_seg_combo.clear()
            for label_name in store.list_labels():
                if label_name not in mask_set:
                    self._active_seg_combo.addItem(label_name)

        if hasattr(self, "_active_mask_combo"):
            self._active_mask_combo.clear()
            for mask_name in store.list_masks():
                self._active_mask_combo.addItem(mask_name)

        # Populate management combos
        self._refresh_management_combos()

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
        if hasattr(self, "_info_label"):
            self._info_label.setText("No dataset loaded")
        if hasattr(self, "_active_seg_combo"):
            self._active_seg_combo.clear()
        if hasattr(self, "_active_mask_combo"):
            self._active_mask_combo.clear()
        self.statusBar().showMessage("Dataset closed")

    def _update_active_channel_label(self) -> None:
        """Update channel labels in any panels that track the active layer."""
        if hasattr(self, "_seg_panel"):
            self._seg_panel.update_channel_label()

        # Get the active channel name from the viewer
        channel_name = "None selected"
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win.viewer is not None:
            active = viewer_win.viewer.layers.selection.active
            if active is not None and active.__class__.__name__ == "Image":
                channel_name = active.name

        # Update all channel labels
        if hasattr(self, "_thresh_channel_label"):
            self._thresh_channel_label.setText(channel_name)
        if hasattr(self, "_data_channel_label"):
            self._data_channel_label.setText(channel_name)

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
        if change.filter:
            self._on_filter_state_changed()
        if change.segmentation:
            name = self.data_model.active_segmentation
            self._on_model_active_seg_changed(name)
        if change.mask:
            name = self.data_model.active_mask
            self._on_model_active_mask_changed(name)

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

    def _on_phasor_mask_applied(self, mask, color_dict, mask_name) -> None:
        """Handle finalized phasor mask: remove preview, add mask, save to HDF5."""
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win._is_alive():
            # Remove preview layer and stop preview timer to prevent stale
            # preview from reappearing after apply
            try:
                viewer_win._viewer.layers.remove("_phasor_roi_preview")
            except ValueError:
                pass
            if hasattr(self, "_preview_timer"):
                self._preview_timer.stop()

        # Write to store BEFORE adding layer — sync may fire during add_mask
        # and needs to find the mask in the store
        store = getattr(self, "_current_store", None)
        if store is not None:
            store.write_mask(mask_name, mask)

        if viewer_win is not None and viewer_win._is_alive():
            viewer_win.add_mask(mask, name=mask_name, color_dict=color_dict)

        self.data_model.set_active_mask(mask_name)

    def _on_clear_selection(self) -> None:
        """Deselect all cells — restore viewer to normal display."""
        self.data_model.set_selection([])

    def _on_filter_to_selection(self) -> None:
        """Filter all windows to show only the currently selected cells."""
        selected = self.data_model.selected_ids
        if not selected:
            self.statusBar().showMessage("No cells selected to filter", 3000)
            return
        self.data_model.set_filter(list(selected))

    def _on_clear_filter(self) -> None:
        """Remove cell filter — restore all windows to full data."""
        self.data_model.set_filter(None)

    def _on_filter_state_changed(self) -> None:
        """Update filter status display in the Analysis panel."""
        if self.data_model.is_filtered:
            n_filtered = len(self.data_model.filtered_df)
            n_total = len(self.data_model.df)
            self._filter_status_label.setText(
                f"Showing {n_filtered} of {n_total} cells"
            )
            self._filter_status_label.setStyleSheet(f"color: {theme.ACCENT}; font-weight: bold;")
            self._clear_filter_btn.setEnabled(True)
        else:
            self._filter_status_label.setText("No filter active")
            self._filter_status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            self._clear_filter_btn.setEnabled(False)

    def _on_threshold_preview(self) -> None:
        """Compute threshold and show a live preview mask in the viewer.

        Workflow (matching PerCell3):
        1. Compute auto-threshold from the full image (or ROI if drawn)
        2. Create a preview mask layer showing what's above threshold
        3. Add a shapes layer for ROI drawing
        4. When the user draws/moves a ROI, recalculate threshold from
           that region and update the preview mask on the entire image
        """
        import numpy as np

        viewer_win = self._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self.statusBar().showMessage("Open the viewer first")
            return

        # Get the active image
        active = viewer_win.viewer.layers.selection.active
        if active is None or active.__class__.__name__ != "Image":
            for layer in viewer_win.viewer.layers:
                if layer.__class__.__name__ == "Image":
                    active = layer
                    break
            else:
                self.statusBar().showMessage("No image loaded")
                return

        from percell4.measure.thresholding import (
            THRESHOLD_METHODS,
            apply_gaussian_smoothing,
        )

        image = active.data.astype(np.float32)
        channel_name = active.name

        # Apply smoothing
        sigma = self._thresh_sigma.value()
        if sigma > 0:
            image = apply_gaussian_smoothing(image, sigma)

        # Store the working image for ROI updates
        self._thresh_working_image = image
        self._thresh_channel_name = channel_name

        # Compute initial threshold from full image
        method = self._thresh_method.currentText().lower()
        if method == "manual":
            value = self._thresh_value_spin.value()
            if value <= 0:
                self.statusBar().showMessage("Set a threshold value > 0")
                return
        elif method in THRESHOLD_METHODS:
            _, value = THRESHOLD_METHODS[method](image)
        else:
            self.statusBar().showMessage(f"Unknown method: {method}")
            return

        self._thresh_value_spin.setValue(value)

        # Create preview mask (applied to ENTIRE image)
        mask = (image > value).astype(np.uint8)

        # Remove old preview and ROI layers
        for name in ("_threshold_preview", "_threshold_roi"):
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)

        # Add preview mask layer (yellow)
        from napari.utils.colormaps import DirectLabelColormap

        yellow_cmap = DirectLabelColormap(
            color_dict={0: "transparent", 1: "yellow", None: "transparent"},
        )
        viewer_win.viewer.add_labels(
            mask,
            name="_threshold_preview",
            opacity=0.5,
            blending="translucent",
            colormap=yellow_cmap,
        )

        # Add shapes layer for ROI drawing
        viewer_win.viewer.add_shapes(
            [],
            shape_type="rectangle",
            name="_threshold_roi",
            edge_color="yellow",
            edge_width=2,
            face_color=[1, 1, 0, 0.1],
        )

        # Wire ROI changes to recalculate threshold
        for layer in viewer_win.viewer.layers:
            if layer.name == "_threshold_roi":
                layer.events.data.connect(self._on_threshold_roi_changed)
                break

        self._update_thresh_stats(mask, value)
        viewer_win.show()
        self.statusBar().showMessage(
            f"Preview: {method} threshold = {value:.1f}. "
            "Draw a rectangle ROI to recalculate from a region."
        )

    def _on_threshold_roi_changed(self, event=None) -> None:
        """ROI changed in viewer — recalculate threshold from ROI region,
        then update the preview mask on the entire image."""
        import numpy as np

        viewer_win = self._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return

        image = getattr(self, "_thresh_working_image", None)
        if image is None:
            return

        # Extract ROI region
        roi_image = None
        for layer in viewer_win.viewer.layers:
            if layer.name == "_threshold_roi" and hasattr(layer, "data"):
                if len(layer.data) > 0:
                    coords = np.array(layer.data[0])
                    y_min = max(0, int(coords[:, 0].min()))
                    y_max = min(image.shape[0], int(coords[:, 0].max()))
                    x_min = max(0, int(coords[:, 1].min()))
                    x_max = min(image.shape[1], int(coords[:, 1].max()))
                    if y_max > y_min and x_max > x_min:
                        roi_image = image[y_min:y_max, x_min:x_max]
                break

        if roi_image is None or roi_image.size == 0:
            return

        from percell4.measure.thresholding import THRESHOLD_METHODS

        # Recalculate threshold from ROI only
        method = self._thresh_method.currentText().lower()
        if method == "manual":
            return  # manual doesn't recalculate from ROI
        if method not in THRESHOLD_METHODS:
            return

        _, value = THRESHOLD_METHODS[method](roi_image)
        self._thresh_value_spin.setValue(value)

        # Apply new threshold to ENTIRE image (not just ROI)
        mask = (image > value).astype(np.uint8)

        # Update preview layer
        for layer in viewer_win.viewer.layers:
            if layer.name == "_threshold_preview":
                layer.data = mask
                layer.refresh()
                break

        self._update_thresh_stats(mask, value, from_roi=True)

    def _update_thresh_stats(
        self, mask, value: float, from_roi: bool = False
    ) -> None:
        """Update the threshold result label with stats."""
        n_pos = int(mask.sum())
        n_total = mask.size
        pct = 100.0 * n_pos / n_total if n_total > 0 else 0
        roi_note = " (from ROI)" if from_roi else ""
        self._thresh_result_label.setText(
            f"Threshold: {value:.1f}{roi_note}\n"
            f"Positive: {n_pos:,} / {n_total:,} px ({pct:.1f}%)"
        )
        self._thresh_result_label.setStyleSheet(f"color: {theme.WARNING};")

    def _on_threshold_accept(self) -> None:
        """Accept the current preview threshold and save the mask to HDF5."""
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self.statusBar().showMessage("No preview to accept")
            return

        image = getattr(self, "_thresh_working_image", None)
        channel_name = getattr(self, "_thresh_channel_name", "unknown")
        if image is None:
            self.statusBar().showMessage("Run Preview first")
            return

        value = self._thresh_value_spin.value()
        method = self._thresh_method.currentText().lower()

        # Remove preview and ROI layers
        for name in ("_threshold_preview", "_threshold_roi"):
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)

        try:
            from percell4.application.use_cases.accept_threshold import AcceptThreshold
            from percell4.adapters.napari_viewer import NapariViewerAdapter

            viewer_adapter = NapariViewerAdapter(viewer_win)
            uc = AcceptThreshold(self._repo, viewer_adapter, self.data_model.session)
            result = uc.execute(image, value, method, channel_name)
        except ValueError as e:
            self.statusBar().showMessage(str(e))
            return

        # Add mask layer to viewer (use case wrote to store + updated session)
        viewer_win.add_mask(
            (image > value).astype("uint8"), name=result.mask_name
        )

        pct = 100.0 * result.n_positive / result.n_total if result.n_total > 0 else 0
        self._thresh_result_label.setText(
            f"Saved: {result.mask_name}\n"
            f"Threshold: {value:.1f} | {result.n_positive:,} / {result.n_total:,} px ({pct:.1f}%)"
        )
        self._thresh_result_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self.statusBar().showMessage(f"Saved mask '{result.mask_name}' (threshold {value:.1f})")

        # Clean up working state
        self._thresh_working_image = None
        self._thresh_channel_name = None

    def _show_metric_config_dialog(self) -> list[str] | None:
        """Show metric selection dialog. Returns selected metric names, or None if cancelled."""
        from percell4.measure.metrics import BUILTIN_METRICS

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Metrics")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose which metrics to compute:"))

        selected = self._load_selected_metrics()
        checkboxes: dict[str, QCheckBox] = {}
        for name in BUILTIN_METRICS:
            cb = QCheckBox(name.replace("_", " ").title())
            cb.setChecked(name in selected)
            checkboxes[name] = cb
            layout.addWidget(cb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        result = [name for name, cb in checkboxes.items() if cb.isChecked()]
        if not result:
            self.statusBar().showMessage("No metrics selected")
            return None
        self._save_selected_metrics(result)
        return result

    @staticmethod
    def _load_selected_metrics() -> list[str]:
        """Load selected metrics from QSettings (type-safe)."""
        from percell4.measure.metrics import BUILTIN_METRICS
        settings = QSettings("LeeLabPerCell4", "PerCell4")
        raw = settings.value("metrics/selected", defaultValue=None)
        if raw is None:
            return list(BUILTIN_METRICS.keys())
        if isinstance(raw, str):
            raw = [raw]  # QSettings quirk: single-item list → string
        return [m for m in raw if m in BUILTIN_METRICS]

    @staticmethod
    def _save_selected_metrics(metrics: list[str]) -> None:
        """Save selected metrics to QSettings."""
        settings = QSettings("LeeLabPerCell4", "PerCell4")
        settings.setValue("metrics/selected", metrics)

    @staticmethod
    def _merge_group_columns(df, store) -> "pd.DataFrame":
        """Merge stored group columns back into a measurements DataFrame.

        Group columns (e.g., group_GFP_mean) are stored separately at
        /groups/<mask_name> in HDF5 so they survive re-measurement.
        """
        if store is None or df is None or df.empty:
            return df
        try:
            with store.open_read() as s:
                # Check for /groups/ datasets
                import h5py
                if "groups" not in s._f:
                    return df
                for name in s._f["groups"]:
                    group_df = s.read_dataframe(f"groups/{name}")
                    if group_df is not None and not group_df.empty:
                        # Merge on label column
                        for col in group_df.columns:
                            if col != "label" and col not in df.columns:
                                label_to_group = dict(
                                    zip(group_df["label"], group_df[col])
                                )
                                df = df.assign(
                                    **{col: df["label"].map(label_to_group)}
                                )
        except Exception:
            pass  # If groups don't exist yet, that's fine
        return df

    def _on_measure_cells(self) -> None:
        """Measure per-cell metrics using active channel, segmentation, and mask."""
        # UI: get metric selection from dialog
        selected_metrics = self._show_metric_config_dialog()
        if selected_metrics is None:
            return

        if self.data_model.session.dataset is None:
            self.statusBar().showMessage("No dataset loaded")
            return

        self.statusBar().showMessage("Measuring cells...")

        try:
            from percell4.application.use_cases.measure_cells import MeasureCells

            roi_names = self._get_phasor_roi_names() or None
            uc = MeasureCells(self._repo, self.data_model.session)
            df = uc.execute(metrics=selected_metrics, roi_names=roi_names)
        except ValueError as e:
            self.statusBar().showMessage(str(e))
            return
        except Exception as e:
            self.statusBar().showMessage(f"Measurement error: {e}")
            return

        n_cells = len(df)
        n_cols = len(df.columns)
        seg_name = self.data_model.active_segmentation
        mask_name = self.data_model.active_mask
        mask_note = f" (mask: {mask_name})" if mask_name else ""
        self._meas_result_label.setText(
            f"Measured {n_cells} cells across multiple channel(s)\n"
            f"{n_cols} columns | seg: {seg_name}{mask_note}"
        )
        self._meas_result_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self.statusBar().showMessage(
            f"Measured {n_cells} cells, {n_cols} columns"
        )

    def _on_analyze_particles(self) -> None:
        """Analyze particles within each cell using the active mask."""
        min_area = self._particle_min_area.value()
        self.statusBar().showMessage("Analyzing particles...")

        try:
            from percell4.application.use_cases.analyze_particles import AnalyzeParticles

            uc = AnalyzeParticles(self._repo, self.data_model.session)
            result = uc.execute(min_area=min_area)
        except ValueError as e:
            self.statusBar().showMessage(str(e))
            return
        except Exception as e:
            self.statusBar().showMessage(f"Particle analysis error: {e}")
            return

        # Store detail DataFrame for export
        self._last_particle_df = result.summary_df
        self._last_particle_detail_df = result.detail_df

        mask_name = self.data_model.session.active_mask or "unknown"
        self._particle_result_label.setText(
            f"{result.total_particles} particles in {result.n_cells} cells\n"
            f"mask: {mask_name} | min area: {min_area} px"
        )
        self._particle_result_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self.statusBar().showMessage(
            f"Found {result.total_particles} particles across {result.n_cells} cells"
        )

    def _on_export_particle_csv(self) -> None:
        """Export per-particle detail data to CSV (one row per particle)."""
        from qtpy.QtWidgets import QFileDialog

        detail_df = getattr(self, "_last_particle_detail_df", None)
        if detail_df is None or detail_df.empty:
            self.statusBar().showMessage("No particle data — run Analyze Particles first")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Particle Data", "particles.csv", "CSV (*.csv)"
        )
        if path:
            detail_df.to_csv(path, index=False)
            self.statusBar().showMessage(f"Exported particle data to {path}")

    def _on_compute_phasor(self) -> None:
        """Compute phasor G/S from TCSPC decay data for the active channel."""
        import numpy as np

        # Gather UI inputs: active channel from viewer
        viewer_win = self._windows.get("viewer")
        active_channel = None
        if viewer_win is not None and viewer_win.viewer is not None:
            active = viewer_win.viewer.layers.selection.active
            if active is not None and active.__class__.__name__ == "Image":
                active_channel = active.name

        if active_channel is None:
            self.statusBar().showMessage("Select a channel in the viewer first")
            return

        harmonic = int(self._phasor_harmonic.currentText())
        self.statusBar().showMessage(f"Computing phasor for {active_channel}...")

        # Delegate computation + store writes to use case
        try:
            from percell4.application.use_cases.compute_phasor import ComputePhasor

            uc = ComputePhasor(self._repo, self.data_model.session)
            result = uc.execute(channel=active_channel, harmonic=harmonic)
        except ValueError as e:
            self.statusBar().showMessage(str(e))
            return
        except Exception as e:
            self.statusBar().showMessage(f"Phasor computation error: {e}")
            return

        # Read intensity for weighted histogram (UI concern, not use case)
        handle = self.data_model.session.dataset
        phasor_intensity = None
        if handle is not None:
            try:
                intensity_data = self._repo.read_array(handle, "intensity")
                meta = handle.metadata
                if intensity_data.ndim == 3:
                    ch_names = list(meta.get("channel_names", []))
                    if active_channel in ch_names:
                        phasor_intensity = intensity_data[ch_names.index(active_channel)]
                    else:
                        phasor_intensity = intensity_data[0]
                else:
                    phasor_intensity = intensity_data
            except KeyError:
                phasor_intensity = None

        # Open and populate phasor plot
        seg_labels = self._get_active_seg_labels()
        self._show_window("phasor_plot")
        phasor_win = self._windows.get("phasor_plot")
        if phasor_win is not None:
            phasor_win.set_phasor_data(
                result.g_map, result.s_map,
                intensity=phasor_intensity, labels=seg_labels,
            )

        freq = handle.metadata.get("flim_frequency_mhz", "unknown") if handle else "unknown"
        self.statusBar().showMessage(
            f"Phasor computed: {result.n_valid:,} valid pixels | "
            f"channel: {active_channel} | harmonic: {harmonic} | freq: {freq} MHz"
        )

    def _on_apply_wavelet(self) -> None:
        """Apply DTCWT wavelet denoising to the active channel's phasor data."""
        import numpy as np

        # Gather UI inputs: active channel from viewer
        viewer_win = self._windows.get("viewer")
        active_channel = None
        if viewer_win is not None and viewer_win.viewer is not None:
            active = viewer_win.viewer.layers.selection.active
            if active is not None and active.__class__.__name__ == "Image":
                active_channel = active.name

        if active_channel is None:
            self.statusBar().showMessage("Select a channel in the viewer first")
            return

        filter_level = self._wavelet_level.value()
        self.statusBar().showMessage(
            f"Applying wavelet filter (level {filter_level}) to {active_channel}..."
        )

        # Delegate computation + store writes to use case
        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from percell4.application.use_cases.apply_wavelet import ApplyWavelet

            uc = ApplyWavelet(self._repo, self.data_model.session)
            result = uc.execute(channel=active_channel, filter_level=filter_level)
        except ImportError:
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(
                "dtcwt package required. Install: pip install 'percell4[flim]'"
            )
            return
        except ValueError as e:
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(str(e))
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"Wavelet filter error: {e}")
            return

        QApplication.restoreOverrideCursor()

        # Read intensity for phasor plot display (UI concern)
        handle = self.data_model.session.dataset
        intensity = None
        if handle is not None:
            try:
                intensity_data = self._repo.read_array(handle, "intensity")
                if intensity_data.ndim == 3:
                    ch_names = list(handle.metadata.get("channel_names", []))
                    if active_channel in ch_names:
                        intensity = intensity_data[ch_names.index(active_channel)]
                    else:
                        intensity = intensity_data[0]
                else:
                    intensity = intensity_data
            except KeyError:
                pass

        # Read unfiltered phasor for overlay in phasor plot
        g_unfiltered = s_unfiltered = None
        if handle is not None:
            try:
                g_unfiltered = self._repo.read_array(handle, f"phasor/{active_channel}/g")
                s_unfiltered = self._repo.read_array(handle, f"phasor/{active_channel}/s")
            except KeyError:
                pass

        # Update phasor plot with filtered data
        seg_labels = self._get_active_seg_labels()
        phasor_win = self._windows.get("phasor_plot")
        if phasor_win is not None:
            phasor_win.set_phasor_data(
                result.g_filtered, result.s_filtered,
                intensity=intensity.astype(np.float32) if intensity is not None else None,
                g_unfiltered=g_unfiltered, s_unfiltered=s_unfiltered,
                labels=seg_labels,
            )

        self.statusBar().showMessage(
            f"Wavelet filter applied: level {filter_level} | "
            f"{result.n_valid:,} valid pixels | channel: {active_channel}"
        )

    def _on_compute_lifetime(self) -> None:
        """Compute lifetime map from phasor data for the active channel."""
        # Gather UI inputs: active channel from viewer
        viewer_win = self._windows.get("viewer")
        active_channel = None
        if viewer_win is not None and viewer_win.viewer is not None:
            active = viewer_win.viewer.layers.selection.active
            if active is not None and active.__class__.__name__ == "Image":
                active_channel = active.name

        if active_channel is None:
            self.statusBar().showMessage("Select a channel in the viewer first")
            return

        # Delegate computation + store writes to use case
        try:
            from percell4.application.use_cases.compute_lifetime import ComputeLifetime

            uc = ComputeLifetime(self._repo, self.data_model.session)
            result = uc.execute(channel=active_channel)
        except ValueError as e:
            self.statusBar().showMessage(str(e))
            return
        except Exception as e:
            self.statusBar().showMessage(f"Lifetime computation error: {e}")
            return

        # Add lifetime layer to viewer
        if viewer_win is not None:
            viewer_win.viewer.add_image(
                result.lifetime,
                name=f"Lifetime ({active_channel})",
                colormap="turbo",
                blending="additive",
            )

        if result.mean_tau is not None:
            self.statusBar().showMessage(
                f"Lifetime ({result.source}): mean={result.mean_tau:.2f} ns | "
                f"channel: {active_channel} | freq: {result.frequency_mhz} MHz"
            )
        else:
            self.statusBar().showMessage("Lifetime: no valid pixels")

    def _on_run_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Python Script", "", "Python Files (*.py);;All Files (*)"
        )
        if path:
            self.statusBar().showMessage(f"Run script: {path} — not yet implemented")

    # ── Layer management ────────────────────────────────────────

    def _refresh_management_combos(self) -> None:
        """Refresh all management dropdowns from the current store.

        The management combos show ALL entries (including stale mask data
        under /labels/) so users can delete legacy entries.
        """
        store = getattr(self, "_current_store", None)

        if hasattr(self, "_mgmt_seg_combo"):
            self._mgmt_seg_combo.clear()
            if store is not None:
                for name in store.list_labels():
                    self._mgmt_seg_combo.addItem(name)

        if hasattr(self, "_mgmt_mask_combo"):
            self._mgmt_mask_combo.clear()
            if store is not None:
                for name in store.list_masks():
                    self._mgmt_mask_combo.addItem(name)

        if hasattr(self, "_mgmt_chan_combo"):
            self._mgmt_chan_combo.clear()
            viewer_win = self._windows.get("viewer")
            if viewer_win is not None and viewer_win.viewer is not None:
                for layer in viewer_win.viewer.layers:
                    if layer.__class__.__name__ == "Image":
                        self._mgmt_chan_combo.addItem(layer.name)

    def _on_rename_layer(self, prefix: str) -> None:
        """Rename a segmentation or mask in HDF5 and viewer."""
        combo = self._mgmt_seg_combo if prefix == "labels" else self._mgmt_mask_combo
        old_name = combo.currentText()
        if not old_name:
            self.statusBar().showMessage("Nothing selected to rename")
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename", f"New name for '{old_name}':", text=old_name
        )
        if not ok or not new_name or new_name == old_name:
            return

        store = getattr(self, "_current_store", None)
        if store is not None:
            try:
                store.rename_item(f"{prefix}/{old_name}", f"{prefix}/{new_name}")
            except ValueError as e:
                self.statusBar().showMessage(str(e))
                return

        # Rename in viewer
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in viewer_win.viewer.layers:
                if layer.name == old_name:
                    layer.name = new_name
                    break

        # Refresh dropdowns
        self._refresh_management_combos()
        self._refresh_active_combos()
        self.statusBar().showMessage(f"Renamed '{old_name}' → '{new_name}'")

    def _on_delete_layer(self, prefix: str) -> None:
        """Delete a segmentation or mask from HDF5 and viewer."""
        combo = self._mgmt_seg_combo if prefix == "labels" else self._mgmt_mask_combo
        name = combo.currentText()
        if not name:
            self.statusBar().showMessage("Nothing selected to delete")
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete '{name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        store = getattr(self, "_current_store", None)
        if store is not None:
            store.delete_item(f"{prefix}/{name}")

        # Remove from viewer
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)
                    break

        self._refresh_management_combos()
        self._refresh_active_combos()
        self.statusBar().showMessage(f"Deleted '{name}'")

    def _on_rename_channel(self) -> None:
        """Rename a channel (image layer) in the viewer and HDF5 metadata."""
        old_name = self._mgmt_chan_combo.currentText()
        if not old_name:
            self.statusBar().showMessage("Nothing selected to rename")
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename Channel", f"New name for '{old_name}':", text=old_name
        )
        if not ok or not new_name or new_name == old_name:
            return

        # Update channel_names in HDF5 metadata
        store = getattr(self, "_current_store", None)
        if store is not None:
            meta = store.metadata
            names = list(meta.get("channel_names", []))
            if old_name in names:
                names[names.index(old_name)] = new_name
                store.set_metadata({"channel_names": names})

        # Rename in viewer
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in viewer_win.viewer.layers:
                if layer.name == old_name:
                    layer.name = new_name
                    break

        self._refresh_management_combos()
        self.statusBar().showMessage(f"Renamed channel '{old_name}' → '{new_name}'")

    def _on_delete_channel(self) -> None:
        """Delete a channel (image layer) from the viewer."""
        name = self._mgmt_chan_combo.currentText()
        if not name:
            self.statusBar().showMessage("Nothing selected to delete")
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete channel '{name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        viewer_win = self._windows.get("viewer")
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)
                    break

        self._refresh_management_combos()
        self.statusBar().showMessage(f"Deleted channel '{name}'")

    # ── Active layer sync ───────────────────────────────────────

    def _on_active_seg_combo_changed(self, name: str) -> None:
        """User changed the active segmentation dropdown."""
        if name:
            self.data_model.set_active_segmentation(name)

    def _on_active_mask_combo_changed(self, name: str) -> None:
        """User changed the active mask dropdown."""
        if name:
            self.data_model.set_active_mask(name)

    def _on_model_active_seg_changed(self, name: str) -> None:
        """Model's active segmentation changed (e.g., from napari click)."""
        if hasattr(self, "_active_seg_combo") and name:
            self._active_seg_combo.blockSignals(True)
            if self._active_seg_combo.findText(name) < 0:
                self._active_seg_combo.addItem(name)
            self._active_seg_combo.setCurrentText(name)
            self._active_seg_combo.blockSignals(False)
        self._refresh_management_combos()
        self._refresh_dataset_info()

    def _on_model_active_mask_changed(self, name: str) -> None:
        """Model's active mask changed (e.g., from napari click)."""
        if hasattr(self, "_active_mask_combo") and name:
            self._active_mask_combo.blockSignals(True)
            if self._active_mask_combo.findText(name) < 0:
                self._active_mask_combo.addItem(name)
            self._active_mask_combo.setCurrentText(name)
            self._active_mask_combo.blockSignals(False)
        self._refresh_management_combos()
        self._refresh_dataset_info()

    def _refresh_dataset_info(self) -> None:
        """Refresh the Dataset Info label from the current store."""
        store = getattr(self, "_current_store", None)
        h5_path = getattr(self, "_current_h5_path", None)
        if not hasattr(self, "_info_label"):
            return
        if store is None or h5_path is None:
            self._info_label.setText("No dataset loaded")
            return
        try:
            n_labels = len(store.list_labels())
            n_masks = len(store.list_masks())
            with store.open_read() as s:
                intensity = s.read_array("intensity")
                shape = intensity.shape
            self._info_label.setText(
                f"File: {Path(h5_path).name}\n"
                f"Shape: {shape}\n"
                f"Labels: {n_labels}  |  Masks: {n_masks}"
            )
        except Exception:
            pass

    def _refresh_active_combos(self) -> None:
        """Refresh the active segmentation/mask dropdowns.

        Block signals during repopulation to prevent spurious intermediate
        state changes (e.g., first addItem becoming current on an empty combo).
        """
        store = getattr(self, "_current_store", None)
        mask_set = set(store.list_masks()) if store is not None else set()

        if hasattr(self, "_active_seg_combo"):
            self._active_seg_combo.blockSignals(True)
            current = self._active_seg_combo.currentText()
            self._active_seg_combo.clear()
            if store is not None:
                for name in store.list_labels():
                    if name not in mask_set:
                        self._active_seg_combo.addItem(name)
            if current and self._active_seg_combo.findText(current) >= 0:
                self._active_seg_combo.setCurrentText(current)
            self._active_seg_combo.blockSignals(False)

        if hasattr(self, "_active_mask_combo"):
            self._active_mask_combo.blockSignals(True)
            current = self._active_mask_combo.currentText()
            self._active_mask_combo.clear()
            if store is not None:
                for name in store.list_masks():
                    self._active_mask_combo.addItem(name)
            if current and self._active_mask_combo.findText(current) >= 0:
                self._active_mask_combo.setCurrentText(current)
            self._active_mask_combo.blockSignals(False)

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

    def get_data_model(self) -> "CellDataModel":
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
