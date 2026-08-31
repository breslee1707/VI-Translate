"""The one place that decides where spike artifacts may be written.

Every script in this directory routes its output through `work_path`. Nothing
else is allowed to open a file for writing. The rule exists because the spike
generates rasterised corpora, ONNX models and translated PDFs by the hundred,
and `agent-knowledge/index.md` keeps all of that out of commits.

The enforcement is structural rather than advisory: WORK_ROOT sits under
`tmp/`, which `.gitignore` line 6 already ignores, so a path that passes
`work_path` cannot be committed even by accident. A path that fails it raises
before anything is created.
"""

from __future__ import annotations

from pathlib import Path

# research/ocr-spike/paths.py -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# `tmp/` is ignored by .gitignore:6, and agent-knowledge/validation.md already
# designates tmp/ for diagnostic intermediates. Reusing it means the spike adds
# no new ignore rules.
WORK_ROOT = REPO_ROOT / "tmp" / "ocr-spike"

CORPUS = WORK_ROOT / "corpus"
CORPUS_SOURCE = CORPUS / "source"    # born-digital PDFs, copied in by hand
CORPUS_RASTER = CORPUS / "raster"    # image-only rebuilds of the above
CORPUS_SIDECAR = CORPUS / "sidecar"  # raster + an invisible text layer
CORPUS_REAL = CORPUS / "real"        # genuine scans, one named pathology each
RUNS = WORK_ROOT / "runs"
REPORTS = WORK_ROOT / "reports"

_TREE = (
    WORK_ROOT, CORPUS, CORPUS_SOURCE, CORPUS_RASTER,
    CORPUS_SIDECAR, CORPUS_REAL, RUNS, REPORTS,
)


def work_path(*parts: str | Path) -> Path:
    """Resolve a path under WORK_ROOT, refusing anything that escapes it.

    Callers pass either an absolute path to check or fragments to join. The
    resolve() is what makes `..` and symlinks unable to walk out of the tree.
    """
    candidate = Path(*parts) if parts else WORK_ROOT
    if not candidate.is_absolute():
        candidate = WORK_ROOT / candidate
    resolved = candidate.resolve()
    root = WORK_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"Spike output must stay under {root}, refusing: {resolved}. "
            "Generated PDFs, models and caches are never committed."
        )
    return resolved


def ensure_tree() -> None:
    """Create the working tree. Safe to call from every script, every run."""
    for directory in _TREE:
        directory.mkdir(parents=True, exist_ok=True)


def run_dir(name: str) -> Path:
    """Return a fresh directory for one sweep, e.g. `layout-probe`."""
    target = work_path(RUNS / name)
    target.mkdir(parents=True, exist_ok=True)
    return target
