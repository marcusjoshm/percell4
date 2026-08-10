# `.lif` calibration metadata — multi-channel sample

Metadata dump of `Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2.lif`, produced to answer one question: **what
uniquely identifies the several calibration records a single region holds?**

The full pretty-printed header is beside this file as
[`lif-xml-header-multichannel.xml`](lif-xml-header-multichannel.xml)
(6,975 lines). For the format itself see
[`lif-xml-header.md`](lif-xml-header.md).

---

## The answer

**The identifier is the calibration block's ordinal position within its
`PhasorData`, which corresponds to the same-ordinal detector record in that
region.** Nothing inside a block names its channel — `<Channel>` is `0` in every
one of them — so position is the only link, and it is a reliable one: each
region here holds exactly as many calibration blocks as it has detectors, in the
same order.

Concretely, in both regions block 0 belongs to `HyD X 3` and block 1 to
`HyD X 1`.

Corroborating evidence, all of it agreeing on two channels per region:

- `FlimData/LifetimeDecays/NumberChannels` is `2`, and its decay entries carry
  `Channel` values `0` and `1`.
- Two detector records per region, in document order: `HyD X 3` (`Identifier` 3)
  then `HyD X 1` (`Identifier` 1).
- The region image declares two channel descriptions (LUTs `Magenta`, `Green`).
- The FLIM element holds `Pattern Matching Scatter Plot Channel 1` and
  `Channel 2` children.

Matching a block to a detector by the block's `<Channel>` value cannot work —
every acquisition detector record also reports `<Detector>0</Detector>`, so any
lookup keyed on it returns the first detector for every block.

---

## Element tree

- `Rep 3 - Hpep3-Dcp1B + cpHalo3-Dcp2.lif`
  - `UT Hpep3 5x8`
    - `BleachPointROISet`
    - `BleachPointROISet`
    - `BleachPointROISet`
    - `FLIM`
      - `LineOffsets`
      - `FrameOffsets`
      - `Phasor Plot`
      - `Pattern Matching Scatter Plot Channel 1`
      - `Pattern Matching Scatter Plot Channel 2`
      - `Intensity`
      - `Fast Flim`
      - `Standard Deviation`
      - `Phasor Real`
      - `Phasor Imaginary`
      - `Phasor Intensity`
      - `Phasor Mask`
  - `As Hpep3 5x8`
    - `BleachPointROISet`
    - `BleachPointROISet`
    - `BleachPointROISet`
    - `FLIM`
      - `LineOffsets`
      - `FrameOffsets`
      - `Phasor Plot`
      - `Pattern Matching Scatter Plot Channel 1`
      - `Pattern Matching Scatter Plot Channel 2`
      - `Intensity`
      - `Fast Flim`
      - `Standard Deviation`
      - `Phasor Real`
      - `Phasor Imaginary`
      - `Phasor Intensity`
      - `Phasor Mask`

---

## Calibration records per region

### Region `UT Hpep3 5x8`

`FlimData/LifetimeDecays/NumberChannels` = **2**, and the region carries
2 detector records and 2 calibration blocks — matched here by
document order.

| Block # | Detector (same ordinal) | `Identifier` | Block's `<Channel>` | `AutomaticReferencePhase` | `AutomaticReferenceAmplitude` |
|---|---|---|---|---|---|
| 0 | `HyD X 3` | 3 | 0 | 34.8309987 | 1.00029786 |
| 1 | `HyD X 1` | 1 | 0 | 32.70105596 | 1.000585206 |

Note every block reports `<Channel>0</Channel>`. That field does **not** identify
the channel; the block's ordinal does.

Field-by-field, the two blocks:

| Field | Block 0 | Block 1 | Differs |
|---|---|---|---|
| `Channel` | `0` | `0` | no |
| `Period` | `1.281722635e-08` | `1.281722635e-08` | no |
| `AutomaticReference` | `true` | `true` | no |
| `AutomaticReferencePhase` | `34.8309987` | `32.70105596` | **yes** |
| `AutomaticReferenceAmplitude` | `1.00029786` | `1.000585206` | **yes** |
| `Filter` | `Wavelet` | `Wavelet` | no |
| `FilterSize` | `3` | `3` | no |
| `FilterStrength` | `100` | `100` | no |
| `FilterNoiseLevel` | `1` | `1` | no |
| `IntensityThreshold` | `1` | `1` | no |
| `UpperIntensityThreshold` | `1000` | `1000` | no |
| `UpperIntensityThresholdActive` | `false` | `false` | no |
| `PhasorPlotShapes` | — | — | no |

<details><summary>Block 0 verbatim</summary>

