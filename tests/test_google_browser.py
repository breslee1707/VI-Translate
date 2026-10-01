from __future__ import annotations

import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from pdf2zh.google_browser import BrowserBackend, BrowserSession, ENDPOINT, page_verdict
from pdf2zh.translator import GOOGLE_BLOCK, GoogleTranslator, result_lines
from app.errors import describe_failure


class BrowserPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = {"sl": "en", "tl": "vi", "q": "One.\n\nTwo."}
        self.url = ENDPOINT + "?" + urlencode(self.params)

    def test_current_result_keeps_line_breaks_and_formula_tags(self):
        text = "Một.\n\nHai <b0></b0>."
        self.assertEqual(page_verdict({"url": self.url, "result": text}, self.params), ("result", text))

    def test_previous_query_cannot_answer_the_next_request(self):
        params = dict(self.params, q="Other private paragraph")
        self.assertEqual(page_verdict({"url": self.url, "result": "Old result"}, params), ("loading", ""))

    def test_foreign_page_cannot_supply_a_translation(self):
        for url in ("https://example.org/m?" + urlencode(self.params), "https://translate.google.com.evil/m?" + urlencode(self.params)):
            with self.subTest(url=url):
                self.assertEqual(page_verdict({"url": url, "result": "Untrusted"}, self.params), ("loading", ""))

    def test_captcha_is_reported_for_human_verification(self):
        self.assertEqual(page_verdict({"url": "https://www.google.com/sorry/index", "body": "CAPTCHA"}, self.params), ("verification", ""))

    def test_prose_about_unusual_traffic_is_a_normal_result(self):
        text = "Our systems have detected unusual traffic"
        self.assertEqual(page_verdict({"url": self.url, "result": text}, self.params), ("result", text))


class BrowserTransportTests(unittest.TestCase):
    def test_verified_result_reuses_marker_and_batch_parsing(self):
        verification = Mock()
        backend = Mock()
        backend.fetch.return_value = "Một <b0></b0>.\n\n<s1>Hai</s1>."
        session = BrowserSession(verification)
        params = {"sl": "en", "tl": "vi", "q": "One.\n\nTwo."}
        with patch("pdf2zh.google_browser.BACKEND", backend):
            response = session.get(ENDPOINT, params=params)
        self.assertEqual(result_lines(response.text), ["Một <b0></b0>.", "<s1>Hai</s1>."])
        backend.fetch.assert_called_once_with(params, verification, show_on_verification=True)

    def test_transport_cannot_be_used_to_browse_another_endpoint(self):
        with self.assertRaises(ValueError):
            BrowserSession(Mock()).get("https://example.org", params={})

    def test_browser_selection_retains_google_cache_and_separates_cooldowns(self):
        direct = GoogleTranslator("en", "vi", ignore_cache=True)
        browser = GoogleTranslator("en", "vi", ignore_cache=True, envs={"google_browser": True})
        self.assertIsInstance(browser.session, BrowserSession)
        self.assertIsNot(browser.block, GOOGLE_BLOCK)
        self.assertEqual(browser.cache.translate_engine, direct.cache.translate_engine)
        self.assertEqual(browser.cache.translate_engine_params, direct.cache.translate_engine_params)

    def test_human_verification_is_visible_in_translation_status(self):
        browser = GoogleTranslator("en", "vi", ignore_cache=True, envs={"google_browser": True})
        browser.on_status = Mock()
        browser.session.on_verification()
        browser.on_status.assert_called_once_with("verification", 0, 0)

    def test_gui_can_wait_for_user_choice_before_showing_the_browser(self):
        browser = GoogleTranslator("en", "vi", ignore_cache=True,
                                   envs={"google_browser": True, "google_verification_prompt": True})
        self.assertFalse(browser.session.show_on_verification)

    def test_show_and_defer_controls_do_not_send_a_translation(self):
        backend = BrowserBackend()
        backend._connection = Mock()
        self.assertTrue(backend.send_control("show"))
        self.assertTrue(backend.send_control("close"))
        self.assertEqual(backend._connection.send.call_args_list,
                         [unittest.mock.call({"kind": "show"}), unittest.mock.call({"kind": "close"})])

    def test_user_choice_controls_work_while_a_fetch_holds_the_request_lock(self):
        backend = BrowserBackend()
        backend._connection = Mock()
        with backend._lock:
            self.assertTrue(backend.send_control("show"))

    def test_closed_control_channel_is_reported_without_raising(self):
        backend = BrowserBackend()
        backend._connection = Mock()
        backend._connection.send.side_effect = BrokenPipeError()
        self.assertFalse(backend.send_control("show"))

    def test_deferring_verification_has_its_own_reason_and_retains_session(self):
        backend = BrowserBackend()
        backend._connection = Mock()
        self.assertTrue(backend.send_control("close"))
        failure = describe_failure(backend._unavailable())
        self.assertEqual(failure.code, "E-VERIFY-01")
        self.assertIsNotNone(backend._connection)

    def test_failed_defer_control_does_not_claim_the_user_cancelled(self):
        backend = BrowserBackend()
        backend._connection = Mock()
        backend._connection.send.side_effect = BrokenPipeError()
        self.assertFalse(backend.send_control("close"))
        self.assertFalse(backend._verification_deferred)

    def test_defer_choice_wins_if_a_result_arrives_at_the_same_time(self):
        backend = BrowserBackend()
        backend.start = Mock()
        backend._connection = Mock()
        backend._connection.poll.side_effect = lambda _timeout: backend.send_control("close")
        backend._connection.recv.return_value = {"kind": "result", "text": "Fresh result"}
        from pdf2zh.translator import VerificationDeferredError

        with self.assertRaises(VerificationDeferredError):
            backend.fetch({"q": "private words"}, Mock())

    def test_runtime_start_failure_is_not_reported_as_a_google_outage(self):
        from pdf2zh.translator import BrowserRuntimeError

        failure = describe_failure(BrowserRuntimeError("StartupError"))
        self.assertEqual(failure.code, "E-BROWSER-01")
        self.assertIn("WebView2", failure.advice)

    def test_app_choices_show_or_close_the_existing_verification_window(self):
        from types import SimpleNamespace
        from app.gui import App

        app = SimpleNamespace(status=Mock())
        backend = Mock()
        with patch("pdf2zh.google_browser.BACKEND", backend):
            App._choose_google_verification(app, True)
            backend.send_control.assert_called_with("show")
            app.status.configure.assert_not_called()
            App._choose_google_verification(app, False)
            backend.send_control.assert_called_with("close")
            app.status.configure.assert_called_once()

    def test_closing_browser_is_a_terminal_error_without_document_text(self):
        backend = BrowserBackend()
        backend.start = Mock()
        backend._connection = Mock()
        backend._connection.poll.return_value = True
        backend._connection.recv.return_value = {"kind": "error"}
        from pdf2zh.translator import ServiceUnavailableError

        with self.assertRaises(ServiceUnavailableError) as raised:
            backend.fetch({"q": "private words"}, Mock())
        self.assertNotIn("private words", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
