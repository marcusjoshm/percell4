"""FLIM task panel — phasor computation, wavelet filter, lifetime.

Receives dependencies via callbacks — no launcher reference.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.application.session import Event
from percell4.config import viewer_presets as vp
from percell4.gui import theme
from percell4.model import CellDataModel


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
        # GMM Worker reference holder — must persist for the QThread's
        # lifetime to prevent GC mid-run (per gui/workers.py:30 docstring).
        self._gmm_worker: Any = None
        self._build_ui()
        # Reflect freq-availability into the reference-circle checkbox
        # when the dataset switches. The FLIM panel is long-lived; we
        # don't bother with explicit unsubscribe.
        self.data_model.session.subscribe(
            Event.DATASET_CHANGED, self._refresh_ref_circle_enabled
        )
        self._refresh_ref_circle_enabled()

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
        btn_phasor.setToolTip(
            "Compute phasor from /decay (or load cached result if present)\n"
            "Shift+click to force recompute"
        )
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
        btn_wavelet.setToolTip(
            "Apply DTCWT wavelet denoising (or load cached result if present)\n"
            "Shift+click to force recompute"
        )
        btn_wavelet.clicked.connect(self._on_apply_wavelet)
        wavelet_layout.addWidget(btn_wavelet)

        layout.addWidget(wavelet_group)

        # ── Phasor Filters (U6) ──
        # Intensity threshold + reference circle filters live in this
        # task panel; the existing "Filter by active mask" stays on the
        # phasor plot toolbar (see brainstorm 2026-04-30 + plan U5/U6).
        # All three AND-compose with the cell-selection filter and
        # restrict both display and GMM input.
        filters_group = QGroupBox("Phasor Filters")
        filters_layout = QVBoxLayout(filters_group)

        intensity_row = QHBoxLayout()
        intensity_row.addWidget(QLabel("Intensity ≥"))
        self._intensity_threshold_spin = QSpinBox()
        self._intensity_threshold_spin.setRange(0, 1_000_000_000)
        self._intensity_threshold_spin.setSingleStep(1)
        self._intensity_threshold_spin.setValue(0)
        self._intensity_threshold_spin.valueChanged.connect(
            self._push_filters_to_phasor_plot
        )
        intensity_row.addWidget(self._intensity_threshold_spin)
        filters_layout.addLayout(intensity_row)

        self._ref_circle_check = QCheckBox("Reference circle")
        self._ref_circle_check.setToolTip(
            "Restrict the phasor histogram and GMM input to pixels inside a "
            "circle on the universal semicircle anchored at the target "
            "lifetime. Requires flim_frequency_mhz in the dataset metadata."
        )
        self._ref_circle_check.toggled.connect(self._on_ref_circle_toggled)
        filters_layout.addWidget(self._ref_circle_check)

        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("τ (ns)"))
        self._ref_circle_tau_spin = QDoubleSpinBox()
        self._ref_circle_tau_spin.setRange(0.0, 100.0)
        self._ref_circle_tau_spin.setSingleStep(0.1)
        self._ref_circle_tau_spin.setDecimals(2)
        self._ref_circle_tau_spin.setValue(2.5)
        self._ref_circle_tau_spin.setEnabled(False)
        self._ref_circle_tau_spin.valueChanged.connect(self._push_filters_to_phasor_plot)
        ref_row.addWidget(self._ref_circle_tau_spin)
        ref_row.addWidget(QLabel("r"))
        self._ref_circle_radius_spin = QDoubleSpinBox()
        self._ref_circle_radius_spin.setRange(0.0, 1.0)
        self._ref_circle_radius_spin.setSingleStep(0.05)
        self._ref_circle_radius_spin.setDecimals(2)
        self._ref_circle_radius_spin.setValue(0.5)
        self._ref_circle_radius_spin.setEnabled(False)
        self._ref_circle_radius_spin.valueChanged.connect(self._push_filters_to_phasor_plot)
        ref_row.addWidget(self._ref_circle_radius_spin)
        filters_layout.addLayout(ref_row)

        filters_note = QLabel("Active mask filter is on the phasor plot toolbar.")
        filters_note.setStyleSheet(f"color: {theme.TEXT_LABEL}; font-style: italic;")
        filters_layout.addWidget(filters_note)

        layout.addWidget(filters_group)

        # ── Phasor Segmentation (U6) ──
        seg_group = QGroupBox("Phasor Segmentation")
        seg_layout = QVBoxLayout(seg_group)

        shape_row = QHBoxLayout()
        shape_row.addWidget(QLabel("Shape:"))
        self._shape_combo = QComboBox()
        self._shape_combo.addItems(["Ellipse", "Circle"])
        shape_row.addWidget(self._shape_combo)
        seg_layout.addLayout(shape_row)

        n_row = QHBoxLayout()
        self._auto_check = QCheckBox("Auto")
        self._auto_check.setChecked(True)
        self._auto_check.toggled.connect(self._on_auto_toggled)
        n_row.addWidget(self._auto_check)
        self._n_label = QLabel("n_max:")
        n_row.addWidget(self._n_label)
        self._n_clusters_spin = QSpinBox()
        self._n_clusters_spin.setRange(1, 10)
        self._n_clusters_spin.setValue(4)
        n_row.addWidget(self._n_clusters_spin)
        seg_layout.addLayout(n_row)

        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel("Criterion:"))
        self._criterion_combo = QComboBox()
        self._criterion_combo.addItems(["BIC", "AIC"])
        crit_row.addWidget(self._criterion_combo)
        seg_layout.addLayout(crit_row)

        # Note: cov_f and shift placement defaults are not exposed in the
        # FLIM tab — the Phasor window's Selected-ROI panel is the
        # exclusive surface for per-ROI tuning. The use case is invoked
        # with cov_f=2.0 / shift=0.0 (the eigenstructure baseline);
        # users tune each placed ROI individually via Stretch ∥ / ⊥ and
        # Shift ∥ / ⊥ on the phasor plot.
        self._run_gmm_btn = QPushButton("Run GMM")
        self._run_gmm_btn.clicked.connect(self._on_run_gmm)
        seg_layout.addWidget(self._run_gmm_btn)

        layout.addWidget(seg_group)

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
        """Read the canonical active channel from Session.

        The SessionWindow is the only Selector site for active_channel;
        this panel reads it at Run time.
        """
        return self.data_model.session.active_channel

    def _get_active_seg_labels(self):
        return self._get_active_seg_labels_cb()

    # ── Helpers ──────────────────────────────────────────────

    def _shift_held(self) -> bool:
        """Read Shift modifier at handler entry to detect force-recompute intent.

        Reads ``QApplication.keyboardModifiers()`` at slot dispatch time.
        There is a documented async-release race (user releases Shift
        between click and dispatch) — accepted tradeoff per the plan's
        Risks table. If the race bites in practice, upgrade to a
        QPushButton subclass that captures modifier state at press.
        """
        from qtpy.QtWidgets import QApplication

        return bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)

    # ── Phasor ───────────────────────────────────────────────

    def _on_compute_phasor(self) -> None:
        active_channel = self._get_active_channel()
        if active_channel is None:
            self._show_status("Select a channel in the viewer first")
            return

        # Cache-check unless Shift forces recompute.
        if not self._shift_held():
            try:
                from percell4.application.use_cases.load_cached_phasor import (
                    LoadCachedPhasor,
                )
                from percell4.domain.errors import (
                    NoCachedPhasorError,
                    NoDatasetError,
                )

                repo = self._get_repo()
                # Session view bin propagates through the cache load so
                # cached g/s and decay-derived intensity arrive at the
                # binned shape the phasor plot expects to display.
                active_bin = self.data_model.session.active_bin
                cached = LoadCachedPhasor(repo, self.data_model.session).execute(
                    active_channel, view_bin=active_bin,
                )
            except NoCachedPhasorError:
                pass  # Fall through to compute
            except NoDatasetError as e:
                self._show_status(str(e))
                return
            else:
                # Cache hit — push to phasor window with the unfiltered call
                # shape (raw g/s, no g_unfiltered/s_unfiltered).
                self._show_window("phasor_plot")
                seg_labels = self._get_active_seg_labels()
                phasor_win = self._get_phasor_window()
                if phasor_win is not None:
                    phasor_win.set_phasor_data(
                        cached.g_map, cached.s_map,
                        intensity=cached.intensity, labels=seg_labels,
                    )
                self._show_status(
                    f"Loaded cached phasor (channel: {active_channel})"
                )
                self._refresh_ref_circle_enabled()
                return

        harmonic = int(self._phasor_harmonic.currentText())
        recompute_prefix = (
            "Recomputing phasor (Shift)" if self._shift_held()
            else f"Computing phasor for {active_channel}"
        )
        self._show_status(f"{recompute_prefix}...")

        try:
            from percell4.application.use_cases.compute_phasor import ComputePhasor

            repo = self._get_repo()
            uc = ComputePhasor(repo, self.data_model.session)
            active_bin = self.data_model.session.active_bin
            result = uc.execute(
                channel=active_channel, harmonic=harmonic, view_bin=active_bin,
            )
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
            # Intensity for the phasor plot's intensity-weighted histogram
            # MUST be spatially aligned with g_map / s_map. Both g_map and
            # s_map are computed from /decay/<channel>, so derive the
            # intensity weights from /decay/<channel>.sum(axis=-1) too —
            # NOT from the /intensity stack, which can drift out of
            # alignment with /decay (different stitching, rotation,
            # different acquisition source). Misaligned weights produce
            # wildly wrong histograms even though the per-pixel (g, s)
            # values are correct.
            try:
                repo = self._get_repo()
                decay = repo.read_array(
                    handle, f"decay/{active_channel}", view_bin=active_bin,
                )
                phasor_intensity = decay.sum(axis=-1).astype(np.float32)
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
        verb = "Recomputed" if self._shift_held() else "Phasor computed:"
        suffix = " (Shift)" if self._shift_held() else ""
        self._show_status(
            f"{verb} {result.n_valid:,} valid pixels | "
            f"channel: {active_channel} | harmonic: {harmonic} | freq: {freq} MHz{suffix}"
        )
        # Compute Phasor reads (and may have just propagated)
        # flim_frequency_mhz; refresh the reference-circle checkbox state
        # so the user can engage it without re-loading the dataset.
        self._refresh_ref_circle_enabled()

    # ── Wavelet Filter ───────────────────────────────────────

    def _on_apply_wavelet(self) -> None:
        active_channel = self._get_active_channel()
        if active_channel is None:
            self._show_status("Select a channel in the viewer first")
            return

        # Cache-check unless Shift forces recompute. We check g_filtered
        # specifically — wavelet's cache distinct from raw phasor cache.
        if not self._shift_held():
            try:
                from percell4.application.use_cases.load_cached_phasor import (
                    LoadCachedPhasor,
                )
                from percell4.domain.errors import (
                    NoCachedPhasorError,
                    NoDatasetError,
                )

                repo = self._get_repo()
                active_bin = self.data_model.session.active_bin
                cached = LoadCachedPhasor(repo, self.data_model.session).execute(
                    active_channel, view_bin=active_bin,
                )
            except NoCachedPhasorError:
                pass  # No raw phasor either — fall through to compute (will fail in apply_wavelet with a clear ValueError)
            except NoDatasetError as e:
                self._show_status(str(e))
                return
            else:
                if cached.g_filtered is not None and cached.s_filtered is not None:
                    seg_labels = self._get_active_seg_labels()
                    phasor_win = self._get_phasor_window()
                    if phasor_win is not None:
                        phasor_win.set_phasor_data(
                            cached.g_map, cached.s_map,
                            intensity=cached.intensity,
                            g_wavelet=cached.g_filtered, s_wavelet=cached.s_filtered,
                            labels=seg_labels,
                        )
                    self._show_status(
                        f"Loaded cached wavelet (channel: {active_channel})"
                    )
                    return
                # Raw cache exists but no wavelet — fall through to compute.

        filter_level = self._wavelet_level.value()
        recompute_prefix = (
            "Recomputing wavelet (Shift)" if self._shift_held()
            else f"Applying wavelet filter (level {filter_level}) to {active_channel}"
        )
        self._show_status(f"{recompute_prefix}...")

        from qtpy.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from percell4.application.use_cases.apply_wavelet import ApplyWavelet

            repo = self._get_repo()
            uc = ApplyWavelet(repo, self.data_model.session)
            active_bin = self.data_model.session.active_bin
            result = uc.execute(
                channel=active_channel,
                filter_level=filter_level,
                view_bin=active_bin,
            )
        except ImportError:
            QApplication.restoreOverrideCursor()
            self._show_status(
                "dtcwt package required. Install: pip install 'percell4[flim]'"
            )
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

        # Intensity for the phasor plot's intensity-weighted histogram
        # MUST be spatially aligned with g and s. Derive from
        # /decay/<channel>.sum(axis=-1), NOT from the /intensity stack —
        # see the corresponding comment in _on_compute_phasor for why.
        handle = self.data_model.session.dataset
        repo = self._get_repo()
        intensity = None
        if handle is not None and repo is not None:
            try:
                decay = repo.read_array(handle, f"decay/{active_channel}")
                intensity = decay.sum(axis=-1).astype(np.float32)
            except KeyError:
                pass

        g_unfiltered = s_unfiltered = None
        if handle is not None and repo is not None:
            try:
                g_unfiltered = repo.read_array(handle, f"phasor/{active_channel}/g")
                s_unfiltered = repo.read_array(handle, f"phasor/{active_channel}/s")
            except KeyError:
                pass

        # The unfiltered g/s are the canonical maps the window stores; the
        # wavelet result is the optional view. Raw maps should always exist
        # after a successful wavelet run (wavelet reads them), but fall back
        # to the filtered maps as the base if the read somehow failed.
        if g_unfiltered is None or s_unfiltered is None:
            base_g, base_s = result.g_filtered, result.s_filtered
        else:
            base_g, base_s = g_unfiltered, s_unfiltered

        seg_labels = self._get_active_seg_labels()
        phasor_win = self._get_phasor_window()
        if phasor_win is not None:
                phasor_win.set_phasor_data(
                    base_g, base_s,
                    intensity=intensity.astype(np.float32) if intensity is not None else None,
                    g_wavelet=result.g_filtered, s_wavelet=result.s_filtered,
                    labels=seg_labels,
                )

        verb = "Recomputed wavelet:" if self._shift_held() else "Wavelet filter applied:"
        suffix = " (Shift)" if self._shift_held() else ""
        self._show_status(
            f"{verb} level {filter_level} | "
            f"{result.n_valid:,} valid pixels | channel: {active_channel}{suffix}"
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
            active_bin = self.data_model.session.active_bin
            result = uc.execute(channel=active_channel, view_bin=active_bin)
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
                    colormap=vp.FLIM_LIFETIME_COLORMAP,
                    blending=vp.FLIM_LIFETIME_BLENDING,
                    **vp._optional_kwargs(
                        opacity=vp.FLIM_LIFETIME_OPACITY,
                        contrast_limits=vp.FLIM_LIFETIME_CONTRAST_OVERRIDE,
                    ),
                )

        if result.mean_tau is not None:
            self._show_status(
                f"Lifetime ({result.source}): mean={result.mean_tau:.2f} ns | "
                f"channel: {active_channel} | freq: {result.frequency_mhz} MHz"
            )
        else:
            self._show_status("Lifetime: no valid pixels")

    # ── Phasor Filters (U6) ──────────────────────────────────

    def _refresh_ref_circle_enabled(self) -> None:
        """Enable / disable the reference-circle checkbox based on metadata.

        The reference-circle filter requires ``flim_frequency_mhz`` in
        dataset metadata; without it, both display and use case paths
        cannot resolve ``(G_c, S_c)``. Disable the checkbox up front so
        the user gets a discoverable error rather than a silent no-op.

        Reads metadata fresh from the repo when possible — ``handle.metadata``
        is a snapshot taken at ``set_dataset`` time and may not reflect a
        post-snapshot TCSPC import that wrote ``flim_frequency_mhz``.
        Falls back to the snapshot when the repo doesn't expose
        ``read_metadata`` (test stubs, transient errors).
        """
        handle = self.data_model.session.dataset
        has_freq = False
        if handle is not None:
            freq = None
            repo = self._get_repo()
            reader = getattr(repo, "read_metadata", None)
            if reader is not None:
                try:
                    freq = reader(handle).get("flim_frequency_mhz")
                except Exception:
                    freq = None
            if freq is None:
                freq = handle.metadata.get("flim_frequency_mhz")
            has_freq = freq is not None
        self._ref_circle_check.setEnabled(has_freq)
        if not has_freq:
            self._ref_circle_check.setChecked(False)
            self._ref_circle_check.setToolTip(
                "Compute phasor first — flim_frequency_mhz needs to be set."
            )
        else:
            self._ref_circle_check.setToolTip(
                "Restrict the phasor histogram and GMM input to pixels inside a "
                "circle on the universal semicircle anchored at the target lifetime."
            )

    def _on_ref_circle_toggled(self, checked: bool) -> None:
        self._ref_circle_tau_spin.setEnabled(checked)
        self._ref_circle_radius_spin.setEnabled(checked)
        self._push_filters_to_phasor_plot()

    def _on_auto_toggled(self, checked: bool) -> None:
        # When Auto is on the spinbox is the upper bound of the BIC/AIC
        # sweep ("n_max"); when off it's the exact n. Update the label
        # in place so the layout doesn't reflow. Criterion combo stays
        # visible — only enable/disable so the layout is stable.
        self._n_label.setText("n_max:" if checked else "n:")
        self._criterion_combo.setEnabled(checked)

    def _push_filters_to_phasor_plot(self) -> None:
        """Push the FlimPanel filter values into the phasor plot.

        Defensive against a closed phasor window — the existing pattern
        for FlimPanel<->phasor wiring already returns ``None`` from
        ``_get_phasor_window`` in that case (see
        ``_on_compute_phasor``).
        """
        phasor_win = self._get_phasor_window()
        if phasor_win is None:
            return
        ref_active = self._ref_circle_check.isChecked()
        try:
            phasor_win.set_phasor_filters(
                intensity_threshold=float(self._intensity_threshold_spin.value()),
                ref_circle_tau_ns=(
                    self._ref_circle_tau_spin.value() if ref_active else None
                ),
                ref_circle_radius=(
                    self._ref_circle_radius_spin.value() if ref_active else None
                ),
            )
        except Exception as e:
            self._show_status(f"Filter update error: {e}")

    # ── Phasor Segmentation (U6) ─────────────────────────────

    def _on_run_gmm(self) -> None:
        """Kick off a GMM run on a QThread worker.

        Returns early on any of: a worker already running (double-click
        race), no active channel, no phasor window. Constructs the
        kwargs dict the use case expects, hands them to a generic
        ``Worker``, and connects ``finished`` / ``error`` slots. Does
        NOT connect ``progress`` — the generic Worker can't inject a
        progress callback into the wrapped use case (see plan U6 K.T.D.).
        """
        # Double-click race guard. Button-disable is the primary defense;
        # this re-check covers the case where two click events sneak past
        # before the disable lands.
        if self._gmm_worker is not None and self._gmm_worker.isRunning():
            return

        active_channel = self._get_active_channel()
        if active_channel is None:
            self._show_status("Select a channel in the viewer first")
            return

        phasor_win = self._get_phasor_window()
        if phasor_win is None:
            self._show_status("Open the Phasor Plot first")
            return

        auto = self._auto_check.isChecked()
        kwargs = dict(
            channel=active_channel,
            shape=self._shape_combo.currentText().lower(),
            n_components=None if auto else int(self._n_clusters_spin.value()),
            criterion=self._criterion_combo.currentText() if auto else None,
            n_max=int(self._n_clusters_spin.value()) if auto else 4,
            # cov_f / shift placement defaults are baked in; the user
            # tunes each placed ROI's per-axis stretch and shift via
            # the Phasor window's Selected-ROI panel.
            cov_f=2.0,
            shift=0.0,
            intensity_threshold=float(self._intensity_threshold_spin.value()),
            ref_circle_tau_ns=(
                self._ref_circle_tau_spin.value()
                if self._ref_circle_check.isChecked()
                else None
            ),
            ref_circle_radius=(
                self._ref_circle_radius_spin.value()
                if self._ref_circle_check.isChecked()
                else None
            ),
            mask_filter_active=phasor_win._mask_filter_check.isChecked(),
            # GMM reads persisted maps from HDF5: wavelet view → g_filtered,
            # otherwise the raw g/s. The on-demand median view is not a
            # persisted resource, so GMM runs on raw g/s when it is active.
            use_filtered_gs=phasor_win._wavelet_check.isChecked(),
            harmonic=int(phasor_win._harmonic_combo.currentText()),
            # Capture session.active_bin at worker-construction (race-safe
            # per the U12 capture pattern) so a mid-flight bin toggle
            # cannot alter the resolution of an in-flight GMM result.
            view_bin=self.data_model.session.active_bin,
        )

        # Lazy import — keeps the use case's transitive sklearn import out
        # of FlimPanel module-load time.
        from percell4.application.use_cases.run_phasor_gmm import RunPhasorGMM
        from percell4.gui.workers import Worker

        uc = RunPhasorGMM(self._get_repo(), self.data_model.session)

        self._run_gmm_btn.setEnabled(False)
        self._show_status("Running GMM…")
        self._gmm_worker = Worker(uc.execute, **kwargs)
        self._gmm_worker.finished.connect(self._on_gmm_finished)
        self._gmm_worker.error.connect(self._on_gmm_error)
        self._gmm_worker.start()

    def _on_gmm_finished(self, result: Any) -> None:
        """Handle a successful GMM run on the main thread.

        The dataset-snapshot mismatch check protects against the user
        switching datasets mid-fit (origin: doc-review P0 / brainstorm
        risks table). When the snapshot doesn't match, we discard the
        result with a status message rather than placing ROIs over a
        dataset that wasn't fitted.
        """
        self._run_gmm_btn.setEnabled(True)

        current = self.data_model.session.dataset
        if current is None or current.path != result.dataset_path:
            self._show_status("Dataset changed mid-GMM — result discarded")
            return

        phasor_win = self._get_phasor_window()
        if phasor_win is None:
            self._show_status("Phasor window closed mid-GMM — result discarded")
            return

        phasor_win.place_gmm_rois(
            result.geometries,
            shape=self._shape_combo.currentText().lower(),
            criterion=result.criterion,
            sampled_pixels=result.sampled_pixels,
        )
        self._show_status(
            f"GMM placed {len(result.geometries)} ROIs (n={result.chosen_n}, "
            f"criterion={result.criterion or 'manual'}, "
            f"sampled {result.sampled_pixels:,} pixels)"
        )

    def _on_gmm_error(self, err: Any) -> None:
        """Handle a GMM run that raised on the worker thread."""
        self._run_gmm_btn.setEnabled(True)
        message = getattr(err, "message", None) or str(err)
        self._show_status(f"GMM error: {message}")
