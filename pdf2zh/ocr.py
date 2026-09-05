"""Prepare image-only pages for the existing layout-preserving translator.

OCR is deliberately a sidecar instead of a second PDF renderer. The original
scan remains visible while DocLayout classifies it, recognised prose is added
as invisible text, and the translator emits its usual vector text. Only after
translation succeeds is the full-page scan image replaced with an inpainted
copy. Protected structures never enter the cleanup mask.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pymupdf

from pdf2zh.rules import classify_preserved_page, is_formula_font, page_has_image

OCR_MODES = ("off", "standard", "enhanced")
PROTECTED_LAYOUT_CLASSES = frozenset(
    {"abandon", "figure", "table", "isolate_formula", "formula_caption"}
)
OCR_FONT_NAME = "ocrsource"
OCR_FONT_PATH = Path(__file__).resolve().parents[1] / "app" / "fonts" / "BeVietnamPro-Regular.ttf"
MIN_FONT_SIZE = 3.0
MAX_FONT_SIZE = 72.0
MAX_ROTATION_DEGREES = 10.0
MAX_TEXT_BOX_COVERAGE = 0.45
MAX_RESIDUAL_INK = 0.02
MIN_PROTECTED_PAGE_COVERAGE = 0.01
MAX_SAFE_OCR_LINES = 24
MOJIBAKE_MARKERS = ("\ufffd", "Ã", "Â", "â€", "âˆ", "Å")
OCR_MARKER_CHARACTERS = frozenset("•●▪◦○■□◆◇–—-*")


@dataclass(frozen=True)
class OcrProfile:
    name: str
    dpi: int
    model_type: str
    minimum_confidence: float
    inpaint_radius: float


OCR_PROFILES = {
    "standard": OcrProfile("standard", 200, "small", 0.55, 2.0),
    "enhanced": OcrProfile("enhanced", 300, "medium", 0.50, 3.0),
}
_OCR_ENGINES: dict[str, Callable[[np.ndarray], Any]] = {}
_OCR_ENGINE_LOCK = threading.Lock()


@dataclass(frozen=True)
class OcrLine:
    text: str
    polygon: tuple[tuple[float, float], ...]
    confidence: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.polygon]
        ys = [point[1] for point in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class OcrLayoutRegion:
    """One layout-model region in the OCR raster coordinate system."""

    name: str
    bbox: tuple[float, float, float, float]
    confidence: float


@dataclass(frozen=True)
class OcrReflowRegion:
    """A PDF-space region and the OCR line boxes it exclusively owns."""

    bbox: tuple[float, float, float, float]
    line_boxes: tuple[tuple[float, float, float, float], ...]
    preserve_line_breaks: bool = False


@dataclass
class OcrPreparation:
    sidecar: Path
    cleaned_images: dict[int, bytes] = field(default_factory=dict)
    lines_by_page: dict[int, tuple[OcrLine, ...]] = field(default_factory=dict)
    reflow_regions_by_page: dict[int, tuple[OcrReflowRegion, ...]] = field(
        default_factory=dict
    )
    pages: tuple[int, ...] = ()
    recognised_lines: int = 0
    inserted_lines: int = 0
    protected_lines: int = 0
    skipped_lines: int = 0
    warnings: tuple[str, ...] = ()
    seconds: float = 0.0


class OcrUnavailableError(RuntimeError):
    """Raised when the optional recogniser or one of its models is unavailable."""


def page_is_image_only(page: pymupdf.Page) -> bool:
    """Return whether a page has raster content but no extractable text."""
    blocks = page.get_text("dict").get("blocks", [])
    return page_has_image(blocks) and not page.get_text("text").strip()


def load_ocr_engine(mode: str) -> Callable[[np.ndarray], Any]:
    """Load one configured recogniser, downloading its verified models if needed."""
    if mode not in OCR_PROFILES:
        raise ValueError(f"Unknown OCR mode: {mode}")
    profile = OCR_PROFILES[mode]
    with _OCR_ENGINE_LOCK:
        if mode in _OCR_ENGINES:
            return _OCR_ENGINES[mode]
        try:
            from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError as error:
            raise OcrUnavailableError(
                "OCR dependencies are missing. Install requirements-ocr.txt first."
            ) from error

        model_type = ModelType(profile.model_type)
        try:
            engine = RapidOCR(
                params={
                    "Det.engine_type": EngineType.ONNXRUNTIME,
                    # PP-OCRv6 uses one multilingual detector regardless of
                    # this routing label; ``multi`` is not accepted by
                    # RapidOCR 3.9.2.
                    "Det.lang_type": LangDet.EN,
                    "Det.model_type": model_type,
                    "Det.ocr_version": OCRVersion.PPOCRV6,
                    "Rec.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.lang_type": LangRec.EN,
                    "Rec.model_type": model_type,
                    "Rec.ocr_version": OCRVersion.PPOCRV6,
                    "Global.use_cls": True,
                }
            )
        except Exception as error:
            raise OcrUnavailableError(
                f"Could not load the {profile.name} OCR model: {error}"
            ) from error
        _OCR_ENGINES[mode] = engine
        return engine


def verify_ocr_runtime() -> None:
    """Load both packaged profiles so missing modules or models fail smoke tests."""
    sample = np.full((64, 192, 3), 255, dtype=np.uint8)
    cv2.putText(
        sample, "OCR", (8, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
        (0, 0, 0), 2, cv2.LINE_AA,
    )
    for mode in ("standard", "enhanced"):
        load_ocr_engine(mode)(sample)


def _normalise_result(result: Any) -> list[OcrLine]:
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or texts is None or scores is None:
        return []
    lines = []
    for box, text, score in zip(boxes, texts, scores):
        clean = str(text).strip()
        points = np.asarray(box, dtype=float).reshape(-1, 2)
        if not clean or len(points) < 4:
            continue
        lines.append(
            OcrLine(
                clean,
                tuple((float(x), float(y)) for x, y in points),
                float(score),
            )
        )
    return lines


def recognise_image(engine: Callable[[np.ndarray], Any], image: np.ndarray) -> list[OcrLine]:
    """Run an engine and normalise its output for production and benchmarks."""
    return _normalise_result(engine(image))


def _reading_order(lines: Iterable[OcrLine], page_width: float) -> list[OcrLine]:
    items = list(lines)
    if len(items) < 4:
        return sorted(items, key=lambda line: (line.bbox[1], line.bbox[0]))
    midpoint = page_width / 2.0
    left = [
        line
        for line in items
        if line.bbox[2] < midpoint
    ]
    right = [
        line
        for line in items
        if line.bbox[0] > midpoint
    ]
    if len(left) >= 3 and len(right) >= 3:
        remaining = [line for line in items if line not in left and line not in right]
        column_top = min(line.bbox[1] for line in left + right)
        column_bottom = max(line.bbox[3] for line in left + right)
        top = [line for line in remaining if line.bbox[3] <= column_top]
        bottom = [line for line in remaining if line.bbox[1] >= column_bottom]
        middle = [line for line in remaining if line not in top and line not in bottom]
        if not middle:
            def key(line: OcrLine) -> tuple[float, float]:
                return line.bbox[1], line.bbox[0]

            return (
                sorted(top, key=key)
                + sorted(left, key=key)
                + sorted(right, key=key)
                + sorted(bottom, key=key)
            )
    return sorted(items, key=lambda line: (line.bbox[1], line.bbox[0]))


def _line_rotation(line: OcrLine) -> float:
    first, second = line.polygon[0], line.polygon[1]
    return abs(math.degrees(math.atan2(second[1] - first[1], second[0] - first[0])))


def _merge_ocr_line_fragments(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """Join adjacent pieces of one baseline before choosing a region owner."""
    result: list[OcrLine] = []
    for row in _physical_ocr_rows(lines):
        for line in sorted(row, key=lambda item: item.bbox[0]):
            if result:
                previous = result[-1]
                a, b = previous.bbox, line.bbox
                overlap = min(a[3], b[3]) - max(a[1], b[1])
                height = min(a[3] - a[1], b[3] - b[1])
                gap = b[0] - a[2]
                if (overlap > height * 0.6 and 0 <= gap <= height * 0.6
                        and not _is_standalone_marker(previous.text)
                        and not _is_standalone_marker(line.text)):
                    x0, y0, x1, y1 = min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])
                    result[-1] = OcrLine(previous.text.rstrip() + " " + line.text.lstrip(),
                                         ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
                                         min(previous.confidence, line.confidence))
                    continue
            result.append(line)
    return result


def _is_standalone_marker(text: str) -> bool:
    clean = text.strip()
    return bool(clean) and all(character in OCR_MARKER_CHARACTERS for character in clean)


def _layout_regions(model: object, image: np.ndarray) -> list[OcrLayoutRegion]:
    prediction = model.predict(
        image[:, :, ::-1], imgsz=min(1024, max(32, int(image.shape[0] / 32) * 32))
    )[0]
    regions = []
    for detection in prediction.boxes:
        name = prediction.names[int(detection.cls)]
        x0, y0, x1, y1 = (float(value) for value in detection.xyxy.squeeze())
        confidence = float(getattr(detection, "conf", 1.0))
        regions.append(OcrLayoutRegion(name, (x0, y0, x1, y1), confidence))
    return regions


def _protected_boxes(
    regions: Sequence[OcrLayoutRegion],
) -> list[tuple[float, float, float, float]]:
    return [region.bbox for region in regions if region.name in PROTECTED_LAYOUT_CLASSES]


def _overlap_fraction(
    bbox: tuple[float, float, float, float],
    protected: Sequence[tuple[float, float, float, float]],
) -> float:
    x0, y0, x1, y1 = bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if area <= 0:
        return 1.0
    best = 0.0
    for px0, py0, px1, py1 in protected:
        width = max(0.0, min(x1, px1) - max(x0, px0))
        height = max(0.0, min(y1, py1) - max(y0, py0))
        best = max(best, width * height / area)
    return best


def _ink_mask(image: np.ndarray, line: OcrLine) -> np.ndarray | None:
    """Inspect a padded line crop instead of reprocessing a whole scan."""
    x0, y0, x1, y1 = line.bbox
    margin = max(8, int((y1 - y0) * 0.1) + 4)
    left, top = max(0, int(x0) - margin), max(0, int(y0) - margin)
    right = min(image.shape[1], int(math.ceil(x1)) + margin)
    bottom = min(image.shape[0], int(math.ceil(y1)) + margin)
    if right <= left or bottom <= top:
        return None
    local = OcrLine(line.text, tuple((x-left, y-top) for x, y in line.polygon), line.confidence)
    mask = _local_ink_mask(image[top:bottom, left:right], local)
    if mask is None:
        return None
    result = np.zeros(image.shape[:2], dtype=np.uint8)
    result[top:bottom, left:right] = mask
    return result


def _local_ink_mask(image: np.ndarray, line: OcrLine) -> np.ndarray | None:
    """Return a glyph-shaped mask, refusing boxes that cannot be isolated safely."""
    height, width = image.shape[:2]
    polygon = np.rint(np.asarray(line.polygon)).astype(np.int32)
    polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
    polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
    region = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(region, polygon, 255)
    area = int(np.count_nonzero(region))
    if area < 16:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    kernel = np.ones((5, 5), dtype=np.uint8)
    border = cv2.dilate(region, kernel, iterations=1)
    border = cv2.subtract(border, region)
    samples = gray[border > 0]
    if samples.size < 8:
        samples = gray[region > 0]
    background = float(np.median(samples))
    difference = np.abs(gray.astype(np.float32) - background).astype(np.uint8)
    values = difference[region > 0]
    threshold = max(18, int(np.percentile(values, 65)))
    mask = np.where((region > 0) & (difference >= threshold), 255, 0).astype(np.uint8)

    count = int(np.count_nonzero(mask))
    coverage = count / area
    if count < 8 or not 0.005 <= coverage <= 0.45:
        return None

    components, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    filtered = np.zeros_like(mask)
    line_height = max(1.0, line.bbox[3] - line.bbox[1])
    for component in range(1, components):
        component_width = stats[component, cv2.CC_STAT_WIDTH]
        component_height = stats[component, cv2.CC_STAT_HEIGHT]
        component_area = stats[component, cv2.CC_STAT_AREA]
        if component_area < 2:
            continue
        if component_width > line_height * 5 and component_height < line_height * 0.25:
            continue
        filtered[labels == component] = 255
    if np.count_nonzero(filtered) < 8:
        return None
    dilation = max(1, int(round(line_height * 0.04)))
    return cv2.dilate(
        filtered,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation * 2 + 1,) * 2),
        iterations=1,
    )


def _page_safety_reasons(
    image: np.ndarray,
    lines: Sequence[OcrLine],
    protected: Sequence[tuple[float, float, float, float]],
    page_decision: Any,
    *,
    region_ownership_proven: bool = False,
) -> list[str]:
    """Identify page-level structures that must never be partially erased."""
    reasons: list[str] = []
    if page_decision is not None:
        reasons.append(page_decision.kind.lower())
    protected_area = sum(
        max(0.0, min(float(image.shape[1]), x1) - max(0.0, x0))
        * max(0.0, min(float(image.shape[0]), y1) - max(0.0, y0))
        for x0, y0, x1, y1 in protected
    )
    protected_coverage = protected_area / max(1, image.shape[0] * image.shape[1])
    if len(protected) >= 2 or protected_coverage >= MIN_PROTECTED_PAGE_COVERAGE:
        reasons.append("protected layout region")

    if any(any(marker in line.text for marker in MOJIBAKE_MARKERS) for line in lines):
        reasons.append("damaged OCR characters")
    if _has_multiple_columns(lines, image.shape[1]) and not region_ownership_proven:
        reasons.append("multi-column OCR ownership")

    short_lines = sum(len(line.text) <= 3 for line in lines)
    one_character_lines = sum(len(line.text) == 1 for line in lines)
    if lines and short_lines / len(lines) > 0.15:
        reasons.append("fragmented OCR lines")
    if lines and one_character_lines / len(lines) > 0.08:
        reasons.append("single-character OCR fragments")
    if len(lines) > MAX_SAFE_OCR_LINES and not region_ownership_proven:
        reasons.append("too many OCR lines for safe reflow")

    formula_chars = sum(
        sum(character in "=+−-*/^_<>≤≥√∫Σπαβγδ" for character in line.text)
        for line in lines
    )
    code_chars = sum(
        sum(character in "{}[]$@#\\|;~`" for character in line.text)
        for line in lines
    )
    digit_chars = sum(sum(character.isdigit() for character in line.text) for line in lines)
    total_chars = sum(len(line.text) for line in lines)
    if total_chars and (formula_chars / total_chars > 0.10 or digit_chars / total_chars > 0.30):
        reasons.append("formula or numeric-heavy page")
    code_lines = sum(
        bool(
            any(token in line.text for token in ("$", "@", "{", "}", "#", "sub ", "if ("))
        )
        for line in lines
    )
    if total_chars and (code_chars / total_chars > 0.04 or code_lines >= 5):
        reasons.append("code-heavy page")

    text_boxes = np.zeros(image.shape[:2], dtype=np.uint8)
    for line in lines:
        polygon = np.rint(np.asarray(line.polygon)).astype(np.int32)
        polygon[:, 0] = np.clip(polygon[:, 0], 0, image.shape[1] - 1)
        polygon[:, 1] = np.clip(polygon[:, 1], 0, image.shape[0] - 1)
        cv2.fillConvexPoly(text_boxes, polygon, 255)
    if (np.count_nonzero(text_boxes) / max(1, text_boxes.size) > MAX_TEXT_BOX_COVERAGE
            and not region_ownership_proven):
        reasons.append("dense text without reflow headroom")

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    dark = np.where(gray < 190, 255, 0).astype(np.uint8)
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(20, image.shape[1] // 25), 1)
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(20, image.shape[0] // 25))
    )
    horizontal = cv2.morphologyEx(dark, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(dark, cv2.MORPH_OPEN, vertical_kernel)
    def long_components(mask: np.ndarray, horizontal_line: bool) -> int:
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        count = 0
        for stat in stats[1:]:
            component_width, component_height = stat[2:4]
            if horizontal_line:
                count += component_width >= image.shape[1] * 0.20 and component_height <= image.shape[0] * 0.02
            else:
                count += component_height >= image.shape[0] * 0.20 and component_width <= image.shape[1] * 0.02
        return count

    horizontal_rules = long_components(horizontal, True)
    vertical_rules = long_components(vertical, False)
    # Two independent heading underlines are common on an ordinary two-column
    # textbook page. Treat them as typography, not as a form grid. Three
    # horizontal rules, two vertical rules, or a crossing pair still closes
    # the page before any source pixels can be erased.
    if (horizontal_rules >= 3 or vertical_rules >= 2
            or (horizontal_rules >= 1 and vertical_rules >= 1)):
        reasons.append("dense rules or form grid")
    return list(dict.fromkeys(reasons))


def _has_multiple_columns(lines: Sequence[OcrLine], page_width: int) -> bool:
    """Return whether two populated text columns overlap vertically.

    The downstream converter groups OCR sidecar glyphs by its own layout
    classes. On a dense two-column scan, a continuation from the left column
    can otherwise attach to a later right-column block. Preserve the page
    until OCR regions have explicit column ownership.
    """
    if len(lines) < 6 or page_width <= 0:
        return False
    left = [
        line
        for line in lines
        if (line.bbox[0] + line.bbox[2]) / 2 < page_width * 0.45
        and line.bbox[2] < page_width * 0.62
    ]
    right = [
        line
        for line in lines
        if (line.bbox[0] + line.bbox[2]) / 2 > page_width * 0.55
        and line.bbox[0] > page_width * 0.38
    ]
    if len(left) < 3 or len(right) < 3:
        return False
    left_range = (min(line.bbox[1] for line in left), max(line.bbox[3] for line in left))
    right_range = (
        min(line.bbox[1] for line in right),
        max(line.bbox[3] for line in right),
    )
    overlap = max(0.0, min(left_range[1], right_range[1]) - max(left_range[0], right_range[0]))
    smaller_span = min(left_range[1] - left_range[0], right_range[1] - right_range[0])
    return smaller_span > 0 and overlap / smaller_span >= 0.25


def _owned_reflow_regions(
    lines: Sequence[OcrLine],
    layout_regions: Sequence[OcrLayoutRegion],
    page_width: float,
    page_height: float,
    scale: float,
    paragraph_starts: set[tuple[float, float, float, float]] | None = None,
    preserve_lines: bool = False,
) -> tuple[OcrReflowRegion, ...] | None:
    """Assign every OCR line to one model region and return reading-order hints.

    A multi-column sidecar is safe only when the exact regions used to approve
    it also reach the converter. Choosing the highest-confidence box mirrors
    ``high_level.translate_patch`` where stronger detections overwrite weaker
    overlapping boxes. Lines without an owner make the proof fail closed.
    """
    candidates = [
        region
        for region in layout_regions
        if region.name not in PROTECTED_LAYOUT_CLASSES
        and region.bbox[2] > region.bbox[0]
        and region.bbox[3] > region.bbox[1]
    ]
    if not lines or not candidates or page_width <= 0 or page_height <= 0:
        return None

    owned: dict[OcrLayoutRegion, list[OcrLine]] = {}
    for line in lines:
        x0, y0, x1, y1 = line.bbox
        center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        # Model boxes approximate ink, not exact PDF glyph extents. A quarter
        # line-height tolerates a clipped ascender or short edge word without
        # admitting a line that runs across the column gutter.
        padding = max(2.0, (y1 - y0) * 0.25)
        matches = [
            region
            for region in candidates
            if _overlap_fraction(line.bbox, [(
                region.bbox[0] - padding, region.bbox[1] - padding,
                region.bbox[2] + padding, region.bbox[3] + padding,
            )]) >= 0.95
            and region.bbox[0] <= center[0] <= region.bbox[2]
            and region.bbox[1] <= center[1] <= region.bbox[3]
        ]
        if not matches:
            return None
        owner = max(matches, key=lambda region: region.confidence)
        owned.setdefault(owner, []).append(line)

    regions = list(owned)
    if _has_multiple_columns(lines, int(page_width)):
        midpoint = page_width / 2.0
        column_regions = [
            region
            for region in regions
            if not (region.bbox[0] < midpoint < region.bbox[2])
        ]
        if not column_regions:
            return None
        column_top = min(region.bbox[1] for region in column_regions)
        column_bottom = max(region.bbox[3] for region in column_regions)
        ambiguous = [
            region
            for region in regions
            if region.bbox[0] < midpoint < region.bbox[2]
            and not (region.bbox[3] <= column_top or region.bbox[1] >= column_bottom)
        ]
        if ambiguous:
            return None

        def region_key(region: OcrLayoutRegion) -> tuple[int, float, float]:
            x0, y0, x1, y1 = region.bbox
            if x0 < midpoint < x1:
                band = 0 if y1 <= column_top else 3
            else:
                band = 1 if (x0 + x1) / 2.0 < midpoint else 2
            return band, y0, x0

        regions.sort(key=region_key)
    else:
        regions.sort(key=lambda region: (region.bbox[1], region.bbox[0]))

    def pdf_box(
        bbox: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = bbox
        return (
            x0 * scale,
            page_height - y1 * scale,
            x1 * scale,
            page_height - y0 * scale,
        )

    result = []
    for region in regions:
        groups = _split_ocr_paragraphs(owned[region], region.bbox, paragraph_starts)
        rows = []
        for group in groups:
            keep_lines = preserve_lines or _is_postal_address(group)
            rows.extend(((row, True) for row in _physical_ocr_rows(group)) if keep_lines else [(group, False)])
        for group, keep_lines in rows:
            x0, y0, x1, y1 = region.bbox
            # Model list boxes include the bullet gutter. Continuation lines
            # own the prose edge, not the marker's x coordinate.
            x0 = min(line.bbox[0] for line in group)
            x1 = max(x1, max(line.bbox[2] for line in group))
            if len(rows) > 1:
                y0 = min(line.bbox[1] for line in group)
                y1 = max(line.bbox[3] for line in group)
            if keep_lines:
                x0 = group[0].bbox[0]
            result.append(OcrReflowRegion(
                pdf_box((x0, y0, x1, y1)),
                tuple(pdf_box(line.bbox) for line in group),
                preserve_line_breaks=keep_lines,
            ))
    return tuple(result)


def _is_postal_address(lines: Sequence[OcrLine]) -> bool:
    """A postal block's physical lines are semantic, unlike wrapped prose."""
    text = "\n".join(line.text for line in lines)
    return bool(
        2 <= len(lines) <= 8
        and re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", text)
        and re.search(r"\b(?:mail\s*stop|P\.?\s*O\.?\s*box|street|avenue|road|drive|write\s+to)\b", text, re.IGNORECASE)
    )


