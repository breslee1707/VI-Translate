from __future__ import annotations

import io
import queue
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    from app.gui import (
        App,
        DEFAULT_BILINGUAL,
        DEFAULT_OCR_MODE,
        LANGUAGE_NAMES,
        STAGES,
        bilingual_names,
        collect_pdfs,
        elide,
        ensure_writable_streams,
        file_summary,
        human_size,
        main,
        missing_runtime_modules,
        ocr_names,
        page_count,
        primary_action,
        runnable,
        verify_engine,
        translation_outcome,
    )
except ImportError:  # customtkinter and tkinterdnd2 are app-only dependencies
    App = None
    collect_pdfs = None
    ocr_names = None
    main = None
    missing_runtime_modules = None
    verify_engine = None

from app.update import (
    REQUIRED_PAYLOAD,
    STAGED_MARKER,
    Release,
    staged_payload,
    staging_directory,
)
from scripts.translate_pdf import TARGET_LANGUAGES


@unittest.skipIf(collect_pdfs is None, "desktop app dependencies are not installed")
class CollectPdfsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        for name in ("b.pdf", "a.pdf", "UPPER.PDF"):
            (self.root / name).write_bytes(b"%PDF-1.7\n")
        (self.root / "notes.txt").write_text("not a pdf", encoding="utf-8")
        (self.root / "folder.pdf").mkdir()
        nested = self.root / "sub"
        nested.mkdir()
        (nested / "deep.pdf").write_bytes(b"%PDF-1.7\n")

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_a_directory_expands_to_its_pdfs_without_recursing(self):
        names = [path.name for path in collect_pdfs([self.root])]
        self.assertEqual(names, ["a.pdf", "b.pdf", "UPPER.PDF"])

    def test_a_directory_named_like_a_pdf_is_not_queued(self):
        self.assertNotIn("folder.pdf", [path.name for path in collect_pdfs([self.root])])

    def test_non_pdf_files_are_dropped(self):
        self.assertEqual(collect_pdfs([self.root / "notes.txt"]), [])

    def test_duplicates_collapse_across_a_directory_and_an_explicit_file(self):
        result = collect_pdfs([self.root, self.root / "a.pdf"])
        self.assertEqual(len(result), 3)
        self.assertEqual(len(set(result)), 3)

    def test_a_vanished_file_stays_queued_so_the_runner_can_report_it(self):
        names = [path.name for path in collect_pdfs([self.root / "gone.pdf"])]
        self.assertEqual(names, ["gone.pdf"])


@unittest.skipIf(collect_pdfs is None, "desktop app dependencies are not installed")
class LanguageMenuTests(unittest.TestCase):
    def test_every_supported_language_has_a_menu_label(self):
        self.assertEqual(set(LANGUAGE_NAMES), set(TARGET_LANGUAGES))

    def test_menu_labels_are_unique_so_the_reverse_lookup_is_total(self):
        self.assertEqual(len(set(LANGUAGE_NAMES.values())), len(LANGUAGE_NAMES))

    def test_every_ocr_mode_and_bilingual_layout_has_a_unique_label_in_each_language(self):
        """The menus map a label back to its mode, so labels must be unique."""
        from app.i18n import UI_LANGUAGES, set_ui_language
        from scripts.translate_pdf import BILINGUAL_MODES, OCR_MODES

        try:
            for code in UI_LANGUAGES:
                set_ui_language(code)
                for names, modes in ((ocr_names(), OCR_MODES), (bilingual_names(), BILINGUAL_MODES)):
                    with self.subTest(language=code, modes=modes):
                        self.assertEqual(set(names), set(modes))
                        self.assertEqual(len(set(names.values())), len(names))
        finally:
            set_ui_language(None)

    def test_ocr_and_the_bilingual_copy_stay_off_until_the_user_turns_them_on(self):
        """A scanned book read by OCR is thousands of Google requests, and slow;
        a bilingual copy of a textbook is a second file as large again."""
        self.assertEqual(DEFAULT_OCR_MODE, "off")
        self.assertIn(DEFAULT_OCR_MODE, ocr_names())
        self.assertEqual(DEFAULT_BILINGUAL, "off")

    def test_every_stage_the_runner_reports_has_a_label(self):
        from app.i18n import CATALOGS

        for code, catalog in CATALOGS.items():
            for stage in STAGES:
                with self.subTest(language=code, stage=stage):
                    self.assertIn(f"stage.{stage}", catalog)


