# Validation and PDF QA

## Automated Gate

Run from the repository root with the platform virtual environment:

```text
python -m unittest discover -s tests -v
python -m pip check
python -m ruff check pdf2zh scripts app tests --select E9,F63,F7,F82
```

`git diff --check` must be clean. Run `app/gui.py --smoke-test`; after packaging,
run the packaged executable with `--smoke-test` and require exit code 0.

## PDF Gate

Keep source and output hashes/paths separate. For every delivered PDF:

1. Reopen it and require the same page count and canvas sizes as the source.
2. Search extracted text for `{vN}`, `<bN>`, `<sN>`, `U+0000`, and `U+FFFD`.
3. Check span boxes against the page canvas and cell text against detected cell
   boundaries. Extraction is diagnostic, not proof of visual correctness.
4. Render every page. Review contact sheets for global anomalies, then inspect
   formula-, table-, rotation-, style-, first/middle/last-page regressions at
   readable resolution.
5. Confirm formulas, technical identifiers, URLs, figures, borders, bullets,
   bold/italic runs, and page numbers remain legible and correctly placed.
6. Report every fallback segment or materially untranslated region as partial.

Use `tmp/pdfs/` for render/diagnostic intermediates and `output/pdf/` for final
PDFs. Never overwrite a source or an existing user output without explicit
authorization. For expensive translations, validate representative pages
first, then regenerate the complete document with the final code.
