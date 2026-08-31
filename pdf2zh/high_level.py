"""Functions that can be used for the most common use-cases for pdf2zh.six"""

import asyncio
import io
import logging
import os
import re
import sys
import tempfile
from asyncio import CancelledError
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, BinaryIO, Dict, List, Optional

import numpy as np
import pikepdf
import tqdm
from babeldoc.assets.assets import get_font_and_metadata
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfexceptions import PDFValueError
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pymupdf import Document, Font

from pdf2zh.converter import TranslateConverter
from pdf2zh.doclayout import OnnxModel
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.rules import (
    classify_preserved_page,
    cluster_table_words,
    formula_regions,
    is_scanned_page,
    matching_table_cells,
    page_has_image,
    should_translate_table_cell,
)

@dataclass(frozen=True)
class TranslationReport:
    """What a run could not translate, and why.

    The count on its own was ambiguous: a page of diagrams, a document that is
    all scans, and a dropped connection all arrived as the same number, and the
    caller had to guess. Each of those needs the user to do something
    different, so the reasons travel with the count.
    """

    failures: list[str] = field(default_factory=list)
    reasons: Counter = field(default_factory=Counter)
    image_only_pages: set[int] = field(default_factory=set)
    translatable_segments: int = 0
    pages_processed: int = 0

    def __len__(self) -> int:
        return len(self.failures)


NOTO_NAME = "noto"
STYLE_FONT_NAMES = {
    0: NOTO_NAME,
    1: "noto-bold",
    2: "noto-italic",
    3: "noto-bolditalic",
}
BASE14_STYLE_FONTS = {0: "tiro", 1: "tibo", 2: "tiit", 3: "tibi"}

logger = logging.getLogger(__name__)
LARGE_DOCUMENT_SUBSET_PAGE_LIMIT = 200
LARGE_DOCUMENT_BYTE_LIMIT = 50 * 1024 * 1024


def is_large_document(page_count: int, source_size: int = 0) -> bool:
    return (
        page_count >= LARGE_DOCUMENT_SUBSET_PAGE_LIMIT
        or source_size >= LARGE_DOCUMENT_BYTE_LIMIT
    )


def should_subset_fonts(
    page_count: int, skip_subset_fonts: bool, source_size: int = 0
) -> bool:
    """Never subset: the converter writes raw glyph IDs into Identity-H fonts.

    `raw_string()` emits `font.has_glyph(ord(c))` as the CID, so any pass that
    renumbers glyphs repoints every translated character at a different
    outline. It cost Vietnamese every stacked-diacritic letter ("Viet Nam"
    where "Viet" needed U+1EC7) on documents small enough to fall under the old
    page/size threshold. The parameters are kept so the call site still reads
    as a decision rather than a silent omission.
    """
    return False


def pdf_write_options(page_count: int, source_size: int = 0) -> dict[str, int | bool]:
    """Choose fast, low-memory serialization for large documents.

    Recompressing and garbage-collecting every object in a long textbook can
    hold the CPython GIL for tens of seconds.  A light cleanup is almost the same
    size for image-heavy books and lets the GUI finish promptly.
    """
    if is_large_document(page_count, source_size):
        return {"deflate": False, "garbage": 1, "use_objstms": 0}
    return {"deflate": True, "garbage": 3, "use_objstms": 1}


def output_style_font_paths(language: str, regular_path: str) -> dict[int, str]:
    """Resolve regular/bold/italic/bold-italic fonts for translated prose.

    Vietnamese desktop builds run on Windows, where Times New Roman ships with
    all four faces. Other environments retain the existing Unicode font and let
    the converter synthesize missing weight/slant instead of downloading a new
    family at render time.
    """
    regular = str(Path(regular_path))
    result = {style: regular for style in STYLE_FONT_NAMES}
    if Path(regular).name.lower() != "times.ttf":
        return result
    windows_variants = {
        1: Path("C:/Windows/Fonts/timesbd.ttf"),
        2: Path("C:/Windows/Fonts/timesi.ttf"),
        3: Path("C:/Windows/Fonts/timesbi.ttf"),
    }
    for style, path in windows_variants.items():
        if path.is_file():
            result[style] = str(path)
    return result

