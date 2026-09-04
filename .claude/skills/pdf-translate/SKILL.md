---
name: pdf-translate
description: Translate or diagnose local text-based PDFs in this repository while preserving layout, formulas, tables, figures, styles, rotation, and technical glyphs. Use for PDF translation, incomplete output, visual regressions, or preservation fixes; OCR-only scans need the opt-in `--ocr` mode; not for complex-script targets.
---

# PDF Translate Project Skill

This is the Claude Code adapter for the canonical cross-agent skill.

Before acting:

1. Read `../../../SKILL.md` completely and follow its workflow and boundaries.
2. Read `../../../agent-knowledge/index.md`, then its routed PDF engine,
   regression, and validation files relevant to the request.
3. Resolve commands from the repository root containing `pdf2zh/` and
   `scripts/translate_pdf.py`, not from this adapter directory.

On macOS use `.venv/bin/python` and `bash build-macos.sh`; on Windows use
`.venv\Scripts\python.exe` and `build.ps1`. The preservation behavior and tests
are shared across platforms. Keep the source unchanged, validate rendered
output, and report partial fallbacks honestly.
