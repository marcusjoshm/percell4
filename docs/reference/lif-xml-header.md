# Leica `.lif` XML header — metadata reference

Every `.lif` opens with a single UTF-16LE XML header describing the whole file:
the element tree, acquisition settings, hardware state, and — the reason this
document exists — the FLIM phasor calibration that LAS X computed and stored.

The full pretty-printed header is checked in beside this file as
[`lif-xml-header.xml`](lif-xml-header.xml) (1,986 lines). This
document is the guide to it: how to read the header out, where the calibration
lives, and an index of every tag in the tree.

**Sample file.** `FLIM_calibratoin_test.lif` — one region, one FLIM detector
(`HyD X 3`), harmonic 1, phasor auto-calibrated in LAS X. Values quoted below
are that file's; structure is general.

---

## Container layout

The header is the first block of the file. Fields are little-endian:

| Offset | Type | Value here | Meaning |
|---|---|---|---|
| 0 | `int32` | `0x70` | block marker — must be `0x70` |
| 4 | `int32` | `227,199` | bytes remaining in the block — the five below plus the XML |
| 8 | `uint8` | `0x2a` | separator — must be `0x2a` |
| 9 | `int32` | `113,597` | XML length in UTF-16 **characters**, not bytes |
| 13 | `bytes` | — | the XML, UTF-16LE, `227,194` bytes |

The offset-4 count is the whole remainder of the block, so
`xml_bytes = 1 + 4 + nchars * 2` (227,199 = 5 + 227,194). Sizing a read
from it directly overshoots the XML by five bytes; use `nchars * 2`.

Object memory blocks follow the header and carry the pixel data. Nothing in
this document needs them — the calibration is entirely in the header.

```python
import struct, pathlib, xml.etree.ElementTree as ET

raw = pathlib.Path("sample.lif").read_bytes()
testvalue, xml_bytes = struct.unpack("<ii", raw[:8])
assert testvalue == 0x70 and raw[8] == 0x2A
nchars = struct.unpack("<i", raw[9:13])[0]
root = ET.fromstring(raw[13:13 + nchars * 2].decode("utf-16-le"))
```

Root is `<LMSDataContainerHeader Version="2">`. The tree holds
1,509 nodes across 292 distinct tags, 19 levels deep.

---

## Element tree

`Element` nodes nest through `Children`, and their `Name` attributes reconstruct
the LAS X project tree. The exported `.tif` stem — `FLIM_calibratoin_test_Region_1`
— is the root name joined to the region name with `_`.

- `FLIM_calibratoin_test`
  - `Region_1` — image 512x512x6
    - `BleachPointROISet`
    - `BleachPointROISet`
    - `FLIM Compressed` — **acquisition record**, reference decay, **phasor calibration**
      - `Intensity` — image 512x512x6
      - `Fast Flim` — image 512x512x6
      - `Standard Deviation` — image 512x512x6
      - `Phasor Real` — image 512x512x6
      - `Phasor Imaginary` — image 512x512x6
      - `Phasor Intensity` — image 512x512x6
      - `Phasor Mask` — image 512x512x6
      - `Phasor Plot` — image 512x300
      - `Pattern Matching Scatter Plot Channel 1` — image 100x100

---

## Phasor calibration: two records, only one of them correct

The header carries **two** phasor calibration records under the same
`FLIM Compressed` element. They hold different numbers and are not derived from
each other. Reading the wrong one is the main hazard in this format.

### Per-image record — the one LAS X displays and applies

Path: `…/Element[@Name='FLIM Compressed']//Channels`, adjacent to the phasor
filter settings. This is the **Images** table row in the LAS X *Phasor
Calibration* dialog.

```xml
<Channels>
  <Channel>0</Channel>
  <Period>1.281722635e-08</Period>
  <AutomaticReference>true</AutomaticReference>
  <AutomaticReferencePhase>25.45322087</AutomaticReferencePhase>
  <AutomaticReferenceAmplitude>1.000587231</AutomaticReferenceAmplitude>
  <Filter>Wavelet</Filter>
  <FilterSize>3</FilterSize>
  <FilterStrength>100</FilterStrength>
  <FilterNoiseLevel>1</FilterNoiseLevel>
  <IntensityThreshold>1</IntensityThreshold>
  <UpperIntensityThreshold>1000</UpperIntensityThreshold>
  <UpperIntensityThresholdActive>false</UpperIntensityThresholdActive>
  <!-- PhasorPlotShapes: overlay geometry, omitted -->
</Channels>
```

