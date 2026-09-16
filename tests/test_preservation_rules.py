from __future__ import annotations

import unittest
from types import SimpleNamespace

from pdfminer.pdfinterp import PDFResourceManager

from pdf2zh.converter import TranslateConverter, is_translatable_segment
from pdf2zh.rules import (
    BULLET_CHARACTERS,
    classify_preserved_page,
    cluster_table_words,
    formula_regions,
    is_bullet_character,
    is_formula_font,
    is_scanned_page,
    line_height_for_language,
    page_has_image,
    matching_table_cells,
    min_line_height_for_language,
    should_translate_table_cell,
    text_aligned_table_cells,
)


def _words(line: int, y: float, *runs: tuple[float, str], space: float = 2.0) -> list[tuple]:
    """Words of one 8 pt table line: 4.5 pt a character, `space` between words."""
    words = []
    for x, text in runs:
        for word in text.split():
            words.append((x, y, x + len(word) * 4.5, y + 8.0, word, 0, line, len(words)))
            x += len(word) * 4.5 + space
    return words


class TextAlignedTableTests(unittest.TestCase):
    """Tables drawn with no grid: PyMuPDF finds no cells, so the whole table stayed English."""

    def _cells_text(self, cells, words):
        return [
            " ".join(word[4] for word in words
                     if cell[0] <= (word[0] + word[2]) / 2 <= cell[2]
                     and cell[1] <= (word[1] + word[3]) / 2 <= cell[3])
            for cell in cells
        ]

    def test_columns_from_white_space_and_items_from_bullets_rows_and_rules(self):
        """Combatting Table 2: a header rule, bullet items, rows set a little apart."""
        words = (
            _words(0, 663, (53, "Infectious Diarrhoea"), (289, "Treatment approaches"), (525, "Refs"))
            + _words(1, 675, (53, "Acute diarrhea"), (155, "●Rehydration therapy"), (525, "[15]"))
            + _words(2, 684, (155, "●Antibiotic regime of ciprofloxacin"))
            + _words(3, 707, (53, "Chronic diarrhea"), (155, "●Rehydration therapy"), (525, "[51]"))
        )
        rules = [(49, 672, 156, 672.5), (156, 672, 525, 672.5), (525, 672, 562, 672.5)]
        cells = text_aligned_table_cells((49, 658, 564, 763), words, rules)
        self.assertEqual(
            self._cells_text(cells, words),
            ["Infectious Diarrhoea", "Acute diarrhea", "Chronic diarrhea",
             "Treatment approaches", "●Rehydration therapy",
             "●Antibiotic regime of ciprofloxacin", "●Rehydration therapy",
             "Refs", "[15]", "[51]"],
        )
        header, acute = cells[0], cells[1]
        self.assertLess(header[3], 672)  # the header cell stops at its rule
        self.assertGreater(acute[3], 692)  # a label may grow into the space beside its bullets
        self.assertLess(acute[3], 707)  # but never into the next row

    def test_a_record_name_set_out_is_its_own_cell_above_its_details(self):
        """Combatting Table 4: rows touch, and only the left edge marks a new record."""
        words = (
            _words(0, 77, (52, "RotaTeq"), (171, "Merck Sharp"))
            + _words(1, 86, (60, "Live attenuated oral"), (179, "Prequalified and licensed"))
            + _words(2, 95, (60, "vaccine"), (179, "by the WHO."))
            + _words(3, 104, (52, "Rotarix"), (171, "GlaxoSmithKline"))
            + _words(4, 113, (60, "Live attenuated oral"), (179, "Received prequalification"))
        )
        cells = text_aligned_table_cells((48, 63, 400, 130), words)
        self.assertEqual(
            self._cells_text(cells, words),
            ["RotaTeq", "Live attenuated oral vaccine", "Rotarix", "Live attenuated oral",
             "Merck Sharp", "Prequalified and licensed by the WHO.", "GlaxoSmithKline",
             "Received prequalification"],
        )

    def test_justified_spaces_and_a_centred_heading_are_not_gutters(self):
        words = (
            _words(0, 10, (200, "Treatment approaches"), space=9.0)
            + _words(1, 26, (10, "Label"), (155, "stretched words set wide"), space=5.0)
            + _words(2, 42, (10, "Other"), (155, "stretched words again"), space=5.0)
        )
        self.assertEqual(
            self._cells_text(text_aligned_table_cells((0, 0, 400, 50), words), words),
            ["Label", "Other", "Treatment approaches", "stretched words set wide",
             "stretched words again"],
        )

    def test_text_without_a_column_gutter_stays_protected(self):
        words = (_words(0, 10, (10, "a paragraph of ordinary prose that wraps"))
                 + _words(1, 20, (10, "onto a second line of the same width")))
        self.assertEqual(text_aligned_table_cells((0, 0, 300, 40), words), [])

    def test_a_line_spanning_the_gutter_merges_its_columns(self):
        words = (
            _words(0, 10, (10, "Name"), (200, "Value"))
            + _words(1, 20, (10, "A heading that runs straight across the gutter"))
            + _words(2, 30, (10, "Alpha"), (200, "First"))
        )
        # One column left means nothing divides cleanly, so the table stays whole.
        self.assertEqual(text_aligned_table_cells((0, 0, 400, 50), words), [])


