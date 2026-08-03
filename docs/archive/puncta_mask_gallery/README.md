# Stress-Granule Detection — Method Gallery

**What this is:** a side-by-side comparison of every automated method we tried for detecting
**stress granules** (small fluorescent foci that cells form under stress) in microscopy images,
so we can show why the standard approach falls short and which method we settled on.

**Sample:** `Dish 2 TAOK2 KO 60min As + Noco` — TAOK2-knockout cells stressed with **arsenite +
nocodazole for 60 min**. Granules are imaged on the **mNG** (mNeonGreen) fluorescent reporter.
Cell outlines come from **Cellpose** (`cp_mask`), which lets each granule be assigned to a cell.
**Why the small, dim granules matter:** under this condition the granules are unusually small and
numerous, and they are exactly the population existing methods systematically under-count — so
missing them biases granule-number and size measurements. Recovering them is the whole point.

Every binary mask below was generated headlessly (no manual step), from the **same** cell grouping,
so they are directly comparable.

## How to read this folder

- `images/*.png` — one **binary mask per method** (white = detected granule pixels). Open these and
  overlay them on `images/reference_mNG_grayscale.png` (the raw image) — the shape and "no diffuse
  haze" claims below are **visual judgments**; the numbers only approximate them.
- `images/manual_SG_mask.png` — the existing **hand-drawn mask** (the current practical comparator).
- `stats.json` — method, parameters, and counts for every mask.