### Acquisition record — a decoy

Path: `…/Element[@Name='FLIM Compressed']//RawData/Sequence/SequenceItem/Detectors/Detectors`.
This is the dialog's **Acquisition** panel, whose value cells render empty.
Note it appears *earlier* in the document than the per-image record, so a naive
search for `PhasorPhase` finds this one first.

```xml
<Detectors>
  <Detector>0</Detector>
  <DataType>PulseVersion2</DataType>
  <LaserPulseFrequency>78020000</LaserPulseFrequency>
  <DeadTime>1.3e-09</DeadTime>
  <IntensityLimit>1</IntensityLimit>
  <PhasorAutomaticReference>true</PhasorAutomaticReference>
  <PhasorPhase>19.99392909</PhasorPhase>
  <PhasorAmplitude>1.000513077</PhasorAmplitude>
  <Color>4294967295</Color>
  <ColorPalette />
  <Name>HyD X 3</Name>
  <Identifier>3</Identifier>
  <StedMode>Node</StedMode>
  <Emission>5.49e-07</Emission>
  <Excitation>5.04e-07</Excitation>
  <Depletion>0</Depletion>
  <SaturationFactor>0</SaturationFactor>
  <_AxialRatio>0</_AxialRatio>
</Detectors>
```

### Why they differ

| | acquisition | per-image | change |
|---|---|---|---|
| phase | 19.99392909° | 25.45322087° | +5.459° |
| amplitude | 1.000513077 | 1.000587231 | +0.0074 % |

A different assumed reference lifetime moves phase and modulation together — to
shift phase by +5.459° it would have to drag modulation by −14.9 %. Modulation
does not move. Phase-only displacement is the signature of a time-origin shift:
5.459° at the 12.817 ns period is 194.4 ps, or 2.0044 of the 96.97 ps decay time
bins. The two records describe the same calibration against different time
origins, not two different calibrations.

### Conversion to PerCell4 units

`domain/io/calibration_csv.py` wants `frequency_mhz`, `phase` in radians, and
`modulation` where 1.0 means no scaling:

| CSV field | From the per-image record | This file |
|---|---|---|
| `frequency_mhz` | `1 / Period / 1e6` | 78.020000 |
| `phase` | `-radians(AutomaticReferencePhase)` | -0.444242509 |
| `modulation` | `1 / AutomaticReferenceAmplitude` | 0.999413114 |

LAS X displays these rounded — `25.45°` and `0.9994` — so a hand-transcribed
CSV loses about four decimal places against reading the header directly.

### Related tags in the same block

| Tag | This file | Notes |
|---|---|---|
| `Period` | `1.281722635e-08` | laser period in seconds; reciprocal of `LaserPulseFrequency` |
| `LaserPulseFrequency` | `78020000` | Hz, on the acquisition record |
| `AutomaticReference` | `true` | LAS X solved the calibration rather than taking a manual reference |
| `Filter` / `FilterSize` | `Wavelet` / `3` | the dialog's Filter checkbox and spinner |
| `Harmonic` | `1` | only harmonic 1 is stored here; PerCell4 supports 1–3 |
| `IntensityThreshold` | `1` | phasor plot inclusion floor |

`FlimData/LifetimeDecays` in the same element holds the decay histogram
(`RawDataFrequency`, `RawDataStartTime`, `RawDataTimeStep`). Neither stored
calibration is reproducible from it by a single-lifetime reference fit, so treat
the calibration as a value to read, not to recompute.

---

## Tag index

Every distinct tag, with how often it occurs, the shallowest parent path, and
the first text value found (or its attribute names when the tag carries no text).