noto_list = [
    "am",  # Amharic
    "ar",  # Arabic
    "bn",  # Bengali
    "bg",  # Bulgarian
    "chr",  # Cherokee
    "el",  # Greek
    "gu",  # Gujarati
    "iw",  # Hebrew
    "hi",  # Hindi
    "kn",  # Kannada
    "ml",  # Malayalam
    "mr",  # Marathi
    "ru",  # Russian
    "sr",  # Serbian
    "ta",  # Tamil
    "te",  # Telugu
    "th",  # Thai
    "ur",  # Urdu
    "uk",  # Ukrainian
]


def pymupdf_can_round_trip(path: Path) -> bool:
    """Report whether the engine can both read and rewrite this document.

    pikepdf tolerates structural damage that MuPDF later refuses on write, so
    probing with pikepdf let a malformed 517-page book reach `translate_stream`
    and die there with "invalid key in dict". The engine's own round trip is
    the only probe that predicts the failure it is meant to prevent, and it
    costs under a second even on a 48 MB book.
    """
    document = None
    try:
        document = Document(str(path))
        document.save(io.BytesIO())
    except Exception:
        return False
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass  # a document that failed to save also fails to close
    return True


def check_files(files: List[str]) -> List[str]:
    files = [
        f for f in files if not f.startswith("http://")
    ]  # exclude online files, http
    files = [
        f for f in files if not f.startswith("https://")
    ]  # exclude online files, https
    missing_files = [file for file in files if not os.path.exists(file)]
    return missing_files


