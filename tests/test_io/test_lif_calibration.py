"""Tests for phasor calibration extraction from a Leica ``.lif`` header.

The headers here are synthesised to the shape documented in
``docs/reference/lif-xml-header.md``. The critical property under test is that
extraction reads the per-image record (``AutomaticReferencePhase``) and never
the acquisition-side one (``PhasorPhase``), which sits earlier in the same
element and holds a different number.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from percell4.domain.errors import LifCalibrationError
from percell4.domain.io.lif_calibration import (
    LifCalibrationRecord,
    auto_match,
    read_lif_calibration,
    resolve_lif_calibration,
)

# Values from the reference file. The acquisition record is a decoy: reading it
# instead would yield phase -0.348959893.
REF_PHASE_DEG = "25.45322087"
REF_AMPLITUDE = "1.000587231"
REF_PERIOD = "1.281722635e-08"
ACQ_PHASE_DEG = "19.99392909"
ACQ_AMPLITUDE = "1.000513077"

EXPECTED_PHASE = -0.444242509
EXPECTED_MODULATION = 0.999413114
EXPECTED_FREQ_MHZ = 78.020000
ACQUISITION_PHASE = -0.348959893


def _acquisition_block(*detectors: str) -> str:
    """Ordered detector records.

    Every one reports ``<Detector>0</Detector>``, matching real files — which
    is why extraction pairs blocks to detectors by position, not by that value.
    """
    names = detectors or ("HyD X 3",)
    records = "".join(
        "<Detectors>"
        "<Detector>0</Detector>"
        "<LaserPulseFrequency>78020000</LaserPulseFrequency>"
        "<PhasorAutomaticReference>true</PhasorAutomaticReference>"
        f"<PhasorPhase>{ACQ_PHASE_DEG}</PhasorPhase>"
        f"<PhasorAmplitude>{ACQ_AMPLITUDE}</PhasorAmplitude>"
        f"<Name>{name}</Name>"
        "</Detectors>"
        for name in names
    )
    return (
        "<RawData><Sequence><SequenceItem><Detectors>"
        f"{records}"
        "</Detectors></SequenceItem></Sequence></RawData>"
    )


def _channels_block(
    *,
    channel: int = 0,
    phase: str = REF_PHASE_DEG,
    amplitude: str = REF_AMPLITUDE,
    period: str = REF_PERIOD,
) -> str:
    """One calibration block.

    ``channel`` is written verbatim so tests can prove it is ignored: LAS X
    reports 0 in every block regardless of which channel it describes.
    """
    return (
        "<Channels>"
        f"<Channel>{channel}</Channel>"
        f"<Period>{period}</Period>"
        "<AutomaticReference>true</AutomaticReference>"
        f"<AutomaticReferencePhase>{phase}</AutomaticReferencePhase>"
        f"<AutomaticReferenceAmplitude>{amplitude}</AutomaticReferenceAmplitude>"
        "<Filter>Wavelet</Filter><FilterSize>3</FilterSize>"
        "</Channels>"
    )


def _phasor_data(*blocks: str) -> str:
    """Several calibration blocks in one ``PhasorData``, as a real region has."""
    return "<PhasorData>" + "".join(blocks) + "</PhasorData>"


def _phasor_block(**kwargs) -> str:
    return _phasor_data(_channels_block(**kwargs))


# A Channels element with no AutomaticReferencePhase. The reference file has 25
# of these; extraction must not match them.
DECOY_CHANNELS = "<Channels><Channel>0</Channel><IntensityFactor>1</IntensityFactor></Channels>"


def _region(name: str, inner: str, *, harmonic: str = "1") -> str:
    return (
        f'<Element Name="{name}"><Children>'
        f'<Element Name="FLIM Compressed"><Data><Image><Attachment>'
        f"<Harmonic>{harmonic}</Harmonic>{inner}"
        "</Attachment></Image></Data></Element>"
        "</Children></Element>"
    )


def _header(regions: str, root: str = "FLIM_calibratoin_test") -> str:
    return (
        '<LMSDataContainerHeader Version="2">'
        f'<Element Name="{root}"><Children>{regions}</Children></Element>'
        "</LMSDataContainerHeader>"
    )


@pytest.fixture
def lif_file(tmp_path, lif_header_bytes):
    def build(xml: str, name: str = "sample.lif") -> Path:
        path = tmp_path / name
        path.write_bytes(lif_header_bytes(xml))
        return path

    return build


def test_reads_the_per_image_record(lif_file):
    xml = _header(_region("Region_1", _acquisition_block() + _phasor_block()))

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.phase == pytest.approx(EXPECTED_PHASE, abs=1e-9)
    assert record.modulation == pytest.approx(EXPECTED_MODULATION, abs=1e-9)
    assert record.frequency_mhz == pytest.approx(EXPECTED_FREQ_MHZ, abs=1e-6)


def test_never_reads_the_acquisition_record(lif_file):
    """The decoy sits earlier in the same element and must not win."""
    xml = _header(_region("Region_1", _acquisition_block() + _phasor_block()))

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.phase != pytest.approx(ACQUISITION_PHASE, abs=1e-6)
    assert record.modulation != pytest.approx(1 / float(ACQ_AMPLITUDE), abs=1e-9)


def test_acquisition_record_alone_is_not_calibration(lif_file):
    xml = _header(_region("Region_1", _acquisition_block()))

    with pytest.raises(LifCalibrationError) as excinfo:
        read_lif_calibration(lif_file(xml))

    assert "no phasor calibration" in str(excinfo.value).lower()


def test_identity_values_convert_to_identity(lif_file):
    xml = _header(_region("Region_1", _phasor_block(phase="0", amplitude="1.0")))

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.phase == 0.0
    assert record.modulation == 1.0


def test_phase_is_negated_and_modulation_inverted(lif_file):
    xml = _header(_region("Region_1", _phasor_block(phase="30.0", amplitude="2.0")))

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.phase == pytest.approx(-math.radians(30.0))
    assert record.modulation == pytest.approx(0.5)


def test_dataset_stem_joins_root_and_region(lif_file):
    xml = _header(_region("Region_1", _phasor_block()), root="Experiment A")

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.dataset_stem == "Experiment A_Region_1"
    assert record.region_name == "Region_1"


def test_multiple_regions_yield_distinct_records(lif_file):
    xml = _header(
        _region("Region_1", _phasor_block())
        + _region("Region_2", _phasor_block(phase="10.0"))
    )

    records = read_lif_calibration(lif_file(xml))

    assert [r.dataset_stem for r in records] == [
        "FLIM_calibratoin_test_Region_1",
        "FLIM_calibratoin_test_Region_2",
    ]
    assert records[1].phase == pytest.approx(-math.radians(10.0))


def test_decoy_channels_blocks_are_not_matched(lif_file):
    xml = _header(
        _region("Region_1", DECOY_CHANNELS * 25 + _phasor_block() + DECOY_CHANNELS)
    )

    records = read_lif_calibration(lif_file(xml))

    assert len(records) == 1


def test_detector_name_resolves_positionally(lif_file):
    xml = _header(_region("Region_1", _acquisition_block("HyD X 3") + _phasor_block()))

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.detector_name == "HyD X 3"
    assert record.channel_index == 0


def test_each_block_takes_the_detector_at_its_own_position(lif_file):
    """Two channels, two detectors, and a <Channel> value that names neither."""
    xml = _header(
        _region(
            "Region_1",
            _acquisition_block("HyD X 3", "HyD X 1")
            + _phasor_data(
                _channels_block(channel=0, phase="34.83"),
                _channels_block(channel=0, phase="32.70"),
            ),
        )
    )

    first, second = read_lif_calibration(lif_file(xml))

    assert (first.channel_index, first.detector_name) == (0, "HyD X 3")
    assert (second.channel_index, second.detector_name) == (1, "HyD X 1")


def test_detector_name_falls_back_to_the_position(lif_file):
    xml = _header(_region("Region_1", _phasor_block(channel=2)))

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.detector_name == "detector 0"
    assert record.channel_index == 0


def test_harmonic_defaults_to_one_when_absent(lif_file):
    xml = _header(
        f'<Element Name="Region_1"><Children>'
        f'<Element Name="FLIM Compressed"><Data><Image><Attachment>'
        f"{_phasor_block()}</Attachment></Image></Data></Element>"
        "</Children></Element>"
    )

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.harmonic == 1


def test_zero_period_is_an_error_not_a_division_by_zero(lif_file):
    xml = _header(_region("Region_1", _phasor_block(period="0")))

    with pytest.raises(LifCalibrationError) as excinfo:
        read_lif_calibration(lif_file(xml))

    assert "period" in str(excinfo.value).lower()


def test_non_numeric_fields_are_reported_together(lif_file):
    xml = _header(
        _region("Region_1", _phasor_block(phase="abc", amplitude="xyz"))
        + _region("Region_2", _phasor_block(period="nope"))
    )

    with pytest.raises(LifCalibrationError) as excinfo:
        read_lif_calibration(lif_file(xml))

    assert len(excinfo.value.errors) >= 2
    assert any("Region_1" in e for e in excinfo.value.errors)
    assert any("Region_2" in e for e in excinfo.value.errors)


def test_zero_amplitude_is_an_error(lif_file):
    xml = _header(_region("Region_1", _phasor_block(amplitude="0")))

    with pytest.raises(LifCalibrationError) as excinfo:
        read_lif_calibration(lif_file(xml))

    assert "amplitude" in str(excinfo.value).lower()


def test_record_label_names_region_channel_and_detector(lif_file):
    xml = _header(_region("Region_1", _acquisition_block() + _phasor_block()))

    (record,) = read_lif_calibration(lif_file(xml))

    assert isinstance(record, LifCalibrationRecord)
    assert record.label == "Region_1 · ch0 HyD X 3"


def test_labels_distinguish_every_record_in_a_region(lif_file):
    """A multi-channel region's records must all be separately pickable."""
    xml = _header(
        _region(
            "Region_1",
            _acquisition_block("HyD X 3", "HyD X 1")
            + _phasor_data(
                _channels_block(phase="34.8309987", amplitude="1.00029786"),
                _channels_block(phase="32.70105596", amplitude="1.000585206"),
            ),
        )
    )

    records = read_lif_calibration(lif_file(xml))

    assert len(records) == 2
    assert [r.label for r in records] == [
        "Region_1 · ch0 HyD X 3",
        "Region_1 · ch1 HyD X 1",
    ]


