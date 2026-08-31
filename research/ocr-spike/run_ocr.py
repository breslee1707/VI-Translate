"""Run a recogniser over a rasterised PDF and normalise what it returns.

Every engine is hidden behind the same adapter shape and emits the schema in
ocrjson.py, so the sidecar writer and the scorers never learn which one
produced a result. That is what keeps the bake-off honest.

Adapters are registered lazily: importing a recogniser costs seconds and pulls
native libraries, so an engine nobody asked for on the command line is never
loaded at all.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402

import ocrjson  # noqa: E402
import paths  # noqa: E402


def page_image(page: pymupdf.Page, dpi: int) -> tuple[np.ndarray, float]:
    """Render a page and return it with the factor converting pixels to points."""
    pix = page.get_pixmap(dpi=dpi)
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    return image, 72.0 / dpi


class RapidOCR:
    """PP-OCR under onnxruntime -- the candidate that fits the existing stack.

    Two configurations matter and they are not close. The wheel ships only the
    Chinese recogniser, which is trained on a script that does not use spaces
    and therefore returns English as one run-on token. The English recogniser
    has to be fetched separately, which costs the offline-install property the
    engine was chosen for.
    """

    def __init__(self, rec_model: str | None = None, rec_dict: str | None = None) -> None:
        from rapidocr_onnxruntime import RapidOCR as Engine

        options: dict = {}
        if rec_model:
            options["Rec.model_path"] = rec_model
        if rec_dict:
            options["Rec.rec_keys_path"] = rec_dict
        self.engine = Engine(**options)
        self.label = "rapidocr-en" if rec_model else "rapidocr-ch"

    def __call__(self, image: np.ndarray, scale: float) -> list[ocrjson.Line]:
        result, _ = self.engine(image)
        lines: list[ocrjson.Line] = []
        for box, text, confidence in result or []:
            points = np.asarray(box, dtype=float)
            x0, y0 = points[:, 0].min() * scale, points[:, 1].min() * scale
            x1, y1 = points[:, 0].max() * scale, points[:, 1].max() * scale
            if not text.strip() or x1 - x0 <= 1 or y1 - y0 <= 1:
                continue
            lines.append(
                ocrjson.Line(text=text.strip(), bbox=(x0, y0, x1, y1), conf=float(confidence))
            )
        return lines


class Tesseract:
    """The Latin baseline. Word-level boxes, but an external binary to install."""

    def __init__(self) -> None:
        import pytesseract

        self.pytesseract = pytesseract
        self.label = "tesseract"

    def __call__(self, image: np.ndarray, scale: float) -> list[ocrjson.Line]:
        from pytesseract import Output

        data = self.pytesseract.image_to_data(image, output_type=Output.DICT)
        # Tesseract reports words; group them back into its own lines so every
        # engine hands the sidecar writer the same granularity.
        grouped: dict[tuple, list[int]] = {}
        for index, text in enumerate(data["text"]):
            if not text.strip() or int(data["conf"][index]) < 0:
                continue
            key = (data["page_num"][index], data["block_num"][index],
                   data["par_num"][index], data["line_num"][index])
            grouped.setdefault(key, []).append(index)

        lines: list[ocrjson.Line] = []
        for indices in grouped.values():
            words = [data["text"][i].strip() for i in indices]
            x0 = min(data["left"][i] for i in indices) * scale
            y0 = min(data["top"][i] for i in indices) * scale
            x1 = max(data["left"][i] + data["width"][i] for i in indices) * scale
            y1 = max(data["top"][i] + data["height"][i] for i in indices) * scale
            confidence = sum(float(data["conf"][i]) for i in indices) / len(indices) / 100.0
            lines.append(
                ocrjson.Line(text=" ".join(words), bbox=(x0, y0, x1, y1), conf=confidence)
            )
        return lines


def build(name: str) -> object:
    if name == "rapidocr-ch":
        return RapidOCR()
    if name == "rapidocr-en":
        models = paths.work_path("models")
        return RapidOCR(
            rec_model=str(models / "en_PP-OCRv3_rec_infer.onnx"),
            rec_dict=str(models / "en_dict.txt"),
        )
    if name == "tesseract":
        return Tesseract()
    raise SystemExit(f"unknown engine: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", type=Path, required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args(argv)

    paths.ensure_tree()
    engine = build(args.engine)
    raster = paths.work_path(args.raster)

    pages: list[ocrjson.Page] = []
    timings: list[float] = []
    with pymupdf.open(raster) as doc:
        for index in range(doc.page_count):
            page = doc[index]
            image, scale = page_image(page, args.dpi)
            started = time.time()
            lines = engine(image, scale)
            timings.append(time.time() - started)
            result = ocrjson.Page(
                page=index, width=page.rect.width, height=page.rect.height
            )
            result.lines = ocrjson.reading_order(lines, page.rect.width)
            pages.append(result)

    out = paths.work_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ocrjson.dump(pages, out)

    # The first page pays for building the inference session; quoting it as a
    # per-page cost would libel the engine.
    steady = timings[1:] or timings
    stats = {
        "engine": args.engine,
        "raster": str(raster),
        "dpi": args.dpi,
        "pages": len(pages),
        "lines": sum(len(p.lines) for p in pages),
        "chars": sum(len(line.text) for p in pages for line in p.lines),
        "first_page_s": round(timings[0], 2),
        "seconds_per_page": round(sum(steady) / len(steady), 2),
    }
    out.with_suffix(".stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(
        f"{args.engine:<14} {stats['pages']:>2}p {stats['lines']:>5} lines "
        f"{stats['chars']:>6} chars  first {stats['first_page_s']:>5.2f}s  "
        f"steady {stats['seconds_per_page']:>5.2f}s/page -> {out.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
