from __future__ import annotations

import unittest
from pathlib import Path

from app.errors import UNKNOWN_CODE, describe_failure, flatten, report_text


def raised(error: BaseException, cause: BaseException | None = None) -> BaseException:
    """Return `error` as it would arrive at the GUI: raised, with a traceback."""
    try:
        if cause is None:
            raise error
        try:
            raise cause
        except BaseException as inner:  # noqa: BLE001 - rebuilding a real chain
            raise error from inner
    except BaseException as caught:  # noqa: BLE001
        return caught


class FlattenTests(unittest.TestCase):
    def test_the_cause_survives_the_generic_wrapper(self):
        """The core wraps every failure in "Failed to translate <path>", so only
        the chain says what actually went wrong."""
        error = raised(
            RuntimeError("Failed to translate book.pdf"),
            ValueError("code=8: invalid key in dict"),
        )
        flattened = flatten(error)
        self.assertIn("Failed to translate book.pdf", flattened)
        self.assertIn("invalid key in dict", flattened)


class DescribeFailureTests(unittest.TestCase):
    def test_a_broken_native_library_is_recognised(self):
        failure = describe_failure(
            raised(ImportError("pikepdf's extension library (pikepdf._core) failed to import."))
        )
        self.assertEqual(failure.code, "E-CORE-01")
        self.assertIn("giải nén", failure.advice)

    def test_an_unreadable_pdf_is_recognised_through_its_cause(self):
        failure = describe_failure(
            raised(
                RuntimeError("PDF translation core failed"),
                ValueError("FzErrorSyntax: code=8: invalid key in dict"),
            )
        )
        self.assertEqual(failure.code, "E-PDF-02")

    def test_an_existing_output_is_a_skip_not_a_failure(self):
        failure = describe_failure(
            raised(RuntimeError("Output already exists: C:/out/book-vi.pdf."))
        )
        self.assertEqual(failure.code, "E-OUT-05")
        self.assertIn("Ghi đè", failure.advice)

    def test_a_network_failure_is_recognised(self):
        failure = describe_failure(raised(TimeoutError("Max retries exceeded")))
        self.assertEqual(failure.code, "E-NET-04")

    def test_an_unmapped_failure_keeps_its_original_text(self):
        failure = describe_failure(raised(RuntimeError("something nobody mapped")))
        self.assertEqual(failure.code, UNKNOWN_CODE)
        self.assertIn("something nobody mapped", failure.detail)

    def test_every_failure_offers_a_summary_and_an_action(self):
        for error in (
            ImportError("pikepdf._core"),
            RuntimeError("FzErrorSyntax"),
            TimeoutError("timed out"),
            RuntimeError("unmapped"),
        ):
            with self.subTest(error=error):
                failure = describe_failure(raised(error))
                self.assertTrue(failure.summary.strip())
                self.assertTrue(failure.advice.strip())
                self.assertIn(failure.code, failure.headline)


class ReportTextTests(unittest.TestCase):
    def test_the_report_carries_what_a_maintainer_needs(self):
        """A user who cannot read the error also cannot retype it, so the copied
        block has to stand on its own."""
        failure = describe_failure(raised(ImportError("pikepdf._core failed")))
        report = report_text(
            failure, Path("C:/x/Skin.pdf"), "0.2.2", Path("C:/x/pdf-translate.log")
        )
        for expected in ("Skin.pdf", "0.2.2", failure.code, "pdf-translate.log"):
            with self.subTest(expected=expected):
                self.assertIn(expected, report)

    def test_a_missing_log_is_simply_omitted(self):
        failure = describe_failure(raised(RuntimeError("boom")))
        self.assertNotIn("Log", report_text(failure, Path("a.pdf"), "0.2.2"))


if __name__ == "__main__":
    unittest.main()
