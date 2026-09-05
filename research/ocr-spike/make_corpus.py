"""Turn born-digital PDFs into image-only ones, keeping their text as truth.

Hand-labelling OCR ground truth is not affordable at any useful scale. So the
corpus is built the other way round: take pages that already carry a correct
text layer, render them, and rebuild them as pages holding nothing but a raster.
The text that was thrown away is the answer key, complete with per-line boxes,
and it cost nothing.

The bias this introduces has to be stated plainly, because it is large. A
rasterised page is clean anti-aliased ink on white. Real scans arrive skewed,
speckled, bled through from the verso, halftoned, and thresholded to one bit.
Expect an engine to score several times better here than on paper. The corpus
measures whether an engine can work at all and where the pipeline breaks; the
real-scan set is what decides whether it works in the field.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

import oracle  # noqa: E402
import ocrjson  # noqa: E402
import paths  # noqa: E402

# A page with almost no text teaches the scorers nothing and skews rates.
MIN_CHARS = 200


def rasterise(page: pymupdf.Page, dpi: int) -> pymupdf.Document:
    """Rebuild one page as a single full-bleed image, text layer discarded."""
    pix = page.get_pixmap(dpi=dpi)
    out = pymupdf.open()
    new = out.new_page(width=page.rect.width, height=page.rect.height)
    new.insert_image(new.rect, pixmap=pix)
    return out


def build(source: Path, dpi: int, limit: int, stem: str) -> dict:
    truth_pages: list[ocrjson.Page] = []
    kept: list[int] = []
    raster = pymupdf.open()

    with pymupdf.open(source) as doc:
        for index in range(min(limit, doc.page_count)):
            page = doc[index]
            truth = oracle.extract(page)
            chars = sum(len(line.text) for line in truth.lines)
            if chars < MIN_CHARS:
                continue
            # Renumber to the raster document's own page indices so the two
            # stay aligned even when pages are skipped.
            truth.page = len(kept)
            truth_pages.append(truth)
            kept.append(index)
            with rasterise(page, dpi) as one:
                raster.insert_pdf(one)

    raster_path = paths.work_path(paths.CORPUS_RASTER / f"{stem}-{dpi}dpi.pdf")
    raster_path.parent.mkdir(parents=True, exist_ok=True)
    raster.save(raster_path, garbage=3, deflate=True)
    raster.close()

    truth_path = paths.work_path(paths.CORPUS_RASTER / f"{stem}-{dpi}dpi-truth.json")
    ocrjson.dump(truth_pages, truth_path)

    return {
        "source": str(source),
        "stem": stem,
        "dpi": dpi,
        "pages": len(kept),
        "source_pages": kept,
        "lines": sum(len(p.lines) for p in truth_pages),
        "chars": sum(len(line.text) for p in truth_pages for line in p.lines),
        "raster": str(raster_path),
        "truth": str(truth_path),
        "raster_mb": round(raster_path.stat().st_size / 1e6, 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, nargs="+")
    parser.add_argument("--dpi", type=int, nargs="+", default=[200])
    parser.add_argument("--pages", type=int, default=8)
    args = parser.parse_args(argv)

    paths.ensure_tree()
    manifest = []
    for source in args.sources:
        stem = "".join(ch if ch.isalnum() else "-" for ch in source.stem).strip("-").lower()[:40]
        for dpi in args.dpi:
            row = build(source, dpi, args.pages, stem)
            manifest.append(row)
            print(
                f"{row['stem']:<42} {dpi:>4}dpi  {row['pages']:>3} pages  "
                f"{row['lines']:>5} lines  {row['chars']:>6} chars  {row['raster_mb']:>6.2f} MB"
            )

    out = paths.work_path(paths.CORPUS / "manifest.json")
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
