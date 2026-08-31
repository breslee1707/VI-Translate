"""Score recognition and reading order separately.

Joining a page into one string and taking an edit distance conflates two very
different failures. An engine that reads every character correctly but emits a
two-column page in the wrong sequence scores as badly as one that cannot read
at all -- and the fixes are nothing alike. The first needs a better ordering
pass in the sidecar writer, which is ours to change; the second needs a
different recogniser.

So lines are matched geometrically first, by box overlap, and only then
compared as text. That yields:

  matched CER   recognition quality alone, order irrelevant
  missed        truth lines no detection covers -- text silently dropped
  spurious      detections matching no truth line -- invented text
  order tau     Kendall rank correlation between the reading order the sidecar
                would emit and the true one. This is the number that matters
                for pdf2zh/converter.py:553, which builds paragraphs in
                emission order and never sorts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ocrjson  # noqa: E402
import paths  # noqa: E402
from score_text import edit_distance, normalise  # noqa: E402

# A detection has to cover half a truth line before we call it that line.
MIN_OVERLAP = 0.5


def overlap(a: tuple, b: tuple) -> float:
    """Intersection over the area of `a`, i.e. how much of `a` that `b` covers."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (a[2] - a[0]) * (a[3] - a[1])
    return ((x1 - x0) * (y1 - y0)) / area if area > 0 else 0.0


def kendall_tau(order: list[int]) -> float:
    """Rank correlation of a permutation against the identity, in [-1, 1]."""
    n = len(order)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if order[i] < order[j]:
                concordant += 1
            elif order[i] > order[j]:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 1.0


def score_page(truth: ocrjson.Page, hypothesis: ocrjson.Page) -> dict:
    used: set[int] = set()
    pairs: list[tuple[int, int]] = []

    for t_index, t_line in enumerate(truth.lines):
        best, best_score = None, MIN_OVERLAP
        for h_index, h_line in enumerate(hypothesis.lines):
            if h_index in used:
                continue
            score = overlap(t_line.bbox, h_line.bbox)
            if score > best_score:
                best, best_score = h_index, score
        if best is not None:
            used.add(best)
            pairs.append((t_index, best))

    errors = reference_chars = 0
    for t_index, h_index in pairs:
        reference = normalise(truth.lines[t_index].text)
        candidate = normalise(hypothesis.lines[h_index].text)
        errors += edit_distance(reference, candidate)
        reference_chars += len(reference)

    # Where the sidecar would place each matched detection, against where the
    # truth says it belongs.
    emitted = ocrjson.reading_order(hypothesis.lines, hypothesis.width)
    position = {id(line): rank for rank, line in enumerate(emitted)}
    sequence = [position[id(hypothesis.lines[h])] for _, h in pairs]

    missed_chars = sum(
        len(normalise(truth.lines[i].text))
        for i in range(len(truth.lines))
        if i not in {t for t, _ in pairs}
    )
    truth_chars = sum(len(normalise(line.text)) for line in truth.lines)

    return {
        "page": truth.page + 1,
        "truth_lines": len(truth.lines),
        "hyp_lines": len(hypothesis.lines),
        "matched": len(pairs),
        "missed_lines": len(truth.lines) - len(pairs),
        "spurious_lines": len(hypothesis.lines) - len(pairs),
        "matched_cer": round(errors / max(1, reference_chars), 4),
        "missed_char_rate": round(missed_chars / max(1, truth_chars), 4),
        "order_tau": round(kendall_tau(sequence), 4),
        "ref_chars": truth_chars,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--hyp", type=Path, required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    paths.ensure_tree()
    truth = {p.page: p for p in ocrjson.load(paths.work_path(args.truth))}
    hypothesis = {p.page: p for p in ocrjson.load(paths.work_path(args.hyp))}

    rows = [
        score_page(truth[i], hypothesis.get(i, ocrjson.Page(i, 0, 0, [])))
        for i in sorted(truth)
    ]
    total = sum(row["ref_chars"] for row in rows) or 1
    label = args.label or Path(args.hyp).stem
    summary = {
        "label": label,
        "matched_cer": round(sum(r["matched_cer"] * r["ref_chars"] for r in rows) / total, 4),
        "missed_char_rate": round(
            sum(r["missed_char_rate"] * r["ref_chars"] for r in rows) / total, 4
        ),
        "order_tau": round(sum(r["order_tau"] * r["ref_chars"] for r in rows) / total, 4),
        "spurious_lines": sum(r["spurious_lines"] for r in rows),
        "pages_detail": rows,
    }

    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label)
    out = paths.run_dir("line-scores") / f"{safe}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"{label:<44} matched-CER {summary['matched_cer']:>7.2%}  "
        f"missed {summary['missed_char_rate']:>6.1%}  "
        f"order-tau {summary['order_tau']:>6.3f}  spurious {summary['spurious_lines']:>3}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
