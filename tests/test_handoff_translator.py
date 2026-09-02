from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pdf2zh.cache import clean_test_db, init_test_db
from pdf2zh.translator import (
    FormulaPlaceholderError,
    GoogleTranslator,
    HandoffTranslator,
    SegmentTooLongError,
    encode_formula_placeholders,
    load_segment_table,
    normalise_number_abbreviation,
    placeholders,
    restore_formula_placeholders,
    validate_style_tags,
)


def _jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


class SegmentTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_a_missing_path_yields_an_empty_table(self):
        self.assertEqual(load_segment_table(None), {})

    def test_loads_records_and_skips_blank_translations(self):
        path = _jsonl(
            self.root / "table.jsonl",
            [{"src": "Hello", "dst": "Xin chào"}, {"src": "Unfilled", "dst": ""}],
        )
        self.assertEqual(load_segment_table(str(path)), {"Hello": "Xin chào"})

    def test_skips_entries_that_lost_or_reordered_a_formula_placeholder(self):
        path = _jsonl(
            self.root / "table.jsonl",
            [
                {"src": "where <b0></b0> holds", "dst": "trong đó đúng"},
                {"src": "and <b1></b1> too", "dst": "và <b1></b1> nữa"},
            ],
        )
        table = load_segment_table(str(path))
        self.assertNotIn("where <b0></b0> holds", table)
        self.assertIn("and <b1></b1> too", table)

    def test_rejects_a_malformed_record_and_names_the_line(self):
        path = self.root / "table.jsonl"
        path.write_text('{"src": "a", "dst": "b"}\n{"src": "no dst here"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 2"):
            load_segment_table(str(path))

    def test_placeholders_are_returned_in_order(self):
        self.assertEqual(
            placeholders("a <b0></b0> b <b1></b1>"),
            ["<b0>", "</b0>", "<b1>", "</b1>"],
        )

    def test_legacy_converter_placeholders_are_normalised(self):
        path = _jsonl(
            self.root / "table.jsonl",
            [{"src": "where {v0} holds", "dst": "nÆ¡i {v0} Ä‘Ãºng"}],
        )
        self.assertEqual(
            load_segment_table(str(path)),
            {"where <b0></b0> holds": "nÆ¡i <b0></b0> Ä‘Ãºng"},
        )

    def test_converter_placeholders_round_trip_through_safe_tags(self):
        source = "{v27} C{v28}[ ]"
        encoded = encode_formula_placeholders(source)
        self.assertEqual(encoded, "<b27></b27> C<b28></b28>[ ]")
        self.assertEqual(restore_formula_placeholders(source, encoded), source)

    def test_damaged_or_reordered_placeholder_tags_are_rejected(self):
        source = "{v0} and {v1}"
        for translated in (
            "<b0></b0> and <b1>",
            "<b1></b1> and <b0></b0>",
        ):
            with self.subTest(translated=translated):
                with self.assertRaises(FormulaPlaceholderError):
                    restore_formula_placeholders(source, translated)

    def test_balanced_style_pairs_may_reorder_as_complete_runs(self):
        validate_style_tags(
            "<s1>Bold</s1> and <s2>italic</s2>",
            "<s2>nghiêng</s2> và <s1>đậm</s1>",
        )

    def test_missing_or_cross_nested_style_tags_are_rejected(self):
        source = "<s1>Bold</s1> and <s2>italic</s2>"
        for translated in (
            "đậm and <s2>nghiêng</s2>",
            "<s1><s2>sai</s1></s2>",
        ):
            with self.subTest(translated=translated):
                with self.assertRaises(FormulaPlaceholderError):
                    validate_style_tags(source, translated)

    def test_handoff_skips_a_translation_that_loses_style(self):
        path = _jsonl(
            self.root / "table.jsonl",
            [{"src": "<s1>Important</s1>", "dst": "Quan trọng"}],
        )
        self.assertEqual(load_segment_table(str(path)), {})


class HandoffTranslatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_db = init_test_db()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.misses = self.root / "missing.jsonl"

    def tearDown(self) -> None:
        self.temp_directory.cleanup()
        clean_test_db(self.test_db)

    def _translator(self, table: list[dict] | None = None) -> HandoffTranslator:
        envs = {"segments_out": str(self.misses)}
        if table is not None:
            envs["segments_in"] = str(_jsonl(self.root / "table.jsonl", table))
        return HandoffTranslator("auto", "vi", envs=envs)

    def _recorded_misses(self) -> list[str]:
        lines = self.misses.read_text(encoding="utf-8").splitlines()
        return [json.loads(line)["src"] for line in lines if line.strip()]

    def test_returns_the_supplied_translation(self):
        translator = self._translator([{"src": "Hello", "dst": "Xin chào"}])
        self.assertEqual(translator.translate("Hello"), "Xin chào")
        self.assertEqual(self._recorded_misses(), [])

    def test_records_each_miss_once_and_passes_the_text_through(self):
        translator = self._translator()
        self.assertEqual(translator.translate("Hello"), "Hello")
        self.assertEqual(translator.translate("Hello"), "Hello")
        self.assertEqual(translator.translate("Goodbye"), "Goodbye")
        self.assertEqual(self._recorded_misses(), ["Hello", "Goodbye"])

    def test_never_caches_an_untranslated_passthrough(self):
        translator = self._translator()
        translator.translate("Hello")
        self.assertTrue(translator.ignore_cache)
        self.assertIsNone(translator.cache.get("Hello"))

    def test_truncates_a_stale_miss_file_on_construction(self):
        self.misses.write_text('{"src": "from an older run"}\n', encoding="utf-8")
        self._translator()
        self.assertEqual(self._recorded_misses(), [])


class GoogleSegmentLengthTests(unittest.TestCase):
    def test_a_segment_over_the_limit_is_refused_rather_than_truncated(self):
        """Upstream sends the first 5000 characters and returns that as the whole
        translation, so the rest of a long paragraph disappears with nothing said."""
        translator = GoogleTranslator("en", "vi")
        with self.assertRaises(SegmentTooLongError):
            translator.do_translate("a" * 5001)

    def test_a_segment_at_the_limit_is_still_sent(self):
        translator = GoogleTranslator("en", "vi")
        sent = {}

        def fake_get(endpoint, params, headers, timeout):
            sent["q"] = params["q"]
            raise RuntimeError("stop before the network")

        translator.session.get = fake_get
        with self.assertRaises(RuntimeError):
            translator.do_translate("a" * 5000)
        self.assertEqual(len(sent["q"]), 5000)


class NumberAbbreviationTests(unittest.TestCase):
    """`no.` in front of a number means "number", never "not"."""

    def test_the_abbreviation_is_capitalised_before_a_number(self):
        self.assertEqual(
            normalise_number_abbreviation("can be found in our brochure, ref. no. 305"),
            "can be found in our brochure, ref. No. 305",
        )

    def test_every_occurrence_in_a_segment_is_capitalised(self):
        self.assertEqual(
            normalise_number_abbreviation("Part no. 12 and no. 13"),
            "Part No. 12 and No. 13",
        )

    def test_a_space_before_the_number_is_allowed(self):
        self.assertEqual(normalise_number_abbreviation("no.  7"), "No.  7")

    def test_the_negation_is_left_alone(self):
        self.assertEqual(
            normalise_number_abbreviation("There is no. Then we stop."),
            "There is no. Then we stop.",
        )

    def test_the_tail_of_a_longer_word_is_not_the_abbreviation(self):
        self.assertEqual(
            normalise_number_abbreviation("casino. 5 tables"),
            "casino. 5 tables",
        )

    def test_already_capitalised_text_is_unchanged(self):
        self.assertEqual(normalise_number_abbreviation("ref. No. 305"), "ref. No. 305")


if __name__ == "__main__":
    unittest.main()
