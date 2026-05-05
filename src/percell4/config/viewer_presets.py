"""Centralized napari layer display presets for PerCell4.

Edit values in this file to retune any napari display behavior — layer
colormaps, blending, opacity, label colors, mask color dicts. No napari
or Qt imports here; call sites construct DirectLabelColormap from the
data exposed below.

Pattern mirrors ``percell4.gui.theme`` for Qt styling: pure-data module
of ``Final``-typed constants, imported absolutely.

``None`` sentinel convention
----------------------------

A constant set to ``None`` means "don't pass this kwarg to napari; let
napari use its own default." Editing such a constant from ``None`` to a
concrete value (a float, a tuple, a dict) starts applying that value at
every call site that reads it — no further code edits required.

Constants set to a numeric/string value are *always* passed to napari.

Caller-passed ``kwarg=None`` is treated as "kwarg absent" by the call
sites' pop pattern (the conditional skips re-injection in both cases).
To force napari's default at a call site, omit the kwarg entirely; do
not write ``opacity=None``.
"""

from __future__ import annotations

from typing import Final

# ── Channel → colormap mapping ──────────────────────────────
CHANNEL_COLORMAPS: Final[dict[str, str]] = {
    "dapi": "blue",
    "hoechst": "blue",
    "gfp": "green",
    "fitc": "green",
    "alexa488": "green",
    "rfp": "red",
    "mcherry": "red",
    "tritc": "red",
    "alexa594": "red",
    "cy5": "magenta",
    "alexa647": "magenta",
    "bf": "gray",
    "brightfield": "gray",
    "dic": "gray",
    "phase": "gray",
    "mng": "gray",
    "casir": "magenta",
    "mtq2": "green",
}

CHANNEL_FALLBACK_CYCLE: Final[tuple[str, ...]] = (
    "gray", "gray", "gray", "gray", "gray", "gray",
)


def _colormap_for_channel(name: str, color_index: int = 0) -> tuple[str, int]:
    """Auto-detect colormap from channel name, or cycle through distinct colors.

    Returns ``(colormap_name, updated_color_index)``. Pure function — caller
    owns the index. Substring match on a normalized lower-cased channel name
    (whitespace, ``-``, ``_`` stripped) against ``CHANNEL_COLORMAPS`` keys;
    falls back to ``CHANNEL_FALLBACK_CYCLE`` indexed modulo cycle length.
    """
    key = name.lower().replace(" ", "").replace("-", "").replace("_", "")
    for pattern, cmap in CHANNEL_COLORMAPS.items():
        if pattern in key:
            return cmap, color_index
    color = CHANNEL_FALLBACK_CYCLE[color_index % len(CHANNEL_FALLBACK_CYCLE)]
    return color, color_index + 1


def _optional_kwargs(**axes: object) -> dict[str, object]:
    """Filter ``None`` values from kwargs so call sites can pass preset
    constants through the ``None``-sentinel discipline.

    Used by specialized call sites that construct napari ``add_*`` kwargs
    directly (rather than through ``ViewerWindow.add_image`` / ``add_labels``,
    which do their own per-axis pop). Spread the result with ``**`` at the
    call site::

        viewer.add_image(
            data, name=name, colormap=cmap, blending=blending,
            **_optional_kwargs(
                opacity=THRESHOLD_QC_GROUP_IMAGE_OPACITY,
                contrast_limits=THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE,
            ),
        )

    Pure function — no napari, no Qt, no module state.
    """
    return {k: v for k, v in axes.items() if v is not None}


# ── Image layer defaults ────────────────────────────────────
IMAGE_DEFAULT_BLENDING: Final[str] = "additive"
IMAGE_DEFAULT_OPACITY: Final[float | None] = None
# Override for ``add_image``'s auto-from-data contrast computation.
# Precedence: caller-passed ``contrast_limits=`` wins, then this override
# if non-None, then the auto-from-nanmin/nanmax fallback.
IMAGE_DEFAULT_CONTRAST_OVERRIDE: Final[tuple[float, float] | None] = None

# Note: image colormap is auto-resolved per channel via
# ``_colormap_for_channel`` + CHANNEL_COLORMAPS. Do NOT add a global
# IMAGE_DEFAULT_COLORMAP — it would conflict with auto-resolution.

# ── Labels layer defaults ───────────────────────────────────
LABELS_DEFAULT_BLENDING: Final[str] = "translucent_no_depth"
LABELS_DEFAULT_OPACITY: Final[float | None] = 0.25
# When non-None, ``add_labels`` wraps this dict in a DirectLabelColormap
# and applies it whenever the caller did not pass ``colormap=`` explicitly.
# Caller-passed colormap always wins.
LABELS_DEFAULT_COLOR_DICT: Final[dict[int | None, str] | None] = None

# ── Mask layer defaults (runtime mask creation; tunable independently) ──
MASK_DEFAULT_BLENDING: Final[str] = "translucent_no_depth"
MASK_DEFAULT_OPACITY: Final[float] = 0.75
BINARY_MASK_COLOR_DICT: Final[dict[int | None, str]] = {
    0: "transparent", 1: "yellow", None: "transparent",
}

