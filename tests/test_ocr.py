from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pymupdf

from pdf2zh.ocr import (
    OcrLayoutRegion,
    OcrLine,
    OcrReflowRegion,
    OCR_FONT_PATH,
    _split_ocr_paragraphs,
    _layout_regions,
    _is_verse_layout,
    _raster_bullet_starts,
    _physical_ocr_rows,
    _merge_ocr_line_fragments,
    _ink_mask,
    _local_ink_mask,
    _restore_paper_background,
    _owned_reflow_regions,
    _page_safety_reasons,
    _reading_order,
    _residual_ink_fraction,
    _is_standalone_marker,
    _has_multiple_columns,
    page_is_image_only,
    prepare_ocr_pdf,
    replace_ocr_page_images,
)
from pdf2zh.high_level import apply_ocr_region_ownership, translate_stream
from pdf2zh.converter import glyph_layout_class

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "ocr-spike"))
import ocrjson  # noqa: E402
from oracle import merge_visual_lines  # noqa: E402


class FakeRecognizer:
    def __call__(self, image: np.ndarray) -> SimpleNamespace:
        height, width = image.shape[:2]
        return SimpleNamespace(
            boxes=np.asarray(
                [[
                    [width * 0.08, height * 0.25],
                    [width * 0.82, height * 0.25],
                    [width * 0.82, height * 0.62],
                    [width * 0.08, height * 0.62],
                ]],
                dtype=float,
            ),
            txts=("Hello OCR world",),
            scores=(0.99,),
        )


class EmptyLayoutModel:
    def predict(self, _image: np.ndarray, imgsz: int) -> list[SimpleNamespace]:
        self.imgsz = imgsz
        return [SimpleNamespace(boxes=[], names={})]


class FormulaLayoutModel:
    def predict(self, image: np.ndarray, imgsz: int) -> list[SimpleNamespace]:
        height, width = image.shape[:2]
        detection = SimpleNamespace(
            cls=0,
            xyxy=np.asarray([[0.0, 0.0, float(width), float(height)]], dtype=float),
        )
        return [SimpleNamespace(boxes=[detection], names={0: "isolate_formula"})]


def make_scan(path: Path) -> None:
    image = np.full((200, 600, 3), 255, dtype=np.uint8)
    cv2.putText(
        image,
        "Hello OCR world",
        (48, 125),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.8,
        (0, 0, 0),
        4,
        cv2.LINE_AA,
    )
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("test image encoding failed")
    document = pymupdf.open()
    page = document.new_page(width=216, height=72)
    page.insert_image(page.rect, stream=encoded.tobytes())
    document.save(path)
    document.close()


