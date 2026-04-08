# xbot Path Migration Map (Draft)

This mapping tracks old paths to target domain paths for the one-window repack.

## Runtime

- `xbot/agent/service.py` -> `xbot/runtime/core/service.py`
- `xbot/agent/state/*` -> `xbot/runtime/state/*`
- `xbot/session/*` -> `xbot/runtime/session/*`
- `xbot/cron/*` -> `xbot/runtime/system/cron/*`
- `xbot/heartbeat/*` -> `xbot/runtime/system/heartbeat/*`

## Interaction / Tools / Memory / Crew

- `xbot/agent/interaction/*` -> `xbot/interaction/*`
- `xbot/agent/tools/*` -> `xbot/tools/*`
- `xbot/agent/memory/*` -> `xbot/memory/*`
- `xbot/agent/crew/*` -> `xbot/crew/*`

## Platform

- `xbot/config/*` -> `xbot/platform/config/*`
- `xbot/providers/*` -> `xbot/platform/providers/*`
- `xbot/security/*` -> `xbot/platform/security/*`
- `xbot/bus/*` -> `xbot/platform/bus/*`
- `xbot/logging.py` -> `xbot/platform/logging/__init__.py`
- `xbot/utils/*` -> `xbot/platform/utils/*`

## Interfaces

- `xbot/cli/*` -> `xbot/interfaces/cli/*`
- `xbot/webui/*` -> `xbot/interfaces/webui/*`

## Templates Unification

- `xbot/init_templates/workspace/*` -> `xbot/templates/workspace/*`
- `xbot/init_templates/skills/*` -> `xbot/templates/skills/*`
- `xbot/init_templates/commands/*` -> `xbot/templates/commands/*`
- `xbot/init_templates/packs/default.json` -> `xbot/templates/packs/default.json`
- `xbot/init_templates/` -> removed
