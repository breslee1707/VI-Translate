"""Make bundled native libraries findable before anything imports them.

pikepdf's compiled extension links against qpdf, which its wheel ships in a
sibling ``pikepdf.libs`` directory and locates through a delvewheel patch in
``pikepdf/__init__.py``. That patch derives the path from ``__file__``, which a
frozen build cannot be relied upon to reproduce; when it fails the app dies with
"pikepdf's extension library (pikepdf._core) failed to import" on a user's
machine but never on the developer's. Declaring the directories costs nothing
and removes the dependency on that patch.
"""

import os
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    _root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for _candidate in (_root, _root / "pikepdf.libs"):
        if _candidate.is_dir():
            try:
                os.add_dll_directory(str(_candidate))
            except OSError:
                pass  # a hint the loader ignores must not stop the app starting
