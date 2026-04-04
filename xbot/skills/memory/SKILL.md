---
name: memory
description: Claude-style durable memory with indexed topic files and relevant recall.
always: true
---

# Memory

## Structure

- `memory/MEMORY.md` — Index only. Always loaded into context.
- `memory/<type>/*.md` — Durable memory topics, where `<type>` is one of `user`, `feedback`, `project`, `reference`.

MEMORY.md is an index, not a memory body. Put details in topic files, not in the index.

## Recall And Verification

- Read `MEMORY.md` first to find candidate topics.
- Read relevant topic files when the user references prior decisions, preferences, or external references.
- If a memory mentions a path, function, or flag, verify it against the repo before acting on it.
- If the user says to ignore memory, proceed as if `MEMORY.md` were empty.

## What To Save

Write durable facts as topic files:
- User preferences ("I prefer dark mode")
- Collaboration feedback ("Use rg before grep")
- Project context that cannot be inferred from the repo
- External references and dashboards

Do not save:
- Code structure, file trees, or git history
- Temporary session state
- Facts already encoded in `CLAUDE.md`

## Background Memory Workers

- Turn-end extraction updates topic files from recent conversation signal.
- Auto Dream periodically consolidates and prunes durable memory across sessions.