@unittest.skipIf(App is None, "desktop app dependencies are not installed")
class TranslationOutcomeTests(unittest.TestCase):
    @staticmethod
    def result(**overrides):
        values = {
            "untranslated": 0,
            "reasons": {},
            "image_only_pages": (),
            "ocr_warnings": (),
        }
        values.update(overrides)
        return types.SimpleNamespace(**values)

    def test_clean_ocr_result_is_done(self):
        self.assertEqual(translation_outcome(self.result()), ("done", ""))

    def test_a_blocked_network_is_named_so_the_user_waits_instead_of_retrying(self):
        state, detail = translation_outcome(
            self.result(untranslated=40, reasons={"RateLimitedError": 38, "ConnectionError": 2})
        )
        self.assertEqual(state, "partial")
        self.assertIn("Hoàn thành — còn 40 đoạn chưa dịch", detail)
        self.assertIn("Google tạm chặn", detail)
        self.assertNotIn("kiểm tra mạng", detail)

    def test_a_dead_connection_is_told_apart_from_a_block(self):
        _, detail = translation_outcome(
            self.result(untranslated=3, reasons={"ServiceUnavailableError": 3})
        )
        self.assertIn("kiểm tra mạng", detail)
        self.assertNotIn("Google tạm chặn", detail)

    def test_a_segment_that_did_not_fit_gets_no_network_advice(self):
        _, detail = translation_outcome(
            self.result(untranslated=1, reasons={"single line needs less than 50% font size": 1})
        )
        self.assertEqual(detail, "Hoàn thành — còn 1 đoạn chưa dịch")

    def test_preserved_scan_is_never_reported_as_done(self):
        state, detail = translation_outcome(self.result(image_only_pages=(0, 4)))
        self.assertEqual(state, "partial")
        self.assertIn("1, 5", detail)

    def test_ocr_safety_warning_is_visible_as_partial(self):
        state, detail = translation_outcome(
            self.result(ocr_warnings=("page 2: preserved (form grid)",))
        )
        self.assertEqual(state, "partial")
        self.assertIn("OCR", detail)

    def test_worker_passes_selected_ocr_mode_and_reports_safety_partial(self):
        source = Path("scan.pdf")
        result = types.SimpleNamespace(
            path=Path("translated/scan-vi.pdf"), untranslated=0, reasons={},
            image_only_pages=(), ocr_warnings=("page 1: preserved (form grid)",),
        )
        app = types.SimpleNamespace(events=queue.Queue(), failures={})
        with mock.patch("app.gui.translate_pdf", return_value=result) as translate:
            App._run(app, [source], "vi", False, "standard")
        self.assertEqual(translate.call_args.kwargs["ocr"], "standard")
        events = []
        while not app.events.empty():
            events.append(app.events.get_nowait())
        status = next(event for event in events if event[0] == "status" and event[2] != "running")
        self.assertEqual(status[2], "partial")
        self.assertIn("OCR", status[3])

    def test_completed_pdf_with_one_untranslated_segment_counts_as_finished(self):
        source = Path("book.pdf")
        output = Path("translated/book-vi.pdf")
        app = types.SimpleNamespace(
            events=queue.Queue(), states={source: "running"}, files=[source],
            outputs={}, failures={}, rows={source: mock.Mock()}, status=mock.Mock(),
            _go_determinate=mock.Mock(), _show_output_link=mock.Mock(), _set_busy=mock.Mock(),
        )
        state, detail = translation_outcome(self.result(untranslated=1))
        app.events.put(("status", source, state, detail, output))
        app.events.put(("finished",))
        App._handle_events(app)
        self.assertEqual(app.outputs[source], output)
        self.assertEqual(app.states[source], "partial")
        app.rows[source].set_state.assert_called_with("partial", "", "Hoàn thành — còn 1 đoạn chưa dịch")
        app.rows[source].show_actions.assert_called_once_with()
        app._set_busy.assert_called_once_with(False)
        app.status.configure.assert_called_with(text="Hoàn thành 1/1 file · 1 file còn phần chưa dịch")

    def test_failed_pdf_is_not_counted_with_completed_partial_pdfs(self):
        paths = [Path("done.pdf"), Path("partial.pdf"), Path("failed.pdf")]
        app = types.SimpleNamespace(
            events=queue.Queue(), states=dict(zip(paths, ("done", "partial", "failed"))), files=paths,
            status=mock.Mock(), _go_determinate=mock.Mock(), _set_busy=mock.Mock(),
        )
        app.events.put(("finished",))
        App._handle_events(app)
        summary = app.status.configure.call_args.kwargs["text"]
        self.assertIn("Hoàn thành 2/3 file", summary)
        self.assertIn("1 file còn phần chưa dịch", summary)
        self.assertIn("1 file lỗi", summary)

    def test_captcha_completion_continues_the_current_file_and_then_the_next(self):
        paths = [Path("first.pdf"), Path("second.pdf")]
        app = types.SimpleNamespace(events=queue.Queue(), failures={})

        def translated(path, _destination, **options):
            if path == paths[0]:
                options["on_status"]("verification", 0, 0)
                options["on_status"]("verified", 0, 0)
            return types.SimpleNamespace(path=path.with_suffix(".translated.pdf"),
                                         untranslated=0, reasons={}, image_only_pages=(), ocr_warnings=())

        with mock.patch("app.gui.translate_pdf", side_effect=translated):
            App._run(app, paths, "vi", False, "off", True)
        events = list(app.events.queue)
        verified = events.index(("stage", paths[0], "verified", 0, 0))
        first_done = next(index for index, event in enumerate(events) if event[:3] == ("status", paths[0], "done"))
        next_running = events.index(("status", paths[1], "running", "", None))
        self.assertLess(verified, first_done)
        self.assertLess(first_done, next_running)
        self.assertFalse(app.failures)
        self.assertFalse(any(event[0] == "status" and event[2] == "paused" for event in events))

    def test_a_bilingual_copy_is_reported_before_its_row_turns_done(self):
        """The row offers "Song ngữ" only if it knows the copy exists when it
        draws its buttons, which it does on the status event."""
        source, dual = Path("book.pdf"), Path("translated/book-vi-dual.pdf")
        result = types.SimpleNamespace(
            path=Path("translated/book-vi.pdf"), untranslated=0, reasons={},
            image_only_pages=(), ocr_warnings=(), bilingual=dual,
        )
        app = types.SimpleNamespace(events=queue.Queue(), failures={})
        with mock.patch("app.gui.translate_pdf", return_value=result):
            App._run(app, [source], "vi", False, "off", bilingual="side-by-side")
        events = list(app.events.queue)
        copy = events.index(("bilingual", source, dual))
        done = next(index for index, event in enumerate(events) if event[:3] == ("status", source, "done"))
        self.assertLess(copy, done)


