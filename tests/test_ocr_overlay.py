"""The optional OCR pass: what it reads, what it merges, and what it refuses.

The geometry decisions are pure functions so they can be checked without the
recognizer; the end-to-end cases drive apply_ocr_overlay with a stub session so
the test needs no ONNX models and no network.
"""

import sys
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf2zh.ocr import (  # noqa: E402
    SCALE,
    apply_ocr_overlay,
    ink_for,
    sampled_background,
    group_lines,
    merge_regions,
    quad_to_rect,
    strip_recognized_real_text,
)

PAGE_WIDTH = 612.0


class StubSession:
    """Stands in for RapidOCR, returning fixed lines for every region."""

    def __init__(self, lines):
        # lines: (quad in crop pixels, text, score)
        self.lines = lines
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        return [[quad, text, score] for quad, text, score in self.lines], [0.0]


class StubTranslator:
    def __init__(self):
        self.seen = []

    def translate(self, text):
        self.seen.append(text)
        return f"[{text}]"


def source_with_image(text=None, image_rect=(50, 50, 450, 250)):
    """A one-page PDF carrying a blank image, optionally with real text on it."""
    document = pymupdf.open()
    page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 200), False)
    pixmap.clear_with(255)
    page.insert_image(pymupdf.Rect(*image_rect), pixmap=pixmap)
    if text is not None:
        page.insert_text(pymupdf.Point(60, 80), text, fontsize=11)
    data = document.tobytes()
    document.close()
    return data


class RegionTests(unittest.TestCase):
    def test_touching_tiles_become_one_region(self):
        """A scanner emits one page as a grid of tiles, and no tile is large
        enough on its own to be worth OCRing."""
        tiles = [
            (0.0, 0.0, 300.0, 100.0),
            (300.0, 0.0, 600.0, 100.0),
            (0.0, 100.0, 300.0, 200.0),
            (300.0, 100.0, 600.0, 200.0),
        ]

        self.assertEqual(
            merge_regions(tiles, PAGE_WIDTH), [(0.0, 0.0, 600.0, 200.0)]
        )

    def test_a_logo_is_too_small_to_carry_prose(self):
        merged = merge_regions([(10.0, 10.0, 60.0, 40.0)], PAGE_WIDTH)

        self.assertEqual(merged, [])

    def test_separated_figures_stay_separate(self):
        figures = [(40.0, 40.0, 300.0, 200.0), (40.0, 400.0, 300.0, 600.0)]

        self.assertEqual(len(merge_regions(figures, PAGE_WIDTH)), 2)


class CoordinateTests(unittest.TestCase):
    def test_a_detector_quad_lands_where_the_pixels_were(self):
        # 200 dpi: a point is SCALE pixels, and the crop began 50pt down the page.
        quad = [[0, 0], [SCALE * 100, 0], [SCALE * 100, SCALE * 10], [0, SCALE * 10]]

        rect = quad_to_rect(quad, (50.0, 50.0))

        for got, expected in zip(rect, (50.0, 50.0, 150.0, 60.0)):
            self.assertAlmostEqual(got, expected, places=4)


class RealTextTests(unittest.TestCase):
    def test_text_the_engine_already_translated_is_dropped(self):
        """A picture behind the prose would otherwise hand the recognizer the
        very sentences the converter has just translated, and the page would
        end up carrying both translations stacked."""
        lines = [("Introduction", (100.0, 100.0, 200.0, 112.0))]
        words = [(98.0, 99.0, 205.0, 113.0, "Introduction", 0, 0, 0)]

        self.assertEqual(strip_recognized_real_text(lines, words), [])

    def test_a_label_with_no_real_text_under_it_survives(self):
        lines = [("Figure 1", (100.0, 100.0, 200.0, 112.0))]
        words = [(300.0, 400.0, 380.0, 412.0, "elsewhere", 0, 0, 0)]

        self.assertEqual(len(strip_recognized_real_text(lines, words)), 1)


class GroupingTests(unittest.TestCase):
    def test_lines_of_one_paragraph_are_translated_together(self):
        lines = [
            ("the first half of", (100.0, 100.0, 260.0, 112.0)),
            ("a single sentence", (100.0, 114.0, 250.0, 126.0)),
        ]

        blocks = group_lines(lines)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "the first half of a single sentence")

    def test_a_wide_gap_starts_a_new_paragraph(self):
        lines = [
            ("a heading", (100.0, 100.0, 260.0, 112.0)),
            ("body text far below", (100.0, 300.0, 260.0, 312.0)),
        ]

        self.assertEqual(len(group_lines(lines)), 2)

    def test_two_columns_are_not_read_as_one_paragraph(self):
        lines = [
            ("left column", (60.0, 100.0, 200.0, 112.0)),
            ("right column", (320.0, 114.0, 460.0, 126.0)),
        ]

        self.assertEqual(len(group_lines(lines)), 2)


