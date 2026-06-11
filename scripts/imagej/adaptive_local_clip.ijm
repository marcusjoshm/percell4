// =====================================================================
// Local adaptive threshold (background + k*sigma, per pixel)
//   — ImageJ/Fiji macro
// =====================================================================
// A faithful port of PerCell4's PRODUCTION puncta detector, `adaptive`
// (window=15, k=2.25): the method that was selected for stress-granule
// detection. Where the `bg-k-sigma` macro uses ONE background+sigma for a whole
// region, this compares EVERY pixel to its OWN local background, so the cutoff
// floats across the image and the diffuse "dilute phase" is rejected while small
// dim foci are kept.
//
//   local_bg(p) = Gaussian-weighted average over a `window`-px neighborhood of p
//   keep p       if   smoothed(p)  >  local_bg(p)  +  k * sigma
//
// where sigma is the region's robust background noise (1.4826 * MAD), exactly as
// in PerCell4. (The local blur uses sigma = (window-1)/6, matching skimage's
// threshold_local block_size -> Gaussian-sigma convention, so window=15 -> ~2.33.)
//
// Equivalence to PerCell4: PerCell4 subtracts a global background first, but that
// constant cancels in the comparison wherever the window sits inside the cell, so
// it reduces to exactly the rule above — see docs/methods/headless-puncta-thresholding.md.
// PerCell4's interactive Adaptive Clip module exposes a "Noise (sigma) estimate"
// selector — MAD (robust) / stddev / gaussian-peak — defaulting to MAD, so the
// default reproduces this macro. (gaussian-peak fits the background histogram mode;
// on a whole frame a large black outside-cell region collapses that fit to a tiny
// sigma and massively over-detects — which is why MAD is the whole-frame default.)
//
// Region modes:
//   * Whole image (or current selection) — one sigma for the region; the LOCAL
//     background still floats per pixel.
//   * Per ROI (ROI Manager) — one independent sigma per ROI (PerCell4's per-group
//     / per-cell isolation), unioned into a single mask.
// =====================================================================

macro "Local adaptive clip" {

    if (nImages == 0) exit("Open an image first.");

    Dialog.create("Local adaptive (background + k*sigma) clip");
    Dialog.addNumber("k  (sigma multiplier)", 2.5);
    Dialog.addNumber("window (px, local background size)", 15);
    Dialog.addNumber("Gaussian pre-smooth sigma (0 = none)", 1.0);
    Dialog.addNumber("Minimum spot size (px, 0 = none)", 9);
    Dialog.addChoice("Noise (sigma) estimate",
        newArray("MAD (robust)", "stddev"), "MAD (robust)");
    Dialog.addChoice("Region mode",
        newArray("Whole image (or current selection)", "Per ROI (ROI Manager)"),
        "Whole image (or current selection)");
    Dialog.show();

    k       = Dialog.getNumber();
    window  = Dialog.getNumber();
    gsigma  = Dialog.getNumber();
    minSize = Dialog.getNumber();
    robust  = (Dialog.getChoice() == "MAD (robust)");
    perRoi  = (Dialog.getChoice() == "Per ROI (ROI Manager)");

    // odd window -> local-background Gaussian sigma, matching skimage threshold_local
    window = 2 * floor(window / 2) + 1;
    localSigma = (window - 1) / 6.0;

    setOption("BlackBackground", true);
    setBackgroundColor(0, 0, 0);   // so "Clear Outside" zeros (not whitens) outside the ROI
    orig = getTitle();
    noiseName = "stddev";
    if (robust) noiseName = "MAD";
    print("\n--- local adaptive clip on '" + orig + "'  (k=" + k +
          ", window=" + window + ", localSigma=" + d2s(localSigma, 3) +
          ", smooth=" + gsigma + ", minSize=" + minSize + ", " + noiseName + ") ---");

    // Capture a drawn selection (non-per-ROI mode) into the ROI Manager so the
    // whole pipeline runs through one clean per-region path.
    tempRoi = -1;
    if (!perRoi && selectionType() != -1) {
        roiManager("add");
        tempRoi = roiManager("count") - 1;
    }

    // Smoothed 32-bit working copy.
    selectWindow(orig);
    run("Select None");
    run("Duplicate...", "title=__work");
    work = getTitle();
    run("32-bit");
    if (gsigma > 0) run("Gaussian Blur...", "sigma=" + gsigma);

    // Local background = Gaussian blur of the smoothed image; diff = work - local.
    selectWindow(work);
    run("Duplicate...", "title=__local");
    run("Gaussian Blur...", "sigma=" + localSigma);
    imageCalculator("Subtract create 32-bit", "__work", "__local");
    rename("__diff");
    if (isOpen("__local")) { selectWindow("__local"); close(); }

    getDimensions(W, H, channels, slices, frames);
    newImage("__mask", "8-bit black", W, H, 1);   // 0/255 accumulator

    if (perRoi) {
        n = roiManager("count");
        if (n == 0) { cleanup(work, tempRoi); exit("Per-ROI mode needs ROIs in the ROI Manager."); }
        for (i = 0; i < n; i++) {
            sigma = noiseEstimate(work, robust, i);
            paintRegion("__diff", "__mask", k * sigma, i);
            print("ROI " + (i + 1) + "/" + n + ":  sigma = " + d2s(sigma, 3) +
                  "  ->  diff threshold = " + d2s(k * sigma, 3));
        }
    } else if (tempRoi >= 0) {
        sigma = noiseEstimate(work, robust, tempRoi);
        paintRegion("__diff", "__mask", k * sigma, tempRoi);
        print("sigma = " + d2s(sigma, 3) + "  ->  diff threshold = " + d2s(k * sigma, 3) +
              "  (within drawn selection)");
    } else {
        sigma = noiseEstimate(work, robust, -1);
        paintRegion("__diff", "__mask", k * sigma, -1);
        print("sigma = " + d2s(sigma, 3) + "  ->  diff threshold = " + d2s(k * sigma, 3) +
              "  (whole image)");
    }

    // Size filter -> clean binary result.
    selectWindow("__mask");
    run("Select None");
    if (minSize > 0) {
        run("Analyze Particles...", "size=" + minSize + "-Infinity pixel show=Masks clear");
        close("__mask");
        selectWindow("Mask of __mask");
    }
    rename(orig + "_adaptive_mask");
    result = getTitle();

    // Count + report.
    run("Set Measurements...", "area redirect=None decimal=2");
    run("Analyze Particles...", "size=0-Infinity pixel summarize");

    cleanup(work, tempRoi);
    selectWindow(result);
    print("Done -> " + result);
}

