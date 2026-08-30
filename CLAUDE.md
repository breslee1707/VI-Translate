# Repository Guidelines

@agent-knowledge/index.md

## Claude Code Adapter

- Treat `agent-knowledge/` as the shared source of truth with Codex. Read the
  topic file routed by the index before changing code or release state.
- Use the project skill `/pdf-translate` for PDF translation, preservation
  diagnosis, regression QA, or incomplete-output investigation.
- On macOS, start Claude Code from this repository (or a subdirectory). Claude
  automatically discovers `.claude/skills/pdf-translate/SKILL.md` after a
  clone or pull. If `.claude/skills` was created after the current session
  started, restart Claude once, then verify discovery with `/skills`.
- Keep personal machine paths, permissions, and secrets in
  `CLAUDE.local.md` or `.claude/settings.local.json`; both are gitignored.
