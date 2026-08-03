"""Canonical channel display / storage name derivation.

A channel "token" is whatever the channel regex captures: a number (``"00"``)
for the numeric ``_ch(\\d+)`` convention, or a name (``"DNA"``, ``"SG_mask"``)
for tokenless name-suffixed imports.  :func:`channel_display_name` maps that
token to the single canonical string that is *both* shown in the UI and stored
verbatim in ``/metadata.channel_names``.

This is the one source of truth for the channel-name string.  The importer
(producer) and every consumer (compress dialog, single-cell workflow config
dialog) call it, so a channel name never diverges across the pipeline — the
producer/consumer mismatch that previously surfaced minutes into a segmentation
pass cannot recur.

Numeric tokens keep their historical ``chXX`` form byte-for-byte; a bare token
(single unnamed channel) is ``"ch0"``.  Name tokens are returned verbatim.

Known limitation: a name token consisting only of digits (e.g. a wavelength
channel literally named ``488``) is indistinguishable from a numeric channel id
and is rendered ``ch488``.  The motivating use case uses alphabetic names; use
Manual mode to rename such a channel if needed.
"""

from __future__ import annotations


def channel_display_name(token: str) -> str:
    """Canonical display/storage name for a channel *token*.

    ``"" -> "ch0"``, ``"00" -> "ch00"``, ``"1" -> "ch1"`` (numeric legacy form);
    ``"DNA" -> "DNA"``, ``"SG_mask" -> "SG_mask"`` (name tokens, verbatim).
    """
    if not token:
        return "ch0"
    if token.isdigit():
        return f"ch{token}"
    return token