def test_dataset_stem_drops_a_lif_suffix_on_the_root_element(lif_file):
    """LAS X names the root Element for the file, extension included.

    No exported .h5 stem carries it, so leaving it in makes every stem
    comparison fail and auto-match silently bind nothing.
    """
    xml = _header(_region("UT Hpep3 5x8", _phasor_block()), root="Rep 3 - Dcp2.lif")

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.dataset_stem == "Rep 3 - Dcp2_UT Hpep3 5x8"


def test_dataset_stem_suffix_strip_is_case_insensitive(lif_file):
    xml = _header(_region("Region_1", _phasor_block()), root="Experiment.LIF")

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.dataset_stem == "Experiment_Region_1"


def test_a_lif_inside_the_name_is_not_stripped(lif_file):
    """Only a trailing suffix is an extension."""
    xml = _header(_region("Region_1", _phasor_block()), root="calif.brate")

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.dataset_stem == "calif.brate_Region_1"


REFERENCE_LIF = Path(
    "/Volumes/NX-74205/2026-08-10_export/FLIM_calibratoin_test.lif"
)


@pytest.mark.skipif(
    not REFERENCE_LIF.exists(), reason="reference .lif volume is not mounted"
)
def test_reference_lif_yields_the_documented_values():
    """Guards the synthesised headers against drift from the real format."""
    (record,) = read_lif_calibration(REFERENCE_LIF)

    assert record.dataset_stem == "FLIM_calibratoin_test_Region_1"
    assert record.detector_name == "HyD X 3"
    assert record.phase == pytest.approx(EXPECTED_PHASE, abs=1e-9)
    assert record.modulation == pytest.approx(EXPECTED_MODULATION, abs=1e-9)
    assert record.frequency_mhz == pytest.approx(EXPECTED_FREQ_MHZ, abs=1e-6)


