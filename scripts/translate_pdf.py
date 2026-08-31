#!/usr/bin/env python3
"""Translate one text-based PDF while preserving its layout and formulas."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_CORE = (SKILL_ROOT / "pdf2zh").resolve()
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

CORE_VERSION = "1.9.11"
RULESET = "code4life-preservation-v1"
DEFAULT_TARGET_LANGUAGE = "vi"

# Latin-script targets the bundled GoNotoKurrent font renders correctly. Scripts
# needing CJK glyphs, right-to-left runs, or complex shaping are refused rather
# than emitted as blank boxes or reordered text.
TARGET_LANGUAGES = frozenset(
    {
        "af", "ca", "cs", "cy", "da", "de", "en", "es", "et", "eu", "fi", "fr",
        "ga", "gl", "hr", "hu", "id", "is", "it", "lt", "lv", "ms", "mt", "nl",
        "no", "pl", "pt", "ro", "sk", "sl", "sq", "sv", "sw", "tl", "tr", "vi",
    }
)

ENGINES = ("google", "handoff")

# Measured on an eight-page sample: 2 threads 48s, 4 threads 30s, 8 threads 27s,
# 12 threads 29s. Past four, the layout pass rather than the network is the floor,
# and more concurrency only raises the odds of the service throttling a long run.
DEFAULT_THREADS = 4
MAX_THREADS = 8


class TranslationError(RuntimeError):
    """Raised when input validation or the translation engine fails."""


class Translation(NamedTuple):
    """Where the translated file landed, and how much of it stayed in the source language."""

    path: Path | None
    untranslated: int = 0


def _positive_threads(value: str) -> int:
    threads = int(value)
    if not 1 <= threads <= MAX_THREADS:
        raise argparse.ArgumentTypeError(f"threads must be between 1 and {MAX_THREADS}")
    return threads


def _page_selection(value: str) -> str:
    if not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?(?:,[1-9]\d*(?:-[1-9]\d*)?)*", value):
        raise argparse.ArgumentTypeError("pages must use one-based ranges such as 1,3-5")
    for item in value.split(","):
        if "-" in item:
            start, end = (int(part) for part in item.split("-", 1))
            if start > end:
                raise argparse.ArgumentTypeError("page range start must not exceed its end")
    return value


def _source_language(value: str) -> str:
    if value == "auto" or re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", value):
        return value
    raise argparse.ArgumentTypeError("source language must be 'auto' or a Google language code")


def _target_language(value: str) -> str:
    language = value.lower()
    if language not in TARGET_LANGUAGES:
        supported = ", ".join(sorted(TARGET_LANGUAGES))
        raise argparse.ArgumentTypeError(
            f"unsupported target language {value!r}. The bundled font covers Latin-script "
            f"targets only, so CJK, right-to-left, and complex-shaping scripts would render "
            f"as blank boxes or reordered text. Supported: {supported}"
        )
    return language


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translate one text-based PDF while preserving layout and formulas."
    )
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--target-language", default=DEFAULT_TARGET_LANGUAGE, type=_target_language
    )
    parser.add_argument("--source-language", default="auto", type=_source_language)
    parser.add_argument("--pages", type=_page_selection)
    parser.add_argument("--threads", default=DEFAULT_THREADS, type=_positive_threads)
    parser.add_argument("--engine", default="google", choices=ENGINES)
    parser.add_argument(
        "--segments",
        type=Path,
        help='handoff engine: JSONL of {"src","dst"} records to translate from',
    )
    parser.add_argument(
        "--emit-segments",
        type=Path,
        help="handoff engine: write the segments left untranslated here, as JSONL",
    )
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _validate_arguments(args: argparse.Namespace) -> None:
    if args.output_dir is None and args.emit_segments is None:
        raise TranslationError("--output-dir is required unless --emit-segments is given")
    if args.engine == "handoff":
        if args.segments is None and args.emit_segments is None:
            raise TranslationError("--engine handoff needs --segments, --emit-segments, or both")
    elif args.segments is not None or args.emit_segments is not None:
        raise TranslationError("--segments and --emit-segments require --engine handoff")


def _require_core() -> None:
    try:
        import pdf2zh
        importlib.import_module("pdf2zh.doclayout")
        # high_level pulls in the native stack - pikepdf/qpdf, PyMuPDF, onnx.
        # Without it a broken install slipped past this check and surfaced as a
        # raw ImportError from the engine, once per file in the queue, instead
        # of one actionable message before any work started.
        importlib.import_module("pdf2zh.high_level")
    except ImportError as error:
        requirements = SKILL_ROOT / "requirements.txt"
        install = f'"{sys.executable}" -m pip install -r "{requirements}"'
        raise TranslationError(f"PDF core dependencies are missing. Run: {install}") from error
    if pdf2zh.__version__ != CORE_VERSION:
        raise TranslationError(
            f"Expected bundled PDF core {CORE_VERSION}, found {pdf2zh.__version__}"
        )
    if getattr(pdf2zh, "__ruleset__", None) != RULESET:
        raise TranslationError("Bundled PDF core does not expose the required preservation ruleset")
    # A packaged build has no pip environment for a PyPI wheel to shadow the core,
    # and its module paths point inside the extraction directory rather than here.
    if getattr(sys, "frozen", False):
        return
    module_path = Path(pdf2zh.__file__).resolve()
    if not module_path.is_relative_to(BUNDLED_CORE):
        raise TranslationError(f"Refusing external PDF core: {module_path}")


def _validate_input(path: Path) -> Path:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise TranslationError(f"Input PDF does not exist: {source}")
    if source.suffix.lower() != ".pdf":
        raise TranslationError(f"Input must have a .pdf extension: {source}")
    with source.open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise TranslationError(f"Input does not contain a PDF header: {source}")
    return source


def _describe(error: BaseException) -> str:
    """Flatten an exception chain into one line.

    The core wraps every failure in a generic "Failed to translate <path>", so
    reporting only str(error) hides the reason the document actually failed.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        parts.append(f"{type(current).__name__}: {message}" if message else type(current).__name__)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def _pages_to_indices(pages: str | None) -> list[int] | None:
    if pages is None:
        return None
    indices: list[int] = []
    for item in pages.split(","):
        if "-" in item:
            start, end = (int(part) for part in item.split("-", 1))
            indices.extend(range(start - 1, end))
        else:
            indices.append(int(item) - 1)
    return indices


