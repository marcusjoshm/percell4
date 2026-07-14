## Adaptive Local Clipping

### Overview

Segmenting biomolecular condensates — stress granules, P-bodies, and other membraneless organelles — from fluorescence images is a two-phase measurement problem disguised as a thresholding problem. Each punctum is a dense, condensed phase sitting on a diffuse cytoplasmic *dilute phase* of the same molecular species. The dilute phase is real signal biologically but must not be counted as a particle; the hard acceptance criterion in our validation was zero dilute-phase pickup. A single intensity cutoff fails because the intensity landscape is heterogeneous on two independent axes at once, and one number can be right for only one of them.

The first axis is **inter-cell (population) heterogeneity**: expression varies many-fold across a field — roughly $3\times$ within a single field and up to $\sim\!40\times$ across datasets in polyclonal lines — so a global cutoff calibrated to a bright cell erases a dim cell's puncta, while one calibrated to a dim cell over-detects and fragments the bright ones. The second axis is **intracellular heterogeneity**: within a single cell the dilute-phase background is spatially uneven, compounded by illumination and cytoplasmic gradients. This defeats even a *per-cell* global threshold such as Otsu, which places its cut where it best splits the histogram it is handed — a histogram dominated by whichever population is largest. Where bright foci dominate, the split lands high and dim foci are lost; where diffuse haze dominates, it lands low and haze is admitted. A hand-drawn small ROI historically rescued this because a small box supplies local scale, local background, and local class balance simultaneously.

Adaptive Local Clipping (ALC) reconstructs all three automatically and per cell. It is organized around **two adaptive scales, one spatial and one statistical**: a *local background subtraction* that acts spatially within a cell, and a *per-cell robust noise floor* that normalizes statistically across cells. These two scales are the core of the method, and each maps cleanly onto one of the two kinds of heterogeneity.

### How it works

Formally, ALC is a Difference-of-Gaussians (DoG) band-pass followed by a per-cell robust $z$-score, applied to the raw single channel $I$ against an integer Cellpose label image (one ID per cell), with $p$ the pixel size in µm/px.

**(1) Presmooth (fixed pixel scale).** Blur the image to define the working buffer,
$$\text{work} = G(\sigma_{\text{pre}}) * I,\qquad \sigma_{\text{pre}} = 1\ \text{px}.$$
This scale is fixed in pixels, not microns, because shot/pixel noise does not scale with magnification.

**(2) Local background subtraction (the "clipping").** Blur $\text{work}$ again at $\sigma_{\text{bg}} = (\text{window}-1)/6$ (the `threshold_local` Gaussian convention) and subtract, $\text{diff} = \text{work} - G(\sigma_{\text{bg}}) * \text{work}$. Because convolving Gaussians adds variances, the background is the image at $\sigma_{\text{eff}} = \sqrt{\sigma_{\text{pre}}^2 + \sigma_{\text{bg}}^2}$, so
$$\text{diff} = \big[\,G(\sigma_{\text{pre}}) - G(\sigma_{\text{eff}})\,\big] * I,$$
a DoG band-pass that keeps structure between $\sigma_{\text{pre}}$ (smallest resolvable) and $\sigma_{\text{bg}}$ (largest preserved). This is computed **once over the whole frame**, never on per-cell crops — cropping would distort the background inside the window at each bounding box, precisely at cell-edge granules.

**(3) Per-cell robust noise floor.** For each cell, estimate the noise on the same $\text{work}$ buffer the detector thresholds:
$$\sigma_{\text{cell}} = 1.4826\cdot \operatorname{MAD}\!\big(\text{work} \mid \text{cell mask}\big),$$
the standard outlier-resistant estimator of a Gaussian $\sigma$. Using MAD rather than the plain standard deviation keeps the bright foci from inflating the very noise estimate they are tested against. Cells with zero or non-finite MAD are skipped.

**(4) Per-cell $z$-score threshold (the "adaptive clip").** A pixel is foreground iff, inside its own cell,
$$\text{diff} > k\,\sigma_{\text{cell}} \iff I > \text{local background} + k\,\sigma_{\text{cell}},\qquad k=1.$$

**(5) Post-processing.** An optional per-pass hole-fill closes a large particle that under-windowed into a ring, then a size filter drops 4-connected components below $\text{min\_spot\_px}$. The output is a whole-frame $\{0,1\}$ mask, intersected with each cell so puncta stay attributed per cell.

**The single physical knob.** Everything above is fixed except one parameter with physical meaning — $d_{\min}$, the smallest target diameter in microns — which sets both length scales:
$$\text{window} = \operatorname{odd}\!\big(\text{round}(6\,d_{\min}/p)\big)\ \ (\text{floor }3),\qquad \text{min\_spot\_px}=\text{round}\!\left(\frac{\pi (d_{\min}/2)^2}{p^2}\right).$$
$\text{min\_spot\_px}$ falls to $1$ — the size filter switches off — once $d_{\min}$ reaches the pixel/diffraction scale, so diffraction-limited P-bodies are never deleted by a filter that cannot tell a sub-pixel spot from a noise pixel. At $p = 0.120369$ µm/px this gives stress granules $d_{\min}\approx0.40$ µm → window $21$ px, filter on; P-bodies $d_{\min}\approx0.14$ µm → window $7$ px, filter off. Specifying the rule in microns and converting per image is what makes it transfer across magnifications.