class PreservationRuleTests(unittest.TestCase):
    def test_formula_rule_covers_math_and_monospace_code_fonts(self):
        for font in (
            "CMMI10",
            "TeX-math-symbols",
            "STIXMath",
            "Consolas",
            "CourierNewPSMT",
            "SourceCodePro-Regular",
        ):
            with self.subTest(font=font):
                self.assertTrue(is_formula_font(font))
        self.assertFalse(is_formula_font("TimesNewRomanPSMT"))

    def test_operator_only_ordinary_font_block_is_protected_as_formula(self):
        blocks = [(10, 20, 90, 40, "F1 / b0 ≤ C2 [N/mm]")]
        self.assertEqual(formula_regions(blocks, []), [(10.0, 20.0, 90.0, 40.0)])

    def test_trigonometric_functions_do_not_make_an_equation_look_like_prose(self):
        for equation in (
            "FWA = F1² + F2² - 2 · F1 · F2 · cos β [N]",
            "FR = 2 · F2 · cos γ / 2 - FTR [N]",
            "FW6 = √2 · F2 · sin (β/2) [N]",
        ):
            with self.subTest(equation=equation):
                blocks = [(10, 20, 190, 40, equation)]
                self.assertEqual(
                    formula_regions(blocks, []),
                    [(10.0, 20.0, 190.0, 40.0)],
                )

    def test_prose_containing_variables_is_not_protected_as_a_formula(self):
        blocks = [(10, 20, 190, 40, "If F1 is larger than C2, use another belt")]
        self.assertEqual(formula_regions(blocks, []), [])

    def test_stacked_numbered_identifiers_are_protected_inside_prose(self):
        words = [
            (10, 10, 20, 20, "F1", 7, 0, 0),
            (10, 20, 20, 30, "b0", 7, 1, 0),
            (30, 20, 60, 30, "value", 7, 1, 1),
        ]
        self.assertEqual(formula_regions([], words), [(10.0, 10.0, 20.0, 30.0)])

    def test_stacked_detection_does_not_cross_a_protected_table(self):
        words = [
            (10, 10, 20, 20, "F1", 7, 0, 0),
            (10, 20, 20, 30, "F2", 7, 1, 0),
        ]
        self.assertEqual(
            formula_regions([], words, stacked_exclusions=[(0, 0, 100, 100)]),
            [],
        )

    def test_table_cells_require_a_half_area_model_match(self):
        matching = SimpleNamespace(
            bbox=(0, 0, 100, 140),
            cells=[(0, 0, 50, 50), (50, 0, 100, 50), (0, 100, 100, 140)],
        )
        distant = SimpleNamespace(bbox=(200, 200, 300, 300), cells=[(200, 200, 300, 300)])
        self.assertEqual(
            matching_table_cells((0, 0, 100, 100), [distant, matching]),
            [(0.0, 0.0, 50.0, 50.0), (50.0, 0.0, 100.0, 50.0)],
        )
        self.assertEqual(
            matching_table_cells((0, 0, 100, 100), [SimpleNamespace(bbox=(0, 0, 40, 100), cells=[])]),
            [],
        )

    def test_table_codes_and_numbers_stay_as_original_glyphs(self):
        for value in ("E 2/1, E 3/1, NOVO", "180° 210° 240°", "2.0"):
            with self.subTest(value=value):
                self.assertFalse(should_translate_table_cell(value))
        self.assertTrue(should_translate_table_cell("Tension member"))
        self.assertTrue(should_translate_table_cell("Lớp phủ mặt dưới"))

    def test_merged_description_and_code_cell_splits_into_x_clusters(self):
        words = [
            (0, 0, 20, 10, "Drive", 1, 0, 0),
            (22, 0, 45, 10, "drum", 1, 0, 1),
            (47, 0, 80, 10, "diameter", 1, 0, 2),
            (160, 0, 170, 10, "dA", 1, 0, 3),
        ]
        clusters = cluster_table_words(words, (0, 0, 180, 12))
        self.assertEqual([cluster.text for cluster in clusters], ["Drive drum diameter", "dA"])
        self.assertTrue(should_translate_table_cell(clusters[0].text))
        self.assertFalse(should_translate_table_cell(clusters[1].text))

    def test_wrapped_description_lines_stay_in_one_cluster(self):
        words = [
            (0, 0, 30, 10, "Maximum", 1, 0, 0),
            (32, 0, 50, 10, "belt", 1, 0, 1),
            (0, 11, 20, 21, "pull", 1, 1, 0),
            (22, 11, 55, 21, "allowed", 1, 1, 1),
            (160, 5, 170, 15, "F1", 1, 2, 0),
        ]
        clusters = cluster_table_words(words, (0, 0, 180, 24))
        self.assertEqual(len(clusters), 2)
        self.assertIn("Maximum", clusters[0].text)
        self.assertIn("allowed", clusters[0].text)

    def test_vietnamese_line_height_and_extended_bullets_are_preserved(self):
        self.assertEqual(line_height_for_language("vi"), 1.2)
        self.assertTrue({"•", "■", "▸", "◆", "⬤"}.issubset(BULLET_CHARACTERS))

    def test_vietnamese_leading_never_goes_below_its_measured_ink(self):
        """Rendering every letter of the output font gives 0.890 em above the
        baseline for stacked tone marks and 0.210 em below for dot-below vowels,
        so lines closer than 1.10 em are drawn through each other. The leading
        used to be compressed to 0.75 to buy room for a longer translation."""
        self.assertGreaterEqual(min_line_height_for_language("vi"), 1.10)

    def test_every_target_can_be_typeset_without_lines_touching(self):
        for language in ("vi", "en", "fr", "de", "unknown-code"):
            with self.subTest(language=language):
                minimum = min_line_height_for_language(language)
                self.assertGreaterEqual(minimum, 0.9)
                self.assertLessEqual(minimum, line_height_for_language(language))

    def test_office_private_use_bullets_keep_their_dingbat_font(self):
        for character, font in (
            ("\uf0d8", "Wingdings"),
            ("\uf0b7", "Symbol"),
            ("\uf0fc", "Wingdings"),
        ):
            with self.subTest(character=hex(ord(character)), font=font):
                self.assertTrue(is_bullet_character(character, font))
        self.assertFalse(is_bullet_character("\uf0d8", "Times New Roman"))

    def test_full_page_image_is_classified_as_scanned(self):
        self.assertTrue(
            is_scanned_page([{"type": 1, "bbox": (0, 0, 80, 80)}], 10_000)
        )
        self.assertFalse(
            is_scanned_page([{"type": 1, "bbox": (0, 0, 20, 20)}], 10_000)
        )

    def test_table_of_contents_page_keeps_number_alignment(self):
        text = "Table of Contents\n" + "\n".join(
            f"Chapter {index} .......... {index * 3}" for index in range(1, 6)
        )
        decision = classify_preserved_page(text)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.kind, "TOC")

    def test_index_page_keeps_term_and_page_number_columns(self):
        text = "Index\nAlpha, 11\nBeta, 12\nGamma, 13"
        decision = classify_preserved_page(text)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.kind, "INDEX")

    def test_nomenclature_page_keeps_symbol_definition_pairs(self):
        lines = ["Nomenclature"]
        for symbol, definition in (
            ("E", "Energy of the system"),
            ("m", "Mass of the particle"),
            ("c", "Speed of light"),
            ("F", "Applied force"),
            ("a", "Measured acceleration"),
        ):
            lines.extend((symbol, definition))
        decision = classify_preserved_page("\n".join(lines))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.kind, "NOMENCLATURE")

    def test_reference_page_keeps_citation_numbering(self):
        text = "References\n" + "\n".join(
            f"[{index}] Author, A. ({2020 + index}). https://doi.org/10.1/{index}"
            for index in range(1, 6)
        )
        decision = classify_preserved_page(text)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.kind, "REFERENCES")

    def test_normal_prose_is_not_misclassified(self):
        self.assertIsNone(
            classify_preserved_page(
                "A short introduction\nThis paragraph explains a translation system."
            )
        )

    def test_converter_rejects_an_unregistered_service(self):
        with self.assertRaisesRegex(ValueError, "Unsupported translation service"):
            TranslateConverter(PDFResourceManager(), service="bing")

    def test_converter_accepts_every_registered_engine(self):
        for service in ("google", "handoff"):
            with self.subTest(service=service):
                converter = TranslateConverter(PDFResourceManager(), service=service)
                self.assertEqual(converter.translator.name, service)

    def test_converter_keeps_the_late_filled_cell_bounds_mapping(self):
        bounds = {}
        converter = TranslateConverter(
            PDFResourceManager(), service="google", layout_bounds=bounds
        )
        bounds[0] = {7: (1, 2, 3, 4)}
        self.assertIs(converter.layout_bounds, bounds)
        self.assertEqual(converter.layout_bounds[0][7], (1, 2, 3, 4))