def _segment_envs(segments: Path | None, emit_segments: Path | None) -> dict[str, str]:
    """Resolve the handoff file paths that the translator reads through `envs`."""
    envs: dict[str, str] = {}
    if segments is not None:
        source = segments.expanduser().resolve()
        if not source.is_file():
            raise TranslationError(f"Segments file does not exist: {source}")
        envs["segments_in"] = str(source)
    if emit_segments is not None:
        emitted = emit_segments.expanduser().resolve()
        emitted.parent.mkdir(parents=True, exist_ok=True)
        envs["segments_out"] = str(emitted)
    return envs


_LAYOUT_MODEL: dict[str | None, object] = {}
# The desktop app warms the model on a background thread while the user is still
# picking files, so two threads really can arrive here at once. On a first run
# onnxruntime serialises a 71 MB optimised graph next to the model, and two of
# those writing the same path would race over a file the next run has to trust.
_LAYOUT_MODEL_LOCK = threading.Lock()


def _layout_model(bundled_path: str | None) -> object:
    """Return the layout model, loading it at most once per process.

    Building the inference session takes about a second and a half, which a
    batch of files would otherwise pay for every single document.
    """
    with _LAYOUT_MODEL_LOCK:
        if bundled_path not in _LAYOUT_MODEL:
            from pdf2zh.doclayout import OnnxModel

            _LAYOUT_MODEL[bundled_path] = (
                OnnxModel(bundled_path) if bundled_path else OnnxModel.load_available()
            )
        return _LAYOUT_MODEL[bundled_path]


def load_layout_model() -> object:
    """Build the inference session, raising if the native stack is unusable.

    The packaged smoke test calls this: onnxruntime and its model are the
    heaviest thing a frozen build has to load, and a bundle that cannot do it
    is broken for every document, not just the first.
    """
    return _layout_model(os.environ.get("PDF_TRANSLATE_MODEL"))


def preload_layout_model() -> None:
    """Build the inference session ahead of the first translation.

    Safe to call from any thread and any number of times; it never raises,
    because a failed warm-up only means the first translation pays the cost
    it used to pay anyway.
    """
    try:
        load_layout_model()
    except Exception:  # noqa: BLE001 - a warm-up failure must stay invisible
        pass