// --- robust (or stddev) background noise sigma for one region of `work` ---
// roiIndex >= 0 : that ROI Manager ROI;  -1 : whole image.
function noiseEstimate(workTitle, robust, roiIndex) {
    selectWindow(workTitle);
    if (roiIndex >= 0) roiManager("select", roiIndex);
    else run("Select None");

    if (!robust) {
        getStatistics(area, mean, mn, mx, std);   // honors the active selection
        return std;
    }

    med = getValue("Median");                      // honors the active selection
    // MAD = median(|x - med|) within the same region (duplicating a selection
    // crops to its bbox but keeps the ROI, so the median below stays in-region).
    run("Duplicate...", "title=__dev");
    run("Subtract...", "value=" + med);
    run("Abs");
    mad = getValue("Median");
    close("__dev");
    return 1.4826 * mad;
}

// --- OR (diff > T), restricted to the region, into the accumulator mask ---
function paintRegion(diffTitle, maskTitle, T, roiIndex) {
    selectWindow(diffTitle);
    run("Select None");
    run("Duplicate...", "title=__thr");          // full-size 32-bit copy of the diff
    setThreshold(T, 1e30);
    run("Convert to Mask");                       // 0/255; diff > T -> 255 (BlackBackground)
    if (roiIndex >= 0) {
        roiManager("select", roiIndex);
        run("Clear Outside");                     // zero everything outside the region
        run("Select None");
    }
    imageCalculator("Max", maskTitle, "__thr");   // union into accumulator
    close("__thr");
}

function cleanup(workTitle, tempRoi) {
    if (isOpen("__dev"))  { selectWindow("__dev");  close(); }
    if (isOpen("__thr"))  { selectWindow("__thr");  close(); }
    if (isOpen("__local")){ selectWindow("__local");close(); }
    if (isOpen("__diff")) { selectWindow("__diff"); close(); }
    if (isOpen(workTitle)){ selectWindow(workTitle);close(); }
    if (tempRoi >= 0) { roiManager("select", tempRoi); roiManager("delete"); }
}
