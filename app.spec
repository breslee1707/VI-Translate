# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows and macOS desktop apps.

One-folder, deliberately not --onefile: onnxruntime, opencv, and PyMuPDF push
the bundle past 400 MB, and onefile re-extracts all of that to a temp directory
on every launch, which is slow and trips antivirus heuristics.
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)

datas = []
for optional in ("app/fonts", "app/assets"):
    directory = ROOT / optional
    if not directory.is_dir():
        continue
    for item in sorted(directory.iterdir()):
        # onnxruntime caches a hardware-specific optimised graph next to the
        # model. It is 75 MB, and it is only valid on the machine that built it.
        if item.is_file() and item.suffix != ".optimized":
            datas.append((str(item), optional))

datas += collect_data_files("customtkinter")
datas += collect_data_files("tkinterdnd2")
datas += collect_data_files("babeldoc")

hiddenimports = [
    "peewee",
    "pdf2zh.doclayout",  # reached through importlib.import_module, not a static import
    "pdf2zh.high_level",
    "pdf2zh.converter",
    "pdf2zh.translator",
]

analysis = Analysis(
    [str(ROOT / "app" / "gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "pytest",
        "scipy",
        "pandas",
        # Model-optimisation trees pulled in with onnxruntime. Inference never
        # touches them, and they drag in torch and transformers references.
        "onnxruntime.transformers",
        "onnxruntime.tools",
        "onnxruntime.quantization",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PDFTranslate",
    icon=str(ROOT / "app" / "assets" / ("icon.png" if sys.platform == "darwin" else "icon.ico")),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDFTranslate",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collect,
        name="PDFTranslate.app",
        icon=str(ROOT / "app" / "assets" / "icon.png"),
        bundle_identifier="io.github.breslee1707.pdftranslate",
        info_plist={
            "CFBundleDisplayName": "PDF Translate",
            "NSHighResolutionCapable": True,
        },
    )
