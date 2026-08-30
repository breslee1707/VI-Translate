# PDF Engine Architecture and Invariants

## Pipeline

1. `scripts/translate_pdf.py` validates a text-based PDF and stages output.
2. `pdf2zh/high_level.py` loads fonts/model, predicts layout, matches tables,
   detects preserved structures, patches pages, and serializes the mono PDF.
3. `pdf2zh/converter.py` groups glyphs into paragraphs, carries formulas and
   styles through translation markers, reflows text, and emits PDF operators.
4. `pdf2zh/translator.py` validates placeholders/style markers and caches only
   safe translations.
5. `pdf2zh/pdfinterp.py` retains source graphics while replacing selected text.

## Preservation Invariants

- Formula glyphs and rules retain source fonts and relative geometry. Ordinary
  fonts can still contain formulas, so detection also uses operators and
  stacked-token geometry.
- `{vN}` is the internal formula placeholder. Translators receive safe
  `<bN></bN>` tags. Missing or reordered formula tags reject the translation.
- `<s1>`, `<s2>`, and `<s3>` carry bold, italic, and bold-italic runs. Pairs may
  move with their phrase but must stay balanced and non-cross-nested.
- Tables translate per reliable cell only when the model region and
  `PyMuPDF.find_tables()` overlap by at least 50%. Grid, fill, and border
  operators remain source content. Unreliable tables stay protected.
- Quarter-turn text uses logical baseline orientation. Reflected matrices used
  with negative font sizes are normalized before classification.
- Symbol/Wingdings private-use bullets remain source glyphs in their embedded
  dingbat font; prose fonts must not receive those code points.
- Text fitting accounts for first-line indentation, final glyph ink, formula
  offsets, and cell borders. The minimum translated size is 50% of source;
  unsafe overflow falls back to source text and records a partial result.
- Scanned image-only pages are not OCRed. Source pixels under translated text
  receive backing only where required by the scan path.

## Large Documents

The app requests mono output only; do not construct the unused interleaved
dual-language document. A PDF is large at 200 pages or 50 MiB. Large PDFs skip
whole-document font subsetting and use light serialization
(`garbage=1`, no recompression/object streams) because aggressive cleanup can
hold the GIL for tens of seconds after page progress reaches 100%.

The product-level authority is
[`references/preservation-rules.md`](../references/preservation-rules.md).
