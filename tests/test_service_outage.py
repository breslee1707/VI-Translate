from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import requests

from pdf2zh.converter import request_translation
from pdf2zh.translator import (
    GoogleTranslator,
    NetworkBlock,
    OutageBackoff,
    RateLimitedError,
    RequestPace,
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
        self.started: list[float] = []
        self.finished: list[float] = []

    def __call__(self, endpoint, params, headers, timeout):
        self.sent.append(params["q"])
        self.started.append(self.clock.now)
        self.clock.now += 1.0
        self.finished.append(self.clock.now)
        return self.answer(params["q"])


def translator_answering(
    answer=lambda _text: blocked(),
    clock: FakeClock | None = None,
    block: NetworkBlock | None = None,
) -> tuple[GoogleTranslator, FakeClock, FakeGoogle]:
    """A translator whose network, clock and memory of blocks are all fakes."""
    clock = clock or FakeClock()
    translator = GoogleTranslator("en", "vi", ignore_cache=True)
    translator.outage = OutageBackoff(clock=clock.clock, sleep=clock.sleep)
    translator.pace = RequestPace(clock=clock.clock, sleep=clock.sleep)
    translator.block = block or NetworkBlock(clock=clock.clock)
    translator.sleep = clock.sleep
    google = FakeGoogle(clock, answer)
    translator.session.get = google
    return translator, clock, google


class GoogleBlockTests(unittest.TestCase):
    def test_a_block_stops_requests_at_once(self):
        """0.3.0 sent each blocked segment eight more times, which kept the block alive."""
        translator, clock, google = translator_answering()
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Hello")
        self.assertEqual(len(google.sent), 1)
        self.assertEqual(clock.sleeps, [])

    def test_a_blocked_document_sends_one_request_not_one_per_segment(self):
        translator, _clock, google = translator_answering()
        refused = 0
        for number in range(40):
            try:
                request_translation(translator, f"Segment {number}")
            except RateLimitedError:
                refused += 1
        self.assertEqual(refused, 40)
        self.assertEqual(len(google.sent), 1)

    def test_a_blocked_page_refuses_every_segment_after_one_request(self):
        translator, _clock, google = translator_answering()
        answers = translator.translate_many([f"Sentence {n} " * 40 for n in range(300)])
        self.assertTrue(all(isinstance(answer, RateLimitedError) for answer in answers))
        self.assertEqual(len(google.sent), 1)

    def test_the_next_document_in_the_queue_sends_nothing_into_the_block(self):
        clock = FakeClock()
        block = NetworkBlock(clock=clock.clock)
        first, _clock, first_google = translator_answering(clock=clock, block=block)
        first.translate_many(["Hello", "World"])
        second, _clock, second_google = translator_answering(
            lambda text: translation(f"vi:{text}"), clock=clock, block=block
        )
        answers = second.translate_many(["Hello", "World"])
        self.assertTrue(all(isinstance(answer, RateLimitedError) for answer in answers))
        self.assertEqual(len(first_google.sent), 1)
        self.assertEqual(second_google.sent, [])

    def test_one_request_per_interval_checks_whether_the_block_lifted(self):
        state = {"lifted": False}
        translator, clock, google = translator_answering(
            lambda text: translation(f"vi:{text}") if state["lifted"] else blocked()
        )
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Hello")
        clock.now += NetworkBlock.RECHECK_AFTER / 2
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Hello")
        self.assertEqual(len(google.sent), 1)

        clock.now += NetworkBlock.RECHECK_AFTER
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Still blocked")
        self.assertEqual(len(google.sent), 2)
        with self.assertRaises(RateLimitedError):
            translator.do_translate("Not sent")
        self.assertEqual(len(google.sent), 2)

        state["lifted"] = True
        clock.now += NetworkBlock.RECHECK_AFTER
        self.assertEqual(translator.do_translate("World"), "vi:World")
        self.assertEqual(translator.do_translate("Again"), "vi:Again")
        self.assertEqual(len(google.sent), 4)

    def test_the_next_run_remembers_the_block(self):
        clock = FakeClock()
        clock.now = 1_000_000.0
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / "pdf2zh" / "google-block.json"
            NetworkBlock(str(record), clock=clock.clock).refused()
            self.assertEqual(json.loads(record.read_text())["blocked_at"], clock.now)

            translator, _clock, google = translator_answering(
                lambda text: translation(f"vi:{text}"),
                clock=clock,
                block=NetworkBlock(str(record), clock=clock.clock),
            )
            with self.assertRaises(RateLimitedError):
                translator.do_translate("Hello")
            self.assertEqual(google.sent, [])

            clock.now += NetworkBlock.RECHECK_AFTER
            self.assertEqual(translator.do_translate("Hello"), "vi:Hello")
            self.assertFalse(record.exists())

    def test_an_unreadable_record_does_not_stop_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / "google-block.json"
            record.write_text("not json", encoding="utf-8")
            translator, _clock, _google = translator_answering(
                lambda text: translation(f"vi:{text}"),
                block=NetworkBlock(str(record)),
            )
            self.assertEqual(translator.do_translate("Hello"), "vi:Hello")

    def test_a_clock_set_back_does_not_hold_requests_forever(self):
        clock = FakeClock()
        clock.now = 5_000.0
        block = NetworkBlock(clock=clock.clock)
        block.refused()
        clock.now = 1_000.0
        block.before_request()


class GoogleOutageTests(unittest.TestCase):
    def test_a_brief_outage_is_waited_out_and_the_segment_still_translates(self):
        answers = iter([google_answer(503), translation("Xin chào")])
        translator, clock, google = translator_answering(lambda _text: next(answers))
        self.assertEqual(translator.do_translate("Hello"), "Xin chào")
        self.assertIn(OutageBackoff.FIRST_PAUSE, clock.sleeps)
        self.assertEqual(len(google.sent), 2)

    def test_a_lasting_outage_is_given_up_after_one_patience_with_few_requests(self):
        translator, clock, google = translator_answering(lambda _text: google_answer(503))
        with self.assertRaises(ServiceUnavailableError):
            translator.do_translate("Hello")
        self.assertGreaterEqual(clock.now, OutageBackoff.PATIENCE)
        self.assertLess(clock.now, OutageBackoff.PATIENCE + 2 * OutageBackoff.LONGEST_PAUSE)
        self.assertLessEqual(len(google.sent), 8)

    def test_a_dead_connection_is_an_outage_that_does_not_repeat_the_document_text(self):
        def unreachable(text):
            raise requests.ConnectionError(f"Max retries exceeded with url: /m?q={text}")

        translator, _clock, _google = translator_answering(unreachable)
        with self.assertRaises(ServiceUnavailableError) as raised:
            translator.do_translate("confidential wording")
        self.assertNotIn("confidential", str(raised.exception))

    def test_a_rejected_segment_is_refused_at_once(self):
        translator, clock, google = translator_answering(lambda _text: google_answer(400))
        with self.assertRaises(SegmentRejectedError):
            translator.do_translate("Hello")
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(len(google.sent), 1)


class RequestPaceTests(unittest.TestCase):
    def test_requests_go_one_at_a_time_a_gap_apart(self):
        translator, _clock, google = translator_answering(lambda text: translation(f"vi:{text}"))
        for word in ("one", "two", "three"):
            translator.do_translate(word)
        for finished, started in zip(google.finished, google.started[1:]):
            self.assertGreaterEqual(started - finished, RequestPace.GAP)

    def test_a_refused_request_does_not_cost_a_gap(self):
        clock = FakeClock()
        block = NetworkBlock(clock=clock.clock)
        block.refused()
        translator, _clock, _google = translator_answering(clock=clock, block=block)
        for _ in range(100):
            with self.assertRaises(RateLimitedError):
                translator.do_translate("Hello")
        self.assertEqual(clock.sleeps, [])


class OutageBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.backoff = OutageBackoff(clock=self.clock.clock, sleep=self.clock.sleep)

    def test_workers_that_meet_one_outage_pause_once(self):
        first = self.backoff.before_request()
        second = self.backoff.before_request()
        self.backoff.failed(first, ServiceUnavailableError("down"))
        self.backoff.failed(second, ServiceUnavailableError("down"))
        self.backoff.before_request()
        self.assertEqual(self.clock.sleeps, [OutageBackoff.FIRST_PAUSE])

    def test_a_reply_already_on_its_way_does_not_end_the_outage(self):
        first = self.backoff.before_request()
        second = self.backoff.before_request()
        self.backoff.failed(first, ServiceUnavailableError("down"))
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
        self.backoff.failed(started, ServiceUnavailableError("down"))
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
