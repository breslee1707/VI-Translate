"""Write an invisible text layer over a scanned page.

The whole integration idea rests on this file. Recognised text is drawn onto
the page in render mode 3, so pdfminer extracts it as ordinary LTChars and the
unmodified engine does the rest -- layout, paragraphing, translation, backing
rectangles, font embedding -- with no change to pdf2zh/ at all.

That works only because pdf2zh/pdfinterp.py:330-333 drops every operator whose
name starts with T when it replays the original content stream. The text we add
is therefore never echoed into the output, so there is no risk of doubled
glyphs. The same line has a sharp edge, though: Tr 3 is dropped too, so
"invisible" does not survive into the output. Anything the engine chooses to
re-emit from this layer -- a {vN} formula, notably -- comes back visible.

Three constraints are encoded here, each learned from a specific line of the
engine, and each of which silently ruins a page if broken:

  helv, never cour   pdf2zh/rules.py:76-78 treats a font named Courier, Mono,
                     Math or Sym as a formula font. Pick the wrong base-14
                     alias and every character on every page becomes {vN}.
  size per line      pdf2zh/converter.py:569-573 starts a formula run when a
                     glyph is under 0.79x the paragraph size. Fitting size per
                     word makes ascender-free words like "come" measure ~0.55x
                     and tears them out of their own paragraph.
  emit in order      pdf2zh/converter.py:553 does not sort. Emission order is
                     paragraph order.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pymupdf  # noqa: E402

import ocrjson  # noqa: E402
import paths  # noqa: E402
from pdf2zh.rules import is_formula_font  # noqa: E402

# Base-14 Helvetica: no embedding, WinAnsi, no ToUnicode needed, and a name
# that is_formula_font does not match. Checked at import, not assumed.
FONT = "helv"
_FONT = pymupdf.Font(FONT)
assert not is_formula_font("Helvetica"), "base font must not read as a formula font"

DESCENDERS = set("gjpqy")
# Helvetica descender as a fraction of em, used only when the oracle has not
# supplied a real baseline.
DESCENT = 0.212
MIN_SIZE, MAX_SIZE = 3.0, 72.0


@dataclass
class Placement:
    text: str
    x: float
    baseline: float
    size: float


def fit_size(line: ocrjson.Line) -> float:
    """Choose one font size for the whole line, from its width.

    Width is the right handle rather than height: it is measurable without
    knowing which glyphs have ascenders, it is what the paragraph box's
    horizontal extent is built from, and converter.py:834-841 gives only 3pt of
    padding to hide the original ink underneath.
    """
    if line.size:
        return max(MIN_SIZE, min(MAX_SIZE, line.size))
    natural = _FONT.text_length(line.text, fontsize=1.0)
    if natural <= 0:
        return max(MIN_SIZE, min(MAX_SIZE, line.height * 0.8))
    size = line.width / natural
    # A line box taller than the fitted size means the recogniser boxed
    # something the text does not explain; trust the smaller of the two.
    return max(MIN_SIZE, min(MAX_SIZE, size, line.height * 1.4))


def fit_baseline(line: ocrjson.Line, size: float) -> float:
    """Place the baseline inside the line box.

    pdf2zh/pdfinterp.py:100 zeroes every font's descent, so an LTChar's y0 is
    its baseline exactly. The backing rectangle's lower edge is therefore
    baseline - 3pt, and any error here shows up as the original scan's
    descenders surviving under the translation.
    """
    if line.baseline is not None:
        return line.baseline
    has_descender = any(ch in DESCENDERS for ch in line.text)
    return line.bbox[3] - (DESCENT * size if has_descender else 0.0)


def place(page: ocrjson.Page) -> list[Placement]:
    ordered = ocrjson.reading_order(page.lines, page.width)
    out: list[Placement] = []
    for line in ordered:
        text = line.text.strip()
        if not text:
            continue
        size = fit_size(line)
        out.append(Placement(text, line.bbox[0], fit_baseline(line, size), size))
    return out


def write(raster_pdf: Path, pages: list[ocrjson.Page], out_pdf: Path) -> dict:
    """Draw the text layer onto a copy of raster_pdf."""
    by_index = {p.page: p for p in pages}
    stats = {"lines": 0, "chars": 0, "unencodable": 0, "pages": 0}

    doc = pymupdf.open(raster_pdf)
    try:
        for index in range(doc.page_count):
            page_ocr = by_index.get(index)
            if page_ocr is None:
                continue
            target = doc[index]
            stats["pages"] += 1
            for item in place(page_ocr):
                # helv is WinAnsi; anything outside it would be written as a
                # substitute glyph and score as an OCR error we invented.
                try:
                    item.text.encode("cp1252")
                except UnicodeEncodeError:
                    stats["unencodable"] += 1
                    continue
                target.insert_text(
                    (item.x, item.baseline),
                    item.text,
                    fontname=FONT,
                    fontsize=item.size,
                    render_mode=3,  # invisible in the sidecar itself
                )
                stats["lines"] += 1
                stats["chars"] += len(item.text)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.save(out_pdf, garbage=3, deflate=True)
    finally:
        doc.close()
    return stats


def verify(out_pdf: Path, pages: list[ocrjson.Page]) -> dict:
    """Check the text we wrote reads back, and that fonts look translatable.

    Catches the two silent killers up front: an encoding that round-trips to
    something else, and a font name the engine would classify as a formula.
    """
    problems: list[str] = []
    recovered = 0
    with pymupdf.open(out_pdf) as doc:
        for page in doc:
            for font_name in {str(span["font"]) for span in page.get_texttrace()}:
                if is_formula_font(font_name):
                    problems.append(f"page {page.number + 1}: formula-like font {font_name!r}")
            recovered += len(page.get_text().strip())
    expected = sum(len(line.text.strip()) for p in pages for line in p.lines)
    return {
        "expected_chars": expected,
        "recovered_chars": recovered,
        "ratio": round(recovered / expected, 4) if expected else 0.0,
        "problems": sorted(set(problems)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", type=Path, required=True)
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--ignore-truth",
        action="store_true",
        help="discard oracle baseline/size, forcing the fitting path",
    )
    args = parser.parse_args(argv)

    paths.ensure_tree()
    pages = ocrjson.load(paths.work_path(args.ocr))
    if args.ignore_truth:
        for page in pages:
            for line in page.lines:
                line.baseline = None
                line.size = None

    out = paths.work_path(args.out)
    stats = write(paths.work_path(args.raster), pages, out)
    checks = verify(out, pages)
    print(f"wrote {out}")
    extra = f", {stats['unencodable']} unencodable" if stats["unencodable"] else ""
    print(f"  {stats['pages']} pages, {stats['lines']} lines, {stats['chars']} chars{extra}")
    print(
        f"  round-trip: {checks['recovered_chars']}/{checks['expected_chars']} chars "
        f"= {checks['ratio']:.3f}"
    )
    for problem in checks["problems"]:
        print(f"  PROBLEM: {problem}")
    return 0 if checks["ratio"] > 0.98 and not checks["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
