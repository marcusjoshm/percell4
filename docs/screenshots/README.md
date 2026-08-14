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
| Target width | 1600 px (main window) · 1200 px (protocol and dialog shots) |
| Application theme | Dark — matches `main-window.png`, which is already in the README |
| Window chrome | Fine to include; it reads as a desktop application |
| Visible paths | None. Blur or rename anything showing a home directory, a username, or an unpublished dataset name |
| Cursor | Not visible |

Capture the whole set in one sitting under one appearance setting. A mixed light
and dark set reads as two different products.

---

## Shots

### 1. `main-window.png` — README ✅ filled

**Lives at `art/main-window.png`**, not in this directory: it ships on `main`
alongside the logo, while this capture list stays on `development`.

Shows the session control strip, the launcher, the viewer, and the phasor plot
with a FLIM phasor histogram against the universal semicircle. 1600 x 944,
downscaled from a 6300 x 3716 retina capture.

Note the S axis: it reads 0 to 0.6, with the semicircle apex at 0.5. Any
replacement must be captured with the fix from
`fix/phasor-s-axis-si-prefix` in place, or the axis will read 0 to 600 and the
screenshot will contradict the software.

**alt:** `The PerCell4 launcher, session controls, image viewer, and phasor plot, showing a FLIM phasor histogram against the universal semicircle`

The README references it with no caption, matching the surrounding text.

To replace it, keep the same filename and the settings below; nothing else needs
to change.

---

### 2. `result-manual-vs-adaptive.png` — not used in the README

**Already generated**, built from `docs/archive/puncta_mask_gallery/` by overlaying
`manual_SG_mask.png` and `adaptive_w15_k225_WINNER.png` on
`reference_mNG_grayscale.png`.

This is a method-comparison figure and belongs with the validation record in
[`docs/methods/`](../methods/), not in the README — the README describes what the
software does and leaves method validation to the methods documents and the
paper. Kept here as an available asset.

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

### 9. `social-preview.png` — GitHub repository settings (optional)

Not referenced by any markdown file. This is the card GitHub renders when the repo
URL is shared in a chat client or email. Optional; the repo works fine without one.

**Size:** exactly 1280 x 640 px.
**Content:** the PerCell4 logo, the project name, and the opening sentence from the
README, over a crop of the main-window screenshot or a clean segmentation field.
Keep text well inside the middle 80% — the card is cropped differently by each
platform.

**Where it goes:** GitHub → repository **Settings** → **General** → **Social
preview** → *Upload an image*. This is a repository setting, not a commit.

---

## Repository settings to apply alongside these

Not carried by any file in this repo:

- **About description** — the opening sentence from the README.
- **Topics** — `microscopy`, `flim`, `cell-segmentation`, `cellpose`, `napari`,
  `hdf5`, `image-analysis`, `single-cell`, `phasor`, `pyqt`.
- **Website** — leave empty until a docs site exists; an empty field reads better
  than one pointing at a placeholder.