class BackingTests(unittest.TestCase):
    def test_the_patch_takes_the_colour_of_the_paper(self):
        """Scanned paper is warm grey far more often than white, and a pure
        white patch turns every translated paragraph into a visible rectangle."""
        document = pymupdf.open()
        page = document.new_page()
        page.draw_rect(page.rect, color=None, fill=(0.8, 0.78, 0.74))
        try:
            red, green, blue = sampled_background(page, (100, 100, 300, 140))
        finally:
            document.close()

        self.assertAlmostEqual(red, 0.8, places=1)
        self.assertAlmostEqual(green, 0.78, places=1)
        self.assertAlmostEqual(blue, 0.74, places=1)

    def test_white_paper_still_gets_a_white_patch(self):
        document = pymupdf.open()
        page = document.new_page()
        try:
            self.assertEqual(sampled_background(page, (100, 100, 300, 140)), (1.0, 1.0, 1.0))
        finally:
            document.close()


class InkTests(unittest.TestCase):
    def test_a_title_on_a_dark_photograph_is_written_light(self):
        """A slide title is as often white on a picture as black on paper, and
        the recognizer reports words, not their colour."""
        self.assertGreater(min(ink_for((0.10, 0.16, 0.24))), 0.5)

    def test_text_on_paper_stays_dark(self):
        self.assertLess(max(ink_for((0.94, 0.93, 0.90))), 0.5)


class BlockLineTests(unittest.TestCase):
    def test_a_block_keeps_the_boxes_it_was_built_from(self):
        """The backing is painted over those boxes. Painting the paragraph
        instead put a slab of flat colour across the picture behind a title
        whose translation only needed two lines of a five-line box."""
        lines = [
            ("Assessing Manufacturing", (239.0, 216.0, 810.0, 283.0)),
            ("Innovations For Patenting", (234.0, 296.0, 809.0, 369.0)),
        ]

        blocks = group_lines(lines)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(len(blocks[0].lines), 2)
        self.assertEqual(blocks[0].rect, (234.0, 216.0, 810.0, 369.0))
        # And the size comes from a line, not from the whole paragraph.
        self.assertAlmostEqual(blocks[0].height, 73.0)


class OverlayTests(unittest.TestCase):
    def _run(self, source, session):
        translator = StubTranslator()
        output = pymupdf.open(stream=source)
        try:
            outcome = apply_ocr_overlay(
                source,
                output,
                None,
                translator,
                pymupdf.Font("helv"),
                session=session,
            )
            written = output[0].get_text()
        finally:
            output.close()
        return outcome, written, translator

    def test_a_label_inside_an_image_is_translated_onto_the_page(self):
        session = StubSession(
            [([[20, 20], [300, 20], [300, 60], [20, 60]], "inlet pressure", 0.95)]
        )

        outcome, written, translator = self._run(source_with_image(), session)

        self.assertEqual(translator.seen, ["inlet pressure"])
        self.assertEqual(outcome.pages, (0,))
        self.assertEqual(outcome.segments, 1)
        self.assertIn("[inlet pressure]", written)

    def test_a_faint_reading_is_left_as_pixels(self):
        session = StubSession(
            [([[20, 20], [300, 20], [300, 60], [20, 60]], "smudge", 0.2)]
        )

        outcome, written, translator = self._run(source_with_image(), session)

        self.assertEqual(translator.seen, [])
        self.assertEqual(outcome.segments, 0)
        self.assertNotIn("smudge", written)

    def test_the_page_prose_is_not_translated_a_second_time(self):
        """The regression this guards: an image behind real text made the pass
        recognize, translate and redraw text the engine had already handled."""
        source = source_with_image(text="Introduction")
        # The stub reports a line sitting exactly where that real word is.
        session = StubSession(
            [
                (
                    [
                        [10 * SCALE, 22 * SCALE],
                        [70 * SCALE, 22 * SCALE],
                        [70 * SCALE, 32 * SCALE],
                        [10 * SCALE, 32 * SCALE],
                    ],
                    "Introduction",
                    0.99,
                )
            ]
        )

        outcome, _written, translator = self._run(source, session)

        self.assertEqual(translator.seen, [])
        self.assertEqual(outcome.segments, 0)

    def test_a_page_with_no_image_is_never_rasterized(self):
        document = pymupdf.open()
        document.new_page()
        source = document.tobytes()
        document.close()
        session = StubSession([([[0, 0], [10, 0], [10, 10], [0, 10]], "text", 0.9)])

        outcome, _written, _translator = self._run(source, session)

        self.assertEqual(session.calls, 0)
        self.assertEqual(outcome.pages, ())


if __name__ == "__main__":
    unittest.main()