def translate_patch(
    inf: BinaryIO,
    pages: Optional[list[int]] = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    noto_name: str = "",
    noto: Font = None,
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    ignore_cache: bool = False,
    style_font_names: Dict | None = None,
    style_fonts: Dict | None = None,
    synthetic_styles: set[int] | None = None,
    **kwarg: Any,
) -> None:
    rsrcmgr = PDFResourceManager()
    layout = {}
    layout_bounds = {}
    scanned_pages = set()
    pages_with_images = set()
    device = TranslateConverter(
        rsrcmgr,
        vfont,
        vchar,
        thread,
        layout,
        lang_in,
        lang_out,
        service,
        noto_name,
        noto,
        envs,
        prompt,
        ignore_cache,
        layout_bounds,
        style_font_names,
        style_fonts,
        synthetic_styles,
    )

    assert device is not None
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch)
    if pages:
        total_pages = len(pages)
    else:
        total_pages = doc_zh.page_count

    parser = PDFParser(inf)
    doc = PDFDocument(parser)
    with tqdm.tqdm(total=total_pages) as progress:
        for pageno, page in enumerate(PDFPage.create_pages(doc)):
            if cancellation_event and cancellation_event.is_set():
                raise CancelledError("task cancelled")
            if pages and (pageno not in pages):
                continue
            progress.update()
            if callback:
                callback(progress)
            page.pageno = pageno
            page_rect = doc_zh[page.pageno].rect
            page_area = page_rect.width * page_rect.height
            page_blocks = doc_zh[page.pageno].get_text("dict")["blocks"]
            if is_scanned_page(page_blocks, page_area):
                scanned_pages.add(pageno)
            if page_has_image(page_blocks):
                pages_with_images.add(pageno)
            pix = doc_zh[page.pageno].get_pixmap()
            image = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, 3
            )[:, :, ::-1]
            page_layout = model.predict(image, imgsz=int(pix.height / 32) * 32)[0]
            box = np.ones((pix.height, pix.width))
            h, w = box.shape
            vcls = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]
            model_table_bounds = []
            # Process non-vcls boxes in ascending confidence order so that
            # higher-confidence boxes overwrite lower-confidence ones
            non_vcls_boxes = [
                (i, d) for i, d in enumerate(page_layout.boxes)
                if page_layout.names[int(d.cls)] not in vcls
            ]
            for i, d in reversed(non_vcls_boxes):
                x0, y0, x1, y1 = d.xyxy.squeeze()
                x0, y0, x1, y1 = (
                    np.clip(int(x0 - 1), 0, w - 1),
                    np.clip(int(h - y1 - 1), 0, h - 1),
                    np.clip(int(x1 + 1), 0, w - 1),
                    np.clip(int(h - y0 + 1), 0, h - 1),
                )
                box[y0:y1, x0:x1] = i + 2
            for i, d in enumerate(page_layout.boxes):
                name = page_layout.names[int(d.cls)]
                if name in vcls:
                    raw_x0, raw_y0, raw_x1, raw_y1 = (
                        float(value) for value in d.xyxy.squeeze()
                    )
                    if name == "table":
                        model_table_bounds.append(
                            (raw_x0, raw_y0, raw_x1, raw_y1)
                        )
                    x0, y0, x1, y1 = raw_x0, raw_y0, raw_x1, raw_y1
                    x0, y0, x1, y1 = (
                        np.clip(int(x0 - 1), 0, w - 1),
                        np.clip(int(h - y1 - 1), 0, h - 1),
                        np.clip(int(x1 + 1), 0, w - 1),
                        np.clip(int(h - y0 + 1), 0, h - 1),
                    )
                    box[y0:y1, x0:x1] = 0

            # A model-detected table stays protected unless PyMuPDF can split
            # that same region into cells. Each reliable cell gets its own class
            # so its text is translated independently while the original grid,
            # fills and borders remain untouched.
            source_page = doc_zh[page.pageno]
            try:
                detected_tables = source_page.find_tables().tables
            except Exception as error:
                logger.warning(
                    "Page %s table-cell detection failed; preserving tables: %s",
                    pageno + 1,
                    error,
                )
                detected_tables = []
            next_class = len(page_layout.boxes) + 2
            page_bounds = layout_bounds.setdefault(page.pageno, {})
            page_height = float(page_rect.height)
            page_words = source_page.get_text("words", sort=True)
            for table_bounds in model_table_bounds:
                for cell in matching_table_cells(table_bounds, detected_tables):
                    cx0 = max(float(cell[0]), table_bounds[0])
                    cy0 = max(float(cell[1]), table_bounds[1])
                    cx1 = min(float(cell[2]), table_bounds[2])
                    cy1 = min(float(cell[3]), table_bounds[3])
                    if cx1 - cx0 <= 4 or cy1 - cy0 <= 1:
                        continue
                    cell_words = [
                        word
                        for word in page_words
                        if cx0 <= (float(word[0]) + float(word[2])) / 2 <= cx1
                        and cy0 <= (float(word[1]) + float(word[3])) / 2 <= cy1
                    ]
                    for cluster in cluster_table_words(
                        cell_words, (cx0, cy0, cx1, cy1)
                    ):
                        if not should_translate_table_cell(cluster.text):
                            continue
                        for word in cluster.words:
                            wx0, wy0, wx1, wy1 = (
                                float(value) for value in word[:4]
                            )
                            px0, py0, px1, py1 = (
                                np.clip(int(wx0 - 1), 0, w - 1),
                                np.clip(int(h - wy1 - 1), 0, h - 1),
                                np.clip(int(wx1 + 1), 0, w - 1),
                                np.clip(int(h - wy0 + 1), 0, h - 1),
                            )
                            box[py0:py1, px0:px1] = next_class
                        bx0, by0, bx1, by1 = cluster.bbox
                        content_y0 = min(by0, *(float(word[1]) for word in cluster.words))
                        content_y1 = max(by1, *(float(word[3]) for word in cluster.words))
                        padded = (
                            bx0 + 2.0,
                            page_height - content_y1,
                            bx1 - 2.0,
                            page_height - content_y0,
                        )
                        if padded[2] > padded[0] and padded[3] > padded[1]:
                            page_bounds[next_class] = padded
                        next_class += 1

            # Technical documents often use ordinary prose fonts for equations.
            # Protect operator-only blocks and stacked identifiers before the
            # converter can reflow them. A one-point pad catches their rules and
            # small subscripts without swallowing adjacent prose.
            fallback_formulas = formula_regions(
                source_page.get_text("blocks", sort=True),
                page_words,
                stacked_exclusions=model_table_bounds,
            )
            for fx0, fy0, fx1, fy1 in fallback_formulas:
                bx0, by0, bx1, by1 = (
                    np.clip(int(fx0 - 1), 0, w - 1),
                    np.clip(int(h - fy1 - 1), 0, h - 1),
                    np.clip(int(fx1 + 1), 0, w - 1),
                    np.clip(int(h - fy0 + 1), 0, h - 1),
                )
                box[by0:by1, bx0:bx1] = 0

            # Detect TOC pages by analyzing extracted text patterns
            page_text = doc_zh[page.pageno].get_text("text")
            lines = [l.strip() for l in page_text.split('\n') if l.strip()]
            toc_score = 0
            standalone_nums = 0
            spaced_page_nums = 0
            emspace_page_nums = 0
            for line in lines:
                # dot leaders: ". . . . ." or "......"
                if re.search(r'\.{5,}', line) or re.search(r'(\.\s){4,}', line):
                    toc_score += 3
                # unicode dot leaders / replacement chars used as dot leaders
                # e.g. "\x08�����" or "───" or other fill chars before page number
                elif re.search(r'[\x08\ufffd\u2500-\u257f]{3,}', line):
                    toc_score += 3
                # line ending with page number after fill chars
                elif re.search(r'[\x08\ufffd\u2500-\u257f]+\s*\d{1,4}\s*$', line):
                    toc_score += 2
                # text followed by 5+ spaces then a page number (space-padded TOC)
                elif re.search(r'\S\s{5,}\d{1,4}\s*$', line):
                    spaced_page_nums += 1
                # standalone page number on its own line (1-4 digits only)
                elif re.match(r'^\d{1,4}$', line):
                    standalone_nums += 1
                # em-space / en-space separator before page number or roman numeral
                if re.search(r'[\u2002\u2003]+\s*\d{1,4}\s*$', line):
                    emspace_page_nums += 1
                elif re.search(r'[\u2002\u2003]+\s*[ivxlcdm]+\s*$', line, re.IGNORECASE):
                    emspace_page_nums += 1
            # Check if any line says "Contents" or "Table of Contents"
            has_contents_header = any(
                re.match(r'^(table\s+of\s+)?contents?$', l, re.IGNORECASE)
                for l in lines[:5]
            )
            if has_contents_header:
                toc_score += 5
            # Space-padded page numbers (text + spaces + number): strong TOC signal
            if spaced_page_nums >= 5:
                toc_score += spaced_page_nums
            # Em-space / en-space separated page numbers: common in e-books
            if emspace_page_nums >= 5:
                toc_score += emspace_page_nums
            # Many standalone numbers = TOC sub-entries, but only if there are other TOC signals
            if standalone_nums >= 8 and toc_score > 0:
                toc_score += standalone_nums
            # High ratio of standalone numbers to total lines indicates TOC-like structure
            if len(lines) >= 15 and standalone_nums >= 10 and (standalone_nums / len(lines)) > 0.3:
                toc_score += standalone_nums
            # Lines ending with "text number" (single-space TOC style): if >80% match, it's TOC
            if len(lines) >= 15:
                lines_ending_num = sum(1 for l in lines if re.search(r'\S\s+\d{1,4}\s*$', l))
                if lines_ending_num / len(lines) > 0.8:
                    toc_score += lines_ending_num
            if toc_score >= 8:
                logger.info(f"Page {pageno + 1} detected as TOC (score={toc_score}), preserving original layout")
                box[:, :] = 0

            # Detect INDEX pages: many lines ending with page numbers, comma+number patterns
            if not (toc_score >= 8):
                idx_comma_num = sum(1 for l in lines if re.search(r',\s*\d{1,4}', l))
                is_index_header = bool(lines and re.match(r'^index$', lines[0], re.IGNORECASE))
                # Index pages typically have >50% lines ending with "term, number"
                if len(lines) >= 20 and (idx_comma_num / len(lines)) > 0.4:
                    logger.info(f"Page {pageno + 1} detected as INDEX (comma_num={idx_comma_num}/{len(lines)}), preserving original layout")
                    box[:, :] = 0
                elif is_index_header:
                    logger.info(f"Page {pageno + 1} detected as INDEX (header), preserving original layout")
                    box[:, :] = 0

            # Detect NOMENCLATURE / symbol list pages: short symbol lines alternating with definitions
            if not (toc_score >= 8) and box.any():
                has_nomenclature_header = any(
                    re.match(r'^(nomenclature|list\s+of\s+symbols|symbols?\s+and\s+abbreviations?|glossary|notation)s?$', l, re.IGNORECASE)
                    for l in lines[:5]
                )
                if has_nomenclature_header and len(lines) >= 10:
                    # Count symbol-definition pairs: short line (≤15 chars) followed by longer description
                    symbol_def_pairs = sum(
                        1 for i in range(len(lines) - 1)
                        if len(lines[i]) <= 15 and len(lines[i + 1]) > 5 and not lines[i].isdigit()
                    )
                    if symbol_def_pairs / len(lines) > 0.3:
                        logger.info(f"Page {pageno + 1} detected as NOMENCLATURE (pairs={symbol_def_pairs}/{len(lines)}), preserving original layout")
                        box[:, :] = 0

            # Detect REFERENCE / bibliography pages: numbered entries with years, ISBN/DOI
            if not (toc_score >= 8) and box.any():
                has_ref_header = any(
                    re.match(r'^[\xad]?(references?|bibliography|suggested\s+reading|further\s+reading|works?\s+cited)$', l, re.IGNORECASE)
                    for l in lines[:10]
                )
                numbered_refs = sum(1 for l in lines if re.match(r'^\d{1,3}\.\s', l))
                # Author-year style: "Surname, I. (2014)." or "[1] Author..."
                author_year_refs = sum(1 for l in lines if re.match(r'^[A-Z][a-z]+,?\s.*\(\d{4}\)', l))
                bracketed_refs = sum(1 for l in lines if re.match(r'^\[\d{1,3}\]', l))
                year_paren = sum(1 for l in lines if re.search(r'\(\d{4}\)', l))
                isbn_doi = sum(1 for l in lines if re.search(r'ISBN|ISSN|doi\.org|https?://', l, re.IGNORECASE))
                all_refs = numbered_refs + author_year_refs + bracketed_refs
                ref_signals = all_refs + year_paren + isbn_doi
                if has_ref_header and ref_signals >= 5:
                    logger.info(f"Page {pageno + 1} detected as REFERENCES (header, refs={all_refs}, years={year_paren}, isbn_doi={isbn_doi}), preserving original layout")
                    box[:, :] = 0
                elif len(lines) >= 10 and all_refs >= 5 and (year_paren + isbn_doi) >= 3:
                    logger.info(f"Page {pageno + 1} detected as REFERENCES (refs={all_refs}, years={year_paren}, isbn_doi={isbn_doi}), preserving original layout")
                    box[:, :] = 0

            preservation = classify_preserved_page(page_text)
            if preservation is not None and box.any():
                logger.info(
                    "Page %s detected as %s (%s), preserving original layout",
                    pageno + 1,
                    preservation.kind,
                    preservation.detail,
                )
                box[:, :] = 0

            layout[page.pageno] = box
            if pageno in scanned_pages:
                device.scanned_pages.add(pageno)
            if pageno in pages_with_images:
                device.pages_with_images.add(pageno)
            page.page_xref = doc_zh.get_new_xref()
            doc_zh.update_object(page.page_xref, "<<>>")
            doc_zh.update_stream(page.page_xref, b"")
            doc_zh[page.pageno].set_contents(page.page_xref)
            interpreter.process_page(page)

    device.close()
    return obj_patch, TranslationReport(
        failures=device.translation_failures,
        reasons=device.failure_reasons,
        image_only_pages=device.image_only_pages,
        translatable_segments=device.translatable_segments,
        pages_processed=total_pages,
    )


