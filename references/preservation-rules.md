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

- Vietnamese text uses a `1.2` line-height multiplier.
- Windows uses Times New Roman when available; other environments use the downloaded Unicode font fallback.
- Long translations scale down before rendering, wrap at word boundaries, reduce line height when necessary, and shrink again only when the paragraph still exceeds its original box.
- Width fitting deducts first-line indentation from the available line budget so justified or indented translations cannot cross the source right edge.
- Extended bullets remain anchored, and vertically separated list items start new paragraphs.
- Symbol and Wingdings private-use bullets stay in their original embedded dingbat font instead of being emitted as missing glyphs by the macOS or Windows prose font.
- Quarter-turn text keeps its source orientation. Rotated table headings are translated and fitted along their logical baseline instead of wrapping one glyph per line.
- Reflected text matrices paired with negative font sizes are normalized from their baseline direction; this prevents technically mirrored but visually upright source text from being replayed upside down.
- Bold, italic, and bold-italic runs travel through the translator as validated style markers and use the matching Times New Roman face on Windows. Missing variants use synthetic weight/slant without discarding the style.

## Scan and source safety

- A rendered image covering more than half the page marks the page as scanned; translated text regions receive white backing rectangles so source pixels do not show through.
- The core does not perform OCR. A scan without an extractable text layer remains untranslated.
- Structural PDF repair uses a temporary copy. The source file is never overwritten.
- The translated PDF retains the source page canvas and page count; a requested page subset limits translation rather than removing pages.
- The app emits only the mono translation. Documents of 200 pages or 50 MB and larger skip whole-document font subsetting and use light PDF serialization to avoid a long, UI-blocking finalization pass.

## Known limits

Text inside an unmatched table or protected figure can remain in the source language. Complex embedded fonts, malformed content streams, or inaccurate layout-model classifications can also require manual review. Treat any substantial untranslated passage or visual defect as a partial result.
