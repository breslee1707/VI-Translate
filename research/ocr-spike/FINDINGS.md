# OCR mode — spike findings

Research only. Nothing in `pdf2zh/`, `app/`, `scripts/`, `requirements*.txt` or
`app.spec` was modified. Every number below comes from running the shipping
`translate_pdf()` unchanged, offline, through `HandoffTranslator`.

Date: 2026-08-31 · branch `research/ocr-spike` · Python 3.12.10 · Windows 11

---

## Verdict in one line

The sidecar approach works and the recogniser is already in the dependency
tree. But on a real scan every page is delivered wrong: the pages the backing
rectangles miss get the translation printed on top of the original, and the
pages they reach lose their background. Sampling a fill colour was measured and
rescues under half the damage. **Not shippable without replacing the
cover-with-a-rectangle strategy outright — the tractable route is erasing the
original text from the raster and drawing onto the cleaned page.**

---

## What was tested

| Phase | Question | Result |
| --- | --- | --- |
| A | Does DocLayout survive a scanned page? | **PASS** |
| B | Can a perfect OCR result get a clean PDF out of the unmodified pipeline? | **PASS on text, FAIL on ink** |
| C | How good is the recogniser, and what does it cost? | Good on prose, fails on formulas; ~0 new dependencies |

---

## Phase A — the layout model is not the problem (R1 retired)

The cheapest kill signal, and it needs no OCR engine. If DocLayout-YOLO called
a scanned page one big `figure`, every recovered glyph would become a `{vN}`
formula, `translatable_segments` would reach zero, and
`scripts/translate_pdf.py:413` would refuse the document the tool had just
OCR'd.

It does not happen.

| Corpus | Pages | All-protected pages | Protected-agreement |
| --- | --- | --- | --- |
| Born-digital, rasterised at 200 DPI | 24 | **0 (0.0%)** | median 0.999, min 0.700 |
| Genuine scan (`tk.pdf`) | 4 | **0 (0.0%)** | 5–22 detections/page, 3–23% protected |

The real-scan row is the one that matters: DocLayout finds text regions on a
genuine scan perfectly well. Kill threshold was >10%; actual is 0%.

Two round-trip pages dropped to 0.86 and 0.70 agreement (conveyor p13, p15),
where rasterising made the model protect *more* area — worth watching, not a
blocker.

Reproduce: `python research/ocr-spike/layout_probe.py --born-digital … --real-scan …`

---

## Phase B — the pipeline digests a sidecar, and then paints over the page

### What works

An invisible text layer (`insert_text(..., render_mode=3)`, base-14 `helv`,
one font size per line) written over a rasterised page and fed to the
**completely unmodified** `translate_pdf()`:

| Sidecar | Refused? | Untranslated | Pages/canvas | Markers | Time |
| --- | --- | --- | --- | --- | --- |
| conveyor, oracle-exact | no | 0 | match | clean | 8.1 s / 6 pp |
| conveyor, oracle-bbox | no | 0 | match | clean | 7.3 s / 6 pp |
| finance, oracle-exact | no | 0 | match | clean | 7.5 s / 6 pp |
| finance, oracle-bbox | no | 0 | match | clean | 7.4 s / 6 pp |

No refusal fired. No `{vN}` / `<bN>` / `<sN>` / `U+0000` / `U+FFFD` survived.
`is_scanned_page` fires on all six sidecar pages, so the backing rectangles do
get painted. The `oracle-bbox` variant — which throws away the true baseline
and font size and re-derives both through the same fitting code a real engine
would use — scores the same as `oracle-exact`, so **the fitting maths is
sound**.

That clears blocker B1. The sidecar shape is viable.

### What breaks — and the automated gate could not see it

`agent-knowledge/validation.md` steps 1–3 reported *clean* for every row above.
Steps 4–5 say render every page and look. Doing so shows the tinted formula
panels wiped to white and the formulas replaced by fragments (`22`, `mB 2`).

Quantified against a control — the same six pages, born-digital, through the
identical pipeline:

| Run | Mean whited-out | Mean colour lost | Worst page |
| --- | --- | --- | --- |
| Control (born-digital) | 0.7 % | **0.0 %** | — |
| Sidecar, conveyor (tinted panels) | 15.7 % | **58.6 %** | p3: 96.7 % |
| Sidecar, finance (plain white) | 4.1 % | **0.0 %** | — |

The mechanism, not a guess: on a scanned page the background *is* the raster.
`pdf2zh/converter.py:831-849` paints an opaque white rectangle behind every
translated paragraph, and everything living under it — tint panels, table
fills, rules — goes with it. A born-digital page keeps them because they are
vector fills replayed from `ops_base` and `is_scanned_page` is false.

