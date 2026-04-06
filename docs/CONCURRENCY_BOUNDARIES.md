# Concurrency Boundaries

This repository intentionally uses both `asyncio.Lock` and `threading.Lock`, but they protect different classes of state.

## Allowed Patterns

- `asyncio.Lock`: event-loop-confined state shared by coroutines in the same loop.
- `threading.Lock`: synchronous state that may be touched from non-async callbacks or cross-thread integration points.
- message passing / queue handoff: the preferred bridge between thread-owned and event-loop-owned state.

## Rules

- Never hold a `threading.Lock` across an `await`.
- Never use `threading.Lock` as a substitute for coroutine scheduling control.
- If state is only accessed from the main event loop, prefer `asyncio.Lock` or event-loop confinement over `threading.Lock`.
- If a module keeps `threading.Lock`, document the non-async or cross-thread caller that requires it.

## Current Decisions

- `xbot/channels/feishu.py`: keeps `threading.Lock` because message dedup state can be touched from WebSocket callback paths outside the normal coroutine flow.
- `xbot/agent/capabilities/tool_adapter.py`: keeps `threading.Lock` because MCP/tool registration may be triggered from sync integration paths; lock scope must stay synchronous-only.
- `xbot/agent/tools/registry.py`: keeps `threading.Lock` because registry operations are synchronous dictionary mutations and must not cross `await`.