def _physical_ocr_rows(lines: Sequence[OcrLine]) -> list[list[OcrLine]]:
    """Keep horizontally split OCR fragments together on their source row."""
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        if rows:
            previous = rows[-1][0].bbox
            overlap = min(previous[3], line.bbox[3]) - max(previous[1], line.bbox[1])
            if overlap > 0.5 * min(previous[3] - previous[1], line.bbox[3] - line.bbox[1]):
                rows[-1].append(line)
                rows[-1].sort(key=lambda item: item.bbox[0])
                continue
        rows.append([line])
    return rows


def _split_ocr_paragraphs(
    lines: Sequence[OcrLine],
    bounds: tuple[float, float, float, float],
    paragraph_starts: set[tuple[float, float, float, float]] | None = None,
) -> list[list[OcrLine]]:
    """Find real paragraph gaps before sidecar font metrics can distort them."""
    ordered = sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))
    if not ordered:
        return []
    ink_height = float(np.median([line.bbox[3] - line.bbox[1] for line in ordered]))
    groups = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:]):
        gap = current.bbox[1] - previous.bbox[3]
        indented = current.bbox[0] - bounds[0] > ink_height
        short_end = previous.bbox[2] < bounds[0] + 0.75 * (bounds[2] - bounds[0])
        sentence_end = previous.text.rstrip().endswith(('.', '!', '?', ':'))
        if (current.bbox in (paragraph_starts or set())
                or gap > ink_height or (indented and short_end and sentence_end)):
            groups.append([])
        groups[-1].append(current)
    return groups


