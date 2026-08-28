# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS desktop app.

Build on the target architecture. PyInstaller is not a cross-compiler, and the
large native dependencies make separate arm64 and x86_64 DMGs more reliable
than trying to assemble a universal2 bundle from thin wheels.
"""

import os
import runpy
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)
APP_VERSION = runpy.run_path(str(ROOT / "app" / "update.py"))["APP_VERSION"]
TARGET_ARCH = os.environ.get("MACOS_TARGET_ARCH") or None
SIGNING_IDENTITY = os.environ.get("MACOS_SIGNING_IDENTITY") or None
ICON = ROOT / "build" / "macos" / "PDFTranslate.icns"

if not ICON.is_file():
    raise SystemExit("Missing macOS icon. Run ./build-macos.sh instead of PyInstaller directly.")

datas = []
for optional in ("app/fonts", "app/assets"):
    directory = ROOT / optional
    if not directory.is_dir():
        continue
    for item in sorted(directory.iterdir()):
        if item.is_file() and item.suffix != ".optimized":
            datas.append((str(item), optional))

datas += collect_data_files("customtkinter")
datas += collect_data_files("tkinterdnd2")
datas += collect_data_files("babeldoc")

hiddenimports = [
    "peewee",
    "pdf2zh.doclayout",
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
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH,
    codesign_identity=SIGNING_IDENTITY,
    entitlements_file=None,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="PDFTranslate",
)

app = BUNDLE(
    collect,
    name="PDFTranslate.app",
    icon=str(ICON),
    bundle_identifier="ai.huyg.pdftranslate",
    version=APP_VERSION,
    info_plist={
        "CFBundleDisplayName": "PDF Translate",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    },
    target_arch=TARGET_ARCH,
    codesign_identity=SIGNING_IDENTITY,
    entitlements_file=None,
)
