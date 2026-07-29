---
status: pending
priority: p3
issue_id: "012"
tags: [code-review, tests, workflows]
dependencies: ["009"]
---

# Expand Phase 1 test coverage for edge cases and error paths

## Problem Statement

The initial Phase 1 tests cover the happy path well but leave several gaps that make regressions easy:

1. **`measure_cells_with_masks` numerical equivalence**: the commit message promises "single-pass implementation produces the same numbers as sequential `measure_cells(mask=...)` calls", but the multi-round test only checks column presence + one sanity `area_in_R2 == 0` assertion. A regression that swaps `inside` and `outside` (or fails to broadcast properly) would not be caught. This becomes load-bearing after todo #009's single-pass refactor.

2. **`measure_cells_with_masks(masks={})`** (empty-but-not-None): `test_with_masks_no_masks_matches_measure_cells` only tests `None`. An empty dict is a distinct code path and should be verified to produce whole-cell-only columns.

3. **`RunMetadata.finished_at` round-trip**: the happy path test leaves `finished_at=None`. Add a case that round-trips a stamped `finished_at` since the runner's exception-safety design hinges on stamping it in `finally`.

4. **`config_from_dict` error paths**: corrupted data (missing `"datasets"`, wrong type for `schema_version` (if kept), unknown algorithm string) should fail with clear messages.

5. **`_build_column_list_with_masks` ordering contract**: no test asserts column ordering is stable. The column picker in Phase 2 depends on this being deterministic across runs.

6. **`ThresholdingRound` Unicode word chars**: the regex uses `[A-Za-z0-9_\-]`, not `\w`. No test asserts `"Ca²⁺_high"` is rejected — a Unicode regex change would silently pass.

## Findings

- **kieran-python-reviewer** (S6): numerical equivalence test missing
- **kieran-python-reviewer** (N13, N14): error-path coverage thin
- **kieran-python-reviewer** (N17): `masks={}` path not covered
- **kieran-python-reviewer** (N19): `_build_column_list_with_masks` ordering not tested
- **kieran-python-reviewer** (N23): Unicode regex rejection not tested

## Proposed Solutions

### Option A — Add the missing tests (Recommended)

Add to `tests/test_workflows/test_measurer_with_masks.py`:

```python
def test_multichannel_with_masks_numerical_equivalence(sample_labels, sample_image):
    """Multi-round single-pass must match sequential calls numerically."""
    image_b = sample_image * 2.0
    mask1 = np.zeros_like(sample_labels, dtype=np.uint8); mask1[10:30, 10:20] = 1
    mask2 = np.zeros_like(sample_labels, dtype=np.uint8); mask2[50:70, 60:70] = 1

    single_pass = measure_multichannel_with_masks(
        {"chA": sample_image, "chB": image_b},
        sample_labels,
        masks={"R1": mask1, "R2": mask2},
    )

    # Sequential baseline: measure each mask independently, rename, merge
    seq_no_mask = measure_multichannel({"chA": sample_image, "chB": image_b}, sample_labels)
    seq_r1 = measure_multichannel({"chA": sample_image, "chB": image_b}, sample_labels, mask=mask1)
    seq_r2 = measure_multichannel({"chA": sample_image, "chB": image_b}, sample_labels, mask=mask2)

    # For every metric column in the single-pass result that ends in _in_R1,
    # assert np.allclose against the sequential call's _mask_inside column.
    for col in single_pass.columns:
        if col.endswith("_in_R1"):
            base = col[:-len("_in_R1")]
            seq_col = f"{base}_mask_inside"
            assert np.allclose(
                single_pass[col].to_numpy(), seq_r1[seq_col].to_numpy(), equal_nan=True
            ), f"mismatch on {col}"
        elif col.endswith("_out_R1"):
            base = col[:-len("_out_R1")]
            seq_col = f"{base}_mask_outside"
            assert np.allclose(
                single_pass[col].to_numpy(), seq_r1[seq_col].to_numpy(), equal_nan=True
            )
        # (similar for _in_R2 / _out_R2)


def test_with_empty_masks_dict(sample_labels, sample_image):
    """masks={} is distinct from masks=None but should produce the same columns."""
    df_none = measure_cells_with_masks(sample_image, sample_labels)
    df_empty = measure_cells_with_masks(sample_image, sample_labels, masks={})
    assert list(df_none.columns) == list(df_empty.columns)
    assert len(df_none) == len(df_empty)
```

Add to `tests/test_workflows/test_artifacts.py`:

```python
def test_run_config_roundtrip_with_finished_at(tmp_path):
    folder = create_run_folder(tmp_path)
    cfg = _sample_config()
    meta = _sample_metadata(folder)
    meta.finished_at = datetime(2026, 4, 10, 15, 0, 0, tzinfo=UTC)
    write_run_config(folder, cfg, meta)
    _, loaded = read_run_config(folder)
    assert loaded.finished_at == meta.finished_at


def test_config_from_dict_missing_datasets_key():
    with pytest.raises((KeyError, ValueError)):
        config_from_dict({"cellpose": {}, "thresholding_rounds": []})
```

Add to `tests/test_workflows/test_models.py`:

```python
def test_round_rejects_unicode_name():
    with pytest.raises(ValueError, match="round name"):
        _valid_round(name="Ca²⁺_high")


def test_build_column_list_with_masks_ordering():
    from percell4.measure.measurer import _build_column_list_with_masks
    cols = _build_column_list_with_masks(
        ["mean_intensity", "area"],
        ["R1", "R2"],
    )
    # Core columns first, then whole-cell metrics, then per-round in/out
    assert cols[:8] == [
        "label", "centroid_y", "centroid_x",
        "bbox_y", "bbox_x", "bbox_h", "bbox_w", "area",
    ]
    assert "mean_intensity" in cols
    assert cols.index("mean_intensity_in_R1") < cols.index("mean_intensity_in_R2")
```

- **Pros**: Closes the main coverage gaps. All tests are small and self-contained.
- **Cons**: Six new tests. ~100 LOC of test code.
- **Effort**: Small.

### Option B — Add only the numerical-equivalence test

- **Pros**: Catches the most important regression.
- **Cons**: Leaves the other gaps.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `tests/test_workflows/test_measurer_with_masks.py`
- `tests/test_workflows/test_artifacts.py`
- `tests/test_workflows/test_models.py`

**Pairs with:** todo #009 (the single-pass refactor). Land the refactor and the equivalence test together.

## Acceptance Criteria

- [ ] `test_multichannel_with_masks_numerical_equivalence` passes against the current and refactored implementations
- [ ] `test_with_empty_masks_dict` passes
- [ ] `test_run_config_roundtrip_with_finished_at` passes
- [ ] `test_config_from_dict_missing_datasets_key` passes
- [ ] `test_round_rejects_unicode_name` passes
- [ ] `test_build_column_list_with_masks_ordering` passes

## Work Log

- 2026-04-10 — Gaps identified by kieran-python-reviewer.

## Resources

- Review source: kieran-python-reviewer S6, N13, N14, N17, N19, N23
