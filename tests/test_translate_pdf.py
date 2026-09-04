from __future__ import annotations

import argparse
import tempfile
from collections import Counter
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from pdf2zh.high_level import TranslationReport
from scripts import translate_pdf


class FailureReportingTests(unittest.TestCase):
    def test_an_over_long_segment_is_reported_as_its_own_reason(self):
        """Truncating it silently loses the tail; saying so lets the user act.

        It is neither a fit failure nor a dead connection, and telling the user
        their network is down would send them to check the wrong thing.
        """
        lines = translate_pdf._describe_failures(Counter({"SegmentTooLongError": 2}))
        self.assertEqual(len(lines), 1)
        self.assertIn("2 segments", lines[0])
        self.assertIn("longer than the translation service accepts", lines[0])

    def test_each_kind_of_failure_gets_its_own_line(self):
        lines = translate_pdf._describe_failures(
            Counter(
                {
                    "SegmentTooLongError": 1,
                    "FormulaPlaceholderError": 1,
                    "single line needs less than 50% font size": 1,
                    "ConnectionError": 1,
                }
            )
        )
        self.assertEqual(len(lines), 4)
        self.assertEqual(
            sum("translation engine failed" in line for line in lines), 1
        )


class TranslatePdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name).resolve()
        self.source = self.root / "guide.pdf"
        self.source.write_bytes(b"%PDF-1.7\nsource")
        self.output = self.root / "output"

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    @staticmethod
    def _engine_side_effect(source, temp_output, *_args, **_kwargs):
        (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(
            b"%PDF-1.7\ntranslated"
        )
        # The real runner hands back what the core could not translate, and why.
        return TranslationReport(translatable_segments=12)

    @mock.patch.object(translate_pdf, "_require_core")
    @mock.patch.object(translate_pdf, "_run_engine")
    def test_copies_the_core_result_to_a_stable_vietnamese_name(self, run, _core):
        run.side_effect = self._engine_side_effect

        result = translate_pdf.translate_pdf(self.source, self.output)

        self.assertEqual(result.path, self.output / "guide-vi.pdf")
        self.assertEqual(result.untranslated, 0)
        self.assertEqual(result.path.read_bytes(), b"%PDF-1.7\ntranslated")
        run.assert_called_once_with(
            self.source, mock.ANY, "vi", "auto", None, translate_pdf.DEFAULT_THREADS, False, "google", {}, None,
            ocr=False,
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
            ocr=False,
        )

    def test_reports_segments_the_engine_could_not_translate(self):
        def partial(source, temp_output, *_args, **_kwargs):
            (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(b"%PDF-1.7\n")
            return TranslationReport(
                failures=["a"] * 7,
                reasons=Counter({"ConnectionError": 7}),
                translatable_segments=40,
            )

        with (
            mock.patch.object(translate_pdf, "_require_core"),
            mock.patch.object(translate_pdf, "_run_engine", side_effect=partial),
        ):
            result = translate_pdf.translate_pdf(self.source, self.output)

        # A document that lost some segments must still be delivered, and say so.
        self.assertEqual(result.path, self.output / "guide-vi.pdf")
        self.assertEqual(result.untranslated, 7)

    def test_refuses_a_document_with_no_extractable_text(self):
        """An image-only scan must not be handed over as a finished translation."""
        def scanned(source, temp_output, *_args, **_kwargs):
            (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(b"%PDF")
            return TranslationReport(
                image_only_pages={0, 1, 2, 3}, translatable_segments=0, pages_processed=4
            )

        with (
            mock.patch.object(translate_pdf, "_require_core"),
            mock.patch.object(translate_pdf, "_run_engine", side_effect=scanned),
        ):
            with self.assertRaises(translate_pdf.TranslationError) as caught:
                translate_pdf.translate_pdf(self.source, self.output)

        message = str(caught.exception)
        self.assertIn("image-only", message)
        self.assertIn("OCR", message)
        # Nothing may be written: a file on disk is what made this look like success.
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_reports_image_only_pages_of_a_mixed_document(self):
        def mixed(source, temp_output, *_args, **_kwargs):
            (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(b"%PDF")
            return TranslationReport(image_only_pages={2, 6}, translatable_segments=30)

        with (
            mock.patch.object(translate_pdf, "_require_core"),
            mock.patch.object(translate_pdf, "_run_engine", side_effect=mixed),
        ):
            result = translate_pdf.translate_pdf(self.source, self.output)

        # Delivered, because the rest of it translated, but the gaps are named.
        self.assertEqual(result.path, self.output / "guide-vi.pdf")
        self.assertEqual(result.image_only_pages, (2, 6))

    def test_layout_failures_are_not_reported_as_a_network_problem(self):
        """The bug this guards: every skipped segment blamed the translation service."""
        lines = translate_pdf._describe_failures(
            Counter({"single line needs less than 50% font size": 5})
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("did not fit", lines[0])
        for wrong in ("service", "engine failed", "reach"):
            self.assertNotIn(wrong, lines[0])

    def test_damaged_formula_markers_are_reported_as_their_own_cause(self):
        lines = translate_pdf._describe_failures(Counter({"FormulaPlaceholderError": 1}))

        self.assertEqual(len(lines), 1)
        self.assertIn("formula", lines[0])
        self.assertTrue(lines[0].startswith("1 segment "), lines[0])

    def test_each_cause_is_reported_separately(self):
        lines = translate_pdf._describe_failures(
            Counter({
                "table cell cannot fit at 50% font size": 2,
                "rotated text needs less than 50% font size": 1,
                "FormulaPlaceholderError": 4,
                "ConnectionError": 3,
            })
        )

        self.assertEqual(len(lines), 3)
        joined = " | ".join(lines)
        self.assertIn("3 segments", joined)
        self.assertIn("4 segments", joined)
        self.assertIn("ConnectionError x3", joined)

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

    def test_the_ocr_phase_reaches_the_progress_callback(self):
        """The pass runs after the last page is translated, so a bar with no
        phase name sits at 22/22 and the app reads as hung."""
        from pdf2zh.high_level import OCR_PHASE as CORE_OCR_PHASE

        seen = []
        fake_model = object()
        with (
            mock.patch(
                "pdf2zh.doclayout.OnnxModel.load_available", return_value=fake_model
            ),
            mock.patch(
                "pdf2zh.high_level.translate", return_value=[("x.pdf", "")]
            ) as core,
        ):
            translate_pdf._run_engine(
                self.source, self.output, "vi", "auto", None, 1, False, "google", {},
                lambda done, total, phase: seen.append((done, total, phase)),
            )
            callback = core.call_args.kwargs["callback"]
            callback(SimpleNamespace(n=3, total=22, desc=""))
            callback(SimpleNamespace(n=5, total=22, desc=CORE_OCR_PHASE))

        self.assertEqual(seen, [(3, 22, ""), (5, 22, translate_pdf.OCR_PHASE)])

    def test_ocr_is_rejected_for_the_handoff_engine(self):
        """The handoff table is built in one run and consumed in another. OCR
        text would have to be recognized identically both times, so the
        combination is refused rather than emitted as a table that cannot be
        honoured on rebuild."""
        args = translate_pdf._parser().parse_args(
            [
                str(self.source),
                "--output-dir",
                str(self.output),
                "--engine",
                "handoff",
                "--segments",
                "t.jsonl",
                "--ocr",
            ]
        )
        with self.assertRaisesRegex(translate_pdf.TranslationError, "not supported with"):
            translate_pdf._validate_arguments(args)

    def test_a_scan_is_still_refused_when_ocr_read_nothing(self):
        def unreadable(source, temp_output, *_args, **_kwargs):
            (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(b"%PDF")
            return TranslationReport(
                image_only_pages={0}, translatable_segments=0, pages_processed=1
            )

        with (
            mock.patch.object(translate_pdf, "_require_core"),
            mock.patch.object(translate_pdf, "_run_engine", side_effect=unreadable),
        ):
            with self.assertRaises(translate_pdf.TranslationError) as caught:
                translate_pdf.translate_pdf(self.source, self.output, ocr=True)

        # Must not tell someone who already enabled OCR to enable OCR.
        message = str(caught.exception)
        self.assertIn("OCR read nothing", message)
        self.assertFalse((self.output / "guide-vi.pdf").exists())

    def test_a_page_ocr_translated_is_not_reported_as_an_untranslated_scan(self):
        def with_ocr(source, temp_output, *_args, **_kwargs):
            (Path(temp_output) / f"{Path(source).stem}-mono.pdf").write_bytes(b"%PDF")
            return TranslationReport(
                image_only_pages={2, 6},
                translatable_segments=0,
                ocr_pages=(2,),
                ocr_segments=9,
            )

        with (
            mock.patch.object(translate_pdf, "_require_core"),
            mock.patch.object(translate_pdf, "_run_engine", side_effect=with_ocr),
        ):
            result = translate_pdf.translate_pdf(self.source, self.output, ocr=True)

        self.assertEqual(result.ocr_pages, (2,))
        self.assertEqual(result.image_only_pages, (6,))

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
