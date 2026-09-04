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
                    [width * 0.92, height * 0.25],
                    [width * 0.92, height * 0.72],
                    [width * 0.08, height * 0.72],
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
            SimpleNamespace(text=value, bbox=(10, 10 + index * 20, 80, 25 + index * 20))
            for index, value in enumerate(("a", "b", "c", "d", "e", "f"))
        ]
        reasons = _page_safety_reasons(image, lines, [], None)
        self.assertIn("fragmented OCR lines", reasons)

    def test_any_detected_protected_region_preserves_the_page(self):
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        lines = [SimpleNamespace(text="ordinary prose", bbox=(10, 10, 180, 30))]
        reasons = _page_safety_reasons(image, lines, [(0, 0, 200, 200)], None)
        self.assertIn("protected layout region", reasons)


if __name__ == "__main__":
    unittest.main()
