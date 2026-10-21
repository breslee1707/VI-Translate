#!/usr/bin/env python3
"""Desktop front end for the PDF translation runner.

Google mode only: a packaged executable has no agent and no API key, so the
handoff engine is reachable from the skill rather than from here.
"""

from __future__ import annotations

import ctypes
import importlib
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import tkinter
import traceback
import webbrowser
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from PIL import Image
from tkinterdnd2 import DND_FILES, TkinterDnD

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.errors import Failure, describe_failure, report_text  # noqa: E402
from app.i18n import UI_LANGUAGES, set_ui_language, t, ui_language  # noqa: E402
from app.icons import (  # noqa: E402
    BADGE_FILLS,
    BADGE_SIZE,
    DROP_SIZE,
    GLYPH_SIZE,
    SPINNER_FRAMES,
    badge,
    drop_icon,
    glyph,
)
from app.settings import load_settings, save_settings  # noqa: E402
from app.update import (  # noqa: E402
    APP_VERSION,
    RELEASES_PAGE,
    can_self_update,
    check_for_update,
    clean_previous_install,
    discard_staging,
    has_room_for,
    install_directory,
    is_newer,
    latest_release,
    launch_updater,
    stage_update,
    staged_version,
)
from scripts.translate_pdf import (  # noqa: E402
    BILINGUAL_MODES,
    DEFAULT_TARGET_LANGUAGE,
    OCR_MODES,
    TARGET_LANGUAGES,
    load_layout_model,
    preload_layout_model,
    translate_pdf,
)

FONT_DIRECTORY = APP_ROOT / "app" / "fonts"
ASSET_DIRECTORY = APP_ROOT / "app" / "assets"
UI_FONT = "Be Vietnam Pro"
MONO_FONT = "JetBrains Mono"
if sys.platform == "darwin":
    FALLBACK_UI_FONT = "Helvetica Neue"
    FALLBACK_MONO_FONT = "Menlo"
else:
    FALLBACK_UI_FONT = "Segoe UI"
    FALLBACK_MONO_FONT = "Consolas"
APP_TITLE = "PDF Translate"

# One 8px rhythm for the whole window, so nothing is spaced by feel.
PAD, GAP, EDGE = 8, 16, 24

# Every colour is a (light, dark) pair, picked by the system theme. The accent
# is the logo's blue and everything else a quiet neutral, so the one primary
# button and the state of each file are what the eye finds first.
WINDOW = ("#F3F5F8", "#131518")
CARD = ("#FFFFFF", "#1C1F23")
CARD_BORDER = ("#E2E6EC", "#2B2F35")
FIELD = ("#F0F3F6", "#262A30")
FIELD_HOVER = ("#E4E9EF", "#30353C")
ROW_HOVER = ("#F6F8FA", "#212429")
TEXT = ("#15181C", "#E7E9EC")
MUTED = ("#5E6772", "#9AA2AC")
CHECK_BORDER = ("#B4BCC6", "#5A616B")
SCROLLBAR = ("#D3D8DE", "#3A3F46")
SCROLLBAR_HOVER = ("#B9C0C8", "#4A5059")
ACCENT = ("#176CFF", "#1F6FFF")
ACCENT_HOVER = ("#0F5BE0", "#3D84FF")
ACCENT_TEXT = ("#1259D9", "#7EB0FF")
ACCENT_SOFT = ("#EAF1FF", "#1A2433")
ACCENT_SOFT_HOVER = ("#DCE8FF", "#22314A")
WARNING_SOFT = ("#FFF6E5", "#2B2416")
WARNING_BORDER = ("#F5D9A8", "#4A3B1E")
DANGER = ("#C7252E", "#D93A42")
DANGER_HOVER = ("#A81E26", "#E5545B")
ON_ACCENT = "#FFFFFF"
STATUS_COLORS = {
    "queued": MUTED,
    "running": ACCENT_TEXT,
    "done": ("#13834A", "#46C983"),
    "partial": ("#A15C00", "#E5A54B"),
    "failed": ("#C7252E", "#FF7B7B"),
    "skipped": MUTED,
    "paused": ("#A15C00", "#E5A54B"),
}
# The few weights a button comes in: fill, hover and text colour.
BUTTON_KINDS = {
    "primary": (ACCENT, ACCENT_HOVER, ON_ACCENT),
    "danger": (DANGER, DANGER_HOVER, ON_ACCENT),
    "secondary": (FIELD, FIELD_HOVER, TEXT),
    "subtle": ("transparent", ACCENT_SOFT_HOVER, ACCENT_TEXT),
    "quiet": ("transparent", FIELD, MUTED),
}
# A queue row's badge column, and the running file's page counter and bar.
BADGE_COLUMN = 10 + BADGE_SIZE + 12
COUNTER_WIDTH = 64
BAR_WIDTH = 112
SPIN_INTERVAL_MS = 90

LANGUAGE_NAMES = {
    "af": "Afrikaans", "ca": "Català", "cs": "Čeština", "cy": "Cymraeg",
    "da": "Dansk", "de": "Deutsch", "en": "English", "es": "Español",
    "et": "Eesti", "eu": "Euskara", "fi": "Suomi", "fr": "Français",
    "ga": "Gaeilge", "gl": "Galego", "hr": "Hrvatski", "hu": "Magyar",
    "id": "Bahasa Indonesia", "is": "Íslenska", "it": "Italiano",
    "lt": "Lietuvių", "lv": "Latviešu", "ms": "Bahasa Melayu", "mt": "Malti",
    "nl": "Nederlands", "no": "Norsk", "pl": "Polski", "pt": "Português",
    "ro": "Română", "sk": "Slovenčina", "sl": "Slovenščina", "sq": "Shqip",
    "sv": "Svenska", "sw": "Kiswahili", "tl": "Tagalog", "tr": "Türkçe",
    "vi": "Tiếng Việt",
}

# OCR is slow, and it turns every scanned page into text for Google: a scanned
# book becomes thousands of segments. It runs only for someone who turns it on.
DEFAULT_OCR_MODE = "off"
DEFAULT_BILINGUAL = "off"


def ocr_names() -> dict[str, str]:
    """Menu labels for the OCR modes, in the interface language."""
    return {mode: t(f"ocr.{mode}") for mode in OCR_MODES}


def bilingual_names() -> dict[str, str]:
    """Menu labels for the bilingual layouts, in the interface language."""
    return {mode: t(f"bilingual.{mode}") for mode in BILINGUAL_MODES}

# Every stage the runner reports through on_status; each has a "stage.*" label.
STAGES = (
    "preparing", "ocr", "translating", "layout", "request", "pacing",
    "verification", "verified", "waiting", "saving", "bilingual",
)


def ensure_writable_streams() -> None:
    """Give the app real streams, because a windowed build has none.

    PyInstaller sets sys.stdout and sys.stderr to None when console=False, and
    the core's tqdm progress bar writes to stderr, so translating raised
    AttributeError: 'NoneType' object has no attribute 'write'.
    """
    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def use_bundled_assets() -> None:
    """Point the engine at the packaged model and font so no download is needed."""
    model = ASSET_DIRECTORY / "doclayout.onnx"
    font = ASSET_DIRECTORY / "GoNotoKurrent-Regular.ttf"
    if model.is_file():
        os.environ.setdefault("PDF_TRANSLATE_MODEL", str(model))
    if font.is_file():
        os.environ.setdefault("NOTO_FONT_PATH", str(font))


def load_bundled_fonts() -> bool:
    """Register the bundled fonts for this process only, so no install is needed.

    tkinter can only use fonts the OS knows about, and AddFontResourceEx with
    FR_PRIVATE is the Windows way to add one without touching the system.
    """
    if sys.platform != "win32" or not FONT_DIRECTORY.is_dir():
        return False
    private = 0x10
    loaded = 0
    try:
        for font in FONT_DIRECTORY.glob("*.ttf"):
            loaded += ctypes.windll.gdi32.AddFontResourceExW(str(font), private, 0)
    except OSError:
        return False
    return loaded > 0


# A translation imports these only when it reaches them, so a file antivirus
# quarantined out of the bundle surfaced as E-CORE-01 on the user's first
# document instead (#29: _internal\_multiprocessing.pyd, needed by the Google
# browser session's pipe). They are checked once at start instead.
RUNTIME_MODULES = (
    "pdf2zh.high_level",
    "pdf2zh.translator",
    "pdf2zh.ocr",
    "multiprocessing.connection",
    *(("pdf2zh.google_browser",) if sys.platform in ("win32", "darwin") else ()),
)


def missing_runtime_modules(names: tuple[str, ...] = RUNTIME_MODULES) -> list[str]:
    """Name every module a translation needs that this installation cannot load."""
    missing: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
        except ImportError as error:
            missing.append(error.name or name)
        except Exception:  # noqa: BLE001 - a DLL that refuses to load is just as missing
            missing.append(name)
    return list(dict.fromkeys(missing))


