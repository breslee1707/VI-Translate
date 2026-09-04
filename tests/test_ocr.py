from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pymupdf

from pdf2zh.ocr import (
    _page_safety_reasons,
    _residual_ink_fraction,
    _is_standalone_marker,
    _has_multiple_columns,
    page_is_image_only,
    prepare_ocr_pdf,
    replace_ocr_page_images,
)

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
    def test_truth_fragments_on_one_baseline_are_joined(self):
        fragments = [
            ocrjson.Line("A Digital", (72, 218, 149, 243), baseline=238, size=19),
            ocrjson.Line("Library", (175, 218, 232, 243), baseline=238, size=19),
            ocrjson.Line("another line", (72, 250, 155, 262), baseline=260, size=9),
        ]
        merged = merge_visual_lines(fragments)
        self.assertEqual([line.text for line in merged], ["A Digital Library", "another line"])

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
