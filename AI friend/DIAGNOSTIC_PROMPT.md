# Diagnostic & Refactoring Prompt for PDF Math Extraction & Layout Preservation (`PdfLayoutPreserver.kt`)

## Context
We are developing an Android app (`VI-Translate`) that translates PDF document text (such as high school math exam papers) into Vietnamese while attempting to preserve original page vector graphics, diagrams, and mathematical formatting.

All files are located in this folder:
- `PdfLayoutPreserver.kt` (Main layout preservation & text extraction pipeline)
- `FormulaPlaceholder.kt` (Placeholder encoder/restorer for translation)
- `GoogleTranslateEngine.kt` (Translation API integration)

---

## Current Architecture & Pipeline Summary in `PdfLayoutPreserver.kt`

1. **Extraction (`PageTextCollector: PDFTextStripper`)**:
   - Overrides `writeString()` to collect glyphs (`TextPosition`) into `TextBlock` clusters by horizontal spacing & Y-baseline alignment.
   - Extracts character position (`xDirAdj`, `yDirAdj`), font size, ascent, descent.
   - Uses `resolveTexFallback()` to map non-standard TeX/AMS PostScript font glyphs (CMSY, MSAM, MSBM) to Unicode.

2. **Fraction Merging (`collapseVerticalFractions`)**:
   - **Pass 1**: Merges explicit `numerator / bar / denominator` triples (where bar matches `[-_–—―─═]{2,}`).
   - **Pass 2**: Merges bar-less stacks (e.g., TeX `\frac` where PDF omits an explicit bar character) if both top & bottom blocks match `SHORT_MATH_PATTERN` (≤8 chars, digits/variables/operators).

3. **Line Run Grouping (`groupIntoLineRuns`)**:
   - Groups vertically aligned `TextBlock`s within `0.35 * fontSize` Y-delta into horizontal lines.
   - Merges horizontal runs separated by `gap <= 1.5 * fontSize`.

4. **Math Filter & Translation (`isPureMathOrFormula`)**:
   - Checks if block is pure math to skip translation and avoid API corruption.
   - Replaces placeholders using `FormulaPlaceholder`, calls `GoogleTranslateEngine`, and restores placeholders.

5. **Page Stripping & Re-rendering (`stripTextFromPage` / `drawTextWithWrapping`)**:
   - Strips original PDF text streams (`BT...ET`) to keep underlying vector diagrams clean.
   - Draws translated text using bundled `NotoSerif` font with `sanitizeForFont()`.

---

## The 3 Key Bugs Remaining (Observed in Exam Test PDF Screenshots)

### Bug 1: TeX Radical Bars (`\overline` / `\sqrt`) Rendering as Floating Overlines (`¯`)
- **Symptom**: In expressions like $\sqrt{4-x^2}$ or $\sqrt{3}$, a floating macrons/overlines character (`¯` or `‾`) appears detached above numbers (e.g. `y = x+ 4- x²` with `¯` floating above `4-x²`, or Option `B. 2 2 ¯`, Option `B. a 3 ¯ / 2`).
- **Root Cause**: In TeX-generated PDFs, square roots are drawn as two separate objects: a radical tick `√` and a horizontal line stream `\overline`. PDFBox extracts the horizontal rule glyph as an overline Unicode character (`¯`) or as a short text block. Because it sits slightly above the main line baseline, `PageTextCollector` treats it as a raised cluster or separate block, rendering it as a floating `¯` in the final output.

### Bug 2: Fraction Disruption & Vertical Baseline Overlaps in Tables / Math
- **Symptom 1 (Table Overlap in Question 2)**: In `f(x)` sign variation tables, `f'(x)` in the header sits slightly above or below the surrounding text line, causing PDFBox to group `f'(x)` into a separate line or overlap text: `"the f'(x) sign follows derivative f"`.
- **Symptom 2 (Broken Inline Fractions in Question 6)**: In `Q6`, the expression $\frac{1}{1-x}$ gets extracted as an isolated block `1/1-x` and inserted awkwardly into the middle of sentence fragments: `"Family of primitives of the function 1/1-x f(x)sin(2x) là"`.

### Bug 3: Mixed Language / Translation Wording Mangling
- **Symptom**: Sentence fragments like `"Family of primitives of the function 1/1-x f(x)sin(2x) là"` contain mixed English and Vietnamese (`là`).
- **Root Cause**: `groupIntoLineRuns` breaks a line into multiple horizontal runs when a math block or fraction creates a spatial gap (`gap > 1.5 * fontSize`). The English fragment `"Family of primitives of the function"` is translated to English/Vietnamese, while an adjacent fragment containing math is skipped by `isPureMathOrFormula`. When re-drawn, un-translated and translated blocks are stitched together into hybrid sentences with broken grammar.

---

## Desired Solutions & Questions for Diagnosis

1. **How to handle TeX radical overlines (`¯` / `‾`)**: Should we explicitly detect and filter out standalone overline/macron characters (`\u00AF`, `\u203E`) that sit above math expressions, or absorb them into `sqrt(...)` representation during `writeString()`?
2. **Table & Multi-Column Detection**: How can we prevent PDFBox from scrambling horizontal cell boundaries in math tables, so that table cells retain their grid coordinates instead of merging with surrounding paragraph text?
3. **Line Run Splitting**: How should `groupIntoLineRuns` handle mixed prose + inline math so that a full sentence (including embedded math expressions) is translated as a single cohesive unit rather than being chopped into separate fragments?

Please analyze `PdfLayoutPreserver.kt` and propose a robust refactoring plan to fix these 3 issues!