def _run_engine(
    source: Path,
    temp_output: Path,
    target_language: str,
    source_language: str,
    pages: str | None,
    threads: int,
    ignore_cache: bool,
    engine: str,
    envs: dict[str, str],
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Run the core and return how many segments were left untranslated."""
    from pdf2zh.high_level import translate

    # A packaged build ships the layout model so the first run needs no network.
    model = _layout_model(os.environ.get("PDF_TRANSLATE_MODEL"))

    # The core reports progress by handing its tqdm bar to a callback.
    callback = None
    if on_progress is not None:
        def callback(progress: object) -> None:
            on_progress(getattr(progress, "n", 0), getattr(progress, "total", 0) or 0)

    result = translate(
        files=[str(source)],
        output=str(temp_output),
        pages=_pages_to_indices(pages),
        lang_in=source_language,
        lang_out=target_language,
        service=engine,
        thread=threads,
        model=model,
        envs=envs,
        callback=callback,
        ignore_cache=ignore_cache,
    )
    if len(result) != 1:
        raise TranslationError("PDF core did not report one translated result")
    return int(result[0][1] or 0)


def translate_pdf(
    input_pdf: Path,
    output_dir: Path | None,
    *,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
    source_language: str = "auto",
    pages: str | None = None,
    threads: int = DEFAULT_THREADS,
    ignore_cache: bool = False,
    overwrite: bool = False,
    engine: str = "google",
    segments: Path | None = None,
    emit_segments: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Translation:
    """Translate one PDF, reporting any segments the engine could not translate."""
    _require_core()
    source = _validate_input(input_pdf)
    envs = _segment_envs(segments, emit_segments)

    destination: Path | None = None
    destination_dir: Path | None = None
    if output_dir is not None:
        destination_dir = output_dir.expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{source.stem}-{target_language}.pdf"
        if destination.exists() and not overwrite:
            raise TranslationError(
                f"Output already exists: {destination}. "
                "Pass --overwrite only with replacement authorization."
            )

    with tempfile.TemporaryDirectory(prefix="pdf-translate-", dir=destination_dir) as temp:
        temp_output = Path(temp)
        try:
            untranslated = _run_engine(
                source,
                temp_output,
                target_language,
                source_language,
                pages,
                threads,
                ignore_cache,
                engine,
                envs,
                on_progress,
            )
        except TranslationError:
            raise
        except Exception as error:
            raise TranslationError(f"PDF translation core failed: {_describe(error)}") from error

        if destination is None:
            return Translation(None, untranslated)

        generated = temp_output / f"{source.stem}-mono.pdf"
        if not generated.is_file():
            candidates = sorted(temp_output.glob("*-mono.pdf"))
            if len(candidates) != 1:
                names = ", ".join(path.name for path in temp_output.iterdir()) or "no files"
                raise TranslationError(f"Engine did not produce one translated PDF; found: {names}")
            generated = candidates[0]

        staged = destination_dir / f".{destination.name}.tmp"
        try:
            shutil.copyfile(generated, staged)
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)

    return Translation(destination, untranslated)


def _use_utf8_output() -> None:
    """Print Vietnamese paths on a legacy console codepage instead of crashing.

    Windows terminals still default to cp1252, which cannot encode Vietnamese, so
    a path like D:\\Tai lieu\\sach-vi.pdf would raise after the work was done.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_output()
    args = _parser().parse_args(argv)
    try:
        _validate_arguments(args)
        result = translate_pdf(
            args.input_pdf,
            args.output_dir,
            target_language=args.target_language,
            source_language=args.source_language,
            pages=args.pages,
            threads=args.threads,
            ignore_cache=args.ignore_cache,
            overwrite=args.overwrite,
            engine=args.engine,
            segments=args.segments,
            emit_segments=args.emit_segments,
        )
    except TranslationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if result.path is not None:
        print(f"Translated PDF: {result.path}")
    if result.untranslated:
        print(
            f"warning: {result.untranslated} segments stayed in the source language "
            "because the translation service could not be reached",
            file=sys.stderr,
        )
    if args.emit_segments is not None:
        emitted = args.emit_segments.expanduser().resolve()
        pending = sum(1 for line in emitted.open(encoding="utf-8") if line.strip())
        print(f"Segments left untranslated: {pending} -> {emitted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
