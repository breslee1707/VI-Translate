# Shared Agent Knowledge

This directory is the maintained project memory for both Codex and Claude.
Entry files should stay short; detailed, decision-changing knowledge belongs
here and must be updated with the code that changes it.

## Always Apply

- The source PDF is immutable. Write translations to a separate output path.
- Prefer a partial but structurally valid translation over damaged formulas,
  missing markers, broken tables, lost styles, or clipped text.
- Use the bundled `pdf2zh/` core and supported CLI. Do not silently substitute
  the PyPI package or add OCR/complex-script support that the engine lacks.
- Generated PDFs, models, optimized graphs, caches, build folders, virtual
  environments, and release archives stay out of commits.
- Diagnose with evidence from the actual PDF: matrices, font resources,
  content streams, page geometry, extracted spans, and rendered pages.
- External mutations such as overwrite, push, merge, tag, release, signing,
  or deletion require scope from the user. Verify exact targets first.

## Load by Task

| Task | Read |
| --- | --- |
| Navigate or modify the repository | [repository.md](repository.md) |
| Change translation/layout behavior | [pdf-engine.md](pdf-engine.md) and [regressions.md](regressions.md) |
| Diagnose or deliver a PDF | [validation.md](validation.md) and the relevant regression entries |
| Build, merge, or publish | [release.md](release.md) and [validation.md](validation.md) |
| Build or release the Android app | [android.md](android.md) |

For PDF work, also read the product contract in
[`references/preservation-rules.md`](../references/preservation-rules.md) and
the reusable workflow in [`SKILL.md`](../SKILL.md).
