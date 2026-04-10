"""Tests for measure_cells_with_masks / measure_multichannel_with_masks."""

from __future__ import annotations

import numpy as np

from percell4.measure.measurer import (
    measure_cells,
    measure_cells_with_masks,
    measure_multichannel,
    measure_multichannel_with_masks,
)


def test_with_masks_no_masks_matches_measure_cells(sample_labels, sample_image):
    """With no masks, the helper should produce the core + whole-cell columns only."""
    a = measure_cells(sample_image, sample_labels)
    b = measure_cells_with_masks(sample_image, sample_labels)
    # Same rows, same whole-cell metric values. b may have extra columns only if
    # masks were provided — here it shouldn't.
    assert list(b["label"]) == list(a["label"])
    for col in ("mean_intensity", "max_intensity", "area"):
        assert np.allclose(b[col].to_numpy(), a[col].to_numpy())


def test_with_masks_single_mask_matches_sequential_calls(
    sample_labels, sample_image
):
    """One round through the masked helper should match one measure_cells(mask=...) call."""
    mask = np.zeros_like(sample_labels, dtype=np.uint8)
    # Half-cover cells 1 and 4.
    mask[10:30, 10:20] = 1  # left half of cell 1
    mask[50:70, 60:70] = 1  # left half of cell 4

    direct = measure_cells(sample_image, sample_labels, mask=mask)
    helper = measure_cells_with_masks(
        sample_image, sample_labels, masks={"R1": mask}
    )

    # Whole-cell columns identical
    for col in ("mean_intensity", "max_intensity", "area"):
        assert np.allclose(helper[col].to_numpy(), direct[col].to_numpy())

    # Helper renames _mask_inside -> _in_R1, _mask_outside -> _out_R1
    for metric in ("mean_intensity", "integrated_intensity", "area"):
        assert np.allclose(
            helper[f"{metric}_in_R1"].to_numpy(),
            direct[f"{metric}_mask_inside"].to_numpy(),
            equal_nan=True,
        )
        assert np.allclose(
            helper[f"{metric}_out_R1"].to_numpy(),
            direct[f"{metric}_mask_outside"].to_numpy(),
            equal_nan=True,
        )


def test_with_masks_two_rounds_no_collision(sample_labels, sample_image):
    """Two rounds in one pass should produce independent column sets."""
    mask1 = np.zeros_like(sample_labels, dtype=np.uint8)
    mask1[10:30, 10:20] = 1

    mask2 = np.zeros_like(sample_labels, dtype=np.uint8)
    mask2[50:70, 60:70] = 1

    df = measure_cells_with_masks(
        sample_image,
        sample_labels,
        masks={"R1": mask1, "R2": mask2},
    )

    for metric in ("mean_intensity", "integrated_intensity", "area"):
        assert f"{metric}_in_R1" in df.columns
        assert f"{metric}_out_R1" in df.columns
        assert f"{metric}_in_R2" in df.columns
        assert f"{metric}_out_R2" in df.columns

    # Cell 1 intersects only mask1 — R1 has non-zero area_in, R2 has zero.
    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["area_in_R1"] > 0
    assert cell1["area_in_R2"] == 0.0


def test_multichannel_with_masks_prefixes_channels(sample_labels, sample_image):
    """measure_multichannel_with_masks should prefix per-channel metric columns."""
    image_b = sample_image * 2.0
    mask = np.zeros_like(sample_labels, dtype=np.uint8)
    mask[10:30, 10:20] = 1

    df = measure_multichannel_with_masks(
        {"chA": sample_image, "chB": image_b},
        sample_labels,
        masks={"R1": mask},
    )

    # Channel-prefixed whole-cell metrics
    assert "chA_mean_intensity" in df.columns
    assert "chB_mean_intensity" in df.columns
    # Channel-prefixed per-round in/out metrics
    assert "chA_mean_intensity_in_R1" in df.columns
    assert "chB_mean_intensity_in_R1" in df.columns
    assert "chA_mean_intensity_out_R1" in df.columns
    assert "chB_mean_intensity_out_R1" in df.columns

    # chB is 2x chA, so whole-cell mean for chB should be 2x chA's.
    pair = df[df["label"] == 1].iloc[0]
    assert np.isclose(pair["chB_mean_intensity"], 2 * pair["chA_mean_intensity"])


def test_with_masks_empty_labels_returns_empty_df():
    labels = np.zeros((20, 20), dtype=np.int32)
    image = np.ones((20, 20), dtype=np.float32)
    mask = np.zeros((20, 20), dtype=np.uint8)

    df = measure_cells_with_masks(image, labels, masks={"R1": mask})

    assert len(df) == 0
    # Column scaffold should include the per-round in/out slots even when empty
    assert "mean_intensity_in_R1" in df.columns
    assert "mean_intensity_out_R1" in df.columns


def test_measure_multichannel_unchanged(sample_labels, sample_image):
    """Sanity: the existing measure_multichannel API is untouched."""
    df = measure_multichannel({"chA": sample_image}, sample_labels)
    assert "chA_mean_intensity" in df.columns
    assert "_in_R1" not in "".join(df.columns)
