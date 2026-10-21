# Repository and Development

## Structure

- `app/`: CustomTkinter GUI, the self-updater, UI fonts, icons, bundled assets.
  `app/update.py` also carries the PowerShell helper that performs the swap.
  `app/i18n.py` holds every interface string in Vietnamese (the default) and
  English, error summaries and advice included; a key missing from one catalog
  fails `test_app_i18n.py`. `app/settings.py` remembers the interface
  language, target language and bilingual layout in
  `%LOCALAPPDATA%\PDFTranslate\settings.json` (macOS: Application Support;
  elsewhere `~/.config`), outside the folder the updater replaces. OCR is
  deliberately not remembered. QA scripts must patch `settings_path` rather
  than write the user's real file, and `App._check_for_update` so a
  screenshot never depends on GitHub.
- `app/icons.py` draws the queue's status badges, the spinner frames, the
  button glyphs and the empty-queue icon with Pillow at start-up, in light
  and dark variants, so no font decides how a status mark looks. `app/gui.py`
  relies on a few customtkinter 5.2.2 internals (`FieldMenu`'s canvas tags,
  the draw engine's even-height rounding in `_hairline`, the scrollable
  frame's scrollbar in `_fit_scrollbar`); before raising the pin in
  `requirements-app.txt`, re-check those and look at the window in light and
  dark mode.
- `scripts/translate_pdf.py`: supported CLI and stable output staging.
- `scripts/fetch_assets.py`: downloads the layout model and Unicode PDF font.
- `pdf2zh/`: bundled extraction, translation, preservation, and PDF rendering.
  `pdf2zh/bilingual.py` builds the opt-in bilingual copy from two finished
  PDFs.
- `tests/`: standard-library `unittest` regressions.
- `references/`: product preservation contract.
- `app.spec`, `build.ps1`: Windows packaging.
- `app-macos.spec`, `build-macos.sh`: native macOS packaging.
- `.github/workflows/`: release and on-demand macOS builds.

## Commands

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt
.\.venv\Scripts\python.exe app\gui.py
.\.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --output-dir OUT
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python app/gui.py
.venv/bin/python scripts/translate_pdf.py INPUT.pdf --output-dir OUT
.venv/bin/python -m unittest discover -s tests -v
bash build-macos.sh
```

## Code and Tests

Use four-space indentation, type annotations, concise docstrings,
`snake_case`, `PascalCase`, and `UPPER_CASE`. Prefer `pathlib.Path`, explicit
exceptions, and small helpers whose geometry can be unit-tested. Match nearby
imports and style; there is no repository-wide formatter.

Name tests `test_<feature>.py`, classes `<Feature>Tests`, and methods
`test_<behavior>`. Test observable behavior rather than comments or exact
implementation text. Preserve unrelated dirty-worktree changes.

Commit subjects are short and imperative. PRs state user impact, validation,
and representative visual evidence for GUI/PDF changes. Never commit local
test PDFs or packaged artifacts.