def translate_stream(
    stream: bytes,
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    create_dual: bool = True,
    ignore_cache: bool = False,
    **kwarg: Any,
):
    source_size = len(stream)
    font_path = download_remote_fonts(lang_out.lower())
    style_paths = output_style_font_paths(lang_out.lower(), font_path)
    style_font_names = dict(STYLE_FONT_NAMES)
    style_fonts = {
        style: Font(style_font_names[style], path)
        for style, path in style_paths.items()
    }
    synthetic_styles = {
        style for style, path in style_paths.items() if style and path == style_paths[0]
    }
    font_list = [(name, None) for name in BASE14_STYLE_FONTS.values()]
    noto_name = NOTO_NAME
    noto = style_fonts[0]
    font_list.extend(
        (style_font_names[style], path) for style, path in style_paths.items()
    )

    doc_en = Document(stream=stream)
    stream = io.BytesIO()
    doc_en.save(stream)
    doc_zh = Document(stream=stream)
    if not create_dual:
        doc_en.close()
    page_count = doc_zh.page_count
    # font_list = [("GoNotoKurrent-Regular.ttf", font_path), ("tiro", None)]
    font_id = {}
    for page in doc_zh:
        for font in font_list:
            font_id[font[0]] = page.insert_font(font[0], font[1])
    xreflen = doc_zh.xref_length()
    for xref in range(1, xreflen):
        for label in ["Resources/", ""]:
            try:
                font_res = doc_zh.xref_get_key(xref, f"{label}Font")
                target_key_prefix = f"{label}Font/"
                if font_res[0] == "xref":
                    resource_xref_id = re.search("(\\d+) 0 R", font_res[1]).group(1)
                    xref = int(resource_xref_id)
                    font_res = ("dict", doc_zh.xref_object(xref))
                    target_key_prefix = ""

                if font_res[0] == "dict":
                    for font in font_list:
                        target_key = f"{target_key_prefix}{font[0]}"
                        font_exist = doc_zh.xref_get_key(xref, target_key)
                        if font_exist[0] == "null":
                            doc_zh.xref_set_key(
                                xref,
                                target_key,
                                f"{font_id[font[0]]} 0 R",
                            )
            except Exception:
                pass

    fp = io.BytesIO()

    doc_zh.save(fp)
    obj_patch, report = translate_patch(fp, **locals())

    for obj_id, ops_new in obj_patch.items():
        # ops_old=doc_en.xref_stream(obj_id)
        # print(obj_id)
        # print(ops_old)
        # print(ops_new.encode())
        doc_zh.update_stream(obj_id, ops_new.encode())

    if create_dual:
        doc_en.insert_file(doc_zh)
        for id in range(page_count):
            doc_en.move_page(page_count + id, id * 2 + 1)

    # Off for every document; see should_subset_fonts. It was also the
    # expensive half of finalizing a textbook, so dropping it costs nothing but
    # the size of the four embedded output faces.
    if should_subset_fonts(page_count, skip_subset_fonts, source_size):
        doc_zh.subset_fonts(fallback=True)
        if create_dual:
            doc_en.subset_fonts(fallback=True)
    write_options = pdf_write_options(page_count, source_size)
    mono = doc_zh.write(**write_options)
    dual = (
        doc_en.write(**write_options)
        if create_dual
        else None
    )
    return (
        mono,
        dual,
        report,
    )


