"""Snapshot tests for registered-analysis presets.

Each preset declared in an :class:`Analysis` subclass is mirrored to
``tests/fixtures/preset_snapshots/<analysis_name>.json``. The test
compares the in-code preset dict to the on-disk snapshot key-by-key
and fails loudly when they drift, so a developer changing a preset
value in code without updating the snapshot has to confront the diff
in PR review.

Updating a preset is a deliberate two-step:

1. Edit the value in code (e.g. in
   ``src/percell4/application/analysis/modules/per_particle_donut.py``).
2. Update the matching JSON snapshot.

Per the plan (Key Technical Decisions → "Presets are in-code constants
with a snapshot test"), this is the immutability mechanism. No
import-time hashing, no committed ``preset_hashes.json`` in
``src/``.
"""

from __future__ import annotations

import json
from pathlib import Path

from percell4.application.analysis.modules.per_particle_donut import (
    PerParticleDonut,
)

SNAPSHOT_DIR = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "preset_snapshots"
)


def test_per_particle_donut_preset_matches_snapshot() -> None:
    """``m7g-cap-v1`` in code must match the committed snapshot."""
    snapshot_path = SNAPSHOT_DIR / "per_particle_donut.json"
    snapshot = json.loads(snapshot_path.read_text())

    assert "m7g-cap-v1" in PerParticleDonut.presets, (
        "PerParticleDonut.presets is missing the 'm7g-cap-v1' key. "
        "Either restore it in code or update the snapshot file."
    )
    assert "m7g-cap-v1" in snapshot, (
        "preset_snapshots/per_particle_donut.json is missing the "
        "'m7g-cap-v1' key."
    )

    in_code = PerParticleDonut.presets["m7g-cap-v1"]
    on_disk = snapshot["m7g-cap-v1"]

    assert in_code == on_disk, (
        f"Preset 'm7g-cap-v1' drift between in-code and snapshot.\n"
        f"  in-code: {in_code}\n"
        f"  snapshot: {on_disk}\n"
        f"  Update both files together if you intend to change the preset."
    )


def test_per_particle_donut_all_presets_have_snapshots() -> None:
    """Every preset declared in code must have a snapshot entry.

    Catches the case where a developer adds a new preset to the class
    but forgets to mirror it into the JSON file (or vice versa).
    """
    snapshot_path = SNAPSHOT_DIR / "per_particle_donut.json"
    snapshot = json.loads(snapshot_path.read_text())

    in_code_names = set(PerParticleDonut.presets)
    on_disk_names = set(snapshot)
    assert in_code_names == on_disk_names, (
        f"Preset name set mismatch.\n"
        f"  in-code only: {sorted(in_code_names - on_disk_names)}\n"
        f"  snapshot only: {sorted(on_disk_names - in_code_names)}"
    )


# ── WholeFieldIntensity (U7) ──────────────────────────────────────


def test_whole_field_intensity_presets_match_snapshot() -> None:
    from percell4.application.analysis.modules.whole_field_intensity import (
        WholeFieldIntensity,
    )

    snapshot = json.loads(
        (SNAPSHOT_DIR / "whole_field_intensity.json").read_text()
    )
    assert set(WholeFieldIntensity.presets) == set(snapshot), (
        "WholeFieldIntensity preset name set drifted from the snapshot."
    )
    for name, in_code in WholeFieldIntensity.presets.items():
        assert in_code == snapshot[name], (
            f"Preset {name!r} drift between in-code and snapshot.\n"
            f"  in-code: {in_code}\n  snapshot: {snapshot[name]}"
        )
