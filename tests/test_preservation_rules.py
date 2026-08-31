from __future__ import annotations

import unittest
from types import SimpleNamespace

from pdfminer.pdfinterp import PDFResourceManager

from pdf2zh.converter import TranslateConverter
from pdf2zh.rules import (
    BULLET_CHARACTERS,
    classify_preserved_page,
    cluster_table_words,
    formula_regions,
    is_bullet_character,
    is_formula_font,
    is_scanned_page,
    line_height_for_language,
    matching_table_cells,
    min_line_height_for_language,
    should_translate_table_cell,
)


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
        blocks = [(10, 20, 90, 40, "F1 / b0 â‰¤ C2 [N/mm]")]
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
        for value in ("E 2/1, E 3/1, NOVO", "180Â° 210Â° 240Â°", "2.0"):
            with self.subTest(value=value):
                self.assertFalse(should_translate_table_cell(value))
        self.assertTrue(should_translate_table_cell("Tension member"))
        self.assertTrue(should_translate_table_cell("Lá»›p phá»§ máº·t dÆ°á»›i"))

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


if __name__ == "__main__":
    unittest.main()
