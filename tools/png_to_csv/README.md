# png_to_csv — prepare a FLIM calibration CSV from Leica screenshots

Helper tools for turning a Leica **Phasor Calibration** table into the
`batch_tcspc_calibration.csv` that PerCell4 ingests (per-channel
`phase` / `modulation` / `frequency_mhz`).

> **Prefer the `.lif` when you have one.** The Batch TCSPC dialog now reads
> phase and modulation directly out of a Leica `.lif`, which is the same
> calibration this tool OCRs off a screenshot of — except the file holds it
> at full precision, while the dialog rounds for display. Reading the `.lif`
> needs no Tesseract, no OCR review pass, and no manual CSV. Use these tools
> when the `.lif` is unavailable and a screenshot is all you have.

## Files

- `phasor_ocr_to_xlsx.py` — OCRs Leica Phasor Calibration screenshots (PNG) into
  an Excel sheet of `Name / Channel / Phase (°) / Phase (rad) / Modulation`. The
  **Phase (rad)** column is the value the calibration CSV wants — the Leica phase
  in degrees auto-converted with `-1*RADIANS` (negated radians) — so there is no
  manual Excel step. Cells the OCR is unsure about are highlighted amber and
  listed in the terminal.
- `batch_tcspc_calibration_template.csv` — the empty calibration CSV (header row
  only: `dataset,channel,frequency_mhz,phase,modulation`).
- `phasor_calibration.xlsx` — a sample output of the script.

## One-time setup

The script needs the Tesseract OCR engine plus three Python packages
(`pytesseract`, `Pillow`, `openpyxl`). Install the Python packages via the
PerCell4 `ocr` extra (or by hand), then the Tesseract engine separately:

```bash
pip install -e ".[ocr]"           # from the repo root — installs the python deps
# (or by hand: pip install pytesseract Pillow openpyxl)

# Tesseract OCR engine (system binary, not a pip package):
#   macOS:   brew install tesseract
#   Linux:   sudo apt install tesseract-ocr
#   Windows: https://github.com/UB-Mannheim/tesseract/wiki  (add it to PATH)
```

## Creating a calibration

1. **Screenshot** the Leica Phasor Calibration table (one or more PNGs; if the
   table scrolls, take overlapping screenshots — duplicate rows are de-duped).

2. **Run the script** on the screenshot(s) — paste the path(s) after the command:

   ```bash
   python tools/png_to_csv/phasor_ocr_to_xlsx.py /path/to/screenshot.png
   # multiple images concatenate in order:
   python tools/png_to_csv/phasor_ocr_to_xlsx.py shot1.png shot2.png -o my_calibration.xlsx
   ```

   With no `-o`, it writes `phasor_calibration.xlsx` in the current directory.
   The terminal lists any cells the OCR was unsure about (by Excel cell, e.g.
   `A5`), and those cells are highlighted **amber** in the file — open it and fix
   just those. Channel, Phase, and Modulation numbers are anchored on the
   reliable tokens and are almost always exact; names are best-effort.

3. **Fill in the template CSV.** Copy `batch_tcspc_calibration_template.csv`,
   then for each channel paste the `channel`, the **`Phase (rad)`** value (already
   `-1*RADIANS`-converted — paste this into the CSV's `phase` column, *not* the
   degrees), and `modulation` from the xlsx, plus the two values the screenshot
   doesn't carry:
   - `dataset` — the dataset name (the `.h5` stem this calibration applies to),
     one row per `(dataset, channel)`.
   - `frequency_mhz` — your laser repetition rate (e.g. `78` or `80`).

   ```csv
   dataset,channel,frequency_mhz,phase,modulation
   DA Set 1,mNG,78,-0.692546647,0.9998
   DA Set 1,mCh,78,-0.681,0.991
   ```

4. **Use it in PerCell4.** Point the Batch TCSPC import / Add-Layer TCSPC tab at
   this CSV — `domain/io/calibration_csv.py` reads the required columns
   (`dataset,channel,frequency_mhz,phase,modulation`; extras are ignored) and
   applies each channel's calibration to the imported decay.
