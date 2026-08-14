# Screenshot capture list

The README and `docs/workflow-protocol.md` reference the images below. Every slot
that is not yet filled points at `_placeholder.png`, so it always renders — an
unfilled slot shows a "SCREENSHOT PENDING" card rather than a broken-image glyph.

To fill a slot: capture the shot, save it here under the target filename, and
change that one image reference from `_placeholder.png` to the real filename.
Nothing else needs to change.

---

## Capture settings — identical for every shot

Consistency matters more than any individual shot. GitHub scales every image to
the same container width, so a 1x capture sits next to a 2x capture at double the
apparent size. Fix these once and use them for the whole set:

| Setting | Value |
|---|---|
| Format | PNG |
| Capture density | Retina / 2x, then downscale to the target width below |
| Target width | 1600 px (hero, result) · 1200 px (protocol and dialog shots) |
| OS appearance | Light — matches the logo and the majority of GitHub sessions |
| Window chrome | Cropped out. No macOS traffic lights, no title bar |
| Visible paths | None. Blur or rename anything showing a home directory, a username, or an unpublished dataset name |
| Cursor | Not visible |

Capture the whole set in one sitting under one appearance setting. A mixed light
and dark set reads as two different products.

---

## Shots

### 1. `hero.png` — README, above the fold

**Framing:** wide banner crop, roughly 3:1 (1600 x 530). This is deliberate and
load-bearing: a full 16:9 window grab renders about 495 px tall in GitHub's ~880 px
column and pushes the opening paragraph off the first screen, which defeats the
point of the hero.

**Window:** the napari viewer with a segmentation overlay, plus one peer window
visible at the edge of frame — the cell table or the scatter plot. The single
thing this shot has to convey is that PerCell4 is a multi-window analysis
environment, not a script.

**Dataset state:** a field with enough cells to read as real data, a segmentation
layer active with visible label boundaries, and at least one mask layer in the
layer list. Avoid a field so dense the individual cells are unreadable at 1600 px.

**Must be visible:** the napari layer list (it shows the Channel / Segmentation /
Mask structure at a glance), and label boundaries over real intensity data.

**alt:** `PerCell4's napari viewer showing a cell segmentation overlay alongside the per-cell data table`
**caption:** *Segmentation, measurement, and QC in one synchronized workspace.*

---

### 2. `result-manual-vs-adaptive.png` — README, result section

**Already generated** — see `docs/screenshots/result-manual-vs-adaptive.png`.
Built from `docs/archive/puncta_mask_gallery/`, overlaying `manual_SG_mask.png`
and `adaptive_w15_k225_WINNER.png` on `reference_mNG_grayscale.png`. Recapture is
only needed if the underlying gallery is regenerated.

**alt:** `Side-by-side comparison of a hand-drawn stress granule mask and the adaptive local clipping result on the same field`
**caption:** *Same field, same typical particle size. The adaptive detector finds the small, dim foci the hand-drawn mask misses — with no manual QC step.*

---

### 3-8. Protocol shots — `docs/workflow-protocol.md`

One per major step. All at 1200 px wide, dialog only, chrome cropped.

| # | Filename | Window / dialog | What must be visible |
|---|---|---|---|
| 3 | `protocol-01-import.png` | Compress TIFF Dataset dialog | The Discovery combo expanded, showing all three modes (Subdirectory, Flat Directory, Tokenless (by name)) |
| 4 | `protocol-02-stitching.png` | The stitching form | Overlap fields and the fusion selector (None / Linear Blending) |
| 5 | `protocol-03-segment.png` | Launcher, Segmentation tab | The Cellpose model selector, the Diameter field, and the magenta diameter reference circle over real cells |
| 6 | `protocol-04-seg-qc.png` | Segmentation QC | Label boundaries over the intensity channel, with the `Accept && Next →` button in frame |
| 7 | `protocol-05-rounds.png` | Workflow config dialog, thresholding rounds | The per-round card list with one round expanded, showing only the selected method's fields |
| 8 | `protocol-06-threshold-qc.png` | Threshold QC | A grouped threshold result mid-review, with the group navigation visible |

For each protocol shot, write an `alt` line of one sentence naming the dialog and
what it is doing, and a `caption` line saying why the reader is looking at it.

---

### 9. `social-preview.png` — GitHub repository settings

Not referenced by any markdown file. This is the card that renders when the repo
URL is pasted into LinkedIn, Slack, or email — which, for a link on a CV, is often
the first thing anyone sees.

**Size:** exactly 1280 x 640 px.
**Content:** the PerCell4 logo, the project name, and the one-sentence positioning
from the top of the README, over a crop of the hero image or a clean segmentation
field. Keep text well inside the middle 80% — the card is cropped differently by
each platform.

**Where it goes:** GitHub → repository **Settings** → **General** → **Social
preview** → *Upload an image*. This is a repository setting, not a commit.

---

## Repository settings to apply alongside these

Same trip, same reader, not carried by any file in this repo:

- **About description** — the one-sentence positioning from the top of the README.
- **Topics** — `microscopy`, `flim`, `cell-segmentation`, `cellpose`, `napari`,
  `hdf5`, `image-analysis`, `single-cell`, `phasor`, `pyqt`.
- **Website** — leave empty until a docs site exists; an empty field reads better
  than one pointing at a placeholder.
