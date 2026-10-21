from __future__ import annotations

import unittest
from unittest.mock import patch

from peewee import OperationalError

from pdf2zh.cache import (
    TranslationCache,
    _TranslationCache,
    clean_test_db,
    init_test_db,
)
from tests.test_google_batching import google_page, line_by_line
from tests.test_service_outage import translator_answering

LOCKED = OperationalError("database is locked")


class TranslationCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_db = init_test_db()
        self.cache = TranslationCache("google", {"lang_in": "en", "lang_out": "vi"})

    def tearDown(self) -> None:
        clean_test_db(self.test_db)

    def test_a_cache_that_cannot_be_read_is_a_miss(self):
        self.cache.set("Hello", "Xin chào")
        self.assertEqual(self.cache.get("Hello"), "Xin chào")
        with patch.object(_TranslationCache, "get_or_none", side_effect=LOCKED):
            self.assertIsNone(self.cache.get("Hello"))

    def test_a_locked_cache_does_not_stop_the_page(self):
        """The page's segments were read from the cache unguarded, so one
        locked read threw away a document halfway through its pages."""
        translator, _clock, google = translator_answering(
            lambda text: google_page(line_by_line(text))
        )
        translator.ignore_cache = False
        translator.cache = self.cache
        with patch.object(_TranslationCache, "get_or_none", side_effect=LOCKED):
            answers = translator.translate_many(["The library opens at nine."])
        self.assertEqual(answers, ["vi:The library opens at nine."])
        self.assertEqual(len(google.sent), 1)


if __name__ == "__main__":
    unittest.main()
