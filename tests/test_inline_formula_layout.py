"""Regression tests for inline fractions overlapping the reflowed text.

Two separate defects produced the same symptom. A fraction rule was left at its
source position, striking through whatever the translation happened to reflow
underneath it, and a fraction's denominator was printed on top of the next line
because every line in a paragraph got the same leading.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from pdf2zh.converter import (
    IDENTITY_ORIENTATION,
    OpType,
    TextStyle,
    available_height_below,
    line_offsets,
    matrix_font_size,
    normalised_text_matrix,
    operation_ink,
    paragraph_width_budget,
    preferred_translation,
    should_translate_rotated_text,
    size_should_follow_body,
    styled_text_matrix,
    styled_character_text,
    text_fits_box_at_minimum_size,
    text_orientation,
    text_style_from_font,
    uses_synthetic_bold,
    vertical_ink_extent,
    vertical_shift_to_bounds,
)
from pdf2zh.high_level import output_style_font_paths
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager


class RecordingDevice:
    """Just enough of a PDFDevice to see what do_S hands over."""

    def __init__(self) -> None:
        self.painted: list = []
        self.ctm = None

    def paint_path(self, gstate, stroke, fill, evenodd, path) -> None:
        self.painted.append((gstate, path))

    def set_ctm(self, ctm) -> None:
        self.ctm = ctm


def _stroke(scolor, ctm=(1, 0, 0, 1, 0, 0), linewidth=1.0, path=None):
    device = RecordingDevice()
    interpreter = PDFPageInterpreterEx(PDFResourceManager(), device, {})
    interpreter.ctm = ctm
    interpreter.graphicstate = PDFGraphicState()
    interpreter.graphicstate.scolor = scolor
    interpreter.graphicstate.linewidth = linewidth
    interpreter.curpath = path or [("m", 0.0, 0.0), ("l", 100.0, 0.0)]
    result = interpreter.do_S()
    return result, device.painted


class FractionRuleTests(unittest.TestCase):
    def test_a_rule_drawn_in_the_default_colour_is_moved_with_its_formula(self):
        # TeX emits no stroking-colour operator, so scolor stays None. Treating
        # that as "not black" left every inline fraction bar behind.
        result, painted = _stroke(None)
        self.assertEqual(result, "n")  # the original operator is dropped
        self.assertEqual(len(painted), 1)

    def test_an_explicitly_black_rule_still_moves(self):
        for scolor in (0, (0, 0, 0)):
            with self.subTest(scolor=scolor):
                result, painted = _stroke(scolor)
                self.assertEqual(result, "n")
                self.assertEqual(len(painted), 1)

    def test_a_coloured_rule_is_left_alone(self):
        result, painted = _stroke((1, 0, 0))
        self.assertNotEqual(result, "n")  # keep the original operator in place
        self.assertEqual(painted, [])

    def test_the_line_width_is_scaled_by_the_ctm_it_was_drawn_under(self):
        # The redrawn rule carries no CTM, so a TeX `0.1 cm` scale has to be
        # baked in or a 4.05pt operand comes out as a 4pt slab.
        _, painted = _stroke(None, ctm=(0.1, 0, 0, 0.1, 0, 0), linewidth=4.05)
        self.assertAlmostEqual(painted[0][0].linewidth, 0.405, places=3)

    def test_scaling_does_not_leak_into_later_strokes(self):
        device = RecordingDevice()
        interpreter = PDFPageInterpreterEx(PDFResourceManager(), device, {})
        interpreter.ctm = (0.1, 0, 0, 0.1, 0, 0)
        interpreter.graphicstate = PDFGraphicState()
        interpreter.graphicstate.scolor = None
        interpreter.graphicstate.linewidth = 4.05
        interpreter.curpath = [("m", 0.0, 0.0), ("l", 100.0, 0.0)]
        interpreter.do_S()
        self.assertAlmostEqual(interpreter.graphicstate.linewidth, 4.05)

    def test_a_slanted_rule_is_left_alone(self):
        result, painted = _stroke(None, path=[("m", 0.0, 0.0), ("l", 100.0, 5.0)])
        self.assertNotEqual(result, "n")
        self.assertEqual(painted, [])


class LineOffsetTests(unittest.TestCase):
    SIZE = 10.0
    LEADING = 1.1

    def _offsets(self, ink, lines, budget=None):
        return line_offsets(ink, lines, self.SIZE, self.LEADING, budget)

    def test_prose_keeps_the_usual_even_leading(self):
        prose = (-0.22 * self.SIZE, 0.78 * self.SIZE)
        offsets = self._offsets({i: prose for i in range(4)}, 3)
        self.assertEqual([round(o, 4) for o in offsets], [0.0, 11.0, 22.0, 33.0])

    def test_a_deep_denominator_pushes_only_the_line_below_it(self):
        prose = (-2.2, 7.8)
        ink = {0: prose, 1: (-12.0, 7.8), 2: prose}  # line 1 holds the fraction
        offsets = self._offsets(ink, 2)
        self.assertEqual(round(offsets[1], 4), 11.0)   # gap above is unchanged
        self.assertEqual(round(offsets[2] - offsets[1], 4), 19.8)  # 7.8 - (-12.0)

    def test_a_tall_numerator_pushes_the_line_above_it(self):
        prose = (-2.2, 7.8)
        ink = {0: prose, 1: (-2.2, 16.0)}
        offsets = self._offsets(ink, 1)
        self.assertEqual(round(offsets[1], 4), 18.2)   # 16.0 - (-2.2)

    def test_extra_room_is_capped_at_the_paragraph_slack(self):
        # Growing the paragraph past its own box drops it onto the text below,
        # which looks worse than a formula that is still a little tight.
        ink = {0: (-2.2, 7.8), 1: (-12.0, 7.8), 2: (-2.2, 7.8)}
        offsets = self._offsets(ink, 2, budget=3.0)
        self.assertEqual(round(offsets[2], 4), 25.0)  # 11 + 11 + the 3.0 allowed

    def test_competing_formulas_share_the_slack(self):
        ink = {0: (-12.0, 7.8), 1: (-12.0, 7.8), 2: (-2.2, 7.8)}
        offsets = self._offsets(ink, 2, budget=4.0)
        self.assertEqual(round(offsets[1] - 0.0, 4), 13.0)   # half of 4.0 each
        self.assertEqual(round(offsets[2] - offsets[1], 4), 13.0)

    def test_a_paragraph_with_no_slack_keeps_the_plain_leading(self):
        ink = {0: (-2.2, 7.8), 1: (-12.0, 7.8)}
        self.assertEqual([round(o, 4) for o in self._offsets(ink, 1, budget=0.0)],
                         [0.0, 11.0])

    def test_a_single_line_paragraph_needs_no_gaps(self):
        self.assertEqual(self._offsets({0: (-2.2, 7.8)}, 0), [0.0])

    def test_negative_cell_slack_with_no_formula_extra_keeps_plain_leading(self):
        prose = (-2.2, 7.8)
        self.assertEqual(
            [round(o, 4) for o in self._offsets({0: prose, 1: prose}, 1, budget=-1.0)],
            [0.0, 11.0],
        )

    def test_lines_without_recorded_ink_fall_back_to_the_usual_leading(self):
        self.assertEqual([round(o, 4) for o in self._offsets({}, 2)], [0.0, 11.0, 22.0])

    def test_ink_uses_the_final_shrunk_font_size(self):
        operations = [
            {"type": OpType.TEXT, "size": 5.0, "dy": 0.0, "lidx": 0},
            {"type": OpType.TEXT, "size": 5.0, "dy": 0.0, "lidx": 1},
        ]
        ink = operation_ink(operations)
        self.assertEqual(tuple(round(value, 1) for value in ink[0]), (-1.1, 3.9))
        self.assertEqual(tuple(round(value, 1) for value in ink[1]), (-1.1, 3.9))

    def test_vertical_extent_includes_large_preserved_text_on_later_line(self):
        ink = {0: (-1.1, 3.9), 1: (-2.4, 8.6)}
        self.assertAlmostEqual(vertical_ink_extent(ink, [0.0, 6.0]), 12.3)

    def test_fitted_cell_text_is_shifted_inside_the_lower_border(self):
        ink = {0: (-1.0, 4.0), 1: (-1.0, 4.0)}
        self.assertEqual(
            vertical_shift_to_bounds(20.0, ink, [0.0, 8.0], 13.0, 26.0),
            2.0,
        )

    def test_fitted_cell_text_is_shifted_inside_the_upper_border(self):
        ink = {0: (-1.0, 4.0)}
        self.assertEqual(
            vertical_shift_to_bounds(20.0, ink, [0.0], 10.0, 22.0),
            -2.0,
        )


class AvailableHeightTests(unittest.TestCase):
    """A translated paragraph is often a line or two longer than its source.

    Charging that to the source box alone made paragraphs shrink even with white
    space under them, and - once shrinking hit its floor - draw over the
    paragraph below instead. Boxes are (x0, y0, x1, y1) with y increasing upward.
    """

    PARAGRAPH = (50.0, 700.0, 500.0, 740.0)  # 40pt tall, near the top of a page

    def test_a_clear_page_below_lends_its_whole_gap(self):
        self.assertAlmostEqual(
            available_height_below(self.PARAGRAPH, [], floor=100.0), 640.0
        )

    def test_a_paragraph_directly_below_lends_only_the_real_gap(self):
        below = (50.0, 600.0, 500.0, 680.0)  # its top is 20pt under our bottom
        self.assertAlmostEqual(
            available_height_below(self.PARAGRAPH, [below], floor=0.0), 60.0
        )

    def test_a_touching_paragraph_lends_nothing(self):
        below = (50.0, 600.0, 500.0, 700.0)  # top exactly at our bottom edge
        self.assertAlmostEqual(
            available_height_below(self.PARAGRAPH, [below], floor=0.0), 40.0
        )

    def test_a_neighbour_in_another_column_is_not_an_obstacle(self):
        beside = (520.0, 400.0, 560.0, 690.0)
        self.assertAlmostEqual(
            available_height_below(self.PARAGRAPH, [beside], floor=0.0), 740.0
        )

    def test_the_paragraph_itself_is_never_its_own_obstacle(self):
        self.assertAlmostEqual(
            available_height_below(self.PARAGRAPH, [self.PARAGRAPH], floor=0.0), 740.0
        )

    def test_the_nearest_of_several_obstacles_wins(self):
        far = (50.0, 200.0, 500.0, 300.0)
        near = (50.0, 600.0, 500.0, 680.0)
        self.assertAlmostEqual(
            available_height_below(self.PARAGRAPH, [far, near], floor=0.0), 60.0
        )

    def test_a_paragraph_never_gets_less_than_its_own_box(self):
        overlapping = (50.0, 600.0, 500.0, 760.0)
        self.assertGreaterEqual(
            available_height_below(self.PARAGRAPH, [overlapping], floor=0.0), 40.0
        )


class ParagraphSizeTests(unittest.TestCase):
    """A textbook list item is a 14pt bullet, a tab in the bullet's font, then
    8.75pt body text. Taking the size from that tab typeset the whole item at
    14pt; worse, the body then looked like a subscript against it, so the item
    was preserved as a formula, never translated, and drawn over its
    neighbours."""

    BULLET = 14.0
    BODY = 8.75

    def test_the_first_real_letter_after_a_bullet_sets_the_size(self):
        self.assertTrue(
            size_should_follow_body(self.BULLET, self.BODY, visible_length=0, character="T")
        )

    def test_a_tab_or_space_never_sets_the_size(self):
        for character in (" ",):
            with self.subTest(character=character):
                self.assertFalse(
                    size_should_follow_body(
                        self.BODY, self.BULLET, visible_length=0, character=character
                    )
                )

    def test_the_second_letter_may_still_correct_the_first(self):
        self.assertTrue(
            size_should_follow_body(self.BULLET, self.BODY, visible_length=1, character="h")
        )

    def test_body_text_no_longer_moves_the_size_once_the_paragraph_has_started(self):
        self.assertFalse(
            size_should_follow_body(self.BODY, self.BODY, visible_length=9, character="e")
        )

    def test_a_larger_glyph_mid_paragraph_still_raises_the_size(self):
        self.assertTrue(
            size_should_follow_body(self.BODY, 20.0, visible_length=40, character="Q")
        )


class TableCellFitTests(unittest.TestCase):
    @staticmethod
    def _measure(_character, size):
        return size

    def test_short_translation_fits_at_half_size(self):
        self.assertTrue(
            text_fits_box_at_minimum_size("two words", 25, 10, 10, [], self._measure)
        )

    def test_translation_that_needs_too_many_lines_falls_back(self):
        self.assertFalse(
            text_fits_box_at_minimum_size(
                "one two three four", 15, 5, 10, [], self._measure
            )
        )

    def test_formula_placeholder_uses_its_original_width(self):
        self.assertFalse(
            text_fits_box_at_minimum_size("{v0}", 10, 10, 10, [20], self._measure)
        )


class OrientationAndStyleTests(unittest.TestCase):
    def test_quarter_turn_matrices_are_classified(self):
        self.assertEqual(text_orientation((8, 0, 0, 8, 0, 0)), IDENTITY_ORIENTATION)
        self.assertEqual(text_orientation((0, 8, -8, 0, 0, 0)), (0, 1, -1, 0))
        self.assertEqual(text_orientation((-8, 0, 0, -8, 0, 0)), (-1, 0, 0, -1))
        self.assertEqual(text_orientation((0, -8, 8, 0, 0, 0)), (0, -1, 1, 0))
        self.assertIsNone(text_orientation((6, 4, -4, 6, 0, 0)))

    def test_reflected_upright_text_is_not_mistaken_for_rotation(self):
        self.assertEqual(text_orientation((1, 0, 0, -1, 0, 0)), IDENTITY_ORIENTATION)
        self.assertEqual(
            normalised_text_matrix((1, 0, 0, -1, 0, 0)),
            IDENTITY_ORIENTATION,
        )

    def test_rotated_font_size_comes_from_matrix_not_glyph_advance(self):
        self.assertEqual(matrix_font_size((0, 8, -8, 0, 0, 0)), 8)

    def test_font_face_names_map_to_inline_styles(self):
        cases = {
            "MyriadPro-Regular": TextStyle.REGULAR,
            "MyriadPro-Semibold": TextStyle.BOLD,
            "TimesNewRomanPS-ItalicMT": TextStyle.ITALIC,
            "TimesNewRomanPS-BoldItalicMT": TextStyle.BOLD_ITALIC,
        }
        for face, expected in cases.items():
            with self.subTest(face=face):
                self.assertEqual(text_style_from_font(face), expected)

    def test_styled_runs_are_serialised_without_losing_text(self):
        chars = [
            SimpleNamespace(fontname="Regular", get_text=lambda: "A"),
            SimpleNamespace(fontname="SemiBold", get_text=lambda: "B"),
            SimpleNamespace(fontname="Italic", get_text=lambda: "C"),
        ]
        self.assertEqual(styled_character_text(chars), "A<s1>B</s1><s2>C</s2>")

    def test_rotated_vietnamese_headers_have_stable_terminology_and_style(self):
        self.assertEqual(
            preferred_translation("<s1>Designation</s1>", "vi"),
            "<s1>Tên gọi</s1>",
        )
        self.assertEqual(preferred_translation("Unit", "vi"), "Đơn vị")
        self.assertIsNone(preferred_translation("Designation", "fr"))
        self.assertFalse(should_translate_rotated_text("Ref. no. 304-2"))
        self.assertTrue(should_translate_rotated_text("Designation"))

    def test_synthetic_italic_composes_with_rotation(self):
        self.assertEqual(
            styled_text_matrix((0, 1, -1, 0), TextStyle.ITALIC, True),
            (0, 1, -1.0, 0.2),
        )
        self.assertTrue(uses_synthetic_bold(TextStyle.BOLD_ITALIC, True))
        self.assertFalse(uses_synthetic_bold(TextStyle.BOLD, False))

    def test_missing_style_faces_fall_back_to_the_regular_font(self):
        paths = output_style_font_paths("vi", "C:/missing/regular.ttf")
        self.assertEqual(set(paths.values()), {"C:\\missing\\regular.ttf"})

    def test_first_line_indent_is_deducted_from_width_budget(self):
        self.assertEqual(paragraph_width_budget(20, 10, 110, 1), 90)
        self.assertEqual(paragraph_width_budget(20, 10, 110, 3), 290)


if __name__ == "__main__":
    unittest.main()
