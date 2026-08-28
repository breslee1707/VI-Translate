import concurrent.futures
import logging
import math
import re
import unicodedata
from collections.abc import Callable
from enum import Enum, IntEnum
from string import Template
from typing import Dict

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTFigure, LTLine, LTPage
from pdfminer.pdffont import PDFCIDFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.utils import apply_matrix_pt, mult_matrix
from pymupdf import Font
from tenacity import retry, stop_after_attempt, wait_exponential

from pdf2zh.rules import is_bullet_character, is_formula_font, line_height_for_language
from pdf2zh.translator import (
    ENGINES,
    BaseTranslator,
    encode_formula_placeholders,
    restore_formula_placeholders,
)

log = logging.getLogger(__name__)
STYLE_TAG_PATTERN = re.compile(r"<(/?)s([123])>", re.IGNORECASE)
IDENTITY_ORIENTATION = (1.0, 0.0, 0.0, 1.0)
BASE14_STYLE_FONTS = {0: "tiro", 1: "tibo", 2: "tiit", 3: "tibi"}


class TextStyle(IntEnum):
    REGULAR = 0
    BOLD = 1
    ITALIC = 2
    BOLD_ITALIC = 3


def text_style_from_font(font_name: str | bytes) -> TextStyle:
    """Infer PDF text emphasis from common PostScript font face names."""
    if isinstance(font_name, bytes):
        font_name = font_name.decode("utf-8", errors="ignore")
    face = font_name.split("+")[-1]
    bold = re.search(r"bold|semibold|demi|black|heavy", face, re.IGNORECASE)
    italic = re.search(r"italic|oblique|slanted", face, re.IGNORECASE)
    if bold and italic:
        return TextStyle.BOLD_ITALIC
    if bold:
        return TextStyle.BOLD
    if italic:
        return TextStyle.ITALIC
    return TextStyle.REGULAR


def text_orientation(matrix) -> tuple[float, float, float, float] | None:
    """Return the nearest quarter-turn from the glyph baseline direction.

    Some PDF producers use a negative font size together with a reflected text
    matrix (for example ``1 0 0 -1``) to draw ordinary upright text.  Looking at
    all four matrix components mistakes that implementation detail for an
    unsupported orientation and causes the glyphs to be replayed upside down.
    The first matrix column is the logical baseline, so it is sufficient for
    classifying the four supported reading directions.
    """
    a, b, c, d = (float(value) for value in matrix[:4])
    x_scale = math.hypot(a, b)
    if x_scale <= 1e-6 or math.hypot(c, d) <= 1e-6:
        return None
    baseline = (a / x_scale, b / x_scale)
    candidates = (
        IDENTITY_ORIENTATION,
        (0.0, 1.0, -1.0, 0.0),
        (-1.0, 0.0, 0.0, -1.0),
        (0.0, -1.0, 1.0, 0.0),
    )
    distances = [
        (baseline[0] - candidate[0]) ** 2
        + (baseline[1] - candidate[1]) ** 2
        for candidate in candidates
    ]
    best = min(range(len(candidates)), key=distances.__getitem__)
    return candidates[best] if distances[best] <= 0.04 else None


def normalised_text_matrix(matrix) -> tuple[float, float, float, float]:
    a, b, c, d = (float(value) for value in matrix[:4])
    x_scale = max(math.hypot(a, b), 1e-6)
    y_scale = max(math.hypot(c, d), 1e-6)
    if a * d - b * c < 0:
        # A reflected text matrix is normally paired with a negative font size.
        # Preserve its baseline rotation but remove the technical reflection.
        ux, uy = a / x_scale, b / x_scale
        return (ux, uy, -uy, ux)
    return (a / x_scale, b / x_scale, c / y_scale, d / y_scale)


def paragraph_width_budget(x: float, x0: float, x1: float, lines: int) -> float:
    """Return usable width while accounting for a first-line indentation."""
    if lines <= 0 or x1 <= x0:
        return 0.0
    first_line = max(0.0, x1 - max(x, x0))
    return first_line + max(0, lines - 1) * (x1 - x0)


def styled_text_matrix(
    orientation: tuple[float, float, float, float],
    style: int,
    synthetic: bool,
) -> tuple[float, float, float, float]:
    """Compose an italic shear with the source orientation when needed."""
    a, b, c, d = orientation
    if synthetic and style in (TextStyle.ITALIC, TextStyle.BOLD_ITALIC):
        shear = 0.2
        c, d = c + a * shear, d + b * shear
    return (a, b, c, d)


def uses_synthetic_bold(style: int, synthetic: bool) -> bool:
    return synthetic and style in (TextStyle.BOLD, TextStyle.BOLD_ITALIC)


def matrix_font_size(matrix) -> float:
    """Recover font size from a text matrix even when LTChar.size is advance."""
    return math.hypot(float(matrix[0]), float(matrix[1]))


def strip_style_tags(text: str) -> str:
    return STYLE_TAG_PATTERN.sub("", text)


def styled_character_text(characters: list[LTChar]) -> str:
    """Serialize source character styles into translator-safe inline markers."""
    parts: list[str] = []
    active = TextStyle.REGULAR
    for character in characters:
        style = text_style_from_font(character.fontname)
        if style != active:
            if active:
                parts.append(f"</s{int(active)}>")
            if style:
                parts.append(f"<s{int(style)}>")
            active = style
        parts.append(character.get_text())
    if active:
        parts.append(f"</s{int(active)}>")
    return "".join(parts)


