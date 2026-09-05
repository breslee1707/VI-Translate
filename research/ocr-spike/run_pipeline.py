"""Phase B: push a sidecar through the shipping pipeline, unmodified.

This calls scripts/translate_pdf.py:translate_pdf() exactly as the CLI and the
desktop app do. Nothing in pdf2zh/ or app/ is patched, monkeypatched or
imported around. That is the point: whatever happens here is what would happen
to a user, so a clean result is evidence and a failure is a real blocker.

Translation runs through HandoffTranslator, never Google, for three reasons.
It is offline and deterministic, so a sweep is repeatable and costs nothing. It
forces ignore_cache=True internally, so the user's shared cache at
~/.cache/pdf2zh/ is never seeded with OCR noise that would live there forever.
And its two-pass shape gives the harness both artefacts it needs:

  pass 1  No table, so every segment falls through and is recorded. The misses
          file *is* the pipeline's own view of the recognised text, already
          grouped into the paragraphs the converter built. This is what the
          text scorers read.
  pass 2  A table synthesised from those misses, so new != source. That matters
          visually: pdf2zh/converter.py:836 skips the white backing rectangle
          whenever a segment came back unchanged, so pass 1 produces no visual
          evidence at all. Only pass 2 shows whether the original scan is
          actually covered.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import paths  # noqa: E402
from translate_pdf import TranslationError, translate_pdf  # noqa: E402

# Markers that must never survive into a delivered PDF, from
# agent-knowledge/validation.md step 2.
FORBIDDEN = ("{v", "<b", "</b", "<s", "</s", "\x00", "\ufffd")
PLACEHOLDER = re.compile(r"\{v\d+\}|</?[bs]\d+>")


def synthesise_table(misses: Path, out: Path, expansion: float = 1.2) -> int:
    """Build a stand-in translation for every miss, preserving every marker.

    Vietnamese runs longer than English, and the reflow, line-height and
    shrink-to-fit code is exactly what a translation exercises. So the stand-in
    has to be longer than the source rather than a copy of it, or pass 2 would
    prove nothing about fitting. Markers are copied through untouched because
    pdf2zh/translator.py:151-161 raises if their sequence changes.
    """
    rows = 0
    with misses.open(encoding="utf-8") as stream, out.open("w", encoding="utf-8") as target:
        for raw in stream:
            raw = raw.strip()
            if not raw:
                continue
            source = json.loads(raw)["src"]
            markers = PLACEHOLDER.findall(source)
            body = PLACEHOLDER.sub(" ", source)
            words = body.split()
            # Lengthen by repeating a stable suffix of the words, so the result
            # is deterministic and roughly `expansion` times as long.
            extra = words[: max(0, int(len(words) * (expansion - 1)))]
            stretched = " ".join(words + extra) if words else body
            target.write(
                json.dumps({"src": source, "dst": stretched + "".join(markers)}, ensure_ascii=False)
                + "\n"
            )
            rows += 1
    return rows


def inspect(pdf: Path, source: Path) -> dict:
    """Apply agent-knowledge/validation.md steps 1-3 to a delivered PDF."""
    import pymupdf

    result: dict = {"exists": pdf.is_file()}
    if not result["exists"]:
        return result
    with pymupdf.open(pdf) as out, pymupdf.open(source) as src:
        result["pages"] = out.page_count
        result["pages_match"] = out.page_count == src.page_count
        result["canvas_match"] = all(
            abs(out[i].rect.width - src[i].rect.width) < 0.5
            and abs(out[i].rect.height - src[i].rect.height) < 0.5
            for i in range(min(out.page_count, src.page_count))
        )
        text = "".join(page.get_text() for page in out)
        result["chars"] = len(text.strip())
        result["markers"] = {m: text.count(m) for m in FORBIDDEN if text.count(m)}
    return result


def run(sidecar: Path, label: str, target_language: str = "vi") -> dict:
    out_dir = paths.run_dir(f"pipeline/{label}")
    misses = out_dir / "misses.jsonl"
    table = out_dir / "table.jsonl"
    row: dict = {"label": label, "sidecar": str(sidecar)}

    # Pass 1 -- collect what the pipeline actually sees.
    started = time.time()
    try:
        first = translate_pdf(
            sidecar,
            out_dir / "pass1",
            target_language=target_language,
            engine="handoff",
            emit_segments=misses,
            overwrite=True,
        )
        row["pass1"] = {
            "ok": True,
            "untranslated": first.untranslated,
            "image_only_pages": list(first.image_only_pages),
            "reasons": dict(first.reasons) if first.reasons else {},
        }
    except TranslationError as error:
        row["pass1"] = {"ok": False, "error": str(error)}
        # The refusal firing on an OCR'd document is the outcome that matters
        # most, so record it precisely rather than just failing.
        row["refused"] = "does not perform OCR" in str(error)
        row["seconds"] = round(time.time() - started, 1)
        return row

    row["segments_seen"] = sum(1 for _ in misses.open(encoding="utf-8")) if misses.is_file() else 0

    # Pass 2 -- a real translation, so backing rectangles are actually painted.
    rows = synthesise_table(misses, table) if misses.is_file() else 0
    row["table_rows"] = rows
    second = translate_pdf(
        sidecar,
        out_dir / "pass2",
        target_language=target_language,
        engine="handoff",
        segments=table,
        overwrite=True,
    )
    row["pass2"] = {
        "ok": True,
        "untranslated": second.untranslated,
        "reasons": dict(second.reasons) if second.reasons else {},
        "output": str(second.path),
    }
    row["gate"] = inspect(Path(second.path), sidecar) if second.path else {}
    row["seconds"] = round(time.time() - started, 1)
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecars", type=Path, nargs="+")
    args = parser.parse_args(argv)

    paths.ensure_tree()
    rows = []
    for sidecar in args.sidecars:
        resolved = paths.work_path(sidecar)
        label = resolved.stem
        print(f"\n=== {label} ===")
        row = run(resolved, label)
        rows.append(row)

        if not row["pass1"]["ok"]:
            verdict = "REFUSED AS A SCAN" if row.get("refused") else "FAILED"
            print(f"  pass 1: {verdict}\n          {row['pass1']['error'][:150]}")
            continue
        print(
            f"  pass 1: {row['segments_seen']} segments seen, "
            f"{row['pass1']['untranslated']} untranslated, "
            f"image-only pages {row['pass1']['image_only_pages'] or 'none'}"
        )
        gate = row.get("gate", {})
        markers = gate.get("markers", {})
        print(
            f"  pass 2: {row['pass2']['untranslated']} untranslated, "
            f"{gate.get('chars', 0)} chars out, {gate.get('pages')} pages"
        )
        print(
            f"  gate  : pages_match={gate.get('pages_match')} "
            f"canvas_match={gate.get('canvas_match')} "
            f"markers={markers or 'clean'}"
        )
        if row["pass2"]["reasons"]:
            print(f"  reasons: {row['pass2']['reasons']}")
        print(f"  {row['seconds']}s -> {row['pass2']['output']}")

    out = paths.run_dir("pipeline") / "results.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
