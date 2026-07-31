"""Cellpose segmentation wrapper.

Pure function: image in, label array out. No store or GUI coupling.
Lazy-imports cellpose to avoid heavy dependency at startup.

Cellpose 4.x only (the pin is ``cellpose>=4.2,<5``). The 4.x API exposes a
single ``CellposeModel`` class whose built-in model is chosen with the
``pretrained_model`` argument — e.g. ``CellposeModel(pretrained_model="cpsam_v2")``.
The 3.x ``Cellpose`` class (with ``model_type``) is gone, so there is no version
branch here. Built-in 4.x models: ``cpsam_v2`` (default — improved CellposeSAM),
``cpsam`` (original), ``cpdino`` / ``cpdino-vitb`` (DINOv3 backbones).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from percell4.adapters.torch_device import DeviceResolution

# Default Cellpose 4.x model when a caller passes none. Mirrors the first entry
# of ``percell4.workflows.models.CELLPOSE_MODELS``; duplicated as a plain string
# rather than imported so the adapter does not depend on the workflows layer.
_DEFAULT_MODEL = "cpsam_v2"


def build_cellpose_model(
    model_type: str | None = None,
    gpu: bool = False,
    device: str | None = None,
    device_callback: Callable[[DeviceResolution], None] | None = None,
):
    """Construct a Cellpose 4.x ``CellposeModel`` on a resolved device.

    Useful for batch workflows that build the model once and reuse it across
    many images, avoiding the per-image construction cost. Returns the raw
    Cellpose model object; pass it to ``run_cellpose(..., model=)``.

    ``model_type`` is the built-in model name, forwarded to Cellpose as
    ``pretrained_model``. Defaults to :data:`_DEFAULT_MODEL` (``cpsam_v2``) when
    unset. Valid values are the 4.x built-ins (``cpsam_v2``, ``cpsam``,
    ``cpdino``, ``cpdino-vitb``); requires ``cellpose>=4.2``.

    ``device`` names an explicit torch device. When omitted, the override
    stored by the Advanced panel applies, so a call site that knows nothing
    about the setting still honors it. When a device resolves to something
    other than CPU it is handed to Cellpose outright, which bypasses that
    library's own CUDA-or-MPS-only resolver and reaches hardware it would
    otherwise refuse.

    ``device_callback`` receives the :class:`DeviceResolution` exactly once.
    This is the only place it fires: callers that cache a model and pass it
    back through ``run_cellpose(model=...)`` would never see it otherwise,
    and a per-image callback would raise one warning per frame of a stack.
    """
    from cellpose import models

    from percell4.adapters.torch_device import resolve_device
    from percell4.config.advanced import load_cellpose_device

    if model_type is None:
        model_type = _DEFAULT_MODEL

    override = device if device is not None else load_cellpose_device()
    resolution = resolve_device(gpu_requested=gpu, override=override)

    if device_callback is not None:
        device_callback(resolution)

    if resolution.device == "cpu":
        # Preserve the historical call shape exactly. Every unconfigured
        # install takes this path, and it must stay indistinguishable from
        # the behavior that predates device resolution.
        return models.CellposeModel(gpu=False, pretrained_model=model_type)

    import torch

    return models.CellposeModel(
        device=torch.device(resolution.device), pretrained_model=model_type
    )


def run_cellpose(
    image: NDArray,
    model_type: str | None = None,
    diameter: float | None = None,
    gpu: bool = False,
    channels: list[int] | None = None,
    flow_threshold: float = 0.4,
    cellprob_threshold: float = 0.0,
    min_size: int = 15,
    model=None,
    device: str | None = None,
    device_callback: Callable[[DeviceResolution], None] | None = None,
) -> NDArray[np.int32]:
    """Run Cellpose segmentation on a 2D image.

    Parameters
    ----------
    image : 2D array (H, W) or 3D array (H, W, C) for multi-channel
    model_type : built-in Cellpose 4.x model name, forwarded as
        ``pretrained_model``. Defaults to ``cpsam_v2`` if unset. Other values:
        ``cpsam``, ``cpdino``, ``cpdino-vitb``.
    diameter : estimated cell diameter in pixels (None = auto). Still honored in
        4.x (downsamples the image); niter auto-scales with it inside Cellpose.
    gpu : use GPU acceleration
    channels : accepted for call-site compatibility but ignored on Cellpose 4.x
    flow_threshold : flow error threshold (higher = more permissive)
    cellprob_threshold : cell probability threshold
    min_size : minimum cell size in pixels
    model : optional pre-built Cellpose model. When provided, model construction
        is skipped and this model is reused; ``model_type``, ``gpu``, ``device``,
        and ``device_callback`` are then all ignored -- that model already
        chose its device at build time. Use :func:`build_cellpose_model` for
        batch workflows.
    device : explicit torch device, or None to use the stored override.
        Ignored when ``model`` is supplied.
    device_callback : receives the :class:`DeviceResolution` if this call
        constructs the model. Ignored when ``model`` is supplied.

    Returns
    -------
    Label array (H, W) int32 where each cell has a unique integer ID.
    Background is 0.
    """
    if model is None:
        model = build_cellpose_model(
            model_type=model_type,
            gpu=gpu,
            device=device,
            device_callback=device_callback,
        )

    # Cellpose 4.x CellposeModel.eval returns a 3-tuple (masks, flows, diams);
    # the 3.x ``channels`` argument is gone.
    result = model.eval(
        image,
        diameter=diameter,
        flow_threshold=flow_threshold,
        cellprob_threshold=cellprob_threshold,
        min_size=min_size,
    )
    masks = result[0]
    return np.asarray(masks, dtype=np.int32)


def run_cellpose_stack(
    images: NDArray,
    model_type: str | None = None,
    diameter: float | None = None,
    gpu: bool = False,
    progress_callback=None,
    device: str | None = None,
    device_callback: Callable[[DeviceResolution], None] | None = None,
    **kwargs,
) -> NDArray[np.int32]:
    """Segment each timepoint of a ``(T, H, W)`` stack -> ``(T, H, W)`` int32.

    Builds the Cellpose model once and reuses it across frames (avoids the
    per-frame construction cost). Each frame is segmented independently, so
    the per-frame label ids are NOT consistent across time — tracking
    (TrackCells) makes them consistent. ``progress_callback(done, total)``
    is invoked after each frame when supplied.

    A ``(T, H, W)`` stack must NOT be passed to :func:`run_cellpose`
    directly: that function reads a 3D array as ``(H, W, C)`` multichannel.

    ``device_callback`` fires once, at model construction -- not once per
    frame. A hundred-frame stack reports its device once.
    """
    model = build_cellpose_model(
        model_type=model_type,
        gpu=gpu,
        device=device,
        device_callback=device_callback,
    )
    frames = []
    n = len(images)
    for t in range(n):
        frames.append(
            run_cellpose(images[t], diameter=diameter, model=model, **kwargs)
        )
        if progress_callback is not None:
            progress_callback(t + 1, n)
    return np.stack(frames, axis=0).astype(np.int32)


class CellposeSegmenter:
    """Segmenter port implementation backed by Cellpose.

    Conforms to percell4.ports.segmenter.Segmenter protocol.
    """

    def run(
        self,
        image: NDArray,
        model_type: str = _DEFAULT_MODEL,
        diameter: float | None = None,
        gpu: bool = False,
        flow_threshold: float = 0.4,
        cellprob_threshold: float = 0.0,
        min_size: int = 15,
        device: str | None = None,
    ) -> NDArray[np.int32]:
        return run_cellpose(
            image,
            model_type=model_type,
            diameter=diameter,
            gpu=gpu,
            flow_threshold=flow_threshold,
            cellprob_threshold=cellprob_threshold,
            min_size=min_size,
            device=device,
        )
