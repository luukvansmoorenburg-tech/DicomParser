# DicomParser

Generates readable, standardised names for DICOM MRI sequences from header tags.

## What it does

Builds a name in the form `<undersampling>_<weighting>_<time>_<thickness>` — for example `PI_T2_2m23s_4.0mm` — from four components derived from DICOM tags:

| # | Component | Source |
|---|-----------|--------|
| 1 | Undersampling technique | Philips private tag `(2005,1710)` or `(0018,9078)` |
| 2 | MRI weighting (T1/T2/PD/FLAIR/…) | Derived from TR, TE, TI values |
| 3 | Acquisition time | `(0018,9073)` AcquisitionDuration |
| 4 | Slice thickness | `(0018,0050)` SliceThickness |

Philips-specific noise files (DICOMDIR, ExamCards, Presentation States) are automatically skipped.

## Requirements

```
pip install pydicom
```

## Usage

```bash
# Single file
python dicom_naam.py path/to/file.dcm

# Entire folder (recursive), one name per series
python dicom_naam.py path/to/folder/

# Write results to CSV
python dicom_naam.py path/to/folder/ --csv results.csv

# Show every slice individually instead of grouping by series
python dicom_naam.py path/to/folder/ --per-bestand

# Copy series into renamed subfolders and update ProtocolName tag
python dicom_naam.py path/to/folder/ --output output/
```

No path given? The script falls back to the pydicom built-in test file so you can try it immediately.
