from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "ocr-spike"))
from audit_translation import FORBIDDEN, audit, overlap_candidates, text_coordinate_bounds  # noqa: E402


class TranslationAuditTests(unittest.TestCase):
    def test_common_punctuation_mojibake_is_forbidden(self):
        self.assertEqual(FORBIDDEN.findall("13\u00e2\u20ac\u201c39; \u00c2\u00a9"), ["â€“", "Â©"])

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