def preferred_translation(text: str, language: str) -> str | None:
    """Return stable Vietnamese terminology for the three rotated table headers."""
    if language.lower() != "vi":
        return None
    visible = strip_style_tags(text).strip()
    replacement = {
        "Designation": "Tên gọi",
        "Abbreviation": "Viết tắt",
        "Unit": "Đơn vị",
    }.get(visible)
    if replacement is None:
        return None
    leading = re.match(r"^\s*", text).group(0)
    trailing = re.search(r"\s*$", text).group(0)
    style = re.fullmatch(r"\s*(<s[123]>).*?(</s[123]>)\s*", text, re.DOTALL)
    if style:
        return f"{leading}{style.group(1)}{replacement}{style.group(2)}{trailing}"
    return f"{leading}{replacement}{trailing}"


def should_translate_rotated_text(text: str) -> bool:
    """Keep rotated document-control identifiers such as reference numbers."""
    visible = strip_style_tags(text).strip()
    if re.search(r"\d", visible) and re.search(
        r"\b(ref|no|rev|code|version)\b", visible, re.IGNORECASE
    ):
        return False
    return True


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
    ) -> None:
        PDFConverter.__init__(self, rsrcmgr, None, "utf-8", 1, None)

    def begin_page(self, page, ctm) -> None:
        x0, y0, x1, y1 = page.cropbox
        x0, y0 = apply_matrix_pt(ctm, (x0, y0))
        x1, y1 = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0 - x1), abs(y0 - y1))
        self.cur_item = LTPage(page.pageno, mediabox)

    def end_page(self, page):
        return self.receive_layout(self.cur_item)

    def begin_figure(self, name, bbox, matrix) -> None:
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        self.cur_item.pageid = self._stack[-1].pageid

    def end_figure(self, _: str) -> None:
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure), str(type(self.cur_item))
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return self.receive_layout(fig)

    def render_char(
        self,
        matrix,
        font,
        fontsize: float,
        scaling: float,
        rise: float,
        cid: int,
        ncs,
        graphicstate: PDFGraphicState,
    ) -> float:
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, str), str(type(text))
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            textwidth,
            textdisp,
            ncs,
            graphicstate,
        )
        self.cur_item.add(item)
        item.cid = cid
        item.font = font
        return item.adv


class Paragraph:
    def __init__(self, y, x, x0, x1, y0, y1, size, brk, cls=-1, matrix=None):
        self.y: float = y
        self.x: float = x
        self.x0: float = x0
        self.x1: float = x1
        self.y0: float = y0
        self.y1: float = y1
        self.size: float = size
        self.brk: bool = brk
        self.cls: int = int(cls)
        self.layout_bound: tuple[float, float, float, float] | None = None
        self.orientation = text_orientation(matrix or (1, 0, 0, 1))
        self.rotated_chars: list[LTChar] = []
        self.open_style = TextStyle.REGULAR
        self.text_length = 0
        self.anchor: tuple[float, float] = (x, y)


def text_fits_box_at_minimum_size(
    text: str,
    width: float,
    height: float,
    source_size: float,
    formula_widths: list[float],
    measure_char: Callable[[str, float], float],
) -> bool:
    """Conservatively check whether a cell translation can fit at 50% size."""
    size = source_size * 0.5
    if width <= 0 or height <= 0 or size <= 0:
        return False

    text = strip_style_tags(text)
    chunks = re.findall(r"\{\s*v[\d\s]+\}|\S+|\s+", text, re.IGNORECASE)

    def chunk_width(chunk: str) -> float:
        marker = re.fullmatch(r"\{\s*v([\d\s]+)\}", chunk, re.IGNORECASE)
        if marker:
            try:
                return formula_widths[int(marker.group(1).replace(" ", ""))]
            except (IndexError, ValueError):
                return 0.0
        if chunk.isspace():
            chunk = " "
        return sum(measure_char(character, size) for character in chunk)

    lines = 1
    current = 0.0
    for chunk in chunks:
        measured = chunk_width(chunk)
        is_formula = re.fullmatch(
            r"\{\s*v([\d\s]+)\}", chunk, re.IGNORECASE
        ) is not None
        if is_formula and measured > width:
            return False
        if chunk.isspace():
            if current:
                current += measured
            continue
        if current and current + measured > width:
            lines += 1
            current = 0.0
        if measured > width:
            extra_lines = max(0, math.ceil(measured / width) - 1)
            lines += extra_lines
            current = measured - extra_lines * width
        else:
            current += measured

    occupied_height = size + max(0, lines - 1) * size * 0.8
    return occupied_height <= height + 0.01