# ── Shared label-overlay defaults for preview-style layers ──
# Used by every "preview" labels layer: threshold-QC group preview,
# threshold-QC yellow_cmap preview, segmentation cleanup preview, analysis
# threshold preview. To retune any preview-overlay opacity/blending in one
# place, edit here. Values that genuinely need to differ get their own
# constant (see GROUPED_SEG_CLEANUP_PREVIEW_OPACITY below).
LABELS_OVERLAY_DEFAULT_OPACITY: Final[float] = 0.5
LABELS_OVERLAY_DEFAULT_BLENDING: Final[str] = "translucent"

# ── Selection & filter highlight RGBAs (consumed by viewer._update_label_display) ──
# Char-by-char preservation matters: these tuples were tuned through the
# four compound bugs in
# docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md
SELECTION_HIGHLIGHT_RGBA: Final[tuple[float, ...]] = (1.0, 1.0, 0.0, 0.8)
SELECTION_DIM_NONSELECTED: Final[tuple[float, ...]] = (0.5, 0.5, 0.5, 0.15)
FILTER_ONLY_VISIBLE_RGBA: Final[tuple[float, ...]] = (0.3, 0.8, 0.8, 0.5)
TRANSPARENT_RGBA: Final[tuple[float, ...]] = (0.0, 0.0, 0.0, 0.0)

# ── Multi-select staged overlay ─────────────────────────────
STAGED_OVERLAY_LAYER_NAME: Final[str] = "_multi_select_staged"
STAGED_OVERLAY_COLOR: Final[tuple[float, ...]] = (0.0, 0.9, 0.9, 0.6)
STAGED_OVERLAY_OPACITY: Final[float] = 0.6
STAGED_OVERLAY_BLENDING: Final[str] = "translucent"

# ── Phasor ROI mask (peer-view picks hex; launcher reads opacity/blending) ──
PHASOR_ROI_MASK_OPACITY: Final[float] = 0.4
PHASOR_ROI_MASK_BLENDING: Final[str] = "translucent"

# ── Threshold-QC group preview (image; labels overlay reads LABELS_OVERLAY_*) ──
THRESHOLD_QC_GROUP_COLORS: Final[tuple[str, ...]] = (
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
)
THRESHOLD_QC_GROUP_IMAGE_COLORMAP: Final[str] = "gray"
THRESHOLD_QC_GROUP_IMAGE_BLENDING: Final[str] = "additive"
THRESHOLD_QC_GROUP_IMAGE_OPACITY: Final[float | None] = None
THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE: Final[tuple[float, float] | None] = None

# ── Yellow labels color_dict (shared by threshold_qc + analysis_panel) ──
YELLOW_LABEL_COLOR_DICT: Final[dict[int | None, str]] = {
    0: "transparent", 1: "yellow", None: "transparent",
}

# ── Yellow ROI shapes preset (shared between threshold-QC + analysis) ──
# Used by threshold_qc.py:432 and analysis_panel.py:387. The threshold-QC
# call site passes ``blending=YELLOW_ROI_BLENDING`` explicitly; the analysis
# call site continues to omit ``blending=``, preserving today's drift at the
# call-site level rather than in the module.
YELLOW_ROI_EDGE_COLOR: Final[str] = "yellow"
YELLOW_ROI_EDGE_WIDTH: Final[int] = 2
YELLOW_ROI_FACE_COLOR: Final[tuple[float, ...]] = (1, 1, 0, 0.1)
YELLOW_ROI_BLENDING: Final[str] = "additive"
# Layer-level opacity multiplies into face, edge, and text alphas at the
# vispy node level. Effective face alpha = face_color[3] * layer.opacity.
# Setting YELLOW_ROI_OPACITY tunes ALL sub-elements globally; for
# per-element alpha, edit YELLOW_ROI_FACE_COLOR[3] / YELLOW_ROI_EDGE_COLOR.
YELLOW_ROI_OPACITY: Final[float | None] = None

# ── Segmentation cleanup previews (drift survives in opacity only) ──
# segmentation_panel.py:491 reads LABELS_OVERLAY_DEFAULT_OPACITY (0.5).
# seg_qc.py:450 reads GROUPED_SEG_CLEANUP_PREVIEW_OPACITY (0.6 — the drift).
# Both read LABELS_OVERLAY_DEFAULT_BLENDING for the "translucent" string.
GROUPED_SEG_CLEANUP_PREVIEW_OPACITY: Final[float] = 0.6

# ── FLIM lifetime ───────────────────────────────────────────
FLIM_LIFETIME_COLORMAP: Final[str] = "turbo"
FLIM_LIFETIME_BLENDING: Final[str] = "additive"
FLIM_LIFETIME_OPACITY: Final[float | None] = None
FLIM_LIFETIME_CONTRAST_OVERRIDE: Final[tuple[float, float] | None] = None
