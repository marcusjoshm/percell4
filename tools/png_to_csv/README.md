# png_to_csv — prepare a FLIM calibration CSV from Leica screenshots

Helper tools for turning a Leica **Phasor Calibration** table into the
`batch_tcspc_calibration.csv` that PerCell4 ingests (per-channel
`phase` / `modulation` / `frequency_mhz`).

## Files

- `phasor_ocr_to_xlsx.py` — OCRs Leica Phasor Calibration screenshots (PNG) into
  an Excel sheet of `Name / Channel / Phase / Modulation`.
- `batch_tcspc_calibration_template.csv` — the empty calibration CSV (header row
  only: `dataset,channel,frequency_mhz,phase,modulation`).
- `phasor_calibration.xlsx` — a sample output of the script.

## One-time setup

The script needs the Tesseract OCR engine plus three Python packages:

```bash
# Tesseract OCR engine:
#   macOS:   brew install tesseract
#   Linux:   sudo apt install tesseract-ocr
#   Windows: https://github.com/UB-Mannheim/tesseract/wiki  (add it to PATH)
pip install pytesseract Pillow openpyxl
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
   Open it and skim the **Name** column for OCR slips (e.g. `FLIM5` → `FLIM 5`);
   Phase / Modulation / Channel are regex-anchored and almost always exact.

3. **Fill in the template CSV.** Copy `batch_tcspc_calibration_template.csv`,
   then for each channel paste the `channel`, `phase`, and `modulation` values
   from the xlsx and add the two values the screenshot doesn't carry:
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
