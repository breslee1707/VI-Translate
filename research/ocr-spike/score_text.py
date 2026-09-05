"""Score recognised text against the born-digital answer key.

Comparison is per page, on the whole page's text joined in reading order,
rather than line against line. Line-level alignment would mostly measure how
each engine chose to split lines, which is not what the pipeline cares about:
pdf2zh/converter.py regroups everything into its own paragraphs anyway.

Three numbers, because they fail independently:

  CER / WER   ordinary recognition quality.
  CER-nospace the same with every space removed. A recogniser trained on a
              script that does not use spaces returns English as one run-on
              token: near-perfect letters, useless words. The gap between CER
              and CER-nospace isolates exactly that failure, which a single
              CER would hide as generic badness.
  numbers     recall of digit-bearing tokens. pdf2zh/rules.py:269-304 already
              treats codes and quantities as things that must not be altered,
              and a wrong digit in a technical manual does more damage than a
              missing sentence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import ocrjson  # noqa: E402
import paths  # noqa: E402

LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
DASHES = dict.fromkeys("‐‑‒–—―", "-")
QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'", "″": '"', "′": "'"}
NUMERIC = re.compile(r"\S*\d\S*")


def normalise(text: str) -> str:
    """Remove differences no engine could have got right, or wrong.

    Ligatures, dashes and curly quotes are encoding choices of the source
    document, not recognition outcomes. Scoring them as errors would penalise
    an engine for reading the page correctly.
    """
    for source, target in {**LIGATURES, **DASHES, **QUOTES}.items():
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def edit_distance(left: str, right: str) -> int:
    """Levenshtein distance, one row at a time so a full page stays affordable."""
    if left == right:
        return 0
    if not left or not right:
        return max(len(left), len(right))
    previous = np.arange(len(right) + 1, dtype=np.int32)
    right_array = np.frombuffer(right.encode("utf-32-le"), dtype=np.uint32)
    for index, char in enumerate(left):
        current = np.empty_like(previous)
        current[0] = index + 1
        substitution = previous[:-1] + (right_array != ord(char))
        deletion = previous[1:] + 1
        current[1:] = np.minimum(substitution, deletion)
        # Insertions chain along the row, so they cannot be vectorised away.
        for position in range(1, len(current)):
            if current[position - 1] + 1 < current[position]:
                current[position] = current[position - 1] + 1
        previous = current
    return int(previous[-1])


def page_text(page: ocrjson.Page) -> str:
    ordered = ocrjson.reading_order(page.lines, page.width)
    return normalise(" ".join(line.text for line in ordered))


def score_page(truth: ocrjson.Page, hypothesis: ocrjson.Page) -> dict:
    reference, candidate = page_text(truth), page_text(hypothesis)
    cer = edit_distance(reference, candidate) / max(1, len(reference))

    bare_reference = reference.replace(" ", "")
    bare_candidate = candidate.replace(" ", "")
    cer_nospace = edit_distance(bare_reference, bare_candidate) / max(1, len(bare_reference))

    reference_words, candidate_words = reference.split(), candidate.split()
    wer = edit_distance(
        "\n".join(reference_words), "\n".join(candidate_words)
    ) / max(1, len("\n".join(reference_words)))

    expected_numbers = set(NUMERIC.findall(reference))
    found_numbers = set(NUMERIC.findall(candidate))
    recall = (
        len(expected_numbers & found_numbers) / len(expected_numbers)
        if expected_numbers
        else 1.0
    )

    return {
        "page": truth.page + 1,
        "ref_chars": len(reference),
        "hyp_chars": len(candidate),
        "cer": round(cer, 4),
        "cer_nospace": round(cer_nospace, 4),
        "wer_proxy": round(wer, 4),
        "numeric_recall": round(recall, 4),
        "numeric_expected": len(expected_numbers),
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
        score_page(truth[index], hypothesis.get(index, ocrjson.Page(index, 0, 0, [])))
        for index in sorted(truth)
    ]
    total_reference = sum(row["ref_chars"] for row in rows)
    weighted = lambda key: sum(  # noqa: E731 - a table of one-liners reads better
        row[key] * row["ref_chars"] for row in rows
    ) / max(1, total_reference)

    summary = {
        "label": args.label or Path(args.hyp).stem,
        "pages": len(rows),
        "ref_chars": total_reference,
        "cer": round(weighted("cer"), 4),
        "cer_nospace": round(weighted("cer_nospace"), 4),
        "wer_proxy": round(weighted("wer_proxy"), 4),
        "numeric_recall": round(weighted("numeric_recall"), 4),
        "pages_detail": rows,
    }

    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in summary["label"])
    out = paths.run_dir("text-scores") / f"{safe}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"{summary['label']:<40} CER {summary['cer']:>7.2%}  "
        f"CER-nospace {summary['cer_nospace']:>7.2%}  "
        f"WER {summary['wer_proxy']:>7.2%}  numbers {summary['numeric_recall']:>6.1%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
