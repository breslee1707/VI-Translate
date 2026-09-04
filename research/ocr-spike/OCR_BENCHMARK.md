# OCR experiment and benchmark

This directory is research-only. It does not enable OCR in the desktop GUI and
does not change the default `--ocr off` behavior.

## Reproduce locally

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
.\.venv\Scripts\python.exe research\ocr-spike\fetch_benchmark.py
.\.venv\Scripts\python.exe research\ocr-spike\make_benchmark.py
.\.venv\Scripts\python.exe research\ocr-spike\make_benchmark.py --real-only
.\.venv\Scripts\python.exe research\ocr-spike\run_benchmark.py --suite smoke --profile standard
```

All downloaded PDFs, rendered variants, OCR models and reports are written
under `tmp/ocr-benchmark/`, which is ignored by Git. `manifest.lock.json` and
`variants.lock.json` contain source/selection hashes and provenance. Refreshing
the corpus requires an explicit `--refresh` and creates a new checksum lock.

## Corpus

The lock contains 36 documents and 144 selected source pages. The active
generator creates 95 text-grounded variants plus 17 image-only `real-scan`
variants (428 pages total). Sources cover NASA technical reports, IRS forms and
public-domain Library of Congress scans. Synthetic variants are clean raster,
JPEG/150 DPI, skew+blur, noise+contrast and tiled-image pages.

Pages from scan-like sources are marked `provisional-source-ocr`; their hidden
source text is useful for smoke diagnostics but is not a release-quality OCR
ground truth. They must be manually transcribed or independently verified
before entering a hard accuracy gate.

## Current result

The standard PP-OCRv6 small profile passed the recognition smoke gate on ten
text-grounded pages: matched CER 0.23%, missed-character rate 0.23%, numeric
token recall 100%, reading-order tau 0.985, and 2.96 seconds/page warm CPU.
The enhanced PP-OCRv6 medium profile was about 17x slower on the same machine
without a measurable accuracy improvement, so it remains a benchmark-only
profile and is not advertised as a quality upgrade.

Visual QA found that partial inpainting is unsafe on pages containing formulas,
nomenclature, forms, dense rules or protected layout regions. The experimental
CLI now keeps those pages byte-for-byte unchanged and reports a partial result;
it only cleans a page when OCR and layout safety checks both pass. This is a
fail-safe research mode, not a claim that arbitrary scans are production-ready.
