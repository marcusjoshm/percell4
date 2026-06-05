// =====================================================================
// Background + k*sigma clipping  —  ImageJ/Fiji macro
// =====================================================================
// A faithful port of PerCell4's `bg-k-sigma` puncta detector (the global,
// per-region statistical threshold), paired with the robust `mad` background
// estimate. For a region it estimates a background level and a noise scale,
// then keeps every pixel brighter than  background + k * sigma.
//
//   Robust (default, matches PerCell4 `bgks_mad`):
//       background = median(region)
//       sigma      = 1.4826 * MAD(region)        MAD = median(|x - median|)
//       threshold  = background + k * sigma
//   Gaussian alternative:
//       background = mean(region),  sigma = stddev(region)
//
// The robust estimate ignores the bright-foci tail (foci don't drag the
// background/noise up), which is why PerCell4 prefers it over mean/stddev.
//
// Region modes:
//   * Whole image (or the current selection) — one threshold for the region.
//     Drawing an ROI first reproduces the "circle a region and clip" workflow.
//   * Per ROI (ROI Manager) — one independent background+sigma per ROI, unioned
//     into a single mask. This mirrors PerCell4's per-group / per-cell isolation.
//
// NOTE: this is the *global per-region* threshold. The method PerCell4 actually
// selected for production is the LOCAL `adaptive` threshold (each pixel vs its
// own ~15 px neighborhood), which suppresses the diffuse "dilute phase" a single
// per-region cut lets through. See the companion note at the end of this file.
// =====================================================================

macro "Background plus k-sigma clip" {

    if (nImages == 0) exit("Open an image first.");

    Dialog.create("Background + k*sigma clip");
    Dialog.addNumber("k  (sigma multiplier)", 2.5);
    Dialog.addNumber("Gaussian pre-smooth sigma (0 = none)", 1.0);
    Dialog.addNumber("Minimum spot size (px, 0 = none)", 3);
    Dialog.addChoice("Background / noise estimate",
        newArray("median + MAD (robust)", "mean + stddev"), "median + MAD (robust)");
    Dialog.addChoice("Region mode",
        newArray("Whole image (or current selection)", "Per ROI (ROI Manager)"),
        "Whole image (or current selection)");
    Dialog.show();

    k       = Dialog.getNumber();
    gsigma  = Dialog.getNumber();
    minSize = Dialog.getNumber();
    robust  = (Dialog.getChoice() == "median + MAD (robust)");
    perRoi  = (Dialog.getChoice() == "Per ROI (ROI Manager)");

    setOption("BlackBackground", true);
    setBackgroundColor(0, 0, 0);   // so "Clear Outside" zeros (not whitens) outside the ROI
    orig = getTitle();
    estName = "mean+std";
    if (robust) estName = "median+MAD";
    print("\\n--- bg + k*sigma clip on '" + orig + "'  (k=" + k +
          ", smooth=" + gsigma + ", minSize=" + minSize +
          ", " + estName + ") ---");

    // If we are NOT in per-ROI mode but the user has drawn a selection, capture
    // it into the ROI Manager so the whole pipeline runs through one clean path.
    tempRoi = -1;
    if (!perRoi && selectionType() != -1) {
        roiManager("add");
        tempRoi = roiManager("count") - 1;
    }

    // Full-size 32-bit working copy (so subtract/abs never clip at 0).
    selectWindow(orig);
    run("Select None");
    run("Duplicate...", "title=__work");
    work = getTitle();
    run("32-bit");
    if (gsigma > 0) run("Gaussian Blur...", "sigma=" + gsigma);

    getDimensions(W, H, channels, slices, frames);
    newImage("__mask", "8-bit black", W, H, 1);   // 0/255 accumulator

    // Build the list of regions to process.
    if (perRoi) {
        n = roiManager("count");
        if (n == 0) { cleanup(work, tempRoi); exit("Per-ROI mode needs ROIs in the ROI Manager."); }
        for (i = 0; i < n; i++) {
            T = computeThreshold(work, robust, k, i);
            paintRegion(work, "__mask", T, i);
            print("ROI " + (i + 1) + "/" + n + ":  threshold = " + d2s(T, 3));
        }
    } else if (tempRoi >= 0) {
        T = computeThreshold(work, robust, k, tempRoi);
        paintRegion(work, "__mask", T, tempRoi);
        print("threshold = " + d2s(T, 3) + "  (within drawn selection)");
    } else {
        T = computeThreshold(work, robust, k, -1);
        paintRegion(work, "__mask", T, -1);
        print("threshold = " + d2s(T, 3) + "  (whole image)");
    }

    // Always surface the raw mask first, then optionally swap in the size-filtered one.
    selectWindow("__mask");
    run("Select None");
    rename(orig + "_bgksigma_mask");
    result = getTitle();
    if (minSize > 0) {
        run("Analyze Particles...", "size=" + minSize + "-Infinity pixel show=Masks clear");
        if (isOpen("Mask of " + result)) {
            selectWindow(result); close();
            selectWindow("Mask of " + result);
            rename(result);
        }
    }
    selectWindow(result);

    // Count + report.
    run("Set Measurements...", "area redirect=None decimal=2");
    run("Analyze Particles...", "size=0-Infinity pixel summarize");

    cleanup(work, tempRoi);
    selectWindow(result);
    print("Done -> " + result);
}