def match_title_bar(window: tkinter.Misc) -> None:
    """Draw a dialog's Windows title bar dark while the app is dark.

    CustomTkinter does it as the dialog opens, but making the dialog transient
    rebuilds its frame, which came back light over a dark dialog.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        dark = ctypes.c_int(1 if ctk.get_appearance_mode() == "Dark" else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20, or 19 before Windows 10 20H1.
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(dark), ctypes.sizeof(dark)
            ) == 0:
                break
        # SWP_FRAMECHANGED with NOSIZE|NOMOVE|NOZORDER|NOACTIVATE: repaint the frame.
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)
    except (AttributeError, OSError, tkinter.TclError):
        pass  # an old Windows keeps its light title bar


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def count_pages(paths: list[Path]) -> int:
    """Total pages across the batch, or 0 if that cannot be read cheaply.

    The wait before the first page is mostly the engine embedding fonts into
    every page, so the page count is what actually explains the delay.
    """
    try:
        import pymupdf

        total = 0
        for path in paths:
            with pymupdf.open(path) as document:
                total += document.page_count
        return total
    except Exception:  # noqa: BLE001 - a nicer status line is never worth a crash
        return 0


def page_count(path: Path) -> int:
    """One file's page count from its catalog, or 0 if it cannot be read.

    pdfminer rather than PyMuPDF: this runs on its own thread while a
    translation may be using PyMuPDF, which must never run on two at once.
    """
    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdftypes import resolve1

        with path.open("rb") as handle:
            pages = resolve1(PDFDocument(PDFParser(handle)).catalog["Pages"])
            count = resolve1(pages["Count"])
        return count if isinstance(count, int) and count > 0 else 0
    except Exception:  # noqa: BLE001 - a missing page count is never worth an error
        return 0


def collect_pdfs(paths: list[Path]) -> list[Path]:
    """Expand dropped or picked paths into a deduplicated list of PDF files."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            # Not glob("*.pdf"): that is case-sensitive on POSIX and would miss .PDF.
            found.extend(sorted(
                (p for p in path.iterdir() if _is_pdf(p)),
                key=lambda entry: (entry.name.casefold(), entry.name),
            ))
        elif path.suffix.lower() == ".pdf":
            found.append(path)
    unique: dict[Path, None] = {}
    for path in found:
        unique.setdefault(path.resolve(), None)
    return list(unique)


def human_size(size: int) -> str:
    """A file size as people read it: 840 KB, or 1,2 MB in Vietnamese."""
    value = float(size)
    unit = "B"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            break
        value /= 1024
    if unit in ("B", "KB") or value >= 100:
        return f"{value:.0f} {unit}"
    number = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{number.replace('.', t('number.decimal'))} {unit}"


def file_summary(pages: int, size: int) -> str:
    """What a queued file is, before anything happens to it: 23 pages · 1,2 MB."""
    parts = []
    if pages > 0:
        parts.append(t("info.pages_one") if pages == 1 else t("info.pages_many", count=pages))
    if size > 0:
        parts.append(human_size(size))
    return " · ".join(parts)


def elide(text: str, font, width: float) -> str:
    """Cut the middle out of text wider than ``width`` pixels, keeping its end.

    The end of a file name is where "v2" or "-final" usually is, so it stays.
    ``font`` is anything with Tk's ``measure``.
    """
    if width <= 0 or font.measure(text) <= width:
        return text
    keep = min(12, len(text) // 3)
    tail = text[len(text) - keep:]
    low, high = 0, len(text) - keep
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle].rstrip() + "…" + tail) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…" + tail


def runnable(state: str, overwrite: bool = False) -> bool:
    """Whether pressing the main button translates a file in this state.

    A skipped file already has a translation, so it runs again only once
    "overwrite" is ticked - which is exactly what its error tells the user.
    """
    return state in ("queued", "failed", "paused") or (overwrite and state == "skipped")


def primary_action(states: Iterable[str], overwrite: bool = False) -> tuple[str, bool]:
    """What the main button offers for a queue in these states, and whether it can."""
    states = list(states)
    if "paused" in states:
        return t("controls.resume"), True
    count = sum(runnable(state, overwrite) for state in states)
    if count == 1:
        return t("controls.translate_one"), True
    if count:
        return t("controls.translate_many", count=count), True
    return t("controls.translate"), False


# Named beside the count, because a block and a dead connection each ask the
# user for something different, and neither is a fault in their document.
SERVICE_FAILURES = ("RateLimitedError", "ServiceUnavailableError")


@contextmanager
def google_diagnostics(destination: Path) -> Iterator[None]:
    """Bounded local counters only; never record request URLs or document text."""
    logger = logging.getLogger("pdf2zh.translator")
    handler = None
    previous_level = logger.level
    try:
        destination.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            destination / "translation-service.log", maxBytes=262144,
            backupCount=1, encoding="utf-8",
        )
        thread_id = threading.get_ident()
        handler.addFilter(lambda record: record.thread == thread_id and
                          record.msg == "Google translation counters: %s")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    except OSError:
        if handler is not None:
            handler.close()
        handler = None
    try:
        yield
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(previous_level)


def translation_outcome(result) -> tuple[str, str]:
    """Return the queue state and an honest, compact coverage summary."""
    details = []
    if result.untranslated:
        summary = t("outcome.untranslated", count=result.untranslated)
        causes = [
            t(f"service.{reason}") for reason in SERVICE_FAILURES if result.reasons.get(reason)
        ]
        if causes:
            summary += t("outcome.kept", causes="; ".join(causes))
        details.append(summary)
    if result.image_only_pages:
        pages = ", ".join(str(page + 1) for page in result.image_only_pages)
        details.append(t("outcome.scan_pages", pages=pages))
    if result.ocr_warnings:
        details.append(t("outcome.ocr_kept", count=len(result.ocr_warnings)))
    # The translation itself is saved; the copy the user also asked for is not.
    if getattr(result, "bilingual_error", ""):
        details.append(t("outcome.bilingual_failed"))
    partial = bool(details)
    detail = t("outcome.done", details="; ".join(details)) if partial else ""
    return ("partial" if partial else "done", detail)


class FieldMenu(ctk.CTkOptionMenu):
    """An option menu drawn as a form field, which lights up as a whole on
    hover; CustomTkinter lights only the arrow end. The canvas tags it paints
    are customtkinter 5.2.2's, which requirements-app.txt pins."""

    def _on_enter(self, event=0) -> None:
        super()._on_enter(event)
        if self._hover and self._state == tkinter.NORMAL and self._values:
            self._paint(self._button_hover_color)

    def _on_leave(self, event=0) -> None:
        super()._on_leave(event)
        self._paint(self._fg_color)

    def _paint(self, color) -> None:
        color = self._apply_appearance_mode(color)
        self._canvas.itemconfig("inner_parts_left", outline=color, fill=color)
        self._text_label.configure(bg=color)


