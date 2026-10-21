from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pymupdf

from pdf2zh.converter import cell_line_ends_paragraph
from pdf2zh.ocr import OCR_FONT_PATH
from pdf2zh.rules import words_by_cell
from tests.test_damaged_sources import translated

# Embedded rather than a base-14 font, so that its descriptor can be edited.
FONT = str(OCR_FONT_PATH)
SIZE = 9.0


class TableModel:
    """The layout model finding one table over the drawn grid."""

    def __init__(self, bounds: tuple[float, float, float, float]) -> None:
        self.bounds = bounds

    def predict(self, _image, imgsz: int):
        box = SimpleNamespace(cls=0, conf=0.9, xyxy=np.asarray([self.bounds], dtype=float))
        return [SimpleNamespace(boxes=[box], names={0: "table"})]


def ruled_table(
    cells: list[list[list[str]]],
    columns: tuple[float, ...] = (20.0, 150.0, 380.0),
    descent: int | None = None,
    starts: tuple[float, ...] | None = None,
    merged: frozenset[int] = frozenset(),
) -> tuple[bytes, TableModel]:
    """A ruled table whose cells hold the given lines, one row per entry.

    ``descent`` replaces the font descriptor's /Descent, which recent Chrome
    and Edge write as a positive number. ``starts`` is where each entry's text
    begins, 6 points inside its rule unless given. The rows in ``merged`` have
    no rule between their columns, so all their entries share one cell.
    """
    starts = starts or tuple(x + 6 for x in columns)
    top, row_height = 40.0, 60.0
    bottom = top + row_height * len(cells)
    document = pymupdf.open()
    page = document.new_page(width=400, height=bottom + top)
    page.insert_font(fontname="F0", fontfile=FONT)
    for row in range(len(cells) + 1):
        page.draw_line((columns[0], top + row * row_height),
                       (columns[-1], top + row * row_height), width=0.5)
    for x in (columns[0], columns[-1]):
        page.draw_line((x, top), (x, bottom), width=0.5)
    for row in range(len(cells)):
        if row not in merged:
            for x in columns[1:-1]:
                page.draw_line((x, top + row * row_height),
                               (x, top + (row + 1) * row_height), width=0.5)
    for row, entries in enumerate(cells):
        for column, lines in enumerate(entries):
            for index, line in enumerate(lines):
                page.insert_text(
                    (starts[column], top + row * row_height + 20 + index * 11),
                    line, fontname="F0", fontsize=SIZE,
                )
    if descent is not None:
        for xref in range(1, document.xref_length()):
            if document.xref_get_key(xref, "Type") == ("name", "/FontDescriptor"):
                document.xref_set_key(xref, "Descent", str(descent))
    model = TableModel((columns[0] - 2, top - 2, columns[-1] + 2, bottom + 2))
    return document.tobytes(), model


def segments(sent: list[str]) -> list[str]:
    return [segment for request in sent for segment in request.split("\n\n")]


def line_starts(mono: bytes, left: float) -> list[float]:
    """Where each output line drawn right of ``left`` begins, top to bottom."""
    with pymupdf.open(stream=mono) as output:
        words = output[0].get_text("words")
    lines: list[list[float]] = []
    for x0, _y0, _x1, y1, *_rest in sorted(words, key=lambda word: (word[3], word[0])):
        if x0 < left:
            continue
        if lines and abs(y1 - lines[-1][0]) < 2:
            lines[-1][1] = min(lines[-1][1], x0)
        else:
            lines.append([y1, x0])
    return [x0 for _y1, x0 in lines]


class CellLineTests(unittest.TestCase):
    def test_values_listed_one_per_line_stay_on_their_own_lines(self):
        """Read as one paragraph they ran together on the first line, away from
        the symbol drawn in front of each."""
        source, model = ruled_table([[["Colours"], ["1073741824 colours", "30 bits"]]])
        _mono, report, sent = translated(source, model=model)
        self.assertEqual(segments(sent), ["Colours", "1073741824 colours", "30 bits"])
        self.assertEqual(report.failures, [])

    def test_a_sentence_wrapped_in_a_narrow_cell_stays_one_sentence(self):
        """"See the" stops short of its cell only because the next word is long."""
        font = pymupdf.Font(fontfile=FONT)
        word = font.text_length("documentation", SIZE)
        # Room for the long word on a line of its own, not after "See the".
        width = word + 16
        self.assertGreater(font.text_length("See the documentation", SIZE), width - 8)
        source, model = ruled_table(
            [[["Notes"], ["See the", "documentation"]]], columns=(20.0, 150.0, 150.0 + width)
        )
        _mono, _report, sent = translated(source, model=model)
        self.assertEqual(segments(sent), ["Notes", "See the documentation"])

    def test_wrapped_lines_start_where_the_cell_text_starts(self):
        """They went back to the cell's rule, a few points left of the first
        line, so every cell's paragraph had a hanging first line."""
        font = pymupdf.Font(fontfile=FONT)
        first = "See the documentation for"
        # The first line fills the cell, so the second continues its sentence.
        width = font.text_length(first, SIZE) + 16
        source, model = ruled_table(
            [[["Notes"], [first, "every setting it lists."]]], columns=(20.0, 150.0, 150.0 + width)
        )
        mono, _report, sent = translated(source, model=model)
        self.assertIn("See the documentation for every setting it lists.", segments(sent))
        starts = line_starts(mono, left=150.0)
        self.assertGreaterEqual(len(starts), 2)
        for start in starts:
            self.assertAlmostEqual(start, 156.0, delta=0.5)

    def test_a_cell_split_off_a_merged_one_wraps_inside_its_own_column(self):
        """With no rule between "Partial." and its description, PyMuPDF returns
        one cell for both. The description's wrapped lines went back halfway
        across the gap, into the column of "Partial."."""
        font = pymupdf.Font(fontfile=FONT)
        first = "Retired switches are always rejected"
        self.assertLess(200.0 + font.text_length(first, SIZE), 375.0)
        source, model = ruled_table(
            [[["Name"], ["Meaning"]], [["Partial."], [first, "and need validation."]]],
            columns=(20.0, 190.0, 380.0), starts=(26.0, 200.0), merged=frozenset({1}),
        )
        mono, _report, sent = translated(source, model=model)
        self.assertIn("Retired switches are always rejected and need validation.", segments(sent))
        starts = line_starts(mono, left=100.0)
        self.assertGreaterEqual(len(starts), 2)
        for start in starts:
            self.assertAlmostEqual(start, 200.0, delta=0.5)

    def test_a_short_line_ends_a_paragraph_only_when_the_next_word_had_room(self):
        cell = (100.0, 0.0, 300.0, 50.0)
        self.assertTrue(cell_line_ends_paragraph(180.0, 30.0, cell))
        self.assertFalse(cell_line_ends_paragraph(180.0, 130.0, cell))  # wrapped
        self.assertFalse(cell_line_ends_paragraph(290.0, 5.0, cell))  # a full line
        self.assertFalse(cell_line_ends_paragraph(180.0, None, cell))
        self.assertFalse(cell_line_ends_paragraph(180.0, 30.0, None))


