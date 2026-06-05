"""Build a gallery of binary masks for every detection method tried, + stats.

Computes the k-means intensity grouping ONCE (deterministic, shared across all
recipes so the masks are directly comparable), then generates each recipe's mask
and writes it as a binary PNG (white foci on black). Emits stats.json with the
per-mask method metadata + pixel/component counts that the methods document is
built from.

Two demonstrations are wired in explicitly:
  * plain Otsu on the background-subtracted residual across 6 background methods
    -> shows the mask is invariant to (constant) background subtraction.
  * floored Otsu (exclusion floor) across the same 6 backgrounds + a floor sweep
    -> shows the exclusion floor makes Otsu more conservative AND background-dependent.

Run:
    python scripts/build_mask_gallery.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from skimage import measure
from skimage.io import imsave

from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.store import DatasetStore
from percell4.workflows.models import (
    PunctaDetectorSettings,
    ThresholdAlgorithm,
    ThresholdingRound,
)
from percell4.workflows.phases import _apply_puncta_groups, threshold_compute_one

DATASET = "/Volumes/NX-01-A/2026-05-28_datasets/Dish 2 TAOK2 KO 60min As + Noco.h5"
CHANNEL = "mNG"
SEG = "cp_mask"
OUT = Path("puncta_mask_gallery")
IMG = OUT / "images"

# Each recipe: key, section, label, and the detector/seed/bg/params that define it.
# section -> headline grouping in the document.
RECIPES = [
    # --- winner + manual reference handled specially below ---
    # Section 1: plain Otsu (both passes) on the background-subtracted residual.
    dict(key="otsu_bg_gaussian_peak", section="1-otsu-bg-invariance",
         detector="otsu", seed="otsu", bg="gaussian-peak", dp={}, label="Otsu, bg=gaussian-peak"),
    dict(key="otsu_bg_mad", section="1-otsu-bg-invariance",
         detector="otsu", seed="otsu", bg="mad", dp={}, label="Otsu, bg=MAD (median)"),
    dict(key="otsu_bg_percentile", section="1-otsu-bg-invariance",
         detector="otsu", seed="otsu", bg="percentile", dp={}, label="Otsu, bg=percentile(50)"),
    dict(key="otsu_bg_donut_median", section="1-otsu-bg-invariance",
         detector="otsu", seed="otsu", bg="donut-median", dp={"buffer_px": 5, "donut_px": 4},
         label="Otsu, bg=donut-median"),
    dict(key="otsu_bg_donut_mean", section="1-otsu-bg-invariance",
         detector="otsu", seed="otsu", bg="donut-mean", dp={"buffer_px": 5, "donut_px": 4},
         label="Otsu, bg=donut-mean"),
    dict(key="otsu_bg_rolling_ball", section="1-otsu-bg-invariance",
         detector="otsu", seed="otsu", bg="rolling-ball", dp={}, label="Otsu, bg=rolling-ball (surface)"),

    # Section 2: floored Otsu (exclusion floor) across the same backgrounds.
    dict(key="floored_otsu_bg_gaussian_peak", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="gaussian-peak", dp={"floor_k": 1.0},
         label="Floored Otsu (floor_k=1.0), bg=gaussian-peak"),
    dict(key="floored_otsu_bg_mad", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="mad", dp={"floor_k": 1.0},
         label="Floored Otsu (floor_k=1.0), bg=MAD"),
    dict(key="floored_otsu_bg_percentile", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="percentile", dp={"floor_k": 1.0},
         label="Floored Otsu (floor_k=1.0), bg=percentile"),
    dict(key="floored_otsu_bg_donut_median", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="donut-median", dp={"floor_k": 1.0, "buffer_px": 5, "donut_px": 4},
         label="Floored Otsu (floor_k=1.0), bg=donut-median"),
    dict(key="floored_otsu_bg_donut_mean", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="donut-mean", dp={"floor_k": 1.0, "buffer_px": 5, "donut_px": 4},
         label="Floored Otsu (floor_k=1.0), bg=donut-mean"),
    dict(key="floored_otsu_bg_rolling_ball", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="rolling-ball", dp={"floor_k": 1.0},
         label="Floored Otsu (floor_k=1.0), bg=rolling-ball"),
    # floor sweep at fixed bg (gaussian-peak) — the exclusion knob.
    dict(key="floored_otsu_floor_0p5", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="gaussian-peak", dp={"floor_k": 0.5},
         label="Floored Otsu floor_k=0.5"),
    dict(key="floored_otsu_floor_1p5", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="gaussian-peak", dp={"floor_k": 1.5},
         label="Floored Otsu floor_k=1.5"),
    dict(key="floored_otsu_floor_2p0", section="2-floored-otsu-conservative",
         detector="otsu-floored", seed="otsu", bg="gaussian-peak", dp={"floor_k": 2.0},
         label="Floored Otsu floor_k=2.0"),

    # Section 3: Otsu re-thresholded inside an expanded ROI (region exclusion).
    dict(key="refine_otsu_particle_e10", section="3-otsu-roi-refinement",
         detector="refine-otsu", seed="otsu", bg="gaussian-peak", dp={"expand_px": 10},
         label="Per-particle ROI Otsu (expand=10)"),
    dict(key="refine_otsu_cell_e10", section="3-otsu-roi-refinement",
         detector="refine-cell-otsu", seed="otsu", bg="gaussian-peak", dp={"unit": "cell", "expand_px": 10},
         label="Per-cell ROI Otsu (expand=10)"),
    dict(key="refine_otsu_group_e10", section="3-otsu-roi-refinement",
         detector="refine-cell-otsu", seed="otsu", bg="gaussian-peak", dp={"unit": "group", "expand_px": 10},
         label="Per-group ROI Otsu (expand=10)"),

    # Section 4: adaptive local threshold (the winning family).
    dict(key="adaptive_w15_k20", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 15, "k": 2.0},
         label="Adaptive window=15 k=2.0"),
    dict(key="adaptive_w15_k225_WINNER", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 15, "k": 2.25},
         label="Adaptive window=15 k=2.25  *** SELECTED ***"),
    dict(key="adaptive_w15_k25", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 15, "k": 2.5},
         label="Adaptive window=15 k=2.5"),
    dict(key="adaptive_w15_k275", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 15, "k": 2.75},
         label="Adaptive window=15 k=2.75"),
    dict(key="adaptive_w11_k20", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 11, "k": 2.0},
         label="Adaptive window=11 k=2.0"),
    dict(key="adaptive_w9_k20", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 9, "k": 2.0},
         label="Adaptive window=9 k=2.0"),
    dict(key="adaptive_w25_k20", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 25, "k": 2.0},
         label="Adaptive window=25 k=2.0"),
    dict(key="adaptive_w41_k20", section="4-adaptive-winner",
         detector="adaptive", seed="otsu", bg="gaussian-peak", dp={"window_px": 41, "k": 2.0},
         label="Adaptive window=41 k=2.0"),

    # Section 5: other detectors tried.
    dict(key="log_blob_tr05", section="5-other-detectors",
         detector="log", seed="log", bg="gaussian-peak", dp={"threshold_rel": 0.05}, sp={"k": 2.5},
         label="LoG blob detector (threshold_rel=0.05) — REJECTED (disk shapes)"),
    dict(key="dog_blob_tr05", section="5-other-detectors",
         detector="dog", seed="log", bg="gaussian-peak", dp={"threshold_rel": 0.05}, sp={"k": 2.5},
         label="DoG blob detector (threshold_rel=0.05) — REJECTED (disk shapes)"),
    dict(key="bg_k_sigma_mad_k25", section="5-other-detectors",
         detector="bg-k-sigma", seed="otsu", bg="mad", dp={"k": 2.5}, label="Global k·sigma, bg=MAD, k=2.5"),
    dict(key="bg_k_sigma_mad_k20", section="5-other-detectors",
         detector="bg-k-sigma", seed="otsu", bg="mad", dp={"k": 2.0}, label="Global k·sigma, bg=MAD, k=2.0"),
    dict(key="white_tophat_r4_k25", section="5-other-detectors",
         detector="white-tophat", seed="otsu", bg="gaussian-peak", dp={"r": 4, "k": 2.5},
         label="White top-hat (r=4) at k·MAD=2.5"),
    dict(key="h_maxima_k25", section="5-other-detectors",
         detector="h-maxima", seed="otsu", bg="gaussian-peak", dp={"k": 2.5}, label="h-maxima (h=2.5·sigma)"),
]


def stats(mask: np.ndarray) -> dict:
    a = np.asarray(mask) > 0
    lab = measure.label(a)
    pr = measure.regionprops(lab)
    ar = np.array([p.area for p in pr]) if pr else np.array([0])
    return dict(px=int(a.sum()), n_foci=int(lab.max()),
                median_area=float(np.median(ar)), mean_area=float(round(ar.mean(), 1)))


def main() -> int:
    IMG.mkdir(parents=True, exist_ok=True)
    store = DatasetStore(DATASET)
    names = list(store.metadata.get("channel_names", []))
    idx = names.index(CHANNEL)

    base = ThresholdingRound(
        name="gallery_grouping", channel=CHANNEL, metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=3, gaussian_sigma=1.0,
        puncta=PunctaDetectorSettings(detector_name="adaptive", seed_detector_name="otsu",
            background_estimator_name="gaussian-peak", detector_params={"window_px": 15, "k": 2.25},
            seed_params={"k": 2.5}, min_spot_px=3, spot_scale_prior=(1.0, 4.0)),
    )
    grouping, fail, msg = threshold_compute_one(store, base, seg_name=SEG)
    if fail is not None:
        print("grouping failed:", fail, msg)
        return 1
    print(f"grouping: {grouping.n_groups} groups")

    image = store.read_channel("intensity", idx)
    smoothed = apply_gaussian_smoothing(image.astype(np.float32), 1.0)
    labels = store.read_labels(SEG)

    results: list[dict] = []

    def save(key, label, section, mask, **meta):
        path = IMG / f"{key}.png"
        imsave(path, (np.asarray(mask) > 0).astype(np.uint8) * 255, check_contrast=False)
        row = dict(key=key, file=f"images/{key}.png", section=section, label=label, **meta, **stats(mask))
        results.append(row)
        print(f"{key:32s} {row['px']:>8d}px {row['n_foci']:>6d} foci  mean {row['mean_area']:>5.1f}")

    # Manual reference + grayscale.
    save("manual_SG_mask", "Manual hand-QC mask (the bar to beat)", "0-reference",
         np.asarray(store.read_mask("SG_mask")), method="manual", background="-", params="-")
    lo, hi = np.percentile(image, [1, 99.5])
    gray = np.clip((image.astype(np.float32) - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
    imsave(IMG / "reference_mNG_grayscale.png", gray, check_contrast=False)

    # Plain whole-group Otsu, NO background (legacy path baseline).
    from skimage.filters import threshold_otsu as sk_otsu
    nobg = np.zeros(labels.shape, dtype=np.uint8)
    for gid in range(1, grouping.n_groups + 1):
        cells = grouping.group_assignments.index[grouping.group_assignments.values == gid].to_numpy(np.int32)
        gmask = np.isin(labels, list(cells))
        gp = smoothed[gmask]
        if gp.size and np.unique(gp).size >= 2:
            nobg |= gmask & (smoothed >= float(sk_otsu(gp)))
    save("otsu_no_background", "Plain whole-group Otsu (no background subtraction)",
         "1-otsu-bg-invariance", nobg, method="otsu", background="none", params="-")

    # All pipeline recipes.
    for r in RECIPES:
        puncta = PunctaDetectorSettings(
            detector_name=r["detector"], seed_detector_name=r["seed"],
            background_estimator_name=r["bg"], detector_params=r.get("dp", {}),
            seed_params=r.get("sp", {"k": 2.5}), min_spot_px=3, spot_scale_prior=(1.0, 4.0),
        )
        combined = np.zeros(labels.shape, dtype=np.uint8)
        err = _apply_puncta_groups(smoothed, labels, grouping, puncta, combined, r["key"])
        if err:
            print(f"{r['key']}: ERROR {err}")
            continue
        save(r["key"], r["label"], r["section"], combined,
             method=r["detector"], background=r["bg"], params=json.dumps(r.get("dp", {})))

    (OUT / "stats.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {len(results)} masks + stats.json to {OUT}/")

    # ---- verification of the two headline claims ----
    by = {r["key"]: r for r in results}
    print("\n=== VERIFY: plain Otsu is invariant to (scalar) background subtraction ===")
    scalar = ["otsu_no_background", "otsu_bg_gaussian_peak", "otsu_bg_mad",
              "otsu_bg_percentile", "otsu_bg_donut_median", "otsu_bg_donut_mean"]
    pxs = {k: by[k]["px"] for k in scalar if k in by}
    print("  px per scalar-bg Otsu:", pxs)
    print("  all identical:", len(set(pxs.values())) == 1)
    if "otsu_bg_rolling_ball" in by:
        print("  rolling-ball (surface) px:", by["otsu_bg_rolling_ball"]["px"], "(expected to differ)")
    print("=== VERIFY: floored Otsu is more conservative than plain Otsu ===")
    plain = by.get("otsu_bg_gaussian_peak", {}).get("n_foci")
    floored = {k: by[k]["n_foci"] for k in by if k.startswith("floored_otsu_bg")}
    print(f"  plain Otsu foci={plain}; floored foci={floored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
