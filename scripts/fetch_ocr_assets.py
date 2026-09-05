#!/usr/bin/env python3
"""Download and verify every OCR model shipped by the desktop application."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pdf2zh.ocr import verify_ocr_runtime  # noqa: E402


def main() -> None:
    verify_ocr_runtime()
    print("OCR standard/enhanced models are ready")


if __name__ == "__main__":
    main()