```xml
<Channels>
  <Channel>0</Channel>
  <Period>1.281722635e-08</Period>
  <AutomaticReference>true</AutomaticReference>
  <AutomaticReferencePhase>34.8309987</AutomaticReferencePhase>
  <AutomaticReferenceAmplitude>1.00029786</AutomaticReferenceAmplitude>
  <Filter>Wavelet</Filter>
  <FilterSize>3</FilterSize>
  <FilterStrength>100</FilterStrength>
  <FilterNoiseLevel>1</FilterNoiseLevel>
  <IntensityThreshold>1</IntensityThreshold>
  <UpperIntensityThreshold>1000</UpperIntensityThreshold>
  <UpperIntensityThresholdActive>false</UpperIntensityThresholdActive>
</Channels>
```

</details>

<details><summary>Block 1 verbatim</summary>

```xml
<Channels>
  <Channel>0</Channel>
  <Period>1.281722635e-08</Period>
  <AutomaticReference>true</AutomaticReference>
  <AutomaticReferencePhase>32.70105596</AutomaticReferencePhase>
  <AutomaticReferenceAmplitude>1.000585206</AutomaticReferenceAmplitude>
  <Filter>Wavelet</Filter>
  <FilterSize>3</FilterSize>
  <FilterStrength>100</FilterStrength>
  <FilterNoiseLevel>1</FilterNoiseLevel>
  <IntensityThreshold>1</IntensityThreshold>
  <UpperIntensityThreshold>1000</UpperIntensityThreshold>
  <UpperIntensityThresholdActive>false</UpperIntensityThresholdActive>
</Channels>
```

</details>

### Region `As Hpep3 5x8`

`FlimData/LifetimeDecays/NumberChannels` = **2**, and the region carries
2 detector records and 2 calibration blocks — matched here by
document order.

| Block # | Detector (same ordinal) | `Identifier` | Block's `<Channel>` | `AutomaticReferencePhase` | `AutomaticReferenceAmplitude` |
|---|---|---|---|---|---|
| 0 | `HyD X 3` | 3 | 0 | 34.78851526 | 1.000300839 |
| 1 | `HyD X 1` | 1 | 0 | 32.6008048 | 1.000588184 |

Note every block reports `<Channel>0</Channel>`. That field does **not** identify
the channel; the block's ordinal does.

Field-by-field, the two blocks:

| Field | Block 0 | Block 1 | Differs |
|---|---|---|---|
| `Channel` | `0` | `0` | no |
| `Period` | `1.281722635e-08` | `1.281722635e-08` | no |
| `AutomaticReference` | `true` | `true` | no |
| `AutomaticReferencePhase` | `34.78851526` | `32.6008048` | **yes** |
| `AutomaticReferenceAmplitude` | `1.000300839` | `1.000588184` | **yes** |
| `Filter` | `Wavelet` | `Wavelet` | no |
| `FilterSize` | `3` | `3` | no |
| `FilterStrength` | `100` | `100` | no |
| `FilterNoiseLevel` | `1` | `1` | no |
| `IntensityThreshold` | `1` | `1` | no |
| `UpperIntensityThreshold` | `1000` | `1000` | no |
| `UpperIntensityThresholdActive` | `false` | `false` | no |
| `PhasorPlotShapes` | — | — | no |

<details><summary>Block 0 verbatim</summary>

```xml
<Channels>
  <Channel>0</Channel>
  <Period>1.281722635e-08</Period>
  <AutomaticReference>true</AutomaticReference>
  <AutomaticReferencePhase>34.78851526</AutomaticReferencePhase>
  <AutomaticReferenceAmplitude>1.000300839</AutomaticReferenceAmplitude>
  <Filter>Wavelet</Filter>
  <FilterSize>3</FilterSize>
  <FilterStrength>100</FilterStrength>
  <FilterNoiseLevel>1</FilterNoiseLevel>
  <IntensityThreshold>1</IntensityThreshold>
  <UpperIntensityThreshold>1000</UpperIntensityThreshold>
  <UpperIntensityThresholdActive>false</UpperIntensityThresholdActive>
</Channels>
```

</details>

<details><summary>Block 1 verbatim</summary>

```xml
<Channels>
  <Channel>0</Channel>
  <Period>1.281722635e-08</Period>
  <AutomaticReference>true</AutomaticReference>
  <AutomaticReferencePhase>32.6008048</AutomaticReferencePhase>
  <AutomaticReferenceAmplitude>1.000588184</AutomaticReferenceAmplitude>
  <Filter>Wavelet</Filter>
  <FilterSize>3</FilterSize>
  <FilterStrength>100</FilterStrength>
  <FilterNoiseLevel>1</FilterNoiseLevel>
  <IntensityThreshold>1</IntensityThreshold>
  <UpperIntensityThreshold>1000</UpperIntensityThreshold>
  <UpperIntensityThresholdActive>false</UpperIntensityThresholdActive>
</Channels>
```

</details>

