"""Build deterministic scan variants and OCR truth from the locked corpus."""

from __future__ import annotations

import hashlib
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pymupdf  # noqa: E402

import benchmark_paths as paths  # noqa: E402
import ocrjson  # noqa: E402
import oracle  # noqa: E402

VARIANTS = ("clean-200", "jpeg-150", "skew-blur", "noise-contrast", "tiled-200")
MIN_MEDIAN_CHARACTERS = 200
VARIANT_MANIFEST = paths.ROOT / "variants.lock.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    return np.frombuffer(pixmap.samples, np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )[:, :, :3].copy()


def encode(image: np.ndarray, suffix: str, quality: int | None = None) -> bytes:
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if quality is not None else []
    ok, result = cv2.imencode(
        suffix,
        cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
        params,
    )
    if not ok:
        raise RuntimeError(f"could not encode {suffix} benchmark image")
    return result.tobytes()


def transformed(page: pymupdf.Page, variant: str, seed: int) -> tuple[np.ndarray, str, int | None]:
    dpi = 150 if variant == "jpeg-150" else 200
    image = render(page, dpi)
    if variant == "skew-blur":
        height, width = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 1.2, 1.0)
        image = cv2.warpAffine(
            image,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
        image = cv2.GaussianBlur(image, (3, 3), 0.8)
    elif variant == "noise-contrast":
        generator = np.random.default_rng(seed)
        adjusted = image.astype(np.float32) * 0.72 + 32.0
        noise = generator.normal(0, 8.0, image.shape)
        image = np.clip(adjusted + noise, 0, 255).astype(np.uint8)
    suffix = ".jpg" if variant == "jpeg-150" else ".png"
    quality = 45 if variant == "jpeg-150" else None
    return image, suffix, quality


def add_raster_page(
    output: pymupdf.Document,
    source_page: pymupdf.Page,
    image: np.ndarray,
    suffix: str,
    quality: int | None,
    tiled: bool,
) -> None:
    page = output.new_page(width=source_page.rect.width, height=source_page.rect.height)
    if not tiled:
        page.insert_image(page.rect, stream=encode(image, suffix, quality))
        return
    rows = cols = 4
    height, width = image.shape[:2]
    for row in range(rows):
        y0 = round(row * height / rows)
        y1 = round((row + 1) * height / rows)
        for column in range(cols):
            x0 = round(column * width / cols)
            x1 = round((column + 1) * width / cols)
            tile = image[y0:y1, x0:x1]
            rect = pymupdf.Rect(
                column * page.rect.width / cols,
                row * page.rect.height / rows,
                (column + 1) * page.rect.width / cols,
                (row + 1) * page.rect.height / rows,
            )
            page.insert_image(rect, stream=encode(tile, ".png"))


def build_document(row: dict, source: Path, variant: str) -> dict:
    destination_dir = paths.safe_path(paths.VARIANTS / variant)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = paths.safe_path(destination_dir / f"{row['id']}.pdf")
    output = pymupdf.open()
    seed_base = int(hashlib.sha256(row["id"].encode()).hexdigest()[:8], 16)
    with pymupdf.open(source) as document:
        for index, page in enumerate(document):
            image, suffix, quality = transformed(page, variant, seed_base + index)
            add_raster_page(
                output,
                page,
                image,
                suffix,
                quality,
                tiled=variant == "tiled-200",
            )
    output.save(destination, garbage=3, deflate=True)
    output.close()
    return {
        "id": row["id"],
        "variant": variant,
        "path": str(destination.relative_to(paths.REPO_ROOT)),
        "sha256": sha256(destination),
        "pages": len(row["selected_pages"]),
        "split": row["split"],
        "features": row["features"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--truth-only", action="store_true", help="refresh truth without rebuilding PDFs"
    )
    parser.add_argument(
        "--real-only", action="store_true", help="add image-only real-scan variants only"
    )
    args = parser.parse_args(argv)
    paths.ensure_tree()
    if not paths.LOCK.is_file():
        raise RuntimeError("run fetch_benchmark.py before building variants")
    rows = json.loads(paths.LOCK.read_text(encoding="utf-8"))
    variants = []
    existing_variants = (
        json.loads(VARIANT_MANIFEST.read_text(encoding="utf-8"))
        if VARIANT_MANIFEST.is_file()
        else []
    )
    eligible_documents = 0
    for position, row in enumerate(rows, 1):
        source = paths.safe_path(paths.REPO_ROOT / row["selected_path"])
        with pymupdf.open(source) as document:
            truth_pages = [oracle.extract(page) for page in document]
            character_counts = [
                sum(len(line.text) for line in truth.lines) for truth in truth_pages
            ]
        truth_path = paths.safe_path(paths.TRUTH / f"{row['id']}.json")
        ocrjson.dump(truth_pages, truth_path)
        row["truth_path"] = str(truth_path.relative_to(paths.REPO_ROOT))
        row["truth_sha256"] = sha256(truth_path)
        row["median_characters"] = int(np.median(character_counts)) if character_counts else 0
        source_is_scan = "scan" in row["bucket"]
        row["truth_status"] = (
            "provisional-source-ocr" if source_is_scan else "exact-text-layer"
        )
        if source_is_scan or row["median_characters"] < MIN_MEDIAN_CHARACTERS:
            reason = "scan truth needs manual review" if source_is_scan else "too little text"
            print(
                f"[{position:02}/{len(rows):02}] {row['id']}: real/image-only "
                f"({row['median_characters']} median chars; {reason}), no synthetic variants"
            )
            if args.real_only and source_is_scan:
                source = paths.safe_path(paths.REPO_ROOT / row["selected_path"])
                variants.append(build_document(row, source, "real-scan"))
            continue
        if args.real_only:
            continue
        eligible_documents += 1
        if args.truth_only:
            continue
        print(f"[{position:02}/{len(rows):02}] {row['id']}: {', '.join(VARIANTS)}")
        for variant in VARIANTS:
            variants.append(build_document(row, source, variant))

    paths.LOCK.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.real_only:
        variants = [
            item for item in existing_variants if item.get("variant") != "real-scan"
        ] + variants
    if not args.truth_only:
        VARIANT_MANIFEST.write_text(
            json.dumps(variants, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        variants = existing_variants
    pages = sum(item["pages"] for item in variants)
    print(
        f"Built {len(variants)} variant PDFs / {pages} pages from "
        f"{eligible_documents} text-grounded documents -> {VARIANT_MANIFEST}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
