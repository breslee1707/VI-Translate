from __future__ import annotations

import unittest

import requests

from pdf2zh.converter import request_translation
from pdf2zh.translator import (
    GoogleTranslator,
    OutageBackoff,
    RateLimitedError,
    SegmentRejectedError,
    SegmentTooLongError,
    ServiceUnavailableError,
)

ENDPOINT = "https://translate.google.com/m"
# What the endpoint really answered a heavy run with: a 302 to this page, then 429.
CAPTCHA_PAGE = "https://www.google.com/sorry/index?continue=https://translate.google.com/m"


class FakeClock:
    """Time that passes only when the code under test sleeps or sends."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def google_answer(status: int, url: str = ENDPOINT, body: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode("utf-8")
    response.encoding = "utf-8"
    return response


def translation(text: str) -> requests.Response:
    return google_answer(200, body=f'<div class="result-container">{text}</div>')


def blocked() -> requests.Response:
    return google_answer(429, CAPTCHA_PAGE, "Our systems have detected unusual traffic")


class FakeGoogle:
    """Answers each request from `answer`, and takes a second doing it."""

    def __init__(self, clock: FakeClock, answer) -> None:
        self.clock = clock
        self.answer = answer
        self.sent: list[str] = []

    def __call__(self, endpoint, params, headers, timeout):
        self.sent.append(params["q"])
        self.clock.now += 1.0
        return self.answer(params["q"])


def translator_answering(answer=lambda _text: blocked()) -> tuple[GoogleTranslator, FakeClock, FakeGoogle]:
    clock = FakeClock()
    translator = GoogleTranslator("en", "vi", ignore_cache=True)
    translator.outage = OutageBackoff(clock=clock.clock, sleep=clock.sleep)
    google = FakeGoogle(clock, answer)
    translator.session.get = google
    return translator, clock, google


class GoogleOutageTests(unittest.TestCase):
    def test_a_brief_block_is_waited_out_and_the_segment_still_translates(self):
        answers = iter([blocked(), translation("Xin chào")])
        translator, clock, google = translator_answering(lambda _text: next(answers))
        self.assertEqual(translator.do_translate("Hello"), "Xin chào")
        self.assertEqual(clock.sleeps, [OutageBackoff.FIRST_PAUSE])
        self.assertEqual(len(google.sent), 2)

    def test_a_lasting_block_is_given_up_after_one_patience_with_few_requests(self):
        translator, clock, google = translator_answering()
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Hello")
        self.assertGreaterEqual(clock.now, OutageBackoff.PATIENCE)
        self.assertLess(clock.now, OutageBackoff.PATIENCE + 2 * OutageBackoff.LONGEST_PAUSE)
        self.assertLessEqual(len(google.sent), 8)

    def test_a_blocked_document_costs_one_patience_not_one_per_segment(self):
        """The old per-segment retry spent about two minutes on every segment, so
        forty blocked segments on four threads took twenty minutes and sent 320
        requests into the block."""
        translator, clock, google = translator_answering()
        refused = 0
        for number in range(40):
            try:
                request_translation(translator, f"Segment {number}")
            except RateLimitedError:
                refused += 1
        self.assertEqual(refused, 40)
        self.assertLess(clock.now, 2 * OutageBackoff.PATIENCE)
        self.assertLessEqual(len(google.sent), 8)

    def test_after_patience_later_segments_are_refused_without_sending_or_waiting(self):
        translator, clock, google = translator_answering()
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Hello")
        sent, waited, now = len(google.sent), list(clock.sleeps), clock.now
        with self.assertRaises(RateLimitedError):
            translator.do_translate("World")
        self.assertEqual(len(google.sent), sent)
        self.assertEqual(clock.sleeps, waited)
        self.assertEqual(clock.now, now)

    def test_one_request_per_pause_notices_that_the_block_lifted(self):
        answers = {"lifted": False}
        translator, clock, google = translator_answering(
            lambda text: translation(f"vi:{text}") if answers["lifted"] else blocked()
        )
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Hello")
        answers["lifted"] = True
        clock.now += OutageBackoff.LONGEST_PAUSE
        self.assertEqual(translator.do_translate("World"), "vi:World")
        sent = len(google.sent)
        self.assertEqual(translator.do_translate("Again"), "vi:Again")
        self.assertEqual(len(google.sent), sent + 1)

    def test_a_dead_connection_is_an_outage_that_does_not_repeat_the_document_text(self):
        def unreachable(text):
            raise requests.ConnectionError(f"Max retries exceeded with url: /m?q={text}")

        translator, _clock, _google = translator_answering(unreachable)
        with self.assertRaises(ServiceUnavailableError) as raised:
            translator.do_translate("confidential wording")
        self.assertNotIn("confidential", str(raised.exception))

    def test_a_server_error_is_an_outage(self):
        translator, _clock, _google = translator_answering(lambda _text: google_answer(503))
        with self.assertRaises(ServiceUnavailableError):
            translator.do_translate("Hello")

    def test_a_rejected_segment_is_refused_at_once(self):
        translator, clock, google = translator_answering(lambda _text: google_answer(400))
        with self.assertRaises(SegmentRejectedError):
            translator.do_translate("Hello")
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(len(google.sent), 1)


class OutageBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.backoff = OutageBackoff(clock=self.clock.clock, sleep=self.clock.sleep)

    def test_workers_that_meet_one_block_pause_once(self):
        first = self.backoff.before_request()
        second = self.backoff.before_request()
        self.backoff.failed(first, RateLimitedError("blocked"))
        self.backoff.failed(second, RateLimitedError("blocked"))
        self.backoff.before_request()
        self.assertEqual(self.clock.sleeps, [OutageBackoff.FIRST_PAUSE])

    def test_a_reply_already_on_its_way_does_not_end_the_outage(self):
        first = self.backoff.before_request()
        second = self.backoff.before_request()
        self.backoff.failed(first, RateLimitedError("blocked"))
        self.backoff.succeeded(second)
        self.backoff.before_request()
        self.assertEqual(self.clock.sleeps, [OutageBackoff.FIRST_PAUSE])

    def test_the_pause_doubles_up_to_its_limit(self):
        for _ in range(6):
            started = self.backoff.before_request()
            self.backoff.failed(started, ServiceUnavailableError("down"))
        self.assertEqual(self.clock.sleeps, [5.0, 10.0, 20.0, 40.0, 60.0])

    def test_an_answer_after_the_failure_ends_the_outage(self):
        started = self.backoff.before_request()
        self.backoff.failed(started, RateLimitedError("blocked"))
        started = self.backoff.before_request()
        self.backoff.succeeded(started)
        waited = list(self.clock.sleeps)
        self.backoff.before_request()
        self.assertEqual(self.clock.sleeps, waited)


class RequestRetryTests(unittest.TestCase):
    """What reaches the converter's retry is either settled or a one-off glitch."""

    class Translator:
        def __init__(self, error: Exception) -> None:
            self.error = error
            self.calls = 0

        def translate(self, text: str) -> str:
            self.calls += 1
            raise self.error

    def attempts(self, error: Exception) -> int:
        translator = self.Translator(error)
        with self.assertRaises(type(error)):
            request_translation.retry_with(sleep=lambda _seconds: None)(translator, "Hello")
        return translator.calls

    def test_a_segment_the_service_cannot_take_is_not_sent_again(self):
        self.assertEqual(self.attempts(SegmentTooLongError("too long")), 1)
        self.assertEqual(self.attempts(SegmentRejectedError("rejected")), 1)

    def test_an_outage_the_translator_already_waited_out_is_not_retried(self):
        self.assertEqual(self.attempts(RateLimitedError("blocked")), 1)
        self.assertEqual(self.attempts(ServiceUnavailableError("down")), 1)

    def test_a_glitch_in_one_answer_gets_a_few_quick_attempts(self):
        self.assertEqual(self.attempts(RuntimeError("no translation in the page")), 3)


if __name__ == "__main__":
    unittest.main()