**Mini-glossary** (each mask's filename, e.g. `adaptive_w15_k225`, encodes its method + settings):
- **Focus / granule** — one detected blob. **"Foci" here = connected components** (groups of touching
  white pixels). This is a *count of detected blobs*, **not** a verified count of true granules (see
  "Ground truth" below).
- **Dilute phase** — diffuse haze that is real signal but *not* a granule. Counting it as granules
  inflates the numbers, so picking it up is a failure.
- **Otsu** — the standard automatic thresholder: it picks one brightness cutoff per region.
- **Mean / median px** — average / middle blob area; a size proxy (the hand mask sits at median 8 /
  mean 17 px). A method whose sizes are much larger is drawing granules too big.
- **window / k** — the two dials of the method we chose: *window* = how big a neighborhood each pixel
  is compared against; *k* = how far above the local background (in noise units) a pixel must rise.

## Ground truth (so the counts are read correctly)

On this field, **4,664 foci were exhaustively marked by eye** — the closest thing to truth we have.
Measured against those 4,664 points, the **hand-drawn mask recovers ~67%** (it misses many dim foci).
**Important:** a blob count is *not* the same as that recall percentage — one blob can cover several
labeled foci or none, so the component counts in the tables below should be read as "how many distinct
blobs each method drew," not "what fraction of the 4,664 it found." We have **not** scored the chosen
method's exact recall against the 4,664 points (selection was by eye — see Conclusion); that
quantification is the next step.

---

## The headline result

The standard tool — **per-group Otsu thresholding** — plateaus well below the hand mask and never
reaches the small, dim granules, and none of the usual fixes (background subtraction, exclusion
floors, ROI re-thresholding) change that. A **local adaptive threshold** is the only method we tried
that draws *more* blobs than the hand mask while keeping granules small and true-shaped, because it
compares each pixel to its *local* surroundings instead of using one cutoff per cell.

| Method family | Blobs drawn | Median / mean px | Read as |
|---|---:|---:|---|
| Hand-drawn mask (comparator) | 3,570 | 8 / 17 | misses dim foci (~67% of the 4,664) |
| Plain Otsu (any background) | 2,223 | 20 / 30 | even fewer blobs, larger — plateaus ~2,200 |
| Floored Otsu (exclusion) | 1,417–1,977 | ~14 / ~27 | **fewer** still — more conservative |
| Otsu in an expanded ROI | 1,959–2,642 | ~13 / 29–40 | still on the plateau |
| **Adaptive `w15 k2.25` (chosen)** | **4,247** | **13 / 18** | draws most blobs; small, true-shaped; no haze by eye |
| LoG / DoG blobs | 3,839–4,184 | 25 / 27–35 | too big **and** drawn as round disks |
| Global k·σ / white top-hat | 5,167–19,375 | — / 39–49 | counts too high — sweeps in haze/noise |

---

## Section 1 — Background subtraction does **not** affect Otsu

Otsu picks the brightness cut that best separates two pixel groups. Subtracting a **constant**
background just slides the brightness histogram sideways, so the cut slides with it and **the mask is
unchanged**.

**Verified:** the five constant-background methods produced **pixel-for-pixel identical masks**:

| Mask file | Background | px | Blobs | Identical? |
|---|---|---:|---:|:--:|
| `otsu_bg_gaussian_peak` | Gaussian peak (mode) | 66,741 | 2,223 | reference |
| `otsu_bg_mad` | median | 66,741 | 2,223 | ✅ identical |
| `otsu_bg_percentile` | 50th percentile | 66,741 | 2,223 | ✅ identical |
| `otsu_bg_donut_median` | ring median around foci | 66,741 | 2,223 | ✅ identical |
| `otsu_bg_donut_mean` | ring mean around foci | 66,741 | 2,223 | ✅ identical |
| `otsu_bg_rolling_ball` | rolling-ball **surface** | 63,016 | 2,172 | ✗ differs |

The five constant-background masks differ from each other by **0 pixels**. The only one that changes
the result is `rolling-ball`, because it subtracts a *spatially-varying surface* rather than a single
number (it differs from the others in **5,439 pixels** — counting every pixel that is white in one but
not the other) — the exception that proves the rule. (A "no background" baseline is computed a little
differently for technical reasons unrelated to the background, so its blob count is slightly off at
2,423; among the *background-subtraction methods themselves*, the masks are identical.)

**Takeaway:** choosing a background-subtraction method is pointless when the detector is Otsu.

## Section 2 — An exclusion floor makes Otsu **more conservative**, not better

"Floored Otsu" first discards every pixel at or below `background + k·noise`, then runs Otsu on what
remains — the hope being that removing the background bulk lets the cut drop onto the dim foci. **It
does the opposite:** with the background gone, Otsu now splits between the *upper edge of the
background* and the foci, so the cut rises and **fewer** blobs survive.

**Verified — every floored variant drew fewer blobs than plain Otsu's 2,223:**

| Mask file | Background | Blobs | vs plain Otsu |
|---|---|---:|:--:|
| `floored_otsu_bg_rolling_ball` | rolling-ball | 1,977 | fewer |
| `floored_otsu_bg_gaussian_peak` | Gaussian peak | 1,737 | fewer |
| `floored_otsu_bg_mad` / `_percentile` | median | 1,553 | fewer |
| `floored_otsu_bg_donut_median` | ring median | 1,487 | fewer |
| `floored_otsu_bg_donut_mean` | ring mean | 1,417 | fewer |

And unlike plain Otsu, floored Otsu **now depends on the background method** (1,417–1,977), because the
exclusion floor's position rides on the background estimate. The floor is a monotonic "more
conservative" dial:

| Mask file | floor (`k`·noise) | Blobs |
|---|---:|---:|
| `floored_otsu_floor_0p5` | 0.5 | 1,851 |
| `floored_otsu_bg_gaussian_peak` | 1.0 | 1,737 |
| `floored_otsu_floor_1p5` | 1.5 | 1,577 |
| `floored_otsu_floor_2p0` | 2.0 | 1,460 |

**Takeaway:** the exclusion floor tightens Otsu (and reintroduces background-dependence), but tightening
is the wrong direction — we need *more* dim foci, not fewer.

## Section 3 — Re-running Otsu inside an expanded region

The manual trick was to draw a region around the foci and re-threshold inside it. Automated three ways
(re-Otsu inside each particle / cell / group, grown by 10 px), it stays pinned near the Otsu plateau
(~2,000–2,600 blobs); the per-particle version also **bloats** granule size (mean 40 px).

| Mask file | Region | Blobs | Median / mean px |
|---|---|---:|---:|
| `refine_otsu_particle_e10` | per particle | 2,642 | 17 / 40.2 |
| `refine_otsu_cell_e10` | per cell | 2,100 | 13 / 29.0 |
| `refine_otsu_group_e10` | per group | 1,959 | 13 / 28.9 |

**Takeaway:** any Otsu flavor gravitates to the brightest-vs-bulk split; the dim foci are a minority no
single cutoff carves out.

## Section 4 — Adaptive local threshold (the method we chose)

**The idea:** keep a pixel if it is a sharp bright bump relative to its *immediate neighbors* — exactly
what you do by eye when you circle a spot and compare it to the ring around it. Concretely: each pixel
is compared to a local background (a Gaussian-weighted average over a `window`-sized patch, ~15 px) and
kept only if it rises above that local level by `k` noise units. Because the cutoff floats locally,
diffuse haze (locally flat) never passes while compact foci do — at their true pixel shapes.

| Mask file | window | k | Blobs | Median / mean px | Note |
|---|---:|---:|---:|---:|---|
| `adaptive_w9_k20` | 9 | 2.0 | 2,890 | 10 / 13.1 | smallest window |
| `adaptive_w11_k20` | 11 | 2.0 | 3,651 | 12 / 15.2 | |
| `adaptive_w15_k20` | 15 | 2.0 | 4,698 | 14 / 17.9 | most pickup (some haze by eye) |
| **`adaptive_w15_k225_WINNER`** | **15** | **2.25** | **4,247** | **13 / 17.8** | **chosen — no haze by eye** |
| `adaptive_w15_k25` | 15 | 2.5 | 3,921 | 13 / 17.4 | |
| `adaptive_w15_k275` | 15 | 2.75 | 3,622 | 13 / 17.2 | |
| `adaptive_w25_k20` | 25 | 2.0 | 5,704 | — / 23.3 | window too large |
| `adaptive_w41_k20` | 41 | 2.0 | 6,015 | — / 29.0 | window too large |

The chosen point (`window=15, k=2.25`) draws **4,247 blobs vs the hand mask's 3,570** — ~19% more
distinct detections (a blob count, **not** a measured recall gain) — and by eye these extra blobs are
the small, dim granules the hand mask misses. Sizes stay granule-scale: the **mean** matches the hand
mask (17.8 vs 17.3 px), though the **median is larger** (13 vs 8 px), i.e. the very smallest
single-pixel specks of the hand mask are not reproduced — consistent with these being real small foci
rather than noise. The window/k sweep shows the two dials: a bigger window or lower `k` picks up more;
the chosen point is the most permissive setting that stayed haze-free by eye.

## Section 5 — Other detectors tried

| Mask file | Method | Blobs | Median / mean px | Why not |
|---|---|---:|---:|---|
| `log_blob_tr05` | Laplacian-of-Gaussian blobs | 4,184 | 25 / 35.3 | sizes too big **and**, by design, painted as round disks — wrong granule shape |
| `dog_blob_tr05` | Difference-of-Gaussians blobs | 3,839 | 25 / 27.4 | same disk-painting problem |
| `bg_k_sigma_mad_k25` | global `k·σ` (k=2.5) | 5,167 | — / 43.2 | one global cutoff → sweeps in haze |
| `bg_k_sigma_mad_k20` | global `k·σ` (k=2.0) | 6,043 | — / 49.1 | more haze pickup |
| `white_tophat_r4_k25` | white top-hat (r=4) | 19,375 | — / 38.9 | massively over-segments |
| `h_maxima_k25` | h-maxima (h=2.5·noise) | 0 | — | found nothing at this threshold — a parameter artifact, not pursued |

On LoG/DoG: the **size** numbers (mean 27–35 px vs ~17) show the detections are too big; the **round
disk shape** is a separate, fatal problem — those detectors mark a center and paint a fixed disk, so a
granule's real outline is lost regardless of size. That shape defect is visible by eye in the PNGs and
is why they were rejected for per-granule measurement.

---

## Conclusion

Otsu — with or without background subtraction, with an exclusion floor, or re-run inside regions —
plateaus around **2,000–2,600 blobs** on this field, consistent with its assumption that the two pixel
classes are balanced (dim foci are a small minority). The **adaptive** local threshold is the only
approach we tried that gets past that plateau **and** keeps true granule shapes: at `window=15, k=2.25`
it draws **4,247 blobs (~19% more than the hand mask)**, small and true-shaped, with no diffuse haze by
eye — fully headless.

### Scope and limits

- **Single field, chosen by eye.** This was selected by visually comparing masks on **one field of one
  dish**. "No haze" and "true shapes" are by-eye judgments, not quantified metrics.
- **Counts are not recall.** The blob counts compare methods to each other and to the hand mask; they
  are **not** scored against the 4,664 hand-labeled foci. The hand mask's measured recall is ~67%; the
  chosen method's exact recall has not yet been measured.
- **Next step:** confirm on 1–2 more comparable dishes (and, if a quantified recall is wanted, score the
  chosen mask against the labeled foci) before trusting it headless across the whole condition.

*Technical references:* `docs/methods/headless-puncta-thresholding.md` (code-level),
`docs/methods/how-puncta-detection-processes-the-image.md` (plain-language). Regenerate with
`scripts/build_mask_gallery.py`.
