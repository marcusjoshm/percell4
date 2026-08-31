#!/usr/bin/env python3
"""Extract the XML metadata header from a Leica .lif file.

A .lif file stores its complete experiment metadata as an XML block at the
very start of the file, ahead of all image/decay data. This script reads only
that header (typically well under 1 MB), so it works quickly even on
multi-gigabyte files sitting on network or external drives -- no need to copy
the .lif anywhere.

LIF header layout:
    offset 0   int32 (LE)   magic value 0x70
    offset 4   int32 (LE)   header chunk length
    offset 8   uint8        test byte 0x2A
    offset 9   int32 (LE)   number of UTF-16 characters in the XML
    offset 13  bytes        the XML, encoded as UTF-16-LE

Usage:
    python extract_lif_metadata.py "experiment.lif"
    python extract_lif_metadata.py "experiment.lif" -o metadata.xml
    python extract_lif_metadata.py "experiment.lif" --pretty
"""

import argparse
import struct
import sys
import xml.dom.minidom
from pathlib import Path

LIF_MAGIC = 0x70
LIF_TEST_BYTE = 0x2A


def extract_lif_xml(path: Path) -> str:
    """Read only the XML metadata header from a .lif file."""
    with open(path, "rb") as f:
        header = f.read(13)
        if len(header) < 13:
            raise ValueError(f"File too small to be a LIF file: {path}")
        magic, _chunk_len, test, nchars = struct.unpack("<iiBi", header)
        if magic != LIF_MAGIC or test != LIF_TEST_BYTE:
            raise ValueError(f"Not a valid LIF file (bad header): {path}")
        xml_bytes = f.read(nchars * 2)
        if len(xml_bytes) < nchars * 2:
            raise ValueError(f"Truncated LIF header in: {path}")
    return xml_bytes.decode("utf-16-le")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract XML metadata from a Leica .lif file without "
        "reading the image data."
    )
    parser.add_argument("lif_file", type=Path, help="Path to the .lif file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .xml path (default: same name as the .lif file, "
        "next to it)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print (indent) the XML for human reading",
    )
    args = parser.parse_args()

    if not args.lif_file.is_file():
        print(f"Error: file not found: {args.lif_file}", file=sys.stderr)
        return 1

    try:
        xml_text = extract_lif_xml(args.lif_file)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.pretty:
        xml_text = xml.dom.minidom.parseString(xml_text).toprettyxml(indent="  ")

    out_path = args.output or args.lif_file.with_suffix(".xml")
    out_path.write_text(xml_text, encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
