"""U3 regression tests: ThresholdQCController must not crash on NaN.

The dilute-phase workflow passes a `channel_image` containing NaN where
prior rounds have already subtracted condensed-phase pixels. Two
surfaces inside the controller historically choked on non-finite input:

  1. Construction's sigma smoothing — fixed by U2 (dispatches to the
     NaN-safe Gaussian when needed).
  2. The per-pixel threshold paths inside `_show_group_qc`,
     `_update_preview`, `_on_accept`, and `_update_stats_display` —
     skimage threshold functions raise on NaN, and `pixels.max()` on a
     NaN-containing slice returns NaN.

These tests pin the NaN-aware behavior end-to-end against the real
ThresholdQCController (with a small fake viewer/store) — no big mocks,
no monkeypatches.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from percell4.domain.measure.grouper import GroupingResult


@pytest.fixture
def fake_viewer(qtbot):
    """Minimal stand-in for ViewerWindow's surface used by the
    controller's _show_group_qc/_show_group_preview/_cleanup_all
    paths. Only the bits the controller actually pokes."""
    from unittest.mock import MagicMock

    class _Layers(list):
        def remove(self, item):
            super().remove(item)

    viewer = MagicMock()
    viewer.layers = _Layers()
    viewer.add_image = MagicMock()
    viewer.add_labels = MagicMock()
    viewer.add_shapes = MagicMock()

    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    viewer_win.show = MagicMock()
    viewer_win.add_mask = MagicMock()
    return viewer_win


def _make_grouping_result(cell_labels: np.ndarray) -> GroupingResult:
    """Two-group result splitting cells in half by index."""
    n = len(cell_labels)
    half = n // 2
    assignments = pd.Series(
        data=np.array([1] * half + [2] * (n - half), dtype=int),
        index=pd.Index(cell_labels, name="label"),
        name="group",
    )
    return GroupingResult(
        group_assignments=assignments,
        n_groups=2,
        group_means=[1.0, 5.0],
    )


def test_construction_does_not_raise_for_nan_channel_image(qtbot, fake_viewer):
    """Pre-U3, sigma smoothing went through scipy.gaussian_filter which
    poisons every pixel with NaN; the U2 dispatch sends NaN inputs to
    nan_safe_gaussian_filter, so construction is safe."""
    from unittest.mock import MagicMock

    from percell4.gui.threshold_qc import ThresholdQCController

    H, W = 16, 16
    image = np.ones((H, W), dtype=np.float32)
    image[:8, :] = np.nan  # top half NaN

    seg_labels = np.zeros((H, W), dtype=np.int32)
    seg_labels[10:12, 4:6] = 1
    seg_labels[10:12, 10:12] = 2
    grouping_result = _make_grouping_result(np.array([1, 2], dtype=np.int32))

    data_model = MagicMock()
    data_model.df = pd.DataFrame()
    data_model.session = MagicMock()

    controller = ThresholdQCController(
        viewer_win=fake_viewer,
        data_model=data_model,
        store=None,
        grouping_result=grouping_result,
        channel_image=image,
        seg_labels=seg_labels,
        channel="ch0",
        metric="mean_intensity",
        sigma=1.5,
        mask_name="m",
    )

    # The smoothed image should have its top half still NaN (those
    # pixels' kernel footprints are all NaN until far from the seam).
    # The finite-half pixels (rows 10-12) should remain finite.
    finite_finite_half = controller._smoothed_image[10:12, 4:6]
    assert np.all(np.isfinite(finite_finite_half))


def test_initial_threshold_computation_filters_nan(qtbot, fake_viewer):
    """`_show_group_qc` extracts pixels in the group's cells, then asks
    THRESHOLD_METHODS['otsu'] for an initial threshold. NaN pixels must
    be filtered out before the call — otherwise skimage raises."""
    from unittest.mock import MagicMock

    from percell4.gui.threshold_qc import ThresholdQCController

    H, W = 16, 16
    image = np.ones((H, W), dtype=np.float32)
    # The cells for group 1 land in rows 2-4, which are FULLY NaN.
    image[:5, :] = np.nan
    image[5:, :] = 1.0  # group 2's cells live in finite half

    seg_labels = np.zeros((H, W), dtype=np.int32)
    seg_labels[2:4, 2:4] = 1   # group 1 — entirely in NaN half
    seg_labels[8:10, 2:4] = 2  # group 2 — finite half

    grouping_result = _make_grouping_result(np.array([1, 2], dtype=np.int32))

    data_model = MagicMock()
    data_model.df = pd.DataFrame()
    data_model.session = MagicMock()

    controller = ThresholdQCController(
        viewer_win=fake_viewer,
        data_model=data_model,
        store=None,
        grouping_result=grouping_result,
        channel_image=image,
        seg_labels=seg_labels,
        channel="ch0",
        metric="mean_intensity",
        sigma=0.0,  # skip smoothing to isolate the threshold-path fix
        mask_name="m",
    )

    # Drive _show_group_qc for group 1 (all pixels NaN). Must not raise.
    controller._current_index = 0
    # Build the QC dock requires _viewer_win.viewer.layers contents.
    # We can pre-empt the dock build by skipping it — call the slice we
    # care about: the threshold computation.
    try:
        controller._show_group_qc()
    except (ValueError, TypeError) as e:
        pytest.fail(f"_show_group_qc must not raise on all-NaN group: {e!r}")


def test_accepted_mask_excludes_nan_pixels(qtbot, fake_viewer):
    """When the user accepts a round, the resulting mask must NOT
    include any NaN pixels — even if they technically pass the
    (image > value) comparison (NaN > value is False, but pin this
    via an explicit np.isfinite filter as defense-in-depth)."""
    from unittest.mock import MagicMock

    from percell4.gui.threshold_qc import ThresholdQCController

    H, W = 8, 8
    image = np.full((H, W), 10.0, dtype=np.float32)
    image[0:2, :] = np.nan  # top two rows NaN

    seg_labels = np.zeros((H, W), dtype=np.int32)
    seg_labels[0:6, 0:6] = 1  # one cell spans both NaN and finite
    grouping_result = _make_grouping_result(np.array([1], dtype=np.int32))
    grouping_result = GroupingResult(
        group_assignments=pd.Series(
            data=np.array([1], dtype=int),
            index=pd.Index([1], name="label"),
            name="group",
        ),
        n_groups=1,
        group_means=[10.0],
    )

    data_model = MagicMock()
    data_model.df = pd.DataFrame()
    data_model.session = MagicMock()

    controller = ThresholdQCController(
        viewer_win=fake_viewer,
        data_model=data_model,
        store=None,
        grouping_result=grouping_result,
        channel_image=image,
        seg_labels=seg_labels,
        channel="ch0",
        metric="mean_intensity",
        sigma=0.0,
        mask_name="m",
    )
    controller._current_index = 0
    controller._current_group_mask = seg_labels == 1
    controller._current_threshold = 5.0
    controller._current_method = "otsu"

    # Build the accepted mask via the controller's _on_accept path.
    # We bypass the QC dock and just call the mask-build branch.
    controller._groups[0].mask = None  # ensure fresh
    controller._group_image_buffer = image  # match what _show_group_qc would set
    controller._on_accept()

    accepted = controller._groups[0].mask
    assert accepted is not None
    # No mask pixel sits where the channel image is NaN.
    assert not accepted[0:2, :].any(), (
        "Accepted mask must exclude NaN-region pixels even after the "
        "user accepts the round. Bug class: NaN > value returns False, "
        "but defensive np.isfinite(image) keeps the contract explicit."
    )
    # Finite pixels in the cell that are above threshold survive.
    assert accepted[2:6, 0:6].all()
