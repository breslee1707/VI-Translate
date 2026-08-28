from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from app.gui import App, LANGUAGE_NAMES, collect_pdfs, ensure_writable_streams
except ImportError:  # customtkinter and tkinterdnd2 are app-only dependencies
    collect_pdfs = None

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


@unittest.skipIf(collect_pdfs is None, "desktop app dependencies are not installed")
class OpenOutputTests(unittest.TestCase):
    def test_macos_opens_the_output_with_the_system_open_command(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("app.gui.sys.platform", "darwin"), \
                mock.patch("app.gui.subprocess.run") as run:
            target = Path(directory)
            App._open(target)
        run.assert_called_once_with(["open", str(target)], check=False)


if __name__ == "__main__":
    unittest.main()
