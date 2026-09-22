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

Each run returns a `TranslationReport`: the segments left in the source
language, why each was left, the image-only pages, and how much text was
translatable at all. Callers must report the reason rather than the count -
a fit failure, a damaged formula marker, and a dead connection each need the
user to do something different. The layout model's stride and class names
come from the onnxruntime session, so nothing imports `onnx` directly.

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
  A model table with no grid is divided by its text alignment instead
  (`text_aligned_table_cells`): columns are white-space gutters wider than
  twice the table's own word spacing that hold text on at least two rows, and a
  cell ends at a wider line gap, a rule, a bullet, or a change of left edge. A
  table that does not divide that way stays protected, and a gridless cell that
  mixes text sizes (a subscripted variable) keeps its source glyphs.
- Fully protected blocks replay source glyph matrices, font size, horizontal
  scaling and rise without prose fitting. Their horizontal grid rules stay in
  the source stream. Array operands use PDF whitespace syntax, not Python list
  commas, so retained dash patterns do not change during serialization.
- A wholly protected page keeps its original content stream. Rebuilding every
  glyph is unnecessary and can rotate the content a second time when the page
  uses `/Rotate 90`; page-level rotation and landscape tables stay exact.
- Quarter-turn text uses logical baseline orientation. Reflected matrices used
  with negative font sizes are normalized before classification. Only an
  isolated single rotated line is rebuilt; a rotated line with a neighbour
  alongside it, a continuation on its baseline or protected glyphs inside it
  (a sideways table) replays its source glyphs.
- Symbol/Wingdings private-use bullets remain source glyphs in their embedded
  dingbat font; prose fonts must not receive those code points.
- Text fitting accounts for first-line indentation, final glyph ink, formula
  offsets, and cell borders. The minimum translated size is 50% of source;
  unsafe overflow falls back to source text and records a partial result.
- A wrap back to the left edge starts a new paragraph only when the gap exceeds
  1.5 em and 1.25 times the region's own line pitch, or when the new line
  opens with a heading or item number. A wrapped paragraph whose source pitch
  is 1.4 em or more keeps that leading, and the fit loop may still tighten it.
  Single-spaced documents split and lead exactly as before.
- Leading is never compressed below `min_line_height_for_language`, measured
  from real glyph ink (`vi` = 1.10 em). A paragraph short of room reduces
  leading to that floor, then borrows the clear gap below it
  (`available_height_below`), and only then shrinks the font.
- Output fonts are never subset. `raw_string` writes glyph IDs into Identity-H
  fonts, so renumbering them silently repoints every translated character.
  A glyph-stable alternative would be a fontTools subset with `retain_gids`.
- A paragraph takes its size from the first characters that draw ink, so an
  oversized bullet and its tab cannot set the size for a whole list item.
- With OCR off, scanned image-only pages are not read. With OCR standard or
  enhanced, only explicitly owned, structurally safe prose is recognized and
  reflowed; unsafe regions remain source pixels and make the result partial.
  A page carrying an image that yields no translatable segment is reported as
  image-only, and a document with no translatable segment at all is refused
  rather than delivered as a translation of nothing. Note the two questions:
  `is_scanned_page` (one image over half the page) drives backing rectangles;
  `page_has_image` (any image at all) drives the image-only report, because a
  scanner routinely emits one page as dozens of small tiles.
- The experimental OCR path is opt-in at the CLI (`--ocr standard|enhanced`)
  and in the desktop GUI, which starts at off: OCR is slow, and a scanned book
  it reads becomes thousands of Google segments. The packaged smoke test loads
  both OCR profiles.
  It adds an invisible sidecar only for image-only pages, never paints white
  backing rectangles on those pages, and replaces the scan image only after a
  safe inpainting pass. DocLayout inference is capped at 1024 pixels while OCR
  keeps its recognition DPI; using a 4445-pixel layout input on old book scans
  detected fragments instead of paragraphs. Dense and multi-column prose is
  allowed only with explicit region ownership carried into the converter's
  fit bounds. Boundaries tolerate small model-edge errors, never a line across
  a column gutter. Each OCR glyph uses its centre to choose its owned row:
  the descender origin can lie inside the next row's padded ink box.
- OCR paragraph breaks come from source ink gaps, indentation and raster bullet
  positions, not sidecar font-size heuristics. Postal-address rows and strongly
  evidenced numbered verse retain physical lines. Adjacent OCR pieces on one
  baseline are joined before region ownership. OCR prose uses one embedded
  Unicode font family for accented and unaccented characters.
- Two isolated textbook heading underlines are not a form grid. OCR closes the
  page for three horizontal rules, two vertical rules, or a crossing pair;
  detected table/formula regions remain independently protected.
- Outer running headers/footers and standalone bullets remain source pixels.
  So do item numbers and bullets that introduce text on their row; each starts
  its item's paragraph and none counts as a recognition fragment. A bullet the
  recogniser glued to its text is split off at its ink gap.
  Interior protected structures, dense grids, formula/numeric content, damaged
  characters, ambiguous ownership and residual ink still preserve the page and
  report partial. Safe cleanup uses Navier-Stokes inpainting; line-mask analysis
  is cropped locally to avoid repeatedly processing the full scan. The default
  `--ocr off` path stays opt-out; the generic glyph-edge recovery also benefits
  text PDFs whose layout model clips the first letter of a word.
- On aged plain-paper scans with proven ownership, full approved line bands
  can use a weighted blank-paper estimate instead of glyph-only inpainting,
  which retains JPEG checkerboards. Sampling expands only up to a bounded
  radius and uses one scale per page to avoid contour seams. Protected pixels
  and margins remain exact; insufficient support falls back to normal cleanup.
  This is not restoration of arbitrary artwork: faint band boundaries on old
  paper still require visual review and must not be described as flawless.