SPECIFICATIONS = [
    [["Colours"], ["The maximum number of colours the panel", "can show at once."]],
    [["Brightness"], ["Light the screen gives off at full power."]],
]


class PositiveDescentTests(unittest.TestCase):
    def test_a_table_set_in_a_font_with_a_positive_descent_is_translated_whole(self):
        """Recent Chrome writes /Descent as a positive number. MuPDF then put
        each word half an em above its glyphs, so the cells painted from those
        words missed pdfminer's characters: "The maximum num" was all that was
        sent."""
        source, model = ruled_table(SPECIFICATIONS, descent=265)
        _mono, report, sent = translated(source, model=model)
        well_formed, model = ruled_table(SPECIFICATIONS, descent=-265)
        _mono, _report, expected = translated(well_formed, model=model)
        self.assertEqual(segments(sent), segments(expected))
        self.assertIn("The maximum number of colours the panel can show at once.", segments(sent))
        self.assertEqual(report.failures, [])

    def test_the_output_keeps_the_source_descriptor(self):
        source, model = ruled_table(SPECIFICATIONS, descent=265)
        mono, _report, _sent = translated(source, model=model)
        with pymupdf.open(stream=mono) as output:
            descents = [
                output.xref_get_key(xref, "Descent")
                for xref in range(1, output.xref_length())
                if output.xref_get_key(xref, "Type") == ("name", "/FontDescriptor")
            ]
        self.assertIn(("int", "265"), descents)


class OverlappingCellTests(unittest.TestCase):
    def test_a_stray_cell_inside_a_real_one_does_not_split_its_sentence(self):
        """Inline-code backgrounds gave PyMuPDF a small cell inside a
        description's cell. "True" went to it, too narrow for its translation,
        and the rest of the sentence was sent without it."""
        source, model = ruled_table(
            [[["Key"], ["True to suppress visible warnings when", "a shebang launches it."]]]
        )
        with pymupdf.open(stream=source) as document:
            word = next(w for w in document[0].get_text("words") if w[4] == "True")
        # Over the left half of "True" only, as the stray cell was.
        stray = (word[0] - 6, word[1] + 1, (word[0] + word[2]) / 2 + 0.5, word[3] - 1)
        table = SimpleNamespace(
            bbox=(20.0, 40.0, 380.0, 100.0),
            cells=[(20.0, 40.0, 150.0, 100.0), (150.0, 40.0, 380.0, 100.0), stray],
        )
        with patch("pymupdf.Page.find_tables", return_value=SimpleNamespace(tables=[table])):
            _mono, report, sent = translated(source, model=model)
        self.assertIn(
            "True to suppress visible warnings when a shebang launches it.", segments(sent)
        )
        self.assertEqual(report.failures, [])

    def test_a_word_goes_to_the_cell_covering_most_of_it(self):
        description = (111.6, 635.7, 560.1, 681.9)
        stray = (111.6, 654.6, 126.0, 663.6)
        first = (116.1, 652.7, 131.8, 663.9, "True")
        second = (134.1, 652.7, 141.9, 663.9, "to")
        self.assertEqual(words_by_cell([description, stray], [first, second]), [[first, second], []])
        self.assertEqual(words_by_cell([stray, description], [first, second]), [[], [first, second]])

    def test_a_word_wholly_inside_two_cells_goes_to_the_larger(self):
        outer = (0.0, 0.0, 200.0, 50.0)
        inner = (10.0, 10.0, 60.0, 30.0)
        word = (20.0, 15.0, 40.0, 25.0, "code")
        self.assertEqual(words_by_cell([inner, outer], [word]), [[], [word]])


if __name__ == "__main__":
    unittest.main()
