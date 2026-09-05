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

The compact core run covered 16 content pages across NASA and IRS material in
184 seconds (11.5 seconds/page, including heavy form pages). Its aggregate
matched CER was 2.53%, missed-character rate 3.08%, numeric recall 97.75% and
reading-order tau 0.961. The report includes per-feature scores; form/rule and
small-font groups are intentionally below the prose gate and remain protected
or partial in the CLI.

Visual QA found that partial inpainting is unsafe on pages containing formulas,
nomenclature, forms, dense rules or protected layout regions. The experimental
CLI keeps those pages visually unchanged and reports a partial result;
it only cleans a page when OCR and layout safety checks both pass. This is a
fail-safe research mode, not a claim that arbitrary scans are production-ready.
Code-heavy pages and mojibake remain protected. Multi-column and dense prose
now require explicit owned paragraph bounds; numbered verse and postal rows
keep physical line breaks. A previous real Vietnamese smoke render is kept under
`tmp/ocr-benchmark/qa-real-vietnamese-v2/`; it has no residual source ink,
replacement glyphs or out-of-canvas spans.

## Real Vietnamese visual regression batch

`visual_samples.json` pins eight development/validation pages and their
one-based original/subset page mapping. Artifacts and genuine authored handoff
tables are under `tmp/ocr-benchmark/luna-qa-20260905/`. Never substitute the old
`qa-sol*/table.jsonl` synthetic English expansions for these translations.

The batch found and reproduced false OCR paragraph breaks, clipped initial
glyphs in a text-PDF control, postal rows assigned to the following row, and
layout detection failure on high-resolution historical scans. The corresponding
regressions are in `test_ocr.py` and `test_inline_formula_layout.py`. Intermediate
folders are evidence, not approved deliverables; consult the latest visual
review before selecting a PDF.

Run the reusable structural audit after rebuilding a sample:

```powershell
.\.venv\Scripts\python.exe research\ocr-spike\audit_translation.py SOURCE.pdf OUTPUT.pdf --pages 2 --report NEW_RUN\audit.json --translations TABLE.jsonl --render-dir NEW_RUN\renders
```

It hashes source/output/table, checks every page/canvas, verifies unselected
pages remain raster-identical, searches for invalid glyphs/markers and reports
overlapping span candidates. It renders every page with Poppler when requested.
The structural gate is not a visual or translation-coverage certificate:
formula footnotes, protected headings and dense form grids may remain English.
Report translated content and protected content separately, even with zero
missing handoff mappings.

The latest preservation review also caught shifted protected form labels,
missing rules and changed dash lengths. Source glyph replay and valid PDF
array serialization are covered by an exact-raster form regression. IRS OCR
still refuses the dense form; an unchanged preservation control is **not** a
translated sample. Aged-paper cleanup now uses bounded blank-paper estimation;
the LOC samples retain faint cleanup-band boundaries and are not a claim of
flawless photographic restoration.

### Review checkpoint (2026-09-05)

`output/pdf/ocr-review-20260905/` collects the immutable-source pointers,
fixed translation tables, per-run audits and source/output page images.
NASA 2015/1999 use `final-r4`, validation NASA 2019/2020 use
`validation-final-r3`, LOC uses `final-r7`, and the text-formula control uses
`after-protection-fix/rerun`. The IRS `dash-fix` preservation control is kept
separately and contributes zero translated pages. Seven selected pages have
real Vietnamese output; this does not mean seven entire source documents
were translated. Only the selected page in each retained subset was processed.

Manual image review accepts the NASA column/address geometry and the text-PDF
formula geometry. LOC has legible Vietnamese, preserved stanza/line structure
and retained handwritten marks, but its paper-band boundaries remain a visual
limitation. IRS retains all 449 drawings and 298 line segments on page 1,
including exactly matching dash patterns. This validates preservation only,
not OCR table-cell translation. The two validation pages are NASA prose;
unseen historical scans and OCR formula/table translation are not certified.

## Ganong physiology stress set (2026-09-05)

The local 727-page Ganong source is locked by SHA-256 under
`tmp/ganong-qa-20260905/`. A 12-page derived set maps original pages
1, 2, 9, 13, 67, 107, 171, 279, 464, 613, 701 and 727 to a compact sample.
It covers an image-only cover, 51x3 and 36x4 tables, TOC, chapter opener and
bullets, dense figures, a clinical box, math/subscripts, an alveolar-gas
equation, three-column index and a `/Rotate 90` 21x25 landscape table.

The text-layer run uses 184 fixed Vietnamese handoff records. The structural
audit passes with equal page count/canvases, no forbidden markers and no spans
outside text-coordinate bounds. TOC, index, image-only and the landscape
appendix are preserved, not counted as translated. A separate raster of
original page 346 verifies safe two-column OCR; 13/13 segments translate after
the heading-rule false positive is removed. Raster versions of the 12 hardest
pages all fail closed because they contain grids, protected figures, formulas,
fragmented OCR, TOC/index structure or residual cover ink. This is the expected
safety result, not OCR coverage success.

A separate validation set uses original pages 50, 56, 78, 234, 332 and 451,
which were not used to choose or tune the Ganong fixes. It now carries a fixed
104-record Vietnamese handoff table under `validation/final-r2/`; the earlier
Google output stays layout-stress evidence only, not the translation oracle.
All 104 segments translate, none are dropped, and the structural audit passes
with equal page count and canvases, no forbidden markers, no residual mojibake
and no spans outside text coordinates. The three overlap candidates on original
page 451 are figure labels that intersect identically in the source, so they are
pre-existing geometry rather than translation damage. All six rendered pages
passed manual review for table, figure, clinical-box and two-column geometry.

Two limits are disclosed rather than hidden. Figure labels, the `Fat` row of
Table 26-4 and the sub-table under Figure 26-15 stay English because they are
protected regions, so this is partial page translation and not full coverage.
Seven captions carry a single source glyph that the layout model split off the
front of an English word; placed where the English word began it lands inside a
Vietnamese word and breaks it. Each was moved into a word that needs that
letter, except the `f` of "formula", which Vietnamese orthography has no home
for and which stays a visible trailing letter in the Figure 26-15 caption.

The first attempt at this set was worse than it looked. Its one finished chunk
was 33/35 mojibake and its `src` keys were damaged too, yet the handoff
validator reported no errors. Both the loader and the audit now detect that
class of damage; see [regressions.md](../../agent-knowledge/regressions.md).

On raster page 346, enhanced OCR took about 123 seconds versus 11.5 seconds for
standard (10.7x slower), while normalized text was only 99.96% similar and the
enhanced run introduced a whitespace split and a dash difference. Standard
remains the practical profile; enhanced is not presented as a quality upgrade.
