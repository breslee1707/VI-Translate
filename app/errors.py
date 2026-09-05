#!/usr/bin/env python3
"""Turn an engine failure into something a user can read, act on, and report.

The queue row has space for one short line, which is never enough to diagnose a
failure on someone else's document. Users were sent a clipped English exception
and could neither understand it nor quote it back, so every failure here gets a
stable code, a plain Vietnamese summary, one concrete instruction, and the full
technical text kept verbatim for the report.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
# cause wins. Each entry is (code, markers, summary, advice); a failure matches
# when any marker appears in the chain's type names or messages.
_RULES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "E-OCR-07",
        ("OCR dependencies are missing", "OCR found no text that could be translated safely",
         "OCR failed", "could not encode cleaned raster"),
        "OCR không thể xử lý trang scan an toàn",
        "Ứng dụng đã giữ nguyên trang để tránh làm hỏng bảng, công thức hoặc hình. "
        "Thử chế độ OCR Chuẩn; nếu vẫn lỗi, dùng PDF có lớp chữ hoặc gửi báo cáo này.",
    ),
    (
        "E-CORE-01",
        ("pikepdf", "_core", "extension library", "DLL load failed",
         "ImportError", "ModuleNotFoundError"),
        "Ứng dụng thiếu thư viện nội bộ",
        "Hãy giải nén TOÀN BỘ thư mục rồi chạy lại từ thư mục đã giải nén "
        "(không chạy trực tiếp trong file .zip), và thêm ngoại lệ cho phần mềm "
        "diệt virus. Nếu vẫn lỗi, gửi báo cáo này cho nhà phát triển.",
    ),
    (
        "E-OUT-05",
        ("already exists", "Output already exists"),
        "Đã có bản dịch trước đó",
        'Tick ô "Ghi đè file đã dịch trước đó" rồi dịch lại, hoặc xoá file cũ '
        "trong thư mục translated.",
    ),
    (
        "E-PDF-02",
        ("FzErrorSyntax", "damaged beyond repair", "Could not repair",
         "invalid key in dict", "FzErrorFormat", "PdfError"),
        "File PDF bị lỗi cấu trúc",
        "File này hỏng ở mức không sửa được. Hãy mở bằng trình đọc PDF rồi "
        '"In ra PDF" (Print to PDF) để tạo bản sạch, sau đó dịch bản đó.',
    ),
    (
        "E-PDF-01",
        ("does not exist", "must have a .pdf extension", "does not contain a PDF header"),
        "File không phải PDF hợp lệ",
        "Kiểm tra lại file: nó phải là PDF thật và vẫn còn ở đúng vị trí cũ.",
    ),
    (
        "E-PDF-03",
        ("scanned", "image-only", "OCR", "no extractable text"),
        "PDF chỉ chứa ảnh scan",
        "Bật OCR Tự động trong ứng dụng. Vùng bảng, công thức hoặc hình không an "
        "toàn sẽ được giữ nguyên và kết quả được báo là dịch một phần.",
    ),
    (
        "E-NET-04",
        ("ConnectionError", "Timeout", "timed out", "HTTPError", "SSLError",
         "Max retries", "getaddrinfo", "Google Translate", "RetryError"),
        "Không kết nối được dịch vụ dịch",
        "Kiểm tra mạng hoặc VPN rồi dịch lại. Các đoạn đã dịch được vẫn giữ "
        "trong bộ nhớ đệm nên lần chạy sau sẽ nhanh hơn.",
    ),
    (
        "E-MEM-06",
        ("MemoryError", "not enough memory", "Cannot allocate"),
        "Máy không đủ bộ nhớ cho file này",
        "Đóng bớt ứng dụng khác, hoặc dịch file lớn thành từng phần nhỏ hơn.",
    ),
)


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
    for code, markers, summary, advice in _RULES:
        if any(marker.lower() in haystack for marker in markers):
            return Failure(code, summary, advice, detail)
    return Failure(
        UNKNOWN_CODE,
        "Lỗi không xác định",
        "Hãy gửi báo cáo bên dưới cho nhà phát triển để được hỗ trợ.",
        detail,
    )


def report_text(failure: Failure, source: Path, version: str, log: Path | None = None) -> str:
    """Build the block the user copies into a bug report.

    Everything a maintainer needs to reproduce, and nothing the user has to
    type out by hand.
    """
    lines = [
        "PDF Translate - báo cáo lỗi",
        f"Thời điểm : {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Phiên bản : {version}",
        f"Hệ điều hành: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python    : {sys.version.split()[0]}",
        f"File      : {source.name}",
        f"Mã lỗi    : {failure.code}  {failure.summary}",
        "",
        "Chi tiết kỹ thuật:",
        failure.detail,
    ]
    if log is not None:
        lines += ["", f"Log đầy đủ: {log}"]
    return "\n".join(lines)
