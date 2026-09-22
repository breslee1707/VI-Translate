from __future__ import annotations

import logging
import queue
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf

from app.errors import describe_failure
try:
    from app.gui import App, STATUS_COLORS, STATUS_MARKS, google_diagnostics
except ImportError:
    App = None
from pdf2zh.high_level import translate_stream
from pdf2zh.ocr import OCR_FONT_PATH, prepare_ocr_pdf
from pdf2zh.translator import NetworkBlock, RateLimitedError, RequestPace, ServiceUnavailableError
from scripts.translate_pdf import TranslationError, translate_pdf
from tests.test_google_batching import FakeCache, google_page, line_by_line
from tests.test_ocr import EmptyLayoutModel
from tests.test_service_outage import FakeClock, blocked, google_answer, translator_answering


class TranslationRecoveryTests(unittest.TestCase):
    def test_waiting_worker_does_not_send_after_first_worker_is_blocked(self):
        entered = threading.Event()
        waiting = threading.Event()

        class ObservedPace(RequestPace):
            @contextmanager
            def turn(self):
                if entered.is_set():
                    waiting.set()
                with super().turn():
                    yield

        def answer(_text):
            entered.set()
            self.assertTrue(waiting.wait(3))
            return blocked()

        translator, _, google = translator_answering(answer)
        translator.pace = ObservedPace(sleep=lambda _seconds: None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(translator.do_translate, "First")
            self.assertTrue(entered.wait(3))
            second = pool.submit(translator.do_translate, "Second")
            for future in (first, second):
                with self.assertRaises(RateLimitedError):
                    future.result(timeout=5)
        self.assertEqual(len(google.sent), 1)

    def test_captcha_with_http_200_stops_without_retrying(self):
        translator, _, google = translator_answering(
            lambda _text: google_answer(200, body="Our systems have detected unusual traffic")
        )
        results = translator.translate_many(["First", "Second"])
        self.assertTrue(all(isinstance(result, RateLimitedError) for result in results))
        self.assertEqual(len(google.sent), 1)

    def test_http_error_does_not_expose_text_or_retry_each_segment(self):
        translator, _, google = translator_answering(lambda _text: google_answer(403))
        results = translator.translate_many(["private words", "more private words"])
        self.assertTrue(all(isinstance(result, ServiceUnavailableError) for result in results))
        self.assertNotIn("private", str(results[0]))
        self.assertEqual(len(google.sent), 1)

    def test_single_segment_keeps_text_after_html_line_breaks(self):
        translator, _, _ = translator_answering(
            lambda _text: google_answer(200, body='<div class="result-container">One<br>Two</div>')
        )
        self.assertEqual(translator.do_translate("One\nTwo"), "One Two")

    def test_empty_or_unreadable_responses_stop_after_three_requests_for_document(self):
        for body in ("<html>Unknown service page</html>", '<div class="result-container"> </div>'):
            with self.subTest(body=body):
                translator, _, google = translator_answering(lambda _text: google_answer(200, body=body))
                answers = translator.translate_many([f"Paragraph {n}" for n in range(200)])
                self.assertTrue(all(isinstance(answer, ServiceUnavailableError) for answer in answers))
                self.assertEqual(len(google.sent), 3)

    def test_prose_about_captcha_is_not_misclassified_as_a_block(self):
        text = "Our systems have detected unusual traffic"
        translator, _, _ = translator_answering(lambda _text: google_page(text))
        self.assertEqual(translator.do_translate("A sentence about network traffic"), text)

    def test_corrupt_cached_markers_are_retranslated_and_not_cached_again(self):
        translator, _, google = translator_answering(lambda text: google_page(line_by_line(text)))
        source = "Energy <b0></b0> is conserved."
        translator.ignore_cache = False
        translator.cache = FakeCache({source: "Broken cached answer"})
        self.assertEqual(translator.translate_many([source]), [f"vi:{source}"])
        self.assertEqual(len(google.sent), 1)
        translator.cache = FakeCache()
        translator.session.get = lambda *args, **kwargs: google_page("Missing markers")
        answer = translator.translate_many([source])[0]
        self.assertIsInstance(answer, Exception)
        self.assertEqual(translator.cache.entries, {})

    def test_known_block_skips_ocr_and_pdf_engine(self):
        clock = FakeClock()
        block = NetworkBlock(clock=clock.clock)
        block.refused()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            source.write_bytes(b"%PDF-1.7\n")
            with (
                patch("pdf2zh.translator.GOOGLE_BLOCK", block),
                patch("pdf2zh.ocr.prepare_ocr_pdf") as ocr,
                patch("scripts.translate_pdf._run_engine") as core,
            ):
                with self.assertRaises(TranslationError) as raised:
                    translate_pdf(source, Path(directory) / "out", ocr="standard")
                self.assertIsInstance(raised.exception.__cause__, RateLimitedError)
            ocr.assert_not_called()
            core.assert_not_called()
            self.assertEqual(source.read_bytes(), b"%PDF-1.7\n")

    def test_text_pdf_with_ocr_enabled_does_not_load_recognizer(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            document = pymupdf.open()
            document.new_page().insert_text((30, 50), "This page already has text.")
            document.save(source)
            document.close()
            progress = []
            with patch("pdf2zh.ocr.load_ocr_engine") as load:
                prepare_ocr_pdf(
                    source, Path(directory) / "sidecar.pdf", mode="standard",
                    pages=None, layout_model=EmptyLayoutModel(),
                    on_progress=lambda *args: progress.append(args),
                )
            load.assert_not_called()
            self.assertEqual(progress, [(1, 1)])

    def test_interrupted_translation_never_replaces_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            source.write_bytes(b"%PDF-1.7\nsource")
            output = root / "out"
            output.mkdir()
            previous = output / "book-vi.pdf"
            previous.write_bytes(b"previous user output")
            with (
                patch("pdf2zh.translator.GOOGLE_BLOCK", NetworkBlock()),
                patch("scripts.translate_pdf._run_engine", side_effect=RateLimitedError("blocked")),
            ):
                with self.assertRaises(TranslationError):
                    translate_pdf(source, output, overwrite=True)
            self.assertEqual(previous.read_bytes(), b"previous user output")
            self.assertEqual(source.read_bytes(), b"%PDF-1.7\nsource")
            self.assertEqual(list(output.iterdir()), [previous])

    @unittest.skipIf(App is None, "desktop app dependencies are not installed")
    def test_queue_stops_at_service_failure_and_keeps_remaining_files_pending(self):
        for error in (RateLimitedError("blocked"), ServiceUnavailableError("unavailable")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                paths = [Path(directory) / f"{number}.pdf" for number in range(3)]
                app = SimpleNamespace(events=queue.Queue(), failures={}, _log_failure=Mock(return_value=None))
                with patch("app.gui.translate_pdf", side_effect=error) as translate:
                    App._run(app, paths, "vi", False, "off")
                self.assertEqual(translate.call_count, 1)
                events = list(app.events.queue)
                self.assertEqual(events[-1], ("finished",))
                self.assertTrue(any(event[:3] == ("status", paths[0], "paused") for event in events))
                self.assertFalse(any(len(event) > 1 and event[1] in paths[1:] for event in events))

    def test_block_classification_survives_ocr_filename_in_exception_chain(self):
        error = RuntimeError("Failed to translate OCR scan.pdf")
        error.__cause__ = RateLimitedError("blocked")
        self.assertEqual(describe_failure(error).code, "E-NET-08")

    @unittest.skipIf(App is None, "desktop app dependencies are not installed")
    def test_paused_ui_offers_resume_and_preserves_queue_count(self):
        paths = [Path("first.pdf"), Path("second.pdf")]
        app = SimpleNamespace(
            events=queue.Queue(), states={paths[0]: "paused", paths[1]: "queued"},
            files=paths, translate_button=Mock(), clear_button=Mock(),
            status=Mock(), _go_determinate=Mock(),
        )
        app.events.put(("finished",))
        App._handle_events(app)
        app.translate_button.configure.assert_called_with(text="Tiếp tục dịch")
        summary = app.status.configure.call_args.kwargs["text"]
        self.assertIn("1 file đang chờ", summary)
        self.assertNotIn("Xong", summary)
        self.assertIn("paused", STATUS_MARKS)
        self.assertIn("paused", STATUS_COLORS)

    @unittest.skipIf(App is None, "desktop app dependencies are not installed")
    def test_counters_log_excludes_document_text(self):
        translator, _, _ = translator_answering(lambda text: google_page(line_by_line(text)))
        with tempfile.TemporaryDirectory() as directory:
            with google_diagnostics(Path(directory)):
                translator.translate_many(["confidential sentence", "another sentence"])
                logging.getLogger("pdf2zh.translator").warning("private content")
            text = (Path(directory) / "translation-service.log").read_text(encoding="utf-8")
            self.assertIn("'requests': 1", text)
            self.assertIn("'batch_hits': 1", text)
            self.assertNotIn("confidential", text)
            self.assertNotIn("private content", text)

    def test_pdf_stops_before_next_page_and_resume_reuses_successful_cache(self):
        document = pymupdf.open()
        for words in ("The library opens in the morning.", "The experiment was repeated three times.",
                      "Please save the document before closing."):
            page = document.new_page(width=300, height=200)
            page.insert_text((20, 50), words, fontsize=11)
        source = document.tobytes()
        document.close()
        cache = FakeCache()
        calls = []

        def answer(text):
            calls.append(text)
            return google_page(line_by_line(text)) if len(calls) == 1 else blocked()

        translator, _, _ = translator_answering(answer)
        translator.ignore_cache = False
        translator.cache = cache
        model = EmptyLayoutModel()
        model.predict = Mock(wraps=model.predict)
        progress = []
        options = dict(lang_in="en", lang_out="vi", service="google", thread=1,
                       model=model, create_dual=False,
                       callback=lambda bar: progress.append(bar.n))
        with (
            patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)),
            patch("pdf2zh.high_level.output_style_font_paths", return_value={0: str(OCR_FONT_PATH)}),
            patch.dict("pdf2zh.converter.ENGINES", {"google": lambda *args, **kwargs: translator}),
        ):
            with self.assertRaises(RateLimitedError):
                translate_stream(source, **options)
        self.assertEqual(len(calls), 2)
        self.assertEqual(model.predict.call_count, 2)
        self.assertEqual(max(progress), 1)
        self.assertEqual(len(cache.entries), 1)
        recovered, _, google = translator_answering(lambda text: google_page(line_by_line(text)))
        recovered.ignore_cache = False
        recovered.cache = cache
        stages = []
        with (
            patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)),
            patch("pdf2zh.high_level.output_style_font_paths", return_value={0: str(OCR_FONT_PATH)}),
            patch.dict("pdf2zh.converter.ENGINES", {"google": lambda *args, **kwargs: recovered}),
        ):
            mono, _, report = translate_stream(source, on_status=lambda *args: stages.append(args), **options)
        self.assertEqual(len(google.sent), 2)
        self.assertEqual(report.failures, [])
        self.assertEqual(progress[-1], 3)
        self.assertIn(("saving", 0, 0), stages)
        self.assertIn(("request", 0, 0), stages)
        with pymupdf.open(stream=mono) as result:
            self.assertEqual(len(result), 3)
            self.assertIn("vi:", result[0].get_text())
            self.assertIn("vi:", result[1].get_text())


if __name__ == "__main__":
    unittest.main()
