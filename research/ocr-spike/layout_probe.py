"""Phase A: does the layout model survive a scanned page?

The cheapest kill signal in the whole spike, and it needs no OCR engine. If
DocLayout-YOLO calls a scanned page one big `figure`, every glyph an OCR pass
recovers there is classed as a formula, `translatable_segments` reaches zero,
and `scripts/translate_pdf.py:413` refuses the document the tool just OCR'd.
No recogniser, however accurate, can survive that.

Two questions, deliberately separated:

  round-trip  A born-digital page, rasterised and rebuilt as an image-only
              page, then re-rendered. Isolates what resampling alone costs.
  real-scan   A genuine scan. This is where the risk actually lives: real scans
              carry grey grounds, speckle, skew and photographic texture that
              rasterising a clean page never reproduces.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pymupdf  # noqa: E402

import layout  # noqa: E402
import paths  # noqa: E402


def rebuild_image_only(page: pymupdf.Page, dpi: int) -> pymupdf.Document:
    """Return a one-page document holding only a raster of `page`.

    This is what a scanner would have produced from the same sheet: the text
    layer is gone and a single image covers the page. Note that a single image
    is the friendly case -- `pdf2zh/rules.py:386-395` warns that real scanners
    routinely emit dozens of tiles instead, which the real-scan corpus covers.
    """
    pix = page.get_pixmap(dpi=dpi)
    out = pymupdf.open()
    new = out.new_page(width=page.rect.width, height=page.rect.height)
    new.insert_image(new.rect, pixmap=pix)
    return out


def probe_document(model: object, path: Path, kind: str, dpi: int, limit: int) -> list[dict]:
    rows: list[dict] = []
    with pymupdf.open(path) as doc:
        step = max(1, doc.page_count // limit)
        for index in list(range(0, doc.page_count, step))[:limit]:
            page = doc[index]
            original = layout.class_map(model, layout.page_pixmap(page))
            row = {
                "file": path.name,
                "kind": kind,
                "page": index + 1,
                "dpi": dpi,
                "orig_detections": original.detections,
                "orig_protected": round(original.protected_fraction, 4),
                "orig_all_protected": original.is_all_protected,
                "orig_names": list(original.names),
            }
            if kind == "round-trip":
                with rebuild_image_only(page, dpi) as rebuilt:
                    raster = layout.class_map(model, layout.page_pixmap(rebuilt[0]))
                row.update(
                    raster_detections=raster.detections,
                    raster_protected=round(raster.protected_fraction, 4),
                    raster_all_protected=raster.is_all_protected,
                    raster_names=list(raster.names),
                    agreement=round(layout.agreement(original, raster), 4),
                )
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--born-digital", type=Path, nargs="*", default=[])
    parser.add_argument("--real-scan", type=Path, nargs="*", default=[])
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--pages", type=int, default=8, help="pages sampled per file")
    args = parser.parse_args(argv)

    paths.ensure_tree()
    sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))
    from translate_pdf import load_layout_model  # noqa: E402

    model = load_layout_model()

    rows: list[dict] = []
    for path in args.born_digital:
        rows += probe_document(model, path, "round-trip", args.dpi, args.pages)
    for path in args.real_scan:
        rows += probe_document(model, path, "real-scan", args.dpi, args.pages)

    out = paths.run_dir("layout-probe")
    (out / f"probe-dpi{args.dpi}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    print(f"\n{'=' * 78}\nPHASE A - layout on rasterised and scanned pages\n{'=' * 78}")
    for kind in ("round-trip", "real-scan"):
        group = [r for r in rows if r["kind"] == kind]
        if not group:
            continue
        print(f"\n--- {kind} ({len(group)} pages) ---")
        for row in group:
            flag_o = " ALL-PROTECTED" if row["orig_all_protected"] else ""
            line = (
                f"  {row['file'][:34]:<34} p{row['page']:<4} "
                f"det={row['orig_detections']:<3} prot={row['orig_protected']:.2f}{flag_o}"
            )
            if kind == "round-trip":
                flag_r = " ALL-PROTECTED" if row["raster_all_protected"] else ""
                line += (
                    f" | raster det={row['raster_detections']:<3} "
                    f"prot={row['raster_protected']:.2f} agree={row['agreement']:.3f}{flag_r}"
                )
            print(line)

        key = "raster_all_protected" if kind == "round-trip" else "orig_all_protected"
        dead = sum(1 for r in group if r[key])
        rate = dead / len(group)
        print(f"\n  all-protected pages : {dead}/{len(group)} = {rate:.1%}   (kill threshold: >10%)")
        if kind == "round-trip":
            agrees = [r["agreement"] for r in group]
            print(f"  protected-agreement : median {statistics.median(agrees):.3f}  min {min(agrees):.3f}")
        else:
            prot = [r["orig_protected"] for r in group]
            print(f"  protected fraction  : median {statistics.median(prot):.3f}  max {max(prot):.3f}")
        print(f"  VERDICT             : {'FAIL - stop the spike' if rate > 0.10 else 'PASS'}")

    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
