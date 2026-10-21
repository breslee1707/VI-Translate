from __future__ import annotations

import string
import unittest
from pathlib import Path

from app.errors import ERROR_CODES, describe_failure, report_text
from app.i18n import (
    CATALOGS,
    DEFAULT_UI_LANGUAGE,
    UI_LANGUAGES,
    set_ui_language,
    t,
    ui_language,
)


def fields(text: str) -> set[str]:
    return {name for _literal, name, _spec, _conversion in string.Formatter().parse(text) if name}


class CatalogTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_ui_language(None)

    def test_every_interface_language_has_a_catalog(self):
        self.assertEqual(set(UI_LANGUAGES), set(CATALOGS))
        self.assertIn(DEFAULT_UI_LANGUAGE, CATALOGS)

    def test_every_catalog_says_everything_the_vietnamese_one_does(self):
        reference = CATALOGS[DEFAULT_UI_LANGUAGE]
        for code, catalog in CATALOGS.items():
            with self.subTest(language=code):
                self.assertEqual(set(catalog), set(reference))

    def test_placeholders_match_so_no_value_is_lost_or_raises(self):
        reference = CATALOGS[DEFAULT_UI_LANGUAGE]
        for code, catalog in CATALOGS.items():
            for key, text in catalog.items():
                with self.subTest(language=code, key=key):
                    self.assertEqual(fields(text), fields(reference[key]))
                    self.assertTrue(text.strip())

    def test_every_error_code_has_a_summary_and_advice_in_every_language(self):
        for code in CATALOGS:
            for error in ERROR_CODES:
                with self.subTest(language=code, error=error):
                    self.assertIn(f"{error}.summary", CATALOGS[code])
                    self.assertIn(f"{error}.advice", CATALOGS[code])

    def test_an_unknown_language_keeps_vietnamese(self):
        self.assertEqual(set_ui_language("xx"), DEFAULT_UI_LANGUAGE)
        self.assertEqual(ui_language(), DEFAULT_UI_LANGUAGE)

    def test_placeholders_are_filled(self):
        set_ui_language("en")
        self.assertEqual(t("queue.title_count", count=3), "Queue (3)")

    def test_a_failure_reads_in_the_interface_language(self):
        """#21: a user who cannot read Vietnamese could not act on an error either."""
        set_ui_language("en")
        failure = describe_failure(ImportError("pikepdf._core failed"))
        self.assertEqual(failure.code, "E-CORE-01")
        self.assertIn("Extract the WHOLE folder", failure.advice)
        report = report_text(failure, Path("book.pdf"), "0.4.0", Path("C:/x/pdf-translate.log"))
        for expected in ("error report", "Error code", "E-CORE-01", "pdf-translate.log"):
            with self.subTest(expected=expected):
                self.assertIn(expected, report)

    def test_the_default_stays_vietnamese(self):
        failure = describe_failure(ImportError("pikepdf._core failed"))
        self.assertIn("giải nén", failure.advice)


if __name__ == "__main__":
    unittest.main()
