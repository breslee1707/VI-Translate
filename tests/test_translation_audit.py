from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

from pdf2zh.ocr import OCR_FONT_PATH

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "ocr-spike"))
from audit_translation import (  # noqa: E402
    FORBIDDEN,
    audit,
    mojibake_lines,
    overlap_candidates,
    text_coordinate_bounds,
)


UNDEFINED_IN_CP1252 = (0x81, 0x8D, 0x8F, 0x90, 0x9D)


def damaged(value: str) -> str:
    """UTF-8 bytes read as Windows-1252, the way a mishandled JSONL arrives.

    A lenient reader passes the five undefined bytes through unchanged, so
    they surface as C1 control characters rather than raising.
    """
    return "".join(
        chr(byte) if byte in UNDEFINED_IN_CP1252 else bytes([byte]).decode("cp1252")
        for byte in value.encode("utf-8")
    )


class TranslationAuditTests(unittest.TestCase):
    def test_common_punctuation_mojibake_is_forbidden(self):
        self.assertEqual(FORBIDDEN.findall("13\u00e2\u20ac\u201c39; \u00c2\u00a9"), ["â€“", "Â©"])

    def test_damaged_vietnamese_letters_fail_the_structural_gate(self):
        """FORBIDDEN knows seven punctuation sequences and none of these.

        A handoff table that crossed the encoding boundary reached the page as
        unreadable text while every gate reported success, so the damage has to
        be caught on the page and not only in the table.
        """
        # Lower case on purpose: Á and Í damage into bytes cp1252 leaves
        # undefined, and no PDF font can carry those control characters.
        broken = damaged("các phân tử bám dính tế bào")
        self.assertEqual(FORBIDDEN.findall(broken), [])
        self.assertEqual(mojibake_lines(broken), [broken])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source.pdf", root / "output.pdf"
            doc = pymupdf.open()
            doc.new_page().insert_text((72, 72), "CELL ADHESION MOLECULES")
            doc.save(source)
            doc.close()
            doc = pymupdf.open()
            page = doc.new_page()
            # The same embedded Unicode font the pipeline writes prose with; a
            # base font silently drops these characters instead of storing them.
            page.insert_font(fontname="vi", fontfile=str(OCR_FONT_PATH))
            page.insert_text((72, 72), broken, fontname="vi")
            doc.save(output)
            doc.close()
            report = audit(source, output, {1})
            self.assertEqual(report["pages"][0]["forbidden_markers"], [])
            self.assertEqual(report["pages"][0]["mojibake_lines"], [broken])
            self.assertFalse(report["structural_gate"])

    def test_correct_vietnamese_and_ranges_are_not_flagged(self):
        """Â and Ã are Vietnamese letters; a marker search condemns a good page."""
        self.assertEqual(mojibake_lines("CÁC PHÂN TỬ BÁM DÍNH TẾ BÀO"), [])
        self.assertEqual(mojibake_lines("Nam: 0–0.8 sigma unit/mL"), [])
        self.assertEqual(mojibake_lines("Osteoblasts secrete 884-2050 nmol"), [])

    def test_postal_overlap_is_flagged_even_when_inside_canvas(self):
        spans = [{"text": "Information Desk", "bbox": (50, 100, 150, 112)},
                 {"text": "7121 Standard Drive", "bbox": (52, 103, 170, 115)},
                 {"text": "next row", "bbox": (50, 125, 140, 137)}]
        result = overlap_candidates(spans)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["second"], "7121 Standard Drive")

    def test_audit_rejects_changes_on_unselected_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source.pdf", root / "output.pdf"
            doc = pymupdf.open()
            for text in ("first page", "second page"):
                doc.new_page().insert_text((72, 72), text)
            doc.save(source)
            doc[1].insert_text((72, 90), "unexpected collateral change")
            doc.save(output)
            doc.close()
            report = audit(source, output, {1})
            self.assertFalse(report["structural_gate"])
            self.assertFalse(report["pages"][1]["raster_identical"])

    def test_unchanged_scan_is_structurally_valid_but_not_translation_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            doc = pymupdf.open()
            page = doc.new_page(width=300, height=200)
            raster = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), False)
            raster.clear_with(240)
            page.insert_image(page.rect, pixmap=raster)
            doc.save(source)
            doc.close()
            report = audit(source, source, {1})
            self.assertTrue(report["structural_gate"])
            self.assertTrue(report["pages"][0]["raster_identical"])
            self.assertEqual(report["visual_review"], "pending")
            self.assertNotIn("translation_success", report)

    def test_rotated_page_uses_unrotated_text_coordinate_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "rotated.pdf"
            doc = pymupdf.open()
            page = doc.new_page(width=200, height=300)
            page.insert_text((20, 250), "bottom row")
            page.set_rotation(90)
            doc.save(source)
            doc.close()
            with pymupdf.open(source) as reopened:
                self.assertEqual(tuple(text_coordinate_bounds(reopened[0])), (0.0, 0.0, 200.0, 300.0))
            report = audit(source, source, {1})
            self.assertTrue(report["structural_gate"])
            self.assertEqual(report["pages"][0]["out_of_canvas"], [])
