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
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pdf2zh.high_level import TranslationReport

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
    reasons: Mapping[str, int] = MappingProxyType({})
    image_only_pages: tuple[int, ...] = ()
    ocr_pages: tuple[int, ...] = ()


# record_translation_failure passes up either an exception class name or one of
# the converter's fit rules. Reporting them as one sentence sent users to check
# a network that was never the problem, so they are separated here.
_FIT_MARKERS = ("font size", "cannot fit")
_FORMULA_REASON = "FormulaPlaceholderError"
_TOO_LONG_REASON = "SegmentTooLongError"


def _count_of_segments(count: int) -> str:
    return f"{count} segment" if count == 1 else f"{count} segments"


def _describe_failures(reasons: Mapping[str, int]) -> list[str]:
    """Turn raw skip reasons into lines that say what the user can do."""
    def is_fit(reason: str) -> bool:
        return any(marker in reason for marker in _FIT_MARKERS)

    fit = sum(count for reason, count in reasons.items() if is_fit(reason))
    formula = reasons.get(_FORMULA_REASON, 0)
    too_long = reasons.get(_TOO_LONG_REASON, 0)
    engine = {
        reason: count
        for reason, count in reasons.items()
        if reason not in (_FORMULA_REASON, _TOO_LONG_REASON) and not is_fit(reason)
    }

    lines: list[str] = []
    if fit:
        lines.append(
            f"{_count_of_segments(fit)} stayed in the source language because the "
            "translation did not fit the original line at the smallest allowed size"
        )
    if formula:
        lines.append(
            f"{_count_of_segments(formula)} stayed in the source language because the "
            "translation came back with damaged formula markers"
        )
    if too_long:
        lines.append(
            f"{_count_of_segments(too_long)} stayed in the source language because the "
            "paragraph was longer than the translation service accepts in one request"
        )
    if engine:
        names = ", ".join(f"{name} x{count}" for name, count in sorted(engine.items()))
        lines.append(
            f"{_count_of_segments(sum(engine.values()))} stayed in the source language "
            f"because the translation engine failed ({names})"
        )
    return lines


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
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="also translate the text drawn inside images, such as a scan or a "
        "labelled diagram; slower, and needs no network beyond the translator",
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
        # The handoff flow emits every segment once and rebuilds from the filled
        # table. OCR text would have to survive both runs identically, which
        # means recognizing the same pixels twice and trusting the result to
        # match. Refuse rather than emit a table the rebuild cannot honour.
        if args.ocr:
            raise TranslationError("--ocr is not supported with --engine handoff")
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
    *,
    ocr: bool = False,
) -> "TranslationReport":
    """Run the core and return what it could not translate, and why."""
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
        ocr=ocr,
    )
    if len(result) != 1:
        raise TranslationError("PDF core did not report one translated result")
    return result[0][1]


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
    ocr: bool = False,
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
            report = _run_engine(
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
                ocr=ocr,
            )
        except TranslationError:
            raise
        except Exception as error:
            raise TranslationError(f"PDF translation core failed: {_describe(error)}") from error

        # Nothing was translatable, so the engine produced a copy of the source
        # with no translated text in it. Handing that over as a finished
        # translation is the one outcome the preservation rules forbid outright:
        # say what the document actually needs instead. The message carries the
        # words app/errors.py matches for E-PDF-03.
        if report.translatable_segments == 0 and report.ocr_segments == 0:
            # Nothing outside the images was translatable, so every reason in
            # the report came from the OCR pass. It read the page and lost the
            # text somewhere later - to the network, most often - and saying
            # the scan is illegible would send the user to fix the wrong thing.
            # The word "OCR" is deliberately absent: app/errors.py matches it
            # for E-PDF-03, and this failure is not the scan's fault.
            if ocr and report.reasons:
                why = "; ".join(_describe_failures(report.reasons))
                raise TranslationError(
                    f"Nothing in {source.name} could be translated: the text inside the "
                    f"images was read, but {why}."
                )
            if ocr:
                raise TranslationError(
                    f"No text could be extracted from {source.name}: the selected pages "
                    "are image-only scans and OCR read nothing in them. Check that the "
                    "scan is legible and the right way up."
                )
            raise TranslationError(
                f"No text could be extracted from {source.name}: the selected pages are "
                "image-only scans. Translate again with OCR enabled, or run OCR on the "
                "PDF first and translate the result."
            )

        untranslated = len(report.failures)
        # A page the OCR pass translated is no longer an untranslated scan, and
        # reporting it as one would send the user to fix what is already fixed.
        ocr_pages = tuple(sorted(report.ocr_pages))
        image_only = tuple(sorted(set(report.image_only_pages) - set(ocr_pages)))
        if destination is None:
            return Translation(None, untranslated, report.reasons, image_only, ocr_pages)

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

    return Translation(destination, untranslated, report.reasons, image_only, ocr_pages)


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
            ocr=args.ocr,
        )
    except TranslationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if result.path is not None:
        print(f"Translated PDF: {result.path}")
    if result.ocr_pages:
        numbers = ", ".join(str(page + 1) for page in result.ocr_pages)
        label = "page" if len(result.ocr_pages) == 1 else "pages"
        print(f"OCR translated the text inside images on {label} {numbers}")
    if result.image_only_pages:
        numbers = ", ".join(str(page + 1) for page in result.image_only_pages)
        advice = "run again with --ocr" if not args.ocr else "OCR read nothing there"
        print(
            f"warning: page {numbers} is an image-only scan and was left "
            f"untranslated; {advice}"
            if len(result.image_only_pages) == 1
            else f"warning: pages {numbers} are image-only scans and were left "
            f"untranslated; {advice}",
            file=sys.stderr,
        )
    for line in _describe_failures(result.reasons):
        print(f"warning: {line}", file=sys.stderr)
    if args.emit_segments is not None:
        emitted = args.emit_segments.expanduser().resolve()
        pending = sum(1 for line in emitted.open(encoding="utf-8") if line.strip())
        print(f"Segments left untranslated: {pending} -> {emitted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
