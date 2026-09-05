"""A faithful replica of the engine's per-page layout class map.

`pdf2zh/high_level.py:264-305` decides, for every pixel of a page, which
paragraph a glyph landing there belongs to -- or whether it is protected and
must not be translated at all. That decision is what an OCR mode lives or dies
by, so the probe has to reproduce it exactly rather than approximate it. The
code below is a transcription of those lines, with the only additions being the
statistics the probe needs.

Keeping it here, rather than importing a private helper, is deliberate: the
spike must not modify `pdf2zh/`, and high_level.py has no seam that exposes the
class map on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymupdf

# high_level.py:271 -- the classes whose contents are protected from translation.
VCLS = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]


@dataclass(frozen=True)
class ClassMap:
    """The `box` array of high_level.py:269, plus what the probe measures."""

    box: np.ndarray
    detections: int
    protected_fraction: float   # share of the page that is class 0
    translatable_fraction: float  # share that is class 1 (untouched default)
    names: tuple[str, ...]      # class name of every detection, in order

    @property
    def is_all_protected(self) -> bool:
        """True when nothing on the page could be translated.

        This is the failure that matters: every glyph on such a page becomes a
        `{vN}` formula, `translatable_segments` reaches zero, and
        `scripts/translate_pdf.py:413` refuses the document -- even one whose
        text an OCR pass had just recovered.
        """
        return not np.any(self.box != 0)


def page_pixmap(page: pymupdf.Page) -> np.ndarray:
    """Rasterise a page exactly as high_level.py:264-267 does (72 DPI, RGB)."""
    pix = page.get_pixmap()
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1]


def class_map(model: object, image: np.ndarray) -> ClassMap:
    """Build the class map for one rendered page.

    Transcribed from high_level.py:264-305. The two loops are kept in their
    original order because it is load-bearing: non-protected boxes are painted
    in ascending confidence so higher-confidence ones overwrite, and the
    protected classes are painted afterwards so they win outright.
    """
    height, width = image.shape[:2]
    page_layout = model.predict(image, imgsz=int(height / 32) * 32)[0]

    box = np.ones((height, width))
    h, w = box.shape

    non_vcls_boxes = [
        (i, d)
        for i, d in enumerate(page_layout.boxes)
        if page_layout.names[int(d.cls)] not in VCLS
    ]
    for i, d in reversed(non_vcls_boxes):
        x0, y0, x1, y1 = d.xyxy.squeeze()
        x0, y0, x1, y1 = (
            np.clip(int(x0 - 1), 0, w - 1),
            np.clip(int(h - y1 - 1), 0, h - 1),
            np.clip(int(x1 + 1), 0, w - 1),
            np.clip(int(h - y0 + 1), 0, h - 1),
        )
        box[y0:y1, x0:x1] = i + 2

    for i, d in enumerate(page_layout.boxes):
        name = page_layout.names[int(d.cls)]
        if name in VCLS:
            x0, y0, x1, y1 = (float(value) for value in d.xyxy.squeeze())
            x0, y0, x1, y1 = (
                np.clip(int(x0 - 1), 0, w - 1),
                np.clip(int(h - y1 - 1), 0, h - 1),
                np.clip(int(x1 + 1), 0, w - 1),
                np.clip(int(h - y0 + 1), 0, h - 1),
            )
            box[y0:y1, x0:x1] = 0

    total = float(box.size)
    return ClassMap(
        box=box,
        detections=len(page_layout.boxes),
        protected_fraction=float(np.count_nonzero(box == 0)) / total,
        translatable_fraction=float(np.count_nonzero(box == 1)) / total,
        names=tuple(page_layout.names[int(d.cls)] for d in page_layout.boxes),
    )


def agreement(left: ClassMap, right: ClassMap) -> float:
    """Share of pixels the two maps agree on as protected-or-not.

    Comparing raw class ids would be meaningless because they are assigned by
    detection order, which differs between runs. What matters downstream is the
    binary question the converter asks: is this pixel translatable at all.
    """
    a, b = left.box, right.box
    if a.shape != b.shape:
        rows = min(a.shape[0], b.shape[0])
        cols = min(a.shape[1], b.shape[1])
        a, b = a[:rows, :cols], b[:rows, :cols]
    return float(np.count_nonzero((a == 0) == (b == 0))) / float(a.size)
