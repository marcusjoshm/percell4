"""Launcher/hub window — the main control center for PerCell4.

Sidebar with category buttons, stacked content area showing sub-options
for the selected category. Manages all other windows.
"""

from __future__ import annotations

from pathlib import Path

from qtpy.QtCore import QSettings, Qt
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
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

from percell4.model import CellDataModel


class LauncherWindow(QMainWindow):
    """Main hub window with sidebar navigation and content panels."""

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4")
        self.resize(700, 500)

        # Window registry — all managed windows
        self._windows: dict[str, QWidget] = {}

        # Global dark theme for the launcher window
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QMenuBar {
                background-color: #1e2a3a;
                color: #e0e0e0;
            }
            QMenuBar::item:selected { background-color: #2a3d52; }
            QMenu {
                background-color: #1e2a3a;
                color: #e0e0e0;
                border: 1px solid #3a3a3a;
            }
            QMenu::item:selected {
                background-color: #4ea8de;
                color: #ffffff;
            }
            QStatusBar {
                background-color: #0d1b2a;
                color: #a0a0a0;
                border-top: 1px solid #2a2a2a;
            }
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
        sidebar.setStyleSheet("""
            QWidget { background-color: #1e2a3a; }
            QPushButton {
                background-color: #1e2a3a;
                color: #e0e0e0;
                border: none;
                padding: 14px 12px;
                text-align: left;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2a3d52;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #0d1b2a;
                color: #ffffff;
                border-left: 3px solid #4ea8de;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 8, 0, 0)
        sidebar_layout.setSpacing(0)

        # Content stack — dark background, white text
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet("""
            QStackedWidget {
                background-color: #121212;
                color: #ffffff;
            }
            QLabel { color: #e0e0e0; }
            QGroupBox {
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 16px;
            }
            QGroupBox::title {
                color: #4ea8de;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #3a3a3a;
                border-color: #4ea8de;
            }
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
            QComboBox {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QComboBox:hover { border-color: #4ea8de; }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                color: #ffffff;
                selection-background-color: #4ea8de;
            }
            QSpinBox, QDoubleSpinBox {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 4px;
            }
        """)

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

        btn_import = QPushButton("Import TIFF Dataset...")
        btn_import.clicked.connect(self._on_import_dataset)
        import_layout.addWidget(btn_import)

        btn_load = QPushButton("Load Existing .h5 Dataset...")
        btn_load.clicked.connect(self._on_load_dataset)
        import_layout.addWidget(btn_load)

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

        layout.addWidget(export_group)

        # ── Placeholders ──
        layout.addWidget(self._placeholder("Prism Export"))
        layout.addWidget(self._placeholder("Batch Import"))
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

        # ── Thresholding group ──
        thresh_group = QGroupBox("Thresholding")
        thresh_layout = QVBoxLayout(thresh_group)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:"))
        self._thresh_method = QComboBox()
        self._thresh_method.addItems(["Otsu", "Triangle", "Li", "Adaptive", "Manual"])
        method_row.addWidget(self._thresh_method)
        thresh_layout.addLayout(method_row)

        sigma_row = QHBoxLayout()
        sigma_row.addWidget(QLabel("Gaussian σ:"))
        self._thresh_sigma = QDoubleSpinBox()
        self._thresh_sigma.setRange(0.0, 20.0)
        self._thresh_sigma.setValue(0.0)
        self._thresh_sigma.setSpecialValueText("None")
        self._thresh_sigma.setSingleStep(0.5)
        sigma_row.addWidget(self._thresh_sigma)
        thresh_layout.addLayout(sigma_row)

        btn_thresh = QPushButton("Apply Threshold")
        btn_thresh.clicked.connect(self._on_apply_threshold)
        thresh_layout.addWidget(btn_thresh)

        btn_save_mask = QPushButton("Save Mask to HDF5")
        btn_save_mask.clicked.connect(self._on_save_mask)
        thresh_layout.addWidget(btn_save_mask)

        layout.addWidget(thresh_group)

        # ── Measurements group ──
        meas_group = QGroupBox("Measurements")
        meas_layout = QVBoxLayout(meas_group)

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
        btn_particle = QPushButton("Analyze Particles")
        btn_particle.clicked.connect(self._on_analyze_particles)
        particle_layout.addWidget(btn_particle)
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

        layout.addWidget(self._section_label("Workflows"))
        layout.addWidget(self._placeholder("Standard Analysis Pipeline"))
        layout.addWidget(self._placeholder("Custom Workflow Builder"))
        layout.addStretch()
        return panel

    def _create_data_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)

        layout.addWidget(self._section_label("Data"))

        # ── Active layers ──
        layers_group = QGroupBox("Active Layers")
        layers_layout = QVBoxLayout(layers_group)

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

        # Listen to model for changes from other sources (e.g., napari click)
        self.data_model.active_segmentation_changed.connect(
            self._on_model_active_seg_changed
        )
        self.data_model.active_mask_changed.connect(
            self._on_model_active_mask_changed
        )

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
        scroll.setStyleSheet(
            "QScrollArea { background-color: #121212; border: none; }"
            " QScrollArea > QWidget > QWidget { background-color: #121212; }"
        )
        return scroll

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #ffffff;"
            " margin-bottom: 12px; padding-bottom: 4px;"
            " border-bottom: 1px solid #3a3a3a;"
        )
        return label

    @staticmethod
    def _placeholder(text: str) -> QLabel:
        label = QLabel(f"  {text} — coming soon")
        label.setStyleSheet(
            "color: #555555; font-style: italic; padding: 4px 8px;"
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

            factories = {
                "viewer": lambda: ViewerWindow(self.data_model),
                "data_plot": lambda: DataPlotWindow(self.data_model),
                "phasor_plot": lambda: PhasorPlotWindow(self.data_model),
                "cell_table": lambda: CellTableWindow(self.data_model),
            }
            if key in factories:
                self._windows[key] = factories[key]()
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
        from percell4.gui.import_dialog import ImportDialog

        dialog = ImportDialog(
            self, project_dir=getattr(self, "_project_dir", None)
        )
        if dialog.exec_() != ImportDialog.Accepted:
            return

        source_dir = dialog.source_dir
        output_path = dialog.output_path

        if not source_dir or not output_path:
            self.statusBar().showMessage("Import cancelled — missing paths")
            return

        # Build project.csv path if we have a project dir
        project_csv = None
        if hasattr(self, "_project_dir") and self._project_dir:
            project_csv = str(Path(self._project_dir) / "project.csv")

        # Run import in background thread
        self.statusBar().showMessage(f"Importing from {source_dir}...")

        from percell4.gui.workers import Worker
        from percell4.io.importer import import_dataset

        self._import_worker = Worker(
            import_dataset,
            source_dir,
            output_path,
            token_config=dialog.token_config,
            tile_config=dialog.tile_config,
            z_project_method=dialog.z_project_method,
            metadata={
                "condition": dialog.condition,
                "replicate": dialog.replicate,
                "notes": dialog.notes,
            },
            project_csv=project_csv,
            progress_callback=self._on_import_progress,
        )
        self._import_worker.finished.connect(
            lambda n_ch: self._on_import_finished(output_path, n_ch)
        )
        self._import_worker.error.connect(
            lambda msg: self.statusBar().showMessage(f"Import error: {msg}")
        )
        self._import_worker.start()

    def _on_import_progress(self, current: int, total: int, msg: str) -> None:
        self.statusBar().showMessage(f"Import: {msg} ({current}/{total})")

    def _on_import_finished(self, h5_path: str, n_channels: int) -> None:
        self.statusBar().showMessage(
            f"Import complete: {n_channels} channel(s) → {Path(h5_path).name}"
        )
        # Auto-load the new dataset into the viewer
        self._load_h5_into_viewer(h5_path)

    def _on_load_dataset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Dataset", "", "HDF5 Files (*.h5);;All Files (*)"
        )
        if path:
            self._load_h5_into_viewer(path)

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

                # Load existing labels
                for label_name in s.list_labels():
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

        # Populate active layers dropdowns
        if hasattr(self, "_active_seg_combo"):
            self._active_seg_combo.clear()
            for label_name in store.list_labels():
                self._active_seg_combo.addItem(label_name)

        if hasattr(self, "_active_mask_combo"):
            self._active_mask_combo.clear()
            for mask_name in store.list_masks():
                self._active_mask_combo.addItem(mask_name)

        # Populate management combos
        self._refresh_management_combos()

    def _on_close_dataset(self) -> None:
        viewer = self._windows.get("viewer")
        if viewer is not None:
            viewer.clear()
        self.data_model.clear()
        self._current_store = None
        self._current_h5_path = None
        if hasattr(self, "_info_label"):
            self._info_label.setText("No dataset loaded")
        if hasattr(self, "_seg_channel_label"):
            self._seg_channel_label.setText("None selected")
        if hasattr(self, "_active_seg_combo"):
            self._active_seg_combo.clear()
        if hasattr(self, "_active_mask_combo"):
            self._active_mask_combo.clear()
        self.statusBar().showMessage("Dataset closed")

    def _update_active_channel_label(self) -> None:
        """Update channel labels in any panels that track the active layer."""
        if hasattr(self, "_seg_panel"):
            self._seg_panel.update_channel_label()

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
        store = getattr(self, "_current_store", None)

        # Determine if this is a segmentation or a mask
        if store is not None:
            label_names = store.list_labels()
            mask_names = store.list_masks()
            if name in mask_names:
                self.data_model.set_active_mask(name)
                return
            if name in label_names:
                self.data_model.set_active_segmentation(name)
                return

        # Not in store — default to treating it as a segmentation
        self.data_model.set_active_segmentation(name)

    def _on_save_mask(self) -> None:
        """Save a mask layer from viewer to HDF5."""
        store = getattr(self, "_current_store", None)
        if store is None:
            self.statusBar().showMessage("No dataset loaded")
            return

        viewer_win = self._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self.statusBar().showMessage("Viewer not open")
            return

        # Find mask-like labels layers (names containing 'mask' or from threshold)
        mask_layer = None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Labels" and "mask" in layer.name.lower():
                mask_layer = layer
                break

        if mask_layer is None:
            # Fall back to active layer if it's a labels layer
            active = viewer_win.viewer.layers.selection.active
            if active is not None and active.__class__.__name__ == "Labels":
                mask_layer = active

        if mask_layer is None:
            self.statusBar().showMessage("No mask layer to save")
            return

        import numpy as np
        data = np.asarray(mask_layer.data, dtype=np.uint8)
        count = store.write_mask(mask_layer.name, data)

        # Add to active mask dropdown
        if hasattr(self, "_active_mask_combo"):
            if self._active_mask_combo.findText(mask_layer.name) == -1:
                self._active_mask_combo.addItem(mask_layer.name)
            self._active_mask_combo.setCurrentText(mask_layer.name)

        self.statusBar().showMessage(f"Saved mask '{mask_layer.name}' ({count} pixels)")

    def _on_apply_threshold(self) -> None:
        method = self._thresh_method.currentText()
        self.statusBar().showMessage(f"Threshold ({method}) — not yet implemented")

    def _on_measure_cells(self) -> None:
        self.statusBar().showMessage("Measure cells — not yet implemented")

    def _on_analyze_particles(self) -> None:
        self.statusBar().showMessage("Particle analysis — not yet implemented")

    def _on_compute_phasor(self) -> None:
        self.statusBar().showMessage("Compute phasor — not yet implemented")

    def _on_apply_wavelet(self) -> None:
        self.statusBar().showMessage("Wavelet filter — not yet implemented")

    def _on_compute_lifetime(self) -> None:
        self.statusBar().showMessage("Compute lifetime — not yet implemented")

    def _on_run_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Python Script", "", "Python Files (*.py);;All Files (*)"
        )
        if path:
            self.statusBar().showMessage(f"Run script: {path} — not yet implemented")

    # ── Layer management ────────────────────────────────────────

    def _refresh_management_combos(self) -> None:
        """Refresh all management dropdowns from the current store."""
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
        """Rename a channel (image layer) in the viewer."""
        old_name = self._mgmt_chan_combo.currentText()
        if not old_name:
            self.statusBar().showMessage("Nothing selected to rename")
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename Channel", f"New name for '{old_name}':", text=old_name
        )
        if not ok or not new_name or new_name == old_name:
            return

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
        # Also update management combo
        self._refresh_management_combos()

    def _on_model_active_mask_changed(self, name: str) -> None:
        """Model's active mask changed (e.g., from napari click)."""
        if hasattr(self, "_active_mask_combo") and name:
            self._active_mask_combo.blockSignals(True)
            if self._active_mask_combo.findText(name) < 0:
                self._active_mask_combo.addItem(name)
            self._active_mask_combo.setCurrentText(name)
            self._active_mask_combo.blockSignals(False)
        self._refresh_management_combos()

    def _refresh_active_combos(self) -> None:
        """Refresh the active segmentation/mask dropdowns."""
        store = getattr(self, "_current_store", None)
        if hasattr(self, "_active_seg_combo"):
            current = self._active_seg_combo.currentText()
            self._active_seg_combo.clear()
            if store is not None:
                for name in store.list_labels():
                    self._active_seg_combo.addItem(name)
            if current and self._active_seg_combo.findText(current) >= 0:
                self._active_seg_combo.setCurrentText(current)

        if hasattr(self, "_active_mask_combo"):
            current = self._active_mask_combo.currentText()
            self._active_mask_combo.clear()
            if store is not None:
                for name in store.list_masks():
                    self._active_mask_combo.addItem(name)
            if current and self._active_mask_combo.findText(current) >= 0:
                self._active_mask_combo.setCurrentText(current)

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

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Close all managed windows and quit."""
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