# ── Resolution against a dataset selection ────────────────────


def _record(
    stem: str = "FLIM_calibratoin_test_Region_1",
    *,
    region: str = "Region_1",
    detector: str = "HyD X 3",
    channel_index: int = 0,
    frequency_mhz: float = 78.02,
    phase: float = EXPECTED_PHASE,
    modulation: float = EXPECTED_MODULATION,
) -> LifCalibrationRecord:
    return LifCalibrationRecord(
        dataset_stem=stem,
        region_name=region,
        element_path=f"root/{region}",
        channel_index=channel_index,
        detector_name=detector,
        frequency_mhz=frequency_mhz,
        phase=phase,
        modulation=modulation,
        harmonic=1,
    )


def test_auto_match_binds_the_one_to_one_case():
    records = [_record()]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1"]}

    bindings = auto_match(records, selection)

    assert bindings == {("FLIM_calibratoin_test_Region_1", "G3BP1"): 0}


def test_auto_match_leaves_two_channels_unbound():
    records = [_record()]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG"]}

    assert auto_match(records, selection) == {}


def test_auto_match_binds_channels_to_records_by_position():
    """Two records, two channels: the n-th channel takes the n-th record."""
    records = [_record(channel_index=0), _record(channel_index=1, detector="HyD X 1")]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG"]}

    assert auto_match(records, selection) == {
        ("FLIM_calibratoin_test_Region_1", "G3BP1"): 0,
        ("FLIM_calibratoin_test_Region_1", "mNG"): 1,
    }


