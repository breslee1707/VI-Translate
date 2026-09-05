"""Reproducible structural audit for real handoff translation samples.

This report does not certify visual quality. Review the rendered pages and
record untranslated regions separately; an unchanged scan is not OCR success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pymupdf

MOJIBAKE_SEQUENCES = (
    "\u00e2\u20ac\u201c", "\u00e2\u20ac\u201d", "\u00c2\u00a9",
    "\u00c2\u00ae", "\u00c2\u00b0", "\u00c2\u00b1", "\u00c2\u00b5",
)
FORBIDDEN = re.compile(
    r"\{v\d+\}|</?[bs]\d+>|[\x00\ufffd]|"
    + "|".join(re.escape(value) for value in MOJIBAKE_SEQUENCES)
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def overlap_candidates(spans: list[dict]) -> list[dict]:
    """Flag intersecting ink boxes for review, not an automatic visual verdict.

    Subscripts and intentional overlays can be valid. A passing structural
    gate must never hide these candidates from the reviewer.
    """
    candidates = []
    for index, first in enumerate(spans):
        a = pymupdf.Rect(first["bbox"])
        for second in spans[index + 1:]:
            b = pymupdf.Rect(second["bbox"])
            overlap = a & b
            if (not overlap.is_empty
                    and overlap.width > max(1.0, min(a.width, b.width) * 0.25)
                    and overlap.height > min(a.height, b.height) * 0.35):
                candidates.append({"first": first["text"], "second": second["text"],
                                   "intersection": list(overlap)})
    return candidates


def text_coordinate_bounds(page: pymupdf.Page) -> pymupdf.Rect:
    """Bounds used by PyMuPDF span boxes, before page-level rotation."""
    crop = page.cropbox
    return pymupdf.Rect(0, 0, crop.width, crop.height)


def audit(source: Path, output: Path, pages: set[int]) -> dict:
    """Page numbers in both input and report are one-based."""
    report = {
        "source": str(source.resolve()), "source_sha256": sha256(source),
        "output": str(output.resolve()), "output_sha256": sha256(output),
        "selected_pages": sorted(pages), "visual_review": "pending",
        "pages": [],
    }
    with pymupdf.open(source) as original, pymupdf.open(output) as translated:
        if not pages or min(pages) < 1 or max(pages) > len(original):
            raise ValueError("Selected pages must exist in the source PDF")
        report["page_count_match"] = len(original) == len(translated)
        report["source_page_count"] = len(original)
        report["output_page_count"] = len(translated)
        for index in range(min(len(original), len(translated))):
            src, dst = original[index], translated[index]
            # Render before extracting image dictionaries: decoding image
            # blocks can change MuPDF's cached image interpolation state.
            before, after = src.get_pixmap(alpha=False), dst.get_pixmap(alpha=False)
            spans = [span for block in dst.get_text(
                "dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
            )["blocks"]
                     for line in block.get("lines", []) for span in line["spans"]
                     if span["text"].strip()]
            selected = index + 1 in pages
            span_bounds = text_coordinate_bounds(dst) + (-0.5, -0.5, 0.5, 0.5)
            # Rendering unselected pages also catches accidental collateral
            # changes that text comparison cannot detect.
            row = {
                "page": index + 1, "selected": selected,
                "canvas_match": tuple(src.rect) == tuple(dst.rect),
                "raster_identical": (before.width, before.height, before.samples)
                == (after.width, after.height, after.samples),
                "forbidden_markers": sorted(set(FORBIDDEN.findall(dst.get_text()))),
                "out_of_canvas": [span["text"] for span in spans
                                  if not span_bounds.contains(pymupdf.Rect(span["bbox"]))],
                "fonts": sorted({span["font"] for span in spans}),
                "font_sizes": sorted({round(span["size"], 2) for span in spans}),
                "overlap_candidates": overlap_candidates(spans) if selected else [],
            }
            report["pages"].append(row)
    report["structural_gate"] = bool(report["page_count_match"] and all(
        row["canvas_match"] and (
            not row["forbidden_markers"] and not row["out_of_canvas"]
            if row["selected"] else row["raster_identical"]
        ) for row in report["pages"]
    ))
    report["note"] = "Structural checks do not measure translation coverage or visual overlap."
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pages", required=True, help="One-based pages, comma-separated")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--translations", type=Path)
    parser.add_argument("--render-dir", type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Report already exists; choose a new run path")
    report = audit(args.source, args.output, {int(value) for value in args.pages.split(",")})
    if args.translations:
        report["translations_sha256"] = sha256(args.translations)
    if args.render_dir:
        renderer = shutil.which("pdftoppm")
        if not renderer:
            parser.error("pdftoppm is required for sample render QA")
        args.render_dir.mkdir(parents=True, exist_ok=False)
        for name, path in (("source", args.source), ("output", args.output)):
            subprocess.run([renderer, "-r", "150", "-png", str(path),
                            str(args.render_dir / name)], check=True, capture_output=True)
        report["render_directory"] = str(args.render_dir.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report), "structural_gate": report["structural_gate"]}))


if __name__ == "__main__":
    main()