# fmt: off
class TranslateConverter(PDFConverterEx):
    def __init__(
        self,
        rsrcmgr,
        vfont: str = None,
        vchar: str = None,
        thread: int = 0,
        layout={},
        lang_in: str = "",
        lang_out: str = "",
        service: str = "",
        noto_name: str = "",
        noto: Font = None,
        envs: Dict = None,
        prompt: Template = None,
        ignore_cache: bool = False,
        layout_bounds: Dict | None = None,
        style_font_names: Dict | None = None,
        style_fonts: Dict | None = None,
        synthetic_styles: set[int] | None = None,
    ) -> None:
        super().__init__(rsrcmgr)
        self.vfont = vfont
        self.vchar = vchar
        self.thread = thread
        self.layout = layout
        # high_level fills this mapping after the converter is constructed.
        # Preserve an empty mapping by identity instead of replacing it.
        self.layout_bounds = layout_bounds if layout_bounds is not None else {}
        self.noto_name = noto_name
        self.noto = noto
        self.style_font_names = style_font_names or {0: noto_name}
        self.style_fonts = style_fonts or {0: noto}
        self.synthetic_styles = synthetic_styles or set()
        self.output_fonts_by_name = {
            self.style_font_names[style]: font
            for style, font in self.style_fonts.items()
        }
        self.translator: BaseTranslator = None
        self.scanned_pages: set = set()
        # Segments whose retries ran out; reported as a partial translation.
        self.translation_failures: list[str] = []
        # e.g. "handoff:model" -> ["handoff", "model"]; model is unused by both engines
        param = service.split(":", 1)
        service_name = param[0]
        service_model = param[1] if len(param) > 1 else None
        if not envs:
            envs = {}
        if service_name not in ENGINES:
            supported = ", ".join(sorted(ENGINES))
            raise ValueError(
                f"Unsupported translation service {service_name!r}; supported: {supported}"
            )
        self.translator = ENGINES[service_name](
            lang_in,
            lang_out,
            service_model,
            envs=envs,
            prompt=prompt,
            ignore_cache=ignore_cache,
        )

    def record_translation_failure(self, segment: str, reason: str) -> None:
        self.translation_failures.append(segment)
        log.warning(
            "Leaving segment in source language (%s): %r",
            reason,
            strip_style_tags(segment)[:200],
        )

    def receive_layout(self, ltpage: LTPage):
        sstk: list[str] = []
        pstk: list[Paragraph] = []
        vbkt: int = 0
        vstk: list[LTChar] = []
        vlstk: list[LTLine] = []
        vfix: float = 0
        var: list[list[LTChar]] = []
        varl: list[list[LTLine]] = []
        varf: list[float] = []
        vlen: list[float] = []
        lstk: list[LTLine] = []
        xt: LTChar = None
        xt_cls: int = -1
        vmax: float = ltpage.width / 4
        ops: str = ""
        preserved_segments: set[str] = set()

        def vflag(font: str, char: str):
            if isinstance(font, bytes):
                try:
                    font = font.decode('utf-8')
                except UnicodeDecodeError:
                    font = ""
            font = font.split("+")[-1]
            if re.match(r"\(cid:", char):
                return True
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if is_formula_font(font):
                    return True
            if self.vchar:
                if re.match(self.vchar, char):
                    return True
            else:
                if (
                    char
                    and char != " "
                    and (
                        unicodedata.category(char[0])
                        in ["Lm", "Mn", "Sk", "Sm", "Zl", "Zp", "Zs"]
                        or ord(char[0]) in range(0x370, 0x400)
                    )
                ):
                    return True
            return False

        def close_style(index: int) -> None:
            paragraph = pstk[index]
            if paragraph.open_style:
                sstk[index] += f"</s{int(paragraph.open_style)}>"
                paragraph.open_style = TextStyle.REGULAR

        def append_styled(index: int, text: str, style: TextStyle) -> None:
            paragraph = pstk[index]
            if style != paragraph.open_style:
                close_style(index)
                if style:
                    sstk[index] += f"<s{int(style)}>"
                paragraph.open_style = style
            sstk[index] += text
            paragraph.text_length += len(text)

        def append_formula(index: int, identifier: int) -> None:
            close_style(index)
            sstk[index] += f"{{v{identifier}}}"

        def new_paragraph(child: LTChar, cls: int) -> None:
            orientation = text_orientation(child.matrix)
            size = (
                matrix_font_size(child.matrix)
                if orientation not in (None, IDENTITY_ORIENTATION)
                else child.size
            )
            sstk.append("")
            pstk.append(
                Paragraph(
                    child.y0,
                    child.x0,
                    child.x0,
                    child.x0,
                    child.y0,
                    child.y1,
                    size,
                    False,
                    cls,
                    child.matrix,
                )
            )

        ############################################################
        for child in ltpage:
            if isinstance(child, LTChar):
                cur_v = False
                layout = self.layout[ltpage.pageid]
                h, w = layout.shape
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                if is_bullet_character(child.get_text(), child.fontname):
                    cls = 0
                orientation = text_orientation(child.matrix)
                if (
                    cls == 0
                    or (
                        cls == xt_cls
                        and pstk[-1].text_length > 1
                        and orientation == IDENTITY_ORIENTATION
                        and child.size < pstk[-1].size * 0.79
                    )
                    or vflag(child.fontname, child.get_text())
                    or orientation is None
                ):
                    cur_v = True
                if not cur_v:
                    # Keep brackets with a formula only when the formula starts
                    # the segment. In prose such as "Factor C1 (applies...)" a
                    # subscript leaves vstk populated; capturing the following
                    # bracket then strands it at its source coordinate.
                    if vstk and not pstk[-1].text_length and child.get_text() == "(":
                        cur_v = True
                        vbkt += 1
                    if vbkt and child.get_text() == ")":
                        cur_v = True
                        vbkt -= 1
                if (
                    not cur_v
                    or cls != xt_cls
                    or (pstk[-1].text_length and abs(child.x0 - xt.x0) > vmax)
                ):
                    if vstk:
                        if (
                            not cur_v
                            and cls == xt_cls
                            and child.x0 > max([vch.x0 for vch in vstk])
                        ):
                            vfix = vstk[0].y0 - child.y0
                        if not pstk[-1].text_length:
                            xt_cls = -1
                        append_formula(len(sstk) - 1, len(var))
                        var.append(vstk)
                        varl.append(vlstk)
                        varf.append(vfix)
                        vstk = []
                        vlstk = []
                        vfix = 0
                if not vstk:
                    if cls == xt_cls:
                        # Force paragraph break for list items: when text wraps back
                        # to left AND there's a significant vertical gap (> 1.5x font size),
                        # it's likely a new list item, not a continuation
                        if (child.x1 < xt.x0
                            and abs(child.y0 - xt.y0) > pstk[-1].size * 1.5):
                            close_style(len(sstk) - 1)
                            new_paragraph(child, cls)
                        elif child.x0 > xt.x1 + 1:
                            if pstk[-1].orientation == IDENTITY_ORIENTATION:
                                append_styled(
                                    len(sstk) - 1,
                                    " ",
                                    text_style_from_font(child.fontname),
                                )
                        elif child.x1 < xt.x0:
                            if pstk[-1].orientation == IDENTITY_ORIENTATION:
                                append_styled(
                                    len(sstk) - 1,
                                    " ",
                                    text_style_from_font(child.fontname),
                                )
                            pstk[-1].brk = True
                    else:
                        if sstk:
                            close_style(len(sstk) - 1)
                        new_paragraph(child, cls)
                if not cur_v:
                    if pstk[-1].orientation != IDENTITY_ORIENTATION:
                        pstk[-1].rotated_chars.append(child)
                        pstk[-1].text_length += len(child.get_text())
                    else:
                        if (
                            child.size > pstk[-1].size
                            or pstk[-1].text_length == 1
                        ) and child.get_text() != " ":
                            pstk[-1].y -= child.size - pstk[-1].size
                            pstk[-1].size = child.size
                        append_styled(
                            len(sstk) - 1,
                            child.get_text(),
                            text_style_from_font(child.fontname),
                        )
                else:
                    if (
                        not vstk
                        and cls == xt_cls
                        and child.x0 > xt.x0
                    ):
                        vfix = child.y0 - xt.y0
                    vstk.append(child)
                pstk[-1].x0 = min(pstk[-1].x0, child.x0)
                pstk[-1].x1 = max(pstk[-1].x1, child.x1)
                pstk[-1].y0 = min(pstk[-1].y0, child.y0)
                pstk[-1].y1 = max(pstk[-1].y1, child.y1)
                xt = child
                xt_cls = cls
            elif isinstance(child, LTFigure):
                pass
            elif isinstance(child, LTLine):
                layout = self.layout[ltpage.pageid]
                h, w = layout.shape
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                if vstk and cls == xt_cls:
                    vlstk.append(child)
                else:
                    lstk.append(child)
            else:
                pass
        if vstk:
            append_formula(len(sstk) - 1, len(var))
            var.append(vstk)
            varl.append(vlstk)
            varf.append(vfix)

        for index, paragraph in enumerate(pstk):
            close_style(index)
            if not paragraph.rotated_chars:
                continue
            a, b, _c, _d = paragraph.orientation
            ordered = sorted(
                paragraph.rotated_chars,
                key=lambda character: (
                    float(character.matrix[4]) * a
                    + float(character.matrix[5]) * b
                ),
            )
            sstk[index] = styled_character_text(ordered)
            paragraph.text_length = sum(len(char.get_text()) for char in ordered)
            first = ordered[0]
            paragraph.anchor = (float(first.matrix[4]), float(first.matrix[5]))
            paragraph.x, paragraph.y = paragraph.anchor
            paragraph.size = matrix_font_size(first.matrix)
            paragraph.brk = False
            if not should_translate_rotated_text(sstk[index]):
                preserved_segments.add(sstk[index])

        page_bounds = self.layout_bounds.get(ltpage.pageid, {})
        for paragraph in pstk:
            bound = page_bounds.get(paragraph.cls)
            if bound is None:
                continue
            paragraph.x0, paragraph.y0, paragraph.x1, paragraph.y1 = bound
            if paragraph.orientation == IDENTITY_ORIENTATION:
                paragraph.x = max(paragraph.x, paragraph.x0)
            paragraph.layout_bound = bound
        log.debug("\n==========[VSTACK]==========\n")
        for id, v in enumerate(var):
            l = max([vch.x1 for vch in v]) - v[0].x0
            log.debug(f'< {l:.1f} {v[0].x0:.1f} {v[0].y0:.1f} {v[0].cid} {v[0].fontname} {len(varl[id])} > v{id} = {"".join([ch.get_text() for ch in v])}')
            vlen.append(l)

        ############################################################
        log.debug("\n==========[SSTACK]==========\n")

        # Google throttles a long document, so back off instead of hammering it.
        # Roughly two minutes of patience per segment, then give up rather than
        # hang the run forever the way an unbounded retry used to.
        @retry(
            wait=wait_exponential(multiplier=1, min=1, max=60),
            stop=stop_after_attempt(8),
            reraise=True,
        )
        def request_translation(s: str) -> str:
            return self.translator.translate(s)

        def translate_segment(s: str) -> str:
            preferred = preferred_translation(s, self.translator.lang_out)
            if preferred is not None:
                return preferred
            encoded = encode_formula_placeholders(s)
            translated = request_translation(encoded)
            return restore_formula_placeholders(s, translated)

        def worker(s: str) -> str:
            visible = strip_style_tags(s).strip()
            if (
                s in preserved_segments
                or not visible
                or re.fullmatch(r"\{v\d+\}", visible)
            ):
                return s
            try:
                return translate_segment(s)
            except BaseException as e:
                # A book is thousands of segments over tens of minutes, so one
                # dead connection must not throw the whole document away. Keep
                # the source text and let the caller report how much is missing.
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.exception(e, exc_info=False)
                self.record_translation_failure(s, type(e).__name__)
                return s
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.thread
        ) as executor:
            news = list(executor.map(worker, sstk))

        ############################################################
        def raw_string(fcur: str, cstk: str):
            if fcur in self.output_fonts_by_name:
                font = self.output_fonts_by_name[fcur]
                return "".join(["%04x" % font.has_glyph(ord(c)) for c in cstk])
            elif isinstance(self.fontmap[fcur], PDFCIDFont):
                return "".join(["%04x" % ord(c) for c in cstk])
            else:
                return "".join(["%02x" % ord(c) for c in cstk])

        def output_font(character: str, style: int, size: float) -> tuple[str, float]:
            base_name = BASE14_STYLE_FONTS.get(style, "tiro")
            try:
                base = self.fontmap.get(base_name)
                if base is not None and base.to_unichr(ord(character)) == character:
                    return base_name, base.char_width(ord(character)) * size
            except Exception:
                pass
            font_name = self.style_font_names.get(style, self.noto_name)
            font = self.style_fonts.get(style, self.noto)
            try:
                return font_name, font.char_lengths(character, size)[0]
            except Exception:
                return font_name, size * 0.5

        def measure_styled_text(text: str, size: float) -> float:
            total = 0.0
            style = TextStyle.REGULAR
            pointer = 0
            while pointer < len(text):
                style_tag = STYLE_TAG_PATTERN.match(text, pointer)
                if style_tag:
                    closing, identifier = style_tag.groups()
                    style = TextStyle.REGULAR if closing else TextStyle(int(identifier))
                    pointer = style_tag.end()
                    continue
                formula = re.match(
                    r"\{\s*v([\d\s]+)\}", text[pointer:], re.IGNORECASE
                )
                if formula:
                    try:
                        total += vlen[int(formula.group(1).replace(" ", ""))]
                    except (IndexError, ValueError):
                        pass
                    pointer += len(formula.group(0))
                    continue
                _font_name, advance = output_font(text[pointer], int(style), size)
                total += advance
                pointer += 1
            return total

        default_line_height = line_height_for_language(self.translator.lang_out)
        _x, _y = 0, 0
        ops_list = []

        # Draw white rectangles to cover original text in background image (scanned PDFs only)
        white_rects = ""
        if ltpage.pageid in self.scanned_pages:
            pad = 3  # padding to fully cover original text with descenders/ascenders
            for id, new in enumerate(news):
                if new != sstk[id]:  # Only cover areas that were translated
                    rx0 = pstk[id].x0 - pad
                    ry0 = pstk[id].y0 - pad
                    rw = pstk[id].x1 - pstk[id].x0 + pad * 2
                    rh = pstk[id].y1 - pstk[id].y0 + pad * 2
                    white_rects += f"q 1 1 1 rg {rx0:f} {ry0:f} {rw:f} {rh:f} re f Q "
            # Also cover formula areas
            for v in var:
                if v:
                    fx0 = min(ch.x0 for ch in v) - pad
                    fy0 = min(ch.y0 for ch in v) - pad
                    fx1 = max(ch.x1 for ch in v) + pad
                    fy1 = max(ch.y1 for ch in v) + pad
                    white_rects += f"q 1 1 1 rg {fx0:f} {fy0:f} {fx1-fx0:f} {fy1-fy0:f} re f Q "

        def gen_op_txt(
            font,
            size,
            x,
            y,
            rtxt,
            style=TextStyle.REGULAR,
            orientation=IDENTITY_ORIENTATION,
        ):
            synthetic = int(style) in self.synthetic_styles
            a, b, c, d = styled_text_matrix(orientation, int(style), synthetic)
            render = ""
            reset = ""
            if uses_synthetic_bold(int(style), synthetic):
                render = f"2 Tr {max(0.15, size * 0.025):f} w "
                reset = "0 Tr "
            return (
                f"/{font} {size:f} Tf {render}{a:f} {b:f} {c:f} {d:f} "
                f"{x:f} {y:f} Tm [<{rtxt}>] TJ {reset}"
            )

        def gen_op_line(x, y, xlen, ylen, linewidth):
            return f"ET q 1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

        def rotated_available_length(paragraph: Paragraph) -> float:
            a, b, _c, _d = paragraph.orientation
            bounds = paragraph.layout_bound or (
                paragraph.x0,
                paragraph.y0,
                paragraph.x1,
                paragraph.y1,
            )
            x0, y0, x1, y1 = bounds
            projections = [
                x * a + y * b
                for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))
            ]
            anchor_projection = paragraph.anchor[0] * a + paragraph.anchor[1] * b
            return max(0.0, max(projections) - anchor_projection)

        def render_rotated_text(
            paragraph: Paragraph,
            source: str,
            translated: str,
        ) -> list[str]:
            size = paragraph.size
            available = rotated_available_length(paragraph)
            measured = measure_styled_text(translated, size)
            if measured > available * 1.05 and available > 0:
                ratio = available / measured
                if ratio < 0.5:
                    if translated != source:
                        self.record_translation_failure(
                            source, "rotated text needs less than 50% font size"
                        )
                    translated = source
                    measured = measure_styled_text(translated, size)
                    ratio = min(1.0, available / measured) if measured else 1.0
                size *= max(0.5, min(1.0, ratio))

            a, b, _c, _d = paragraph.orientation
            anchor_x, anchor_y = paragraph.anchor
            cursor = 0.0
            pointer = 0
            active_style = TextStyle.REGULAR
            run_font: str | None = None
            run_style = TextStyle.REGULAR
            run_text = ""
            run_start = 0.0
            operations: list[str] = []

            def flush() -> None:
                nonlocal run_text
                if not run_text or run_font is None:
                    run_text = ""
                    return
                operations.append(
                    gen_op_txt(
                        run_font,
                        size,
                        anchor_x + a * run_start,
                        anchor_y + b * run_start,
                        raw_string(run_font, run_text),
                        run_style,
                        paragraph.orientation,
                    )
                )
                run_text = ""

            while pointer < len(translated):
                style_tag = STYLE_TAG_PATTERN.match(translated, pointer)
                if style_tag:
                    flush()
                    closing, identifier = style_tag.groups()
                    active_style = (
                        TextStyle.REGULAR
                        if closing
                        else TextStyle(int(identifier))
                    )
                    pointer = style_tag.end()
                    continue
                formula = re.match(
                    r"\{\s*v([\d\s]+)\}", translated[pointer:], re.IGNORECASE
                )
                if formula:
                    flush()
                    try:
                        vid = int(formula.group(1).replace(" ", ""))
                        formula_chars = var[vid]
                    except (IndexError, ValueError):
                        pointer += len(formula.group(0))
                        continue
                    first = formula_chars[0]
                    first_origin = (float(first.matrix[4]), float(first.matrix[5]))
                    for formula_char in formula_chars:
                        dx = float(formula_char.matrix[4]) - first_origin[0]
                        dy = float(formula_char.matrix[5]) - first_origin[1]
                        operations.append(
                            gen_op_txt(
                                self.fontid[formula_char.font],
                                matrix_font_size(formula_char.matrix),
                                anchor_x + a * cursor + dx,
                                anchor_y + b * cursor + dy,
                                raw_string(
                                    self.fontid[formula_char.font],
                                    chr(formula_char.cid),
                                ),
                                TextStyle.REGULAR,
                                normalised_text_matrix(formula_char.matrix),
                            )
                        )
                    cursor += vlen[vid]
                    pointer += len(formula.group(0))
                    continue
                character = translated[pointer]
                font_name, advance = output_font(character, int(active_style), size)
                if font_name != run_font or active_style != run_style:
                    flush()
                    run_font = font_name
                    run_style = active_style
                    run_start = cursor
                run_text += character
                cursor += advance
                pointer += 1
            flush()
            return operations

        for id, new in enumerate(news):
            x: float = pstk[id].x
            y: float = pstk[id].y
            x0: float = pstk[id].x0
            x1: float = pstk[id].x1
            height: float = pstk[id].y1 - pstk[id].y0
            size: float = pstk[id].size
            brk: bool = pstk[id].brk

            if pstk[id].orientation not in (None, IDENTITY_ORIENTATION):
                ops_list.extend(render_rotated_text(pstk[id], sstk[id], new))
                continue

            if pstk[id].layout_bound is not None and new != sstk[id]:
                def _cell_measure(character: str, candidate_size: float) -> float:
                    return max(
                        output_font(character, style, candidate_size)[1]
                        for style in self.style_font_names
                    )

                if not text_fits_box_at_minimum_size(
                    new,
                    x1 - x0,
                    height,
                    size,
                    vlen,
                    _cell_measure,
                ):
                    self.record_translation_failure(
                        sstk[id], "table cell cannot fit at 50% font size"
                    )
                    new = sstk[id]

            # Auto-scale translated text to the footprint of the source. This is
            # also required for a single-line title: without it a longer target
            # string ignores x1 completely and runs into the neighbouring column.
            if new != sstk[id]:
                # Count how many lines the original text occupied
                orig_lines = (
                    max(1, round(height / (pstk[id].size * default_line_height)))
                    if brk
                    else 1
                )
                total_avail = paragraph_width_budget(x, x0, x1, orig_lines)
                # Measure actual width of translated text (excluding formula tags)
                total_new_width = 0
                tmp_ptr = 0
                plain_new = new
                measure_style = TextStyle.REGULAR
                while tmp_ptr < len(plain_new):
                    style_tag = STYLE_TAG_PATTERN.match(plain_new, tmp_ptr)
                    if style_tag:
                        closing, identifier = style_tag.groups()
                        measure_style = (
                            TextStyle.REGULAR
                            if closing
                            else TextStyle(int(identifier))
                        )
                        tmp_ptr = style_tag.end()
                        continue
                    vm = re.match(r"\{\s*v([\d\s]+)\}", plain_new[tmp_ptr:], re.IGNORECASE)
                    if vm:
                        try:
                            vid_tmp = int(vm.group(1).replace(" ", ""))
                            total_new_width += vlen[vid_tmp]
                        except Exception:
                            pass
                        tmp_ptr += len(vm.group(0))
                    else:
                        ch = plain_new[tmp_ptr]
                        total_new_width += output_font(
                            ch, int(measure_style), pstk[id].size
                        )[1]
                        tmp_ptr += 1
                if total_avail > 0 and total_new_width > total_avail * 1.05:
                    ratio = total_avail / total_new_width
                    if not brk and ratio < 0.5:
                        self.record_translation_failure(
                            sstk[id], "single line needs less than 50% font size"
                        )
                        new = sstk[id]
                    else:
                        size = pstk[id].size * max(ratio, 0.5)

            # Pre-compute word-boundary line breaks to avoid mid-word splits
            if brk:
                def _measure_char(c, style):
                    return output_font(c, int(style), size)[1]

                break_positions = set()
                cur_x = x
                last_space_ptr = -1
                last_space_x_after = cur_x
                p2 = 0
                wrap_style = TextStyle.REGULAR
                while p2 < len(new):
                    style_tag = STYLE_TAG_PATTERN.match(new, p2)
                    if style_tag:
                        closing, identifier = style_tag.groups()
                        wrap_style = (
                            TextStyle.REGULAR
                            if closing
                            else TextStyle(int(identifier))
                        )
                        p2 = style_tag.end()
                        continue
                    vr2 = re.match(r"\{\s*v([\d\s]+)\}", new[p2:], re.IGNORECASE)
                    if vr2:
                        try:
                            vid_t = int(vr2.group(1).replace(" ", ""))
                            cw = vlen[vid_t]
                        except Exception:
                            cw = 0
                        if cur_x + cw > x1 + 0.1 * size and cur_x > x0 + 0.1 * size:
                            if last_space_ptr >= 0:
                                break_positions.add(last_space_ptr + 1)
                                cur_x = x0 + (cur_x - last_space_x_after)
                                last_space_ptr = -1
                                last_space_x_after = x0
                        cur_x += cw
                        p2 += len(vr2.group(0))
                    else:
                        ch2 = new[p2]
                        cw = _measure_char(ch2, wrap_style)
                        if ch2 == ' ':
                            last_space_ptr = p2
                            last_space_x_after = cur_x + cw
                        if cur_x + cw > x1 + 0.1 * size and cur_x > x0 + 0.1 * size:
                            if last_space_ptr >= 0:
                                break_positions.add(last_space_ptr + 1)
                                cur_x = x0 + (cur_x - last_space_x_after)
                                last_space_ptr = -1
                                last_space_x_after = x0
                        cur_x += cw
                        p2 += 1
                # Replace spaces at break positions with newlines (process in reverse)
                for bp in sorted(break_positions, reverse=True):
                    new = new[:bp - 1] + '\n' + new[bp:]

            cstk: str = ""
            fcur: str = None
            lidx = 0
            tx = x
            fcur_ = fcur
            ptr = 0
            active_style = TextStyle.REGULAR
            cstyle = TextStyle.REGULAR
            log.debug(f"< {y} {x} {x0} {x1} {size} {brk} > {sstk[id]} | {new}")

            ops_vals: list[dict] = []

            while ptr < len(new):
                style_tag = STYLE_TAG_PATTERN.match(new, ptr)
                if style_tag:
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur,
                            "size": size,
                            "x": tx,
                            "dy": 0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx,
                            "style": cstyle,
                        })
                        cstk = ""
                    closing, identifier = style_tag.groups()
                    active_style = (
                        TextStyle.REGULAR
                        if closing
                        else TextStyle(int(identifier))
                    )
                    ptr = style_tag.end()
                    continue
                vy_regex = re.match(
                    r"\{\s*v([\d\s]+)\}", new[ptr:], re.IGNORECASE
                )
                mod = 0
                if vy_regex:
                    ptr += len(vy_regex.group(0))
                    try:
                        vid = int(vy_regex.group(1).replace(" ", ""))
                        adv = vlen[vid]
                    except Exception:
                        continue
                    if var[vid][-1].get_text() and unicodedata.category(var[vid][-1].get_text()[0]) in ["Lm", "Mn", "Sk"]:
                        mod = var[vid][-1].width
                else:
                    ch = new[ptr]
                    if ch == '\n':  # Forced line break from word-wrap pre-computation
                        if cstk:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur,
                                "size": size,
                                "x": tx,
                                "dy": 0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx,
                                "style": cstyle,
                            })
                            cstk = ""
                        x = x0
                        lidx += 1
                        ptr += 1
                        continue
                    fcur_, adv = output_font(ch, int(active_style), size)
                    ptr += 1
                if (
                    fcur_ != fcur
                    or vy_regex
                    or x + adv > x1 + 0.1 * size
                ):
                    if cstk:
                        # Word-wrap: if hitting right boundary, break at last space
                        if brk and x + adv > x1 + 0.1 * size and ' ' in cstk:
                            last_space = cstk.rfind(' ')
                            before = cstk[:last_space]
                            after = cstk[last_space + 1:]
                            if before:
                                ops_vals.append({
                                    "type": OpType.TEXT,
                                    "font": fcur,
                                    "size": size,
                                    "x": tx,
                                    "dy": 0,
                                    "rtxt": raw_string(fcur, before),
                                    "lidx": lidx,
                                    "style": cstyle,
                                })
                            # Move remainder to new line
                            lidx += 1
                            x = x0
                            tx = x
                            # Recalculate x for the remaining text
                            for rc in after:
                                x += output_font(rc, int(cstyle), size)[1]
                            cstk = after
                        else:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur,
                                "size": size,
                                "x": tx,
                                "dy": 0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx,
                                "style": cstyle,
                            })
                            cstk = ""
                if brk and x + adv > x1 + 0.1 * size:
                    x = x0
                    lidx += 1
                if vy_regex:
                    fix = 0
                    if fcur is not None:
                        fix = varf[vid]
                    for vch in var[vid]:
                        vc = chr(vch.cid)
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": self.fontid[vch.font],
                            "size": vch.size,
                            "x": x + vch.x0 - var[vid][0].x0,
                            "dy": fix + vch.y0 - var[vid][0].y0,
                            "rtxt": raw_string(self.fontid[vch.font], vc),
                            "lidx": lidx,
                            "style": TextStyle.REGULAR,
                            "orientation": normalised_text_matrix(vch.matrix),
                        })
                        if log.isEnabledFor(logging.DEBUG):
                            lstk.append(LTLine(0.1, (_x, _y), (x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0)))
                            _x, _y = x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0
                    for l in varl[vid]:
                        if l.linewidth < 5:
                            ops_vals.append({
                                "type": OpType.LINE,
                                "x": l.pts[0][0] + x - var[vid][0].x0,
                                "dy": l.pts[0][1] + fix - var[vid][0].y0,
                                "linewidth": l.linewidth,
                                "xlen": l.pts[1][0] - l.pts[0][0],
                                "ylen": l.pts[1][1] - l.pts[0][1],
                                "lidx": lidx
                            })
                else:
                    if not cstk:
                        tx = x
                        cstyle = active_style
                        if x == x0 and ch == " ":
                            adv = 0
                        else:
                            cstk += ch
                    else:
                        cstk += ch
                adv -= mod
                fcur = fcur_
                x += adv
                if log.isEnabledFor(logging.DEBUG):
                    lstk.append(LTLine(0.1, (_x, _y), (x, y)))
                    _x, _y = x, y
            if cstk:
                ops_vals.append({
                    "type": OpType.TEXT,
                    "font": fcur,
                    "size": size,
                    "x": tx,
                    "dy": 0,
                    "rtxt": raw_string(fcur, cstk),
                    "lidx": lidx,
                    "style": cstyle,
                })

            line_height = default_line_height

            # Fit the prose to the box on its own. Charging the formula's extra
            # room to this loop drops the leading for every line in the
            # paragraph, until they collide with each other instead.
            while (lidx + 1) * size * line_height > height and line_height >= 0.8:
                line_height -= 0.05

            # If still overflowing after reducing line_height, shrink font to fit
            if lidx > 0 and (lidx + 1) * size * line_height > height:
                shrink = height / ((lidx + 1) * size * line_height)
                shrink = max(shrink, 0.5)  # Don't go below 50%
                size *= shrink
                for vals in ops_vals:
                    if vals["type"] == OpType.TEXT:
                        vals["size"] *= shrink

            # Measure ink only after the final font-size adjustment.  Measuring
            # before shrinking left the old line gaps in place, so dense table
            # cells used smaller glyphs but still crossed the row below.
            ink = operation_ink(ops_vals)

            if pstk[id].layout_bound is not None and ink:
                # Preserved codes and formula placeholders can be larger than
                # the surrounding translated prose.  The prose-only line count
                # above cannot see that, so fit the union of the actual glyph
                # extents to the cell as a final guard.
                for _attempt in range(3):
                    preview_offsets = line_offsets(
                        ink,
                        lidx,
                        size,
                        line_height,
                        budget=height - (lidx + 1) * size * line_height,
                    )
                    occupied = vertical_ink_extent(ink, preview_offsets)
                    available_height = max(0.0, height - 1.0)
                    if occupied <= available_height + 0.01 or occupied <= 0:
                        break
                    minimum_size = pstk[id].size * 0.5
                    scale = max(minimum_size / max(size, 1e-6), available_height / occupied)
                    scale = min(1.0, scale)
                    if scale >= 0.999:
                        break
                    size *= scale
                    for vals in ops_vals:
                        if vals["type"] == OpType.TEXT:
                            vals["size"] *= scale
                    ink = operation_ink(ops_vals)

            # ponytail: the paragraph's own box is the whole budget, so a
            # formula in an already tight paragraph stays somewhat cramped.
            # Measuring the gap down to the next paragraph would buy the rest.
            offsets = line_offsets(ink, lidx, size, line_height,
                                   budget=height - (lidx + 1) * size * line_height)

            if pstk[id].layout_bound is not None:
                y += vertical_shift_to_bounds(
                    y,
                    ink,
                    offsets,
                    pstk[id].y0 + 0.5,
                    pstk[id].y1 - 0.5,
                )

            for vals in ops_vals:
                if vals["type"] == OpType.TEXT:
                    ops_list.append(
                        gen_op_txt(
                            vals["font"],
                            vals["size"],
                            vals["x"],
                            vals["dy"] + y - offsets[vals["lidx"]],
                            vals["rtxt"],
                            vals.get("style", TextStyle.REGULAR),
                            vals.get("orientation", IDENTITY_ORIENTATION),
                        )
                    )
                elif vals["type"] == OpType.LINE:
                    ops_list.append(gen_op_line(vals["x"], vals["dy"] + y - offsets[vals["lidx"]], vals["xlen"], vals["ylen"], vals["linewidth"]))

        for l in lstk:
            if l.linewidth < 5:
                ops_list.append(gen_op_line(l.pts[0][0], l.pts[0][1], l.pts[1][0] - l.pts[0][0], l.pts[1][1] - l.pts[0][1], l.linewidth))

        ops = f"{white_rects}BT {''.join(ops_list)}ET "
        return ops