**The defect is conditional.** A plain black-on-white scan loses 4.1 %, which
is just the intended cover-and-redraw. A document with coloured panels loses
most of its colour.

**This is a text-blind failure.** It is the strongest argument in the spike for
keeping steps 4–5 of the PDF gate human.

---

## Phase C — RapidOCR measured

Scored two ways, because a single CER conflates two unrelated failures.
Page-level joined-text CER punishes an engine for emitting a two-column page in
a different order; geometric line matching separates recognition from ordering.

### Order-independent (the honest numbers)

| Document | matched-CER | missed | order-tau | spurious |
| --- | --- | --- | --- | --- |
| conveyor (technical, formulas) | **6.15 %** | 3.6 % | 0.978 | 19 |
| finance (prose + TOC) | 11.91 % | 33.8 % | 0.956 | 52 |

Per page, the spread is the whole story:

| Page | matched-CER | missed | What is on it |
| --- | --- | --- | --- |
| conveyor p1 | **1.7 %** | 0.3 % | prose |
| conveyor p2 | **1.9 %** | 0.7 % | prose + tables |
| conveyor p6 | **2.7 %** | 4.3 % | prose |
| conveyor p4 | 8.1 % | 0.6 % | mixed |
| conveyor p3 | **27.2 %** | 24.0 % | formula panels |
| finance p4 | **0.2 %** | — | dense prose |
| finance p2 | 77.5 % | — | TOC with dot leaders |

Three conclusions:

1. **Reading order is solved.** Kendall tau 0.956–0.995. The column-split pass
   in `ocrjson.reading_order` holds up, so the `converter.py:553` no-sort
   hazard is handled.
2. **Prose recognition meets the bar.** 0.2–2.7 % CER on prose pages, against a
   ≤2 % ship threshold. Borderline-pass on clean rasters.
3. **Formulas are the failure mode.** The formula page is 27 % CER with 24 % of
   characters missing. That is the same page the backing rectangles wrecked, so
   formula-dense documents fail twice over.

The finance `missed 33.8 %` is inflated by a table of contents whose dot
leaders the answer key counts and the recogniser sensibly ignores — a scoring
artefact, not an engine defect.

### Speed

2.75–4.52 s/page steady-state at 200 DPI, single-threaded, plus the ~1.2 s/page
the pipeline already costs. Against a 3 s target and a 6 s hard ceiling this is
**borderline**: acceptable at the low end, over target at the high end.

### The Chinese-model space problem is real but narrower than expected

The wheel ships only `ch_PP-OCRv4_rec`, trained on a script without spaces. It
returns `CHAPTER2.LITERATUREREVIEW` for headings — but body prose comes back
correctly spaced (`the financing of a company's debt and equity is known as`).
The damage is confined to all-caps runs. `CER ≈ CER-nospace` (27.67 % vs
27.85 %) confirms spaces are not the main error source.

Getting the English recogniser means downloading `en_PP-OCRv3_rec` +
`en_dict.txt` separately, which costs the offline-install property. **Not yet
tested** — see open items.

---

## Correction: the OCR engine is already a dependency

An earlier reading of this repo said `.venv` had been contaminated by someone
installing RapidOCR, and that `.venv` should be rebuilt before the next
release. **That was wrong**, and it would have sent you on a pointless cleanup.

`babeldoc==0.2.33`, pinned at `requirements.txt:1`, declares:

```
rapidocr-onnxruntime>=1.4.4
opencv-python-headless>=4.10.0.84
```

So `rapidocr-onnxruntime`, `pyclipper` and `shapely` arrive transitively in
every clean install, and `opencv-python-headless` is pulled by babeldoc itself
while `requirements.txt:29` pins full `opencv-python`. The double-opencv
install is produced by the pinned requirements as they stand, not by anyone's
stray `pip install`. `scipy`/`scikit-image` likewise come from babeldoc.

The comment at `requirements.txt:19-21` says babeldoc "requires the full
package, and installing both just shadows one with the other" — but babeldoc's
own metadata asks for the headless build, so both are always present. Worth a
look independently of OCR, but it is a pre-existing condition, not a
regression, and not something this spike created.

### What that means for cost

| | |
| --- | --- |
| New entries needed in `requirements.txt` | **none** |
| RapidOCR + pyclipper + shapely on disk | 19.4 MB |
| onnxruntime + opencv it reuses | 154.6 MB, **already bundled** |
| Present in `dist/PDFTranslate` today | **no** — nothing imports it, so PyInstaller drops it |
| Bundle cost to enable | ~19–20 MB on 455 MB = **+4.3 %**, plus a `hiddenimports` entry in `app.spec` |

