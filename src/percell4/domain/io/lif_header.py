"""Reader for the XML header block at the front of a Leica ``.lif`` file.

A ``.lif`` opens with one block describing the whole file — element tree,
acquisition settings, and the FLIM phasor calibration. Everything PerCell4
wants from a ``.lif`` lives here, so this module reads the header bytes and
stops; pixel data in the object memory blocks that follow is never touched.

The same header can also arrive as a standalone ``.xml`` file exported by
``tools/extract_lif_metadata.py`` (shipped as a Windows exe for the LAS X
acquisition PC, where copying a multi-GB ``.lif`` off the machine is the
alternative). :func:`read_lif_metadata` accepts either form and returns the
same parsed tree.

Layout, little-endian::

    offset 0   int32   block marker, always 0x70
    offset 4   int32   bytes remaining in the block
    offset 8   uint8   separator, always 0x2A
    offset 9   int32   XML length in UTF-16 *characters*
    offset 13  bytes   the XML, UTF-16LE

Size the payload read from the character count at offset 9, never from the
byte count at offset 4: that field covers the separator and the character
count as well as the XML, so it runs five bytes long. A ``.lif`` continues
straight into its memory blocks, so trusting it splices five foreign bytes
onto the end of the XML.

Full format notes, including where the phasor calibration sits and which of
the two calibration records is the right one, are in
``docs/reference/lif-xml-header.md``.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from percell4.domain.errors import LifHeaderError

BLOCK_MARKER = 0x70
SEPARATOR = 0x2A

# marker + byte count + separator + character count
_PREFIX_BYTES = 13

# Root element of every LAS X header document; a sidecar .xml carrying any
# other root is some unrelated XML the user picked by mistake.
_ROOT_TAG = "LMSDataContainerHeader"


def read_lif_metadata(path: Path | str) -> ET.Element:
    """Return the parsed LIF header XML from a ``.lif`` or a ``.xml``.

    Routes on suffix: ``.xml`` is read as a metadata sidecar produced by
    ``tools/extract_lif_metadata.py`` (the header XML re-encoded as UTF-8,
    possibly pretty-printed); anything else is read as a ``.lif`` container
    via :func:`read_lif_header`. Both paths raise :class:`LifHeaderError`
    with the file name and the failed check, so callers keep one error
    contract regardless of which form the user supplied.
    """
    path = Path(path)
    if path.suffix.lower() == ".xml":
        return _read_xml_sidecar(path)
    return read_lif_header(path)


def _read_xml_sidecar(path: Path) -> ET.Element:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise LifHeaderError(
            f"{path.name}: not well-formed XML — {exc}"
        ) from exc
    if root.tag != _ROOT_TAG:
        raise LifHeaderError(
            f"{path.name}: root element is <{root.tag}>, expected "
            f"<{_ROOT_TAG}> — not a LIF metadata export"
        )
    return root


def read_lif_header(path: Path | str) -> ET.Element:
    """Return the parsed XML header of the ``.lif`` at ``path``.

    Raises :class:`LifHeaderError` when the container prefix is not a ``.lif``
    header, when the declared payload runs past the end of the file, or when
    the payload is not well-formed XML. The message names the file and the
    check that failed, so a caller can tell "this is not a ``.lif``" apart
    from "this ``.lif`` is damaged".
    """
    path = Path(path)

    with open(path, "rb") as handle:
        prefix = handle.read(_PREFIX_BYTES)
        if len(prefix) < _PREFIX_BYTES:
            raise LifHeaderError(
                f"{path.name}: file is too short to hold a .lif header "
                f"({len(prefix)} bytes, need at least {_PREFIX_BYTES})"
            )

        marker, _block_bytes = struct.unpack("<ii", prefix[:8])
        separator = prefix[8]
        (nchars,) = struct.unpack("<i", prefix[9:13])

        if marker != BLOCK_MARKER:
            raise LifHeaderError(
                f"{path.name}: bad block marker 0x{marker:x} at offset 0 "
                f"(expected 0x{BLOCK_MARKER:x}) — not a .lif file"
            )
        if separator != SEPARATOR:
            raise LifHeaderError(
                f"{path.name}: bad separator 0x{separator:x} at offset 8 "
                f"(expected 0x{SEPARATOR:x})"
            )
        if nchars <= 0:
            raise LifHeaderError(
                f"{path.name}: header declares {nchars} XML characters"
            )

        # _block_bytes is deliberately unused for sizing — see module docstring.
        payload = handle.read(nchars * 2)

    if len(payload) != nchars * 2:
        raise LifHeaderError(
            f"{path.name}: header declares {nchars} XML characters "
            f"({nchars * 2} bytes) but only {len(payload)} bytes remain — "
            "the file is truncated"
        )

    try:
        return ET.fromstring(payload.decode("utf-16-le"))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise LifHeaderError(f"{path.name}: header XML is malformed — {exc}") from exc
