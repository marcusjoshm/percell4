"""File scanner for discovering and parsing microscopy image files.

Walks a directory, identifies TIFF files, and extracts tokens (channel,
timepoint, z-slice, tile) from filenames using regex patterns.
"""

from __future__ import annotations

import re
from pathlib import Path

from percell4.domain.io.models import DiscoveredFile, ScanResult, TokenConfig

_TIFF_EXTENSIONS = {".tif", ".tiff"}


class FileScanner:
    """Discovers image files and parses filename tokens."""

    def __init__(self, token_config: TokenConfig | None = None) -> None:
        self.config = token_config or TokenConfig()

    def scan(
        self,
        path: str | Path | None = None,
        files: list[str | Path] | None = None,
    ) -> ScanResult:
        """Scan a directory or explicit file list for TIFF images.

        Provide either ``path`` (directory to walk) or ``files`` (explicit
        list of file paths), but not both.
        """
        if path is not None and files is not None:
            raise ValueError("Provide either path or files, not both")
        if path is None and files is None:
            raise ValueError("Must provide either path or files")

        if files is not None:
            tiff_paths = [Path(f) for f in files if Path(f).suffix.lower() in _TIFF_EXTENSIONS]
        else:
            tiff_paths = sorted(
                p for p in Path(path).rglob("*")
                if p.suffix.lower() in _TIFF_EXTENSIONS and not p.is_symlink()
            )

        result = ScanResult()

        for fpath in tiff_paths:
            tokens = self._parse_tokens(fpath)
            discovered = DiscoveredFile(path=fpath, tokens=tokens)
            result.files.append(discovered)

            if "channel" in tokens:
                result.channels.add(tokens["channel"])
            if "timepoint" in tokens:
                result.timepoints.add(tokens["timepoint"])
            if "z_slice" in tokens:
                result.z_slices.add(tokens["z_slice"])
            if "tile" in tokens:
                result.tiles.add(tokens["tile"])

        return result

    def _parse_tokens(self, path: Path) -> dict[str, str]:
        """Extract tokens from a filename using the configured regex patterns."""
        stem = path.stem
        tokens: dict[str, str] = {}

        for field_name in ("channel", "timepoint", "z_slice", "tile"):
            pattern = getattr(self.config, field_name)
            if pattern is None:
                continue
            match = re.search(pattern, stem)
            if match:
                tokens[field_name] = match.group(1)

        return tokens
