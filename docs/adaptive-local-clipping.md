# Adaptive Local Clipping (ALC)

Adaptive Local Clipping is PerCell4's universal puncta detection method. It segments punctate structures inside individual cells without requiring a hand-tuned intensity threshold. It is available in the GUI thresholding workflow and headlessly via `percell4-batch-threshold --strategy adaptive-clip` (single-window) or `--strategy auto-extract` (two-pass auto-extraction).

## Motivation

Two properties of real single-cell imaging data defeat conventional global thresholding:

1. **Cell-to-cell expression variability.** In polyclonal populations (e.g., after lentiviral transduction), expression levels differ widely between cells. A single intensity threshold across the whole image cannot accommodate this variability.
2. **Within-cell brightness range.** Puncta in a single cell can span orders of magnitude in brightness. Canonical structures are bright, while small structures near or below the diffraction limit and out-of-focus structures are far dimmer. One foreground–background threshold within a cell cannot capture both.

ALC addresses both problems by making every decision local: noise is estimated per cell, background is estimated per pixel, and detection is performed at multiple spatial scales.

## Method

### Pre-processing

The input image is smoothed with a Gaussian blur (radius 1 px by default). Denote the smoothed intensity at pixel (x, y) by I(x, y).

### Per-cell noise estimation

Using the cell segmentation, a robust noise estimate σ_c is computed for each cell c as the median absolute deviation (MAD) of the pixel intensities within that cell, scaled to be comparable to a standard deviation under a normal distribution:

    σ_c = 1.4826 · median_{(x,y) ∈ c} | I(x, y) − median_{(x,y) ∈ c} I(x, y) |

The MAD is used rather than the standard deviation because it is insensitive to the bright puncta themselves, which would otherwise inflate the noise estimate.

### Local background estimation

The local background B(x, y) at every pixel is estimated as the Gaussian-weighted average of intensities in a neighborhood around that pixel — the *adaptive window* — computed by convolving the image with a Gaussian kernel:

    B(x, y) = G_{σ_bg}[I](x, y)

The kernel scale is tied to the adaptive window width w (in pixels):

    σ_bg = (w − 1) / 6

so that the window spans approximately ±3 standard deviations of the kernel.

### Clipping criterion

Subtracting the local background yields a residual image:

    r(x, y) = I(x, y) − B(x, y)

A pixel is assigned to the binary mask when its residual exceeds the cell's noise by a stringency coefficient k (expressed in units of noise widths):

    r(x, y) > k · σ_c

Because both the background and the noise reference are local, the same criterion applies meaningfully in a dim cell and a bright one.

### Multi-band-pass detection

The adaptive window size determines which particle sizes can be resolved:

- **Window too small:** the local average around a large particle is dominated by the particle itself, so the background estimate approaches the particle's own intensity — leaving holes in the mask of larger particles.
- **Window too large:** small particles contribute negligibly to their surrounding average and fall below the detection criterion.

Since no single window can cover the full particle size range, ALC runs (at least) two band-pass filters and combines their binary masks with a union:

- The **fine filter** targets the smallest reliably detectable particles. The minimum particle diameter is taken as 2 px, and the pre-processing blur guarantees that a genuine point source spans at least this extent.
- The **coarse filter** targets the largest particles. Their diameter is determined automatically by Laplacian-of-Gaussian (LoG) blob detection (scikit-image), which identifies blobs as maxima in LoG scale-space; the scale of the peak response corresponds to particle size.

In both cases, the window is set to **three times the target particle diameter**, ensuring the Gaussian-weighted neighborhood average is not dominated by the particle signal itself.

### Automatic stringency calibration

For the fine filter, k = 1. For the coarse filter, k is raised automatically and separately for each cell, using the symmetry of noise about the local background: pixels below the *negative* threshold −k·σ_c contain only background, so their count estimates the number of false positives above the positive threshold. k is raised until

    #{(x, y) : z(x, y) < −k}  ≤  α · #{(x, y) : z(x, y) > +k},    α = 0.1

where z(x, y) = r(x, y) / σ_c is the standardized residual and #{·} counts pixels satisfying the condition. In other words, k is the smallest value at which the estimated false-positive count falls to 10% of the detected particle area.

### Output

The fine and coarse masks are combined into a single total particle mask, written to `/masks/<round>` in the dataset. On time-lapse data, detection runs independently per frame.

## Subpopulation classification by contrast-to-noise ratio

The total mask may contain a mix of populations, such as canonical bright puncta alongside dimmer out-of-focus or sub-diffraction structures. ALC can optionally separate these using a per-particle contrast-to-noise ratio:

    CNR = (I_peak − B_local) / σ_c

where I_peak is the 90th-percentile intensity within the particle interior, B_local is the local background around the particle, and σ_c is the cell's noise estimate. Particles are split either at a user-supplied CNR threshold (guided mode), or automatically by fitting a two-component Gaussian mixture model to the log-scaled CNR histogram across the dataset (discover mode), yielding high- and low-CNR masks (`<round>_high` / `<round>_low`) plus a per-particle CNR table at `/classification/<round>`.

## Parameters

| Parameter | Meaning | Default |
| --- | --- | --- |
| Pre-smooth sigma | Gaussian blur applied before detection | 1 px |
| Smallest particle diameter (`--d-min-um`) | Sets the fine window and size filter | required (adaptive-clip) / auto (auto-extract) |
| Largest particle diameter | Sets the coarse window | auto (LoG blob detection) |
| Window-to-particle ratio | Adaptive window width per target diameter | 3× |
| Stringency k (`--k`) | Detection threshold in noise widths | 1 (fine); auto-calibrated (coarse) |
| False-positive fraction α | Target for automatic k calibration | 0.1 |
| CNR threshold (`--cnr-threshold`) | Guided split point for subpopulations | user-supplied |

---

See also the [command-line reference](cli.md) for the flags that drive this method,
and [Methods](methods/) for the validation record. Back to the [README](../README.md).