def convert_to_pdfa(input_path, output_path):
    """
    Convert PDF to PDF/A format

    Args:
        input_path: Path to source PDF file
        output_path: Path to save PDF/A file
    """
    from pikepdf import Dictionary, Name, Pdf

    # Open the PDF file
    pdf = Pdf.open(input_path)

    # Add PDF/A conformance metadata
    metadata = {
        "pdfa_part": "2",
        "pdfa_conformance": "B",
        "title": pdf.docinfo.get("/Title", ""),
        "author": pdf.docinfo.get("/Author", ""),
        "creator": "PDF Math Translate",
    }

    with pdf.open_metadata() as meta:
        meta.load_from_docinfo(pdf.docinfo)
        meta["pdfaid:part"] = metadata["pdfa_part"]
        meta["pdfaid:conformance"] = metadata["pdfa_conformance"]

    # Create OutputIntent dictionary
    output_intent = Dictionary(
        {
            "/Type": Name("/OutputIntent"),
            "/S": Name("/GTS_PDFA1"),
            "/OutputConditionIdentifier": "sRGB IEC61966-2.1",
            "/RegistryName": "http://www.color.org",
            "/Info": "sRGB IEC61966-2.1",
        }
    )

    # Add output intent to PDF root
    if "/OutputIntents" not in pdf.Root:
        pdf.Root.OutputIntents = [output_intent]
    else:
        pdf.Root.OutputIntents.append(output_intent)

    # Save as PDF/A
    pdf.save(output_path, linearize=True)
    pdf.close()


