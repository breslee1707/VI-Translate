"""Remember the few choices a user makes once rather than per batch.

Only the interface language, the target language and the bilingual layout are
kept. OCR is deliberately not: it is slow and turns a scanned book into
thousands of Google requests, so it stays a choice made for each batch.

The file lives beside the updater log, outside the install folder, so an update
that swaps the folder keeps it. Settings are a convenience: a file that cannot
be read or written is treated as empty, never as a reason to fail.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

SETTINGS_KEYS = ("ui_language", "target_language", "bilingual")


def settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "PDFTranslate" / "settings.json"


def load_settings(path: Path | None = None) -> dict[str, str]:
    """The saved choices; anything unreadable or unexpected is left out."""
    try:
        values = json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    return {
        key: value for key, value in values.items()
        if key in SETTINGS_KEYS and isinstance(value, str)
    }


def save_settings(values: Mapping[str, str], path: Path | None = None) -> bool:
    """Merge ``values`` into the saved choices. Returns whether they were kept."""
    target = path or settings_path()
    merged = load_settings(target)
    merged.update({key: value for key, value in values.items() if key in SETTINGS_KEYS})
    staged = target.with_name(f".{target.name}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        staged.replace(target)
    except OSError:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
