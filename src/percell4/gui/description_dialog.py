"""Editor for a dataset's free-text experiment description.

A dataset's description is the only place, other than its filename, that
records what the experiment actually was -- sample, preparation, condition,
and whatever else the researcher wants to be able to recognise weeks later.
This dialog edits that text; it performs no file I/O, so the caller owns
the write and this module stays testable without a dataset on disk.

Clearing is confirmed before it destroys a non-empty description, matching
how the Data tab's layer deletes already guard a destructive action.
Cancelling discards typed text without a prompt, matching the repo's other
text prompts.
"""

from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui._dialog_utils import (
    cap_to_screen,
    make_freestanding,
    message_box,
    wrap_in_scroll,
)


@dataclass(frozen=True)
class DescriptionResult:
    """What the user decided in the dialog.

    Three outcomes, kept distinct because they mean different things to the
    caller: ``saved`` carries new text to write, ``cleared`` asks for the
    description to be removed, and cancelled asks for nothing at all.
    ``cleared`` is deliberately not "saved with empty text" -- the caller
    routes them the same way in the end, but the user's intent differs and
    conflating them would hide that in the status message.
    """

    accepted: bool
    clear: bool
    text: str | None

    @classmethod
    def saved(cls, text: str) -> DescriptionResult:
        return cls(accepted=True, clear=False, text=text)

    @classmethod
    def cleared(cls) -> DescriptionResult:
        return cls(accepted=True, clear=True, text=None)

    @classmethod
    def cancelled(cls) -> DescriptionResult:
        return cls(accepted=False, clear=False, text=None)


class DescriptionDialog(QDialog):
    """Multi-line editor for one dataset's description."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        description: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dataset Description")
        self._result = DescriptionResult.cancelled()
        self._build_ui(description or "")
        make_freestanding(self)
        cap_to_screen(self)

    def _build_ui(self, description: str) -> None:
        content = QWidget()
        content_layout = QVBoxLayout(content)

        hint = QLabel(
            "Describe the sample, how it was prepared, the experimental "
            "condition, or anything else that will help you recognise this "
            "dataset later. Saved inside the .h5 file."
        )
        hint.setWordWrap(True)
        content_layout.addWidget(hint)

        self._editor = QPlainTextEdit()
        self._editor.setPlainText(description)
        self._editor.setPlaceholderText(
            "e.g. HeLa p14, fixed 4% PFA 15min, 2h 10uM drug at 37C"
        )
        content_layout.addWidget(self._editor)

        layout = QVBoxLayout(self)
        layout.addWidget(wrap_in_scroll(content))

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self._clear_button = QPushButton("Clear")
        self._clear_button.setToolTip("Remove this dataset's description.")
        self._clear_button.clicked.connect(self._on_clear)
        buttons.addButton(self._clear_button, QDialogButtonBox.DestructiveRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(560, 380)

    # ── Actions ───────────────────────────────────────────────

    def _on_save(self) -> None:
        self._result = DescriptionResult.saved(self._editor.toPlainText())
        self.accept()

    def _on_clear(self) -> None:
        """Remove the description, confirming first when there is one.

        The confirmation only fires when there is text to lose. Clearing an
        already-empty editor destroys nothing, so prompting there would be
        friction with no risk behind it.
        """
        if self._editor.toPlainText().strip():
            reply = message_box(
                self,
                "Confirm Clear",
                "Clear this dataset's description? This cannot be undone.",
                icon=QMessageBox.Question,
                buttons=QMessageBox.Yes | QMessageBox.No,
                default_button=QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._result = DescriptionResult.cleared()
        self.accept()

    # ── Result ────────────────────────────────────────────────

    def result_value(self) -> DescriptionResult:
        """What the user chose. Cancelled until Save or Clear is taken."""
        return self._result


def edit_description(
    parent: QWidget | None, description: str | None,
) -> DescriptionResult:
    """Show the editor modally and return the user's decision.

    The dialog never touches the dataset -- the caller writes, clears, or
    does nothing based on the returned :class:`DescriptionResult`.
    """
    dialog = DescriptionDialog(parent, description=description)
    if dialog.exec_() != QDialog.Accepted:
        return DescriptionResult.cancelled()
    return dialog.result_value()
