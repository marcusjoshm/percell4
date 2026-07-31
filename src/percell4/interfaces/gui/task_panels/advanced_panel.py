"""Advanced task panel — expert-only configuration.

Deliberately separate from the Segmentation surface. The settings here are
needed by a small minority of installs, and putting the device override next
to the Cellpose controls would make every user read past a knob they will
never touch.

Receives callbacks at construction — no launcher reference. Grouped by the
subsystem each setting belongs to, so later advanced settings land beside the
device override rather than inside its group.
"""

from __future__ import annotations

from collections.abc import Callable

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.adapters.torch_device import describe_torch_environment, resolve_device
from percell4.config.advanced import (
    AdvancedSettings,
    load_advanced_settings,
    save_advanced_settings,
)
from percell4.gui import theme

#: Seeds for the device combo. The list is a convenience, not a constraint:
#: valid device strings depend on the installed torch build and the hardware
#: (``cuda:2``, ``xpu:1``), so the field stays editable.
_COMMON_DEVICES = ("", "cuda", "cuda:0", "cuda:1", "mps", "xpu", "cpu")

#: Cap for a per-backend line in the readout. Torch's CUDA message runs to a
#: full paragraph with a download URL; at full length one unavailable backend
#: pushes the others out of view, which is the opposite of what a
#: what-does-this-machine-offer readout is for. The full text stays on the
#: tooltip, and the resolver's own warning is never truncated.
_READOUT_LINE_CAP = 72


def _first_sentence(text: str) -> str:
    """Trim a backend failure to its first sentence, capped for display."""
    head = text.split(". ")[0].strip().rstrip(".")
    if len(head) > _READOUT_LINE_CAP:
        head = head[: _READOUT_LINE_CAP - 1].rstrip() + "…"
    return head


class AdvancedPanel(QWidget):
    """Expert-only settings. Empty defaults leave behavior unchanged."""

    def __init__(
        self,
        *,
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._show_status = show_status
        self._environment_loaded = False
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("Advanced"))

        intro = QLabel(
            "Settings most installs never need. Leave everything blank for "
            "the standard behavior."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(intro)

        layout.addWidget(self._build_cellpose_group())
        layout.addWidget(self._build_environment_group())

    def _build_cellpose_group(self) -> QGroupBox:
        group = QGroupBox("Cellpose")
        form = QFormLayout(group)

        self._device = QComboBox()
        self._device.setEditable(True)
        self._device.addItems(_COMMON_DEVICES)
        self._device.setToolTip(
            "Explicit compute device for Cellpose, e.g. 'xpu' for Intel Arc "
            "or 'cuda:1' for a second NVIDIA card.\n\n"
            "Leave blank to auto-detect, which finds NVIDIA (CUDA), AMD "
            "(ROCm) and Apple (MPS) cards on its own — those need nothing "
            "here.\n\n"
            "Only applies when 'Use GPU' is checked on the Segment tab. An "
            "unusable device falls back to CPU with a warning."
        )
        # Wired at construction rather than at first use: a widget built
        # interactive whose edit signal is never connected is a repeat bug
        # shape here (docs/solutions/conventions/qt-wire-user-edit-signals).
        self._device.currentTextChanged.connect(self._on_device_edited)
        form.addRow("Device:", self._device)

        self._device_status = QLabel("")
        self._device_status.setWordWrap(True)
        self._device_status.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        form.addRow("", self._device_status)

        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Store this device and check it works now.")
        self._save_btn.clicked.connect(lambda: self._on_save())
        form.addRow("", self._save_btn)

        return group

    def _build_environment_group(self) -> QGroupBox:
        group = QGroupBox("Detected PyTorch environment")
        outer = QVBoxLayout(group)

        self._env_label = QLabel("Checking…")
        self._env_label.setWordWrap(True)
        self._env_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self._env_label)

        row = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.setToolTip(
            "Re-check which devices this machine offers. Use after "
            "installing a different PyTorch build."
        )
        refresh.clicked.connect(lambda: self._on_refresh_environment())
        row.addWidget(refresh)
        row.addStretch()
        outer.addLayout(row)

        return group

    # ── Settings ──────────────────────────────────────────────────────

    def _load_settings(self) -> None:
        stored = load_advanced_settings().cellpose_device
        self._device.setCurrentText(stored or "")

    def _on_device_edited(self, _text: str) -> None:
        """Clear a stale verdict as soon as the field changes.

        Leaving the previous device's result on screen next to a new,
        unsaved value is how a user comes to believe an untested device
        works.
        """
        self._device_status.setText("")

    def _on_save(self) -> None:
        """Store the device, then say whether it actually works.

        Checking here rather than only at run time is the point: this whole
        feature exists because a device that silently falls back to CPU
        reads as a broken install. Accepting a device without comment and
        reporting the problem an hour into a run would rebuild that
        experience one layer up.

        An unusable device is still stored — a user may be configuring for
        hardware they are about to install. The warning informs; it does not
        refuse.
        """
        text = self._device.currentText().strip()
        device = text or None

        save_advanced_settings(AdvancedSettings(cellpose_device=device))

        if device is None:
            message = "Auto-detect: Cellpose will find CUDA, ROCm or MPS on its own."
            colour = theme.TEXT_MUTED
            self._show_status("Cellpose device: auto-detect")
        else:
            resolution = resolve_device(gpu_requested=True, override=device)
            if resolution.fell_back:
                message = resolution.reason
                colour = theme.WARNING
                self._show_status(f"Cellpose device {device!r} is not usable here")
            else:
                message = f"Saved. {device} is available on this machine."
                colour = theme.SUCCESS
                self._show_status(f"Cellpose device: {device}")

        self._device_status.setText(message)
        self._device_status.setStyleSheet(f"color: {colour};")

    # ── Environment readout ───────────────────────────────────────────

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Build the readout the first time the panel is actually shown.

        Probing initializes every backend it can reach, which costs seconds
        and reserves memory. Deferring it to first show keeps that cost off
        the launcher's startup for the majority who never open this panel.
        """
        super().showEvent(event)
        if not self._environment_loaded:
            self._environment_loaded = True
            self._refresh_environment()

    def _on_refresh_environment(self) -> None:
        self._refresh_environment()

    def _refresh_environment(self) -> None:
        report = describe_torch_environment()
        lines = [report.summary]
        for name, failure in report.backends.items():
            lines.append(
                f"  • {name}: available"
                if failure is None
                else f"  • {name}: unavailable — {_first_sentence(failure)}"
            )
        self._env_label.setText("\n".join(lines))
        self._env_label.setToolTip(
            "\n".join(
                f"{name}: {failure or 'available'}"
                for name, failure in report.backends.items()
            )
        )