| Tag | n | Parent path (tail) | Sample |
|---|---|---|---|
| `ATLConfocalBleachPointsSettings` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `ATLConfocalSettingDefinition` | 3 | `Data/Image/Attachment` | @ActiveCS_SubModeForRLD, @ActiveCS_SubModeFor… |
| `AdditionalZPosition` | 2 | `Attachment/ATLConfocalSettingDefinition/AdditionalZ…` | @SuperZMode, @SuperZModeName, @Valid, @ZMode |
| `AdditionalZPositionList` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `AdjustedLifetimeBackground` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | 0 |
| `AnalysisMode` | 1 | `Dataset/FlimData/Presentation` | Flim |
| `Aotf` | 6 | `Attachment/ATLConfocalSettingDefinition/AotfList` | @CanDoMultipleConstantPowerCurves, @CanDoPuls… |
| `AotfList` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `Arguments` | 1 | `FlimData/LifetimeDistributionHistograms/LifetimeDis…` | 2.999999901e-11 3.343384944e-11 3.72607431e-1… |
| `Attachment` | 27 | `Element/Data/Image` | @Application, @DataSourceType, @DataSourceTyp… |
| `Attribute` | 11 | `LMSDataContainerHeader/Element/Attributes` | ___Saving |
| `Attributes` | 11 | `LMSDataContainerHeader/Element` | — |
| `AutoRange3DLowerLevel` | 5 | `FlimData/FastFlimImage/Channels` | 0 |
| `AutoRange3DUpperLevel` | 5 | `FlimData/FastFlimImage/Channels` | 1 |
| `AutoRangeLowerLevel` | 1 | `FlimData/FastFlimImage/Channels` | 2.4283015e-09 |
| `AutoRangeUpperLevel` | 5 | `FlimData/FastFlimImage/Channels` | 1 |
| `Autofocus-config` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | @AFAnalyseType, @AFAnalyseTypeName, @AFCActiv… |
| `AutomaticReference` | 1 | `FlimData/PhasorData/Channels` | true |
| `AutomaticReferenceAmplitude` | 1 | `FlimData/PhasorData/Channels` | 1.000587231 |
| `AutomaticReferencePhase` | 1 | `FlimData/PhasorData/Channels` | 25.45322087 |
| `AutomaticTimeGates` | 1 | `FlimData/LifetimeDecays/LifetimeDecays` | — |
| `BasicModel` | 5 | `PatternMatching/FitData/FitModel` | ExponentialReconvolution |
| `BeamPosition` | 106 | `LUT_List/LUT/BeamRoute` | @BeamPosition, @BeamPositionLevel |
| `BeamRoute` | 69 | `ATLConfocalSettingDefinition/LUT_List/LUT` | @Version |
| `BiDirectional` | 1 | `SingleMoleculeDetection/Dataset/RawData` | false |
| `Binning` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | 1 |
| `BleachPoints` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `CalculatedInstrumentResponseFunctions` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `Channel` | 8 | `Dataset/RawData/Channels` | 0 |
| `ChannelDescription` | 10 | `Image/ImageDescription/Channels` | @BitInc, @BytesInc, @ChannelTag, @DataType |
| `ChannelLink` | 12 | `ATLConfocalSettingDefinition/LifetimeDetection/Chan…` | @Channel, @Detector |
| `ChannelLinks` | 3 | `Attachment/ATLConfocalSettingDefinition/LifetimeDet…` | — |
| `ChannelProperty` | 7 | `ImageDescription/Channels/ChannelDescription` | — |
| `ChannelScalingInfo` | 10 | `Data/Image/Attachment` | @Automatic, @BackgroundLutName, @BlackValue, … |
| `Channels` | 26 | `Data/Image/ImageDescription` | — |
| `Children` | 14 | `LMSDataContainerHeader/Element` | — |
| `ClockPeriod` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 9.696969697e-11 |
| `Color` | 2 | `RawData/Channels/Channel` | 4294967295 |
| `ColorPalette` | 2 | `RawData/Channels/Channel` | — |
| `ComponentIndex` | 4 | `Dataset/FlimData/IntensityHistograms` | 0 |
| `Coordinate` | 4 | `Data/Image/Attachment` | @Name, @Value |
| `CrossCorrelations` | 3 | `Attachment/ATLConfocalSettingDefinition/LifetimeDet…` | — |
| `Data` | 14 | `LMSDataContainerHeader/Element` | — |
| `DataType` | 1 | `SequenceItem/Detectors/Detectors` | PulseVersion2 |
| `Dataset` | 1 | `Element/Data/SingleMoleculeDetection` | — |
| `DeadTime` | 3 | `Dataset/FlimData/LifetimeDecaysSum` | 1.3e-09 |
| `DecoratorColors` | 2 | `ShapeList/Items/Item0` | — |
| `Depletion` | 1 | `SequenceItem/Detectors/Detectors` | 0 |
| `DetectionReferenceLine` | 3 | `ATLConfocalSettingDefinition/DetectorList/Detector` | @IsAutomaticAssigned, @LaserName, @LaserWavel… |
| `Detector` | 16 | `Attachment/ATLConfocalSettingDefinition/DetectorList` | 0 |
| `DetectorAssignment` | 3 | `ATLConfocalSettingDefinition/LifetimeDetection/Dete…` | @AutomaticAssigned, @Detector, @LaserName, @L… |
| `DetectorAssignments` | 3 | `Attachment/ATLConfocalSettingDefinition/LifetimeDet…` | — |
| `DetectorIndex` | 1 | `Channel/Detectors/DetectorReferences` | 0 |
| `DetectorList` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | @DetectorAutoSelection, @InExternalDetectionM… |
| `DetectorReferences` | 1 | `Channels/Channel/Detectors` | — |
| `Detectors` | 3 | `RawData/Channels/Channel` | — |
| `Dimension` | 11 | `Dataset/RawData/Dimensions` | — |
| `DimensionDescription` | 28 | `Image/ImageDescription/Dimensions` | @BitInc, @BytesInc, @DimID, @Length |
| `DimensionIdentifier` | 13 | `RawData/Dimensions/Dimension` | X |
| `Dimensions` | 13 | `Data/Image/ImageDescription` | — |
| `Duration` | 1 | `Data/SingleMoleculeDetection/Dataset` | 0 |
| `Element` | 14 | `LMSDataContainerHeader` | @CopyOption, @Name, @UniqueID, @Visibility |
| `Emission` | 1 | `SequenceItem/Detectors/Detectors` | 5.49e-07 |
| `EmissionWavelengthIndex` | 6 | `Data/SingleMoleculeDetection/Dataset` | -1 |
| `EmissionWavelengths` | 1 | `Data/SingleMoleculeDetection/Dataset` | — |
| `End` | 3 | `FlimData/LifetimeDecaysSum/TimeGates` | 1e+10 |
| `EndTime` | 5 | `PatternMatching/FitData/FitModel` | 1.236363636e-08 |
| `EstimatedLifetimeBackground` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | 0 |
| `Excitation` | 1 | `SequenceItem/Detectors/Detectors` | 5.04e-07 |
| `ExcitationWavelengthIndex` | 6 | `Data/SingleMoleculeDetection/Dataset` | -1 |
| `ExcitationWavelengths` | 1 | `Data/SingleMoleculeDetection/Dataset` | — |
| `Experiment` | 1 | `LMSDataContainerHeader/Element/Data` | @IsSavedFlag, @Path |
| `ExtendedProperties` | 2 | `ShapeList/Items/Item0` | — |
| `FastFlimImage` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `FillMaskMode` | 4 | `PatternMatching/ImageShapes/ShapeList` | Add |
| `Filter` | 1 | `FlimData/PhasorData/Channels` | Wavelet |
| `FilterNoiseLevel` | 1 | `FlimData/PhasorData/Channels` | 1 |
| `FilterReflections` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | true |
| `FilterSize` | 1 | `FlimData/PhasorData/Channels` | 3 |
| `FilterStrength` | 1 | `FlimData/PhasorData/Channels` | 100 |
| `FilterWheel` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | @BeamMergerAutoSelectionEnabled, @BeamSplitte… |
| `FilteredDataFrequency` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 34645 26443 26241 26339 25320 24889 24100 370… |
| `FilteredDataStartTime` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 4.848484848e-11 |
| `FilteredDataTimeStep` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 9.696969697e-11 |
| `FilteredImaginaryImage` | 2 | `Dataset/FlimData/STED` | — |
| `FilteredRealImage` | 2 | `Dataset/FlimData/STED` | — |
| `FirstRepetitionToAnalyze` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | 0 |
| `FitData` | 1 | `Dataset/FlimData/PatternMatching` | — |
| `FitFlim` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | — |
| `FitFret` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | — |
| `FitModel` | 5 | `FlimData/PatternMatching/FitData` | — |
| `FitResiduals` | 5 | `FlimData/PatternMatching/FitData` | — |
| `FitResult` | 5 | `FlimData/PatternMatching/FitData` | — |
| `FitResultStart` | 5 | `FlimData/PatternMatching/FitData` | 0 |
| `FlimData` | 1 | `Data/SingleMoleculeDetection/Dataset` | — |
| `Font` | 2 | `ShapeList/Items/Item0` | — |
| `Format` | 1 | `SingleMoleculeDetection/Dataset/RawData` | LMSCOMPRESSED |
| `FrameMarker` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0 |
| `FrameRepetitions` | 1 | `RawData/Sequence/SequenceItem` | 1 |
| `FrameRepetitionsMarked` | 1 | `SingleMoleculeDetection/Dataset/RawData` | false |
| `FreeIntensityParameter` | 1 | `Dataset/FlimData/Presentation` | None |
| `FreeIntensityParameterComponentIndex` | 1 | `Dataset/FlimData/Presentation` | 0 |
| `FreeIntensityType` | 1 | `Dataset/FlimData/Presentation` | PhotonCounts |
| `FreePaletteParameter` | 1 | `Dataset/FlimData/Presentation` | None |
| `FreePaletteParameterComponentIndex` | 1 | `Dataset/FlimData/Presentation` | 0 |
| `FreePaletteType` | 1 | `Dataset/FlimData/Presentation` | FastFlim |
| `Frequency` | 7 | `Dataset/FlimData/LifetimeDecaysSum` | 34645 26443 26241 26339 25320 24889 24100 370… |
| `GalvoSwitchParameter` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | @LastCsPhase, @LastCsScanSpeed, @LastRsPhase,… |
| `GatedImage` | 1 | `Dataset/FlimData/STED` | — |
| `GatedStedImage` | 1 | `Dataset/FlimData/STED` | — |
| `Harmonic` | 2 | `Dataset/FlimData/STED` | 1 |
| `HasRawData` | 1 | `RawData/Sequence/SequenceItem` | true |
| `Identifier` | 64 | `Dataset/FlimData/IntensityHistograms` | 3 |
| `Image` | 10 | `Children/Element/Data` | @TextDescription |
| `ImageChannel` | 3 | `DetectorList/Detector/ImageChannelArray` | @AcquisitionMode, @AcquisitionModeName, @CanD… |
| `ImageChannelArray` | 3 | `ATLConfocalSettingDefinition/DetectorList/Detector` | — |
| `ImageDescription` | 10 | `Element/Data/Image` | — |
| `ImageFitThreshold` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 50 |
| `ImageMaskImage` | 1 | `Dataset/FlimData/PhasorData` | — |
| `ImageShapes` | 1 | `Dataset/FlimData/PatternMatching` | — |
| `ImaginaryImage` | 2 | `Dataset/FlimData/STED` | — |
| `IncludesSecondPhoton` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | false |
| `InstrumentResponseFunctions` | 1 | `Dataset/FlimData/CalculatedInstrumentResponseFuncti…` | — |
| `InstrumentSettingMask` | 1 | `Attachment/LDM_Block_Sequential/LDM_Block_Sequentia…` | @AOTF, @PMTs_on_off, @Z_Compensation |
| `IntensityFactor` | 11 | `FlimData/FastFlimImage/Channels` | 1 |
| `IntensityHistograms` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `IntensityImage` | 3 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `IntensityLimit` | 1 | `SequenceItem/Detectors/Detectors` | 1 |
| `IntensityOffset` | 1 | `FlimData/FastFlimImage/Channels` | -8.727272727e-10 |
| `IntensityThreshold` | 1 | `FlimData/PhasorData/Channels` | 1 |
| `InvertImageX` | 1 | `SingleMoleculeDetection/Dataset/RawData` | true |
| `InvertImageY` | 1 | `SingleMoleculeDetection/Dataset/RawData` | true |
| `IsPreview` | 1 | `SingleMoleculeDetection/Dataset/RawData` | false |
| `Item0` | 4 | `PhasorPlotShapes/ShapeList/Items` | — |
| `Items` | 10 | `PhasorData/PhasorPlotShapes/ShapeList` | — |
| `Key` | 7 | `Channels/ChannelDescription/ChannelProperty` | ChannelGroup |
| `LDM_Block_Sequential` | 1 | `Data/Image/Attachment` | @AutoFocusSequence, @BlockName, @SequentialMo… |
| `LDM_Block_Sequential_Execution_Mask` | 1 | `Image/Attachment/LDM_Block_Sequential` | — |
| `LDM_Block_Sequential_List` | 1 | `Image/Attachment/LDM_Block_Sequential` | — |
| `LDM_Block_Sequential_Master` | 1 | `Image/Attachment/LDM_Block_Sequential` | — |
| `LMSDataContainerHeader` | 3 | `` | @Version |
| `LUT` | 15 | `Attachment/ATLConfocalSettingDefinition/LUT_List` | @Channel, @LutName |
| `LUT_List` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `LabelBackgroundColor` | 2 | `ShapeList/Items/Item0` | R:0 ,G: 0,B: 0,A: 0 |
| `LabelTextColor` | 2 | `ShapeList/Items/Item0` | R:255 ,G: 255,B: 255,A: 255 |
| `Laser` | 4 | `Attachment/ATLConfocalSettingDefinition/LaserArray` | @CanDoChangeWavelength, @CanDoLinearOutputPow… |
| `LaserArray` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `LaserLineSetting` | 27 | `ATLConfocalSettingDefinition/AotfList/Aotf` | @AOBSIntensityDev, @AOBSIntensityLowDev, @Can… |
| `LaserPulseFrequency` | 2 | `SingleMoleculeDetection/Dataset/RawData` | 78020000 |
| `LaserPulseIntervals` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 3.021239589e+10 |
| `LastRepetitionToAnalyze` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | 2147483647 |
| `LifetimeBinning` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | 1 |
| `LifetimeDecays` | 2 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `LifetimeDecaysSum` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `LifetimeDetection` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | @ChannelLinkActive, @ExclusiveWhitelightLaser… |
| `LifetimeDistributionHistograms` | 2 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `LifetimeHistograms` | 2 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `LifetimeSeparation` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `LightSourceList` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `LightSourceSetting` | 6 | `Attachment/ATLConfocalSettingDefinition/LightSource…` | @LightSourceName, @LightSourceType, @version |
| `LineBlocked` | 27 | `LightSourceList/LightSourceSetting/LinesBlockedForD…` | @IsBlocked |
| `LineEndMarker` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0 |
| `LineRepetitions` | 1 | `RawData/Sequence/SequenceItem` | 1 |
| `LineStartMarker` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0 |
| `LineTime` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0.04 |
| `LinesBlockedForDyeAssistant` | 6 | `ATLConfocalSettingDefinition/LightSourceList/LightS…` | — |
| `LinesXT` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0 |
| `LinkDetectors` | 1 | `SingleMoleculeDetection/Dataset/RawData` | false |
| `LoadedInstrumentResponseFunctions` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `LoopIndex` | 5 | `Dataset/FlimData/LifetimeDecaysSum` | -1 |
| `LowerIntensityThreshold` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 0 |
| `LowerLevel` | 2 | `FlimData/FastFlimImage/Channels` | -380.4368676 |
| `LowerLevel3D` | 5 | `FlimData/FastFlimImage/Channels` | 0 |
| `LutInfo` | 15 | `ATLConfocalSettingDefinition/DetectorList/Detector` | @LutBackground, @LutBlackValue, @LutDescripti… |
| `Maximum` | 66 | `FlimData/FastFlimImage/Channels` | 1e-06 |
| `Mean` | 2 | `FlimData/FastFlimImage/Channels` | 54.51980845 |
| `Measurement` | 1 | `Data/SingleMoleculeDetection/Dataset` | urn:uuid:ea369425-b7e8-4034-279e-9bb2509a8dad |
| `Memory` | 14 | `LMSDataContainerHeader/Element` | @MemoryBlockID, @Size |
| `Minimum` | 14 | `PhasorData/Regions/Channels` | 1e-11 |
| `MinimumFractionDonorImage` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `MultiBand` | 8 | `Attachment/ATLConfocalSettingDefinition/Spectro` | @Channel, @ChannelName, @LeftWorld, @RightWor… |
| `Name` | 5 | `RawData/Channels/Channel` | HyD X 3 |
| `NumberChannels` | 4 | `Dataset/FlimData/LifetimeDecays` | 1 |
| `NumberExponentialComponents` | 5 | `PatternMatching/FitData/FitModel` | 1 |
| `NumberHarmonics` | 1 | `Dataset/FlimData/PhasorData` | 1 |
| `NumberPatterns` | 5 | `PatternMatching/FitData/FitModel` | 1 |
| `NumberPhasorImagePairs` | 1 | `Dataset/FlimData/PhasorData` | 1 |
| `NumberRepetitions` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | 1 |
| `NumericalAperture` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 1.4 |
| `OnlineDyeSeparation` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | @Channels, @Detectors, @Enabled, @KeepRawImage |
| `OnlineDyeSeparationChannelList` | 3 | `Attachment/ATLConfocalSettingDefinition/OnlineDyeSe…` | — |
| `OnlineDyeSeparationDetectorList` | 3 | `Attachment/ATLConfocalSettingDefinition/OnlineDyeSe…` | — |
| `Parameters` | 59 | `PatternMatching/FitData/FitModel` | — |
| `PatternMatching` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `Period` | 3 | `Dataset/FlimData/LifetimeDecaysSum` | 1.281722635e-08 |
| `PhasorAmplitude` | 1 | `SequenceItem/Detectors/Detectors` | 1.000513077 |
| `PhasorAutomaticReference` | 1 | `SequenceItem/Detectors/Detectors` | true |
| `PhasorData` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `PhasorPhase` | 1 | `SequenceItem/Detectors/Detectors` | 19.99392909 |
| `PhasorPlot` | 1 | `Dataset/FlimData/STED` | — |
| `PhasorPlotShapes` | 2 | `Dataset/FlimData/PhasorData` | — |
| `PhotonsPerLaserPulseHistograms` | 2 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `PinholeAiry` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 1 |
| `PixelTime` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 1.53875e-05 |
| `PointModeAnalysisMode` | 1 | `Data/SingleMoleculeDetection/Dataset` | Undefined |
| `Presentation` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `ProbabilityLevel` | 5 | `PatternMatching/FitData/FitModel` | 1 |
| `Processed` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | false |
| `ProcessedV2` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | true |
| `Quantity` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | @Unit, @Value |
| `ROISet` | 2 | `LMSDataContainerHeader/Element/Data` | @PossibleChildROITypes, @PossibleROIActions, … |
| `RawData` | 1 | `Data/SingleMoleculeDetection/Dataset` | — |
| `RawDataFilterMode` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | Mixed |
| `RawDataFrequency` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 34645 26443 26241 26339 25320 24889 24100 370… |
| `RawDataStartTime` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 4.848484848e-11 |
| `RawDataTimeStep` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 9.696969697e-11 |
| `RealImage` | 2 | `Dataset/FlimData/STED` | — |
| `RefractiveIndex` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 1.518 |
| `RegionIndex` | 5 | `Dataset/FlimData/LifetimeDecaysSum` | -1 |
| `Regions` | 1 | `Dataset/FlimData/PhasorData` | — |
| `RootNode` | 1 | `Data/Image/Attachment` | — |
| `STED` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `STED_DepletionLine` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | @IsPulsedLaser, @LineIndex, @Wavelength |
| `SaturationFactor` | 1 | `SequenceItem/Detectors/Detectors` | 0 |
| `ScatterPlot` | 1 | `Dataset/FlimData/PatternMatching` | — |
| `ScatterPlotShapes` | 1 | `Dataset/FlimData/PatternMatching` | — |
| `Sequence` | 1 | `SingleMoleculeDetection/Dataset/RawData` | — |
| `SequenceItem` | 1 | `Dataset/RawData/Sequence` | — |
| `SequenceItemIndex` | 1 | `Channel/Detectors/DetectorReferences` | 0 |
| `SequentialMode` | 1 | `SingleMoleculeDetection/Dataset/RawData` | SequentialLine |
| `ShapeList` | 4 | `FlimData/PatternMatching/ImageShapes` | — |
| `Shutter` | 4 | `Attachment/ATLConfocalSettingDefinition/ShutterList` | @IsActive, @LightSourceName, @LightSourceType… |
| `ShutterList` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `SingleMoleculeDetection` | 1 | `Children/Element/Data` | @IsAnalysisResult, @IsImage |
| `SinusCorrection` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0 |
| `Size` | 13 | `RawData/Dimensions/Dimension` | 512 |
| `Spectro` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | — |
| `SpimCACompensationParameter` | 3 | `Image/Attachment/ATLConfocalSettingDefinition` | @YGalvoOffsetLeft, @YGalvoOffsetRight |
| `StackPlaneIndex` | 5 | `Dataset/FlimData/LifetimeDecaysSum` | -1 |
| `StagePositionIndex` | 1 | `Data/SingleMoleculeDetection/Dataset` | 0 |
| `StagePositions` | 6 | `Data/SingleMoleculeDetection/Dataset` | — |
| `StandardDeviation` | 2 | `FlimData/FastFlimImage/Channels` | 97.56711261 |
| `StandardDeviationImage` | 1 | `SingleMoleculeDetection/Dataset/FlimData` | — |
| `Start` | 7 | `Dataset/FlimData/IntensityHistograms` | 0 |
| `StartTime` | 3 | `Dataset/FlimData/LifetimeDecaysSum` | 4.848484848e-11 |
| `StartTimeForReconvolution` | 3 | `PatternMatching/FitData/FitModel` | 4.848484848e-11 |
| `StartTimeForTailFit` | 3 | `PatternMatching/FitData/FitModel` | 1.115151515e-09 |
| `StedImage` | 1 | `Dataset/FlimData/STED` | — |
| `StedMode` | 1 | `SequenceItem/Detectors/Detectors` | Node |
| `StedModeValidV2` | 1 | `SingleMoleculeDetection/Dataset/RawData` | true |
| `Step` | 4 | `Dataset/FlimData/IntensityHistograms` | 9.696969697e-11 |
| `Stroke` | 2 | `ShapeList/Items/Item0` | R:255 ,G: 0,B: 0,A: 255 |
| `SwapImageXY` | 1 | `SingleMoleculeDetection/Dataset/RawData` | true |
| `SyncronizationMarkerPeriod` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0 |
| `TailStartTime` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 1.115151515e-09 |
| `TauScanDef` | 15 | `ATLConfocalSettingDefinition/DetectorList/Detector` | @TauScanCount, @TauScanRangeBegin, @TauScanRa… |
| `TauStedDeconvolvedFilteredImage` | 1 | `Dataset/FlimData/STED` | — |
| `TauStedDeconvolvedImage` | 1 | `Dataset/FlimData/STED` | — |
| `TauStedDeconvolvedUnfilteredImage` | 1 | `Dataset/FlimData/STED` | — |
| `TauStedFilteredImage` | 1 | `Dataset/FlimData/STED` | — |
| `TauStedImage` | 1 | `Dataset/FlimData/STED` | — |
| `TauStedPlusImage` | 1 | `Dataset/FlimData/STED` | — |
| `TauStedUnfilteredImage` | 1 | `Dataset/FlimData/STED` | — |
| `Tile` | 42 | `Data/Image/Attachment` | @FieldX, @FieldY, @PosX, @PosY |
| `Time` | 5 | `FlimData/PatternMatching/FitData` | — |
| `TimeGates` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | — |
| `TimeIndex` | 6 | `Data/SingleMoleculeDetection/Dataset` | -1 |
| `TimeStamp` | 1 | `Element/Data/Experiment` | @HighInteger, @LowInteger |
| `TimeStampList` | 10 | `Element/Data/Image` | 1db48f9603ae290 1db48f96cde34c0 1db48f9794456… |
| `TimeStamps` | 1 | `Data/SingleMoleculeDetection/Dataset` | 13378084962.267 |
| `TimeStep` | 3 | `Dataset/FlimData/LifetimeDecaysSum` | 9.696969697e-11 |
| `Type` | 53 | `FitData/FitModel/Parameters` | Free |
| `UniqueIdentifier` | 28 | `Dataset/FlimData/FastFlimImage` | 932e035f-9ccc-48b4-62a8-c44d79d4ef77 |
| `UpperIntensityThreshold` | 3 | `Dataset/FlimData/LifetimeDecaysSum` | 1e+100 |
| `UpperIntensityThresholdActive` | 1 | `FlimData/PhasorData/Channels` | false |
| `UpperLevel` | 5 | `FlimData/FastFlimImage/Channels` | 168.7192688 |
| `UpperLevel3D` | 5 | `FlimData/FastFlimImage/Channels` | 1 |
| `Value` | 24 | `Channels/ChannelDescription/ChannelProperty` | 0 |
| `VariableBeamExpanderFactors` | 2 | `Image/Attachment/ATLConfocalSettingDefinition` | @CommonFactor, @CommonSettingEnabled |
| `VertexUnitMode` | 4 | `PatternMatching/ImageShapes/ShapeList` | Metric |
| `Verticies` | 2 | `ShapeList/Items/Item0` | — |
| `ViewerMode` | 1 | `Dataset/FlimData/Presentation` | FastFlim |
| `VoxelSizeX` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 7.222048924e-08 |
| `VoxelSizeY` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 7.222048924e-08 |
| `VoxelSizeZ` | 1 | `SingleMoleculeDetection/Dataset/RawData` | 0 |
| `Weight` | 2 | `Dataset/FlimData/LifetimeDecaysSum` | 0.05 0.05 0.05 0.05 0.05 0.05 0.05 0.05 0.05 … |
| `Weights` | 5 | `FlimData/PatternMatching/FitData` | — |
| `Wheel` | 8 | `Attachment/ATLConfocalSettingDefinition/FilterWheel` | @FilterDisplayName, @FilterIndex, @FilterName… |
| `X` | 8 | `SingleMoleculeDetection/Dataset/StagePositions` | 40 |
| `Y` | 8 | `SingleMoleculeDetection/Dataset/StagePositions` | 40 |
| `Z` | 6 | `SingleMoleculeDetection/Dataset/StagePositions` | 0.0067155563 |
| `_AxialRatio` | 1 | `SequenceItem/Detectors/Detectors` | 0 |
