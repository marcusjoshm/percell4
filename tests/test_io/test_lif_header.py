"""Tests for the Leica ``.lif`` container header reader.

Every test synthesises its own container via the ``lif_header_bytes``
fixture. The reference ``.lif`` is 78 MB and is not checked in; see
``docs/reference/lif-xml-header.md`` for the format these tests encode.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from percell4.domain.errors import LifHeaderError
from percell4.domain.io.lif_header import read_lif_header

MINIMAL = (
    '<LMSDataContainerHeader Version="2">'
    '<Element Name="Region_1"><Data><Depth>16</Depth></Data></Element>'
    "</LMSDataContainerHeader>"
)


def _write(tmp_path: Path, blob: bytes, name: str = "sample.lif") -> Path:
    path = tmp_path / name
    path.write_bytes(blob)
    return path


def test_reads_root_and_nested_text(tmp_path, lif_header_bytes):
    root = read_lif_header(_write(tmp_path, lif_header_bytes(MINIMAL)))

    assert root.tag == "LMSDataContainerHeader"
    assert root.get("Version") == "2"
    assert root.find(".//Depth").text == "16"


def test_non_ascii_text_survives_the_utf16_decode(tmp_path, lif_header_bytes):
    xml = (
        '<LMSDataContainerHeader Version="2">'
        "<Name>Ölçüm · µm² — Región</Name>"
        "</LMSDataContainerHeader>"
    )

    root = read_lif_header(_write(tmp_path, lif_header_bytes(xml)))

    assert root.find("Name").text == "Ölçüm · µm² — Región"


def test_payload_is_sized_from_the_character_count_not_the_byte_field(
    tmp_path, lif_header_bytes
):
    """The offset-4 field counts five bytes more than the XML payload.

    A real ``.lif`` continues into object memory blocks immediately after the
    header, so sizing the read from that field pulls five bytes of the next
    block into the XML and corrupts it. Appending a trailing block here makes
    that failure mode reachable rather than theoretical.
    """
    blob = lif_header_bytes(MINIMAL) + struct.pack("<ii", 0x70, 38) + b"\x2a" * 38

    root = read_lif_header(_write(tmp_path, blob))

    assert root.find(".//Depth").text == "16"
    assert len(list(root)) == 1


def test_rejects_a_bad_block_marker(tmp_path, lif_header_bytes):
    path = _write(tmp_path, lif_header_bytes(MINIMAL, marker=0x71))

    with pytest.raises(LifHeaderError) as excinfo:
        read_lif_header(path)

    assert "marker" in str(excinfo.value).lower()
    assert path.name in str(excinfo.value)


def test_rejects_a_bad_separator(tmp_path, lif_header_bytes):
    path = _write(tmp_path, lif_header_bytes(MINIMAL, separator=0x2B))

    with pytest.raises(LifHeaderError) as excinfo:
        read_lif_header(path)

    assert "separator" in str(excinfo.value).lower()


def test_rejects_a_payload_truncated_mid_xml(tmp_path, lif_header_bytes):
    path = _write(tmp_path, lif_header_bytes(MINIMAL, truncate=40))

    with pytest.raises(LifHeaderError):
        read_lif_header(path)


def test_rejects_a_character_count_longer_than_the_payload(
    tmp_path, lif_header_bytes
):
    path = _write(tmp_path, lif_header_bytes(MINIMAL, nchars=len(MINIMAL) + 500))

    with pytest.raises(LifHeaderError):
        read_lif_header(path)


def test_rejects_well_formed_container_holding_malformed_xml(
    tmp_path, lif_header_bytes
):
    path = _write(tmp_path, lif_header_bytes("<LMSDataContainerHeader><oops>"))

    with pytest.raises(LifHeaderError) as excinfo:
        read_lif_header(path)

    assert "xml" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    ("name", "blob"),
    [
        ("empty", b""),
        ("shorter_than_prefix", b"\x70\x00\x00\x00\x10\x00"),
        ("prefix_only", struct.pack("<ii", 0x70, 5) + b"\x2a" + struct.pack("<i", 0)),
    ],
)
def test_rejects_files_too_short_to_hold_a_header(tmp_path, name, blob):
    path = _write(tmp_path, blob, name=f"{name}.lif")

    with pytest.raises(LifHeaderError):
        read_lif_header(path)


def test_accepts_a_str_path(tmp_path, lif_header_bytes):
    path = _write(tmp_path, lif_header_bytes(MINIMAL))

    assert read_lif_header(str(path)).tag == "LMSDataContainerHeader"
