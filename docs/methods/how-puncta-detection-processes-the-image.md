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
The **second pass** then looks again — this time comparing **every pixel to its own
local surroundings** and keeping the ones that stand out as a bright bump — and
catches the dim foci the first pass missed. The per-group results are combined into
one mask.

The key idea: instead of picking a single brightness cutoff for a whole cell (which
always either lets in dilute haze or misses dim foci), PerCell4 lets the cutoff
**float locally**. A focus is a sharp local bump and stands out; the dilute phase is
raised evenly over a wide area, so it never beats its own surroundings and is left
out — and because the test is pixel-by-pixel, each focus keeps its true, irregular
shape.

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

- This is **not the final mask.** Its only job is to mark roughly *where the obvious
  signal is*.
- It uses a quick, permissive **Otsu auto-threshold** to do that. (Your original
  sketch had this first pass using Otsu — that is exactly what the validated recipe
  does.) Getting it perfect doesn't matter: the background step below reads the
  background level straight from the brightness histogram, so it doesn't depend on
  this scout being precise.

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
**not** use a single brightness cutoff for the whole cell (that was the old approach
that kept failing). Instead it uses a **local adaptive threshold**:

- For **each pixel**, it looks at a small window of the immediate surroundings
  (about 15 pixels across) and works out the **local** background right there.
- It keeps the pixel only if it is brighter than that local background by a set
  **margin** — a few times the noise level.
- That margin is the one **sensitivity knob** (called *k*). Lower finds more, dimmer
  foci; higher is stricter. The winning setting was the most generous one that still
  picked up zero dilute phase.

Because the comparison is always against the **local** surroundings, a patch of
dilute phase — raised evenly over a wide area — never beats its own neighborhood and
is left out, while a small focus — a sharp local bump — stands out and is kept. And
because it is a true pixel-by-pixel test, each focus keeps its **real, irregular
shape** (it is never rounded into a disk), which the later per-particle measurements
depend on.

A final size filter removes anything too small to be a real focus.

> Why this replaces a plain Otsu cutoff: a single brightness cut has to pick one
> number for the whole cell, so it must either include the diffuse haze (too low) or
> miss the dim foci (too high). A *local* cutoff doesn't face that dilemma — it
> recognizes a dim focus by *how it sits above its immediate surroundings*, not by an
> absolute brightness. This is the automation of the manual trick of circling a small
> region to threshold it against its own neighborhood. That is the whole reason the
> new method beats the old one.

## 5. Combine into the final mask

The foci found in every group are merged into one mask for the image — the final
result, produced with no manual QC step.

---

## What was tested, and how the winner was chosen

To have something to check against, every focus in a real image
(`Dish 2 TAOK2 KO 60min As + Noco`, mNG channel) was marked by eye — **4,664 foci**,
including the faint ones. This answer key confirmed the detector finds foci in the
right places, and showed that the old hand-QC'd mask only recovered about **67%** of
them (it was missing the dim ones).

But a count of correctly-placed dots can't tell you whether each granule's **shape**
is right, or whether any **dilute phase** snuck in — and those are the two things
that actually matter. (One family of detectors scored well on placement but drew
every granule as a uniform circle — useless for measuring real, irregular granules,
so it was rejected.) So the winner was chosen the same way the manual QC was done: by
**laying the candidate masks over the real image and comparing them by eye**, walking
the sensitivity knob from generous toward strict and stopping at the **first setting
with no dilute phase at all** that still kept the small dim foci.

The winning setting — a **15-pixel local window** with a **margin of k = 2.25** —
found **4,247 granules, about 19% more than the old manual mask's 3,570**, at the
same typical granule size, with **no dilute phase** and no manual step:

| | Granules found | Typical size | Dilute phase | Manual QC |
|---|---:|---:|:--:|:--:|
| Old hand-QC mask | 3,570 | 17 px | none | required |
| New automated method | **4,247** | 18 px | **none** | **none** |

Those extra granules are exactly the small, dim ones the old approach was missing —
the ones no existing stress-granule method reliably picks up.

This means: for this experimental condition, the laborious manual QC step can be
retired, and the same recipe can be applied automatically to comparable images.

---

*Companion technical reference (methods raced, exact settings, code locations):*
`docs/methods/headless-puncta-thresholding.md`.