@unittest.skipIf(App is None, "desktop app dependencies are not installed")
class PrimaryActionTests(unittest.TestCase):
    """The main button names what it will do, and is off when it would do nothing."""

    def test_an_empty_queue_leaves_nothing_to_press(self):
        self.assertEqual(primary_action([]), ("Dịch", False))

    def test_the_button_counts_the_files_it_will_translate(self):
        self.assertEqual(primary_action(["queued"]), ("Dịch 1 file", True))
        self.assertEqual(primary_action(["queued", "failed", "done", "partial"]), ("Dịch 2 file", True))

    def test_a_finished_queue_has_nothing_left(self):
        self.assertEqual(primary_action(["done", "partial", "skipped"]), ("Dịch", False))

    def test_overwrite_brings_a_skipped_file_back(self):
        """E-OUT-05 tells the user to tick overwrite; the file must then be translatable."""
        self.assertFalse(runnable("skipped"))
        self.assertTrue(runnable("skipped", overwrite=True))
        self.assertEqual(primary_action(["done", "skipped"], overwrite=True), ("Dịch 1 file", True))

    def test_a_paused_batch_offers_to_continue(self):
        self.assertEqual(primary_action(["done", "paused", "queued"]), ("Tiếp tục dịch", True))

    def test_finished_files_are_never_translated_again(self):
        for state in ("done", "partial", "running"):
            with self.subTest(state=state):
                self.assertFalse(runnable(state, overwrite=True))

    def test_the_label_follows_the_interface_language(self):
        from app.i18n import set_ui_language

        set_ui_language("en")
        try:
            self.assertEqual(primary_action(["queued", "queued"]), ("Translate 2 files", True))
        finally:
            set_ui_language(None)


