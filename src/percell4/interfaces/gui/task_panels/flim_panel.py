"""FLIM task panel — phasor computation, wavelet filter, lifetime.

Receives dependencies via callbacks — no launcher reference.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import Qt, QSettings
from qtpy.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4 import store_schema
from percell4.domain.errors import MissingOptionalDependencyError
from percell4.domain.flim.wavelet import ALGORITHM_CHOICES
from percell4.gui import theme
from percell4.model import CellDataModel

_QSETTINGS_ORG = "leelab"
_QSETTINGS_APP = "percell4"
_QSETTINGS_KEY = "flim/wavelet_algorithm"


class FlimPanel(QWidget):
    """Panel for FLIM analysis: phasor computation, wavelet filter, lifetime."""

    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_repo: Callable[[], Any],
        get_viewer_window: Callable[[], Any | None],
        get_phasor_window: Callable[[], Any | None],
        get_active_seg_labels: Callable[[], Any | None],
        show_window: Callable[[str], None],
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._get_repo = get_repo
        self._get_viewer_window = get_viewer_window
        self._get_phasor_window = get_phasor_window
        self._get_active_seg_labels_cb = get_active_seg_labels
        self._show_window_cb = show_window
        self._show_status = show_status
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("FLIM")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT};"
            f" margin-bottom: 12px; padding-bottom: 4px;"
            f" border-bottom: 1px solid {theme.BORDER};"
        )
        layout.addWidget(title)

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

        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm:"))
        self._wavelet_algorithm = QComboBox()
        for alg_id, label, tooltip in ALGORITHM_CHOICES:
            self._wavelet_algorithm.addItem(label, userData=alg_id)
            idx = self._wavelet_algorithm.count() - 1
            self._wavelet_algorithm.setItemData(idx, tooltip, Qt.ToolTipRole)
        # Restore last-used algorithm across sessions.
        saved = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(
            _QSETTINGS_KEY, "boe_2021"
        )
        restored = self._wavelet_algorithm.findData(saved)
        self._wavelet_algorithm.setCurrentIndex(max(restored, 0))
        self._wavelet_algorithm.currentIndexChanged.connect(
            self._on_wavelet_algorithm_changed
        )
        algo_row.addWidget(self._wavelet_algorithm, stretch=1)
        wavelet_layout.addLayout(algo_row)

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

        layout.addStretch()

    # ── Helpers ───────────────────────────────────────────────

    def _show_window(self, name: str) -> None:
        self._show_window_cb(name)

    def _get_active_channel(self) -> str | None:
        return self.data_model.session.active_channel

    def _get_active_seg_labels(self):
        return self._get_active_seg_labels_cb()

    # ── Phasor ───────────────────────────────────────────────

    def _on_compute_phasor(self) -> None:
        active_channel = self._get_active_channel()
        if active_channel is None:
            self._show_status("Select a channel in the viewer first")
            return

        harmonic = int(self._phasor_harmonic.currentText())
        self._show_status(f"Computing phasor for {active_channel}...")

        try:
            from percell4.application.use_cases.compute_phasor import ComputePhasor

            repo = self._get_repo()
            uc = ComputePhasor(repo, self.data_model.session)
            result = uc.execute(channel=active_channel, harmonic=harmonic)
        except ValueError as e:
            self._show_status(str(e))
            return
        except Exception as e:
            self._show_status(f"Phasor computation error: {e}")
            return

        # Read intensity for weighted histogram (UI concern)
        handle = self.data_model.session.dataset
        phasor_intensity = None
        if handle is not None:
            try:
                repo = self._get_repo()
                intensity_data = repo.read_array(handle, "intensity")
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

        seg_labels = self._get_active_seg_labels()
        self._show_window("phasor_plot")
        phasor_win = self._get_phasor_window()
        if phasor_win is not None:
                phasor_win.set_phasor_data(
                    result.g_map, result.s_map,
                    intensity=phasor_intensity, labels=seg_labels,
                )

        freq = handle.metadata.get("flim_frequency_mhz", "unknown") if handle else "unknown"
        self._show_status(
            f"Phasor computed: {result.n_valid:,} valid pixels | "
            f"channel: {active_channel} | harmonic: {harmonic} | freq: {freq} MHz"
        )

    # ── Wavelet Filter ───────────────────────────────────────

    def _on_wavelet_algorithm_changed(self, _idx: int) -> None:
        """Persist algorithm choice across sessions."""
        alg_id = self._wavelet_algorithm.currentData()
        if alg_id:
            QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).setValue(
                _QSETTINGS_KEY, alg_id
            )

    def _confirm_algorithm_switch(
        self, channel: str, selected: str,
    ) -> bool:
        """If the channel already has filtered data from a different
        algorithm, ask the user whether to overwrite. Returns True to
        proceed, False to cancel. Returns True unconditionally when
        there's no existing filtered data.
        """
        repo = self._get_repo()
        handle = self.data_model.session.dataset
        if repo is None or handle is None:
            return True
        g_path = store_schema.PHASOR_G_FILTERED.format(channel=channel)
        try:
            attrs = repo.read_array_attrs(handle, g_path)
        except KeyError:
            return True  # No existing filtered dataset.
        existing = store_schema.read_wavelet_algorithm(attrs)
        if existing == selected:
            return True
        selected_label = next(
            (label for alg, label, _ in ALGORITHM_CHOICES if alg == selected),
            selected,
        )
        existing_label = next(
            (label for alg, label, _ in ALGORITHM_CHOICES if alg == existing),
            existing,
        )
        reply = QMessageBox.warning(
            self,
            "Overwrite filtered result?",
            f"Channel '{channel}' was last filtered with "
            f"<b>{existing_label}</b>. Apply <b>{selected_label}</b>? "
            f"The existing {existing_label} result will be discarded.",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return reply == QMessageBox.Ok

    def _on_apply_wavelet(self) -> None:
        active_channel = self._get_active_channel()
        if active_channel is None:
            self._show_status("Select a channel in the viewer first")
            return

        filter_level = self._wavelet_level.value()
        algorithm = self._wavelet_algorithm.currentData() or "boe_2021"

        if not self._confirm_algorithm_switch(active_channel, algorithm):
            self._show_status("Wavelet filter cancelled")
            return

        self._show_status(
            f"Applying {algorithm} wavelet filter (level {filter_level}) "
            f"to {active_channel}..."
        )

        from qtpy.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from percell4.application.use_cases.apply_wavelet import ApplyWavelet

            repo = self._get_repo()
            uc = ApplyWavelet(repo, self.data_model.session)
            result = uc.execute(
                channel=active_channel,
                filter_level=filter_level,
                algorithm=algorithm,
            )
        except MissingOptionalDependencyError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self,
                "Optional dependency missing",
                "Wavelet filtering requires the <code>dtcwt</code> package.\n\n"
                "Install with:\n    pip install 'percell4[flim]'\n\n"
                f"Details: {e}",
            )
            self._show_status("dtcwt not installed — see dialog for install hint")
            return
        except ValueError as e:
            QApplication.restoreOverrideCursor()
            self._show_status(str(e))
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self._show_status(f"Wavelet filter error: {e}")
            return

        QApplication.restoreOverrideCursor()

        # Read intensity for phasor plot (UI concern)
        handle = self.data_model.session.dataset
        repo = self._get_repo()
        intensity = None
        if handle is not None and repo is not None:
            try:
                intensity_data = repo.read_array(handle, "intensity")
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

        g_unfiltered = s_unfiltered = None
        if handle is not None and repo is not None:
            try:
                g_unfiltered = repo.read_array(handle, f"phasor/{active_channel}/g")
                s_unfiltered = repo.read_array(handle, f"phasor/{active_channel}/s")
            except KeyError:
                pass

        seg_labels = self._get_active_seg_labels()
        phasor_win = self._get_phasor_window()
        if phasor_win is not None:
                phasor_win.set_phasor_data(
                    result.g_filtered, result.s_filtered,
                    intensity=intensity.astype(np.float32) if intensity is not None else None,
                    g_unfiltered=g_unfiltered, s_unfiltered=s_unfiltered,
                    labels=seg_labels,
                )

        self._show_status(
            f"Wavelet filter applied ({result.algorithm}): "
            f"level {filter_level} | {result.n_valid:,} valid pixels | "
            f"channel: {active_channel}"
        )

    # ── Lifetime ─────────────────────────────────────────────

    def _on_compute_lifetime(self) -> None:
        active_channel = self._get_active_channel()
        if active_channel is None:
            self._show_status("Select a channel in the viewer first")
            return

        try:
            from percell4.application.use_cases.compute_lifetime import ComputeLifetime

            repo = self._get_repo()
            uc = ComputeLifetime(repo, self.data_model.session)
            result = uc.execute(channel=active_channel)
        except ValueError as e:
            self._show_status(str(e))
            return
        except Exception as e:
            self._show_status(f"Lifetime computation error: {e}")
            return

        # Add lifetime layer to viewer
        viewer_win = self._get_viewer_window()
        if viewer_win is not None:
                viewer_win.viewer.add_image(
                    result.lifetime,
                    name=f"Lifetime ({active_channel})",
                    colormap="turbo",
                    blending="additive",
                )

        if result.mean_tau is not None:
            self._show_status(
                f"Lifetime ({result.source}): mean={result.mean_tau:.2f} ns | "
                f"channel: {active_channel} | freq: {result.frequency_mhz} MHz"
            )
        else:
            self._show_status("Lifetime: no valid pixels")
