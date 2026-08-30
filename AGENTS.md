# Repository Guidelines

## Shared Knowledge

The canonical instructions for Codex and Claude live in
[`agent-knowledge/index.md`](agent-knowledge/index.md). Read that file before
acting, then load the topic files it routes to for the current task. Do not copy
those details into this file; update the shared knowledge when behavior changes.

## Working Contract

- Preserve source PDFs and unrelated user changes. Generated PDFs, models,
  caches, virtual environments, `build/`, and `dist/` are not source artifacts.
- Keep preservation-sensitive changes in `pdf2zh/` small and backed by a
  regression test that demonstrates the failing geometry, marker, or glyph.
- Use Python 3.12 and the repository virtual environment. Run the full
  `unittest` suite, `pip check`, hard-error Ruff checks, and proportionate PDF
  render QA before handoff.
- Do not commit, push, merge, tag, publish, sign, or overwrite user output
  unless the user has authorized that operation. A release must have matching
  `APP_VERSION` and `v*` tag values.
- Keep UI text consistent with the existing Vietnamese application. Follow
  nearby Python style: four spaces, type annotations, `snake_case` functions,
  `PascalCase` classes, `UPPER_CASE` constants, and `pathlib.Path` for paths.

## Task Routing

- Repository layout and commands: [`repository.md`](agent-knowledge/repository.md)
- PDF architecture and invariants: [`pdf-engine.md`](agent-knowledge/pdf-engine.md)
- Known failures and proven fixes: [`regressions.md`](agent-knowledge/regressions.md)
- Test and visual QA: [`validation.md`](agent-knowledge/validation.md)
- Windows/macOS builds and releases: [`release.md`](agent-knowledge/release.md)

The reusable translation workflow remains in [`SKILL.md`](SKILL.md). Claude
Code uses the project adapter under `.claude/skills/pdf-translate/`.
