# xbot Init Templates

This directory is the single source of truth for `xbot init` bootstrap content.

- `workspace/`: files copied into user workspace (e.g. `AGENTS.md`, `memory/`).
- `skills/`: optional init-installed skill templates/packages.
- `commands/`: optional init-installed command templates/packages.
- `packs/`: pack manifests (which skills/commands are installed by default).
- Config defaults are schema-driven (`xbot.config.schema.Config`) and written by `xbot init`; no separate `config_template/` directory is maintained.

`xbot/templates/` remains for backward compatibility during migration.
