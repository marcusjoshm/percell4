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
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
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
            self._content_stack.addWidget(panel)

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

    def _create_analysis_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(self._section_label("Analysis"))

        # ── Segmentation group ──
        seg_group = QGroupBox("Segmentation")
        seg_layout = QVBoxLayout(seg_group)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:"))
        self._seg_method = QComboBox()
        self._seg_method.addItems(["Cellpose", "Import ROIs", "Manual Drawing"])
        method_row.addWidget(self._seg_method)
        seg_layout.addLayout(method_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._seg_model = QComboBox()
        self._seg_model.addItems(["cpsam", "cyto3", "cyto2", "cyto", "nuclei"])
        model_row.addWidget(self._seg_model)
        seg_layout.addLayout(model_row)

        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Channel:"))
        self._seg_channel = QComboBox()
        self._seg_channel.setPlaceholderText("Load a dataset first")
        chan_row.addWidget(self._seg_channel)
        seg_layout.addLayout(chan_row)

        diam_row = QHBoxLayout()
        diam_row.addWidget(QLabel("Diameter:"))
        self._seg_diameter = QSpinBox()
        self._seg_diameter.setRange(0, 500)
        self._seg_diameter.setValue(30)
        self._seg_diameter.setSpecialValueText("Auto")
        diam_row.addWidget(self._seg_diameter)
        seg_layout.addLayout(diam_row)

        gpu_row = QHBoxLayout()
        self._seg_gpu = QCheckBox("Use GPU")
        self._seg_gpu.setStyleSheet("QCheckBox { color: #e0e0e0; }")
        gpu_row.addWidget(self._seg_gpu)
        seg_layout.addLayout(gpu_row)

        btn_segment = QPushButton("Run Segmentation")
        btn_segment.clicked.connect(self._on_run_segmentation)
        seg_layout.addWidget(btn_segment)

        layout.addWidget(seg_group)

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

    def _show_window(self, key: str) -> None:
        """Show/raise a managed window, creating it if needed."""
        window = self._get_or_create_window(key)
        if window is None:
            return
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
        """Load an .h5 dataset into the napari viewer."""
        from percell4.store import DatasetStore

        store = DatasetStore(h5_path)
        if not store.exists():
            self.statusBar().showMessage(f"File not found: {h5_path}")
            return

        self._current_store = store
        self._current_h5_path = h5_path

        # Ensure viewer is open
        self._show_window("viewer")
        viewer_win = self._windows.get("viewer")
        if viewer_win is None:
            return

        # Clear previous data
        viewer_win.clear()
        self.data_model.clear()

        # Read and display intensity data
        try:
            with store.open_read() as s:
                intensity = s.read_array("intensity")
                meta = s.metadata
                channel_names = meta.get("channel_names", [])

                if intensity.ndim == 2:
                    # Single channel
                    name = channel_names[0] if channel_names else "Intensity"
                    viewer_win.add_image(intensity, name=name)
                elif intensity.ndim == 3 and intensity.shape[0] <= 20:
                    # Multi-channel (C, H, W) — add each channel separately
                    for i in range(intensity.shape[0]):
                        name = (
                            channel_names[i]
                            if i < len(channel_names)
                            else f"ch{i}"
                        )
                        viewer_win.add_image(intensity[i], name=name)
                else:
                    # Unknown layout — add as single image
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

        # Update info label
        if hasattr(self, "_info_label"):
            n_labels = len(store.list_labels())
            n_masks = len(store.list_masks())
            self._info_label.setText(
                f"File: {Path(h5_path).name}\n"
                f"Shape: {intensity.shape}\n"
                f"Labels: {n_labels}  |  Masks: {n_masks}"
            )

        # Populate channel dropdown for segmentation
        if hasattr(self, "_seg_channel"):
            self._seg_channel.clear()
            viewer_win_check = self._windows.get("viewer")
            if viewer_win_check is not None and viewer_win_check.viewer is not None:
                for layer in viewer_win_check.viewer.layers:
                    if layer.__class__.__name__ == "Image":
                        self._seg_channel.addItem(layer.name)

        self.statusBar().showMessage(f"Loaded: {Path(h5_path).name}")

    def _on_close_dataset(self) -> None:
        viewer = self._windows.get("viewer")
        if viewer is not None:
            viewer.clear()
        self.data_model.clear()
        self._current_store = None
        self._current_h5_path = None
        if hasattr(self, "_info_label"):
            self._info_label.setText("No dataset loaded")
        if hasattr(self, "_seg_channel"):
            self._seg_channel.clear()
        self.statusBar().showMessage("Dataset closed")

    def _on_run_segmentation(self) -> None:
        method = self._seg_method.currentText()

        if method == "Import ROIs":
            self._on_import_rois()
            return

        if method == "Manual Drawing":
            self._show_window("viewer")
            self.statusBar().showMessage(
                "Manual drawing: use napari's paint/fill tools on the Labels layer"
            )
            return

        # Cellpose segmentation
        viewer_win = self._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self.statusBar().showMessage("Open a dataset in the viewer first")
            return

        # Get the selected channel image
        selected_channel = self._seg_channel.currentText()
        if not selected_channel:
            self.statusBar().showMessage("No channel selected for segmentation")
            return

        try:
            image = viewer_win.viewer.layers[selected_channel].data
        except KeyError:
            self.statusBar().showMessage(f"Channel '{selected_channel}' not found in viewer")
            return
        model_type = self._seg_model.currentText()
        diameter = self._seg_diameter.value() if self._seg_diameter.value() > 0 else None
        gpu = self._seg_gpu.isChecked()

        self.statusBar().showMessage(
            f"Running Cellpose ({model_type})..."
        )

        from percell4.gui.workers import Worker
        from percell4.segment.cellpose import run_cellpose

        self._seg_worker = Worker(
            run_cellpose,
            image,
            model_type=model_type,
            diameter=diameter,
            gpu=gpu,
        )
        self._seg_worker.finished.connect(self._on_segmentation_done)
        self._seg_worker.error.connect(
            lambda msg: self.statusBar().showMessage(f"Segmentation error: {msg}")
        )
        self._seg_worker.start()

    def _on_segmentation_done(self, masks) -> None:
        """Handle completed segmentation: postprocess, store, display."""
        from percell4.segment.postprocess import (
            filter_edge_cells,
            filter_small_cells,
            relabel_sequential,
        )

        # Postprocess
        labels, edge_removed = filter_edge_cells(masks)
        labels, small_removed = filter_small_cells(labels, min_area=15)
        labels = relabel_sequential(labels)
        n_cells = int(labels.max())

        self.statusBar().showMessage(
            f"Segmentation complete: {n_cells} cells "
            f"({edge_removed} edge, {small_removed} small removed)"
        )

        # Write to HDF5 if a dataset is loaded
        store = getattr(self, "_current_store", None)
        if store is not None:
            seg_name = f"cellpose_{n_cells}"
            store.write_labels(seg_name, labels)

        # Display in viewer
        viewer_win = self._windows.get("viewer")
        if viewer_win is not None:
            seg_name = f"cellpose_{n_cells}"
            viewer_win.add_labels(labels, name=seg_name)

    def _on_import_rois(self) -> None:
        """Import ImageJ ROI .zip file as a label array."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import ImageJ ROIs", "",
            "ROI Files (*.zip);;All Files (*)"
        )
        if not path:
            return

        viewer_win = self._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self.statusBar().showMessage("Open a dataset in the viewer first")
            return

        # Get image shape from first image layer
        image_layers = [
            layer for layer in viewer_win.viewer.layers
            if layer.__class__.__name__ == "Image"
        ]
        if not image_layers:
            self.statusBar().showMessage("No image loaded")
            return

        shape = image_layers[0].data.shape[-2:]  # (H, W)

        try:
            from percell4.segment.roi_import import import_imagej_rois

            labels = import_imagej_rois(path, shape)
            n_cells = int(labels.max())

            # Write to HDF5
            store = getattr(self, "_current_store", None)
            if store is not None:
                store.write_labels(f"roi_import_{n_cells}", labels)

            viewer_win.add_labels(labels, name=f"roi_import_{n_cells}")
            self.statusBar().showMessage(f"Imported {n_cells} ROIs from {Path(path).name}")
        except ImportError:
            QMessageBox.warning(
                self, "Missing Dependency",
                "roifile package required for ImageJ ROI import.\n"
                "Install with: pip install roifile"
            )
        except Exception as e:
            self.statusBar().showMessage(f"ROI import error: {e}")

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
