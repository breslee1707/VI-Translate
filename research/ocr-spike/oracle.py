"""A perfect OCR result, taken from a born-digital page's own text layer.

This is the spike's ceiling. Every engine is scored against it, and Phase B
runs it end to end: if a recogniser that makes no mistakes at all cannot get a
clean translated PDF out of the pipeline, the sidecar shape is wrong and no
real engine can rescue it.

Two variants, because they answer different questions:

  exact  Uses the span's own baseline and font size. Tests the pipeline alone.
  bbox   Uses only the line's text and bounding box -- exactly what a
         recogniser gives you -- and re-derives size and baseline through the
         same fitting code the real engines will use. Tests that fitting too.

Comparing the two separates "the pipeline cannot digest a sidecar" from "my
fitting maths is wrong", which are very different verdicts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

import ocrjson  # noqa: E402
import paths  # noqa: E402

# Ground truth with these in it is dirty, not hard: a broken ToUnicode map or a
# PUA bullet would be scored as an OCR error that no engine actually made.
DIRTY = ("\ufffd", "(cid:")


def is_clean(text: str) -> bool:
    if any(marker in text for marker in DIRTY):
        return False
    private = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
    return private <= max(1, len(text) // 100)


def extract(page: pymupdf.Page) -> ocrjson.Page:
    result = ocrjson.Page(page=page.number, width=page.rect.width, height=page.rect.height)
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(span["text"] for span in spans).strip()
            if not text or not is_clean(text):
                continue
            x0 = min(span["bbox"][0] for span in spans)
            y0 = min(span["bbox"][1] for span in spans)
            x1 = max(span["bbox"][2] for span in spans)
            y1 = max(span["bbox"][3] for span in spans)
            if x1 - x0 <= 1 or y1 - y0 <= 1:
                continue
            result.lines.append(
                ocrjson.Line(
                    text=text,
                    bbox=(x0, y0, x1, y1),
                    conf=1.0,
                    # span["origin"] is the baseline point: the truth a
                    # recogniser can never hand you.
                    baseline=float(spans[0]["origin"][1]),
                    size=float(spans[0]["size"]),
                )
            )
    result.lines = ocrjson.reading_order(result.lines, page.rect.width)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=0, help="0 = all")
    args = parser.parse_args(argv)

    paths.ensure_tree()
    with pymupdf.open(args.pdf) as doc:
        limit = args.pages or doc.page_count
        pages = [extract(doc[i]) for i in range(min(limit, doc.page_count))]

    out = paths.work_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ocrjson.dump(pages, out)
    lines = sum(len(p.lines) for p in pages)
    chars = sum(len(ln.text) for p in pages for ln in p.lines)
    print(f"{args.pdf.name}: {len(pages)} pages, {lines} lines, {chars} chars -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
