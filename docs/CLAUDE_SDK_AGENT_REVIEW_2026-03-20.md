# Claude SDK Agent Review And Improvement Plan

> Date: 2026-03-20
> Scope: dual-agent upgrade, gateway integration, Claude SDK path, router/backend abstraction
> Review basis: current workspace code only

## Summary

This upgrade has successfully connected `gateway` to a configurable dual-agent entrypoint, so `config.agents.type` now affects the gateway runtime via `create_agent()`. However, the codebase currently has two parallel integration paths:

1. A gateway-facing path based on `AgentLoop` and `ClaudeSDKAgentLoop`
2. A new abstraction path based on `AgentRouter` and backend adapters

The first path is partially usable. The second path is still not production-ready and is not actually wired into the runtime. The biggest risk is architectural drift: behavior, provider handling, tools, and safety boundaries now differ depending on which path a caller uses.

## Findings

### P0: `AgentRouter` path is still dead code, and its LiteLLM backend is broken at runtime

The new backend abstraction was added, but the live gateway still uses `create_agent()` from `nanobot.agent.claude_sdk_loop` instead of `AgentRouter`. At the same time, the new `LiteLLMBackend` cannot construct `AgentLoop` correctly because it passes parameters that `AgentLoop.__init__()` does not accept.

Evidence:

- Gateway uses `create_agent()` directly instead of `AgentRouter`:
  - `nanobot/cli/commands.py:465`
  - `nanobot/cli/commands.py:494`
- `AgentRouter` exists but is not referenced by runtime callers:
  - `nanobot/agent/router.py:16`
  - `nanobot/agent/router.py:147`
- `LiteLLMBackend` passes invalid constructor args:
  - `nanobot/agent/backends/litellm_backend.py:55`
  - `nanobot/agent/loop.py:45`

Impact:

- The new abstraction cannot be trusted as an execution path.
- Future contributors may assume the backend abstraction is the source of truth when it is not.
- Any later attempt to switch runtime to `AgentRouter` will fail immediately.

### P0: `LiteLLMBackend.process()` still constructs `AgentResponse` with a non-existent `metadata` field

`AgentResponse` does not define `metadata`, but `LiteLLMBackend` still passes `metadata=response.metadata`. This is a runtime exception on the adapter path.

Evidence:

- `nanobot/agent/backends/litellm_backend.py:106`
- `nanobot/agent/protocol.py:12`

Impact:

- Even if the constructor mismatch above were fixed, the backend adapter path would still fail during response conversion.

### P1: Claude SDK gateway path still does not propagate tool routing context

The original `AgentLoop` calls `_set_tool_context()` before handling a message, which is how `message`, `cron`, and `spawn` know the current `channel/chat_id`. The Claude SDK loop creates MCP tools but never calls `ToolAdapter.set_tool_context()`, so tool execution is missing session routing context.

Evidence:

- Old loop sets context on every request:
  - `nanobot/agent/loop.py:158`
  - `nanobot/agent/loop.py:419`
- Claude SDK loop builds the adapter but never sets context:
  - `nanobot/agent/claude_sdk_loop.py:111`
  - `nanobot/agent/claude_sdk_loop.py:320`
- Adapter supports context injection, but is unused:
  - `nanobot/agent/tool_adapter.py:186`

Impact:

- `message` may fail with no target routing information.
- `cron` may fail to create jobs tied to the current conversation.
- The SDK agent is not behaviorally equivalent to the original agent for channel-aware tools.

### P1: Claude SDK tool adapter bypasses `restrict_to_workspace`

The original loop restricts file tools and `ExecTool` when `config.tools.restrict_to_workspace` is enabled. The Claude SDK tool adapter does not pass `allowed_dir` to file tools and does not enable `restrict_to_workspace` on the shell tool.

Evidence:

- Original loop applies workspace restriction:
  - `nanobot/agent/loop.py:116`
  - `nanobot/agent/loop.py:123`
- Claude SDK adapter does not:
  - `nanobot/agent/tool_adapter.py:124`
  - `nanobot/agent/tool_adapter.py:140`

Impact:

- The Claude SDK path has a wider authority surface than the LiteLLM path.
- A config flag that users reasonably expect to be global is only enforced on one agent family.

### P1: CLI and gateway still behave differently for agent selection

The gateway now uses `config.agents.type`, but the `nanobot agent` command still directly constructs `AgentLoop`, so CLI usage cannot exercise the Claude SDK path.

Evidence:

- Gateway uses `create_agent()`:
  - `nanobot/cli/commands.py:494`
- CLI still hardcodes `AgentLoop`:
  - `nanobot/cli/commands.py:657`
  - `nanobot/cli/commands.py:678`

Impact:

- The same config produces different runtime behavior depending on entrypoint.
- Developers cannot reliably validate Claude SDK behavior through the direct CLI workflow.

### P1: Claude SDK path still lacks feature parity with the original loop

The LiteLLM-based loop supports slash commands, memory consolidation, background archival, explicit progress/tool-hint semantics, and subagent spawning. The Claude SDK loop currently does not replicate those runtime behaviors.

Evidence:

- Original loop capabilities:
  - Slash commands: `nanobot/agent/loop.py:271`
  - Memory/session handling: `nanobot/agent/loop.py:369`, `nanobot/agent/loop.py:447`
  - Spawn tool registration: `nanobot/agent/loop.py:132`
  - Tool hints: `nanobot/agent/loop.py:173`, `nanobot/agent/loop.py:432`
- Claude SDK loop omits or simplifies these behaviors:
  - `nanobot/agent/claude_sdk_loop.py:287`
  - `nanobot/agent/claude_sdk_loop.py:320`

Impact:

