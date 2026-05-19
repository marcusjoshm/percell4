"""Tests for the shared ``GroupedThresholdSettingsWidget`` (U4).

Covers the public surface — ``current_config()`` snapshotting, the
GMM/K-means group visibility toggle, ``set_enabled``, and the aggregated
``config_changed`` signal — plus a regression test confirming the
existing ``GroupedSegPanel`` still reads the right values after the
refactor.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest


# ──────────────────────────────────────────────────────────────
# Standalone-widget tests
# ──────────────────────────────────────────────────────────────


def test_default_config_matches_documented_defaults(qtbot) -> None:
    """Constructor defaults match the values from
    ``grouped_seg_panel.py:67-134`` at the time of extraction.
    """
    from percell4.gui._grouped_threshold_settings import (
        GroupedThresholdConfig,
        GroupedThresholdSettingsWidget,
    )

    widget = GroupedThresholdSettingsWidget()
    qtbot.addWidget(widget)

    assert widget.current_config() == GroupedThresholdConfig(
        metric="mean_intensity",
        algorithm="GMM",
        gmm_criterion="bic",
        gmm_max_components=10,
        kmeans_n_clusters=3,
        sigma=0.0,
    )


def test_programmatic_changes_reflected_in_current_config(qtbot) -> None:
    """Changing the underlying widgets is visible in the next snapshot."""
    from percell4.gui._grouped_threshold_settings import (
        GroupedThresholdSettingsWidget,
    )

    widget = GroupedThresholdSettingsWidget()
    qtbot.addWidget(widget)

    widget._metric_combo.setCurrentText("max_intensity")
    widget._algo_combo.setCurrentText("K-means")
    widget._sigma.setValue(2.5)
    widget._n_clusters.setValue(5)

    config = widget.current_config()
    assert config.metric == "max_intensity"
    assert config.algorithm == "K-means"
    assert config.sigma == pytest.approx(2.5)
    assert config.kmeans_n_clusters == 5


def test_switching_to_kmeans_toggles_visibility_and_config(qtbot) -> None:
    """Algorithm switch hides GMM options, shows K-means options, and the
    snapshot reflects the K-means cluster count."""
    from percell4.gui._grouped_threshold_settings import (
        GroupedThresholdSettingsWidget,
    )

    widget = GroupedThresholdSettingsWidget()
    qtbot.addWidget(widget)
    widget.show()  # required for visibility assertions

    assert widget._gmm_group.isVisible()
    assert not widget._kmeans_group.isVisible()

    widget._algo_combo.setCurrentText("K-means")
    widget._n_clusters.setValue(7)

    assert not widget._gmm_group.isVisible()
    assert widget._kmeans_group.isVisible()

    config = widget.current_config()
    assert config.algorithm == "K-means"
    assert config.kmeans_n_clusters == 7


def test_config_changed_fires_on_metric_edit(qtbot) -> None:
    """``config_changed`` fires when the metric combo changes."""
    from percell4.gui._grouped_threshold_settings import (
        GroupedThresholdSettingsWidget,
    )

    widget = GroupedThresholdSettingsWidget()
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.config_changed, timeout=200):
        widget._metric_combo.setCurrentText("max_intensity")


def test_config_changed_fires_on_sigma_edit(qtbot) -> None:
    """``config_changed`` fires when the σ spinbox value changes."""
    from percell4.gui._grouped_threshold_settings import (
        GroupedThresholdSettingsWidget,
    )

    widget = GroupedThresholdSettingsWidget()
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.config_changed, timeout=200):
        widget._sigma.setValue(1.5)


def test_config_changed_fires_on_algorithm_switch(qtbot) -> None:
    """``config_changed`` fires when the algorithm combo changes."""
    from percell4.gui._grouped_threshold_settings import (
        GroupedThresholdSettingsWidget,
    )

    widget = GroupedThresholdSettingsWidget()
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.config_changed, timeout=200):
        widget._algo_combo.setCurrentText("K-means")


def test_set_enabled_toggles_every_child(qtbot) -> None:
    """``set_enabled(False)`` disables every contained widget;
    ``set_enabled(True)`` re-enables them."""
    from percell4.gui._grouped_threshold_settings import (
        GroupedThresholdSettingsWidget,
    )

    widget = GroupedThresholdSettingsWidget()
    qtbot.addWidget(widget)

    children = [
        widget._metric_combo,
        widget._algo_combo,
        widget._criterion_combo,
        widget._max_components,
        widget._n_clusters,
        widget._sigma,
        widget._gmm_group,
        widget._kmeans_group,
        widget._thresh_group,
    ]

    widget.set_enabled(False)
    for child in children:
        assert not child.isEnabled(), f"{child} should be disabled"

    widget.set_enabled(True)
    for child in children:
        assert child.isEnabled(), f"{child} should be re-enabled"


# ──────────────────────────────────────────────────────────────
# Regression: GroupedSegPanel still reads via the settings widget
# ──────────────────────────────────────────────────────────────


def _build_panel(qtbot, *, channel="ch0", active_seg="seg", existing_masks=None):
    """Construct a GroupedSegPanel with mocked store + viewer.

    Mirrors the fixture in ``test_grouped_seg_panel_name_prompt.py`` so the
    integration regression test exercises the same call path.
    """
    from percell4.gui.grouped_seg_panel import GroupedSegPanel
    from percell4.model import CellDataModel

    existing_masks = existing_masks or []

    model = CellDataModel()
    model.session.set_active_channel(channel)
    model.session.set_active_segmentation(active_seg)

    store = MagicMock()
    store.list_masks.return_value = list(existing_masks)

    def make_layer(name, klass_name):
        layer = MagicMock()
        layer.__class__ = type(klass_name, (MagicMock,), {})
        layer.__class__.__name__ = klass_name
        layer.name = name
        layer.data = np.zeros((10, 10), dtype=np.uint16)
        return layer

    channel_layer = make_layer(channel, "Image")
    seg_layer = make_layer(active_seg, "Labels")

    viewer = MagicMock()
    viewer.layers = [channel_layer, seg_layer]
    viewer_win = MagicMock()
    viewer_win.viewer = viewer

    panel = GroupedSegPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: viewer_win,
        show_status=lambda _msg: None,
    )
    qtbot.addWidget(panel)
    return panel, store


def test_panel_on_run_reads_metric_sigma_through_settings_widget(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the U4 refactor, ``_on_run`` reads metric/σ from
    ``settings_widget.current_config()`` rather than poking private combos.
    Drive a value into the new widget and confirm the value reaches the
    downstream measure-or-group dispatch.
    """
    from percell4.gui import grouped_seg_panel as gsp_module

    panel, _store = _build_panel(qtbot)

    # Drive non-default values through the new widget surface
    panel._settings_widget._metric_combo.setCurrentText("max_intensity")
    panel._settings_widget._sigma.setValue(3.5)

    monkeypatch.setattr(
        gsp_module, "prompt_for_resource_name", lambda *a, **kw: "regression_mask"
    )

    captured: dict = {}

    def fake_measure(channel, channel_image, seg_labels, metric, sigma, mask_name):
        captured["metric"] = metric
        captured["sigma"] = sigma
        captured["mask_name"] = mask_name

    panel._auto_measure_then_group = fake_measure
    panel._run_grouping = MagicMock()

    panel._on_run()

    # Empty DataFrame → routes through auto-measure path
    assert captured["metric"] == "max_intensity"
    assert captured["sigma"] == pytest.approx(3.5)
    assert captured["mask_name"] == "regression_mask"