@unittest.skipIf(App is None, "desktop app dependencies are not installed")
class QueueTextTests(unittest.TestCase):
    class Font:
        """One pixel per character, like a font whose every glyph is as wide."""

        @staticmethod
        def measure(text: str) -> int:
            return len(text)

    def tearDown(self) -> None:
        from app.i18n import set_ui_language

        set_ui_language(None)

    def test_sizes_read_the_way_people_write_them(self):
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(2048), "2 KB")
        self.assertEqual(human_size(1_258_291), "1,2 MB")
        self.assertEqual(human_size(3 * 1024 ** 2), "3 MB")
        self.assertEqual(human_size(150 * 1024 ** 2), "150 MB")
        self.assertEqual(human_size(5 * 1024 ** 3), "5 GB")

    def test_the_decimal_mark_follows_the_interface_language(self):
        from app.i18n import set_ui_language

        set_ui_language("en")
        self.assertEqual(human_size(1_258_291), "1.2 MB")
        self.assertEqual(file_summary(1, 2048), "1 page · 2 KB")

    def test_a_summary_leaves_out_what_could_not_be_read(self):
        self.assertEqual(file_summary(23, 1_258_291), "23 trang · 1,2 MB")
        self.assertEqual(file_summary(0, 2048), "2 KB")
        self.assertEqual(file_summary(0, 0), "")

    def test_a_name_that_fits_is_left_alone(self):
        self.assertEqual(elide("book.pdf", self.Font, 40), "book.pdf")

    def test_a_long_name_loses_its_middle_and_keeps_its_end(self):
        name = "ESP-IDF Programming Guide v5.3.2 final.pdf"
        short = elide(name, self.Font, 24)
        self.assertLessEqual(len(short), 24)
        self.assertTrue(short.startswith("ESP-IDF"))
        self.assertTrue(short.endswith("final.pdf"))
        self.assertIn("…", short)

    def test_no_room_still_leaves_the_end_of_the_name(self):
        self.assertTrue(elide("abcdefghijklmnopqrstuvwxyz.pdf", self.Font, 3).endswith(".pdf"))

    def test_page_count_reads_the_catalog_and_shrugs_at_anything_else(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "three.pdf"
            document = pymupdf.open()
            for _ in range(3):
                document.new_page()
            document.save(pdf)
            document.close()
            text = Path(directory) / "notes.pdf"
            text.write_text("not a pdf", encoding="utf-8")
            self.assertEqual(page_count(pdf), 3)
            self.assertEqual(page_count(text), 0)
            self.assertEqual(page_count(Path(directory) / "gone.pdf"), 0)


@unittest.skipIf(collect_pdfs is None, "desktop app dependencies are not installed")
class WritableStreamTests(unittest.TestCase):
    """A windowed PyInstaller build sets sys.stdout and sys.stderr to None, and
    the core's tqdm progress bar writes to stderr. Without this, translating in
    the packaged app died with "'NoneType' object has no attribute 'write'"."""

    def setUp(self) -> None:
        self.saved = {name: getattr(sys, name) for name in
                      ("stdout", "stderr", "__stdout__", "__stderr__")}

    def tearDown(self) -> None:
        for name, stream in self.saved.items():
            setattr(sys, name, stream)

    def test_replaces_streams_that_a_windowed_build_leaves_as_none(self):
        sys.stdout = sys.stderr = None
        ensure_writable_streams()
        for name in ("stdout", "stderr"):
            with self.subTest(stream=name):
                stream = getattr(sys, name)
                self.assertIsNotNone(stream)
                stream.write("tqdm writes here")  # must not raise

    def test_leaves_a_working_stream_alone(self):
        marker = io.StringIO()
        sys.stdout = marker
        ensure_writable_streams()
        self.assertIs(sys.stdout, marker)


@unittest.skipIf(App is None, "desktop app dependencies are not installed")
class OpenResultTests(unittest.TestCase):
    def test_macos_uses_the_native_open_command(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with (
                mock.patch("app.gui.sys.platform", "darwin"),
                mock.patch("app.gui.subprocess.Popen") as launch,
            ):
                App._open(target)

        launch.assert_called_once_with(
            ["open", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def test_revealing_a_result_selects_it_in_explorer(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "book with spaces-vi.pdf"
            target.write_bytes(b"%PDF-1.7\n")
            with (
                mock.patch("app.gui.sys.platform", "win32"),
                mock.patch("app.gui.subprocess.Popen") as launch,
            ):
                App._reveal(target)
        launch.assert_called_once_with(f'explorer /select,"{target}"')

    def test_revealing_a_result_selects_it_in_finder(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "book-vi.pdf"
            target.write_bytes(b"%PDF-1.7\n")
            with (
                mock.patch("app.gui.sys.platform", "darwin"),
                mock.patch("app.gui.subprocess.Popen") as launch,
            ):
                App._reveal(target)
        self.assertEqual(launch.call_args.args[0], ["open", "-R", str(target)])

    def test_a_vanished_result_is_not_revealed(self):
        with mock.patch("app.gui.subprocess.Popen") as launch:
            App._reveal(Path("gone/book-vi.pdf"))
        launch.assert_not_called()


@unittest.skipIf(App is None, "desktop app dependencies are not installed")
class UpdateFlowTests(unittest.TestCase):
    """What the update thread decides, driven through App's own method.

    Constructing App needs a real window; the flow it runs on a daemon thread
    needs only an event queue, so it is given one and nothing else.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.install = Path(self.temp.name) / "PDFTranslate"
        self.install.mkdir()
        self.app = types.SimpleNamespace(events=queue.Queue(), update_percent=-1)
        self.app._report_download = lambda *_arguments: None
        self.release = Release("v9.0.0", "https://example/windows.zip", 1024)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _stage_payload(self, tag: str) -> None:
        payload = staged_payload(self.install)
        payload.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_PAYLOAD:
            (payload / name).parent.mkdir(parents=True, exist_ok=True)
            (payload / name).write_bytes(b"payload")
        (staging_directory(self.install) / STAGED_MARKER).write_text(tag, encoding="utf-8")

    def _run(self, **patches):
        with mock.patch("app.gui.install_directory", return_value=self.install), \
             mock.patch("app.gui.can_self_update", return_value=True), \
             mock.patch.multiple("app.gui", **patches):
            App._check_for_update(self.app)
        events = []
        while not self.app.events.empty():
            events.append(self.app.events.get_nowait())
        return events

    def test_a_build_that_cannot_swap_itself_only_gets_the_link(self):
        """Running from source, or on a Mac: there is no folder to replace."""
        with mock.patch("app.gui.install_directory", return_value=None), \
             mock.patch("app.gui.check_for_update", return_value="v9.0.0"):
            App._check_for_update(self.app)
        self.assertEqual(self.app.events.get_nowait(), ("update", "v9.0.0"))

    def test_a_build_staged_earlier_is_offered_without_downloading_again(self):
        self._stage_payload("v9.0.0")
        network = mock.Mock()
        events = self._run(latest_release=network)
        self.assertEqual(events, [("update_ready", "v9.0.0")])
        network.assert_not_called()

    def test_a_release_with_no_windows_asset_falls_back_to_the_link(self):
        events = self._run(latest_release=mock.Mock(return_value=Release("v9.0.0", "", 0)))
        self.assertEqual(events, [("update", "v9.0.0")])

    def test_a_disk_with_no_room_falls_back_to_the_link(self):
        events = self._run(
            latest_release=mock.Mock(return_value=self.release),
            has_room_for=mock.Mock(return_value=False),
        )
        self.assertEqual(events, [("update", "v9.0.0")])

    def test_a_staged_download_ends_in_the_restart_offer(self):
        events = self._run(
            latest_release=mock.Mock(return_value=self.release),
            stage_update=mock.Mock(return_value=staged_payload(self.install)),
        )
        self.assertEqual(
            events, [("update_downloading", "v9.0.0"), ("update_ready", "v9.0.0")]
        )

    def test_a_failed_download_leaves_the_link_and_no_staging(self):
        self._stage_payload("v0.0.1")  # older than this build, so it is discarded
        events = self._run(
            latest_release=mock.Mock(return_value=self.release),
            stage_update=mock.Mock(side_effect=OSError("connection reset")),
        )
        self.assertEqual(events, [("update_downloading", "v9.0.0"), ("update", "v9.0.0")])
        self.assertFalse(staging_directory(self.install).exists())


@unittest.skipIf(App is None, "desktop app dependencies are not installed")
class InstallUpdateTests(unittest.TestCase):
    def _app(self, translating: bool):
        worker = mock.Mock()
        worker.is_alive.return_value = translating
        return types.SimpleNamespace(
            worker=worker,
            status=mock.Mock(),
            update_tag="v9.0.0",
            destroy=mock.Mock(),
            _confirm_install=mock.Mock(return_value=True),
            _show_update_link=mock.Mock(),
        )

    def test_a_running_batch_is_never_interrupted_by_a_restart(self):
        app = self._app(translating=True)
        with mock.patch("app.gui.launch_updater") as launch:
            App._install_update(app)
        launch.assert_not_called()
        app.destroy.assert_not_called()
        app.status.configure.assert_called_once()

    def test_declining_the_dialog_changes_nothing(self):
        app = self._app(translating=False)
        app._confirm_install.return_value = False
        with mock.patch("app.gui.install_directory", return_value=Path("C:/app")), \
             mock.patch("app.gui.launch_updater") as launch:
            App._install_update(app)
        launch.assert_not_called()
        app.destroy.assert_not_called()

    def test_accepting_starts_the_helper_and_closes_the_app(self):
        app = self._app(translating=False)
        install = Path("C:/app/PDFTranslate")
        with mock.patch("app.gui.install_directory", return_value=install), \
             mock.patch("app.gui.launch_updater") as launch:
            App._install_update(app)
        launch.assert_called_once_with(install)
        app.destroy.assert_called_once_with()

    def test_a_helper_that_will_not_start_leaves_a_working_app(self):
        app = self._app(translating=False)
        with mock.patch("app.gui.install_directory", return_value=Path("C:/app")), \
             mock.patch("app.gui.launch_updater", side_effect=OSError("blocked")):
            App._install_update(app)
        app.destroy.assert_not_called()
        app._show_update_link.assert_called_once()
        self.assertEqual(app._show_update_link.call_args.args[1], "browser")


@unittest.skipIf(App is None, "desktop app dependencies are not installed")
class RuntimeHealthTests(unittest.TestCase):
    """#29: antivirus quarantined one file out of the bundle, and the user only
    learnt of it as E-CORE-01 on their first document."""

    def test_this_environment_has_everything_a_translation_imports(self):
        self.assertEqual(missing_runtime_modules(), [])

    def test_the_quarantined_file_is_named_rather_than_the_module_that_needed_it(self):
        error = ModuleNotFoundError("No module named '_multiprocessing'", name="_multiprocessing")
        with mock.patch("app.gui.importlib.import_module", side_effect=error):
            self.assertEqual(
                missing_runtime_modules(("multiprocessing.connection", "pdf2zh.google_browser")),
                ["_multiprocessing"],
            )

    def test_a_native_library_that_refuses_to_load_counts_as_missing(self):
        with mock.patch("app.gui.importlib.import_module", side_effect=OSError("DLL load failed")):
            self.assertEqual(missing_runtime_modules(("pdf2zh.ocr",)), ["pdf2zh.ocr"])

    def test_a_damaged_install_is_announced_before_the_first_document(self):
        app = types.SimpleNamespace(events=queue.Queue())
        with mock.patch("app.gui.preload_layout_model"), \
             mock.patch("app.gui.missing_runtime_modules", return_value=["_multiprocessing"]):
            App._prepare_engine(app)
        self.assertEqual(app.events.get_nowait(), ("health", ["_multiprocessing"]))

    def test_a_complete_install_stays_quiet(self):
        app = types.SimpleNamespace(events=queue.Queue())
        with mock.patch("app.gui.preload_layout_model"), \
             mock.patch("app.gui.missing_runtime_modules", return_value=[]):
            App._prepare_engine(app)
        self.assertTrue(app.events.empty())

    def test_the_smoke_test_fails_on_a_missing_module(self):
        with mock.patch("app.gui.missing_runtime_modules", return_value=["_multiprocessing"]), \
             mock.patch("app.gui.load_layout_model") as load:
            with self.assertRaisesRegex(ImportError, "_multiprocessing"):
                verify_engine()
        load.assert_not_called()


@unittest.skipIf(main is None, "desktop app dependencies are not installed")
class PackagedSmokeTestTests(unittest.TestCase):
    def test_smoke_test_loads_and_closes_the_app_without_entering_mainloop(self):
        fake_app = mock.Mock()
        with (
            mock.patch("app.gui.sys.argv", ["PDFTranslate", "--smoke-test"]),
            mock.patch("app.gui.ensure_writable_streams"),
            mock.patch("app.gui.use_bundled_assets"),
            mock.patch("app.gui.ctk.set_appearance_mode"),
            mock.patch("app.gui.ctk.set_default_color_theme"),
            mock.patch("app.gui.App", return_value=fake_app),
            mock.patch("app.gui.verify_engine") as verify,
        ):
            main()

        fake_app.withdraw.assert_called_once_with()
        fake_app.update_idletasks.assert_called_once_with()
        verify.assert_called_once_with()
        fake_app.destroy.assert_called_once_with()
        fake_app.mainloop.assert_not_called()

    def test_the_smoke_test_exits_nonzero_when_the_native_stack_is_broken(self):
        """A packaged build that cannot load pikepdf or onnx must not exit 0.

        It must also exit rather than raise: a windowed build turns an escaping
        exception into a modal traceback dialog, which on a build machine hangs
        the job instead of failing it.
        """
        fake_app = mock.Mock()
        with (
            mock.patch("app.gui.sys.argv", ["PDFTranslate", "--smoke-test"]),
            mock.patch("app.gui.ensure_writable_streams"),
            mock.patch("app.gui.use_bundled_assets"),
            mock.patch("app.gui.ctk.set_appearance_mode"),
            mock.patch("app.gui.ctk.set_default_color_theme"),
            mock.patch("app.gui.App", return_value=fake_app),
            mock.patch("app.gui.verify_engine", side_effect=ImportError("no qpdf")),
        ):
            with self.assertRaises(SystemExit) as exit_status:
                main()

        self.assertEqual(exit_status.exception.code, 1)
        fake_app.destroy.assert_called_once_with()
        fake_app.mainloop.assert_not_called()

    def test_verify_engine_loads_the_real_native_stack(self):
        """Not mocked: this is the check itself, run against this environment.

        Building the session fills a process-wide cache that other tests expect
        to be cold so they can substitute their own model, so it is put back
        exactly as it was found.
        """
        from scripts import translate_pdf

        cached = dict(translate_pdf._LAYOUT_MODEL)
        try:
            verify_engine()
        finally:
            translate_pdf._LAYOUT_MODEL.clear()
            translate_pdf._LAYOUT_MODEL.update(cached)


if __name__ == "__main__":
    unittest.main()