def _is_verse_layout(lines: Sequence[OcrLine]) -> bool:
    """Conservative evidence for numbered stanzas, not arbitrary short prose."""
    stanzas = sum(bool(re.fullmatch(r"[IVXLCDM]+\.", line.text.strip())) for line in lines)
    body = [line.text.strip() for line in lines if len(line.text.split()) >= 3]
    return bool(stanzas >= 2 and len(body) >= 12
                and sum(text[0].isupper() for text in body) / len(body) >= 0.85
                and np.median([len(text.split()) for text in body]) <= 12
                and sum(text[-1] in ",;:.!?—" for text in body) / len(body) >= 0.65)


def _raster_bullet_starts(image: np.ndarray, lines: Sequence[OcrLine]) -> set:
    """Find compact round markers immediately left of a recognized text line."""
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    starts = set()
    for line in lines:
        x0, y0, _x1, y1 = line.bbox
        height = y1 - y0
        left, right = max(0, int(x0 - height * 2.5)), max(0, int(x0 - height * 0.15))
        top, bottom = max(0, int(y0)), min(gray.shape[0], int(y1))
        crop = gray[top:bottom, left:right]
        if not crop.size:
            continue
        mask = np.where(crop < float(np.median(crop)) - 45, 255, 0).astype(np.uint8)
        count, _labels, stats, _centers = cv2.connectedComponentsWithStats(mask, 8)
        for _x, _y, width, ink_height, area in stats[1:count]:
            if (height * 0.18 <= ink_height <= height * 0.8
                    and 0.7 <= width / max(1, ink_height) <= 1.3
                    and area / max(1, width * ink_height) >= 0.65):
                starts.add(line.bbox)
                break
    return starts


