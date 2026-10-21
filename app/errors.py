#!/usr/bin/env python3
"""Turn an engine failure into something a user can read, act on, and report.

The queue row has space for one short line, which is never enough to diagnose a
failure on someone else's document. Users were sent a clipped English exception
and could neither understand it nor quote it back, so every failure here gets a
stable code, a plain summary in the interface language, one concrete
instruction, and the full technical text kept verbatim for the report.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.i18n import t

UNKNOWN_CODE = "E-UNK-99"


@dataclass(frozen=True)
class Failure:
    """One failure in the forms the app needs: label, guidance, evidence."""

    code: str
    summary: str
    advice: str
    detail: str

    @property
    def headline(self) -> str:
        """The single line the queue row shows."""
        return f"{self.summary} [{self.code}]"


# Matched in order against the flattened exception chain, so the most specific
# cause wins. A failure matches a code when any of its markers appears in the
# chain's type names or messages; each code's words live in app/i18n.py.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("E-VERIFY-01", ("VerificationDeferredError",)),
    ("E-BROWSER-01", ("BrowserRuntimeError",)),
    ("E-NET-08", ("RateLimitedError",)),
    ("E-NET-09", ("ServiceUnavailableError",)),
    (
        "E-OCR-07",
        ("OCR dependencies are missing", "OCR found no text that could be translated safely",
         "OCR failed", "could not encode cleaned raster"),
    ),
    ("E-OUT-11", ("Could not save the translated PDF",)),
    # Before E-CORE-01: "CoreMLExecutionProvider_CoreML" lowercased holds
    # "_core", which once sent Mac users off to reinstall a complete app.
    ("E-MODEL-10", ("LayoutModelError", "ONNXRuntimeError", "onnxruntime_pybind11_state")),
    (
        "E-CORE-01",
        ("pikepdf._core", "._core", "extension library", "DLL load failed",
         "ImportError", "ModuleNotFoundError"),
    ),
    ("E-OUT-05", ("already exists", "Output already exists")),
    (
        "E-PDF-02",
        ("FzErrorSyntax", "damaged beyond repair", "Could not repair",
         "invalid key in dict", "FzErrorFormat", "PdfError"),
    ),
    ("E-PDF-01", ("does not exist", "must have a .pdf extension", "does not contain a PDF header")),
    ("E-PDF-03", ("scanned", "image-only", "OCR", "no extractable text")),
    (
        "E-NET-04",
        ("ConnectionError", "Timeout", "timed out", "HTTPError", "SSLError",
         "Max retries", "getaddrinfo", "Google Translate", "RetryError"),
    ),
    ("E-MEM-06", ("MemoryError", "not enough memory", "Cannot allocate")),
)

ERROR_CODES = tuple(code for code, _markers in _RULES) + (UNKNOWN_CODE,)


def flatten(error: BaseException) -> str:
    """Join an exception chain into one searchable string.

    The core wraps every failure in a generic wrapper, so only the chain says
    what actually went wrong.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        parts.append(f"{type(current).__name__}: {message}" if message
                     else type(current).__name__)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def describe_failure(error: BaseException) -> Failure:
    """Classify one failure. Unrecognised causes keep their original text."""
    detail = flatten(error)
    haystack = detail.lower()
    code = next(
        (code for code, markers in _RULES
         if any(marker.lower() in haystack for marker in markers)),
        UNKNOWN_CODE,
    )
    return Failure(code, t(f"{code}.summary"), t(f"{code}.advice"), detail)


def report_text(failure: Failure, source: Path, version: str, log: Path | None = None) -> str:
    """Build the block the user copies into a bug report.

    Everything a maintainer needs to reproduce, and nothing the user has to
    type out by hand.
    """
    lines = [
        t("report.title"),
        t("report.time", value=f"{datetime.now():%Y-%m-%d %H:%M:%S}"),
        t("report.version", value=version),
        t("report.system", value=f"{platform.system()} {platform.release()} ({platform.machine()})"),
        t("report.python", value=sys.version.split()[0]),
        t("report.file", value=source.name),
        t("report.code", value=f"{failure.code}  {failure.summary}"),
        "",
        t("report.details"),
        failure.detail,
    ]
    if log is not None:
        lines += ["", t("report.log", value=log)]
    return "\n".join(lines)