class TranslatableSegmentTests(unittest.TestCase):
    """The count these drive decides whether a document is called a scan."""

    def test_real_text_is_translatable(self):
        self.assertTrue(is_translatable_segment("Chapter 1", set()))
        self.assertTrue(is_translatable_segment("<s1>Bold</s1> text", set()))

    def test_blank_and_placeholder_only_runs_are_not_work(self):
        for empty in ("", "   ", "<s1></s1>", "{v3}", " {v12} "):
            with self.subTest(segment=empty):
                self.assertFalse(is_translatable_segment(empty, set()))

    def test_preserved_segments_are_not_counted(self):
        self.assertFalse(is_translatable_segment("E = mc2", {"E = mc2"}))

    def test_a_figure_caption_beside_a_formula_still_counts(self):
        """A page of diagrams that carries one real line is not an image-only
        page, so a scan-heavy textbook is never refused for needing OCR."""
        self.assertTrue(is_translatable_segment("Figure 3: the lever arm", {"{v1}"}))


class ImageOnlyPageTests(unittest.TestCase):
    def test_any_image_counts_even_when_no_single_tile_is_large(self):
        """Scanners emit a page as dozens of tiles, so the half-page rule that
        drives backing rectangles misses most scanned pages entirely."""
        tiles = [{"type": 1, "bbox": (0, 0, 60, 60)} for _ in range(12)]
        self.assertTrue(page_has_image(tiles))
        self.assertFalse(is_scanned_page(tiles, page_area=600 * 800))

    def test_a_page_of_text_alone_carries_no_image(self):
        self.assertFalse(page_has_image([{"type": 0, "bbox": (0, 0, 10, 10)}]))
        self.assertFalse(page_has_image([]))

    def test_a_page_is_judged_on_everything_it_contributed(self):
        """receive_layout runs once per nested container and only the last call
        carries the text, so judging a page as each call arrives reported a
        fully translated page as an untranslated scan."""
        converter = TranslateConverter(PDFResourceManager(), service="google")
        converter.pages_with_images.update({0, 1})
        for count in (0, 0, 0, 14):  # one page, four calls
            converter.segments_by_page[0] += count
        converter.segments_by_page[1] += 0

        self.assertEqual(converter.image_only_pages, {1})

    def test_a_blank_page_is_not_called_a_scan(self):
        converter = TranslateConverter(PDFResourceManager(), service="google")
        converter.segments_by_page[3] += 0

        self.assertEqual(converter.image_only_pages, set())


if __name__ == "__main__":
    unittest.main()