def _residual_ink_fraction(image: np.ndarray, line: OcrLine) -> float:
    """Measure dark contrast left inside a supposedly cleaned text polygon."""
    height, width = image.shape[:2]
    polygon = np.rint(np.asarray(line.polygon)).astype(np.int32)
    polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
    polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
    region = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(region, polygon, 255)
    border = cv2.dilate(region, np.ones((5, 5), dtype=np.uint8), iterations=1)
    border = cv2.subtract(border, region)
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    samples = gray[border > 0]
    if samples.size < 8:
        return 1.0
    background = float(np.median(samples))
    residual = (region > 0) & (np.abs(gray.astype(np.float32) - background) >= 24)
    return float(np.count_nonzero(residual)) / max(1, int(np.count_nonzero(region)))


def _restore_paper_background(
    image: np.ndarray,
    lines: Sequence[OcrLine],
    protected: Sequence[tuple[float, float, float, float]],
) -> np.ndarray | None:
    """Estimate plain paper behind approved text, excluding ink from samples.

    Glyph-only inpainting retains JPEG ringing between the old letters, which
    appears as a checkerboard after reflow. Sample neighbouring blank paper on
    a coarse grid and replace only the approved line bands. Protected pixels
    and all other source content remain exact.
    """
    mask = np.zeros(image.shape[:2], np.uint8)
    for line in lines:
        cv2.fillConvexPoly(mask, np.rint(line.polygon).astype(np.int32), 255)
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    protected_mask = np.zeros_like(mask)
    for x0, y0, x1, y1 in protected:
        cv2.rectangle(protected_mask, (max(0, int(x0)-2), max(0, int(y0)-2)),
                      (int(math.ceil(x1))+2, int(math.ceil(y1))+2), 255, -1)
    mask[protected_mask > 0] = 0
    valid = ((mask == 0) & (protected_mask == 0)).astype(np.float32)
    small = (max(1, image.shape[1] // 8), max(1, image.shape[0] // 8))
    weights = cv2.resize(valid, small, interpolation=cv2.INTER_AREA)
    weighted = cv2.resize(image.astype(np.float32) * valid[:, :, None], small,
                          interpolation=cv2.INTER_AREA)
    # Dense historical lines can leave almost no blank sample within the
    # first kernel. Grow the sampling radius until every approved band has
    # support. Use one scale throughout: switching scales per pixel leaves
    # visible contour seams on an otherwise smoothly shaded paper surface.
    for sigma in (8, 16, 32):
        blurred_weights = cv2.GaussianBlur(weights, (0, 0), sigma)
        support = cv2.resize(blurred_weights, (image.shape[1], image.shape[0]))
        if not np.any((mask > 0) & (support < 0.03)):
            break
    else:
        return None
    background = cv2.GaussianBlur(weighted, (0, 0), sigma) / np.maximum(
        blurred_weights[:, :, None], 0.001
    )
    background = cv2.resize(background, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)
    result = image.copy()
    result[mask > 0] = np.clip(np.rint(background[mask > 0]), 0, 255).astype(np.uint8)
    return result


def _font_size(line: OcrLine, scale: float, font: pymupdf.Font) -> float:
    x0, y0, x1, y1 = line.bbox
    width = max(1.0, (x1 - x0) * scale)
    height = max(1.0, (y1 - y0) * scale)
    natural = font.text_length(line.text, fontsize=1.0)
    fitted = width / natural if natural > 0 else height * 0.8
    return max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, fitted, height * 1.4))


