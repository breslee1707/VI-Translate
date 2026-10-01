from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
import numpy as np

from pdf2zh.high_level import translate_stream
from pdf2zh.ocr import OCR_FONT_PATH
from tests.test_ocr import EmptyLayoutModel


class MixedTextOrientationTests(unittest.TestCase):
    def test_missed_plot_with_a_rotated_axis_is_preserved_on_the_recheck(self):
        class PlotModel:
            def __init__(self) -> None:
                self.sizes: list[int] = []

            def predict(self, _image, imgsz: int):
                self.sizes.append(imgsz)
                boxes = [] if imgsz != 1024 else [
                    SimpleNamespace(cls=0, xyxy=np.asarray([[35.0, 90.0, 190.0, 210.0]]))
                ]
                return [SimpleNamespace(boxes=boxes, names={0: "figure"})]

        source = pymupdf.open()
        page = source.new_page(width=300, height=300)
        page.draw_rect((80, 100, 175, 175), color=(0, 0, 0))
        page.draw_line((85, 160), (160, 125), color=(1, 0, 0))
        page.insert_text((60, 150), "d (mm)", rotate=90, fontsize=6, fontname="tiro")
        page.insert_text((125, 165), "Ave.", fontsize=6, fontname="tiro")
        original = source.tobytes()
        before = page.get_pixmap(alpha=False).samples
        model = PlotModel()
        with (
            patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)),
            patch("pdf2zh.high_level.output_style_font_paths",
                  return_value={style: str(OCR_FONT_PATH) for style in range(4)}),
        ):
            mono, _, _ = translate_stream(original, lang_in="en", lang_out="vi", service="handoff",
                                           thread=1, model=model, create_dual=False, ignore_cache=True)
        source.close()
        self.assertEqual(model.sizes, [288, 1024])
        with pymupdf.open(stream=mono) as output:
            self.assertEqual(before, output[0].get_pixmap(alpha=False).samples)

    def test_rotated_axis_and_horizontal_label_in_one_region_keep_their_directions(self):
        for rotated_first in (True, False):
            with self.subTest(rotated_first=rotated_first):
                source = pymupdf.open()
                page = source.new_page(width=595, height=842)

                def rotated() -> None:
                    page.insert_text((60, 430), "d (mm)", rotate=90, fontsize=6, fontname="tiro")

                def horizontal() -> None:
                    page.insert_text((130, 480), "Ave.", fontsize=6, fontname="tiro")

                for draw in ((rotated, horizontal) if rotated_first else (horizontal, rotated)):
                    draw()
                original = source.tobytes()
                source.close()
                with (
                    patch("pdf2zh.high_level.download_remote_fonts", return_value=str(OCR_FONT_PATH)),
                    patch("pdf2zh.high_level.output_style_font_paths",
                          return_value={style: str(OCR_FONT_PATH) for style in range(4)}),
                ):
                    mono, _, report = translate_stream(
                        original, lang_in="en", lang_out="vi", service="handoff", thread=1,
                        model=EmptyLayoutModel(), create_dual=False, ignore_cache=True,
                    )
                self.assertFalse(report.failures)
                with pymupdf.open(stream=mono) as output:
                    self.assertEqual(len(output), 1)
                    self.assertEqual(tuple(output[0].rect), (0.0, 0.0, 595.0, 842.0))
                    lines = [line for block in output[0].get_text("dict")["blocks"]
                             for line in block.get("lines", [])]
                    labels = [("".join(span["text"] for span in line["spans"]), line["dir"])
                              for line in lines]
                    self.assertTrue(any("Ave." in text and direction == (1.0, 0.0)
                                        for text, direction in labels), labels)
                    self.assertTrue(any("mm" in text and direction == (0.0, -1.0)
                                        for text, direction in labels), labels)


if __name__ == "__main__":
    unittest.main()