Against a 60 MB budget this is comfortable, and it needs no dependency
decision at all — only an import.

---

## Against the decision rubric

| Gate | Threshold | Actual | |
| --- | --- | --- | --- |
| B1 oracle produces a clean PDF | must pass | passes | ✅ |
| B2 refusal fires on OCR'd pages | ≤2 % | 0 % | ✅ |
| B3 markers in output | 0 | 0 | ✅ |
| B6 seconds/page | ≤6 s | 2.75–4.52 s | ⚠️ over the 3 s target |
| B7 torch / external binary | none | none | ✅ |
| CER, prose | ≤2 % | 0.2–2.7 % | ⚠️ borderline |
| CER, formula pages | ≤2 % | 27.2 % | ❌ |
| Numeric-token recall | ≥99 % | 33–86 % | ❌ |
| Bundle growth | ≤60 MB | ~20 MB | ✅ |
| **Background preservation** | not in the original rubric | **58.6 % colour lost** | ❌ |

The last row is the finding the rubric did not anticipate, and it is the one
that decides the shape of any shipped mode.

Numeric recall of 33–86 % is far below the 99 % bar and deserves its own look:
in a technical manual a wrong digit is worse than a missing sentence.

---

## Recommendation

**Do not ship OCR as a mode yet.** Session 2 moved this from "narrow it" to
"the covering strategy has to change first". In order:

1. **Drive the backing rectangles off `image_only_pages`, not
   `is_scanned_page`.** Correct on all sixteen pages tested, no false
   positives, no new heuristic. This alone fixes the overprint on three of
   tk.pdf's four pages, and it is a small change.
2. **Replace the white rectangle before shipping anything.** Sampling a flat
   colour rescues only 42 % of affected regions, so it is not the answer.
   Erasing the recognised glyphs from the raster and drawing onto the cleaned
   image is what the measurements support. This is the real cost of the
   feature and it should be scoped before any UI work.
3. **Refuse formula-dense pages rather than OCR them**, reporting why, the way
   `report.image_only_pages` is already surfaced at `translate_pdf.py:427-430`.
4. **Keep the current refusal as the default** for everything not explicitly
   opted in.

Steps 1 and 3 are cheap. Step 2 is the feature's actual price, and until it is
paid, an OCR mode delivers damaged pages on anything but plain white paper.

---

## Standing defect found on the way: tiled scans get no backing rectangles

`tk.pdf`, the first genuine scan to hand, is tiled — 10–27 image blocks per
page, zero text:

| Page | Image blocks | Largest tile | `is_scanned_page` |
| --- | --- | --- | --- |
| 1 | 12 | 27.2 % | **False** |
| 2 | 27 | 84.6 % | True |
| 3 | 10 | 14.4 % | **False** |
| 4 | 13 | 7.3 % | **False** |

`rules.py:398-411` needs one image covering >50 % of the page. On 3 of 4 pages
`scanned_pages` stays empty, so no backing rectangles are painted at all and a
translation would print Vietnamese directly on top of the original English.

`rules.py:386-395` already documents that scanners "routinely emit a page as
dozens of tiles" — the codebase knows, but only `page_has_image` acts on it.
Any OCR mode must use tile-aware detection, not `is_scanned_page`.

This also exposes the ceiling of the synthetic corpus, which produces exactly
one image per page and can never surface this.

---

## Session 2 — a real scan end to end, and whether the fixes work

### The real scan fails both ways at once

`tk.pdf` run through OCR → sidecar → unmodified pipeline. Four pages, and not
one of them is delivered correctly.

| Page | Backing rects | ink before → after | whited out | ink added | colour lost |
| --- | --- | --- | --- | --- | --- |
| 1 | **none** | 0.059 → 0.069 | 0.000 | 0.010 | — |
| 2 | painted | 0.074 → 0.026 | 0.060 | 0.012 | **74.7 %** |
| 3 | **none** | 0.054 → 0.065 | 0.000 | 0.011 | — |
| 4 | **none** | 0.030 → 0.032 | 0.000 | 0.002 | — |

Pages 1, 3 and 4 remove *nothing* and add ink: the translation is printed
straight over the original. Rendering confirms it — every line reads twice,
the original with diacritics and the OCR'd version without, offset by a few
points. Page 2, the only one the backing rectangles reached, loses three
quarters of its colour instead.

So the two defects are not alternatives. On a single ordinary scan, whichever
branch a page takes, it is wrong.

### Vietnamese diacritics do not survive the Chinese recogniser

