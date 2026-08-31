"""Would the two recommended fixes actually work?

A recommendation nobody has measured is a guess. Both fixes this spike proposes
live in app code the spike is not allowed to touch, so instead of implementing
them it computes what they *would* decide, on the same documents, and reports
whether that is an improvement or a new way to be wrong.

  tile-aware detection   pdf2zh/rules.py:398-411 asks whether one image covers
                         half the page. A real scan emitted as tiles fails
                         that test, gets no backing rectangles, and the
                         translation prints on top of the original. The
                         candidate asks the same question of the tiles' union.
                         The risk to check is the opposite error: a
                         born-digital page rich in figures being called a scan
                         and having its text needlessly covered.

  background sampling    converter.py:831-849 fills its backing rectangle with
                         white. Where the page under it is not white, the
                         background dies. The candidate samples what is there
                         instead. Whether that can work at all depends on
                         something measurable: is the region behind a
                         paragraph a flat colour, or a gradient or picture that
                         no single fill could stand in for?
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402

import paths  # noqa: E402
from pdf2zh.rules import is_scanned_page  # noqa: E402

# converter.py:834-841 pads its rectangle by 3pt; the sampler has to look at
# the same region the fill would cover.
PAD = 3.0
# Two colours within this Euclidean RGB distance read as the same flat tone.
FLAT_TOLERANCE = 12.0
# Ink is dark; sampling it would tint the fill towards the text being removed.
INK_MAX = 160


def union_image_coverage(blocks: list, page_area: float) -> float:
    """Share of the page covered by the union of every image block.

    A union rather than a sum: overlapping tiles would otherwise total more
    than the page and call a mostly-blank sheet a scan.
    """
    boxes = [b["bbox"] for b in blocks if b.get("type") == 1]
    if not boxes or page_area <= 0:
        return 0.0
    # Rasterise the union coarsely; exact geometry is not worth the code here.
    grid = np.zeros((200, 200), dtype=bool)
    x_span = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    del x_span  # kept for clarity of intent; the grid is page-relative below
    return _union_fraction(boxes, blocks, page_area, grid)


def _union_fraction(boxes: list, blocks: list, page_area: float, grid: np.ndarray) -> float:
    # Page-relative grid: the page rect is recovered from the caller's area and
    # the widest block extent, so this stays independent of page size.
    width = max(b[2] for b in boxes)
    height = max(b[3] for b in boxes)
    if width <= 0 or height <= 0:
        return 0.0
    rows, cols = grid.shape
    for x0, y0, x1, y1 in boxes:
        c0 = int(np.clip(x0 / width * cols, 0, cols - 1))
        c1 = int(np.clip(x1 / width * cols, 0, cols))
        r0 = int(np.clip(y0 / height * rows, 0, rows - 1))
        r1 = int(np.clip(y1 / height * rows, 0, rows))
        grid[r0:r1, c0:c1] = True
    covered = grid.mean()
    # Scale back to the real page: the grid spans the blocks' bounding box.
    return float(covered * (width * height) / page_area)


def tile_aware_is_scanned(page: pymupdf.Page) -> bool:
    blocks = page.get_text("dict")["blocks"]
    area = page.rect.width * page.rect.height
    return union_image_coverage(blocks, area) > 0.5


def background_flatness(
    page: pymupdf.Page, regions: list[tuple] | None = None, dpi: int = 150
) -> list[dict]:
    """For each text region, ask whether one colour could replace its background.

    On a born-digital page the regions are the page's own text blocks. On a
    scan there are none -- that is what makes it a scan -- so the caller passes
    the recogniser's line boxes instead, which is exactly where the backing
    rectangles will land.

    For each region the non-ink pixels are gathered and their spread measured:
    a tight spread means a sampled fill would be invisible, a wide one means
    the region sits on a gradient or a picture that no flat fill can stand in
    for.
    """
    pix = page.get_pixmap(dpi=dpi)
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    scale = dpi / 72.0

    if regions is None:
        regions = [
            tuple(block["bbox"])
            for block in page.get_text("dict")["blocks"]
            if block.get("type") == 0
        ]

    results = []
    for x0, y0, x1, y1 in regions:
        c0 = int(max(0, (x0 - PAD) * scale))
        r0 = int(max(0, (y0 - PAD) * scale))
        c1 = int(min(image.shape[1], (x1 + PAD) * scale))
        r1 = int(min(image.shape[0], (y1 + PAD) * scale))
        if c1 - c0 < 4 or r1 - r0 < 4:
            continue
        patch = image[r0:r1, c0:c1].reshape(-1, 3).astype(float)
        background = patch[patch.mean(axis=1) > INK_MAX]
        if len(background) < 16:
            continue
        # Glyph edges are anti-aliased, so a band of mid-tones sits between the
        # ink and the paper. Those pixels belong to the text, not the
        # background, and including them makes every region look like a
        # gradient. Keeping the brighter half of what survives the ink cut
        # measures the background the fill actually has to match.
        brightness = background.mean(axis=1)
        background = background[brightness >= np.percentile(brightness, 50)]
        if len(background) < 16:
            continue
        median = np.median(background, axis=0)
        spread = float(np.percentile(np.linalg.norm(background - median, axis=1), 90))
        results.append(
            {
                "flat": spread <= FLAT_TOLERANCE,
                "spread_p90": round(spread, 1),
                "is_white": bool(median.min() >= 245),
                "median_rgb": [int(v) for v in median],
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, nargs="*", default=[], help="genuine scans")
    parser.add_argument("--born-digital", type=Path, nargs="*", default=[])
    parser.add_argument(
        "--ocr",
        type=Path,
        nargs="*",
        default=[],
        help="recogniser output for each --scan, in the same order; supplies the "
        "regions a scan cannot supply itself",
    )
    args = parser.parse_args(argv)

    paths.ensure_tree()
    import ocrjson

    boxes_for: dict[str, dict[int, list[tuple]]] = {}
    for scan, ocr in zip(args.scan, args.ocr):
        pages = ocrjson.load(paths.work_path(ocr))
        boxes_for[str(scan)] = {p.page: [tuple(line.bbox) for line in p.lines] for p in pages}

    rows = []
    for kind, files in (("scan", args.scan), ("born-digital", args.born_digital)):
        for path in files:
            with pymupdf.open(paths.work_path(path)) as doc:
                for index in range(doc.page_count):
                    page = doc[index]
                    blocks = page.get_text("dict")["blocks"]
                    area = page.rect.width * page.rect.height
                    regions = boxes_for.get(str(path), {}).get(index)
                    flat = background_flatness(page, regions)
                    rows.append(
                        {
                            "file": Path(path).name,
                            "kind": kind,
                            "page": index + 1,
                            "current": is_scanned_page(blocks, area),
                            "tile_aware": tile_aware_is_scanned(page),
                            "coverage": round(union_image_coverage(blocks, area), 3),
                            "regions": len(flat),
                            "flat_regions": sum(1 for r in flat if r["flat"]),
                            "non_white_flat": sum(
                                1 for r in flat if r["flat"] and not r["is_white"]
                            ),
                            "non_white": sum(1 for r in flat if not r["is_white"]),
                        }
                    )

    print("=== FIX 1: tile-aware scan detection ===")
    print(f"{'file':<24} {'kind':<13} {'pg':>3} {'cover':>6} {'current':>8} {'tile-aware':>11}")
    for row in rows:
        mark = "  <-- rescued" if row["tile_aware"] and not row["current"] else ""
        if row["kind"] == "born-digital" and row["tile_aware"] and not row["current"]:
            mark = "  <-- FALSE POSITIVE"
        print(
            f"{row['file'][:24]:<24} {row['kind']:<13} {row['page']:>3} "
            f"{row['coverage']:>6.2f} {str(row['current']):>8} {str(row['tile_aware']):>11}{mark}"
        )

    scans = [r for r in rows if r["kind"] == "scan"]
    born = [r for r in rows if r["kind"] == "born-digital"]
    if scans:
        now = sum(r["current"] for r in scans)
        fixed = sum(r["tile_aware"] for r in scans)
        print(f"\n  scans covered: {now}/{len(scans)} now -> {fixed}/{len(scans)} tile-aware")
    if born:
        false_positive = sum(1 for r in born if r["tile_aware"] and not r["current"])
        print(f"  born-digital false positives: {false_positive}/{len(born)}")

    print("\n=== FIX 2: sampling the background instead of filling white ===")
    print(f"{'file':<24} {'pg':>3} {'regions':>8} {'flat':>6} {'non-white':>10} {'nw+flat':>8}")
    for row in rows:
        print(
            f"{row['file'][:24]:<24} {row['page']:>3} {row['regions']:>8} "
            f"{row['flat_regions']:>6} {row['non_white']:>10} {row['non_white_flat']:>8}"
        )
    total = sum(r["regions"] for r in rows) or 1
    non_white = sum(r["non_white"] for r in rows)
    rescuable = sum(r["non_white_flat"] for r in rows)
    print(f"\n  regions on a non-white background : {non_white}/{total} = {non_white/total:.1%}")
    print(
        f"  of those, flat enough for one fill: {rescuable}/{non_white or 1} = "
        f"{rescuable/max(1,non_white):.1%}"
    )

    out = paths.run_dir("fix-feasibility") / "feasibility.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
