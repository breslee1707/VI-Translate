"""Optional OCR pass: translate the text that lives inside a page's images.

The engine proper only ever sees glyphs the PDF actually carries, so a scan, a
screenshot, or a diagram with labels baked into the pixels passes through
untouched. When the caller asks for it, this module rasterizes each image
region of the source page, recognizes the text in it, translates it, and draws
the result back over the lines it read, leaving the artwork around them alone.

It runs after the main translation, on the finished document, so nothing in
converter.py needs to know it exists. Pixels are read from the untouched
source: rasterizing the translated document would feed the translation it
already carries straight back into the recognizer.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np
import pymupdf
from pymupdf import Document, Font

logger = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]
Line = tuple[str, Rect]

# 200 dpi reads 8pt print reliably and keeps an A4 page just under RapidOCR's
# own 2000px working size, so nothing is resampled twice.
# ponytail: one constant, raise it if a source turns out to carry smaller print.
OCR_DPI = 200
SCALE = OCR_DPI / 72.0

# PP-OCRv4 scores every line it returns. Below this it is usually reading
# texture, a rule, or the edge of a table.
MIN_CONFIDENCE = 0.6
# One or two recognized characters are noise more often than they are a word.
MIN_TEXT_CHARACTERS = 3
# An image narrower than this fraction of the page is a logo, an icon, or a
# bullet, and carries no prose worth the OCR pass.
MIN_IMAGE_WIDTH_FRACTION = 0.15
# Scanners emit one page as a grid of tiles; tiles this close are one region.
TILE_GAP = 2.0
# Two lines belong to the same paragraph when they sit in the same column and
# the gap between them is small next to their own height.
LINE_GAP_RATIO = 0.6
COLUMN_OVERLAP_RATIO = 0.3
# The translation has to fit where the source text was. Same floor the
# converter holds real text to: half the size it was read at.
MIN_FONT_SCALE = 0.5
FONT_SHRINK_STEP = 0.5
MIN_FONT_SIZE = 4.0
# The detected box is what the source glyphs occupied, so it sets the size. A
# fixed ceiling here drew a 70pt slide title at 24pt.
FONT_SIZE_OF_BOX = 0.8
# Below this the paper is dark and black text on it cannot be read.
DARK_BACKGROUND_LUMINANCE = 0.5
LIGHT_INK = (0.97, 0.97, 0.97)
DARK_INK = (0.05, 0.05, 0.05)
# Vietnamese stacks diacritics above and below; see references/preservation-rules.md.
LINE_HEIGHT = 1.10
# Only the average colour of a line box is wanted, so read it small.
BACKGROUND_SAMPLE_DPI = 36


class OcrUnavailableError(RuntimeError):
    """RapidOCR could not be loaded, so the OCR mode cannot run."""


@dataclass(frozen=True)
class OcrBlock:
    """One paragraph read out of an image, in page coordinates.

    `lines` keeps the box of each line the paragraph was built from. The
    backing is painted over those boxes rather than over the paragraph, so a
    short translation of a tall title leaves a couple of covered lines instead
    of a slab of flat colour across the picture behind it.
    """

    text: str
    rect: Rect
    height: float
    lines: tuple[Rect, ...] = ()


@dataclass(frozen=True)
class OcrOutcome:
    """What the OCR pass put on the page, and what it could not."""

    pages: tuple[int, ...] = ()
    segments: int = 0
    failures: list[str] = field(default_factory=list)
    reasons: Counter = field(default_factory=Counter)


_SESSION: Any = None
_SESSION_LOCK = threading.Lock()


def ocr_session() -> Any:
    """Load RapidOCR once per process.

    Same shape as the layout model in scripts/translate_pdf.py: building the
    three ONNX sessions costs seconds, and a queue of files would otherwise pay
    that once per document.
    """
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as error:  # pragma: no cover - install-time failure
                raise OcrUnavailableError(
                    "OCR mode needs rapidocr-onnxruntime, which is not installed. "
                    "Install it with: pip install -r requirements.txt"
                ) from error
            # ponytail: the bundled recognizer is ch_PP-OCRv4_rec, which covers
            # Latin as well as Chinese. If Latin accuracy proves short, fetch
            # en_PP-OCRv4_rec through scripts/fetch_assets.py and point Rec's
            # model path at it here.
            _SESSION = RapidOCR()
        return _SESSION


def _touches(one: Rect, other: Rect, gap: float = TILE_GAP) -> bool:
    return (
        one[0] - gap <= other[2]
        and other[0] - gap <= one[2]
        and one[1] - gap <= other[3]
        and other[1] - gap <= one[3]
    )


def _union(one: Rect, other: Rect) -> Rect:
    return (
        min(one[0], other[0]),
        min(one[1], other[1]),
        max(one[2], other[2]),
        max(one[3], other[3]),
    )


def merge_regions(rects: Iterable[Rect], page_width: float) -> list[Rect]:
    """Merge image placements that touch, then drop what is too small for prose.

    A scanner routinely emits one page as dozens of tiles, none of them large
    on its own, the same trap that made is_scanned_page the wrong question for
    the image-only report. Merging first means a tiled scan arrives as one
    region, while a lone logo is still dropped.

    ponytail: O(n^2) over the images on one page, which is dozens. Index them
    if a page ever carries thousands.
    """
    remaining = [tuple(rect) for rect in rects]
    merged: list[Rect] = []
    while remaining:
        current = remaining.pop()
        grew = True
        while grew:
            grew = False
            rest = []
            for other in remaining:
                if _touches(current, other):
                    current = _union(current, other)
                    grew = True
                else:
                    rest.append(other)
            remaining = rest
        merged.append(current)
    minimum = MIN_IMAGE_WIDTH_FRACTION * page_width
    return sorted(
        (rect for rect in merged if rect[2] - rect[0] >= minimum),
        key=lambda rect: (rect[1], rect[0]),
    )


def quad_to_rect(quad: Sequence[Sequence[float]], origin: tuple[float, float]) -> Rect:
    """Turn a detector quadrilateral in crop pixels into a page-space rectangle."""
    xs = [point[0] for point in quad]
    ys = [point[1] for point in quad]
    return (
        origin[0] + min(xs) / SCALE,
        origin[1] + min(ys) / SCALE,
        origin[0] + max(xs) / SCALE,
        origin[1] + max(ys) / SCALE,
    )


def _intersects(one: Rect, other: Sequence[float]) -> bool:
    return (
        one[0] < other[2]
        and other[0] < one[2]
        and one[1] < other[3]
        and other[1] < one[3]
    )


def strip_recognized_real_text(
    lines: Sequence[Line], words: Sequence[Sequence[Any]]
) -> list[Line]:
    """Drop whatever the engine already held as real text.

    A page can carry a picture behind its prose, and rasterizing that region
    hands the recognizer the very sentences the converter has just translated.
    Anything sitting where the source had a real word belongs to the engine and
    not to this pass, or the page ends up carrying both translations stacked.

    ponytail: O(lines x words) per page; both are small next to the OCR itself.
    """
    return [
        (text, rect)
        for text, rect in lines
        if not any(_intersects(rect, word) for word in words)
    ]


def _continues(block_rect: Rect, block_height: float, rect: Rect) -> bool:
    overlap = min(block_rect[2], rect[2]) - max(block_rect[0], rect[0])
    narrower = min(block_rect[2] - block_rect[0], rect[2] - rect[0])
    if narrower <= 0 or overlap < COLUMN_OVERLAP_RATIO * narrower:
        return False
    height = max(block_height, rect[3] - rect[1])
    gap = rect[1] - block_rect[3]
    return -0.5 * height <= gap < LINE_GAP_RATIO * height


def group_lines(lines: Sequence[Line]) -> list[OcrBlock]:
    """Join the lines that read as one paragraph.

    Translating line by line loses the sentence. The engine learned that on
    real text, and OCR output is worse rather than better, because a detector
    cuts wherever the pixels stop rather than where the thought does.
    """
    blocks: list[tuple[list[str], Rect, list[Rect]]] = []
    ordered = sorted(lines, key=lambda line: (round(line[1][1], 1), line[1][0]))
    for text, rect in ordered:
        if blocks and _continues(
            blocks[-1][1], blocks[-1][2][-1][3] - blocks[-1][2][-1][1], rect
        ):
            texts, block_rect, boxes = blocks[-1]
            texts.append(text)
            boxes.append(rect)
            blocks[-1] = (texts, _union(block_rect, rect), boxes)
        else:
            blocks.append(([text], rect, [rect]))
    result = []
    for texts, rect, boxes in blocks:
        heights = sorted(box[3] - box[1] for box in boxes)
        result.append(
            OcrBlock(
                " ".join(texts).strip(),
                rect,
                heights[len(heights) // 2],
                tuple(boxes),
            )
        )
    return result


def _image_rects(page: Any) -> list[Rect]:
    """Every place this page draws a raster image."""
    rects: list[Rect] = []
    for image in page.get_images(full=True):
        try:
            placements = page.get_image_rects(image[0])
        except Exception:  # noqa: BLE001 - a broken image resource is not fatal
            continue
        rects.extend(tuple(placement) for placement in placements)
    return rects


def _recognize(page: Any, region: Rect, session: Any) -> list[Line]:
    """Read one image region, returning its lines in page coordinates."""
    pixmap = page.get_pixmap(dpi=OCR_DPI, clip=pymupdf.Rect(region))
    if pixmap.width < 2 or pixmap.height < 2:
        return []
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    # RapidOCR reads an ndarray as BGR, the way cv2 hands one over; a pixmap is RGB.
    results, _elapsed = session(image[:, :, ::-1])
    if not results:
        return []
    # The pixmap snaps the clip to whole pixels, so its own rectangle is
    # where the crop actually started, not the rectangle that was asked for.
    crop = tuple(pixmap.irect)
    origin = (crop[0] / SCALE, crop[1] / SCALE)
    lines: list[Line] = []
    for quad, text, score in results:
        if score < MIN_CONFIDENCE:
            continue
        text = text.strip()
        if len(text) < MIN_TEXT_CHARACTERS:
            continue
        lines.append((text, quad_to_rect(quad, origin)))
    return lines


def sampled_background(page: Any, rect: Any) -> tuple[float, float, float]:
    """The colour of the paper under this line.

    Scanned paper is warm grey far more often than it is white, so a patch
    painted pure white glows on the page and turns every translated paragraph
    into a visible rectangle. The glyphs are a minority of the pixels in a line
    box, so the median colour of the box is the paper it sits on.
    """
    try:
        pixmap = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=BACKGROUND_SAMPLE_DPI)
    except Exception:  # noqa: BLE001 - an unsampleable box just gets white
        return (1.0, 1.0, 1.0)
    if pixmap.width < 1 or pixmap.height < 1 or pixmap.n < 3:
        return (1.0, 1.0, 1.0)
    samples = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(-1, pixmap.n)[:, :3]
    median = np.median(samples, axis=0) / 255.0
    return (float(median[0]), float(median[1]), float(median[2]))


def ink_for(background: tuple[float, float, float]) -> tuple[float, float, float]:
    """Pick an ink that can be read on this background.

    The recognizer reports words, not their colour, and a slide title is as
    often white on a photograph as black on paper. Writing everything in black
    made those titles invisible.
    """
    red, green, blue = background
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return DARK_INK if luminance > DARK_BACKGROUND_LUMINANCE else LIGHT_INK


def _draw(page: Any, block: OcrBlock, text: str, font: Font) -> bool:
    """Cover the lines that were read and write the translation in their place.

    The backing covers those line boxes only, never the picture around them, so
    a diagram keeps its artwork and loses just the label it was carrying, and
    each box is painted the colour sampled underneath it rather than white. A
    translation that will not fit even at the floor size is left undrawn rather
    than spilled across the figure, and reported instead.
    """
    rect = pymupdf.Rect(block.rect) + (-1, -1, 1, 1)
    boxes = [pymupdf.Rect(box) + (-1, -1, 1, 1) for box in block.lines or (block.rect,)]
    background = sampled_background(page, boxes[0])
    size = max(MIN_FONT_SIZE, block.height * FONT_SIZE_OF_BOX)
    floor = max(MIN_FONT_SIZE, size * MIN_FONT_SCALE)
    while size >= floor:
        writer = pymupdf.TextWriter(page.rect, color=ink_for(background))
        try:
            leftover = writer.fill_textbox(
                rect, text, font=font, fontsize=size, lineheight=LINE_HEIGHT
            )
        except (ValueError, RuntimeError):
            return False
        if not leftover:
            for box in boxes:
                page.draw_rect(box, color=None, fill=sampled_background(page, box))
            writer.write_text(page)
            return True
        size -= FONT_SHRINK_STEP
    return False


def apply_ocr_overlay(
    source: bytes,
    doc_zh: Document,
    pages: Sequence[int] | None,
    translator: Any,
    font: Font,
    session: Any = None,
    on_page: Any = None,
) -> OcrOutcome:
    """Translate the text inside every image region and draw it back in place.

    `on_page` is called after each page. This pass runs after the whole document
    has been translated, so without it the caller's progress sits at the last
    page for as long as the recognizer takes and the app looks hung.
    """
    session = session or ocr_session()
    wanted = None if pages is None else set(pages)
    touched: list[int] = []
    segments = 0
    failures: list[str] = []
    reasons: Counter[str] = Counter()
    with Document(stream=source) as original:
        page_count = min(original.page_count, doc_zh.page_count)
        selected = [
            pageno
            for pageno in range(page_count)
            if wanted is None or pageno in wanted
        ]
        for done, pageno in enumerate(selected, 1):
            if on_page is not None:
                on_page(done, len(selected))
            source_page = original[pageno]
            regions = merge_regions(_image_rects(source_page), source_page.rect.width)
            if not regions:
                continue
            lines: list[Line] = []
            for region in regions:
                lines.extend(_recognize(source_page, region, session))
            lines = strip_recognized_real_text(lines, source_page.get_text("words"))
            output_page = doc_zh[pageno]
            written = 0
            for block in group_lines(lines):
                try:
                    translated = translator.translate(block.text)
                except Exception as error:  # noqa: BLE001 - one block must not end the run
                    failures.append(block.text)
                    reasons[type(error).__name__] += 1
                    continue
                translated = (translated or "").strip()
                if not translated:
                    continue
                if _draw(output_page, block, translated, font):
                    written += 1
                else:
                    failures.append(block.text)
                    reasons["OCR text cannot fit inside its image"] += 1
            if written:
                touched.append(pageno)
                segments += written
                logger.info(
                    "Page %s: %s OCR paragraphs translated", pageno + 1, written
                )
    return OcrOutcome(tuple(touched), segments, failures, reasons)
