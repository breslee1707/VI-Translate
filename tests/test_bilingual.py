from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pymupdf

from pdf2zh.bilingual import ORIGINAL_LAYER, BilingualError, write_bilingual

ROTATIONS = (0, 90, 180, 270)


def make_pdf(path: Path, label: str, *, blank_last: bool = False, crop: bool = False) -> None:
    """One page per rotation, each marked so a misplaced or turned page shows."""
    document = pymupdf.open()
    for rotation in ROTATIONS:
        page = document.new_page(width=300, height=500)
        page.insert_text((20, 40), f"{label} {rotation}", fontsize=18)
        page.draw_rect(pymupdf.Rect(20, 300, 120, 480), color=(1, 0, 0), fill=(1, 0, 0))
        page.insert_link(
            {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(20, 60, 120, 90),
             "uri": f"https://example.com/{label}"}
        )
        if crop:
            page.set_cropbox(pymupdf.Rect(10, 10, 290, 490))
        page.set_rotation(rotation)
    if blank_last:
        document.new_page(width=300, height=500)
    document.set_toc([[1, "First", 1], [1, "Third", 3]])
    add_citation(document)
    document.save(path)


def add_citation(document: pymupdf.Document) -> None:
    """A journal-style citation: page 1 links to page 3 through a named destination."""
    document.xref_set_key(
        document.pdf_catalog(), "Dests", f"<< /cite.1 [{document[2].xref} 0 R /XYZ 0 400 0] >>"
    )
    link = document.get_new_xref()
    document.update_object(
        link,
        "<< /Type /Annot /Subtype /Link /Rect [20 380 120 410] /Border [0 0 0] "
        "/A << /S /GoTo /D (cite.1) >> >>",
    )
    kind, annots = document.xref_get_key(document[0].xref, "Annots")
    existing = annots.strip("[]") if kind == "array" else ""
    document.xref_set_key(document[0].xref, "Annots", f"[{existing} {link} 0 R]")


def pixels(page: pymupdf.Page, clip: pymupdf.Rect | None = None) -> np.ndarray:
    image = page.get_pixmap(dpi=36, clip=clip)
    return np.frombuffer(image.samples, np.uint8).reshape(image.h, image.w, image.n).astype(int)


class BilingualTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "book.pdf"
        self.translation = self.root / "book-vi.pdf"
        self.output = self.root / "book-vi-dual.pdf"
        make_pdf(self.source, "SOURCE")
        make_pdf(self.translation, "BAN DICH")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assertSamePage(self, actual: np.ndarray, expected: np.ndarray) -> None:
        self.assertEqual(actual.shape, expected.shape)
        self.assertLessEqual(int(np.abs(actual - expected).max()), 8)

    def test_side_by_side_shows_both_pages_upright_at_their_own_size(self):
        """show_pdf_page placed a /Rotate 90 page's raw stream, sideways."""
        write_bilingual(self.source, self.translation, self.output, "side-by-side")
        with (
            pymupdf.open(self.output) as combined,
            pymupdf.open(self.source) as source,
            pymupdf.open(self.translation) as translation,
        ):
            self.assertEqual(combined.page_count, len(ROTATIONS))
            for index, page in enumerate(combined):
                with self.subTest(rotation=ROTATIONS[index]):
                    width = source[index].rect.width
                    self.assertAlmostEqual(page.rect.width, 2 * width)
                    left = pymupdf.Rect(0, 0, width, page.rect.height)
                    right = pymupdf.Rect(width, 0, 2 * width, page.rect.height)
                    self.assertSamePage(pixels(page, left), pixels(source[index]))
                    self.assertSamePage(pixels(page, right), pixels(translation[index]))

    def test_side_by_side_keeps_links_on_their_own_half(self):
        write_bilingual(self.source, self.translation, self.output, "side-by-side")
        with pymupdf.open(self.output) as combined, pymupdf.open(self.source) as source:
            page = combined[0]
            width = source[0].rect.width
            links = {link["uri"]: pymupdf.Rect(link["from"])
                     for link in page.get_links() if link["kind"] == pymupdf.LINK_URI}
            self.assertEqual(links["https://example.com/SOURCE"], pymupdf.Rect(20, 60, 120, 90))
            self.assertEqual(
                links["https://example.com/BAN DICH"], pymupdf.Rect(20 + width, 60, 120 + width, 90)
            )

    def test_citations_still_lead_to_their_reference_in_either_language(self):
        """insert_pdf dropped every link to a named destination, which is how
        journals link a citation to the reference list."""
        with pymupdf.open(self.source) as source:
            named = [link for link in source[0].get_links() if link["kind"] == pymupdf.LINK_NAMED]
            self.assertEqual([link["page"] for link in named], [2])
        expected = {"alternating": {0: [4], 1: [5]}, "side-by-side": {0: [2, 2]}}
        for layout, pages in expected.items():
            with self.subTest(layout=layout):
                write_bilingual(self.source, self.translation, self.output, layout)
                with pymupdf.open(self.output) as combined:
                    for number, targets in pages.items():
                        found = [link["page"] for link in combined[number].get_links()
                                 if link["kind"] == pymupdf.LINK_GOTO]
                        self.assertEqual(found, targets)

    def test_alternating_pairs_each_original_with_its_translation(self):
        write_bilingual(self.source, self.translation, self.output, "alternating")
        with (
            pymupdf.open(self.output) as combined,
            pymupdf.open(self.source) as source,
            pymupdf.open(self.translation) as translation,
        ):
            self.assertEqual(combined.page_count, 2 * len(ROTATIONS))
            for index in range(len(ROTATIONS)):
                with self.subTest(page=index):
                    self.assertSamePage(pixels(combined[2 * index]), pixels(source[index]))
                    self.assertSamePage(pixels(combined[2 * index + 1]), pixels(translation[index]))
            # A bookmark lands on the original page of its pair.
            self.assertEqual(combined.get_toc(), [[1, "First", 1], [1, "Third", 5]])

    def test_layers_show_the_translation_until_the_original_is_switched_on(self):
        write_bilingual(self.source, self.translation, self.output, "layers")
        with (
            pymupdf.open(self.output) as layered,
            pymupdf.open(self.source) as source,
            pymupdf.open(self.translation) as translation,
        ):
            self.assertEqual(layered.page_count, len(ROTATIONS))
            self.assertEqual(
                [ocg["name"] for ocg in layered.get_ocgs().values()], [ORIGINAL_LAYER]
            )
            self.assertEqual(layered.get_toc(), translation.get_toc())
            for index in range(len(ROTATIONS)):
                with self.subTest(layer="off", page=index):
                    self.assertSamePage(pixels(layered[index]), pixels(translation[index]))
            layered.set_layer_ui_config(0, 0)
            for index in range(len(ROTATIONS)):
                with self.subTest(layer="on", page=index):
                    self.assertSamePage(pixels(layered[index]), pixels(source[index]))

    def test_a_cropped_page_is_paired_without_an_offset(self):
        make_pdf(self.source, "SOURCE", crop=True)
        make_pdf(self.translation, "BAN DICH", crop=True)
        for layout in ("side-by-side", "layers"):
            with self.subTest(layout=layout):
                write_bilingual(self.source, self.translation, self.output, layout)
                with pymupdf.open(self.output) as combined, pymupdf.open(self.source) as source:
                    layout_page = combined[1]
                    if layout == "layers":
                        combined.set_layer_ui_config(0, 0)
                        expected = layout_page.rect
                    else:
                        expected = pymupdf.Rect(0, 0, source[1].rect.width, layout_page.rect.height)
                    self.assertSamePage(pixels(layout_page, expected), pixels(source[1]))

    def test_a_blank_page_stays_a_blank_page(self):
        make_pdf(self.source, "SOURCE", blank_last=True)
        make_pdf(self.translation, "BAN DICH", blank_last=True)
        for layout in ("side-by-side", "alternating", "layers"):
            with self.subTest(layout=layout):
                write_bilingual(self.source, self.translation, self.output, layout)
                with pymupdf.open(self.output) as combined:
                    self.assertEqual(int(pixels(combined[-1]).min()), 255)

    def test_the_source_and_the_translation_are_never_written(self):
        before = [hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (self.source, self.translation)]
        for layout in ("side-by-side", "alternating", "layers"):
            write_bilingual(self.source, self.translation, self.output, layout)
        after = [hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (self.source, self.translation)]
        self.assertEqual(before, after)

    def test_documents_that_do_not_pair_are_refused(self):
        make_pdf(self.translation, "BAN DICH", blank_last=True)
        with self.assertRaisesRegex(BilingualError, "cannot be paired"):
            write_bilingual(self.source, self.translation, self.output, "side-by-side")
        self.assertFalse(self.output.exists())

    def test_an_unreadable_translation_is_a_bilingual_error(self):
        self.translation.write_bytes(b"%PDF-1.7\nnot really")
        with self.assertRaises(BilingualError):
            write_bilingual(self.source, self.translation, self.output, "alternating")

    def test_an_unknown_layout_is_refused(self):
        with self.assertRaises(BilingualError):
            write_bilingual(self.source, self.translation, self.output, "diagonal")


if __name__ == "__main__":
    unittest.main()
