"""Adapter: napari ViewerPort implementation.

This is the ONLY file in the codebase that should import napari
(besides the existing gui/viewer.py which will be migrated later).
It wraps the existing ViewerWindow for now, bridging the port
interface to napari's API.
"""

from __future__ import annotations

import logging

from percell4.domain.dataset import DatasetView

logger = logging.getLogger(__name__)


class NapariViewerAdapter:
    """ViewerPort implementation backed by napari.

    Conforms to percell4.ports.viewer.ViewerPort.

    Takes the existing gui.viewer.ViewerWindow at construction —
    we reuse it rather than creating a second napari instance.
    The adapter translates domain types into napari API calls.
    """

    def __init__(self, viewer_window) -> None:
        """Accept the existing ViewerWindow instance.

        Args:
            viewer_window: A percell4.gui.viewer.ViewerWindow instance.
                           Typed loosely to avoid importing gui/ in this module's
                           signature — the composition root provides it.
        """
        self._vw = viewer_window

    def show_dataset(self, view: DatasetView) -> None:
        """Clear viewer and display all channels, labels, and masks."""
        self._vw.clear()

        for name, image in view.channel_images.items():
            self._vw.add_image(image, name=name)

        for name, label_data in view.labels.items():
            self._vw.add_labels(label_data, name=name)

        for name, mask_data in view.masks.items():
            self._vw.add_mask(mask_data, name=name)

        self._vw.show()
        self._vw.raise_()
        self._vw.activateWindow()

    def clear(self) -> None:
        """Remove all layers from the viewer."""
        self._vw.clear()

    def close(self) -> None:
        """Close the viewer window."""
        self._vw.close()
