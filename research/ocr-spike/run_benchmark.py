"""Run the locked OCR corpus and score exact-truth pages by profile."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402

import benchmark_paths as paths  # noqa: E402
import ocrjson  # noqa: E402
from pdf2zh.ocr import OCR_PROFILES, load_ocr_engine, recognise_image  # noqa: E402
from score_lines import score_page as score_lines_page  # noqa: E402
from score_text import score_page as score_text_page  # noqa: E402

VARIANT_MANIFEST = paths.ROOT / "variants.lock.json"
SMOKE_VARIANT_PAGES = {"nasa-19990035925": 1, "nasa-20050215121": 1}
SMOKE_REAL_IDS = ("nasa-19630003300", "loc-elementarytreati00rice")


def git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=paths.REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def page_image(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    return np.frombuffer(pixmap.samples, np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )[:, :, :3].copy()


def recognise_document(
    engine: Any,
    path: Path,
    dpi: int,
    page_indices: list[int] | None = None,
) -> tuple[list[ocrjson.Page], float]:
    pages = []
    seconds = 0.0
    selected = set(page_indices) if page_indices is not None else None
    with pymupdf.open(path) as document:
        for index, page in enumerate(document):
            if selected is not None and index not in selected:
                continue
            image = page_image(page, dpi)
            started = time.perf_counter()
            lines = recognise_image(engine, image)
            seconds += time.perf_counter() - started
            scale = 72.0 / dpi
            pages.append(
                ocrjson.Page(
                    page=index,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    lines=[
                        ocrjson.Line(
                            text=line.text,
                            bbox=tuple(value * scale for value in line.bbox),
                            conf=line.confidence,
                        )
                        for line in lines
                    ],
                )
            )
    return pages, seconds


def tasks_for_suite(
    suite: str,
    lock: list[dict[str, Any]],
    variants: list[dict[str, Any]],
    variant_filter: set[str] | None = None,
    max_pages: int | None = None,
    max_documents: int | None = None,
) -> list[dict[str, Any]]:
    by_id = {row["id"]: row for row in lock}
    if suite == "smoke":
        tasks = [
            dict(
                item,
                page_indices=[SMOKE_VARIANT_PAGES[item["id"]]],
                truth_status="exact-text-layer",
            )
            for item in variants
            if item["id"] in SMOKE_VARIANT_PAGES
            and (variant_filter is None or item["variant"] in variant_filter)
        ]
        tasks += [
            dict(item, page_indices=[0], truth_status="provisional-source-ocr")
            for item in variants
            if item["id"] in SMOKE_REAL_IDS and item["variant"] == "real-scan"
        ]
        return tasks
    allowed_splits = {"dev", "validation"} if suite == "core" else {
        "dev", "validation", "holdout"
    }
    tasks = [
        dict(item, page_indices=None, truth_status="exact-text-layer")
        for item in variants
        if item["split"] in allowed_splits
        and (variant_filter is None or item["variant"] in variant_filter)
    ]
    tasks += [
        dict(item, page_indices=None, truth_status="provisional-source-ocr")
        for item in variants
        if item["variant"] == "real-scan"
        and item["split"] in allowed_splits
        and (variant_filter is None or item["variant"] in variant_filter)
    ]
    if max_pages is not None:
        for task in tasks:
            available = len(task.get("page_indices") or []) or int(task.get("pages", max_pages))
            # Covers and legal boilerplate dominate page 0 in technical PDFs;
            # prefer content pages while remaining deterministic.
            start = 1 if available > 1 else 0
            task["page_indices"] = [
                (start + offset) % available for offset in range(min(max_pages, available))
            ]
    if max_documents is not None:
        tasks = tasks[:max_documents]
    return tasks


def score_task(
    task: dict[str, Any],
    hypothesis: list[ocrjson.Page],
    truth_by_id: dict[str, list[ocrjson.Page]],
) -> list[dict[str, Any]]:
    if task["truth_status"] != "exact-text-layer":
        return []
    truth = {page.page: page for page in truth_by_id[task["id"]]}
    rows = []
    for page in hypothesis:
        expected = truth.get(page.page)
        if expected is None:
            continue
        text = score_text_page(expected, page)
        lines = score_lines_page(expected, page)
        rows.append(
            {
                "page": page.page + 1,
                "ref_chars": text["ref_chars"],
                "cer": text["cer"],
                "numeric_recall": text["numeric_recall"],
                "matched_cer": lines["matched_cer"],
                "missed_char_rate": lines["missed_char_rate"],
                "order_tau": lines["order_tau"],
                "spurious_lines": lines["spurious_lines"],
            }
        )
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [page for row in rows for page in row["scores"] if page["ref_chars"]]
    total = sum(page["ref_chars"] for page in scored) or 1
    weighted = lambda key: sum(page[key] * page["ref_chars"] for page in scored) / total  # noqa: E731
    real_pages = sum(len(row["hypothesis"]) for row in rows if not row["scores"])
    summary = {
        "documents": len(rows),
        "scored_pages": len(scored),
        "real_pages_pending_manual_truth": real_pages,
        "seconds": round(sum(row["seconds"] for row in rows), 3),
        "seconds_per_page": round(
            sum(row["seconds"] for row in rows)
            / max(1, sum(len(row["hypothesis"]) for row in rows)),
            3,
        ),
        "matched_cer": round(weighted("matched_cer"), 4),
        "missed_char_rate": round(weighted("missed_char_rate"), 4),
        "numeric_recall": round(weighted("numeric_recall"), 4),
        "order_tau": round(weighted("order_tau"), 4),
        "spurious_lines": sum(page["spurious_lines"] for page in scored),
        "recognition_gate": (
            "pass"
            if weighted("matched_cer") <= 0.05
            and weighted("missed_char_rate") <= 0.05
            and weighted("numeric_recall") >= 0.97
            and weighted("order_tau") >= 0.95
            else "fail"
        ),
    }
    feature_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for feature in row["features"]:
            feature_groups.setdefault(feature, []).append(row)
    summary["by_feature"] = {}
    for feature, group in sorted(feature_groups.items()):
        feature_pages = [page for row in group for page in row["scores"]]
        feature_chars = sum(page["ref_chars"] for page in feature_pages) or 1
        weighted_feature = lambda key: sum(  # noqa: E731
            page[key] * page["ref_chars"] for page in feature_pages
        ) / feature_chars
        summary["by_feature"][feature] = {
            "documents": len(group),
            "pages": len(feature_pages),
            "matched_cer": round(weighted_feature("matched_cer"), 4)
            if feature_pages
            else None,
            "missed_char_rate": round(weighted_feature("missed_char_rate"), 4)
            if feature_pages
            else None,
            "numeric_recall": round(weighted_feature("numeric_recall"), 4)
            if feature_pages
            else None,
            "order_tau": round(weighted_feature("order_tau"), 4)
            if feature_pages
            else None,
        }
    return summary


def run_profile(
    profile: str,
    suite: str,
    variant_filter: set[str] | None = None,
    max_pages: int | None = None,
    max_documents: int | None = None,
) -> Path:
    lock = json.loads(paths.LOCK.read_text(encoding="utf-8"))
    variants = json.loads(VARIANT_MANIFEST.read_text(encoding="utf-8"))
    tasks = tasks_for_suite(
        suite,
        lock,
        variants,
        variant_filter,
        max_pages,
        max_documents,
    )
    truth_by_id = {
        row["id"]: ocrjson.load(paths.REPO_ROOT / row["truth_path"])
        for row in lock
        if row.get("truth_path")
    }
    engine = load_ocr_engine(profile)
    dpi = OCR_PROFILES[profile].dpi
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = paths.safe_path(paths.RUNS / f"benchmark-{git_revision()}-{profile}-{suite}-{run_id}")
    run_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    for index, task in enumerate(tasks, 1):
        source = paths.safe_path(paths.REPO_ROOT / task["path"])
        hypothesis, seconds = recognise_document(
            engine, source, dpi, task.get("page_indices")
        )
        output = run_dir / f"{task['id']}--{task['variant']}.json"
        ocrjson.dump(hypothesis, output)
        scores = score_task(task, hypothesis, truth_by_id)
        rows.append(
            {
                "id": task["id"],
                "variant": task["variant"],
                "split": task["split"],
                "features": task["features"],
                "truth_status": task["truth_status"],
                "seconds": round(seconds, 3),
                "hypothesis": [asdict(page) for page in hypothesis],
                "scores": scores,
            }
        )
        print(
            f"[{index:03}/{len(tasks):03}] {task['id']} {task['variant']}: "
            f"{len(hypothesis)} pages, {seconds:.2f}s"
        )
    report = {
        "revision": git_revision(),
        "profile": profile,
        "suite": suite,
        "created_utc": run_id,
        "summary": aggregate(rows),
        "documents": rows,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {report_path}")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("standard", "enhanced", "both"), default="both")
    parser.add_argument("--suite", choices=("smoke", "core", "full"), default="smoke")
    parser.add_argument(
        "--variants",
        help="comma-separated variant names; default runs every variant",
    )
    parser.add_argument("--max-pages", type=int, help="limit pages per task")
    parser.add_argument("--max-documents", type=int, help="limit task count")
    args = parser.parse_args(argv)
    paths.ensure_tree()
    if not paths.LOCK.is_file() or not VARIANT_MANIFEST.is_file():
        raise RuntimeError("fetch and build the benchmark before running it")
    profiles = ("standard", "enhanced") if args.profile == "both" else (args.profile,)
    variant_filter = set(args.variants.split(",")) if args.variants else None
    for profile in profiles:
        run_profile(profile, args.suite, variant_filter, args.max_pages, args.max_documents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
