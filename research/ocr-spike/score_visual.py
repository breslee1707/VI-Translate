"""Measure how much of a page the translation erased.

agent-knowledge/validation.md ends with two steps a script cannot do: render
every page and look at it. That is not ceremony. The automated part of the gate
-- page count, canvas size, marker search -- passed the oracle sidecar cleanly
while the rendered page showed formula panels wiped to white. Text-level checks
are blind to ink.

So this closes the loop numerically. For each page it renders the input and the
output at the same scale and asks two questions:

  whited_out   share of the page that carried ink before and is bare white
               after. On a scanned page pdf2zh/converter.py:831-849 paints a
               white rectangle behind every translated paragraph, and anything
               that lived under that rectangle -- a tinted panel, a table fill,
               a rule, a formula the recogniser flattened -- goes with it.
  colour_lost  share of the page that held a saturated (non-grey) colour before
               and is white after. This is the sharper of the two: body text is
               black, so a colour that disappears was background, and
               references/preservation-rules.md requires backgrounds to survive.

Both are computed for the sidecar path and for the born-digital control run
through the identical pipeline, because only the difference between them is
attributable to OCR mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402

import paths  # noqa: E402

DPI = 100
WHITE = 245          # at or above this in every channel counts as bare paper
SATURATION = 18      # max-min channel spread that counts as a real colour


def render(path: Path, index: int) -> np.ndarray:
    with pymupdf.open(path) as doc:
        pix = doc[index].get_pixmap(dpi=DPI)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]


def compare(before: np.ndarray, after: np.ndarray) -> dict:
    rows = min(before.shape[0], after.shape[0])
    cols = min(before.shape[1], after.shape[1])
    before, after = before[:rows, :cols], after[:rows, :cols]

    inked_before = np.any(before < WHITE, axis=2)
    bare_after = np.all(after >= WHITE, axis=2)
    spread = before.max(axis=2).astype(int) - before.min(axis=2).astype(int)
    coloured_before = spread > SATURATION

    inked_after = np.any(after < WHITE, axis=2)
    bare_before = np.all(before >= WHITE, axis=2)

    total = float(rows * cols)
    return {
        "ink_before": round(float(inked_before.sum()) / total, 4),
        "ink_after": round(float(inked_after.sum()) / total, 4),
        "whited_out": round(float((inked_before & bare_after).sum()) / total, 4),
        # Ink laid onto bare paper. On a page the backing rectangles never
        # covered, the translation is drawn straight over the original instead
        # of replacing it, and this is where that shows up: ink is added while
        # almost none is removed.
        "ink_added": round(float((bare_before & inked_after).sum()) / total, 4),
        "colour_before": round(float(coloured_before.sum()) / total, 4),
        "colour_lost": round(float((coloured_before & bare_after).sum()) / total, 4),
    }


def score(label: str, source: Path, output: Path) -> list[dict]:
    rows = []
    with pymupdf.open(source) as doc:
        pages = doc.page_count
    for index in range(pages):
        row = {"label": label, "page": index + 1}
        row.update(compare(render(source, index), render(output, index)))
        # Share of the colour that was there and is now gone, which reads more
        # naturally than an absolute page fraction.
        row["colour_lost_share"] = (
            round(row["colour_lost"] / row["colour_before"], 3) if row["colour_before"] else 0.0
        )
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair", action="append", nargs=3, metavar=("LABEL", "SOURCE", "OUTPUT"), required=True
    )
    args = parser.parse_args(argv)

    paths.ensure_tree()
    rows: list[dict] = []
    for label, source, output in args.pair:
        rows += score(label, paths.work_path(source), paths.work_path(output))

    header = f"{'label':<22} {'pg':>3} {'ink>':>7} {'ink<':>7} {'whited':>7} {'added':>7} {'lost%':>7}"
    print(header)
    for row in rows:
        print(
            f"{row['label'][:22]:<22} {row['page']:>3} {row['ink_before']:>7.3f} "
            f"{row['ink_after']:>7.3f} {row['whited_out']:>7.3f} "
            f"{row['ink_added']:>7.3f} {row['colour_lost_share']:>6.1%}"
        )

    print()
    for label in dict.fromkeys(r["label"] for r in rows):
        group = [r for r in rows if r["label"] == label]
        mean = lambda key: sum(r[key] for r in group) / len(group)  # noqa: E731
        print(
            f"  {label:<22} whited-out {mean('whited_out'):.3f}   "
            f"ink-added {mean('ink_added'):.3f}   "
            f"colour lost {mean('colour_lost_share'):.1%}"
        )

    out = paths.run_dir("visual") / "visual.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
