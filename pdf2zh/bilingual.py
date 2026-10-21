"""Assemble a bilingual PDF from a source document and its finished translation.

The engine could interleave the two documents while it writes the mono file,
but it would do so on every run and double the finalisation of a textbook for
a file most users never asked for. Building it afterwards, only when asked,
from two complete PDFs keeps the mono output exactly what it was: both files
are read into memory and neither is ever opened for writing.

Pages are placed as Form XObjects, so text stays selectable and searchable in
both languages and nothing is rasterised.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

import pymupdf

from pdf2zh.high_level import pdf_write_options

logger = logging.getLogger(__name__)

BILINGUAL_LAYOUTS = ("side-by-side", "alternating", "layers")
ORIGINAL_LAYER = "Original"
_METADATA_KEYS = ("title", "author", "subject", "keywords", "creator", "producer")


class BilingualError(RuntimeError):
    """The bilingual file could not be built; the translation itself is unaffected."""


def write_bilingual(source: Path, translation: Path, output: Path, layout: str) -> None:
    """Write ``output`` with ``source`` and ``translation`` in the chosen layout.

    side-by-side: one wide page per source page, original left, translation right.
    alternating:  original page, then its translation, for every page.
    layers:       the translation, with the original on an optional-content
                  layer that starts hidden; switching it on in a viewer's
                  layer panel shows the original page instead.
    """
    if layout not in BILINGUAL_LAYOUTS:
        raise BilingualError(f"Unsupported bilingual layout: {layout}")
    source_bytes = Path(source).read_bytes()
    try:
        with (
            pymupdf.open(stream=source_bytes, filetype="pdf") as original,
            pymupdf.open(stream=Path(translation).read_bytes(), filetype="pdf") as translated,
        ):
            if original.page_count != translated.page_count:
                raise BilingualError(
                    f"The translation has {translated.page_count} pages and the source "
                    f"{original.page_count}; they cannot be paired"
                )
            if layout == "layers":
                # The translation itself, so its labels, form fields and tags
                # all survive; only a hidden layer is added to each page.
                _add_original_layer(original, translated)
                translated.save(
                    Path(output), **pdf_write_options(translated.page_count, len(source_bytes))
                )
                return
            build = _alternating if layout == "alternating" else _side_by_side
            with build(original, translated) as combined:
                combined.set_metadata(
                    {key: translated.metadata.get(key) or "" for key in _METADATA_KEYS}
                )
                combined.save(
                    Path(output), **pdf_write_options(combined.page_count, len(source_bytes))
                )
    except BilingualError:
        raise
    except Exception as error:
        # Any MuPDF failure means the same thing here.
        raise BilingualError(f"Could not build the bilingual PDF: {error}") from error


def _alternating(original: pymupdf.Document, translated: pymupdf.Document) -> pymupdf.Document:
    """Original page, then its translation.

    Links are put back by hand: insert_pdf dropped every link to a named
    destination, which is how journals link citations to their references.
    """
    count = original.page_count
    original_links = [_page_links(page) for page in original]
    translated_links = [_page_links(page) for page in translated]
    combined = pymupdf.open()
    combined.insert_pdf(original, links=False)
    combined.insert_pdf(translated, links=False)
    for index in range(count):
        combined.move_page(count + index, 2 * index + 1)
    for index in range(count):
        _insert_links(combined[2 * index], original_links[index], lambda page: 2 * page)
        _insert_links(combined[2 * index + 1], translated_links[index], lambda page: 2 * page + 1)
    _copy_outline(translated, combined, lambda page: 2 * page - 1)
    return combined


def _side_by_side(original: pymupdf.Document, translated: pymupdf.Document) -> pymupdf.Document:
    """One page holding the original on the left and the translation on the right."""
    original_links = [_page_links(page) for page in original]
    translated_links = [_page_links(page) for page in translated]
    original_rotations = _unrotate(original)
    translated_rotations = _unrotate(translated)

    combined = pymupdf.open()
    splits = []
    for index in range(original.page_count):
        left = _displayed_size(original[index].rect, original_rotations[index])
        right = _displayed_size(translated[index].rect, translated_rotations[index])
        page = combined.new_page(width=left[0] + right[0], height=max(left[1], right[1]))
        _show(page, pymupdf.Rect(0, 0, *left), original, index, original_rotations[index])
        _show(
            page,
            pymupdf.Rect(left[0], 0, left[0] + right[0], right[1]),
            translated,
            index,
            translated_rotations[index],
        )
        splits.append(left[0])
    # Only once every page exists: a link to a later page, such as a citation
    # to the reference list, cannot be created before its target.
    for index, page in enumerate(combined):
        _insert_links(page, original_links[index], lambda target: target)
        _insert_links(page, translated_links[index], lambda target: target, splits[index])
    _copy_outline(translated, combined, lambda page: page)
    return combined


def _add_original_layer(original: pymupdf.Document, translated: pymupdf.Document) -> None:
    """Put the original over each translated page, on a layer that starts hidden.

    The translation's own content is not touched. The layer paints the page
    white and the original over it, so switching it on shows exactly the
    source page, and a viewer that honours the default state shows only the
    translation.
    """
    layer = translated.add_ocg(ORIGINAL_LAYER, on=False)
    _unrotate(original)
    for index, page in enumerate(translated):
        rotation = page.rotation
        # Both pages share one media box, so drawing the unrotated original
        # into the unrotated translation lines them up in PDF space, and the
        # restored /Rotate then turns both together.
        if rotation:
            page.set_rotation(0)
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1), width=0, oc=layer)
        if original[index].get_contents():
            page.show_pdf_page(page.rect, original, index, oc=layer)
        if rotation:
            page.set_rotation(rotation)


def _unrotate(document: pymupdf.Document) -> list[int]:
    """Clear /Rotate on an in-memory copy and return what each page had.

    show_pdf_page places the unrotated content stream, so a rotated page
    landed sideways; its own ``rotate`` argument, applied to an unrotated page,
    reproduces exactly what a viewer shows.
    """
    rotations = []
    for page in document:
        rotations.append(page.rotation)
        if page.rotation:
            page.set_rotation(0)
    return rotations


def _displayed_size(rect: pymupdf.Rect, rotation: int) -> tuple[float, float]:
    if rotation % 180:
        return rect.height, rect.width
    return rect.width, rect.height


def _show(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    document: pymupdf.Document,
    index: int,
    rotation: int,
) -> None:
    # MuPDF refuses to show a page with no content stream; a blank page in a
    # book is still a page, so its half simply stays blank.
    if document[index].get_contents():
        page.show_pdf_page(rect, document, index, rotate=-rotation)


def _page_links(page: pymupdf.Page) -> list[dict]:
    """The page's web links and its links to pages of the same document.

    A link to a named destination becomes a link to the page it names: the
    names belong to the document it came from, not to the one being built.
    Coordinates are the ones a viewer shows.
    """
    links = []
    for link in page.get_links():
        kind = link.get("kind")
        if kind == pymupdf.LINK_URI and link.get("uri"):
            links.append({"kind": kind, "from": pymupdf.Rect(link["from"]), "uri": link["uri"]})
        elif kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and link.get("page", -1) >= 0:
            links.append({
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(link["from"]),
                "page": link["page"],
                "to": pymupdf.Point(link.get("to") or (0, 0)),
                "zoom": link.get("zoom") or 0,
            })
    return links


def _insert_links(
    page: pymupdf.Page,
    links: Sequence[dict],
    page_of: Callable[[int], int],
    offset: float = 0.0,
) -> None:
    """Recreate ``links`` on ``page``, shifted right by ``offset`` points."""
    for link in links:
        moved = dict(link, **{"from": link["from"] + (offset, 0, offset, 0)})
        if link["kind"] == pymupdf.LINK_GOTO:
            moved["page"] = page_of(link["page"])
            moved["to"] = link["to"] + (offset, 0)
        try:
            page.insert_link(moved)
        except Exception as error:  # noqa: BLE001 - a link MuPDF cannot recreate is only a link
            logger.debug("Left out a link on page %s: %s", page.number + 1, error)


def _copy_outline(
    source: pymupdf.Document, target: pymupdf.Document, page_of: Callable[[int], int]
) -> None:
    """Carry the bookmarks over, pointing each at its page in the new order.

    The outline is a convenience: one MuPDF cannot rebuild is left out rather
    than failing a file whose pages are all in place.
    """
    try:
        outline = source.get_toc(simple=True)
        if outline:
            target.set_toc(
                [[level, title, page_of(page) if page > 0 else page]
                 for level, title, page in outline]
            )
    except Exception as error:  # noqa: BLE001
        logger.debug("Left out the outline: %s", error)
