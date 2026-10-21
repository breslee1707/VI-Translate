from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.settings import load_settings, save_settings, settings_path


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "PDFTranslate" / "settings.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_choices_survive_a_restart(self):
        self.assertTrue(save_settings({"ui_language": "en"}, self.path))
        self.assertTrue(save_settings({"target_language": "fr", "bilingual": "layers"}, self.path))
        self.assertEqual(
            load_settings(self.path),
            {"ui_language": "en", "target_language": "fr", "bilingual": "layers"},
        )

    def test_nothing_saved_reads_as_no_choices(self):
        self.assertEqual(load_settings(self.path), {})

    def test_a_damaged_file_reads_as_no_choices(self):
        self.path.parent.mkdir(parents=True)
        for content in ("{not json", "[1, 2]", json.dumps({"ui_language": 7})):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                self.assertEqual(load_settings(self.path), {})

    def test_only_known_choices_are_kept(self):
        """OCR is deliberately not remembered: it is chosen per batch."""
        save_settings({"ocr": "enhanced", "ui_language": "en"}, self.path)
        self.assertEqual(load_settings(self.path), {"ui_language": "en"})

    def test_a_location_that_cannot_be_written_is_not_an_error(self):
        blocker = Path(self.temp.name) / "PDFTranslate"
        blocker.write_text("a file where the folder should be", encoding="utf-8")
        self.assertFalse(save_settings({"ui_language": "en"}, self.path))
        self.assertEqual(load_settings(self.path), {})

    def test_settings_live_outside_the_install_folder(self):
        """The updater swaps the install folder; the settings must survive it."""
        with mock.patch("app.settings.sys.platform", "win32"), \
                mock.patch.dict("os.environ", {"LOCALAPPDATA": "C:/Users/u/AppData/Local"}):
            self.assertEqual(
                settings_path(), Path("C:/Users/u/AppData/Local/PDFTranslate/settings.json")
            )
        with mock.patch("app.settings.sys.platform", "darwin"):
            self.assertEqual(settings_path().parts[-3:], ("Application Support", "PDFTranslate", "settings.json"))


if __name__ == "__main__":
    unittest.main()