// --- background + k*sigma threshold for one region ---
// roiIndex >= 0 : that ROI Manager ROI;  -1 : whole image.
function computeThreshold(workTitle, robust, k, roiIndex) {
    selectWindow(workTitle);
    if (roiIndex >= 0) roiManager("select", roiIndex);
    else run("Select None");

    if (!robust) {
        getStatistics(area, mean, min, max, std);   // honors the active selection
        return mean + k * std;
    }

    med = getValue("Median");                        // honors the active selection
    // MAD = median(|x - med|) within the same region. Duplicating a selection
    // crops to its bounding box and keeps the (non-rectangular) ROI, so the
    // median below is still measured inside the region.
    run("Duplicate...", "title=__dev");
    run("Subtract...", "value=" + med);
    run("Abs");
    mad = getValue("Median");
    close("__dev");
    return med + k * 1.4826 * mad;
}

// --- OR (work >= T), restricted to the region, into the accumulator mask ---
function paintRegion(workTitle, maskTitle, T, roiIndex) {
    selectWindow(workTitle);
    run("Select None");
    run("Duplicate...", "title=__thr");              // full-size 32-bit copy
    setThreshold(T, 1e30);                           // fast native threshold -> binary
    run("Convert to Mask");                          // 255 where value >= T (BlackBackground)
    if (roiIndex >= 0) {
        roiManager("select", roiIndex);
        run("Clear Outside");                        // zero everything outside the region
        run("Select None");
    }
    imageCalculator("Max", maskTitle, "__thr");      // union into accumulator
    close("__thr");
}

function cleanup(workTitle, tempRoi) {
    if (isOpen("__dev")) { selectWindow("__dev"); close(); }
    if (isOpen("__thr")) { selectWindow("__thr"); close(); }
    if (isOpen(workTitle)) { selectWindow(workTitle); close(); }
    if (tempRoi >= 0) { roiManager("select", tempRoi); roiManager("delete"); }
}

// =====================================================================
// Companion note — why this is not quite the production method
// =====================================================================
// `bg-k-sigma` uses ONE background+sigma for the whole region, so wherever the
// background drifts within the region (the diffuse "dilute phase"), a single cut
// either lets the haze through or loses dim foci. PerCell4's chosen detector,
// `adaptive`, replaces the single regional background with a per-pixel LOCAL
// background (a Gaussian-weighted average over a ~15 px window) and keeps a
// pixel if it exceeds its OWN local level by k*sigma. In ImageJ that is roughly:
//
//     run("Duplicate...", "title=local");          // 32-bit copy of the smoothed image
//     run("Gaussian Blur...", "sigma=2.3");        // ~ window 15 px  (sigma = (15-1)/6)
//     imageCalculator("Subtract create 32-bit", "image", "local");  // image - local_bg
//     // then threshold the difference at k*sigma  (sigma = 1.4826*MAD of the region)
//
// Ask if you'd like that local-adaptive macro written out in full as well.