def test_auto_match_refuses_when_counts_disagree():
    records = [_record(channel_index=0), _record(channel_index=1, detector="HyD X 1")]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG", "Halo"]}

    assert auto_match(records, selection) == {}


def test_auto_match_refuses_when_positions_are_not_consecutive():
    """A gap means a block was skipped; position can no longer be trusted."""
    records = [_record(channel_index=0), _record(channel_index=2, detector="HyD X 1")]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG"]}

    assert auto_match(records, selection) == {}


def test_auto_match_ignores_records_for_unselected_datasets():
    records = [_record(stem="Other_Region_9", region="Region_9")]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1"]}

    assert auto_match(records, selection) == {}


def test_resolution_produces_a_batch_calibration():
    records = [_record()]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1"]}

    cal, unbound = resolve_lif_calibration(
        records, selection, auto_match(records, selection)
    )

    assert unbound == ()
    entry = cal.get("FLIM_calibratoin_test_Region_1", "G3BP1")
    assert entry.frequency_mhz == pytest.approx(78.02)
    assert entry.phase == pytest.approx(EXPECTED_PHASE)
    assert entry.modulation == pytest.approx(EXPECTED_MODULATION)


def test_unbound_channels_are_reported_by_dataset_and_channel():
    records = [_record()]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG"]}

    _, unbound = resolve_lif_calibration(records, selection, {})

    assert len(unbound) == 2
    assert any("G3BP1" in m for m in unbound)
    assert any("mNG" in m for m in unbound)
    assert all("FLIM_calibratoin_test_Region_1" in m for m in unbound)


def test_a_dataset_with_no_matching_record_reports_every_channel():
    records = [_record(stem="Other_Region_9", region="Region_9")]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG"]}

    _, unbound = resolve_lif_calibration(records, selection, {})

    assert len(unbound) == 2


def test_an_unselected_record_is_not_an_error():
    records = [_record(), _record(stem="Other_Region_9", region="Region_9")]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1"]}

    cal, unbound = resolve_lif_calibration(
        records, selection, auto_match(records, selection)
    )

    assert unbound == ()
    assert cal.datasets() == ("FLIM_calibratoin_test_Region_1",)


def test_explicit_binding_resolves_what_auto_match_left_alone():
    records = [_record()]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG"]}
    bindings = {
        ("FLIM_calibratoin_test_Region_1", "G3BP1"): 0,
        ("FLIM_calibratoin_test_Region_1", "mNG"): 0,
    }

    cal, unbound = resolve_lif_calibration(records, selection, bindings)

    assert unbound == ()
    assert set(cal.channels("FLIM_calibratoin_test_Region_1")) == {"G3BP1", "mNG"}


