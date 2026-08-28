#!/usr/bin/env python3
"""Tell the user a newer release exists. Nothing more.

The app ships as a 208 MB one-folder build that locks its own DLLs while it
runs, so replacing it in place needs an outside process. Until someone asks
for that, pointing at the release page is the whole feature.
"""

from __future__ import annotations

APP_VERSION = "0.2.1"
REPOSITORY = "breslee1707/VI-Translate"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"


def version_parts(tag: str) -> tuple[int, ...]:
    """Turn a release tag into numbers: "v0.10.0" -> (0, 10, 0).

    Raises ValueError on anything that is not a dotted number, which is how
    check_for_update rejects tags like "nightly".
    """
    core = tag.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    if not core:
        raise ValueError(f"not a version tag: {tag!r}")
    return tuple(int(piece) for piece in core.split("."))


def is_newer(candidate: str, current: str) -> bool:
    """Compare as numbers, padded to equal length.

    Comparing the strings would be wrong: "0.9.0" sorts above "0.10.0".
    """
    left, right = version_parts(candidate), version_parts(current)
    length = max(len(left), len(right))
    pad = (0,) * length
    return (left + pad)[:length] > (right + pad)[:length]


def check_for_update(current: str = APP_VERSION) -> str | None:
    """Return the newer tag name, or None. Never raises.

    Being offline is normal and must stay silent, so every failure - no
    network, rate limit, no release yet, a tag that is not a version - is
    just "no update".
    """
    try:
        import requests

        response = requests.get(RELEASES_API, timeout=5)
        response.raise_for_status()
        tag = response.json()["tag_name"]
        return tag if is_newer(tag, current) else None
    except Exception:  # noqa: BLE001 - an update check must never break startup
        return None
