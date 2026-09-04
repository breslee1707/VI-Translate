"""Fetch, validate, hash, and select the 36-document OCR benchmark corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402
import requests  # noqa: E402

import benchmark_paths as paths  # noqa: E402

SOURCE_MANIFEST = Path(__file__).with_name("benchmark_sources.json")
NASA_API = "https://ntrs.nasa.gov/api/citations/{citation}"
NASA_ROOT = "https://ntrs.nasa.gov"
MAX_DOCUMENT_BYTES = 250 * 1024 * 1024
TARGET_DOCUMENTS = 36
TARGET_PAGES = 144
CHUNK_SIZE = 1024 * 1024
DOWNLOAD_ATTEMPTS = 4


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_source(session: requests.Session, item: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(item)
    if item["provider"] != "nasa":
        return resolved
    response = session.get(NASA_API.format(citation=item["locator"]), timeout=30)
    response.raise_for_status()
    metadata = response.json()
    determination = metadata.get("copyright", {}).get("determinationType", "")
    if metadata.get("distribution") != "PUBLIC" or "PERMITTED" not in determination:
        raise RuntimeError(
            f"{item['id']}: NASA record is not cleared for public benchmark use ({determination})"
        )
    downloads = [
        row
        for row in metadata.get("downloads", [])
        if row.get("mimetype") == "application/pdf"
    ]
    if not downloads:
        raise RuntimeError(f"{item['id']}: NASA record has no PDF")
    link = downloads[0].get("links", {}).get("pdf") or downloads[0]["links"]["original"]
    resolved.update(
        url=NASA_ROOT + link,
        landing_url=f"https://ntrs.nasa.gov/citations/{item['locator']}",
        rights=f"NASA {determination}",
        title=metadata.get("title", item["id"]),
    )
    return resolved


def download(session: requests.Session, item: dict[str, Any], destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        offset = partial.stat().st_size if partial.is_file() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with session.get(
                item["url"], headers=headers, stream=True, timeout=(30, 120)
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if "pdf" not in content_type and not item["url"].lower().endswith(".pdf"):
                    raise RuntimeError(
                        f"{item['id']}: server did not return a PDF ({content_type})"
                    )
                append = bool(offset and response.status_code == 206)
                if offset and not append:
                    offset = 0
                expected = response.headers.get("Content-Length")
                expected_size = offset + int(expected) if expected else None
                with partial.open("ab" if append else "wb") as target:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if not chunk:
                            continue
                        target.write(chunk)
                        if target.tell() > MAX_DOCUMENT_BYTES:
                            raise RuntimeError(f"{item['id']}: document exceeds 250 MiB")
                if expected_size is not None and partial.stat().st_size != expected_size:
                    raise RuntimeError(
                        f"{item['id']}: short download "
                        f"({partial.stat().st_size}/{expected_size} bytes)"
                    )
            last_error = None
            break
        except (requests.RequestException, OSError, RuntimeError) as error:
            last_error = error
            if attempt == DOWNLOAD_ATTEMPTS:
                break
            print(
                f"    retry {attempt}/{DOWNLOAD_ATTEMPTS - 1} from "
                f"{partial.stat().st_size if partial.is_file() else 0} bytes: {error}"
            )
            time.sleep(2 ** (attempt - 1))
    if last_error is not None:
        raise RuntimeError(f"{item['id']}: download failed after retries") from last_error
    if b"%PDF-" not in partial.read_bytes()[:1024]:
        raise RuntimeError(f"{item['id']}: downloaded payload has no PDF header")
    partial.replace(destination)


def inspect_pdf(path: Path) -> dict[str, Any]:
    with pymupdf.open(path) as document:
        if document.is_encrypted:
            raise RuntimeError(f"encrypted benchmark PDF is not allowed: {path.name}")
        if document.embfile_count():
            raise RuntimeError(f"embedded files are not allowed: {path.name}")
        if document.page_count < 1:
            raise RuntimeError(f"empty benchmark PDF: {path.name}")
        sampled = []
        for index in sorted({0, document.page_count // 2, document.page_count - 1}):
            page = document[index]
            sampled.append(
                {
                    "page": index,
                    "characters": len(page.get_text().strip()),
                    "images": len(page.get_images(full=True)),
                    "rotation": page.rotation,
                }
            )
        return {"page_count": document.page_count, "sampled": sampled}


def page_preference(page_count: int, bucket: str) -> list[int]:
    if page_count <= 4:
        return list(range(page_count))
    first = min(4, page_count - 1) if "scan" in bucket else 0
    preferred = [first, page_count // 3, (page_count * 2) // 3, page_count - 1]
    ordered = []
    for index in preferred + list(range(page_count)):
        if 0 <= index < page_count and index not in ordered:
            ordered.append(index)
    return ordered


def assign_pages(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        preference = page_preference(row["page_count"], row["bucket"])
        row["page_preference"] = preference
        row["selected_pages"] = preference[:4]
    selected = sum(len(row["selected_pages"]) for row in rows)
    while selected < TARGET_PAGES:
        progressed = False
        for row in rows:
            for page in row["page_preference"]:
                if page not in row["selected_pages"]:
                    row["selected_pages"].append(page)
                    selected += 1
                    progressed = True
                    break
            if selected == TARGET_PAGES:
                break
        if not progressed:
            raise RuntimeError(f"corpus has only {selected} selectable pages")
    if selected != TARGET_PAGES:
        raise RuntimeError(f"page selection produced {selected}, expected {TARGET_PAGES}")
    for row in rows:
        row.pop("page_preference", None)
        row["selected_pages"].sort()


def write_selected(row: dict[str, Any]) -> None:
    source = paths.safe_path(paths.SOURCES / f"{row['id']}.pdf")
    destination = paths.safe_path(paths.SELECTED / f"{row['id']}.pdf")
    output = pymupdf.open()
    with pymupdf.open(source) as document:
        for page in row["selected_pages"]:
            output.insert_pdf(document, from_page=page, to_page=page)
    output.save(destination, garbage=3, deflate=True)
    output.close()
    row["selected_path"] = str(destination.relative_to(paths.REPO_ROOT))
    row["selected_sha256"] = sha256(destination)


def load_existing_lock() -> dict[str, dict[str, Any]]:
    if not paths.LOCK.is_file():
        return {}
    rows = json.loads(paths.LOCK.read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="download sources again")
    parser.add_argument("--only", action="append", default=[], help="fetch one source id")
    args = parser.parse_args(argv)

    paths.ensure_tree()
    sources = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if len(sources) != TARGET_DOCUMENTS:
        raise RuntimeError(f"manifest has {len(sources)} documents, expected {TARGET_DOCUMENTS}")
    if args.only:
        wanted = set(args.only)
        sources = [item for item in sources if item["id"] in wanted]
        missing = wanted - {item["id"] for item in sources}
        if missing:
            raise RuntimeError(f"unknown source ids: {', '.join(sorted(missing))}")

    session = requests.Session()
    session.headers["User-Agent"] = "VI-Translate-OCR-Benchmark/1.0"
    old_lock = load_existing_lock()
    rows = []
    for position, source in enumerate(sources, 1):
        item = resolve_source(session, source)
        destination = paths.safe_path(paths.SOURCES / f"{item['id']}.pdf")
        if args.refresh or not destination.is_file():
            print(f"[{position:02}/{len(sources):02}] downloading {item['id']}")
            download(session, item, destination)
        digest = sha256(destination)
        locked = old_lock.get(item["id"])
        if locked and not args.refresh and digest != locked.get("sha256"):
            raise RuntimeError(f"{item['id']}: local source differs from locked checksum")
        row = dict(item)
        row.update(
            path=str(destination.relative_to(paths.REPO_ROOT)),
            sha256=digest,
            bytes=destination.stat().st_size,
            **inspect_pdf(destination),
        )
        rows.append(row)
        print(f"    {row['page_count']} pages, {row['bytes'] / 1e6:.1f} MB")

    if args.only:
        return 0
    assign_pages(rows)
    for row in rows:
        write_selected(row)
    paths.LOCK.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    total_bytes = sum(row["bytes"] for row in rows)
    print(
        f"Locked {len(rows)} PDFs / {sum(len(row['selected_pages']) for row in rows)} pages "
        f"({total_bytes / 1e9:.2f} GB) -> {paths.LOCK}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
