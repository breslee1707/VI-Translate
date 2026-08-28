"""Preservation rules that distinguish the Code4Life PDF translation core."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

FORMULA_FONT_PATTERN = re.compile(
    r"(CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|"
    r"stmary|.*Mono|.*Code|.*Sym|.*Math|.*Typewriter|Cousine|Consolas|Menlo|"
    r"Monaco|Inconsolata|Source.?Code|Fira.?Code|DejaVu.?Sans.?Mono|"
    r"Liberation.?Mono|Courier)"
)

MATH_OPERATOR_PATTERN = re.compile(
    r"[=≤≥≈≠±×÷·∑∫√∞∝+*/^]"
)
PROSE_WORD_PATTERN = re.compile(r"[a-z]{3,}")
STACKED_TOKEN_PATTERN = re.compile(
    r"[A-Za-z\u0370-\u03ff][A-Za-z\u0370-\u03ff0-9%]*"
)

BULLET_CHARACTERS = frozenset(
    ("•", "■", "□", "▪", "▸", "▹", "►", "▶", "●", "○", "◆", "◇", "★", "☆", "‣", "⬤")
)

LANGUAGE_LINE_HEIGHT = {
    "zh-cn": 1.4,
    "zh-tw": 1.4,
    "zh-hans": 1.4,
    "zh-hant": 1.4,
    "zh": 1.4,
    "ja": 1.1,
    "ko": 1.2,
    "en": 1.2,
    "ar": 1.0,
    "ru": 0.8,
    "uk": 0.8,
    "ta": 0.8,
    "vi": 1.2,
}


@dataclass(frozen=True)
class PreservationDecision:
    """A page classification whose layout must remain untouched."""

    kind: str
    detail: str


@dataclass(frozen=True)
class TableTextCluster:
    """A visual text group inside a table cell, separated from codes/units."""

    bbox: tuple[float, float, float, float]
    text: str
    words: tuple[Sequence[Any], ...]


def is_formula_font(font_name: str) -> bool:
    """Return whether a font name marks formula or code text."""
    return FORMULA_FONT_PATTERN.match(font_name) is not None


def line_height_for_language(language: str) -> float:
    """Return the translation line-height multiplier for a target language."""
    return LANGUAGE_LINE_HEIGHT.get(language.lower(), 1.1)


def _rect(value: Sequence[Any]) -> tuple[float, float, float, float] | None:
    if len(value) < 4:
        return None
    try:
        return tuple(float(item) for item in value[:4])
    except (TypeError, ValueError):
        return None


def _inside_any(
    rectangle: tuple[float, float, float, float],
    regions: Iterable[Sequence[Any]],
) -> bool:
    x0, y0, x1, y1 = rectangle
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for region in regions:
        bounds = _rect(region)
        if bounds is None:
            continue
        rx0, ry0, rx1, ry1 = bounds
        if rx0 <= cx <= rx1 and ry0 <= cy <= ry1:
            return True
    return False


def formula_regions(
    blocks: Iterable[Sequence[Any]],
    words: Iterable[Sequence[Any]],
    *,
    stacked_exclusions: Iterable[Sequence[Any]] = (),
) -> list[tuple[float, float, float, float]]:
    """Return ordinary-font regions whose exact mathematical layout must survive.

    The layout model catches dedicated formula fonts well, but technical PDFs
    often typeset equations in the same font as their prose. Operator-heavy
    blocks without prose words are equations. A second geometry rule catches an
    inline stacked fraction such as F1/b0 even when it sits inside a prose block.
    """
    protected: list[tuple[float, float, float, float]] = []
    for block in blocks:
        bounds = _rect(block)
        if bounds is None or len(block) < 5:
            continue
        compact = " ".join(str(block[4]).split())
        if (
            compact
            and MATH_OPERATOR_PATTERN.search(compact)
            and PROSE_WORD_PATTERN.search(compact) is None
        ):
            protected.append(bounds)

    candidates = list(words)
    exclusions = tuple(stacked_exclusions)
    for index, upper in enumerate(candidates):
        upper_bounds = _rect(upper)
        if upper_bounds is None or len(upper) < 8:
            continue
        upper_text = str(upper[4])
        if (
            len(upper_text) > 4
            or STACKED_TOKEN_PATTERN.fullmatch(upper_text) is None
            or not any(character.isdigit() for character in upper_text)
            or _inside_any(upper_bounds, exclusions)
        ):
            continue
        ux0, uy0, ux1, uy1 = upper_bounds
        for lower in candidates[index + 1 :]:
            lower_bounds = _rect(lower)
            if lower_bounds is None or len(lower) < 8:
                continue
            if upper[5] != lower[5] or upper[6] == lower[6]:
                continue
            lower_text = str(lower[4])
            if (
                len(lower_text) > 4
                or STACKED_TOKEN_PATTERN.fullmatch(lower_text) is None
                or not any(character.isdigit() for character in lower_text)
                or _inside_any(lower_bounds, exclusions)
            ):
                continue
            lx0, ly0, lx1, ly1 = lower_bounds
            overlap = max(0.0, min(ux1, lx1) - max(ux0, lx0))
            smaller_width = min(ux1 - ux0, lx1 - lx0)
            centre_gap = abs((uy0 + uy1) / 2 - (ly0 + ly1) / 2)
            max_height = max(uy1 - uy0, ly1 - ly0)
            if (
                smaller_width > 0
                and overlap / smaller_width >= 0.6
                and 2 < centre_gap <= 1.5 * max_height
            ):
                protected.append(
                    (
                        min(ux0, lx0),
                        min(uy0, ly0),
                        max(ux1, lx1),
                        max(uy1, ly1),
                    )
                )
    return protected


def matching_table_cells(
    model_bounds: Sequence[Any],
    tables: Iterable[Any],
    *,
    minimum_overlap: float = 0.5,
) -> list[tuple[float, float, float, float]]:
    """Return cells from the table whose area covers a model table detection.

    The model is the gate: PyMuPDF cell detection only enables translation when
    it can explain at least half of that already-recognised table. Unmatched
    tables keep the old, fully protected behaviour.
    """
    model = _rect(model_bounds)
    if model is None:
        return []
    mx0, my0, mx1, my1 = model
    model_area = max(0.0, mx1 - mx0) * max(0.0, my1 - my0)
    if model_area <= 0:
        return []

    best: Any = None
    best_overlap = 0.0
    for table in tables:
        bounds = _rect(getattr(table, "bbox", ()))
        if bounds is None:
            continue
        tx0, ty0, tx1, ty1 = bounds
        intersection = max(0.0, min(mx1, tx1) - max(mx0, tx0)) * max(
            0.0, min(my1, ty1) - max(my0, ty0)
        )
        overlap = intersection / model_area
        if overlap > best_overlap:
            best, best_overlap = table, overlap
    if best is None or best_overlap < minimum_overlap:
        return []

    cells: list[tuple[float, float, float, float]] = []
    for cell in getattr(best, "cells", ()):
        bounds = _rect(cell)
        if bounds is None:
            continue
        x0, y0, x1, y1 = bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if mx0 <= cx <= mx1 and my0 <= cy <= my1 and bounds not in cells:
            cells.append(bounds)
    return cells


def should_translate_table_cell(text: str) -> bool:
    """Return whether a cell contains natural-language text rather than codes.

    Product identifiers and numeric cells are safer left as original PDF glyphs.
    Natural-language labels in the supported source documents contain lowercase
    letters, including Unicode lowercase letters outside English.
    """
    value = " ".join(text.split())
    if not value:
        return False

    def natural_token(token: str) -> bool:
        token = token.strip("()[]{}:;,\"'“”")
        letters = "".join(character for character in token if character.isalpha())
        if token.lower() in {"dry", "wet"}:
            return True
        if token.lower() in {"max", "min"}:
            return False
        if re.search(r"[\u0370-\u03ff]", token):
            return False
        if len(letters) <= 2:
            return False
        if letters.isupper():
            return False
        if (
            any(character.isdigit() for character in token)
            or re.search(r"[a-z][A-Z]", token)
            or sum(character.isupper() for character in letters) >= 2
            or re.search(r"[%._/·]", token)
        ):
            return False
        if len(letters) <= 3 and letters[:1].isupper():
            return False
        return any(character.islower() for character in letters)

    return any(natural_token(token) for token in value.split())


def cluster_table_words(
    words: Iterable[Sequence[Any]],
    cell: Sequence[Any],
) -> list[TableTextCluster]:
    """Split a visually merged table cell into prose and code-like x clusters.

    Some PDFs omit the rule between a description and its abbreviation column,
    so PyMuPDF returns both as one cell. Normal word spaces are small; the jump
    to a right-aligned code is much larger. X-overlap across wrapped lines keeps
    multi-line descriptions together.
    """
    bounds = _rect(cell)
    items = [word for word in words if _rect(word) is not None and len(word) >= 5]
    if bounds is None or not items:
        return []

    heights = [max(0.1, float(word[3]) - float(word[1])) for word in items]
    gap_limit = max(3.0, median(heights) * 0.6)
    groups: list[list[Sequence[Any]]] = []
    group_bounds: list[list[float]] = []
    for word in sorted(items, key=lambda item: (float(item[0]), float(item[1]))):
        x0, y0, x1, y1 = (float(value) for value in word[:4])
        matches = [
            index
            for index, current in enumerate(group_bounds)
            if x0 <= current[2] + gap_limit and x1 >= current[0] - gap_limit
        ]
        if not matches:
            groups.append([word])
            group_bounds.append([x0, y0, x1, y1])
            continue
        target = matches[0]
        groups[target].append(word)
        current = group_bounds[target]
        current[:] = [
            min(current[0], x0),
            min(current[1], y0),
            max(current[2], x1),
            max(current[3], y1),
        ]
        for extra in reversed(matches[1:]):
            groups[target].extend(groups.pop(extra))
            other = group_bounds.pop(extra)
            current[:] = [
                min(current[0], other[0]),
                min(current[1], other[1]),
                max(current[2], other[2]),
                max(current[3], other[3]),
            ]

    ordered = sorted(zip(groups, group_bounds), key=lambda item: item[1][0])
    cx0, cy0, cx1, cy1 = bounds
    result: list[TableTextCluster] = []
    for index, (group, group_box) in enumerate(ordered):
        left = cx0 if index == 0 else (ordered[index - 1][1][2] + group_box[0]) / 2
        right = cx1 if index + 1 == len(ordered) else (
            group_box[2] + ordered[index + 1][1][0]
        ) / 2
        text = " ".join(
            str(word[4])
            for word in sorted(
                group,
                key=lambda item: (
                    int(item[5]) if len(item) > 5 else 0,
                    int(item[6]) if len(item) > 6 else 0,
                    int(item[7]) if len(item) > 7 else 0,
                ),
            )
        )
        result.append(
            TableTextCluster(
                (max(cx0, left), cy0, min(cx1, right), cy1),
                text,
                tuple(group),
            )
        )
    return result


def is_scanned_page(blocks: Iterable[Mapping[str, Any]], page_area: float) -> bool:
    """Return whether a rendered image covers more than half of the page."""
    if page_area <= 0:
        return False
    for block in blocks:
        if block.get("type") != 1:
            continue
        bbox = block.get("bbox")
        if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (float(value) for value in bbox)
        if max(0.0, x1 - x0) * max(0.0, y1 - y0) > page_area * 0.5:
            return True
    return False


def classify_preserved_page(page_text: str) -> PreservationDecision | None:
    """Classify pages whose number-heavy structure must not be reflowed."""
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if not lines:
        return None

    toc_score = 0
    standalone_nums = 0
    spaced_page_nums = 0
    emspace_page_nums = 0
    for line in lines:
        if (
            re.search(r"\.{5,}", line)
            or re.search(r"(\.\s){4,}", line)
            or re.search(r"[\x08\ufffd\u2500-\u257f]{3,}", line)
        ):
            toc_score += 3
        elif re.search(r"[\x08\ufffd\u2500-\u257f]+\s*\d{1,4}\s*$", line):
            toc_score += 2
        elif re.search(r"\S\s{5,}\d{1,4}\s*$", line):
            spaced_page_nums += 1
        elif re.fullmatch(r"\d{1,4}", line):
            standalone_nums += 1

        if re.search(r"[\u2002\u2003]+\s*\d{1,4}\s*$", line) or re.search(
            r"[\u2002\u2003]+\s*[ivxlcdm]+\s*$", line, re.IGNORECASE
        ):
            emspace_page_nums += 1

    has_contents_header = any(
        re.fullmatch(r"(table\s+of\s+)?contents?", line, re.IGNORECASE)
        for line in lines[:5]
    )
    if has_contents_header:
        toc_score += 5
    if spaced_page_nums >= 5:
        toc_score += spaced_page_nums
    if emspace_page_nums >= 5:
        toc_score += emspace_page_nums
    if standalone_nums >= 8 and toc_score > 0:
        toc_score += standalone_nums
    if len(lines) >= 15 and standalone_nums >= 10 and standalone_nums / len(lines) > 0.3:
        toc_score += standalone_nums
    if len(lines) >= 15:
        lines_ending_num = sum(
            1 for line in lines if re.search(r"\S\s+\d{1,4}\s*$", line)
        )
        if lines_ending_num / len(lines) > 0.8:
            toc_score += lines_ending_num
    if toc_score >= 8:
        return PreservationDecision("TOC", f"score={toc_score}")

    index_comma_numbers = sum(
        1 for line in lines if re.search(r",\s*\d{1,4}", line)
    )
    if len(lines) >= 20 and index_comma_numbers / len(lines) > 0.4:
        return PreservationDecision(
            "INDEX", f"comma_num={index_comma_numbers}/{len(lines)}"
        )
    if re.fullmatch(r"index", lines[0], re.IGNORECASE):
        return PreservationDecision("INDEX", "header")

    has_nomenclature_header = any(
        re.fullmatch(
            r"(nomenclature|list\s+of\s+symbols|symbols?\s+and\s+abbreviations?|"
            r"glossary|notation)s?",
            line,
            re.IGNORECASE,
        )
        for line in lines[:5]
    )
    if has_nomenclature_header and len(lines) >= 10:
        symbol_definition_pairs = sum(
            1
            for index in range(len(lines) - 1)
            if len(lines[index]) <= 15
            and len(lines[index + 1]) > 5
            and not lines[index].isdigit()
        )
        if symbol_definition_pairs / len(lines) > 0.3:
            return PreservationDecision(
                "NOMENCLATURE",
                f"pairs={symbol_definition_pairs}/{len(lines)}",
            )

    has_reference_header = any(
        re.fullmatch(
            r"[\xad]?(references?|bibliography|suggested\s+reading|further\s+reading|"
            r"works?\s+cited)",
            line,
            re.IGNORECASE,
        )
        for line in lines[:10]
    )
    numbered_refs = sum(1 for line in lines if re.match(r"^\d{1,3}\.\s", line))
    author_year_refs = sum(
        1 for line in lines if re.match(r"^[A-Z][a-z]+,?\s.*\(\d{4}\)", line)
    )
    bracketed_refs = sum(1 for line in lines if re.match(r"^\[\d{1,3}\]", line))
    year_parentheses = sum(1 for line in lines if re.search(r"\(\d{4}\)", line))
    isbn_doi = sum(
        1
        for line in lines
        if re.search(r"ISBN|ISSN|doi\.org|https?://", line, re.IGNORECASE)
    )
    all_refs = numbered_refs + author_year_refs + bracketed_refs
    reference_signals = all_refs + year_parentheses + isbn_doi
    if has_reference_header and reference_signals >= 5:
        return PreservationDecision(
            "REFERENCES",
            f"header, refs={all_refs}, years={year_parentheses}, isbn_doi={isbn_doi}",
        )
    if len(lines) >= 10 and all_refs >= 5 and year_parentheses + isbn_doi >= 3:
        return PreservationDecision(
            "REFERENCES",
            f"refs={all_refs}, years={year_parentheses}, isbn_doi={isbn_doi}",
        )
    return None
