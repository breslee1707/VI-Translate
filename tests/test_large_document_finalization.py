from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdfminer.pdfexceptions import PDFValueError

from pdf2zh import high_level


class LargeDocumentFinalizationTests(unittest.TestCase):
    def test_app_translation_requests_only_the_mono_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            source.write_bytes(b"%PDF-1.7\n")
            with (
                mock.patch.object(
                    high_level, "pymupdf_can_round_trip", return_value=True
                ),
                mock.patch.object(
                    high_level,
                    "translate_stream",
                    return_value=(
                        b"%PDF-1.7\ntranslated",
                        None,
                        high_level.TranslationReport(translatable_segments=1),
                    ),
                ) as stream,
            ):
                high_level.translate([str(source)], output=str(root))

            self.assertFalse(stream.call_args.kwargs["create_dual"])
            self.assertTrue((root / "book-mono.pdf").is_file())

    def test_fonts_are_never_subset_at_any_size(self):
        """Subsetting renumbers glyphs, and the content stream stores raw glyph
        IDs, so every subset silently repoints the translated text. A short
        Vietnamese report used to lose every stacked-diacritic letter because
        it fell under the old page/size threshold."""
        limit = high_level.LARGE_DOCUMENT_SUBSET_PAGE_LIMIT
        for pages, size in ((1, 0), (limit - 1, 0), (limit, 0),
                            (1, high_level.LARGE_DOCUMENT_BYTE_LIMIT)):
            with self.subTest(pages=pages, size=size):
                self.assertFalse(high_level.should_subset_fonts(pages, False, size))
        self.assertFalse(high_level.should_subset_fonts(1, True))

    def test_repair_follows_the_engine_rather_than_pikepdf(self):
        """pikepdf opens damage that MuPDF refuses on write, so probing with
        pikepdf let a malformed book reach the converter and die there."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            source.write_bytes(b"%PDF-1.7\n")
            repaired = mock.MagicMock()
            with (
                mock.patch.object(
                    high_level, "pymupdf_can_round_trip", side_effect=[False, True]
                ) as probe,
                mock.patch.object(
                    high_level.pikepdf, "open", return_value=repaired
                ) as reopen,
                mock.patch.object(
                    high_level,
                    "translate_stream",
                    return_value=(
                        b"%PDF-1.7\ntranslated",
                        None,
                        high_level.TranslationReport(translatable_segments=1),
                    ),
                ),
            ):
                high_level.translate([str(source)], output=str(root))

            reopen.assert_called_once()
            self.assertEqual(probe.call_count, 2)  # source, then the repaired copy

    def test_a_file_the_repair_cannot_fix_is_reported_not_translated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            source.write_bytes(b"%PDF-1.7\n")
            with (
                mock.patch.object(
                    high_level, "pymupdf_can_round_trip", return_value=False
                ),
                mock.patch.object(
                    high_level.pikepdf, "open", return_value=mock.MagicMock()
                ),
            ):
                with self.assertRaises(PDFValueError) as raised:
                    high_level.translate([str(source)], output=str(root))

            self.assertIn("damaged beyond repair", str(raised.exception))

    def test_large_document_uses_fast_serialization(self):
        limit = high_level.LARGE_DOCUMENT_SUBSET_PAGE_LIMIT
        self.assertEqual(
            high_level.pdf_write_options(limit),
            {"deflate": False, "garbage": 1, "use_objstms": 0},
        )
        self.assertEqual(
            high_level.pdf_write_options(limit - 1),
            {"deflate": True, "garbage": 3, "use_objstms": 1},
        )
        self.assertEqual(
            high_level.pdf_write_options(
                1, high_level.LARGE_DOCUMENT_BYTE_LIMIT
            ),
            {"deflate": False, "garbage": 1, "use_objstms": 0},
        )


if __name__ == "__main__":
    unittest.main()
