@AGENTS.md

# Claude Code

`AGENTS.md` (imported above) is the source of truth for engineering rules. This file only
adds Claude Code-specific setup.

## MCP servers

MCP configuration is local and not versioned (`.mcp.json` is gitignored because the
repository is public).

- `supabase` (local `.mcp.json`): Supabase hosted MCP, restricted with `read_only=true` and
  `features=database,docs`. Use it to inspect schema; never apply migrations or write data
  through it. Schema changes go through Django migrations.
- `github`: configured per user.

## Secret files

`.claude/settings.json` denies `.env` and runs `.claude/hooks/block_env_files.py`, which
blocks every `.env` / `.env.*` file except templates ending in `.example`. Shell commands are
checked best-effort. Do not try to work around it: document new variables in `.env.example`,
`.env.railway-supabase.example` and `docs/configuracion.md`.

## Project skills

`.claude/skills/` contains `nuevo-crud-django`, `componente-ui` and `deploy-railway`
(user-invoked only; it prepares and verifies, it never deploys).