def line_offsets(
    ink: dict[int, tuple[float, float]],
    lines: int,
    size: float,
    line_height: float,
    budget: float | None = None,
) -> list[float]:
    """Distance from a paragraph's first baseline down to each later baseline.

    `ink[i]` is how far line i's glyphs reach below and above its own baseline.
    Prose lines get the usual leading; a line holding a tall inline formula gets
    the extra room its glyphs and its neighbour's need, so a fraction's
    denominator no longer lands on the line underneath. `budget` caps that extra
    at the space the paragraph has left, because spilling onto the paragraph
    below looks worse than a formula that is still a little tight.
    """
    base = size * line_height
    want = [
        max(0.0, (ink.get(i + 1, (0.0, 0.0))[1] - ink.get(i, (0.0, 0.0))[0]) - base)
        for i in range(lines)
    ]
    total = sum(want)
    if budget is not None and total > 0 and total > budget:
        # Not enough slack for every tall formula. Share out what there is
        # rather than growing the paragraph down over the text below it.
        scale = max(0.0, budget) / total
        want = [w * scale for w in want]
    offsets = [0.0]
    for extra in want:
        offsets.append(offsets[-1] + base + extra)
    return offsets


class OpType(Enum):
    TEXT = "text"
    LINE = "line"


def operation_ink(
    operations: list[dict],
) -> dict[int, tuple[float, float]]:
    """Measure each rendered line using its final glyph sizes and offsets."""
    ink: dict[int, tuple[float, float]] = {}
    for values in operations:
        size = values["size"] if values["type"] == OpType.TEXT else 0.0
        low = (
            values["dy"]
            + min(0.0, values.get("ylen", 0.0))
            - 0.22 * size
        )
        high = (
            values["dy"]
            + max(0.0, values.get("ylen", 0.0))
            + 0.78 * size
        )
        previous_low, previous_high = ink.get(values["lidx"], (low, high))
        ink[values["lidx"]] = (
            min(previous_low, low),
            max(previous_high, high),
        )
    return ink


def vertical_ink_extent(
    ink: dict[int, tuple[float, float]], offsets: list[float]
) -> float:
    """Return total vertical glyph span after applying per-line offsets."""
    extents = [
        (low - offsets[index], high - offsets[index])
        for index, (low, high) in ink.items()
        if index < len(offsets)
    ]
    if not extents:
        return 0.0
    return max(high for _low, high in extents) - min(low for low, _high in extents)


def vertical_shift_to_bounds(
    baseline: float,
    ink: dict[int, tuple[float, float]],
    offsets: list[float],
    lower: float,
    upper: float,
) -> float:
    """Move a fitted paragraph back inside its cell without changing layout."""
    extents = [
        (baseline + low - offsets[index], baseline + high - offsets[index])
        for index, (low, high) in ink.items()
        if index < len(offsets)
    ]
    if not extents or upper <= lower:
        return 0.0
    minimum = min(low for low, _high in extents)
    maximum = max(high for _low, high in extents)
    if maximum - minimum > upper - lower + 0.01:
        return 0.0
    if minimum < lower:
        return lower - minimum
    if maximum > upper:
        return upper - maximum
    return 0.0
