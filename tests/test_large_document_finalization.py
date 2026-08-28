from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf2zh import high_level


class LargeDocumentFinalizationTests(unittest.TestCase):
    def test_app_translation_requests_only_the_mono_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            source.write_bytes(b"%PDF-1.7\n")
            fake_pdf = mock.Mock()
            with (
                mock.patch.object(high_level.pikepdf, "open", return_value=fake_pdf),
                mock.patch.object(
                    high_level,
                    "translate_stream",
                    return_value=(b"%PDF-1.7\ntranslated", None, []),
                ) as stream,
            ):
                high_level.translate([str(source)], output=str(root))

            self.assertFalse(stream.call_args.kwargs["create_dual"])
            self.assertTrue((root / "book-mono.pdf").is_file())

    def test_large_document_skips_the_blocking_subset_scan(self):
        limit = high_level.LARGE_DOCUMENT_SUBSET_PAGE_LIMIT
        self.assertTrue(high_level.should_subset_fonts(limit - 1, False))
        self.assertFalse(high_level.should_subset_fonts(limit, False))
        self.assertFalse(
            high_level.should_subset_fonts(
                1, False, high_level.LARGE_DOCUMENT_BYTE_LIMIT
            )
        )
        self.assertFalse(high_level.should_subset_fonts(1, True))

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
