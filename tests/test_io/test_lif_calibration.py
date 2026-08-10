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


def _acquisition_block(detector: str = "HyD X 3", index: int = 0) -> str:
    return (
        "<RawData><Sequence><SequenceItem><Detectors><Detectors>"
        f"<Detector>{index}</Detector>"
        "<LaserPulseFrequency>78020000</LaserPulseFrequency>"
        "<PhasorAutomaticReference>true</PhasorAutomaticReference>"
        f"<PhasorPhase>{ACQ_PHASE_DEG}</PhasorPhase>"
        f"<PhasorAmplitude>{ACQ_AMPLITUDE}</PhasorAmplitude>"
        f"<Name>{detector}</Name>"
        "</Detectors></Detectors></SequenceItem></Sequence></RawData>"
    )


def _phasor_block(
    *,
    channel: int = 0,
    phase: str = REF_PHASE_DEG,
    amplitude: str = REF_AMPLITUDE,
    period: str = REF_PERIOD,
) -> str:
    return (
        "<PhasorData><Channels>"
        f"<Channel>{channel}</Channel>"
        f"<Period>{period}</Period>"
        "<AutomaticReference>true</AutomaticReference>"
        f"<AutomaticReferencePhase>{phase}</AutomaticReferencePhase>"
        f"<AutomaticReferenceAmplitude>{amplitude}</AutomaticReferenceAmplitude>"
        "<Filter>Wavelet</Filter><FilterSize>3</FilterSize>"
        "</Channels></PhasorData>"
    )


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


def test_detector_name_resolves_by_channel_index(lif_file):
    xml = _header(
        _region("Region_1", _acquisition_block(detector="HyD X 3") + _phasor_block())
    )

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.detector_name == "HyD X 3"
    assert record.channel_index == 0


def test_detector_name_falls_back_to_the_channel_index(lif_file):
    xml = _header(_region("Region_1", _phasor_block(channel=2)))

    (record,) = read_lif_calibration(lif_file(xml))

    assert record.detector_name == "ch2"


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


def test_record_label_names_region_and_detector(lif_file):
    xml = _header(_region("Region_1", _acquisition_block() + _phasor_block()))

    (record,) = read_lif_calibration(lif_file(xml))

    assert isinstance(record, LifCalibrationRecord)
    assert record.label == "Region_1 · HyD X 3"


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


def test_auto_match_leaves_two_records_unbound():
    records = [_record(channel_index=0), _record(channel_index=1, detector="HyD X 1")]
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