def test_panel_run_grouping_reads_algorithm_through_settings_widget(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run_grouping`` reads algorithm + GMM/K-means options from the
    settings widget's snapshot, not from removed private attributes.
    """
    import pandas as pd

    from percell4.gui import grouped_seg_panel as gsp_module
    from percell4.gui.workers import Worker

    panel, _store = _build_panel(qtbot)

    # Seed measurements so _run_grouping doesn't bail
    panel.data_model.set_measurements(
        pd.DataFrame({"label": [1, 2, 3], "ch0_mean_intensity": [1.0, 2.0, 3.0]})
    )

    panel._settings_widget._algo_combo.setCurrentText("K-means")
    panel._settings_widget._n_clusters.setValue(4)

    # Intercept Worker construction so we capture the keyword arguments
    captured_kwargs: dict = {}

    class FakeWorker:
        def __init__(self, fn, *args, **kwargs):
            captured_kwargs["fn_name"] = fn.__name__
            captured_kwargs["kwargs"] = kwargs
            self.finished = MagicMock()
            self.error = MagicMock()

        def start(self):
            pass

    monkeypatch.setattr(gsp_module, "__name__", gsp_module.__name__)  # no-op for clarity
    # The panel imports Worker lazily inside the method; patch via the source module
    monkeypatch.setattr("percell4.gui.workers.Worker", FakeWorker)

    panel._run_grouping(
        channel="ch0",
        channel_image=np.zeros((10, 10), dtype=np.uint16),
        seg_labels=np.zeros((10, 10), dtype=np.int32),
        metric="mean_intensity",
        sigma=0.0,
        mask_name="regression",
    )

    # K-means routes to group_cells_kmeans with n_clusters from the widget
    assert captured_kwargs["fn_name"] == "group_cells_kmeans"
    assert captured_kwargs["kwargs"] == {"n_clusters": 4}