def translate(
    files: list[str],
    output: str = "",
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    compatible: bool = False,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    ignore_cache: bool = False,
    **kwarg: Any,
):
    if not files:
        raise PDFValueError("No files to process.")

    missing_files = check_files(files)

    if missing_files:
        print("The following files do not exist:", file=sys.stderr)
        for file in missing_files:
            print(f"  {file}", file=sys.stderr)
        raise PDFValueError("Some files do not exist.")

    result_files = []

    for file in files:
        source_path = Path(file).resolve()
        if source_path.suffix.lower() != ".pdf":
            raise PDFValueError(f"Only PDF input is supported: {source_path}")
        filename = source_path.stem
        processing_path = source_path
        temporary_paths: list[Path] = []

        if not pymupdf_can_round_trip(source_path):
            logger.warning(
                "PDF structure issue detected in %s; translating a repaired temporary copy",
                source_path,
            )
            try:
                with tempfile.NamedTemporaryFile(suffix="-fixed.pdf", delete=False) as temporary:
                    fixed_path = Path(temporary.name)
                with pikepdf.open(source_path, suppress_warnings=True) as fixed_pdf:
                    fixed_pdf.save(fixed_path)
                processing_path = fixed_path
                temporary_paths.append(fixed_path)
            except Exception as error:
                raise PDFValueError(f"Could not repair PDF structure: {source_path}") from error
            if not pymupdf_can_round_trip(processing_path):
                # Say so here rather than letting the same MuPDF syntax error
                # resurface from deep inside the conversion, where it reads as
                # an engine bug instead of an unreadable source document.
                raise PDFValueError(
                    f"PDF structure is damaged beyond repair: {source_path}"
                )

        if compatible:
            with tempfile.NamedTemporaryFile(suffix="-pdfa.pdf", delete=False) as temporary:
                pdfa_path = Path(temporary.name)
            convert_to_pdfa(processing_path, pdfa_path)
            processing_path = pdfa_path
            temporary_paths.append(pdfa_path)

        s_raw = processing_path.read_bytes()
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)

        try:
            s_mono, _s_dual, report = translate_stream(
                s_raw,
                create_dual=False,
                **locals(),
            )
            if report.failures:
                logger.warning(
                    "%d of the segments in %s could not be translated and were left "
                    "in the source language (%s)",
                    len(report.failures),
                    source_path,
                    ", ".join(
                        f"{reason} x{count}"
                        for reason, count in report.reasons.most_common()
                    ),
                )
            file_mono = Path(output) / f"{filename}-mono.pdf"
            doc_mono = open(file_mono, "wb")
            doc_mono.write(s_mono)
            doc_mono.close()
            result_files.append((str(file_mono), report))
        except Exception as error:
            raise PDFValueError(f"Failed to translate {source_path}") from error

    return result_files


def download_remote_fonts(lang: str):
    lang = lang.lower()
    LANG_NAME_MAP = {
        **{la: "GoNotoKurrent-Regular.ttf" for la in noto_list},
        **{
            la: f"SourceHanSerif{region}-Regular.ttf"
            for region, langs in {
                "CN": ["zh-cn", "zh-hans", "zh"],
                "TW": ["zh-tw", "zh-hant"],
                "JP": ["ja"],
                "KR": ["ko"],
            }.items()
            for la in langs
        },
    }

    # Use Times New Roman for Vietnamese
    if lang == "vi":
        times_path = Path("C:/Windows/Fonts/times.ttf")
        if times_path.exists():
            logger.info(f"use font: {times_path.as_posix()}")
            return times_path.as_posix()

    font_name = LANG_NAME_MAP.get(lang, "GoNotoKurrent-Regular.ttf")

    # docker
    font_path = os.environ.get("NOTO_FONT_PATH", Path("/app", font_name).as_posix())
    if not Path(font_path).exists():
        font_path, _ = get_font_and_metadata(font_name)
        font_path = font_path.as_posix()

    logger.info(f"use font: {font_path}")

    return font_path
