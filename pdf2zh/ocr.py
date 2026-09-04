"""Prepare image-only pages for the existing layout-preserving translator.

OCR is deliberately a sidecar instead of a second PDF renderer. The original
scan remains visible while DocLayout classifies it, recognised prose is added
as invisible text, and the translator emits its usual vector text. Only after
translation succeeds is the full-page scan image replaced with an inpainted
copy. Protected structures never enter the cleanup mask.
"""

from __future__ import annotations

import math
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


@dataclass
class OcrPreparation:
    sidecar: Path
    cleaned_images: dict[int, bytes] = field(default_factory=dict)
    lines_by_page: dict[int, tuple[OcrLine, ...]] = field(default_factory=dict)
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
    left = [line for line in items if (line.bbox[0] + line.bbox[2]) / 2 < midpoint]
    right = [line for line in items if line not in left]
    straddling = sum(
        1
        for line in items
        if line.bbox[0] < midpoint * 0.8 and line.bbox[2] > midpoint * 1.2
    )
    if len(left) >= 3 and len(right) >= 3 and straddling <= max(1, len(items) // 10):
        return sorted(left, key=lambda line: (line.bbox[1], line.bbox[0])) + sorted(
            right, key=lambda line: (line.bbox[1], line.bbox[0])
        )
    return sorted(items, key=lambda line: (line.bbox[1], line.bbox[0]))


def _line_rotation(line: OcrLine) -> float:
    first, second = line.polygon[0], line.polygon[1]
    return abs(math.degrees(math.atan2(second[1] - first[1], second[0] - first[0])))


def _protected_boxes(model: object, image: np.ndarray) -> list[tuple[float, float, float, float]]:
    prediction = model.predict(
        image[:, :, ::-1], imgsz=max(32, int(image.shape[0] / 32) * 32)
    )[0]
    protected = []
    for detection in prediction.boxes:
        name = prediction.names[int(detection.cls)]
        if name not in PROTECTED_LAYOUT_CLASSES:
            continue
        x0, y0, x1, y1 = (float(value) for value in detection.xyxy.squeeze())
        protected.append((x0, y0, x1, y1))
    return protected


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
) -> list[str]:
    """Identify page-level structures that must never be partially erased."""
    reasons: list[str] = []
    if page_decision is not None:
        reasons.append(page_decision.kind.lower())
    if protected:
        reasons.append("protected layout region")

    short_lines = sum(len(line.text) <= 3 for line in lines)
    one_character_lines = sum(len(line.text) == 1 for line in lines)
    if lines and short_lines / len(lines) > 0.15:
        reasons.append("fragmented OCR lines")
    if lines and one_character_lines / len(lines) > 0.08:
        reasons.append("single-character OCR fragments")

    formula_chars = sum(
        sum(character in "=+−-*/^_<>≤≥√∫Σπαβγδ" for character in line.text)
        for line in lines
    )
    digit_chars = sum(sum(character.isdigit() for character in line.text) for line in lines)
    total_chars = sum(len(line.text) for line in lines)
    if total_chars and (formula_chars / total_chars > 0.10 or digit_chars / total_chars > 0.30):
        reasons.append("formula or numeric-heavy page")

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

    rule_count = long_components(horizontal, True) + long_components(vertical, False)
    if rule_count >= 2:
        reasons.append("dense rules or form grid")
    return list(dict.fromkeys(reasons))


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
            lines = _normalise_result(result)
            lines_by_page[index] = tuple(lines)
            recognised += len(lines)
            if not lines:
                warnings.append(f"page {index + 1}: OCR found no text")
                output.insert_pdf(document, from_page=index, to_page=index)
                continue

            protected = _protected_boxes(layout_model, image)
            ordered = _reading_order(lines, float(image.shape[1]))
            page_decision = classify_preserved_page("\n".join(line.text for line in ordered))
            safety_reasons = _page_safety_reasons(image, ordered, protected, page_decision)
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
                if _overlap_fraction(line.bbox, protected) >= 0.5:
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

            cleaned = cv2.inpaint(image, combined_mask, profile.inpaint_radius, cv2.INPAINT_TELEA)
            ok, encoded = cv2.imencode(".png", cv2.cvtColor(cleaned, cv2.COLOR_RGB2BGR))
            if not ok:
                warnings.append(f"page {index + 1}: could not encode cleaned raster")
                output.insert_pdf(document, from_page=index, to_page=index)
                continue

            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, pixmap=pixmap)
            scale = 72.0 / profile.dpi
            inserted += _insert_sidecar_lines(target, accepted, scale)
            cleaned_images[index] = encoded.tobytes()
            processed_pages.append(index)

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    output.save(sidecar, garbage=3, deflate=True)
    output.close()
    return OcrPreparation(
        sidecar=sidecar,
        cleaned_images=cleaned_images,
        lines_by_page=lines_by_page,
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
