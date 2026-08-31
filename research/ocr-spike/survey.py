"""Classify candidate PDFs into born-digital sources and real scans.

The spike needs two corpora with opposite properties: born-digital pages, whose
discarded text layer becomes free ground truth, and genuine scans, which carry
the skew, speckle and tiling that rasterisation can never reproduce. This
script sorts a pile of PDFs into those buckets using the engine's own
predicates, so the classification matches what the pipeline will later decide.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pymupdf  # noqa: E402

import paths  # noqa: E402
from pdf2zh.rules import is_scanned_page, page_has_image  # noqa: E402

# Enough pages to characterise a book without opening all 900 of them.
SAMPLE_PAGES = 12


def survey_one(path: Path) -> dict:
    row: dict = {"path": str(path), "name": path.name}
    try:
        doc = pymupdf.open(path)
    except Exception as error:  # noqa: BLE001 - a broken file is a finding
        row["error"] = str(error)[:120]
        return row

    with doc:
        row["pages"] = doc.page_count
        step = max(1, doc.page_count // SAMPLE_PAGES)
        indices = list(range(0, doc.page_count, step))[:SAMPLE_PAGES]
        chars, scanned, imaged, tiled, rotated, cropped = [], 0, 0, 0, 0, 0
        for index in indices:
            page = doc[index]
            blocks = page.get_text("dict")["blocks"]
            area = page.rect.width * page.rect.height
            chars.append(len(page.get_text().strip()))
            image_blocks = [b for b in blocks if b.get("type") == 1]
            if is_scanned_page(blocks, area):
                scanned += 1
            if page_has_image(blocks):
                imaged += 1
            # The tiled case rules.py:386-395 warns about: images present, but
            # none big enough for is_scanned_page to fire.
            if image_blocks and not is_scanned_page(blocks, area):
                tiled += 1
            if page.rotation:
                rotated += 1
            if page.cropbox != page.mediabox:
                cropped += 1

        sampled = len(indices)
        row.update(
            sampled=sampled,
            median_chars=sorted(chars)[len(chars) // 2],
            min_chars=min(chars),
            scanned_pages=scanned,
            imaged_pages=imaged,
            tiled_pages=tiled,
            rotated_pages=rotated,
            cropped_pages=cropped,
        )
        # Born-digital: real text everywhere, no page-filling image.
        # Real scan: an image on every sampled page and almost no text.
        if row["min_chars"] > 400 and scanned == 0:
            row["bucket"] = "born-digital"
        elif imaged == sampled and row["median_chars"] < 100:
            row["bucket"] = "real-scan"
        elif imaged and row["median_chars"] >= 100:
            row["bucket"] = "mixed"
        else:
            row["bucket"] = "unclear"
    return row


def main(argv: list[str]) -> int:
    paths.ensure_tree()
    candidates: list[Path] = []
    for argument in argv:
        target = Path(argument).expanduser()
        candidates.extend(sorted(target.glob("*.pdf")) if target.is_dir() else [target])

    rows = [survey_one(path) for path in candidates]
    out = paths.run_dir("survey") / "survey.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    width = max((len(r["name"]) for r in rows), default=10)
    print(f"{'bucket':<13} {'pages':>5} {'medchar':>8} {'scan':>5} {'tile':>5} {'rot':>4}  name")
    for row in sorted(rows, key=lambda r: (r.get("bucket", "zz"), r["name"])):
        if "error" in row:
            print(f"{'ERROR':<13} {'':>5} {'':>8} {'':>5} {'':>5} {'':>4}  {row['name']}: {row['error']}")
            continue
        print(
            f"{row['bucket']:<13} {row['pages']:>5} {row['median_chars']:>8} "
            f"{row['scanned_pages']:>2}/{row['sampled']:<2} {row['tiled_pages']:>2}/{row['sampled']:<2} "
            f"{row['rotated_pages']:>4}  {row['name'][:width]}"
        )
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