- `claude_sdk` is not just a transport swap; it changes product behavior.
- Users switching `agents.type` may lose existing workflows silently.

### P2: `process_direct()` still calls async progress callbacks without awaiting them

The Claude SDK direct-processing path invokes `on_progress(block.text)` but does not await it. Gateway heartbeat currently passes an async `_silent()` callback, so this path is inconsistent with the typed async contract used by the old loop.

Evidence:

- Non-awaited callback:
  - `nanobot/agent/claude_sdk_loop.py:438`
- Async callback passed in from gateway heartbeat:
  - `nanobot/cli/commands.py:573`
  - `nanobot/cli/commands.py:576`

Impact:

- Progress callbacks can be silently dropped.
- Runtime warnings are possible depending on execution path and SDK behavior.

### P2: Provider metadata is now duplicated in two registries and has already diverged

The project now has both `nanobot/providers/registry.py` and `nanobot/config/provider_registry.py`. They describe overlapping provider concepts but with different field models and different defaults.

Evidence:

- Runtime LiteLLM registry:
  - `nanobot/providers/registry.py:1`
- New config/provider compatibility registry:
  - `nanobot/config/provider_registry.py:1`
- Example drift: `alrun` base URL is empty in one registry and concrete in the other.

Impact:

- Provider behavior may differ across validation, model detection, SDK setup, and LiteLLM setup.
- Adding or changing providers now requires updating multiple sources of truth.

### P2: The design document does not match the actual implementation state

The architecture doc describes `AgentRouter` as the central runtime switch and presents the backend abstraction as the main integration path. That is not true in the current codebase, where runtime still pivots through `create_agent()`.

Evidence:

- Doc claims router-centered runtime:
  - `docs/DUAL_AGENT_ARCHITECTURE.md:72`
  - `docs/DUAL_AGENT_ARCHITECTURE.md:140`
- Actual runtime still uses factory branching:
  - `nanobot/cli/commands.py:494`
  - `nanobot/agent/claude_sdk_loop.py:470`

Impact:

- The doc overstates implementation completeness.
- Reviewers and future implementers can make incorrect assumptions about what is already shipped.

## What Improved In This Upgrade

- Gateway now at least exposes an agent-type switch through configuration.
- Provider/config schema now has explicit Claude SDK-related fields.
- External MCP servers are passed into the gateway Claude SDK loop.
- The code compiles at the Python syntax level for the reviewed files.

## Verification Performed

- Ran `python -m py_compile` on the changed and newly added dual-agent files.
- Reviewed current diffs and new files under `nanobot/agent/`, `nanobot/config/`, `nanobot/providers/`, and `docs/`.
- Did not run live Claude SDK integration tests in this review.

## Improvement Plan

### Phase 1: Collapse to one runtime path before adding more features

Goal:

- Choose one execution model as the only supported runtime path.

Recommended direction:

- Keep `create_agent()` as the temporary runtime entrypoint only if it is explicitly marked transitional.
- Otherwise, finish `AgentRouter` and migrate gateway and CLI to it.
- Do not maintain both `create_agent()` branching and `AgentRouter` as parallel runtime entrypoints.

Deliverables:

- One runtime selection path for gateway and CLI
- One source of truth for backend lifecycle
- Removal or explicit quarantine of dead adapter code

### Phase 2: Restore baseline parity for Claude SDK behavior

Goal:

- Make `claude_sdk` preserve the core product contract of the current agent.

Required work:

- Inject tool context on every Claude SDK request
- Enforce `restrict_to_workspace` consistently in `ToolAdapter`
- Decide whether `spawn`, slash commands, and memory/session persistence are required parity items
- Align progress and tool-hint behavior with channel config semantics

Acceptance criteria:

- `message` and `cron` work correctly from Claude SDK sessions
- Safety restrictions match LiteLLM behavior
- Switching `agents.type` does not silently remove core agent capabilities

### Phase 3: Fix the unfinished backend adapter layer or remove it

Goal:

- Eliminate the current broken-in-waiting state.

Required work:

- Fix `LiteLLMBackend` constructor usage
- Fix `AgentResponse` mapping
- Ensure backend registration is explicit and exercised by tests
- Make router-backed processing pass a real smoke path before exposing it as architecture

Acceptance criteria:

- Router path can initialize, process one message, and shut down cleanly
- No dead-code-only abstractions remain in the critical path

### Phase 4: Unify provider metadata

Goal:

- Remove duplicated provider truth tables.

Required work:

- Choose a single provider registry model
- Make validation, runtime detection, SDK compatibility, and base URL derivation all read from the same structure
- Add regression tests for `anthropic`, `aliyun_coding_plan`, and `alrun`

Acceptance criteria:

- Adding a provider requires touching one primary registry definition
- Config validation and runtime behavior agree for the same provider/model pair

### Phase 5: Bring docs in line with shipping behavior

Goal:

- Ensure docs describe what exists, not the intended end state.

Required work:

- Update `docs/DUAL_AGENT_ARCHITECTURE.md` to clearly separate:
  - implemented now
  - partially implemented
  - planned next
- Document current feature-parity gaps between `litellm` and `claude_sdk`

Acceptance criteria:

- A new contributor can read the docs and correctly predict the runtime wiring
- No diagram implies `AgentRouter` is live unless that is actually true

## Suggested Execution Order

1. Decide the single runtime path.
2. Fix tool-context propagation and workspace restriction.
3. Align CLI and gateway behavior.
4. Repair or remove the backend adapter layer.
5. Unify provider metadata.
6. Update architecture docs after code truth is stable.

## Recommendation

Do not add more Claude SDK-specific surface area yet. The highest-leverage move is to reduce duplication first. Right now the main problem is not lack of features; it is that the same feature is being represented by multiple partially overlapping implementations.
