from __future__ import annotations

import gzip
import os
import pickle
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pymupdf
from pdfminer.cmapdb import CMapDB
from pdfminer.pdffont import PDFCIDFont
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.psparser import LIT

from pdf2zh.high_level import translate_stream
from pdf2zh.ocr import OCR_FONT_PATH
from tests.test_google_batching import google_page, line_by_line
from tests.test_ocr import EmptyLayoutModel
from tests.test_service_outage import translator_answering

HELVETICA = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
# A Windows path reaches a share over SMB or WebDAV: the file never has to be
# on the machine at all.
HOSTILE_NAMES = (
    "\\\\attacker.example\\share\\evil",
    "//attacker.example/share/evil",
    "/tmp/evil",
    "C:\\Users\\Public\\evil",
    "..\\..\\evil",
    "../evil",
)


@contextmanager
def no_file_is_looked_for():
    """Fail on any look at the disk, whichever loader makes it."""
    with ExitStack() as stack:
        calls = [
            stack.enter_context(patch(target, side_effect=AssertionError(f"{target} called")))
            for target in ("gzip.open", "os.path.exists", "pathlib.Path.is_file")
        ]
        yield
    for call in calls:
        call.assert_not_called()


def page_with(document: pymupdf.Document, resources: str, content: bytes) -> None:
    page = document.new_page(width=300, height=200)
    document.xref_set_key(page.xref, "Resources", resources)
    contents = document.get_new_xref()
    document.update_object(contents, "<< >>")
    document.update_stream(contents, content)
    document.xref_set_key(page.xref, "Contents", f"{contents} 0 R")


def translated(source: bytes, model=None):
    translator, _clock, google = translator_answering(
        lambda text: google_page(line_by_line(text))
    )
    with (
        patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)),
        patch("pdf2zh.high_level.output_style_font_paths", return_value={0: str(OCR_FONT_PATH)}),
        patch.dict("pdf2zh.converter.ENGINES", {"google": lambda *args, **kwargs: translator}),
    ):
        mono, _dual, report = translate_stream(
            source, lang_in="en", lang_out="vi", service="google", thread=1,
            model=model or EmptyLayoutModel(), create_dual=False, ignore_cache=True,
        )
    return mono, report, google.sent


class FormXObjectCycleTests(unittest.TestCase):
    def test_a_form_that_draws_itself_is_translated_once(self):
        """It recursed until Python gave up, and the whole document went with it."""
        document = pymupdf.open()
        form = document.get_new_xref()
        document.update_object(
            form,
            "<< /Type /XObject /Subtype /Form /BBox [0 0 300 200] "
            f"/Resources << /Font << /F1 {HELVETICA} >> /XObject << /Fm0 {form} 0 R >> >> >>",
        )
        document.update_stream(form, b"BT /F1 11 Tf 20 60 Td (The form says hello.) Tj ET /Fm0 Do")
        page_with(
            document,
            f"<< /Font << /F1 {HELVETICA} >> /XObject << /Fm0 {form} 0 R >> >>",
            b"BT /F1 11 Tf 20 150 Td (The page says hello.) Tj ET /Fm0 Do",
        )
        mono, report, sent = translated(document.tobytes())
        self.assertEqual(sorted(sent), ["The form says hello.", "The page says hello."])
        self.assertEqual(report.failures, [])
        with pymupdf.open(stream=mono) as output:
            self.assertEqual(
                output[0].get_text().split("\n")[:2],
                ["vi:The form says hello.", "vi:The page says hello."],
            )


class ICCColourSpaceTests(unittest.TestCase):
    def test_a_profile_without_a_component_count_uses_its_alternate(self):
        """Reading /N unchecked was a KeyError that lost the whole document."""
        document = pymupdf.open()
        profile = document.get_new_xref()
        document.update_object(profile, "<< /Alternate /DeviceRGB >>")
        document.update_stream(profile, b"not really a profile")
        page_with(
            document,
            f"<< /Font << /F1 {HELVETICA} >> /ColorSpace << /CS0 [/ICCBased {profile} 0 R] >> >>",
            b"/CS0 cs 0 0 1 scn BT /F1 11 Tf 20 150 Td (Blue words on the page.) Tj ET",
        )
        mono, report, sent = translated(document.tobytes())
        self.assertEqual(sent, ["Blue words on the page."])
        self.assertEqual(report.failures, [])
        with pymupdf.open(stream=mono) as output:
            spans = [span for block in output[0].get_text("dict")["blocks"]
                     for line in block.get("lines", []) for span in line["spans"]]
        self.assertEqual([span["text"] for span in spans], ["vi:Blue words on the page."])
        # Three components came from the alternate, so the colour still travels.
        self.assertEqual(spans[0]["color"] & 0xFFFFFF, 0x0000FF)


class CMapLoadingTests(unittest.TestCase):
    """CVE-2025-64512 and GHSA-f83h-ghpp-7wcc: pdfminer unpickles the CMap file
    a font names, wherever that name points and wherever CMAP_PATH says."""

    def setUp(self) -> None:
        CMapDB._cmap_cache.clear()
        CMapDB._umap_cache.clear()

    def test_a_name_that_is_a_path_is_refused_without_touching_the_disk(self):
        for name in HOSTILE_NAMES:
            with self.subTest(name=name), no_file_is_looked_for():
                with self.assertRaises(CMapDB.CMapNotFound):
                    CMapDB.get_cmap(name)
                with self.assertRaises(CMapDB.CMapNotFound):
                    CMapDB.get_unicode_map(name)

    def test_a_font_naming_a_share_still_loads_as_an_unknown_encoding(self):
        spec = {
            "Type": LIT("Font"),
            "Subtype": LIT("Type0"),
            "BaseFont": LIT("Hostile"),
            "Encoding": LIT(HOSTILE_NAMES[0]),
            "DescendantFonts": [{
                "Type": LIT("Font"),
                "Subtype": LIT("CIDFontType2"),
                "BaseFont": LIT("Hostile"),
                "CIDSystemInfo": {"Registry": b"..\\..", "Ordering": b"evil", "Supplement": 0},
            }],
        }
        with no_file_is_looked_for():
            font = PDFResourceManager().get_font(None, spec)
        self.assertIsInstance(font, PDFCIDFont)
        self.assertIsNone(font.unicode_map)

    def test_cmaps_installed_with_pdfminer_still_load(self):
        """Chinese text set in a UCS-2 CMap still reads back as itself."""
        cmap = CMapDB.get_cmap("UniGB-UCS2-H")
        self.assertFalse(cmap.is_vertical())
        (cid,) = cmap.decode("中".encode("utf-16-be"))
        self.assertEqual(CMapDB.get_unicode_map("Adobe-GB1").get_unichr(cid), "中")

    def test_a_cmap_planted_on_cmap_path_is_never_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Planted", "UniGB-UCS2-H"):
                with gzip.open(Path(folder) / f"{name}.pickle.gz", "wb") as planted:
                    planted.write(pickle.dumps({"CODE2CID": {}, "IS_VERTICAL": True}))
            with patch.dict(os.environ, {"CMAP_PATH": folder}):
                with self.assertRaises(CMapDB.CMapNotFound):
                    CMapDB.get_cmap("Planted")
                self.assertFalse(CMapDB.get_cmap("UniGB-UCS2-H").is_vertical())


if __name__ == "__main__":
    unittest.main()