def test_explicit_binding_overrides_auto_match_for_that_cell_only():
    records = [
        _record(channel_index=0),
        _record(channel_index=1, detector="HyD X 1", phase=-0.1),
    ]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1"]}
    bindings = {("FLIM_calibratoin_test_Region_1", "G3BP1"): 1}

    cal, _ = resolve_lif_calibration(records, selection, bindings)

    assert cal.get("FLIM_calibratoin_test_Region_1", "G3BP1").phase == pytest.approx(-0.1)


def test_frequency_may_differ_across_datasets():
    records = [
        _record(frequency_mhz=78.02),
        _record(stem="Other_Region_9", region="Region_9", frequency_mhz=40.0),
    ]
    selection = {
        "FLIM_calibratoin_test_Region_1": ["G3BP1"],
        "Other_Region_9": ["G3BP1"],
    }

    _, unbound = resolve_lif_calibration(
        records, selection, auto_match(records, selection)
    )

    assert unbound == ()


def test_frequency_disagreement_within_one_dataset_is_reported():
    records = [
        _record(channel_index=0, frequency_mhz=78.02),
        _record(channel_index=1, detector="HyD X 1", frequency_mhz=40.0),
    ]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1", "mNG"]}
    bindings = {
        ("FLIM_calibratoin_test_Region_1", "G3BP1"): 0,
        ("FLIM_calibratoin_test_Region_1", "mNG"): 1,
    }

    _, messages = resolve_lif_calibration(records, selection, bindings)

    assert any("frequency_mhz" in m for m in messages)


def test_an_out_of_range_binding_index_is_reported_not_raised():
    records = [_record()]
    selection = {"FLIM_calibratoin_test_Region_1": ["G3BP1"]}

    _, unbound = resolve_lif_calibration(
        records, selection, {("FLIM_calibratoin_test_Region_1", "G3BP1"): 7}
    )

    assert len(unbound) == 1


MULTI_REGION_LIF = Path(
    "/Volumes/NX-74205/2026-08-10_export_2/Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2.lif"
)


@pytest.mark.skipif(
    not MULTI_REGION_LIF.exists(), reason="multi-region .lif volume is not mounted"
)
def test_multi_region_lif_records_are_all_distinguishable():
    """Two regions, two records each, every one identical but for its values.

    This file is why the label carries phase and modulation and why the root
    element's ``.lif`` suffix is stripped: with neither, the picker showed two
    identical entries per region and no stem could ever match an .h5.
    """
    records = read_lif_calibration(MULTI_REGION_LIF)

    assert len(records) == 4
    assert len({r.label for r in records}) == 4
    # Two channels per region, each named by its own detector — not the same
    # detector twice, which is what a <Channel>-keyed lookup produced.
    assert [r.label for r in records] == [
        "UT Hpep3 5x8 · ch0 HyD X 3",
        "UT Hpep3 5x8 · ch1 HyD X 1",
        "As Hpep3 5x8 · ch0 HyD X 3",
        "As Hpep3 5x8 · ch1 HyD X 1",
    ]
    assert {r.dataset_stem for r in records} == {
        "Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2_UT Hpep3 5x8",
        "Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2_As Hpep3 5x8",
    }
    assert not any(".lif" in r.dataset_stem for r in records)


@pytest.mark.skipif(
    not MULTI_REGION_LIF.exists(), reason="multi-region .lif volume is not mounted"
)
def test_multi_region_lif_auto_matches_against_real_h5_stems():
    """Auto-match binds nothing here — two records per dataset is ambiguous —
    but the stems must still match, or the rows could never be bound at all."""
    records = read_lif_calibration(MULTI_REGION_LIF)
    selection = {
        "Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2_UT Hpep3 5x8": ["Halo", "mNG"],
        "Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2_As Hpep3 5x8": ["Halo", "mNG"],
    }

    for stem in selection:
        assert [r for r in records if r.dataset_stem == stem], f"no record for {stem}"

    # Two records, two channels, positions 0 and 1: every row binds.
    bindings = auto_match(records, selection)
    assert len(bindings) == 4
    cal, unresolved = resolve_lif_calibration(records, selection, bindings)
    assert unresolved == ()
    ut = "Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2_UT Hpep3 5x8"
    assert cal.get(ut, "Halo").phase != cal.get(ut, "mNG").phase
