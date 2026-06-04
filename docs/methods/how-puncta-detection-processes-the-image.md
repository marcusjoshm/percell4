---
title: How PerCell4 Detects Puncta — A Plain-Language Walkthrough
date: 2026-06-04
status: current
audience: non-technical
companion: docs/methods/headless-puncta-thresholding.md
---

# How PerCell4 Detects Puncta — A Plain-Language Walkthrough

This describes *what happens to the image* as PerCell4 finds stress-granule foci,
without any of the programming details. If you want the code-level reference, see
the companion document `docs/methods/headless-puncta-thresholding.md`.

## The short version

PerCell4 finds puncta in two passes. The **first pass** is a quick, generous scan
that locates the obvious foci so the program can learn what each group of cells
looks like. It then **estimates and removes the background** brightness from each
group of cells, so dim foci are no longer buried under the cell's general glow.
The **second pass** then looks again — this time for focus-*shaped* bright spots of
the expected size — and catches the dim foci the first pass missed. The per-group
results are combined into one mask.

The key idea: instead of picking a single brightness cutoff (which always either
lets in haze or misses dim foci), PerCell4 flattens each group's background and
then looks for the *shape and size* of a focus.

---

## 1. Group the cells by brightness

PerCell4 first sorts the cells into a few **groups of similar brightness**, so
each group can be processed with settings appropriate to its own intensity. Cells
that glow brightly are treated separately from faint cells, which keeps a bright
cell from setting the rules for a faint one.

> *How the grouping is done is adjustable.* It can cluster cells by fitting a
> mixture of Gaussian distributions and letting a statistical criterion (BIC)
> choose how many groups to use, **or** by simple k-means clustering into a chosen
> number of groups; and it can rank cells by their **mean** or **median**
> brightness. The validation run used **k-means on the mean mNG brightness** of
> each cell. (Your sketch described GMM/BIC on median brightness — a valid
> alternative, just not the configuration that was validated.)

## 2. First pass — find the obvious foci (a scouting pass)

Within each group, PerCell4 does a quick, **deliberately generous** scan that
picks up the brighter, easy-to-see foci. Two things matter here:

- This is **not the final mask.** Its only job is to *locate where signal is* so
  the program can measure the background in the spaces between foci.
- It looks for **small bright blobs**, not "everything above a brightness line."
  (Your sketch had this pass producing an initial mask with Otsu autothresholding;
  in the method that was validated, this scouting pass uses the same spot-finding
  idea as the final pass, just tuned to be permissive — Otsu is not used.)

## 3. Estimate and remove each group's background

Now PerCell4 figures out how bright the "empty" parts of each group are — the
diffuse cell glow, the dilute phase, the uneven illumination — and **subtracts that
background away**, so that what remains is mostly the foci standing above zero.
This is the step that lets the second pass see dim foci: once the background floor
is removed, a faint focus that was only slightly brighter than its surroundings now
stands out clearly.

The background is estimated **per group**, and the same group-wide value (or
surface) is subtracted from every cell in that group.

### 3a. The background-estimation methods (and the math behind each)

Seven methods are available (six built, one a placeholder). Think of each as a
different way of answering "what is the background brightness here?" Let the
group's pixel brightness values be `x`.

1. **Gaussian background-peak** *(the method that won the validation).*
   Make a histogram of the group's pixel brightnesses. Most pixels are background,
   so there is a tall peak at low brightness; the foci form a small tail off to the
   bright side. Fit a bell curve to that low-brightness peak —
   `height · exp(−(x − μ)² / (2σ²))` — and read off its center **μ** (the typical
   background level) and width **σ** (the noise spread). **Subtract μ** from every
   pixel. Because the fit only uses the background peak, the bright foci tail
   doesn't drag the estimate upward — this is what makes it robust.

