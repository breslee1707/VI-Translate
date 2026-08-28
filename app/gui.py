#!/usr/bin/env python3
"""Desktop front end for the PDF translation runner.

Google mode only: a packaged executable has no agent and no API key, so the
handoff engine is reachable from the skill rather than from here.
"""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from PIL import Image
from tkinterdnd2 import DND_FILES, TkinterDnD

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.update import APP_VERSION, RELEASES_PAGE, check_for_update  # noqa: E402
from scripts.translate_pdf import (  # noqa: E402
    DEFAULT_TARGET_LANGUAGE,
    TARGET_LANGUAGES,
    TranslationError,
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

# One 8px rhythm for the whole window, so nothing is spaced by feel.
PAD, GAP, EDGE = 8, 16, 24
DROPZONE_TALL, DROPZONE_SHORT = 148, 80

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

STATUS_MARKS = {"queued": "•", "running": "▶", "done": "✓", "partial": "!", "failed": "✕", "skipped": "–"}
STATUS_COLORS = {
    "queued": ("gray45", "gray60"),
    "running": ("#1f6feb", "#58a6ff"),
    "done": ("#1a7f37", "#3fb950"),
    "partial": ("#9a6700", "#d29922"),
    "failed": ("#cf222e", "#f85149"),
    "skipped": ("gray45", "gray60"),
}
ACCENT = STATUS_COLORS["running"]
MUTED = ("gray45", "gray60")
SURFACE = ("gray92", "gray17")
HOVER = ("gray86", "gray23")
BORDER_IDLE = ("gray75", "gray30")


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


def collect_pdfs(paths: list[Path]) -> list[Path]:
    """Expand dropped or picked paths into a deduplicated list of PDF files."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            # Not glob("*.pdf"): that is case-sensitive on POSIX and would miss .PDF.
            found.extend(sorted(p for p in path.iterdir() if _is_pdf(p)))
        elif path.suffix.lower() == ".pdf":
            found.append(path)
    unique: dict[Path, None] = {}
    for path in found:
        unique.setdefault(path.resolve(), None)
    return list(unique)


class QueueRow:
    """The widgets for one queued file, kept together so the event drain can
    address the mark, the name and the detail column separately."""

    def __init__(self, parent, path: Path, app: App) -> None:
        self.path = path
        self.frame = ctk.CTkFrame(parent, corner_radius=8, fg_color="transparent")
        self.frame.grid_columnconfigure(1, weight=1)

        self.mark = ctk.CTkLabel(
            self.frame, text=STATUS_MARKS["queued"], width=16,
            font=ctk.CTkFont(app.ui_font, size=13),
            text_color=STATUS_COLORS["queued"],
        )
        self.mark.grid(row=0, column=0, padx=(PAD, 0), pady=6)

        self.name = ctk.CTkLabel(
            self.frame, text=path.name, anchor="w", justify="left",
            font=ctk.CTkFont(app.mono_font, size=12), text_color=MUTED,
        )
        self.name.grid(row=0, column=1, padx=PAD, pady=6, sticky="ew")

        self.detail = ctk.CTkLabel(
            self.frame, text="", anchor="e",
            font=ctk.CTkFont(app.mono_font, size=11), text_color=MUTED,
        )
        self.detail.grid(row=0, column=2, pady=6, sticky="e")

        self.remove = ctk.CTkButton(
            self.frame, text="✕", width=24, height=24, corner_radius=6,
            fg_color="transparent", hover_color=("gray78", "gray28"),
            text_color=MUTED, font=ctk.CTkFont(app.ui_font, size=12),
            command=lambda: app.remove_file(path),
        )
        self.remove.grid(row=0, column=3, padx=(PAD // 2, PAD), pady=6)

        # Hover the whole row, not just the button, so it reads as one item.
        for widget in (self.frame, self.mark, self.name, self.detail):
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _enter(self, _event) -> None:
        self.frame.configure(fg_color=HOVER)

    def _leave(self, _event) -> None:
        self.frame.configure(fg_color="transparent")

    def set_state(self, state: str, detail: str) -> None:
        self.mark.configure(text=STATUS_MARKS[state], text_color=STATUS_COLORS[state])
        self.name.configure(text_color=MUTED if state == "queued" else STATUS_COLORS[state])
        self.detail.configure(text=detail, text_color=STATUS_COLORS[state])
        if state != "queued":
            # Removing a file mid-batch would desync the worker's own list.
            self.remove.grid_remove()

    def destroy(self) -> None:
        self.frame.destroy()


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self) -> None:
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        has_fonts = load_bundled_fonts()
        self.ui_font = UI_FONT if has_fonts else FALLBACK_UI_FONT
        self.mono_font = MONO_FONT if has_fonts else FALLBACK_MONO_FONT

        self.title("PDF Translate")
        self.minsize(620, 580)
        self._center(760, 700)
        self._set_window_icon()

        self.files: list[Path] = []
        self.rows: dict[Path, QueueRow] = {}
        self.states: dict[Path, str] = {}
        self.events: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.batch_done = 0
        self.batch_total = 0
        self.determinate = True
        self.last_output: Path | None = None
        self.outputs: dict[Path, Path] = {}

        self._build()
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
        self.after(800, lambda: threading.Thread(target=preload_layout_model, daemon=True).start())

    def _center(self, width: int, height: int) -> None:
        """Open in the middle of the screen. Letting Windows drop the window in
        the top-left corner is the clearest tell that nobody positioned it."""
        x = max((self.winfo_screenwidth() - width) // 2, 0)
        y = max((self.winfo_screenheight() - height) // 3, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _set_window_icon(self) -> None:
        try:
            self.iconbitmap(str(ASSET_DIRECTORY / "icon.ico"))
        except (tkinter.TclError, OSError):
            pass  # a missing icon is not worth failing startup over

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self._build_header()
        self._build_dropzone()
        self._build_controls()
        self._build_queue()
        self._build_footer()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=EDGE, pady=(EDGE, GAP), sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        logo = ASSET_DIRECTORY / "icon.png"
        if logo.is_file():
            self.logo_image = ctk.CTkImage(Image.open(logo), size=(38, 38))
            ctk.CTkLabel(header, image=self.logo_image, text="").grid(
                row=0, column=0, rowspan=2, padx=(0, PAD + 4)
            )

        ctk.CTkLabel(
            header, text="PDF Translate", anchor="w",
            font=ctk.CTkFont(self.ui_font, size=21, weight="bold"),
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            header, text="Dịch PDF, giữ nguyên bố cục", anchor="w",
            font=ctk.CTkFont(self.ui_font, size=11), text_color=MUTED,
        ).grid(row=1, column=1, sticky="nw")

        self.update_link = ctk.CTkLabel(
            header, text="", anchor="e", cursor="hand2", text_color=ACCENT,
            font=ctk.CTkFont(self.ui_font, size=12, weight="bold"),
        )
        self.update_link.grid(row=0, column=2, sticky="se")
        self.update_link.bind("<Button-1>", lambda _event: webbrowser.open(RELEASES_PAGE))
        self.update_link.grid_remove()

        ctk.CTkLabel(
            header, text=f"v{APP_VERSION}", anchor="e",
            font=ctk.CTkFont(self.ui_font, size=11), text_color=MUTED,
        ).grid(row=1, column=2, sticky="ne")

    def _build_dropzone(self) -> None:
        self.dropzone = ctk.CTkFrame(
            self, corner_radius=12, border_width=2, height=DROPZONE_TALL,
            border_color=BORDER_IDLE, fg_color=SURFACE,
        )
        self.dropzone.grid(row=1, column=0, padx=EDGE, sticky="ew")
        self.dropzone.grid_propagate(False)
        self.dropzone.grid_columnconfigure(0, weight=1)
        self.dropzone.grid_rowconfigure(0, weight=1)
        self.dropzone.grid_rowconfigure(3, weight=1)

        self.dropzone_glyph = ctk.CTkLabel(
            self.dropzone, text="⤓", text_color=MUTED,
            font=ctk.CTkFont(self.ui_font, size=24),
        )
        self.dropzone_glyph.grid(row=0, column=0, pady=(GAP, 0), sticky="s")

        self.dropzone_text = ctk.CTkLabel(
            self.dropzone, text="Kéo thả file PDF hoặc thư mục vào đây",
            font=ctk.CTkFont(self.ui_font, size=14),
        )
        self.dropzone_text.grid(row=1, column=0, pady=(PAD // 2, PAD))

        buttons = ctk.CTkFrame(self.dropzone, fg_color="transparent")
        buttons.grid(row=2, column=0, pady=(0, GAP))
        # Both outlined on purpose: "Dịch" is the one primary action on screen.
        for text, command in (
            ("Chọn file", self._pick_files),
            ("Chọn thư mục", self._pick_directory),
        ):
            ctk.CTkButton(
                buttons, text=text, width=132, height=32, command=command,
                fg_color="transparent", border_width=1, border_color=BORDER_IDLE,
                text_color=ACCENT, hover_color=HOVER,
                font=ctk.CTkFont(self.ui_font, size=13),
            ).pack(side="left", padx=PAD // 2)

    def _on_drag_enter(self, _event) -> None:
        self.dropzone.configure(border_color=ACCENT, fg_color=HOVER)

    def _on_drag_leave(self, _event) -> None:
        self.dropzone.configure(border_color=BORDER_IDLE, fg_color=SURFACE)

    def _resize_dropzone(self) -> None:
        """Shrink once files are queued: the list is what the user watches."""
        compact = bool(self.files)
        self.dropzone.configure(height=DROPZONE_SHORT if compact else DROPZONE_TALL)
        self.dropzone_text.configure(
            text="Kéo thêm file vào đây" if compact else "Kéo thả file PDF hoặc thư mục vào đây",
            font=ctk.CTkFont(self.ui_font, size=12 if compact else 14),
        )
        if compact:
            self.dropzone_glyph.grid_remove()
        else:
            self.dropzone_glyph.grid()

    def _build_controls(self) -> None:
        controls = ctk.CTkFrame(self, corner_radius=12, fg_color=SURFACE)
        controls.grid(row=2, column=0, padx=EDGE, pady=GAP, sticky="ew")
        controls.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            controls, text="Dịch sang", font=ctk.CTkFont(self.ui_font, size=13)
        ).grid(row=0, column=0, padx=(GAP, PAD + 2), pady=(GAP, PAD), sticky="w")

        names = sorted(LANGUAGE_NAMES[code] for code in TARGET_LANGUAGES if code in LANGUAGE_NAMES)
        self.language = ctk.CTkOptionMenu(
            controls, values=names, width=200, height=34,
            font=ctk.CTkFont(self.ui_font, size=13),
        )
        self.language.set(LANGUAGE_NAMES[DEFAULT_TARGET_LANGUAGE])
        self.language.grid(row=0, column=1, pady=(GAP, PAD), sticky="w")

        self.translate_button = ctk.CTkButton(
            controls, text="Dịch", width=124, height=40, corner_radius=8,
            command=self._start, font=ctk.CTkFont(self.ui_font, size=14, weight="bold"),
        )
        self.translate_button.grid(row=0, column=2, rowspan=2, padx=GAP, pady=GAP)

        self.overwrite = ctk.CTkCheckBox(
            controls, text="Ghi đè file đã dịch trước đó",
            checkbox_width=18, checkbox_height=18,
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.overwrite.grid(row=1, column=0, columnspan=2, padx=GAP, pady=(0, GAP), sticky="w")

    def _build_queue(self) -> None:
        # A separate header, because CTkScrollableFrame's label_text cannot hold
        # a button and the queue needs a "clear" action next to its count.
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=3, column=0, padx=EDGE, sticky="ew")
        head.grid_columnconfigure(0, weight=1)

        self.queue_title = ctk.CTkLabel(
            head, text="Hàng đợi", anchor="w",
            font=ctk.CTkFont(self.ui_font, size=13, weight="bold"),
        )
        self.queue_title.grid(row=0, column=0, sticky="w")

        self.clear_button = ctk.CTkButton(
            head, text="Xoá tất cả", width=88, height=26, corner_radius=6,
            fg_color="transparent", hover_color=HOVER, text_color=MUTED,
            font=ctk.CTkFont(self.ui_font, size=12), command=self._clear_queue,
        )
        self.clear_button.grid(row=0, column=1, sticky="e")

        self.list = ctk.CTkScrollableFrame(self, corner_radius=12, fg_color=SURFACE)
        self.list.grid(row=4, column=0, padx=EDGE, pady=(PAD // 2, GAP), sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)
        self.list.bind("<Configure>", self._rewrap_rows)

        self.empty_state = ctk.CTkLabel(
            self.list, justify="center", text_color=MUTED,
            text="Chưa có file nào.\nKéo thả PDF vào ô phía trên để bắt đầu.",
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.empty_state.grid(row=0, column=0, pady=48)

    def _rewrap_rows(self, event) -> None:
        """Follow the real width. A fixed wraplength clips names on resize."""
        width = max(event.width - 160, 180)
        for row in self.rows.values():
            row.name.configure(wraplength=width)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, padx=EDGE, pady=(0, EDGE), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(footer, height=6)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, pady=(0, PAD), sticky="ew")
        self.progress.grid_remove()

        self.status = ctk.CTkLabel(
            footer, text="Chưa có file nào", anchor="w", text_color=MUTED,
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.status.grid(row=1, column=0, sticky="w")

        self.output_link = ctk.CTkLabel(
            footer, text="", anchor="w", justify="left", cursor="hand2",
            font=ctk.CTkFont(self.ui_font, size=12, underline=True),
            text_color=ACCENT, wraplength=680,
        )
        self.output_link.grid(row=2, column=0, pady=(PAD // 2, 0), sticky="w")
        self.output_link.bind("<Button-1>", lambda _event: self._open(self.last_output))
        self.output_link.grid_remove()

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

    def _show_output_link(self) -> None:
        if self.last_output is not None:
            self.output_link.configure(text=f"Mở thư mục kết quả:  {self.last_output}")
            self.output_link.grid()

    # -- input -------------------------------------------------------------
    def _on_drop(self, event) -> None:
        # DropLeave does not always fire after a drop, so clear the highlight here.
        self._on_drag_leave(event)
        # splitlist handles the brace quoting tkdnd uses for paths with spaces.
        self._add([Path(item) for item in self.tk.splitlist(event.data)])

    def _pick_files(self) -> None:
        chosen = ctk.filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")])
        self._add([Path(item) for item in chosen])

    def _pick_directory(self) -> None:
        chosen = ctk.filedialog.askdirectory()
        if chosen:
            self._add([Path(chosen)])

    def _add(self, paths: list[Path]) -> None:
        for path in collect_pdfs(paths):
            if path in self.rows:
                continue
            self.files.append(path)
            row = QueueRow(self.list, path, self)
            row.frame.grid(row=len(self.rows), column=0, sticky="ew", padx=PAD // 2, pady=1)
            self.rows[path] = row
            self.states[path] = "queued"
        self._refresh_queue_chrome()

    def remove_file(self, path: Path) -> None:
        """Drop one wrongly added file without rebuilding the whole batch."""
        if self.worker and self.worker.is_alive():
            return
        row = self.rows.pop(path, None)
        if row is None:
            return
        row.destroy()
        self.files.remove(path)
        self.states.pop(path, None)
        self.outputs.pop(path, None)
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
        self.states.clear()
        self.outputs.clear()
        self.last_output = None
        self.output_link.grid_remove()
        self.progress.grid_remove()
        self._refresh_queue_chrome()

    def _refresh_queue_chrome(self) -> None:
        """Keep the count, the empty state and the dropzone size in step."""
        count = len(self.files)
        self.queue_title.configure(text=f"Hàng đợi ({count})" if count else "Hàng đợi")
        if count:
            self.empty_state.grid_remove()
        else:
            self.empty_state.grid()
        self.status.configure(text=f"{count} file trong hàng đợi" if count else "Chưa có file nào")
        self._resize_dropzone()

    # -- work --------------------------------------------------------------
    def _check_for_update(self) -> None:
        """Runs on a daemon thread: the network must never delay the window."""
        tag = check_for_update()
        if tag:
            self.events.put(("update", tag))

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        pending = [path for path in self.files if self.states[path] in ("queued", "failed")]
        if not pending:
            self.status.configure(text="Không còn file nào cần dịch")
            return

        names = {name: code for code, name in LANGUAGE_NAMES.items()}
        language = names[self.language.get()]
        overwrite = bool(self.overwrite.get())

        self.translate_button.configure(state="disabled", text="Đang dịch…")
        self.clear_button.configure(state="disabled")
        # The engine loads a 70 MB layout model before the first page event, and
        # a bar frozen at 0% for those seconds reads as a hung app.
        self.determinate = False
        self.progress.grid()
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        pages = count_pages(pending)
        self.status.configure(
            text=f"Đang chuẩn bị {pages} trang…" if pages else "Đang chuẩn bị…"
        )
        self.batch_done, self.batch_total = 0, len(pending)
        self.worker = threading.Thread(
            target=self._run, args=(pending, language, overwrite), daemon=True
        )
        self.worker.start()

    def _run(self, files: list[Path], language: str, overwrite: bool) -> None:
        for index, path in enumerate(files, 1):
            self.events.put(("status", path, "running", "", None))
            destination = path.parent / "translated"

            def report(done: int, total: int, _p: Path = path) -> None:
                self.events.put(("page", _p, done, total))

            try:
                result = translate_pdf(
                    path,
                    destination,
                    target_language=language,
                    overwrite=overwrite,
                    on_progress=report,
                )
                detail = (
                    f"{result.untranslated} đoạn chưa dịch được"
                    if result.untranslated
                    else str(result.path)
                )
                state = "partial" if result.untranslated else "done"
                self.events.put(("status", path, state, detail, result.path))
            except TranslationError as error:
                # One unreadable or already-translated file must not stop the batch.
                state = "skipped" if "already exists" in str(error) else "failed"
                if state == "failed":
                    self._log_failure(destination, path, error)
                self.events.put(("status", path, state, str(error), None))
            except Exception as error:  # noqa: BLE001 - keep the queue moving
                self._log_failure(destination, path, error)
                self.events.put(("status", path, "failed", f"{type(error).__name__}: {error}", None))
            self.events.put(("progress", index / len(files), index, len(files)))
        self.events.put(("finished",))

    @staticmethod
    def _log_failure(destination: Path, source: Path, error: BaseException) -> None:
        """Append the full traceback to a log file the user can send back.

        The queue row only has space for one line, which is never enough to
        diagnose a failure on someone else's document.
        """
        try:
            destination.mkdir(parents=True, exist_ok=True)
            with (destination / "pdf-translate.log").open("a", encoding="utf-8") as log:
                log.write(f"\n{'=' * 70}\n{datetime.now():%Y-%m-%d %H:%M:%S}  {source}\n")
                traceback.print_exception(type(error), error, error.__traceback__, file=log)
        except OSError:
            pass  # a failure to log must never mask the failure being logged

    def _go_determinate(self) -> None:
        """Switch off the spinner the moment there is a real number to show."""
        if not self.determinate:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self.determinate = True

    def _drain_events(self) -> None:
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
                first_line = detail.splitlines()[0] if detail else ""
                row.set_state(
                    state,
                    first_line if state in ("failed", "skipped", "partial") else "",
                )
                if path in self.outputs:
                    for widget in (row.frame, row.mark, row.name, row.detail):
                        widget.configure(cursor="hand2")
                        widget.bind("<Button-1>", lambda _e, p=path: self._open(self.outputs.get(p)))
            elif event[0] == "page":
                # Per-page progress inside the file being translated. A textbook
                # is hundreds of pages, so file-level progress alone looks stuck.
                _, path, done, total = event
                if self.states.get(path) == "running" and total:
                    self._go_determinate()
                    self.rows[path].detail.configure(text=f"trang {done}/{total}")
                    self.progress.set((self.batch_done + done / total) / max(self.batch_total, 1))
                    self.status.configure(text=f"Đang dịch {path.name}   trang {done}/{total}")
            elif event[0] == "progress":
                _, fraction, done_files, total_files = event
                self.batch_done, self.batch_total = done_files, total_files
                self._go_determinate()
                self.progress.set(fraction)
            elif event[0] == "update":
                self.update_link.configure(text=f"● Có bản mới {event[1]}")
                self.update_link.grid()
            elif event[0] == "finished":
                self.translate_button.configure(state="normal", text="Dịch")
                self.clear_button.configure(state="normal")
                self._go_determinate()
                counts = {}
                for state in self.states.values():
                    counts[state] = counts.get(state, 0) + 1
                summary = f"Xong {counts.get('done', 0)}/{len(self.files)} file"
                if counts.get("partial"):
                    summary += f", {counts['partial']} file dịch thiếu"
                if counts.get("failed"):
                    summary += f", {counts['failed']} file lỗi"
                self.status.configure(text=summary)
        self.after(100, self._drain_events)


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
        # native libraries on the Mac architecture that produced the bundle.
        app.withdraw()
        app.update_idletasks()
        app.destroy()
        return
    # Desktop shells can pass files dropped on the executable icon as arguments.
    if arguments:
        app._add([Path(argument) for argument in arguments])
    app.mainloop()


if __name__ == "__main__":
    main()
