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
        LANGUAGE_NAMES,
        collect_pdfs,
        ensure_writable_streams,
        main,
        verify_engine,
    )
except ImportError:  # customtkinter and tkinterdnd2 are app-only dependencies
    App = None
    collect_pdfs = None
    main = None
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