2. **Donut — median.** Take each focus found in the first pass, skip a small
   **buffer** of pixels around it (so you don't measure its own halo), then collect
   a **ring (the "donut")** of pixels just outside that buffer. Pool the ring pixels
   from all foci in the group and take their **median**: `background = median(ring
   pixels)`. Subtract that value. (Typical geometry: a buffer of ~4–5 pixels then a
   ring ~4–5 pixels wide. Your sketch's "buffer then donut" describes exactly this
   family — it just wasn't the winning method.)

3. **Donut — mean.** Same ring geometry, but use the **average** instead of the
   median: `background = (sum of ring pixels) / (number of ring pixels)`. The mean
   is simpler but more sensitive to a few stray bright pixels in the ring than the
   median.

4. **Percentile.** Sort the group's pixel brightnesses and take the value at a
   chosen percentile (default the 50th, i.e. the median) as the background:
   `background = p-th percentile of x`. A blunt but fast estimate of "the typical
   pixel."

5. **MAD (median ± robust noise).** Use the **median** of the group's pixels as the
   background, and estimate the noise from the **median absolute deviation**:
   `σ = 1.4826 × median(|x − median(x)|)`. (The 1.4826 factor rescales the MAD so it
   matches the standard deviation of a normal distribution.) The median resists
   bright outliers, so this is a robust version of "subtract the average, and here's
   how noisy it is."

6. **Rolling ball (a background *surface*, not one number).** Picture the image as a
   landscape where brightness is height. Roll a ball of a chosen radius underneath
   that landscape; the surface the top of the ball traces out is the slowly-varying
   background — it follows gentle illumination gradients but cannot climb into the
   sharp peaks of the foci. **Subtract that surface pixel by pixel.** Best when the
   background drifts smoothly across the field rather than being one flat level.

7. **Donut surface** *(placeholder — not yet implemented).* Would take the donut
   ring samples from around the foci and fit a smooth surface (a thin-plate spline)
   through them, interpolating a per-pixel background everywhere in the group —
   essentially a foci-aware version of the rolling ball. Deferred until there's
   evidence the simpler methods aren't enough.

> Only the **Gaussian background-peak** method was actually run in the validation;
> the others are built and available to try. (So "8 methods were tested" should
> read: *7 methods are available, 1 was validated.*)

### 3b. Subtract the background from each group

Whichever method is chosen, its result is subtracted from that group's image —
every cell in the group gets the same background treatment. After this step, the
diffuse glow is gone and the foci sit on a near-zero floor.

## 4. Second pass — find *every* focus

PerCell4 now scans the background-subtracted image again, this time to capture
**all** the foci, including the dim ones the first pass skipped. Crucially, it does
**not** use a single brightness cutoff (that was the old approach that kept failing).
Instead it uses a **multiscale spot detector**:

- It looks for the **shape of a focus** — a small, round, locally-bright bump —
  rather than "any pixel above a brightness line."
- It looks at **several focus sizes at once** ("multiscale"), so it works even
  though the foci in this condition vary in size.
- It has one **sensitivity knob**. Turning it down finds more (dimmer) foci at the
  cost of occasionally picking up noise; turning it up is stricter. The validation
  tuned this knob to the most-sensitive setting that still kept false positives low.

A final size filter removes anything too small to be a real focus.

> Why this replaces Otsu: a single brightness cutoff has to pick one number for the
> whole group, so it is forced to either include the diffuse haze (too low) or miss
> the dim foci (too high). A shape-and-size detector on a flattened background does
> not face that dilemma — it recognizes a dim focus by *how it sits above its local
> surroundings*, not by an absolute brightness. That is the whole reason the new
> method beats the old one.

## 5. Combine into the final mask

The foci found in every group are merged into one mask for the image — the final
result, produced with no manual QC step.

---

## What was tested, and what won

To prove the method is trustworthy, every focus in a real image
(`Dish 2 TAOK2 KO 60min As + Noco`, mNG channel) was marked by eye — **4,664 foci**,
including the faint ones — to serve as the answer key. The program's results were
then scored against that answer key. For reference, the previous hand-QC'd mask
recovered **67%** of those 4,664 foci.

The detectors and sensitivity settings were raced against each other. The
**Laplacian-of-Gaussian spot detector** with the **Gaussian background-peak**
subtraction, at the most-sensitive setting that kept precision at or above 90%,
came out on top — and it stayed stable when its settings were nudged:

| | Foci correctly found (recall) | Of what it found, how much is real (precision) |
|---|---|---|
| Old hand-QC mask | **67%** | — |
| New automated method | **82%** | **91%** |

So the automated, hands-off method finds **about 82% of every focus that was
labeled by eye — roughly 15 percentage points more than the old manual mask — while
keeping 9 out of 10 of its detections real.** Those extra foci are exactly the dim
ones the old approach was missing.

This means: for this experimental condition, the laborious manual QC step can be
retired, and the same recipe can be applied automatically to comparable images.

---

*Companion technical reference (methods raced, exact settings, code locations):*
`docs/methods/headless-puncta-thresholding.md`.