class QueueRow:
    """One queued file: a status badge, its name over a line of detail, and on
    the right what the file needs next - a page counter and bar while it runs,
    buttons once there is a result to open or an error to read."""

    # Grid columns after the name. The counter and bar exist only while the
    # file runs, and actions and the remove button never show together.
    COUNTER, BAR, ACTIONS, REMOVE = 2, 3, 4, 8

    def __init__(self, parent, path: Path, app: App) -> None:
        self.app = app
        self.path = path
        self.state = "queued"
        # Kept so the row can be redrawn when the interface language changes.
        self.shown: tuple[str, str, str] = ("queued", "", "")
        self.info: tuple[int, int] = (0, 0)
        self.width = 0
        self.removable = True
        self.hovered = False
        self.counter: ctk.CTkLabel | None = None
        self.bar: ctk.CTkProgressBar | None = None
        self.actions: list[tuple[ctk.CTkButton, int]] = []

        self.frame = ctk.CTkFrame(parent, corner_radius=8, fg_color="transparent")
        self.frame.grid_columnconfigure(1, weight=1)

        self.mark = ctk.CTkLabel(
            self.frame, text="", image=app.badge_image("queued"),
            width=BADGE_SIZE, height=BADGE_SIZE,
        )
        self.mark.grid(row=0, column=0, rowspan=2, padx=(10, 12))

        self.name = ctk.CTkLabel(
            self.frame, text=path.name, anchor="w", height=18,
            font=app.font(13), text_color=TEXT,
        )
        self.name.grid(row=0, column=1, pady=(9, 0), sticky="ew")

        # Anything long goes here, wrapped, where it can be read in full. It
        # used to share the name's line and squeeze the name to nothing.
        self.message = ctk.CTkLabel(
            self.frame, text="", anchor="w", justify="left", height=16,
            font=app.font(12), text_color=MUTED,
        )
        self.message.grid(row=1, column=1, pady=(1, 9), sticky="ew")

        self.remove = ctk.CTkButton(
            self.frame, text="", image=app.glyph_image("close"), width=28, height=28,
            corner_radius=6, fg_color="transparent", hover_color=FIELD_HOVER,
            command=lambda: app.remove_file(path),
        )
        self.remove.grid(row=0, column=self.REMOVE, rowspan=2, padx=(4, 4))

        self._watch(self.frame, self.mark, self.name, self.message, self.remove)
        for widget in (self.frame, self.mark, self.name, self.message):
            widget.bind("<Button-1>", lambda _event: app._row_clicked(path))
        self._show_meta()

    def _watch(self, *widgets) -> None:
        """Hover the whole row, not one widget in it, so it reads as one item."""
        for widget in widgets:
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _enter(self, _event=None) -> None:
        if not self.hovered:
            self.hovered = True
            self.frame.configure(fg_color=ROW_HOVER)

    def _leave(self, _event=None) -> None:
        # Leave also fires on the way into a widget inside the row.
        inside = str(self.frame)
        pointer = str(self.frame.tk.call("winfo", "containing", *self.frame.winfo_pointerxy()))
        if pointer != inside and not pointer.startswith(inside + "."):
            self.hovered = False
            self.frame.configure(fg_color="transparent")

    def set_state(self, state: str, detail: str, message: str = "") -> None:
        self.state = state
        self.shown = (state, detail, message)
        self.mark.configure(image=self.app.badge_image(state))
        self.name.configure(text_color=MUTED if state == "skipped" else TEXT)
        if state == "running":
            self._show_counter(detail)
        else:
            self._hide_counter()
        self._show_meta(message)
        self._place_remove()
        self.fit()

    def set_info(self, pages: int, size: int) -> None:
        self.info = (pages, size)
        if self.state in ("queued", "done"):
            self._show_meta()

    def set_meta(self, text: str, color=MUTED) -> None:
        """What the running file is doing right now."""
        self.message.configure(text=text, text_color=color)

    def set_progress(self, done: int, total: int) -> None:
        """The running file's own pages, beside its name."""
        if self.bar is None:
            self._show_counter("")
            self.fit()
        self.counter.configure(text=f"{done}/{total}")
        self.bar.set(done / total if total else 0)

    def set_removable(self, allowed: bool) -> None:
        self.removable = allowed
        self._place_remove()
        self.fit()

    def _show_meta(self, message: str = "") -> None:
        """The line under the name: what the file is, or what became of it."""
        pages, size = self.info
        if self.state == "queued":
            text = file_summary(pages, size)
        elif self.state == "running":
            text = t("row.translating")
        elif self.state == "done":
            text = " · ".join(filter(None, (t("row.done"), file_summary(pages, 0))))
        else:
            text = message
        warns = self.state in ("partial", "failed", "paused")
        self.message.configure(text=text, text_color=STATUS_COLORS[self.state] if warns else MUTED)

    def _show_counter(self, text: str) -> None:
        if self.bar is None:
            self.counter = ctk.CTkLabel(
                self.frame, text="", anchor="e", width=COUNTER_WIDTH, height=16,
                font=self.app.font(12), text_color=MUTED,
            )
            self.counter.grid(row=0, column=self.COUNTER, rowspan=2, padx=(PAD, PAD))
            self.bar = ctk.CTkProgressBar(
                self.frame, width=BAR_WIDTH, height=4, corner_radius=2,
                fg_color=FIELD, progress_color=ACCENT,
            )
            self.bar.grid(row=0, column=self.BAR, rowspan=2, padx=(0, 10))
            self._watch(self.counter)
        self.counter.configure(text=text)
        self.bar.set(0)

    def _hide_counter(self) -> None:
        if self.bar is not None:
            self.counter.destroy()
            self.bar.destroy()
            self.counter = self.bar = None

    def _place_remove(self) -> None:
        # Removing a file mid-batch would desync the worker's own list.
        if self.state == "queued" and self.removable:
            self.remove.grid()
        else:
            self.remove.grid_remove()

    def show_actions(self) -> None:
        """Buttons for what can be done with the file now: open what it
        produced, or read why it did not."""
        for button, _width in self.actions:
            button.destroy()
        self.actions = []
        app = self.app
        output = app.outputs.get(self.path)
        choices: list[tuple[str, Callable[[], None]]] = []
        if self.state in ("done", "partial") and output is not None:
            choices.append((t("row.open"), lambda: app._open(output)))
            dual = app.duals.get(self.path)
            if dual is not None:
                choices.append((t("row.dual"), lambda: app._open(dual)))
            choices.append((t("row.reveal"), lambda: app._reveal(output)))
        elif self.state in ("failed", "skipped", "paused") and self.path in app.failures:
            choices.append((t("row.details"), lambda: app._row_clicked(self.path)))

        font = app.font(12)
        for index, (text, command) in enumerate(choices):
            width = font.measure(text) + 24
            button = ctk.CTkButton(
                self.frame, text=text, command=command, width=width, height=28,
                corner_radius=6, fg_color=FIELD, hover_color=FIELD_HOVER,
                text_color=TEXT, font=font,
            )
            last = index == len(choices) - 1
            button.grid(
                row=0, column=self.ACTIONS + index, rowspan=2,
                padx=(6 if index else PAD, 6 if last else 0),
            )
            self._watch(button)
            self.actions.append((button, width))
        # The whole row opens its result too, as it always has.
        cursor = "hand2" if choices else ""
        for widget in (self.frame, self.mark, self.name, self.message):
            widget.configure(cursor=cursor)
        self.fit()

    def _side_width(self) -> int:
        """Room the widgets right of the name take, in unscaled pixels."""
        width = 12
        if self.bar is not None:
            width += COUNTER_WIDTH + 2 * PAD + BAR_WIDTH + 10
        if self.actions:
            width += sum(button_width for _button, button_width in self.actions)
            width += 6 * (len(self.actions) - 1) + PAD + 6
        if self.state == "queued" and self.removable:
            width += 4 + 28 + 4
        return width

    def fit(self, width: int | None = None) -> None:
        """Elide the name and wrap the detail line to the room the row has.

        A name that wrapped made one row three lines tall, and a fixed
        wraplength clipped names whenever the window was resized.
        """
        if width is not None:
            self.width = width
        if self.width <= 1:
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self.frame)
        room = self.width / scaling - BADGE_COLUMN - self._side_width()
        self.name.configure(text=elide(self.path.name, self.app.font(13), room))
        self.message.configure(wraplength=max(room, 80))

    def destroy(self) -> None:
        self.frame.destroy()


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self) -> None:
        super().__init__(fg_color=WINDOW)
        self.TkdndVersion = TkinterDnD._require(self)

        has_fonts = load_bundled_fonts()
        self.ui_font = UI_FONT if has_fonts else FALLBACK_UI_FONT
        self.mono_font = MONO_FONT if has_fonts else FALLBACK_MONO_FONT
        self.settings = load_settings()
        set_ui_language(self.settings.get("ui_language"))

        self.title(APP_TITLE)
        self.minsize(700, 600)
        self._center(800, 760)
        self._set_window_icon()

        self.files: list[Path] = []
        self.rows: dict[Path, QueueRow] = {}
        self.states: dict[Path, str] = {}
        self.events: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.translating = False
        self.batch_done = 0
        self.batch_total = 0
        self.determinate = True
        self.last_output: Path | None = None
        self.outputs: dict[Path, Path] = {}
        self.duals: dict[Path, Path] = {}
        # Page count and size of each queued file, read off the main thread.
        self.file_info: dict[Path, tuple[int, int]] = {}
        # Kept so a failed row can still show its full detail after the batch.
        self.failures: dict[Path, tuple[Failure, Path | None]] = {}
        # What the header link does right now: open the release page, restart
        # into a downloaded build, or nothing while one is downloading.
        self.update_action: str | None = None
        self.update_tag = ""
        self.update_percent = -1
        # What the header link says, as a catalog key and its values, so a
        # change of interface language can say it again.
        self.update_label: tuple[str, dict] | None = None
        self.missing_modules: list[str] = []
        self.fonts: dict[tuple, ctk.CTkFont] = {}
        self.images: dict[tuple, ctk.CTkImage] = {}
        self.spinner: str | None = None
        self.spin_frame = 0

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        modifier = "Command" if sys.platform == "darwin" else "Control"
        self.bind(f"<{modifier}-o>", lambda _event: self._pick_files())
        self.bind(f"<{modifier}-Return>", lambda _event: self._start())
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)
        self.dnd_bind("<<DropEnter>>", self._on_drag_enter)
        self.dnd_bind("<<DropLeave>>", self._on_drag_leave)
        self.after(100, self._drain_events)
        # Both background threads are staggered: each one holds the GIL long
        # enough while importing to make the freshly opened window hitch, and
        # neither is urgent enough to do that to the first second of the app.
        self.after(2000, lambda: threading.Thread(target=self._check_for_update, daemon=True).start())
        # Build the inference session while the user is still picking files: it is
        # ~0.9s that every first translation used to pay right after the button
        # press, with nothing to show. Delayed, because starting it inside
        # __init__ made the window itself hitch for half a second as it opened.
        self.after(800, lambda: threading.Thread(target=self._prepare_engine, daemon=True).start())

    def _prepare_engine(self) -> None:
        """Warm the model, then say up front if the installation is damaged."""
        preload_layout_model()
        missing = missing_runtime_modules()
        if missing:
            self.events.put(("health", missing))

    def _on_ui_language(self, label: str) -> None:
        """Switch the interface language now, keeping the queue and the choices."""
        code = next((code for code, name in UI_LANGUAGES.items() if name == label), None)
        if code is None or code == ui_language():
            return
        if self.worker and self.worker.is_alive():
            # The menu is disabled during a batch; this only guards a race.
            self.ui_language_menu.set(UI_LANGUAGES[ui_language()])
            return
        choices = self._choices()
        overwrite = bool(self.overwrite.get())
        google_browser = bool(self.google_browser.get())
        set_ui_language(code)
        save_settings({"ui_language": code})
        self._rebuild(choices, overwrite, google_browser)

    def _rebuild(self, choices: tuple[str, str, str], overwrite: bool, google_browser: bool) -> None:
        """Recreate every widget in the current language.

        Rows keep the words they were given: a finished batch is history, and
        the next one is reported in the new language.
        """
        shown = {path: row.shown for path, row in self.rows.items()}
        for child in self.winfo_children():
            child.destroy()
        self.rows = {}
        self._build()

        language, ocr, bilingual = choices
        self.language.set(LANGUAGE_NAMES[language])
        self.ocr.set(ocr_names()[ocr])
        self.bilingual.set(bilingual_names()[bilingual])
        self._show_hints()
        if overwrite:
            self.overwrite.select()
        if not google_browser:
            self.google_browser.deselect()

        for index, path in enumerate(self.files):
            row = self._new_row(path, index)
            if path in self.file_info:
                row.set_info(*self.file_info[path])
            state, detail, message = shown.get(path, ("queued", "", ""))
            if state != "queued":
                row.set_state(state, detail, message)
            row.show_actions()
        self._refresh_queue_chrome()
        self._show_output_link()
        if self.update_label is not None:
            key, values = self.update_label
            self._show_update_link(key, self.update_action, **values)
        if self.missing_modules:
            self._show_health_warning(self.missing_modules)

    def _center(self, width: int, height: int) -> None:
        """Open in the middle of the screen, and never taller than it.

        Letting Windows drop the window in the top-left corner is the clearest
        tell that nobody positioned it. The size is in CustomTkinter's scaled
        units and the position in real pixels, which is what each one takes;
        mixing them up put a 150% screen's window off-centre and off its foot.
        """
        scaling = ctk.ScalingTracker.get_window_scaling(self)
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        # Room for the taskbar and the title bar on a small laptop screen.
        width = min(width, int(screen_width / scaling) - 32)
        height = min(height, int(screen_height / scaling) - 96)
        x = max((screen_width - round(width * scaling)) // 2, 0)
        y = max((screen_height - round(height * scaling)) // 3, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _set_window_icon(self, window: tkinter.Misc | None = None) -> None:
        try:
            (self if window is None else window).iconbitmap(str(ASSET_DIRECTORY / "icon.ico"))
        except (tkinter.TclError, OSError):
            pass  # a missing icon is not worth failing startup over

    # -- shared pieces -----------------------------------------------------
    def font(self, size: int, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
        """One shared font object per size and weight, not one per widget."""
        key = (size, weight, mono)
        if key not in self.fonts:
            family = self.mono_font if mono else self.ui_font
            self.fonts[key] = ctk.CTkFont(family, size=size, weight=weight)
        return self.fonts[key]

    def badge_image(self, state: str, frame: int = 0) -> ctk.CTkImage:
        key = ("badge", state, frame)
        if key not in self.images:
            self.images[key] = ctk.CTkImage(
                badge(state, False, frame), badge(state, True, frame), size=(BADGE_SIZE, BADGE_SIZE)
            )
        return self.images[key]

    def glyph_image(self, kind: str) -> ctk.CTkImage:
        key = ("glyph", kind)
        if key not in self.images:
            self.images[key] = ctk.CTkImage(
                glyph(kind, False), glyph(kind, True), size=(GLYPH_SIZE, GLYPH_SIZE)
            )
        return self.images[key]

    def _button(
        self, parent, text: str, command: Callable[[], object], kind: str = "secondary",
        height: int = 34, width: int = 0, image: ctk.CTkImage | None = None,
    ) -> ctk.CTkButton:
        """A button in one of the window's few weights; see BUTTON_KINDS."""
        fill, hover, color = BUTTON_KINDS[kind]
        font = self.font(13, "bold" if kind in ("primary", "danger") else "normal")
        if not width:
            width = font.measure(text) + (GLYPH_SIZE + 6 if image else 0) + 28
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=height,
            corner_radius=8, fg_color=fill, hover_color=hover, text_color=color,
            text_color_disabled=MUTED, font=font, image=image,
        )

    def _menu(
        self, parent, values: list[str], command: Callable[[str], object] | None = None,
        width: int = 200, height: int = 34, size: int = 13,
    ) -> FieldMenu:
        return FieldMenu(
            parent, values=values, command=command, width=width, height=height,
            corner_radius=8, fg_color=FIELD, button_color=FIELD,
            button_hover_color=FIELD_HOVER, text_color=TEXT, text_color_disabled=MUTED,
            dropdown_fg_color=CARD, dropdown_hover_color=FIELD_HOVER,
            dropdown_text_color=TEXT, font=self.font(size), dropdown_font=self.font(13),
            dynamic_resizing=False,
        )

    def _checkbox(self, parent, text: str, command: Callable[[], object] | None = None) -> ctk.CTkCheckBox:
        # No hover: CustomTkinter fills an unticked box with the hover colour,
        # which reads as ticked.
        return ctk.CTkCheckBox(
            parent, text=text, command=command, checkbox_width=18, checkbox_height=18,
            corner_radius=5, border_width=2, hover=False, fg_color=ACCENT,
            border_color=CHECK_BORDER, checkmark_color=ON_ACCENT, text_color=TEXT,
            text_color_disabled=MUTED, font=self.font(13),
        )

    @staticmethod
    def _wrap(label: ctk.CTkLabel, width: int, reserved: int) -> None:
        """Wrap a label to the width its container really has."""
        scaling = ctk.ScalingTracker.get_widget_scaling(label)
        wraplength = max(round(width / scaling) - reserved, 120)
        if label.cget("wraplength") != wraplength:
            label.configure(wraplength=wraplength)

    @staticmethod
    def _hairline(parent) -> ctk.CTkFrame:
        """A 1px divider. CustomTkinter rounds a frame's height down to an even
        number, which draws a 1px frame as nothing, so that is turned off."""
        line = ctk.CTkFrame(parent, height=1, corner_radius=0, fg_color=CARD_BORDER)
        line._draw_engine.set_round_to_even_numbers(True, False)
        line._draw()
        return line

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self._build_header()
        self._build_notices()
        self._build_settings()
        self._build_content()
        self._build_footer()
        self._refresh_queue_chrome()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=EDGE, pady=(20, GAP), sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        logo = ASSET_DIRECTORY / "icon.png"
        if logo.is_file():
            self.logo_image = ctk.CTkImage(Image.open(logo), size=(36, 36))
            ctk.CTkLabel(header, image=self.logo_image, text="").grid(
                row=0, column=0, rowspan=2, padx=(0, 12)
            )

        ctk.CTkLabel(
            header, text=APP_TITLE, anchor="w", height=24,
            font=self.font(19, "bold"), text_color=TEXT,
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            header, text=f"{t('app.tagline')} · v{APP_VERSION}", anchor="w", height=16,
            font=self.font(12), text_color=MUTED,
        ).grid(row=1, column=1, sticky="nw")

        self.update_link = ctk.CTkButton(
            header, text="", height=30, corner_radius=15, fg_color=ACCENT_SOFT,
            hover_color=ACCENT_SOFT_HOVER, text_color=ACCENT_TEXT,
            text_color_disabled=ACCENT_TEXT, font=self.font(12, "bold"),
            command=self._on_update_link,
        )
        self.update_link.grid(row=0, column=2, rowspan=2, padx=(0, PAD))
        self.update_link.grid_remove()

        # Quiet on purpose: chosen once, and it must not compete with "Dịch".
        self.ui_language_menu = self._menu(
            header, list(UI_LANGUAGES.values()), command=self._on_ui_language,
            width=124, height=30, size=12,
        )
        self.ui_language_menu.set(UI_LANGUAGES[ui_language()])
        self.ui_language_menu.grid(row=0, column=3, rowspan=2)

    def _build_notices(self) -> None:
        self.health_warning, self.health_text = self._notice(
            1, WARNING_SOFT, WARNING_BORDER, "partial", t("health.title"),
        )
        self.verification_actions, _ = self._notice(
            2, ACCENT_SOFT, ACCENT_SOFT_HOVER, "info", t("verification.title"),
            t("row.verification_open"),
        )
        buttons = ctk.CTkFrame(self.verification_actions, fg_color="transparent")
        buttons.grid(row=2, column=1, pady=(0, 12), sticky="w")
        self._button(
            buttons, t("verification.show"), lambda: self._choose_google_verification(True),
            kind="primary", height=30,
        ).pack(side="left")
        self._button(
            buttons, t("common.later"), lambda: self._choose_google_verification(False),
            kind="subtle", height=30,
        ).pack(side="left", padx=(PAD, 0))

    def _notice(
        self, row: int, fill, border, mark: str, title: str, body: str = "",
    ) -> tuple[ctk.CTkFrame, ctk.CTkLabel]:
        """A banner across the window for something the user has to act on.

        It sits above everything else rather than under the queue, where a
        warning used to push the list out of sight.
        """
        frame = ctk.CTkFrame(
            self, corner_radius=10, border_width=1, fg_color=fill, border_color=border,
        )
        frame.grid(row=row, column=0, padx=EDGE, pady=(0, 12), sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            frame, text="", image=self.badge_image(mark), width=BADGE_SIZE, height=BADGE_SIZE,
        ).grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=(11, 0), sticky="n")
        ctk.CTkLabel(
            frame, text=title, anchor="w", height=20, font=self.font(13, "bold"),
            text_color=TEXT,
        ).grid(row=0, column=1, padx=(0, GAP), pady=(12, 0), sticky="ew")
        text = ctk.CTkLabel(
            frame, text=body, anchor="w", justify="left", height=16,
            font=self.font(12), text_color=TEXT,
        )
        text.grid(row=1, column=1, padx=(0, GAP), pady=(2, 12), sticky="ew")
        frame.bind("<Configure>", lambda event: self._wrap(text, event.width, 14 + BADGE_SIZE + 12 + GAP))
        frame.grid_remove()
        return frame, text

    def _build_settings(self) -> None:
        card = ctk.CTkFrame(
            self, corner_radius=12, border_width=1, border_color=CARD_BORDER, fg_color=CARD,
        )
        card.grid(row=3, column=0, padx=EDGE, pady=(0, 12), sticky="ew")
        for column in range(3):
            card.grid_columnconfigure(column, weight=1, uniform="field")

        names = sorted(LANGUAGE_NAMES[code] for code in TARGET_LANGUAGES if code in LANGUAGE_NAMES)
        self.language = self._menu(card, names)
        target = self.settings.get("target_language", DEFAULT_TARGET_LANGUAGE)
        self.language.set(LANGUAGE_NAMES.get(target, LANGUAGE_NAMES[DEFAULT_TARGET_LANGUAGE]))

        self.ocr = self._menu(card, list(ocr_names().values()), command=lambda _label: self._show_hints())
        self.ocr.set(ocr_names()[DEFAULT_OCR_MODE])

        self.bilingual = self._menu(
            card, list(bilingual_names().values()), command=lambda _label: self._show_hints(),
        )
        layout = self.settings.get("bilingual", DEFAULT_BILINGUAL)
        self.bilingual.set(bilingual_names().get(layout, bilingual_names()[DEFAULT_BILINGUAL]))

        fields = (
            (t("controls.target"), self.language),
            (t("controls.ocr"), self.ocr),
            (t("controls.bilingual"), self.bilingual),
        )
        for column, (label, menu) in enumerate(fields):
            padx = (GAP if column == 0 else 6, GAP if column == 2 else 6)
            ctk.CTkLabel(
                card, text=label, anchor="w", height=16, font=self.font(12), text_color=MUTED,
            ).grid(row=0, column=column, padx=padx, pady=(14, 6), sticky="ew")
            menu.grid(row=1, column=column, padx=padx, sticky="ew")

        # Only while OCR or a bilingual copy is on: it explains them, and the
        # queue needs the room the rest of the time.
        self.hint = ctk.CTkLabel(
            card, text="", anchor="w", justify="left", height=16,
            font=self.font(12), text_color=MUTED,
        )
        self.hint.grid(row=2, column=0, columnspan=3, padx=GAP, pady=(10, 0), sticky="ew")
        card.bind("<Configure>", lambda event: self._wrap(self.hint, event.width, 2 * GAP))
        self._show_hints()

        self._hairline(card).grid(row=3, column=0, columnspan=3, padx=GAP, pady=(14, 0), sticky="ew")
        self.overwrite = self._checkbox(card, t("controls.overwrite"), command=self._refresh_primary)
        self.google_browser = self._checkbox(card, t("controls.google_browser"))
        browser = sys.platform in ("win32", "darwin")
        self.overwrite.grid(
            row=4, column=0, columnspan=3, padx=GAP, pady=(12, 0 if browser else 14), sticky="w"
        )
        if browser:
            self.google_browser.grid(row=5, column=0, columnspan=3, padx=GAP, pady=(8, 14), sticky="w")
            self.google_browser.select()

    def _show_hints(self) -> None:
        _, ocr, bilingual = self._choices()
        lines = []
        if ocr != "off":
            lines.append(t("controls.ocr_hint"))
        if bilingual != "off":
            lines.append(t(f"controls.bilingual_hint.{bilingual}"))
        self.hint.configure(text="\n".join(lines))
        if lines:
            self.hint.grid()
        else:
            self.hint.grid_remove()

    def _build_content(self) -> None:
        self.content = ctk.CTkFrame(
            self, corner_radius=12, border_width=1, border_color=CARD_BORDER, fg_color=CARD,
        )
        self.content.grid(row=4, column=0, padx=EDGE, pady=(0, GAP), sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        # While nothing is queued the whole card is the invitation to drop.
        self.empty_view = ctk.CTkFrame(self.content, fg_color="transparent")
        self.empty_view.grid(row=0, column=0, padx=2, pady=2, sticky="nsew")
        self.empty_view.grid_columnconfigure(0, weight=1)
        self.empty_view.grid_rowconfigure(0, weight=1)
        self.empty_view.grid_rowconfigure(5, weight=1)
        self.drop_image = ctk.CTkImage(drop_icon(False), drop_icon(True), size=(DROP_SIZE, DROP_SIZE))
        ctk.CTkLabel(self.empty_view, text="", image=self.drop_image).grid(row=1, column=0, pady=(0, 14))
        ctk.CTkLabel(
            self.empty_view, text=t("drop.prompt"), height=22,
            font=self.font(15, "bold"), text_color=TEXT,
        ).grid(row=2, column=0, padx=GAP)
        ctk.CTkLabel(
            self.empty_view, text=t("drop.hint"), height=16, font=self.font(12), text_color=MUTED,
        ).grid(row=3, column=0, padx=GAP, pady=(4, 18))
        buttons = ctk.CTkFrame(self.empty_view, fg_color="transparent")
        buttons.grid(row=4, column=0)
        self._button(buttons, t("drop.pick_files"), self._pick_files, kind="primary").pack(
            side="left", padx=(0, PAD)
        )
        self._button(buttons, t("drop.pick_folder"), self._pick_directory).pack(side="left")

        self.queue_view = ctk.CTkFrame(self.content, fg_color="transparent")
        self.queue_view.grid(row=0, column=0, padx=2, pady=2, sticky="nsew")
        self.queue_view.grid_columnconfigure(0, weight=1)
        self.queue_view.grid_rowconfigure(2, weight=1)

        # A separate header, because CTkScrollableFrame's label_text cannot hold
        # a button and the queue needs its actions next to its count.
        head = ctk.CTkFrame(self.queue_view, fg_color="transparent")
        head.grid(row=0, column=0, padx=(14, PAD), pady=10, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self.queue_title = ctk.CTkLabel(
            head, text=t("queue.title"), anchor="w", height=20,
            font=self.font(14, "bold"), text_color=TEXT,
        )
        self.queue_title.grid(row=0, column=0, sticky="w")
        self._button(
            head, t("queue.add_files"), self._pick_files, kind="subtle", height=30,
            image=self.glyph_image("plus"),
        ).grid(row=0, column=1)
        self._button(
            head, t("queue.add_folder"), self._pick_directory, kind="subtle", height=30,
            image=self.glyph_image("plus"),
        ).grid(row=0, column=2, padx=(4, 0))
        self.clear_button = self._button(head, t("queue.clear"), self._clear_queue, kind="quiet", height=30)
        self.clear_button.grid(row=0, column=3, padx=(4, 0))

        self._hairline(self.queue_view).grid(row=1, column=0, sticky="ew")
        self.list = ctk.CTkScrollableFrame(
            self.queue_view, corner_radius=0, fg_color=CARD, height=120,
            scrollbar_fg_color="transparent", scrollbar_button_color=CARD,
            scrollbar_button_hover_color=CARD,
        )
        self.list.grid(row=2, column=0, padx=(PAD, 2), pady=(6, PAD), sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)
        self.list_width = 0
        self.list_scrolls = False
        self.list.bind("<Configure>", self._on_list_resize, add="+")
        # _parent_canvas is customtkinter 5.2.2's, which the app pins.
        self.list._parent_canvas.bind("<Configure>", self._fit_scrollbar, add="+")

    def _on_list_resize(self, event) -> None:
        """Follow the real width: names elide to it, never under a button."""
        if event.width != self.list_width:
            self.list_width = event.width
            for row in self.rows.values():
                row.fit(event.width)
        self._fit_scrollbar()

    def _fit_scrollbar(self, _event=None) -> None:
        """Draw the scrollbar only once the queue outgrows its card.

        Its room stays reserved, so rows never shift sideways as the queue
        grows; taking it out of the grid instead left it drawn regardless.
        """
        canvas = self.list._parent_canvas
        overflow = self.list.winfo_reqheight() > canvas.winfo_height()
        if overflow != self.list_scrolls:
            self.list_scrolls = overflow
            self.list.configure(
                scrollbar_button_color=SCROLLBAR if overflow else CARD,
                scrollbar_button_hover_color=SCROLLBAR_HOVER if overflow else CARD,
            )
        if not overflow:
            canvas.yview_moveto(0)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, corner_radius=0, fg_color=CARD)
        footer.grid(row=5, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self._hairline(footer).grid(row=0, column=0, columnspan=3, sticky="ew")

        left = ctk.CTkFrame(footer, fg_color="transparent")
        left.grid(row=1, column=0, padx=(EDGE, GAP), pady=14, sticky="ew")
        left.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(
            left, text=t("status.empty"), anchor="w", justify="left", height=18,
            font=self.font(13), text_color=TEXT,
        )
        self.status.grid(row=0, column=0, sticky="ew")
        # Always in place and drawn in the footer's own colour while idle, so
        # the status above it never jumps when a batch starts.
        self.progress = ctk.CTkProgressBar(
            left, height=4, corner_radius=2, fg_color=CARD, progress_color=CARD,
        )
        self.progress.set(0)
        self.progress.grid(row=1, column=0, pady=(PAD, 0), sticky="ew")
        left.bind("<Configure>", lambda event: self._wrap(self.status, event.width, 0))

        self.output_link = self._button(
            footer, t("output.open_folder"), lambda: self._open(self.last_output), height=36,
        )
        self.output_link.grid(row=1, column=1, padx=(0, PAD))
        self.output_link.grid_remove()

        self.translate_button = self._button(
            footer, t("controls.translate"), self._start, kind="primary", height=36, width=156,
        )
        self.translate_button.grid(row=1, column=2, padx=(0, EDGE))

    def _show_progress(self, visible: bool) -> None:
        if visible:
            self.progress.configure(fg_color=FIELD, progress_color=ACCENT)
            return
        self.progress.stop()
        self.progress.configure(mode="determinate", fg_color=CARD, progress_color=CARD)
        self.progress.set(0)
        self.determinate = True

    def _set_fraction(self, fraction: float) -> None:
        self.progress.set(fraction)
        self.title(t("window.progress", percent=round(fraction * 100)))

    def _show_health_warning(self, missing: list[str]) -> None:
        self.missing_modules = list(missing)
        reinstall = t("health.reinstall_dmg" if sys.platform == "darwin" else "health.reinstall_zip")
        self.health_text.configure(
            text=t("health.missing", modules=", ".join(missing), reinstall=reinstall)
        )
        self.health_warning.grid()

    @staticmethod
    def _open(target: Path | None) -> None:
        """Open a finished file or its folder in the file manager."""
        if target is None or not Path(target).exists():
            return
        try:
            if sys.platform == "win32":
                os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
            else:
                command = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen(  # noqa: S603 - fixed OS command, no shell
                    [command, str(target)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError:
            pass  # nothing useful to do if the shell refuses

    @staticmethod
    def _reveal(target: Path | None) -> None:
        """Open the folder that holds a file, with the file selected in it."""
        if target is None or not Path(target).exists():
            return
        try:
            if sys.platform == "win32":
                # One string: explorer parses /select itself and misreads the
                # quotes subprocess would put around a path with spaces.
                subprocess.Popen(f'explorer /select,"{target}"')  # noqa: S603
            else:
                command = ["open", "-R", str(target)] if sys.platform == "darwin" else [
                    "xdg-open", str(Path(target).parent),
                ]
                subprocess.Popen(  # noqa: S603 - fixed OS command, no shell
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError:
            pass  # nothing useful to do if the shell refuses

    def _show_output_link(self) -> None:
        if self.last_output is not None:
            self.output_link.grid()

    # -- input -------------------------------------------------------------
    def _on_drop(self, event) -> None:
        # DropLeave does not always fire after a drop, so clear the highlight here.
        self._on_drag_leave(event)
        # splitlist handles the brace quoting tkdnd uses for paths with spaces.
        self._add([Path(item) for item in self.tk.splitlist(event.data)])

    def _on_drag_enter(self, _event) -> None:
        self.content.configure(border_color=ACCENT, border_width=2)

    def _on_drag_leave(self, _event) -> None:
        self.content.configure(border_color=CARD_BORDER, border_width=1)

    def _pick_files(self) -> None:
        chosen = ctk.filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")])
        self._add([Path(item) for item in chosen])

    def _pick_directory(self) -> None:
        chosen = ctk.filedialog.askdirectory()
        if chosen:
            self._add([Path(chosen)])

    def _new_row(self, path: Path, index: int) -> QueueRow:
        row = QueueRow(self.list, path, self)
        row.frame.grid(row=index, column=0, sticky="ew", pady=1)
        row.set_removable(not self.translating)
        row.fit(self.list_width)
        self.rows[path] = row
        return row

    def _add(self, paths: list[Path]) -> None:
        added = []
        for path in collect_pdfs(paths):
            if path in self.rows:
                continue
            self.files.append(path)
            self._new_row(path, len(self.rows))
            self.states[path] = "queued"
            added.append(path)
        if added:
            threading.Thread(target=self._read_info, args=(added,), daemon=True).start()
        self._refresh_queue_chrome()

    def _read_info(self, paths: list[Path]) -> None:
        """Runs on a daemon thread: opening a large PDF takes long enough to
        stall the window if it happened as the files were dropped."""
        for path in paths:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            self.events.put(("info", path, page_count(path), size))

    def remove_file(self, path: Path) -> None:
        """Drop one wrongly added file without rebuilding the whole batch."""
        if self.worker and self.worker.is_alive():
            return
        row = self.rows.pop(path, None)
        if row is None:
            return
        row.destroy()
        self.files.remove(path)
        for store in (self.states, self.outputs, self.duals, self.failures, self.file_info):
            store.pop(path, None)
        for index, remaining in enumerate(self.files):
            self.rows[remaining].frame.grid_configure(row=index)
        self._refresh_queue_chrome()

    def _clear_queue(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        for row in self.rows.values():
            row.destroy()
        self.files.clear()
        self.rows.clear()
        for store in (self.states, self.outputs, self.duals, self.failures, self.file_info):
            store.clear()
        self.last_output = None
        self.output_link.grid_remove()
        self._show_progress(False)
        self._refresh_queue_chrome()

    def _refresh_queue_chrome(self) -> None:
        """Keep the count, the empty state and the main button in step."""
        count = len(self.files)
        self.queue_title.configure(
            text=t("queue.title_count", count=count) if count else t("queue.title")
        )
        if count:
            self.empty_view.grid_remove()
            self.queue_view.grid()
        else:
            self.queue_view.grid_remove()
            self.empty_view.grid()
        if not self.translating:
            # Mid-batch the status line and the button belong to the batch.
            self.status.configure(text=t("status.queued", count=count) if count else t("status.empty"))
            self._refresh_primary()

    def _refresh_primary(self) -> None:
        """Say what the main button will do, and grey it out when nothing would."""
        if self.translating:
            return
        label, enabled = primary_action(self.states.values(), bool(self.overwrite.get()))
        self.translate_button.configure(
            text=label, state="normal" if enabled else "disabled",
            fg_color=ACCENT if enabled else FIELD, text_color_disabled=MUTED,
        )

    def _set_busy(self, busy: bool) -> None:
        """Lock what a running batch reads, or hand it back when it ends."""
        self.translating = busy
        state = "disabled" if busy else "normal"
        for widget in (
            self.language, self.ocr, self.bilingual, self.overwrite, self.google_browser,
            self.clear_button, self.ui_language_menu,
        ):
            widget.configure(state=state)
        for row in self.rows.values():
            row.set_removable(not busy)
        if busy:
            self.translate_button.configure(
                state="disabled", text=t("controls.translating"),
                fg_color=ACCENT_SOFT, text_color_disabled=ACCENT_TEXT,
            )
            return
        # A batch that has ended is not waiting for Google any more.
        self.verification_actions.grid_remove()
        self.title(APP_TITLE)
        self._refresh_primary()

    def _follow(self, row: QueueRow) -> None:
        """Keep the file being translated on screen, its badge turning."""
        if self.spinner is None:
            self._spin()
        canvas = self.list._parent_canvas
        self.list.update_idletasks()
        content = self.list.winfo_height()
        if content <= canvas.winfo_height():
            return
        top = row.frame.winfo_y()
        first, last = canvas.yview()
        if top < first * content or top + row.frame.winfo_height() > last * content:
            canvas.yview_moveto(max(top - PAD, 0) / content)

    def _spin(self) -> None:
        """Turn the running file's badge; it stops by itself once none runs."""
        running = [
            self.rows[path] for path, state in self.states.items()
            if state == "running" and path in self.rows
        ]
        if not running:
            self.spinner = None
            return
        self.spin_frame = (self.spin_frame + 1) % SPINNER_FRAMES
        for row in running:
            row.mark.configure(image=self.badge_image("running", self.spin_frame))
        self.spinner = self.after(SPIN_INTERVAL_MS, self._spin)

    # -- closing -----------------------------------------------------------
    def _on_close(self) -> None:
        """Closing mid-batch stops the file being translated, so it asks first."""
        if self.worker and self.worker.is_alive() and not self._confirm(
            t("close.title"), t("close.heading"), t("close.body"),
            t("close.quit"), t("close.keep"), danger=True,
        ):
            return
        self.destroy()

    def _dialog(self, title: str) -> ctk.CTkToplevel:
        dialog = ctk.CTkToplevel(self, fg_color=CARD)
        dialog.title(title)
        dialog.transient(self)
        # CustomTkinter puts its own logo on every new window 200 ms in.
        dialog.after(250, lambda: (self._set_window_icon(dialog), match_title_bar(dialog)))
        return dialog

    def _present(self, dialog: ctk.CTkToplevel, width: int, height: int | None = None) -> None:
        """Size a dialog to its content and open it over the middle of the window."""
        dialog.update_idletasks()
        scaling = ctk.ScalingTracker.get_window_scaling(dialog)
        if height is None:
            height = round(dialog.winfo_reqheight() / scaling)
        x = self.winfo_rootx() + (self.winfo_width() - round(width * scaling)) // 2
        y = self.winfo_rooty() + (self.winfo_height() - round(height * scaling)) // 3
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _confirm(
        self, title: str, heading: str, body: str, accept: str, cancel: str, danger: bool = False,
    ) -> bool:
        """A modal yes/no. Tk has no return value for a window, so it carries one."""
        answer = {"accepted": False}
        dialog = self._dialog(title)
        dialog.resizable(False, False)
        dialog.grid_columnconfigure(1, weight=1)

        def choose(accepted: bool) -> None:
            answer["accepted"] = accepted
            dialog.destroy()

        ctk.CTkLabel(
            dialog, text="", image=self.badge_image("partial" if danger else "info"),
            width=BADGE_SIZE, height=BADGE_SIZE,
        ).grid(row=0, column=0, rowspan=2, padx=(EDGE, 14), pady=(EDGE + 2, 0), sticky="n")
        ctk.CTkLabel(
            dialog, text=heading, anchor="w", justify="left", wraplength=340,
            font=self.font(15, "bold"), text_color=TEXT,
        ).grid(row=0, column=1, padx=(0, EDGE), pady=(EDGE, 0), sticky="ew")
        ctk.CTkLabel(
            dialog, text=body, anchor="w", justify="left", wraplength=340,
            font=self.font(13), text_color=MUTED,
        ).grid(row=1, column=1, padx=(0, EDGE), pady=(6, 0), sticky="ew")

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, padx=EDGE, pady=EDGE, sticky="e")
        self._button(buttons, cancel, lambda: choose(False)).pack(side="left")
        self._button(
            buttons, accept, lambda: choose(True), kind="danger" if danger else "primary",
        ).pack(side="left", padx=(PAD, 0))
        dialog.bind("<Escape>", lambda _event: choose(False))

        self._present(dialog, 440)
        # Grab only after the window exists, or Tk raises on some platforms.
        dialog.after(120, dialog.grab_set)
        self.wait_window(dialog)
        return answer["accepted"]

    # -- update ------------------------------------------------------------
    def _check_for_update(self) -> None:
        """Runs on a daemon thread: the network must never delay the window.

        Downloading is deliberately not asked about - it is 193 MB in the
        background of an app that is already downloading a translation model -
        but restarting is, because a restart interrupts whatever the user came
        here to do.
        """
        install = install_directory()
        if install is None or not can_self_update(install):
            # Running from source, or on a Mac, or installed somewhere this
            # process may not rename folders. The release page is all we have.
            tag = check_for_update()
            if tag:
                self.events.put(("update", tag))
            return

        clean_previous_install(install)
        staged = staged_version(install)
        if staged is not None and is_newer(staged, APP_VERSION):
            # Downloaded on an earlier run and never restarted into.
            self.events.put(("update_ready", staged))
            return
        discard_staging(install)  # nothing staged, or staged before this build

        release = latest_release()
        if release is None:
            return
        if not release.url or not has_room_for(release.size, install.parent):
            self.events.put(("update", release.tag))
            return

        self.events.put(("update_downloading", release.tag))
        try:
            stage_update(release, install, self._report_download)
        except Exception:  # noqa: BLE001 - any staging failure is just a link
            discard_staging(install)
            self.events.put(("update", release.tag))
            return
        self.events.put(("update_ready", release.tag))

    def _report_download(self, received: int, total: int) -> None:
        """Called per megabyte on the update thread; only whole percents cross."""
        percent = int(received * 100 / total) if total else 0
        if percent != self.update_percent:
            self.update_percent = percent
            self.events.put(("update_progress", percent))

    def _show_update_link(self, key: str, action: str | None, **values: object) -> None:
        self.update_action = action
        self.update_label = (key, values)
        text = t(key, **values)
        self.update_link.configure(
            text=text, width=self.font(12, "bold").measure(text) + 28,
            state="normal" if action else "disabled",
        )
        self.update_link.grid()

    def _on_update_link(self) -> None:
        if self.update_action == "browser":
            webbrowser.open(RELEASES_PAGE)
        elif self.update_action == "install":
            self._install_update()

    def _install_update(self) -> None:
        """Start the helper, then close: it swaps the folders and reopens the app."""
        if self.worker and self.worker.is_alive():
            self.status.configure(text=t("update.busy"))
            return
        install = install_directory()
        if install is None or not self._confirm_install():
            return
        try:
            launch_updater(install)
        except OSError:
            self._show_update_link("update.available", "browser", tag=self.update_tag)
            self.status.configure(text=t("update.helper_failed"))
            return
        self.destroy()

    def _confirm_install(self) -> bool:
        return self._confirm(
            t("update.dialog_title"), t("update.downloaded", tag=self.update_tag),
            t("update.dialog_body"), t("update.install"), t("common.later"),
        )

    # -- work --------------------------------------------------------------
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        overwrite = bool(self.overwrite.get())
        pending = [path for path in self.files if runnable(self.states[path], overwrite)]
        if not pending:
            self.status.configure(text=t("status.nothing_left"))
            return

        language, ocr, bilingual = self._choices()
        google_browser = sys.platform in ("win32", "darwin") and bool(self.google_browser.get())
        save_settings({"target_language": language, "bilingual": bilingual})

        for path in pending:
            # Waiting again, so a retried file does not show its last error.
            self.failures.pop(path, None)
            self.states[path] = "queued"
            self.rows[path].set_state("queued", "")
            self.rows[path].show_actions()
        self._set_busy(True)
        # The engine loads a 70 MB layout model before the first page event, and
        # a bar frozen at 0% for those seconds reads as a hung app.
        self.determinate = False
        self._show_progress(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        pages = self._batch_pages(pending)
        self.status.configure(
            text=t("status.preparing_pages", pages=pages) if pages else t("status.preparing")
        )
        self.batch_done, self.batch_total = 0, len(pending)
        self.worker = threading.Thread(
            target=self._run,
            args=(pending, language, overwrite, ocr, google_browser, bilingual),
            daemon=True,
        )
        self.worker.start()

    def _batch_pages(self, files: list[Path]) -> int:
        """Pages in the batch, from what the queue already read where it can."""
        known = [self.file_info.get(path, (0, 0))[0] for path in files]
        return sum(known) if all(known) else count_pages(files)

    def _choices(self) -> tuple[str, str, str]:
        """The target language, OCR mode and bilingual layout the menus show."""
        languages = {name: code for code, name in LANGUAGE_NAMES.items()}
        ocr_modes = {name: mode for mode, name in ocr_names().items()}
        layouts = {name: mode for mode, name in bilingual_names().items()}
        return (
            languages[self.language.get()],
            ocr_modes.get(self.ocr.get(), DEFAULT_OCR_MODE),
            layouts.get(self.bilingual.get(), DEFAULT_BILINGUAL),
        )

    def _run(
        self,
        files: list[Path],
        language: str,
        overwrite: bool,
        ocr: str,
        google_browser: bool = False,
        bilingual: str = DEFAULT_BILINGUAL,
    ) -> None:
        for index, path in enumerate(files, 1):
            self.events.put(("status", path, "running", "", None))
            destination = path.parent / "translated"

            def report(done: int, total: int, _p: Path = path) -> None:
                self.events.put(("page", _p, done, total))

            def stage(name: str, done: int, total: int, _p: Path = path) -> None:
                self.events.put(("stage", _p, name, done, total))

            try:
                with google_diagnostics(destination):
                    browser_options = {"google_browser": True} if google_browser else {}
                    result = translate_pdf(
                        path,
                        destination,
                        target_language=language,
                        overwrite=overwrite,
                        on_progress=report,
                        on_status=stage,
                        ocr=ocr,
                        bilingual=bilingual,
                        **browser_options,
                    )
                state, detail = translation_outcome(result)
                dual = getattr(result, "bilingual", None)
                if dual is not None:
                    self.events.put(("bilingual", path, dual))
                self.events.put(("status", path, state, detail, result.path))
            except Exception as error:  # noqa: BLE001 - keep the queue moving
                # One unreadable or already-translated file must not stop the batch.
                failure = describe_failure(error)
                state = "skipped" if failure.code == "E-OUT-05" else "failed"
                if failure.code in ("E-NET-08", "E-NET-09", "E-BROWSER-01", "E-VERIFY-01"):
                    state = "paused"
                log = self._log_failure(destination, path, error) if state in ("failed", "paused") else None
                self.failures[path] = (failure, log)
                self.events.put(("status", path, state, failure.headline, None))
                if state == "paused":
                    # Keep remaining files queued and avoid repeating OCR/model work.
                    break
            self.events.put(("progress", index / len(files), index, len(files)))
        self.events.put(("finished",))

    @staticmethod
    def _log_failure(destination: Path, source: Path, error: BaseException) -> Path | None:
        """Append the full traceback to a log file, and say where it went.

        The queue row only has space for one line, which is never enough to
        diagnose a failure on someone else's document.
        """
        try:
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / "pdf-translate.log"
            with path.open("a", encoding="utf-8") as log:
                log.write(f"\n{'=' * 70}\n{datetime.now():%Y-%m-%d %H:%M:%S}  {source}\n")
                traceback.print_exception(type(error), error, error.__traceback__, file=log)
            return path
        except OSError:
            return None  # a failure to log must never mask the failure being logged

    def _row_clicked(self, path: Path) -> None:
        """A finished row opens its result; a failed one explains itself."""
        produced = self.outputs.get(path)
        if produced is not None:
            self._open(produced)
            return
        entry = self.failures.get(path)
        if entry is not None:
            self._show_failure(path, *entry)

    def _show_failure(self, path: Path, failure: Failure, log: Path | None) -> None:
        """Show the whole error, with one action that makes it reportable.

        Users were being handed a clipped English exception with no way to read
        or copy it, so they could not say what had gone wrong. Everything the
        maintainer needs is one button away.
        """
        dialog = self._dialog(t("failure.title"))
        dialog.minsize(520, 420)
        dialog.grid_columnconfigure(1, weight=1)
        dialog.grid_rowconfigure(3, weight=1)

        state = self.states.get(path, "failed")
        ctk.CTkLabel(
            dialog, text="", image=self.badge_image(state if state in BADGE_FILLS else "failed"),
            width=BADGE_SIZE, height=BADGE_SIZE,
        ).grid(row=0, column=0, rowspan=2, padx=(EDGE, 14), pady=(EDGE + 2, 0), sticky="n")
        ctk.CTkLabel(
            dialog, text=failure.summary, anchor="w", justify="left", wraplength=440,
            font=self.font(15, "bold"), text_color=TEXT,
        ).grid(row=0, column=1, pady=(EDGE, 0), sticky="ew")
        ctk.CTkLabel(
            dialog, text=failure.code, height=22, corner_radius=6, fg_color=FIELD,
            text_color=MUTED, font=self.font(11, mono=True),
        ).grid(row=0, column=2, padx=(PAD, EDGE), pady=(EDGE, 0), sticky="ne")
        ctk.CTkLabel(
            dialog, text=path.name, anchor="w", justify="left", wraplength=500,
            font=self.font(12), text_color=MUTED,
        ).grid(row=1, column=1, columnspan=2, padx=(0, EDGE), pady=(2, 0), sticky="ew")
        ctk.CTkLabel(
            dialog, text=failure.advice, anchor="w", justify="left", wraplength=580,
            font=self.font(13), text_color=TEXT,
        ).grid(row=2, column=0, columnspan=3, padx=EDGE, pady=GAP, sticky="ew")

        details = ctk.CTkTextbox(
            dialog, wrap="word", corner_radius=8, border_width=0, fg_color=FIELD,
            text_color=TEXT, font=self.font(11, mono=True),
            scrollbar_button_color=SCROLLBAR, scrollbar_button_hover_color=SCROLLBAR_HOVER,
        )
        details.grid(row=3, column=0, columnspan=3, padx=EDGE, sticky="nsew")
        report = report_text(failure, path, APP_VERSION, log)
        details.insert("1.0", report)
        details.configure(state="disabled")

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=3, padx=EDGE, pady=GAP, sticky="ew")
        copied = ctk.CTkLabel(
            buttons, text="", font=self.font(12), text_color=STATUS_COLORS["done"],
        )

        def copy_report() -> None:
            self.clipboard_clear()
            self.clipboard_append(report)
            copied.configure(text=t("failure.copied"))

        self._button(buttons, t("failure.copy"), copy_report, kind="primary").pack(side="left")
        if log is not None:
            self._button(buttons, t("failure.open_log"), lambda: self._open(log)).pack(
                side="left", padx=(PAD, 0)
            )
        if failure.code == "E-BROWSER-01" and sys.platform == "win32":
            self._button(
                buttons, t("failure.webview2"),
                lambda: webbrowser.open("https://developer.microsoft.com/microsoft-edge/webview2/"),
            ).pack(side="left", padx=(PAD, 0))
        self._button(buttons, t("common.close"), dialog.destroy).pack(side="right")
        copied.pack(side="right", padx=(0, PAD))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        self._present(dialog, 640, 500)
        # Grab only after the window exists, or Tk raises on some platforms.
        dialog.after(120, dialog.grab_set)

    def _go_determinate(self) -> None:
        """Switch off the spinner the moment there is a real number to show."""
        if not self.determinate:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self.determinate = True

    def _choose_google_verification(self, open_browser: bool) -> None:
        from pdf2zh.google_browser import BACKEND

        BACKEND.send_control("show" if open_browser else "close")
        if not open_browser:
            self.status.configure(text=t("status.paused_verification"))

    def _drain_events(self) -> None:
        try:
            self._handle_events()
        finally:
            # Always rearm. An exception escaping here used to cancel the only
            # scheduled callback, freezing every later status in the batch and
            # making one bad file look as though the whole queue had failed.
            self.after(100, self._drain_events)

    def _handle_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "status":
                _, path, state, detail, produced = event
                if produced is not None:
                    self.outputs[path] = Path(produced)
                    self.last_output = Path(produced).parent
                    self._show_output_link()
                self.states[path] = state
                row = self.rows[path]
                if state in ("failed", "skipped", "paused", "partial"):
                    # The headline only; "Chi tiết" has the rest.
                    row.set_state(state, "", detail.splitlines()[0] if detail else "")
                else:
                    row.set_state(state, "")
                row.show_actions()
                if state == "running":
                    self._follow(row)
            elif event[0] == "stage":
                _, path, stage, done, total = event
                if self.states.get(path) != "running":
                    continue
                label = t(f"stage.{stage}" if stage in STAGES else "stage.other")
                row = self.rows[path]
                if stage == "verification":
                    row.set_meta(t("row.needs_verification"), STATUS_COLORS["paused"])
                    self.verification_actions.grid()
                else:
                    row.set_meta(label)
                    if stage == "verified":
                        self.verification_actions.grid_remove()
                if stage == "ocr" and total:
                    row.set_progress(done, total)
                if total:
                    label += t("stage.page", done=done, total=total)
                self.status.configure(text=f"{label} · {path.name}")
            elif event[0] == "page":
                # Per-page progress inside the file being translated. A textbook
                # is hundreds of pages, so file-level progress alone looks stuck.
                _, path, done, total = event
                if self.states.get(path) == "running" and total:
                    self._go_determinate()
                    self.rows[path].set_progress(done, total)
                    self._set_fraction((self.batch_done + done / total) / max(self.batch_total, 1))
                    self.status.configure(
                        text=t("status.translating_page", name=path.name, done=done, total=total)
                    )
            elif event[0] == "progress":
                _, fraction, done_files, total_files = event
                self.batch_done, self.batch_total = done_files, total_files
                self._go_determinate()
                self._set_fraction(fraction)
            elif event[0] == "info":
                _, path, pages, size = event
                if path in self.rows:
                    self.file_info[path] = (pages, size)
                    self.rows[path].set_info(pages, size)
            elif event[0] == "bilingual":
                self.duals[event[1]] = Path(event[2])
            elif event[0] == "health":
                self._show_health_warning(event[1])
            elif event[0] == "update":
                self.update_tag = event[1]
                self._show_update_link("update.available", "browser", tag=event[1])
            elif event[0] == "update_downloading":
                self.update_tag = event[1]
                self._show_update_link("update.downloading", None, tag=event[1])
            elif event[0] == "update_progress":
                self._show_update_link("update.progress", None, tag=self.update_tag, percent=event[1])
            elif event[0] == "update_ready":
                self.update_tag = event[1]
                self._show_update_link("update.ready", "install", tag=event[1])
            elif event[0] == "finished":
                self._set_busy(False)
                self._go_determinate()
                counts = Counter(self.states.values())
                completed = counts["done"] + counts["partial"]
                summary = t("summary.completed", completed=completed, total=len(self.files))
                if counts["partial"]:
                    summary += t("summary.partial", count=counts["partial"])
                if counts["failed"]:
                    summary += t("summary.failed", count=counts["failed"])
                if counts["paused"]:
                    summary = t("summary.paused")
                    if counts["queued"]:
                        summary += t("summary.waiting", count=counts["queued"])
                self.status.configure(text=summary)


def verify_engine() -> None:
    """Load everything a translation needs, and fail loudly if it cannot.

    A window that opens proves Tk works and nothing else. The bug that reached
    users last time was pikepdf's compiled extension failing to find its qpdf
    DLL in the frozen bundle - invisible until someone pressed Dich on a real
    document. These imports are the rest of the native stack: PDF read/write,
    the repair path, and the onnxruntime session behind layout detection.
    Building that session is not enough on its own: the CoreML failure of
    #16 and #32 only appeared at the first inference, so one page runs too.
    """
    import numpy

    import pdf2zh.high_level  # noqa: F401 - pulls pikepdf, pymupdf and qpdf
    import pikepdf._core  # noqa: F401 - the extension that failed on its own
    import pymupdf  # noqa: F401
    from pdf2zh.ocr import verify_ocr_runtime

    missing = missing_runtime_modules()
    if missing:
        raise ImportError(f"missing runtime modules: {', '.join(missing)}")
    load_layout_model().predict(numpy.full((256, 192, 3), 255, numpy.uint8), imgsz=256)
    verify_ocr_runtime()
    if sys.platform in ("win32", "darwin"):
        from pdf2zh.google_browser import verify_browser_runtime

        verify_browser_runtime()


def main() -> None:
    # Tk animates the progress bar from a 20ms callback on this thread, and the
    # engine's per-page work holds the GIL long enough at the default 5ms switch
    # interval to freeze that bar for over a second while it says "preparing".
    # A frozen bar reads as a hung app, which is the opposite of what it is for.
    sys.setswitchinterval(0.0005)
    ensure_writable_streams()
    use_bundled_assets()
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = App()
    arguments = [argument for argument in sys.argv[1:] if argument != "--smoke-test"]
    if "--smoke-test" in sys.argv[1:]:
        # CI uses this to prove the frozen executable can load Tk, TkDND and all
        # native libraries on the architecture that produced the bundle.
        app.withdraw()
        app.update_idletasks()
        try:
            verify_engine()
        except BaseException as error:  # noqa: BLE001 - the exit code is the result
            # Never let this reach PyInstaller's windowed traceback dialog: it
            # would wait for a click that a build machine cannot give, turning
            # a failed check into a job that hangs until it times out.
            print(f"smoke test failed: {error!r}", file=sys.stderr)
            app.destroy()
            raise SystemExit(1) from None
        app.destroy()
        return
    # Desktop shells can pass files dropped on the executable icon as arguments.
    if arguments:
        app._add([Path(argument) for argument in arguments])
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
