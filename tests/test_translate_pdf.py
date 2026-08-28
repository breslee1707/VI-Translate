from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import translate_pdf


class TranslatePdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        # macOS exposes /var as a symlink to /private/var. Match production,
        # which resolves paths before returning them.
        self.root = Path(self.temp_directory.name).resolve()
        self.source = self.root / "guide.pdf"
        self.source.write_bytes(b"%PDF-1.7\nsource")
        self.output = self.root / "output"

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    @staticmethod
    def _engine_side_effect(source, temp_output, *_args):
        (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(
            b"%PDF-1.7\ntranslated"
        )
        return 0  # the real runner returns the untranslated segment count

    @mock.patch.object(translate_pdf, "_require_core")
    @mock.patch.object(translate_pdf, "_run_engine")
    def test_copies_the_core_result_to_a_stable_vietnamese_name(self, run, _core):
        run.side_effect = self._engine_side_effect

        result = translate_pdf.translate_pdf(self.source, self.output)

        self.assertEqual(result.path, self.output / "guide-vi.pdf")
        self.assertEqual(result.untranslated, 0)
        self.assertEqual(result.path.read_bytes(), b"%PDF-1.7\ntranslated")
        run.assert_called_once_with(
            self.source, mock.ANY, "vi", "auto", None, translate_pdf.DEFAULT_THREADS, False, "google", {}, None
        )

    @mock.patch.object(translate_pdf, "_require_core")
    @mock.patch.object(translate_pdf, "_run_engine")
    def test_refuses_to_replace_output_without_authorization(self, run, _core):
        self.output.mkdir()
        existing = self.output / "guide-vi.pdf"
        existing.write_bytes(b"existing")

        with self.assertRaisesRegex(translate_pdf.TranslationError, "already exists"):
            translate_pdf.translate_pdf(self.source, self.output)

        run.assert_not_called()
        self.assertEqual(existing.read_bytes(), b"existing")

    @mock.patch.object(translate_pdf, "_require_core")
    def test_rejects_a_non_pdf_payload(self, _core):
        invalid = self.root / "fake.pdf"
        invalid.write_text("not a PDF", encoding="utf-8")

        with self.assertRaisesRegex(translate_pdf.TranslationError, "PDF header"):
            translate_pdf.translate_pdf(invalid, self.output)

    def test_validates_and_converts_page_ranges(self):
        self.assertEqual(translate_pdf._page_selection("1,3-5"), "1,3-5")
        self.assertEqual(translate_pdf._pages_to_indices("1,3-5"), [0, 2, 3, 4])
        with self.assertRaises(argparse.ArgumentTypeError):
            translate_pdf._page_selection("5-3")

    def test_requires_the_bundled_core_instead_of_the_pypi_wheel(self):
        translate_pdf._require_core()

        import pdf2zh

        self.assertTrue(
            Path(pdf2zh.__file__).resolve().is_relative_to(translate_pdf.BUNDLED_CORE)
        )

    def test_engine_contract_forwards_language_engine_and_handoff_files(self):
        fake_model = object()
        with (
            mock.patch(
                "pdf2zh.doclayout.OnnxModel.load_available",
                return_value=fake_model,
            ),
            mock.patch(
                "pdf2zh.high_level.translate",
                return_value=[("translated.pdf", "")],
            ) as core_translate,
        ):
            translate_pdf._run_engine(
                self.source,
                self.output,
                "fr",
                "en",
                "2-3",
                1,
                True,
                "handoff",
                {"segments_in": "table.jsonl"},
            )

        core_translate.assert_called_once_with(
            files=[str(self.source)],
            output=str(self.output),
            pages=[1, 2],
            lang_in="en",
            lang_out="fr",
            service="handoff",
            thread=1,
            model=fake_model,
            envs={"segments_in": "table.jsonl"},
            callback=None,
            ignore_cache=True,
        )

    def test_reports_segments_the_engine_could_not_translate(self):
        def partial(source, temp_output, *_args):
            (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(b"%PDF-1.7\n")
            return 7

        with (
            mock.patch.object(translate_pdf, "_require_core"),
            mock.patch.object(translate_pdf, "_run_engine", side_effect=partial),
        ):
            result = translate_pdf.translate_pdf(self.source, self.output)

        # A document that lost some segments must still be delivered, and say so.
        self.assertEqual(result.path, self.output / "guide-vi.pdf")
        self.assertEqual(result.untranslated, 7)

    def test_target_language_is_limited_to_latin_script(self):
        self.assertEqual(translate_pdf._target_language("FR"), "fr")
        for rejected in ("zh", "ja", "ko", "ar", "he", "th", "hi"):
            with self.subTest(language=rejected):
                with self.assertRaises(argparse.ArgumentTypeError):
                    translate_pdf._target_language(rejected)

    def test_output_name_follows_the_target_language(self):
        with (
            mock.patch.object(translate_pdf, "_require_core"),
            mock.patch.object(translate_pdf, "_run_engine") as run,
        ):
            run.side_effect = self._engine_side_effect
            result = translate_pdf.translate_pdf(
                self.source, self.output, target_language="fr"
            )
        self.assertEqual(result.path, self.output / "guide-fr.pdf")

    def test_handoff_flags_are_rejected_for_the_google_engine(self):
        args = translate_pdf._parser().parse_args(
            [str(self.source), "--output-dir", str(self.output), "--segments", "t.jsonl"]
        )
        with self.assertRaisesRegex(translate_pdf.TranslationError, "require --engine handoff"):
            translate_pdf._validate_arguments(args)

    def test_handoff_engine_requires_a_segments_file(self):
        args = translate_pdf._parser().parse_args(
            [str(self.source), "--output-dir", str(self.output), "--engine", "handoff"]
        )
        with self.assertRaisesRegex(translate_pdf.TranslationError, "needs --segments"):
            translate_pdf._validate_arguments(args)

    def test_output_dir_is_optional_only_when_emitting_segments(self):
        emit = translate_pdf._parser().parse_args(
            [str(self.source), "--engine", "handoff", "--emit-segments", "m.jsonl"]
        )
        translate_pdf._validate_arguments(emit)

        bare = translate_pdf._parser().parse_args([str(self.source)])
        with self.assertRaisesRegex(translate_pdf.TranslationError, "--output-dir is required"):
            translate_pdf._validate_arguments(bare)


if __name__ == "__main__":
    unittest.main()