- Handoff JSONL repairs only unambiguous punctuation mojibake (range/em dashes,
  copyright/registered/degree/plus-minus/micro signs) in both keys and values.
  Vietnamese letter sequences are never guessed. A record whose damage survives
  that repair is dropped with a warning instead, because rendering it would put
  unreadable text on the page while every gate reported success. Detection
  re-encodes the whole value through Windows-1252 and back; searching for marker
  characters is wrong, since A-circumflex and A-tilde are Vietnamese letters.
  Test the raw record, not the repaired one: restoring a dash also restores a
  byte that cannot begin UTF-8, which hides the damaged letters around it. Short title lines ending in
  a lone token such as `2` receive a bounded, gentle font reduction to balance
  the wrap without affecting ordinary body paragraphs.

## Colour and Emphasis

The colour in force is captured as the source wrote it and replayed in front of
each run, the way BabelDOC carries colour: reducing everything to RGB would
guess at a conversion of an ICCBased or Separation space the document never
asked for. One entry is kept per piece of colour state, not per operator, since
`g`, `rg`, `k`, `sc` and `scn` all write the same slot. `sc`/`scn` are never
replayed without a space that explains their operands - a device space is
supplied from the operand count, and a colour that still cannot be explained is
dropped so the run falls back to black rather than to whatever DeviceGray makes
of four CMYK components. An ExtGState travels with the text only when it
carries no soft mask, transfer function or partial alpha. A paragraph takes the
colour most of its own ink uses, because a colour change cannot travel through
the translator the way a style marker can.

Emphasis comes from the font descriptor's own flags before the font name, since
the Adobe Pro families abbreviate the slanted face as `-It`. Boldness also comes from the
name: descriptors seldom mark it, and Google Docs exports carry `Flags 6` on
`TimesNewRomanPS-BoldMT`. Regions of CJK text keep the 1.5 em gap rule for
wrapped lines.

## Translation Service

Google's free `/m` endpoint is meant for people and blocks a network that uses
it like a batch service: a 302 to `www.google.com/sorry/`, HTTP 429, a CAPTCHA
page, no `Retry-After`. Recovery time is unknown; do not promise the app's
cooldown will lift Google's block. Up to 0.3.0 every segment was its own
request, four at a time, with up to eight total attempts per segment.

- A page travels together. `GoogleTranslator.translate_many` joins a page's
  uncached, distinct segments with blank lines, up to 5000 percent-encoded
  bytes and 50 segments per request (a Vietnamese letter can take nine bytes of
  the URL). Google translates each line on its own and keeps the breaks - what
  goslate and Calibre's Ebook Translator rely on to batch it; the `/m` page
  itself has not yet been checked from an unblocked network - so the answer
  must hold exactly one non-empty line per segment, broken by newlines or
  `<br>`. Any other count, or an HTTP 400, halves the batch; after three such
  misses with no batch ever right the document sends segments one by one. A
  segment holding a line break travels alone. On nine stubbed corpus documents
  this cut requests 5-21 fold (RISKS 144 -> 29, a 41-page textbook excerpt
  644 -> 45, the OCR'd ERP scan 207 -> 14) and every page rendered identically.
- `RequestPace` lets one request out at a time, at least 1 s after the last
  answer, for every translator in the process.
- `NetworkBlock` stops at the first refusal. The rest of the document, the rest
  of the queue and the next run (the moment is kept in
  `~/.cache/pdf2zh/google-block.json`) are refused without a request, and one
  request per 10 minutes checks whether the block has lifted.

The final block check and refusal verdict are inside the pacing lock, so a
waiting caller cannot send after another caller sees 429. CAPTCHA HTML is
also detected on HTTP 200, unless it is text inside a real translation result.
The converter now aborts on a block or exhausted service outage; it does not
lay out the rest of a book or publish a misleading partial PDF. The desktop
queue pauses with E-NET-08/E-NET-09 and a Continue action; remaining files stay
queued. Successful segments remain in the existing persistent cache. Retrying
repeats local PDF/OCR processing but sends only uncached segments. Known active
cooldowns are checked before OCR/model work, without reserving a probe.

GUI stages cover OCR page progress, layout, service waits and PDF export.
Page progress counts completed pages, including protected pages. OCR engines
load only when an image-only page actually needs recognition. Local bounded
`translated/translation-service.log` records request/cache/batch counters, not
document text or query URLs. Empty/unparseable responses get three attempts
for the document, then stop; ordinary HTTP access errors also stop instead of
repeating for every segment. Cache entries are checked for formula/style tags
before reuse and before saving new translations. A `<br>` in a single answer
no longer discards the rest of that paragraph.

A dead connection or a 5xx is an outage, not a block. `OutageBackoff` pauses
every worker together (5 s, doubling to 60 s), gives up after 120 s and still
lets one request per pause check whether the service is back. A glitch in one
answer gets three quick attempts. Nothing resends a segment the service refused
(too long, HTTP 400). Never try to get past the CAPTCHA, or rotate proxies,
domains or keys to dodge a block. Finished segments are cached, so a later run
of the same file sends only what is still missing.

## Large Documents

The app requests mono output only; do not construct the unused interleaved
dual-language document. A PDF is large at 200 pages or 50 MiB. Large PDFs use
light serialization (`garbage=1`, no recompression/object streams) because
aggressive cleanup can hold the GIL for tens of seconds after page progress
reaches 100%. Font subsetting is off for every size, not just large documents.

## Damaged Sources

`pymupdf_can_round_trip` decides whether a document needs repair, because
pikepdf opens damage that MuPDF only refuses on write. The repaired copy is
re-checked; a document that still fails is reported, never silently translated.

The product-level authority is
[`references/preservation-rules.md`](../references/preservation-rules.md).
