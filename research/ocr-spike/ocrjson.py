"""The one schema every OCR engine and the oracle are normalised into.

Keeping engines behind a single shape is what makes the bake-off fair: the
sidecar writer, the scorers and the pipeline runner all consume this and never
learn which recogniser produced it.

    {"page": 0, "width": 612.0, "height": 792.0,
     "lines": [{"text": "...", "bbox": [x0, y0, x1, y1], "conf": 0.98,
                "baseline": 96.4 | null, "size": 10.0 | null}]}

Coordinates are PDF points in PyMuPDF's convention: origin top-left, y growing
downward, matching `page.rect`. `baseline` and `size` are optional truth an
oracle can supply and a recogniser cannot; the sidecar writer must work without
them, because that is the case that ships.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Line:
    text: str
    bbox: tuple[float, float, float, float]
    conf: float = 1.0
    baseline: float | None = None
    size: float | None = None

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass
class Page:
    page: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)


def dump(pages: list[Page], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(p) for p in pages], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def load(path: Path) -> list[Page]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Page(
            page=p["page"],
            width=p["width"],
            height=p["height"],
            lines=[Line(**line) for line in p["lines"]],
        )
        for p in raw
    ]


def reading_order(lines: list[Line], page_width: float) -> list[Line]:
    """Sort lines the way a human reads them, columns first.

    This matters more than it looks. `pdf2zh/converter.py:553` iterates the
    page in content-stream order with no sorting of its own, and decides line
    breaks by comparing each glyph's x against the previous one. So the order
    the sidecar emits lines in *is* the order paragraphs are built in: emit a
    two-column page in raw detector order and the two columns are welded into
    one run of nonsense.

    A single split at the widest horizontal gap handles the two-column case
    that dominates real documents, and degrades to a plain top-to-bottom sort
    when no such gap exists.
    """
    if len(lines) < 4:
        return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))

    midpoint = page_width / 2.0
    left = [line for line in lines if (line.bbox[0] + line.bbox[2]) / 2 < midpoint]
    right = [line for line in lines if (line.bbox[0] + line.bbox[2]) / 2 >= midpoint]

    # Two columns only if both sides are populated and almost nothing straddles
    # the centre. A heading spanning the full width would otherwise be torn.
    straddling = sum(
        1 for line in lines if line.bbox[0] < midpoint * 0.8 and line.bbox[2] > midpoint * 1.2
    )
    if len(left) >= 3 and len(right) >= 3 and straddling <= max(1, len(lines) // 10):
        return sorted(left, key=lambda ln: (ln.bbox[1], ln.bbox[0])) + sorted(
            right, key=lambda ln: (ln.bbox[1], ln.bbox[0])
        )
    return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))
