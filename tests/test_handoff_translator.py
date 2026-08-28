from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pdf2zh.cache import clean_test_db, init_test_db
from pdf2zh.translator import (
    FormulaPlaceholderError,
    HandoffTranslator,
    encode_formula_placeholders,
    load_segment_table,
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


if __name__ == "__main__":
    unittest.main()