class OcrPreparationTests(unittest.TestCase):
    @staticmethod
    def _line(
        text: str, bbox: tuple[float, float, float, float]
    ) -> OcrLine:
        x0, y0, x1, y1 = bbox
        return OcrLine(text, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)), 0.99)

    def test_truth_fragments_on_one_baseline_are_joined(self):
        fragments = [
            ocrjson.Line("A Digital", (72, 218, 149, 243), baseline=238, size=19),
            ocrjson.Line("Library", (175, 218, 232, 243), baseline=238, size=19),
            ocrjson.Line("another line", (72, 250, 155, 262), baseline=260, size=9),
        ]
        merged = merge_visual_lines(fragments)
        self.assertEqual([line.text for line in merged], ["A Digital Library", "another line"])

    def test_layout_resolution_is_bounded_for_large_historical_scans(self):
        # LOC pages render at 4445px tall; running DocLayout at that size
        # detected only fragments instead of complete body paragraphs.
        model = EmptyLayoutModel()
        _layout_regions(model, np.zeros((4445, 2662, 3), dtype=np.uint8))
        self.assertEqual(model.imgsz, 1024)

    def test_dense_prose_requires_explicit_ownership_before_reflow(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = [self._line("A dense but ordinary prose line", (20, y, 580, y + 25))
                 for y in range(20, 380, 30)]
        self.assertIn("dense text without reflow headroom", _page_safety_reasons(image, lines, [], None))
        self.assertNotIn("dense text without reflow headroom", _page_safety_reasons(
            image, lines, [], None, region_ownership_proven=True
        ))

    def test_raster_bullets_split_model_merged_list_without_erasing_markers(self):
        image = np.full((200, 300, 3), 255, dtype=np.uint8)
        first = self._line("Access the project home page", (60, 20, 270, 40))
        continuation = self._line("at example.org", (60, 45, 190, 65))
        second = self._line("Send a question by email", (60, 70, 270, 90))
        cv2.circle(image, (35, 30), 5, (0, 0, 0), -1)
        cv2.circle(image, (35, 80), 5, (0, 0, 0), -1)
        before = image.copy()
        starts = _raster_bullet_starts(image, [first, continuation, second])
        self.assertEqual(starts, {first.bbox, second.bbox})
        self.assertTrue(np.array_equal(before, image))
        groups = _split_ocr_paragraphs([first, continuation, second], (60, 20, 280, 90), starts)
        self.assertEqual([len(group) for group in groups], [2, 1])

    def test_numbered_verse_keeps_physical_lines_but_wrapped_prose_does_not(self):
        verse = [self._line("V.", (100, 10, 120, 20)), self._line("VI.", (100, 300, 120, 310))]
        verse += [self._line("The warrior crossed the plain;", (20, 40+i*15, 250, 50+i*15))
                  for i in range(14)]
        self.assertTrue(_is_verse_layout(verse))
        self.assertFalse(_is_verse_layout(verse[2:]))
        owned = _owned_reflow_regions(verse[2:], [
            OcrLayoutRegion("plain text", (18, 35, 260, 270), .99)
        ], 300, 400, 1, preserve_lines=True)
        self.assertEqual(len(owned), 14)
        self.assertTrue(all(region.preserve_line_breaks for region in owned))

    def test_image_only_requires_an_image_and_no_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            scan = Path(temporary) / "scan.pdf"
            make_scan(scan)
            with pymupdf.open(scan) as document:
                self.assertTrue(page_is_image_only(document[0]))

            text = Path(temporary) / "text.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 72), "ordinary text")
            document.save(text)
            document.close()
            with pymupdf.open(text) as document:
                self.assertFalse(page_is_image_only(document[0]))

    def test_prepares_sidecar_without_changing_the_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scan.pdf"
            sidecar = root / "sidecar.pdf"
            final = root / "final.pdf"
            make_scan(source)
            before = hashlib.sha256(source.read_bytes()).digest()

            prepared = prepare_ocr_pdf(
                source,
                sidecar,
                mode="standard",
                pages=None,
                layout_model=EmptyLayoutModel(),
                recognizer=FakeRecognizer(),
            )

            self.assertEqual(prepared.pages, (0,))
            self.assertEqual(prepared.recognised_lines, 1)
            self.assertEqual(prepared.inserted_lines, 1)
            self.assertIn(0, prepared.cleaned_images)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            with pymupdf.open(sidecar) as document:
                self.assertIn("Hello OCR world", document[0].get_text())
                before_ink = sum(value < 220 for value in document[0].get_pixmap().samples)

            replace_ocr_page_images(sidecar, final, prepared.cleaned_images)
            with pymupdf.open(final) as document:
                self.assertIn("Hello OCR world", document[0].get_text())
                after_ink = sum(value < 220 for value in document[0].get_pixmap().samples)
            self.assertLess(after_ink, before_ink)

    def test_formula_region_is_never_cleaned_or_added_to_the_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scan.pdf"
            sidecar = root / "sidecar.pdf"
            make_scan(source)

            prepared = prepare_ocr_pdf(
                source,
                sidecar,
                mode="standard",
                pages=None,
                layout_model=FormulaLayoutModel(),
                recognizer=FakeRecognizer(),
            )

            self.assertEqual(prepared.pages, ())
            self.assertEqual(prepared.protected_lines, 1)
            self.assertFalse(prepared.cleaned_images)
            with pymupdf.open(sidecar) as document:
                self.assertEqual(document[0].get_text().strip(), "")

    def test_fragmented_ocr_is_preserved_as_a_whole_page(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = [
            SimpleNamespace(
                text=value,
                bbox=(10, 10 + index * 20, 80, 25 + index * 20),
                polygon=(
                    (10, 10 + index * 20),
                    (80, 10 + index * 20),
                    (80, 25 + index * 20),
                    (10, 25 + index * 20),
                ),
            )
            for index, value in enumerate(("a", "b", "c", "d", "e", "f"))
        ]
        reasons = _page_safety_reasons(image, lines, [], None)
        self.assertIn("fragmented OCR lines", reasons)

    def test_any_detected_protected_region_preserves_the_page(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = [
            SimpleNamespace(
                text="ordinary prose",
                bbox=(10, 10, 180, 30),
                polygon=((10, 10), (180, 10), (180, 30), (10, 30)),
            )
        ]
        reasons = _page_safety_reasons(image, lines, [(0, 0, 200, 200)], None)
        self.assertIn("protected layout region", reasons)

    def test_tiny_single_layout_detection_does_not_block_prose(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = [
            SimpleNamespace(
                text="ordinary prose",
                bbox=(10, 10, 180, 30),
                polygon=((10, 10), (180, 10), (180, 30), (10, 30)),
            )
        ]
        reasons = _page_safety_reasons(image, lines, [(550, 10, 560, 20)], None)
        self.assertNotIn("protected layout region", reasons)

    def test_mojibake_preserves_the_whole_page(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = [
            SimpleNamespace(
                text="NA\ufffdA",
                bbox=(10, 10, 180, 30),
                polygon=((10, 10), (180, 10), (180, 30), (10, 30)),
            )
        ]
        reasons = _page_safety_reasons(image, lines, [], None)
        self.assertIn("damaged OCR characters", reasons)

    def test_dense_text_boxes_without_reflow_room_preserve_the_page(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = []
        for index in range(8):
            top = index * 45
            bottom = top + 40
            lines.append(
                SimpleNamespace(
                    text="dense prose line with no spare room",
                    bbox=(20, top, 580, bottom),
                    polygon=((20, top), (580, top), (580, bottom), (20, bottom)),
                )
            )
        reasons = _page_safety_reasons(image, lines, [], None)
        self.assertIn("dense text without reflow headroom", reasons)

    def test_two_column_heading_rules_are_not_mistaken_for_a_form_grid(self):
        image = np.full((800, 1200, 3), 255, dtype=np.uint8)
        cv2.line(image, (80, 180), (530, 180), (0, 0, 0), 2)
        cv2.line(image, (670, 280), (1120, 280), (0, 0, 0), 2)
        lines = [
            self._line("ordinary prose in the left column", (80, 220, 530, 245)),
            self._line("ordinary prose in the right column", (670, 320, 1120, 345)),
        ]
        reasons = _page_safety_reasons(
            image, lines, [], None, region_ownership_proven=True
        )
        self.assertNotIn("dense rules or form grid", reasons)

    def test_crossing_rules_still_preserve_a_form_grid(self):
        image = np.full((800, 1200, 3), 255, dtype=np.uint8)
        cv2.line(image, (80, 180), (1120, 180), (0, 0, 0), 2)
        cv2.line(image, (600, 80), (600, 720), (0, 0, 0), 2)
        lines = [self._line("cell label", (100, 210, 300, 235))]
        reasons = _page_safety_reasons(image, lines, [], None)
        self.assertIn("dense rules or form grid", reasons)

    def test_residual_ink_check_distinguishes_clean_and_dirty_regions(self):
        line = SimpleNamespace(
            polygon=((20, 20), (180, 20), (180, 70), (20, 70))
        )
        clean = np.full((100, 200, 3), 255, dtype=np.uint8)
        dirty = clean.copy()
        cv2.putText(dirty, "OCR", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
        self.assertEqual(_residual_ink_fraction(clean, line), 0.0)
        self.assertGreater(_residual_ink_fraction(dirty, line), 0.02)

    def test_standalone_bullets_stay_in_the_source_raster(self):
        self.assertTrue(_is_standalone_marker("●"))
        self.assertTrue(_is_standalone_marker("•"))
        self.assertFalse(_is_standalone_marker("• translated item"))

    def test_two_populated_columns_are_not_reflowed_as_one_page(self):
        lines = []
        for column_x in (20, 340):
            for index in range(4):
                top = 40 + index * 40
                lines.append(
                    SimpleNamespace(
                        bbox=(column_x, top, column_x + 220, top + 25)
                    )
                )
        self.assertTrue(_has_multiple_columns(lines, 600))

    def test_region_aware_order_keeps_spanning_header_before_two_columns(self):
        title = self._line("Spanning title", (240, 10, 360, 25))
        left = [
            self._line(f"left {index}", (20, 40 + index * 30, 250, 60 + index * 30))
            for index in range(3)
        ]
        right = [
            self._line(
                f"right {index}", (350, 40 + index * 30, 580, 60 + index * 30)
            )
            for index in range(3)
        ]
        footer = self._line("Spanning footer", (220, 150, 380, 170))

        ordered = _reading_order(right + [footer] + left + [title], 600)

        self.assertEqual(
            [line.text for line in ordered],
            ["Spanning title", "left 0", "left 1", "left 2", "right 0", "right 1", "right 2", "Spanning footer"],
        )

    def test_layout_regions_prove_multi_column_ownership(self):
        title = self._line("Spanning title", (240, 10, 360, 25))
        left = [
            self._line(f"left {index}", (20, 40 + index * 30, 250, 60 + index * 30))
            for index in range(3)
        ]
        right = [
            self._line(
                f"right {index}", (350, 40 + index * 30, 580, 60 + index * 30)
            )
            for index in range(3)
        ]
        regions = [
            OcrLayoutRegion("title", (230, 5, 370, 30), 0.90),
            OcrLayoutRegion("plain text", (10, 35, 260, 130), 0.95),
            OcrLayoutRegion("plain text", (340, 35, 590, 130), 0.96),
        ]

        owned = _owned_reflow_regions(
            [title] + left + right,
            regions,
            page_width=600,
            page_height=300,
            scale=1.0,
        )

        self.assertIsNotNone(owned)
        assert owned is not None
        self.assertEqual(len(owned), 3)
        self.assertEqual([len(region.line_boxes) for region in owned], [1, 3, 3])
        self.assertEqual(owned[0].bbox, (240.0, 270.0, 370.0, 295.0))

    def test_multi_column_region_proof_fails_when_a_line_has_no_owner(self):
        lines = [
            self._line("left prose", (20, 40, 250, 60)),
            self._line("unowned prose", (270, 80, 330, 100)),
        ]
        regions = [OcrLayoutRegion("plain text", (10, 35, 260, 130), 0.95)]
        self.assertIsNone(
            _owned_reflow_regions(lines, regions, 600, 300, 1.0)
        )

    def test_proven_region_ownership_releases_only_reflow_guards(self):
        image = np.full((1200, 600, 3), 255, dtype=np.uint8)
        lines = []
        for column_x in (20, 340):
            for index in range(13):
                top = 20 + index * 35
                lines.append(
                    self._line(
                        "a normal line of prose",
                        (column_x, top, column_x + 220, top + 22),
                    )
                )

        reasons = _page_safety_reasons(
            image,
            lines,
            [],
            None,
            region_ownership_proven=True,
        )

        self.assertNotIn("multi-column OCR ownership", reasons)
        self.assertNotIn("too many OCR lines for safe reflow", reasons)

    def test_high_level_replays_owned_regions_as_distinct_classes(self):
        layout = np.ones((300, 600), dtype=float)
        bounds: dict[int, tuple[float, float, float, float]] = {}
        regions = (
            OcrReflowRegion((20, 100, 250, 260), ((30, 220, 240, 240),)),
            OcrReflowRegion((350, 100, 580, 260), ((360, 220, 570, 240),)),
        )

        next_class = apply_ocr_region_ownership(layout, bounds, regions, 10)

        self.assertEqual(next_class, 12)
        self.assertEqual(layout[230, 50], 10)
        self.assertEqual(layout[230, 400], 11)
        self.assertEqual(bounds[10], regions[0].bbox)
        self.assertEqual(bounds[11], regions[1].bbox)

    def test_owned_ocr_lines_do_not_overwrite_protected_pixels(self):
        layout = np.ones((100, 200))
        layout[40:60, 50:70] = 0
        region = OcrReflowRegion((10, 20, 190, 80), ((20, 35, 180, 65),))
        apply_ocr_region_ownership(layout, {}, [region], 10)
        self.assertTrue(np.all(layout[40:60, 50:70] == 0))
        self.assertEqual(layout[50, 30], 10)

    def test_center_inside_region_is_not_enough_for_cross_column_line(self):
        line = self._line("a line crossing the column gutter", (20, 40, 380, 60))
        region = OcrLayoutRegion("plain text", (10, 35, 260, 130), 0.95)
        self.assertIsNone(_owned_reflow_regions([line], [region], 600, 300, 1))

    def test_ownership_tolerates_small_model_edge_errors_on_short_lines(self):
        line = self._line("types:", (73.3, 470.6, 100.8, 482.2))
        region = OcrLayoutRegion("plain text", (74.3, 471.6, 297.5, 614.0), 0.95)
        self.assertIsNotNone(_owned_reflow_regions([line], [region], 600, 792, 1))

    def test_ocr_paragraphs_keep_wrapped_short_lines_but_split_real_gaps(self):
        lines = [
            self._line("A sentence continuing", (20, 20, 200, 30)),
            self._line("through a short line", (20, 33, 140, 43)),
            self._line("to its end.", (20, 46, 110, 56)),
            self._line("A separate paragraph.", (20, 80, 220, 90)),
        ]
        groups = _split_ocr_paragraphs(lines, (20, 20, 240, 90))
        self.assertEqual([len(group) for group in groups], [3, 1])

    def test_postal_address_keeps_rows_and_uses_available_region_width(self):
        lines = [
            self._line("Write to:", (20, 20, 70, 30)),
            self._line("Information Desk", (20, 33, 140, 43)),
            self._line("Mail Stop 148", (20, 46, 100, 56)),
            self._line("Hampton, VA 23681-2199", (20, 59, 180, 69)),
        ]
        regions = _owned_reflow_regions(lines, [
            OcrLayoutRegion("plain text", (18, 18, 190, 72), 0.95)
        ], 300, 200, 1)
        self.assertEqual(len(regions), 4)
        self.assertTrue(all(region.preserve_line_breaks for region in regions))
        self.assertTrue(all(region.bbox[2] == 190 for region in regions))
        bounds = {}
        apply_ocr_region_ownership(np.ones((200, 300)), {}, regions, 10, bounds)
        self.assertEqual(len(bounds), 4)
        self.assertEqual(bounds[10], (20, 170, 190, 180))

    def test_address_row_does_not_split_horizontally_fragmented_ocr(self):
        lines = [self._line("NASA Center for Aero", (20, 20, 140, 30)),
                 self._line("Space Information", (142, 21, 240, 31)),
                 self._line("7121 Standard Drive", (20, 40, 170, 50))]
        self.assertEqual([len(row) for row in _physical_ocr_rows(lines)], [2, 1])
        merged = _merge_ocr_line_fragments(lines)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].text, "NASA Center for Aero Space Information")

    def test_postal_glyph_descender_cannot_take_ownership_of_following_row(self):
        # Actual NASA1999 ink bounds: second-row glyph y0=183.0 lands
        # inside row3's padded box, while its centre y=187.8 belongs to row2.
        regions = [
            OcrReflowRegion((335, 194.8, 534, 206.8), ((335, 194.8, 438, 206.8),)),
            OcrReflowRegion((336, 183.6, 534, 195.2), ((336, 183.6, 528, 195.2),)),
            OcrReflowRegion((337, 173.2, 534, 184.0), ((337, 173.2, 433, 184.0),)),
        ]
        layout = np.ones((792, 612))
        apply_ocr_region_ownership(layout, {}, regions, 25)
        self.assertEqual(int(layout[183, 338]), 27)
        self.assertEqual(glyph_layout_class(layout, (338, 183, 344, 192.7), {25, 26, 27}), 26)
        self.assertEqual(glyph_layout_class(layout, (440, 183, 446, 192.7), {25, 26, 27}), 26)

    def test_ocr_fragment_merge_never_joins_columns_or_eats_a_bullet(self):
        lines = [self._line("left prose", (20, 20, 140, 30)),
                 self._line("right prose", (240, 20, 340, 30)),
                 self._line("•", (15, 50, 20, 60)),
                 self._line("bullet body", (22, 50, 140, 60))]
        self.assertEqual(len(_merge_ocr_line_fragments(lines)), 4)

    def test_cropped_ink_mask_matches_full_page_mask(self):
        image = np.full((400, 800, 3), 240, dtype=np.uint8)
        cv2.putText(image, "OCR source", (120, 160), cv2.FONT_HERSHEY_SIMPLEX,
                    1.2, (40, 40, 40), 2, cv2.LINE_AA)
        line = self._line("OCR source", (115, 125, 370, 168))
        self.assertTrue(np.array_equal(_ink_mask(image, line), _local_ink_mask(image, line)))

    def test_paper_restoration_removes_ink_but_keeps_protected_mark_and_margin(self):
        image = np.full((400, 800, 3), (220, 209, 181), dtype=np.uint8)
        cv2.putText(image, "OCR source", (120, 160), cv2.FONT_HERSHEY_SIMPLEX,
                    1.2, (40, 40, 40), 2, cv2.LINE_AA)
        cv2.rectangle(image, (350, 130), (365, 160), (20, 20, 20), -1)
        line = self._line("OCR source", (115, 125, 370, 168))
        restored = _restore_paper_background(image, [line], [(348, 128, 367, 162)])
        self.assertIsNotNone(restored)
        self.assertTrue(np.array_equal(restored[128:163, 348:368], image[128:163, 348:368]))
        self.assertTrue(np.array_equal(restored[:100], image[:100]))
        self.assertTrue(np.all(restored[130:165, 120:340] == (220, 209, 181)))

    def test_paper_restoration_expands_sampling_for_dense_bands(self):
        image = np.full((800, 1200, 3), (220, 209, 181), dtype=np.uint8)
        lines = [self._line("dense prose", (100, y, 1100, y + 39))
                 for y in range(150, 650, 40)]
        for line in lines:
            x0, y0, x1, y1 = map(int, line.bbox)
            image[y0:y1, x0:x1] = 40
        restored = _restore_paper_background(image, lines, [])
        self.assertIsNotNone(restored)
        self.assertTrue(np.all(restored[160:650, 110:1090] == (220, 209, 181)))
        self.assertTrue(np.array_equal(restored[:100], image[:100]))

    def test_paper_restoration_refuses_without_any_blank_samples(self):
        image = np.full((80, 120, 3), 40, dtype=np.uint8)
        line = self._line("unreliable full image", (0, 0, 119, 79))
        self.assertIsNone(_restore_paper_background(image, [line], []))

    def test_ocr_region_prevents_font_metric_induced_sentence_fragments(self):
        # Real NASA OCR: sidecar font 8pt, source baseline step 13pt.
        # The ordinary >1.5em gap heuristic incorrectly split every line.
        with tempfile.TemporaryDirectory() as temporary:
            document = pymupdf.open()
            page = document.new_page(width=300, height=200)
            for y, text in zip((30, 43, 56), (
                "Since its founding, NASA has been dedicated to the",
                "advancement of aeronautics and space science.",
                "The program supports this important role.",
            )):
                page.insert_text((20, y), text, fontsize=8)
            stream = document.tobytes()
            document.close()
            region = OcrReflowRegion((18, 140, 240, 185), ((18, 140, 240, 185),))
            counts = []
            for owned in (False, True):
                emitted = Path(temporary) / f"segments-{owned}.jsonl"
                with patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)), patch(
                    "pdf2zh.high_level.output_style_font_paths", return_value={0: str(OCR_FONT_PATH)}
                ):
                    translate_stream(
                        stream, lang_in="en", lang_out="vi", service="handoff",
                        thread=1, model=EmptyLayoutModel(), create_dual=False,
                        envs={"segments_out": str(emitted)},
                        ocr_regions_by_page={0: (region,)} if owned else None,
                    )
                rows = [json.loads(line) for line in emitted.read_text(encoding="utf-8").splitlines()]
                counts.append(len(rows))
                if owned:
                    self.assertIn("the advancement", rows[0]["src"])
                    self.assertIn("science. The program", rows[0]["src"])
            self.assertEqual(counts, [3, 1])

    def test_protected_form_glyphs_and_rules_retain_source_geometry(self):
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=200)
        page.insert_text((35, 60), "Protected form label", fontsize=12)
        page.insert_text((35, 88), "Small label", fontsize=8)
        page.draw_line((20, 65), (280, 65), width=1)
        page.draw_line((20, 95), (280, 95), width=1)
        page.draw_line((20, 125), (280, 125), width=1, dashes="[1 2] 0")
        original = doc.tobytes()
        doc.close()
        with pymupdf.open(stream=original, filetype="pdf") as source:
            before = source[0].get_pixmap().samples
        with patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)), patch(
            "pdf2zh.high_level.output_style_font_paths", return_value={0: str(OCR_FONT_PATH)}
        ):
            result = translate_stream(original, lang_in="en", lang_out="vi", service="handoff",
                                      thread=1, model=FormulaLayoutModel(), create_dual=False)
        with pymupdf.open(stream=result[0], filetype="pdf") as output:
            after = output[0].get_pixmap().samples
            self.assertEqual(before, after)

    def test_wholly_protected_rotated_page_keeps_original_content_stream(self):
        doc = pymupdf.open()
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 20), "Index", fontsize=12)
        for index in range(20):
            page.insert_text((20, 35 + index * 10), f"Term {index}, {index + 1}", fontsize=7)
        page.draw_line((10, 245), (190, 245), width=1, dashes="[1 2] 0")
        page.set_rotation(90)
        original = doc.tobytes()
        doc.close()
        with pymupdf.open(stream=original, filetype="pdf") as source:
            before = source[0].get_pixmap().samples
            before_text = source[0].get_text()
            before_drawings = len(source[0].get_drawings())
        with patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)), patch(
            "pdf2zh.high_level.output_style_font_paths", return_value={0: str(OCR_FONT_PATH)}
        ):
            result = translate_stream(original, lang_in="en", lang_out="vi", service="handoff",
                                      thread=1, model=EmptyLayoutModel(), create_dual=False)
        with pymupdf.open(stream=result[0], filetype="pdf") as output:
            self.assertEqual(output[0].rotation, 90)
            self.assertEqual(output[0].get_text(), before_text)
            self.assertEqual(len(output[0].get_drawings()), before_drawings)
            self.assertEqual(output[0].get_pixmap().samples, before)

    def test_code_heavy_page_is_preserved(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = []
        for index in range(6):
            top = 20 + index * 45
            text = f"if ($which =~ /host/) {{ push @HOSTS, $value; }} # {index}"
            lines.append(
                SimpleNamespace(
                    text=text,
                    bbox=(30, top, 500, top + 25),
                    polygon=((30, top), (500, top), (500, top + 25), (30, top + 25)),
                )
            )
        reasons = _page_safety_reasons(image, lines, [], None)
        self.assertIn("code-heavy page", reasons)

    def test_long_scan_page_is_preserved_until_reflow_is_proven(self):
        image = np.full((1000, 800, 3), 255, dtype=np.uint8)
        lines = []
        for index in range(25):
            top = 20 + index * 35
            lines.append(
                SimpleNamespace(
                    text="a normal line of prose",
                    bbox=(30, top, 700, top + 22),
                    polygon=((30, top), (700, top), (700, top + 22), (30, top + 22)),
                )
            )
        reasons = _page_safety_reasons(image, lines, [], None)
        self.assertIn("too many OCR lines for safe reflow", reasons)


if __name__ == "__main__":
    unittest.main()
