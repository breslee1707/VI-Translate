# PDF preservation contract

The bundled core translates ordinary text while retaining document structures whose meaning depends on exact placement. These rules are product behavior and require regression coverage when changed.

## Formula and code protection

- Formula glyphs remain original PDF operators and are represented by placeholders only while surrounding prose is translated.
- Formula detection covers TeX and common math fonts plus monospace/code families such as Consolas, Courier, Menlo, Monaco, Inconsolata, Source Code, Fira Code, DejaVu Sans Mono, and Liberation Mono.
- Ordinary-font blocks made only of mathematical operators and identifiers are protected, as are stacked numbered terms such as `F1/b0` inside prose.
- Formula placeholders are validated after translation; a damaged or reordered placeholder leaves its segment untranslated instead of corrupting the PDF.
- Isolated formulas, formula captions, and figures remain protected layout regions.

## Tables

- A layout-model table is translated cell by cell only when PyMuPDF can match a cell grid to at least half of the detected table region.
- Visually merged cells are split into x-position clusters so natural-language labels can be translated without sending adjacent abbreviations, identifiers, numbers, or units to the translation service.
- Each reliable cell is reflowed within its own bounds while the source grid, fills, and borders remain unchanged.
- When a dense cell is shrunk vertically, its line spacing is recomputed from the final font size so the last line cannot spill into the next row.
- Cell translations may shrink to half the source font size. If text still cannot fit, that cell remains in the source language and the result is reported as partial.
- Tables without a reliable cell grid remain fully protected.

## Numbered-page structures

- Table-of-contents detection covers headings, dot leaders, box-drawing leaders, wide spacing, em/en spaces, standalone page numbers, Roman numerals, and pages dominated by text-number endings.
- Index detection covers an `Index` heading and term-to-page-number rows.
- Nomenclature, notation, symbol, abbreviation, and glossary pages preserve alternating symbol-definition structures.
- Reference and bibliography pages preserve numbered, bracketed, author-year, DOI, ISBN, ISSN, and URL-heavy citation structures.

These classifications preserve the complete page layout instead of reflowing numbers into translated prose.

## Vietnamese typesetting

- Vietnamese text uses a `1.2` line-height multiplier and is never typeset
  tighter than `1.10`. That floor is the measured ink of the output font:
  stacked tone marks reach `0.890` em above the baseline and dot-below vowels
  `0.210` em beneath it, against `0.695`/`0.210` for English. Below it, lines
  are drawn through each other.
- Windows uses Times New Roman when available; other environments use the downloaded Unicode font fallback.
- A character the output font cannot draw keeps its source glyph rather than
  being emitted as a missing one.
- Translated text keeps the colour the source drew it in. A paragraph takes the
  colour most of its own text uses; a colour that cannot be replayed safely
  falls back to black rather than to a guess.
- Bold and italic are read from the font descriptor before the font name, so the
  abbreviated Adobe Pro faces keep their slant.
- A line that stops well short of its column ends its paragraph, so indents and
  the break between paragraphs survive translation.
- A segment longer than the translation service accepts is refused and reported,
  never truncated and delivered as if it were whole.
- Long translations scale down before rendering, wrap at word boundaries,
  reduce line height to the language floor, then take up any clear vertical gap
  below the paragraph, and only shrink the font again when it still does not
  fit. Text left in the source language is fitted the same way, because it is
  redrawn in the output font rather than replayed from the source.
- Width fitting deducts first-line indentation from the available line budget so justified or indented translations cannot cross the source right edge.
- Extended bullets remain anchored, and vertically separated list items start
  new paragraphs. A list item takes its font size from its own body text: the
  oversized bullet and the tab set in the bullet's font do not decide it.
- Symbol and Wingdings private-use bullets stay in their original embedded dingbat font instead of being emitted as missing glyphs by the macOS or Windows prose font.
- Quarter-turn text keeps its source orientation. Rotated table headings are translated and fitted along their logical baseline instead of wrapping one glyph per line.
- Reflected text matrices paired with negative font sizes are normalized from their baseline direction; this prevents technically mirrored but visually upright source text from being replayed upside down.
- Bold, italic, and bold-italic runs travel through the translator as validated style markers and use the matching Times New Roman face on Windows. Missing variants use synthetic weight/slant without discarding the style.

## Scan and source safety

- A raster that is one flat ink, barely covers its own box, sits under source text and is line-sized is the shadow or glow of that text, and is removed. The translation replaces the glyphs it belonged to, so leaving it prints the source words smeared under the translated ones. A photograph, the solid shadow behind a photograph, and a watermark all fail at least one of those four tests.

- A rendered image covering more than half the page marks the page as scanned; translated text regions receive white backing rectangles so source pixels do not show through.
- OCR is off by default and translates nothing inside an image. With it enabled (`--ocr`, or the GUI checkbox), every image region is recognized, translated a paragraph at a time, and drawn back over the lines it read; the backing covers the box of each line that was read, never the paragraph around them and never the artwork, and each box is painted the colour sampled underneath it rather than white, with an ink chosen light or dark so it stays legible on either; the size comes from the box the text was read from, and a translation that will not fit its box even at half the size it was read at is left as pixels and reported.
- Text the engine already holds as real glyphs is never re-read from an image behind it, so a page with a picture under its prose is not translated twice.
- A page that carries an image but yields no translatable text is named as image-only in the result. A document with no translatable text anywhere is refused rather than handed over as a finished translation: with OCR off the refusal says to enable it, with OCR on it distinguishes a page nothing could be read from from one that was read and lost its translation later.
- A segment left in the source language is reported with the reason it was left: it did not fit at the smallest allowed size, the translation came back with damaged formula markers, or the engine failed.
- Structural PDF repair uses a temporary copy. The source file is never
  overwritten. Repair is triggered by the engine failing to rewrite the
  document, not by a second library's willingness to open it, and a document
  that still cannot be rewritten afterwards is reported rather than translated.
- The translated PDF retains the source page canvas and page count; a requested page subset limits translation rather than removing pages.
- The app emits only the mono translation. Output fonts are never subset: the
  content stream addresses glyphs by raw ID, so any pass that renumbers them
  repoints every translated character. Documents of 200 pages or 50 MB and
  larger also use light PDF serialization to avoid a long, UI-blocking
  finalization pass.

## Known limits

Text inside an unmatched table or protected figure can remain in the source language. OCR mode adds the recognizer's own limits on top of these: a faint, skewed, or decorative label is misread or skipped rather than translated, and what it reads is not checked against anything. On a low-resolution or heavily compressed scan it also drops the spaces between tightly set words, which the translator then reads as one token and renders badly; the recognized text is only as good as the scan, and a 200 dpi original reads far better than a 120 dpi one. Complex embedded fonts, malformed content streams, or inaccurate layout-model classifications can also require manual review. Treat any substantial untranslated passage or visual defect as a partial result.