def _insert_sidecar_lines(
    page: pymupdf.Page,
    lines: Iterable[OcrLine],
    scale: float,
) -> int:
    if is_formula_font(OCR_FONT_NAME):
        raise AssertionError("OCR sidecar font must not be formula-like")
    page.insert_font(fontname=OCR_FONT_NAME, fontfile=str(OCR_FONT_PATH))
    font = pymupdf.Font(fontfile=str(OCR_FONT_PATH))
    inserted = 0
    for line in lines:
        size = _font_size(line, scale, font)
        x0, _y0, _x1, y1 = line.bbox
        baseline = y1 * scale - size * 0.20
        page.insert_text(
            (x0 * scale, baseline),
            line.text,
            fontname=OCR_FONT_NAME,
            fontsize=size,
            render_mode=3,
        )
        inserted += 1
    return inserted


def prepare_ocr_pdf(
    source: Path,
    sidecar: Path,
    *,
    mode: str,
    pages: Sequence[int] | None,
    layout_model: object,
    recognizer: Callable[[np.ndarray], Any] | None = None,
) -> OcrPreparation:
    """Create a sidecar and cleaned page images without modifying ``source``."""
    if mode not in OCR_PROFILES:
        raise ValueError(f"Unknown OCR mode: {mode}")
    profile = OCR_PROFILES[mode]
    engine = recognizer or load_ocr_engine(mode)
    started = time.perf_counter()
    selected = set(pages) if pages is not None else None
    cleaned_images: dict[int, bytes] = {}
    lines_by_page: dict[int, tuple[OcrLine, ...]] = {}
    reflow_regions_by_page: dict[int, tuple[OcrReflowRegion, ...]] = {}
    warnings: list[str] = []
    processed_pages: list[int] = []
    recognised = inserted = protected_count = skipped = 0

    output = pymupdf.open()
    with pymupdf.open(source) as document:
        for index, page in enumerate(document):
            if (selected is not None and index not in selected) or not page_is_image_only(page):
                output.insert_pdf(document, from_page=index, to_page=index)
                continue

            pixmap = page.get_pixmap(dpi=profile.dpi, alpha=False)
            image = np.frombuffer(pixmap.samples, np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )[:, :, :3].copy()
            try:
                result = engine(image)
            except Exception as error:
                warnings.append(f"page {index + 1}: OCR failed ({error})")
                output.insert_pdf(document, from_page=index, to_page=index)
                continue
            lines = _merge_ocr_line_fragments(_normalise_result(result))
            lines_by_page[index] = tuple(lines)
            recognised += len(lines)
            if not lines:
                warnings.append(f"page {index + 1}: OCR found no text")
                output.insert_pdf(document, from_page=index, to_page=index)
                continue

            layout_regions = _layout_regions(layout_model, image)
            protected = _protected_boxes(layout_regions)
            # A running header/footer stays in the raster. Its presence does
            # not make the non-overlapping body paragraphs unsafe to reflow.
            structural_protected = [
                region.bbox for region in layout_regions
                if region.name in PROTECTED_LAYOUT_CLASSES
                and not (region.name == "abandon" and (
                    region.bbox[3] < image.shape[0] * 0.15
                    or region.bbox[1] > image.shape[0] * 0.90
                ))
            ]
            ordered = _reading_order(lines, float(image.shape[1]))
            page_decision = classify_preserved_page("\n".join(line.text for line in ordered))
            safety_reasons = _page_safety_reasons(
                image,
                ordered,
                structural_protected,
                page_decision,
            )
            reflow_reasons = {
                "multi-column OCR ownership", "too many OCR lines for safe reflow",
                "dense text without reflow headroom",
            }
            requires_proof = bool(reflow_reasons.intersection(safety_reasons))
            safety_reasons = [reason for reason in safety_reasons if reason not in reflow_reasons]
            if safety_reasons:
                warnings.append(
                    f"page {index + 1}: preserved ({', '.join(safety_reasons)})"
                )
                protected_count += len(ordered)
                output.insert_pdf(document, from_page=index, to_page=index)
                continue
            accepted: list[OcrLine] = []
            combined_mask = np.zeros(image.shape[:2], dtype=np.uint8)
            for line in ordered:
                if _overlap_fraction(line.bbox, protected) > 0.01:
                    protected_count += 1
                    continue
                # Keep bullets and list rules in the scan. Redrawing a
                # standalone U+25CF through the prose font produced glyph 0
                # (U+0000) and detached the marker from its paragraph.
                if _is_standalone_marker(line.text):
                    protected_count += 1
                    continue
                if line.confidence < profile.minimum_confidence or _line_rotation(line) > MAX_ROTATION_DEGREES:
                    skipped += 1
                    continue
                mask = _ink_mask(image, line)
                if mask is None:
                    skipped += 1
                    continue
                combined_mask = cv2.bitwise_or(combined_mask, mask)
                accepted.append(line)

            if not accepted:
                reason = page_decision.kind if page_decision is not None else "no safe OCR lines"
                warnings.append(f"page {index + 1}: preserved ({reason})")
                output.insert_pdf(document, from_page=index, to_page=index)
                continue

            scale = 72.0 / profile.dpi
            owned_regions = _owned_reflow_regions(
                accepted,
                layout_regions,
                float(image.shape[1]),
                float(page.rect.height),
                scale,
                paragraph_starts=_raster_bullet_starts(image, accepted),
                preserve_lines=_is_verse_layout(ordered),
            )
            needs_region_ownership = requires_proof
            if needs_region_ownership and owned_regions is None:
                warnings.append(
                    f"page {index + 1}: preserved (ambiguous OCR region ownership)"
                )
                protected_count += len(accepted)
                output.insert_pdf(document, from_page=index, to_page=index)
                continue

            # One final pixel catches anti-aliased glyph rims that survive the
            # per-line mask. This runs only after page-level structure guards,
            # so it cannot eat a protected table rule or formula.
            combined_mask = cv2.dilate(
                combined_mask,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                iterations=1,
            )
            # Navier-Stokes interpolation avoids the bright stippling Telea
            # introduced into aged, non-white paper around erased glyphs.
            cleaned = cv2.inpaint(image, combined_mask, profile.inpaint_radius, cv2.INPAINT_NS)
            if owned_regions is not None and float(np.median(image)) < 245:
                paper = _restore_paper_background(image, accepted, protected)
                if paper is not None:
                    cleaned = paper
            residual = max(_residual_ink_fraction(cleaned, line) for line in accepted)
            if residual > MAX_RESIDUAL_INK:
                warnings.append(
                    f"page {index + 1}: preserved (residual source ink {residual:.1%})"
                )
                protected_count += len(accepted)
                output.insert_pdf(document, from_page=index, to_page=index)
                continue
            ok, encoded = cv2.imencode(".png", cv2.cvtColor(cleaned, cv2.COLOR_RGB2BGR))
            if not ok:
                warnings.append(f"page {index + 1}: could not encode cleaned raster")
                output.insert_pdf(document, from_page=index, to_page=index)
                continue

            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, pixmap=pixmap)
            inserted += _insert_sidecar_lines(target, accepted, scale)
            cleaned_images[index] = encoded.tobytes()
            if owned_regions is not None:
                reflow_regions_by_page[index] = owned_regions
            processed_pages.append(index)

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    output.save(sidecar, garbage=3, deflate=True)
    output.close()
    return OcrPreparation(
        sidecar=sidecar,
        cleaned_images=cleaned_images,
        lines_by_page=lines_by_page,
        reflow_regions_by_page=reflow_regions_by_page,
        pages=tuple(processed_pages),
        recognised_lines=recognised,
        inserted_lines=inserted,
        protected_lines=protected_count,
        skipped_lines=skipped,
        warnings=tuple(warnings),
        seconds=round(time.perf_counter() - started, 3),
    )


def replace_ocr_page_images(
    translated: Path,
    destination: Path,
    cleaned_images: dict[int, bytes],
) -> None:
    """Swap only the normalised scan background; keep translated vector text."""
    with pymupdf.open(translated) as document:
        for page_index, image in cleaned_images.items():
            page = document[page_index]
            candidates = []
            page_area = page.rect.width * page.rect.height
            for info in page.get_image_info(xrefs=True):
                bbox = pymupdf.Rect(info["bbox"])
                coverage = bbox.width * bbox.height / page_area if page_area else 0.0
                if info.get("xref", 0) and coverage >= 0.9:
                    candidates.append((coverage, int(info["xref"])))
            if not candidates:
                raise RuntimeError(f"page {page_index + 1}: translated OCR background is missing")
            _coverage, xref = max(candidates)
            page.replace_image(xref, stream=image)
        document.save(destination, garbage=3, deflate=True)