**Why $\text{window}=6\,d_{\min}$.** For an idealized Gaussian feature of width $\sigma_f$ and peak contrast $A$, the fraction of the peak surviving the band-pass at the feature center is
$$F = \frac{\sigma_{\text{bg}}^2}{\sigma_f^2 + \sigma_{\text{pre}}^2 + \sigma_{\text{bg}}^2},$$
and a center pixel detects solid when $F\cdot\text{CNR} > k$, with $\text{CNR}=A/\sigma_{\text{cell}}$. Large windows ($\sigma_{\text{bg}}\gg\sigma_f$) give $F\to1$ (solid fill); small windows give $F\to0$, where the local mean sits inside the feature and the center subtracts itself away, leaving a diagnostic ring. Setting $\sigma_{\text{bg}}\approx d_{\min}$ (about twice the feature half-width) places resolved granules at $F\approx0.87$–$0.92$; diffraction-limited P-bodies drop to $F\approx0.5$ only because $\sigma_{\text{pre}}$ is comparable to the feature, which is exactly why their size filter turns off and $k$ stays permissive. This makes window and $k$ **orthogonal**: window is a length that sets which object *sizes* survive the band-pass and controls hollow-vs-solid; $k$ is a $z$-score that sets how many noise-sigmas of *contrast* a pixel must clear.

### Robustness across heterogeneous cell populations

Inter-cell brightness variation is absorbed entirely by steps 3–4. Because the test is $\text{diff}>k\,\sigma_{\text{cell}}$ with $\sigma$ measured *inside each cell*, $k$ is a dimensionless $z$-score on that cell's own noise. One value of $k$ therefore imposes one statistical stringency on a dim cell and one up to $40\times$ brighter across the population alike: no per-cell hand-tuning, no dim cells lost to a cutoff calibrated on bright ones, no bright cells shattered by a cutoff calibrated on dim ones. This is the property that lets a single number transfer across a variable-expression population and across datasets. $k=1$ sits at the permissive end — typical Gaussian-band-pass detection uses $k\approx2$–$2.5$, with $k\gtrsim3$ strict — for three reasons: the band-pass attenuates a sliver of low-frequency noise, so the realized false-positive rate is stricter than a nominal $k=1$; diffraction-limited features retain only $\sim$half their contrast and need a low bar to register at all; and the per-cell $\sigma$ already normalizes brightness, so $k$ carries pure stringency. Raise $k$ toward $2$–$3$ to reject nuisance structure that overlaps the targets in size. The one caveat is that $1.4826\cdot\text{MAD}\approx$ noise only in a noise-dominated cell; strong fine texture inflates MAD and makes the same $k$ effectively stricter there.

### Robustness to intracellular heterogeneity

Spatially non-uniform background *within* a cell is handled entirely by step 2, on a scale separate from $\sigma_{\text{cell}}$. Every pixel is judged against its own $\sim$window-sized neighborhood, so a broad patch of dilute phase raised evenly over a wide area never beats its own surroundings and is rejected, while a sharp local focus stands out and survives. The DoG band-pass removes the slowly varying illumination and cytoplasmic gradient by construction, so a non-uniform background manufactures neither false positives in hazy regions nor masked dim foci in bright ones. Because the background is a genuine spatial estimate rather than a single per-cell scalar, ALC does not inherit the whole-cell-Otsu failure mode in which a threshold set by the dominant population misses dim foci in one region and admits haze in another — the mechanism a per-cell *global* threshold structurally lacks.

The decisive point is that these are **two orthogonal mechanisms for two orthogonal problems** — a spatial, within-cell local subtraction ($\sigma_{\text{bg}}$, a length scale controlling which sizes survive and hollow-vs-solid fill) and a statistical, across-cell robust normalization ($\sigma_{\text{cell}}$, the per-cell noise scale on which the $z$-score threshold $k$ sets how much junk is admitted) — fused into one operator. That separation is precisely why a global threshold, or even a per-cell-global one, cannot match it.

### Validation

ALC was eye-validated across four datasets and two condensate types, with expert visual judgment as ground truth. In a separate whole-frame detector bake-off on an arsenite + nocodazole stress-granule field with $\sim\!4{,}664$ hand-labeled foci, the adaptive detector recovered $\sim\!19\%$ more true foci than a manual mask while maintaining zero dilute-phase pickup. (That bake-off variant ran window $=15$, $k=2.25$, distinct from the per-cell workflow defaults of $k=1$ and window $=6\,d_{\min}$ described here; the two default sets should not be conflated.) The same per-cell noise floor $\sigma_{\text{cell}}$ additionally defines the downstream contrast-to-noise ratio, $\text{CNR}=(\text{focus interior}-\text{local background})/\sigma_{\text{cell}}$, used to classify puncta into subpopulations — so detection and interpretation share one noise definition.
