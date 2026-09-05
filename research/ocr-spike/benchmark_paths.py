"""Writable locations for the persistent OCR benchmark."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT = REPO_ROOT / "tmp" / "ocr-benchmark"
SOURCES = ROOT / "sources"
SELECTED = ROOT / "selected"
VARIANTS = ROOT / "variants"
TRUTH = ROOT / "truth"
MODELS = ROOT / "models"
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"
LOCK = ROOT / "manifest.lock.json"


def ensure_tree() -> None:
    for path in (ROOT, SOURCES, SELECTED, VARIANTS, TRUTH, MODELS, RUNS, REPORTS):
        path.mkdir(parents=True, exist_ok=True)


def safe_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != ROOT.resolve() and ROOT.resolve() not in resolved.parents:
        raise ValueError(f"benchmark path escapes {ROOT}: {resolved}")
    return resolved