`tk.pdf` is a Vietnamese document, and `ch_PP-OCRv4_rec` strips the tone and
vowel marks wholesale — `Người mua` comes back as `Ngudi mua`, `Nhập nội dung
cần tìm` as `Nhap noi dung can tim` — at confidence 0.955–0.974. High
confidence, wrong text.

This matters less than it looks for the product, whose job is foreign →
Vietnamese, so the source is rarely Vietnamese. It matters a lot for a
re-translation or verification workflow, and it is one more reason the English
recogniser has to be measured before any decision.

*(That file is a personal purchase receipt containing account credentials. It
is used here only for structural measurement — tiling, geometry, ink — and its
content appears in no report or artifact.)*

### Fix 1 — tile-aware detection: superseded by a simpler and better rule

The proposal was to replace "one image covering half the page" with the same
question asked of the tiles' union. Measured, it is not enough:

| Document | `is_scanned_page` | union-coverage | image-only |
| --- | --- | --- | --- |
| tk-scan (tiled) | 1/4 | 2/4 | **4/4** |
| conveyor raster | 6/6 | 6/6 | **6/6** |
| born-digital control | 0/6 | 0/6 | **0/6** |

Union coverage rescues only tk p1 (0.67); pages 3 and 4 cover 0.21 and 0.10 and
stay missed, because those pages genuinely are sparse. No threshold on coverage
separates "sparse scan" from "born-digital page with a figure".

**The right discriminator is not geometry at all.** A page that has an image and
yields no text is a page the OCR mode just recognised — the pipeline already
computes exactly this as `image_only_pages` at `converter.py:437`. Driving the
backing rectangles off that instead of `is_scanned_page` is correct on all
sixteen pages tested, with no false positives, and needs no new heuristic.

### Fix 2 — sampling the background: insufficient, and this is the blocker

The proposal was for the backing rectangle to sample the colour it is about to
cover rather than filling white. Measured over 637 recogniser line boxes:

| | |
| --- | --- |
| Regions sitting on a non-white background | 269 / 637 = **42.2 %** |
| Of those, flat enough for one solid fill | 113 / 269 = **42.0 %** |

(Anti-aliased glyph edges bias this measurement pessimistically; the figures
above already exclude the darker half of each region's surviving pixels to
compensate. Without that correction the second row reads 28.3 %.)

So a flat fill rescues well under half of the damaged regions. The rest sit on
gradients, rules and picture content that no single colour reproduces.
**Recommendation 1 from session 1 does not survive measurement.**

What the numbers point to instead: do not paint over the page at all. Since the
raster is in hand and the recogniser has already located every glyph, the
tractable route is to erase the original text *from the image* once per page —
inpainting the stroke pixels with the surrounding background, which the already
bundled OpenCV can do — and then draw the translation onto that cleaned raster.
That is a substantially bigger change than sampling a fill colour, and it is
the honest price of OCR on anything but plain white paper.

## Open items

- English recogniser (`en_PP-OCRv3_rec`) not yet measured — needs a download.
- Tesseract not measured: no binary on this machine, and installing one needs
  admin rights.
- `onnxtr[cpu]` (word-level ONNX geometry) not measured.
- Degradation sweep (B2) not run, so the rubric thresholds are still priors
  rather than derived.
- Only 12 corpus pages + 4 real-scan pages. Enough to find blockers, not enough
  to certify an engine.
- Geometry scoring (paragraph-box IoU vs oracle) not yet implemented; the
  visual metric stands in for it.

---

## Harness

`research/ocr-spike/` holds source only. Every generated byte goes to
`tmp/ocr-spike/`, enforced by `paths.work_path()` and already ignored by
`.gitignore:6`.

| File | Does |
| --- | --- |
| `paths.py` | The only writable root; refuses paths escaping it |
| `survey.py` | Sorts PDFs into born-digital / real-scan / mixed |
| `layout.py` | Faithful replica of `high_level.py:264-305` class map |
| `layout_probe.py` | Phase A |
| `ocrjson.py` | Shared engine-neutral schema + reading order |
| `oracle.py` | Perfect OCR from a born-digital text layer |
| `make_corpus.py` | Rasterises to image-only PDFs, keeps text as truth |
| `sidecar.py` | The invisible text layer under test |
| `run_ocr.py` | Engine adapters (RapidOCR, Tesseract) |
| `run_pipeline.py` | Two-pass handoff run through unmodified `translate_pdf()` |
| `score_text.py` | Page-level CER/WER + numeric recall |
| `score_lines.py` | Order-independent matched-CER, missed, Kendall tau |
| `score_visual.py` | Whited-out and colour-lost, the text-blind failure |
