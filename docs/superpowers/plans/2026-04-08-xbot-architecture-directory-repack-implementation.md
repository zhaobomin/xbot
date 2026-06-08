# xbot Architecture Directory Repack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to execute this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Complete a one-window, domain-driven repack of `xbot/` with conservative external compatibility, and merge `init_templates` into `templates` (keep only `xbot/templates/`).

**Architecture:** Reorganize code into domains (`runtime`, `interaction`, `tools`, `crew`, `platform`, `interfaces`) while preserving external behavior (`xbot` CLI entrypoint and core runtime flows).

**Tech Stack:** Python 3.11+, asyncio, typer, pytest

---

## Scope and Non-Goals

### In Scope

1. Repack `xbot/` directory structure by domain boundaries.
2. Move `cron` + `heartbeat` into `runtime/system/`.
3. Merge `xbot/init_templates/**` into `xbot/templates/**`.
4. Replace all in-repo `init_templates` references.
5. Keep runtime behavior and CLI entrypoint stable.

### Out of Scope

1. Functional feature changes.
2. API redesign.
3. Cross-repo migration of auxiliary modules (`mcp`, `bridge`).

---

## Phase 0: Freeze and Baseline

**Files:** none (operational gate)

- [ ] Freeze trunk merges for migration window (0.5-1 day).
- [ ] Create rollback tag: `pre-arch-repack-2026-04-08`.
- [ ] Capture baseline checks:
  - [ ] `pytest -q`
  - [ ] `python -m xbot --help`
  - [ ] `python -m xbot webui --help`
  - [ ] Core startup smoke in local environment

**Exit criteria:** Baseline artifacts captured and reproducible.

---

## Phase 1: Create Target Domain Skeleton

**Files (new directories with `__init__.py`):**

- [ ] `xbot/runtime/core/`
- [ ] `xbot/runtime/state/`
- [ ] `xbot/runtime/system/`
- [ ] `xbot/runtime/session/`
- [ ] `xbot/interaction/`
- [ ] `xbot/tools/`
- [ ] `xbot/memory/`
- [ ] `xbot/crew/`
- [ ] `xbot/platform/config/`
- [ ] `xbot/platform/providers/`
- [ ] `xbot/platform/security/`
- [ ] `xbot/platform/bus/`
- [ ] `xbot/platform/logging/`
- [ ] `xbot/platform/utils/`
- [ ] `xbot/interfaces/cli/`
- [ ] `xbot/interfaces/webui/`

- [ ] Add migration map file: `docs/superpowers/plans/2026-04-08-xbot-path-map.md`

**Exit criteria:** Target tree exists and path map draft is complete.

---

## Phase 2: Bulk Move by Domain (No Behavior Change)

### 2.1 Runtime Domain

- [ ] Move `xbot/agent/service.py` -> `xbot/runtime/core/service.py`
- [ ] Move `xbot/agent/state/*` -> `xbot/runtime/state/*`
- [ ] Move `xbot/session/*` -> `xbot/runtime/session/*`
- [ ] Move `xbot/cron/*` + `xbot/heartbeat/*` -> `xbot/runtime/system/*`

### 2.2 Interaction / Tools / Memory / Crew

- [ ] Move `xbot/agent/interaction/*` -> `xbot/interaction/*`
- [ ] Move `xbot/agent/tools/*` -> `xbot/tools/*`
- [ ] Move `xbot/agent/memory/*` -> `xbot/memory/*`
- [ ] Move `xbot/agent/crew/*` -> `xbot/crew/*`

### 2.3 Platform and Interfaces

- [ ] Move `xbot/config/*` -> `xbot/platform/config/*`
- [ ] Move `xbot/providers/*` -> `xbot/platform/providers/*`
- [ ] Move `xbot/security/*` -> `xbot/platform/security/*`
- [ ] Move `xbot/bus/*` -> `xbot/platform/bus/*`
- [ ] Move `xbot/logging.py` -> `xbot/platform/logging/__init__.py` (or `logging.py` with re-export)
- [ ] Move `xbot/utils/*` -> `xbot/platform/utils/*`
- [ ] Move `xbot/cli/*` -> `xbot/interfaces/cli/*`
- [ ] Move `xbot/webui/*` -> `xbot/interfaces/webui/*`

### 2.4 Import Rewrite and Compatibility

- [ ] Rewrite absolute imports across `xbot/` to new paths.
- [ ] Rewrite relative imports broken by file moves.
- [ ] Keep `project.scripts` entrypoint valid (`xbot.cli.commands:app`) via minimal compatibility wrapper.
- [ ] Keep high-frequency old imports as thin re-export shims when needed.

**Exit criteria:** Repository imports resolve; app starts.

---

## Phase 3: Template Unification (Mandatory)

- [ ] Inventory both trees:
  - [ ] `xbot/init_templates/**`
  - [ ] `xbot/templates/**`
- [ ] Merge rules:
  - [ ] Prefer existing `xbot/templates/` on conflicts.
  - [ ] Copy missing assets from `init_templates` into `templates`.
- [ ] Rewrite all references `init_templates` -> `templates` in:
  - [ ] Python source
  - [ ] Tests
  - [ ] Scripts
  - [ ] Docs
- [ ] Remove `xbot/init_templates/`.
- [ ] Add guard test/check: fail if `init_templates` appears in repo runtime paths.

**Exit criteria:** `init_templates` references count is 0 in runtime codepaths.

---

## Phase 4: Dependency Boundary Enforcement

- [ ] Add import-lint checks (script or test) for allowed direction:
  - [ ] `interfaces -> runtime/interaction/channels/crew/tools -> platform`
- [ ] Forbid `platform` importing upper domains.
- [ ] Forbid `tools` importing `interfaces`.
- [ ] Forbid direct `channels -> crew` coupling.

**Exit criteria:** Boundary checks pass in CI/local.

---

## Phase 5: Verification Gates

### 5.1 Test and Static Gates

- [ ] `pytest -q`
- [ ] `ruff check xbot tests`
- [ ] Type/import sanity (existing project command)

### 5.2 Behavioral Smoke

- [ ] `python -m xbot --help`
- [ ] Core CLI command smoke
- [ ] WebUI serve path smoke
- [ ] Session flow smoke: message -> tool -> response
- [ ] Permission flow smoke: AskUserQuestion parsing/validation

### 5.3 Template Gates

- [ ] `rg -n "init_templates" xbot tests scripts docs` returns no runtime-path hits.

**Exit criteria:** All gates green.

---

## Phase 6: Cleanup and Release Notes

- [ ] Remove temporary compatibility shims that are no longer required.
- [ ] Update architecture and structure docs.
- [ ] Finalize path migration map in `2026-04-08-xbot-path-map.md`.
- [ ] Prepare migration changelog entry.

---

## Rollback Plan

- [ ] Immediate rollback trigger conditions:
  - [ ] Core CLI unusable
  - [ ] Systemic test regressions
  - [ ] Template initialization broken
- [ ] Rollback action: reset branch to tag `pre-arch-repack-2026-04-08`.
- [ ] Preserve failed migration branch for postmortem.

---

## Execution Notes

1. Keep commits domain-scoped and reviewable even within one migration window.
2. Run verification after each domain move, not only at the end.
3. Do not mix behavior changes with path migration.
4. Treat template unification as a release blocker.
