from __future__ import annotations

import html
import unittest
from urllib.parse import quote_plus

from pdf2zh.translator import (
    GoogleTranslator,
    RateLimitedError,
    SegmentRejectedError,
    result_lines,
)
from tests.test_service_outage import (
    blocked,
    google_answer,
    translation,
    translator_answering,
)


def line_by_line(text: str) -> str:
    """Translate the way Google does: every line on its own, line breaks kept."""
    return "\n".join(f"vi:{line}" if line.strip() else line for line in text.split("\n"))


def google_page(translated: str):
    return translation(html.escape(translated))


class FakeCache:
    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries = dict(entries or {})

    def get(self, text: str) -> str | None:
        return self.entries.get(text)

    def set(self, text: str, translated: str) -> None:
        self.entries[text] = translated


class GoogleBatchTests(unittest.TestCase):
    def test_a_page_of_segments_is_one_request(self):
        translator, _clock, google = translator_answering(
            lambda text: google_page(line_by_line(text))
        )
        segments = [f"Paragraph {number} of the page." for number in range(30)]
        answers = translator.translate_many(segments)
        self.assertEqual(answers, [f"vi:{segment}" for segment in segments])
        self.assertEqual(len(google.sent), 1)

    def test_formula_and_style_tags_travel_inside_their_own_line(self):
        translator, _clock, _google = translator_answering(
            lambda text: google_page(line_by_line(text))
        )
        segments = ["Energy <b0></b0> is conserved.", "<s1>Bold</s1> heading"]
        self.assertEqual(
            translator.translate_many(segments),
            ["vi:Energy <b0></b0> is conserved.", "vi:<s1>Bold</s1> heading"],
        )

    def test_no_request_carries_more_than_the_query_budget(self):
        translator, _clock, google = translator_answering(
            lambda text: google_page(line_by_line(text))
        )
        segments = [f"Sentence {number} " + "word " * 150 for number in range(40)]
        answers = translator.translate_many(segments)
        self.assertEqual(answers, [f"vi:{segment.strip()}" for segment in segments])
        self.assertGreater(len(google.sent), 1)
        for query in google.sent:
            self.assertLessEqual(len(quote_plus(query)), GoogleTranslator.BATCH_QUERY_BYTES)

    def test_vietnamese_is_measured_as_it_travels_percent_encoded(self):
        translator, _clock, google = translator_answering(
            lambda text: google_page(line_by_line(text))
        )
        # About 2100 characters each, but some 3900 bytes once in the URL.
        segments = [f"Đoạn {number}: " + "rủi ro thanh khoản " * 110 for number in range(4)]
        translator.translate_many(segments)
        self.assertEqual(len(google.sent), 4)

    def test_a_segment_holding_a_line_break_travels_alone(self):
        translator, _clock, google = translator_answering(
            lambda text: google_page(line_by_line(text))
        )
        translator.translate_many(["First.", "Broken\nline", "Last."])
        self.assertIn("Broken\nline", google.sent)
        self.assertEqual(len(google.sent), 2)

    def test_repeated_and_cached_segments_are_not_sent(self):
        translator, _clock, google = translator_answering(
            lambda text: google_page(line_by_line(text))
        )
        translator.ignore_cache = False
        translator.cache = FakeCache({"Header": "Tiêu đề"})
        answers = translator.translate_many(["Header", "Body", "Body", "Header"])
        self.assertEqual(answers, ["Tiêu đề", "vi:Body", "vi:Body", "Tiêu đề"])
        self.assertEqual(google.sent, ["Body"])
        self.assertEqual(translator.cache.get("Body"), "vi:Body")

    def test_a_segment_that_leaves_its_line_costs_its_half_not_the_page(self):
        def merge_the_run_on(text: str):
            translated = line_by_line(text)
            # Google joins this one segment to the next line.
            return google_page(translated.replace("vi:Run on\n\n", "vi:Run on "))

        translator, _clock, google = translator_answering(merge_the_run_on)
        segments = [f"Sentence {number}." for number in range(32)]
        segments[20] = "Run on"
        answers = translator.translate_many(segments)
        wrong = [answer for answer, segment in zip(answers, segments) if answer != f"vi:{segment}"]
        self.assertEqual(wrong, [])
        self.assertLess(len(google.sent), 16)

    def test_a_service_that_loses_line_breaks_stops_batching(self):
        translator, _clock, google = translator_answering(
            lambda text: google_page(" ".join(f"vi:{line}" for line in text.split("\n") if line))
        )
        segments = [f"Sentence {number}." for number in range(40)]
        answers = translator.translate_many(segments)
        self.assertEqual(answers, [f"vi:{segment}" for segment in segments])
        self.assertEqual(len(google.sent), GoogleTranslator.BATCH_MISSES_ALLOWED + 40)

    def test_line_breaks_written_as_br_tags_are_read(self):
        page = '<div class="result-container">vi:One<br>vi:Two<br/>vi:&lt;b0&gt;&lt;/b0&gt;</div>'
        self.assertEqual(result_lines(page), ["vi:One", "vi:Two", "vi:<b0></b0>"])

    def test_a_rejected_batch_is_halved_until_the_rejected_segment_stands_alone(self):
        def reject(text: str):
            if "Refused" in text:
                return google_answer(400)
            return google_page(line_by_line(text))

        translator, _clock, _google = translator_answering(reject)
        segments = [f"Sentence {number}." for number in range(8)]
        segments[3] = "Refused"
        answers = translator.translate_many(segments)
        self.assertIsInstance(answers[3], SegmentRejectedError)
        self.assertEqual(
            [answer for index, answer in enumerate(answers) if index != 3],
            [f"vi:{segment}" for index, segment in enumerate(segments) if index != 3],
        )

    def test_a_block_midway_refuses_the_rest_without_sending(self):
        state = {"sent": 0}

        def block_the_second(text: str):
            state["sent"] += 1
            return google_page(line_by_line(text)) if state["sent"] == 1 else blocked()

        translator, _clock, google = translator_answering(block_the_second)
        segments = [f"Sentence {number} " + "word " * 150 for number in range(40)]
        answers = translator.translate_many(segments)
        translated = [answer for answer in answers if isinstance(answer, str)]
        refused = [answer for answer in answers if isinstance(answer, RateLimitedError)]
        self.assertTrue(translated)
        self.assertEqual(len(translated) + len(refused), 40)
        self.assertEqual(len(google.sent), 2)

    def test_a_glitch_is_retried_for_the_whole_batch(self):
        answers = iter([google_answer(200, body="<html>no result</html>")])

        def glitch_once(text: str):
            return next(answers, None) or google_page(line_by_line(text))

        translator, _clock, google = translator_answering(glitch_once)
        self.assertEqual(translator.translate_many(["One.", "Two."]), ["vi:One.", "vi:Two."])
        self.assertEqual(len(google.sent), 2)


if __name__ == "__main__":
    unittest.main()
