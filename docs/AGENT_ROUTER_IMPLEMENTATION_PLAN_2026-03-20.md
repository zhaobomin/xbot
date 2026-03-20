# Agent Router Runtime Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AgentRouter` the only runtime entrypoint for gateway and CLI, repair both official backends, and bring `claude_sdk` closer to full parity while preserving config and tool compatibility.

**Architecture:** Introduce a router-backed runtime facade that owns shared message-loop orchestration and direct-processing entrypoints, then narrow each backend to request/response adaptation plus backend-native features. Reuse proven `AgentLoop` behavior where possible, but centralize runtime selection, slash commands, and safety policy so gateway and CLI stop diverging.

**Tech Stack:** Python 3.12, asyncio, Typer, pytest, Claude Agent SDK, LiteLLM

## Current Execution Status

- Round 1 completed:
  - `gateway` and `nanobot agent` now use `AgentRuntime -> AgentRouter -> backend`
  - `LiteLLMBackend` runtime wiring was repaired
  - Claude SDK tool context and workspace restrictions were restored
- Round 2 completed:
  - `nanobot.config.provider_registry` now derives from `nanobot.providers.registry`
  - `anthropic` default base URL is now defined in the canonical provider registry
  - `nanobot.agent.claude_sdk_loop.create_agent()` is explicitly marked as deprecated legacy compatibility
- Round 3 completed:
  - Claude SDK tool adapter now registers `spawn` parity via the existing subagent manager
  - unified runtime `/stop` now cancels backend-managed session subagents, not only foreground tasks
  - Claude SDK backend now persists session history and resets/archive-clears session state on `/new`
  - direct router runtime progress callbacks now receive backend delta updates
- Remaining major work is now concentrated in deeper SDK-native handoff productization, not runtime parity or known blocking bugs

---

### Task 1: Lock The Current Failure Modes With Tests

**Files:**
- Create: `tests/test_agent_router_runtime.py`
- Modify: `tests/test_message_tool.py`
- Modify: `tests/test_tool_validation.py`

- [ ] **Step 1: Write failing tests for router-backed runtime selection**

Add tests that assert:
- a router runtime can initialize the configured backend
- gateway/CLI helper creation does not bypass `agents.type`
- unsupported or unregistered backends fail clearly

- [ ] **Step 2: Write failing tests for Claude SDK context and safety parity**

Add tests that assert:
- `ToolAdapter.set_tool_context()` is applied before message processing
- `message` tool inherits channel/chat routing in the SDK path
- `restrict_to_workspace` is enforced for SDK file/shell tools

- [ ] **Step 3: Run the targeted tests to verify RED**

Run:
```bash
pytest tests/test_agent_router_runtime.py tests/test_message_tool.py tests/test_tool_validation.py -q
```

Expected:
- new router/parity tests fail for the current code

### Task 2: Make `AgentRouter` A Real Runtime Entry

**Files:**
- Create: `nanobot/agent/runtime.py`
- Modify: `nanobot/agent/__init__.py`
- Modify: `nanobot/agent/router.py`
- Modify: `nanobot/cli/commands.py`

- [ ] **Step 1: Write the minimal router runtime facade**

Implement a small runtime facade that:
- owns `run()`, `process_direct()`, `stop()`, `close_mcp()`
- consumes inbound bus messages
- routes requests to `AgentRouter`
- exposes `tools` and `model` consistently for gateway integrations

- [ ] **Step 2: Register default backends deterministically**

Ensure backend registration happens from runtime construction, not by convention.

- [ ] **Step 3: Switch gateway and CLI to the router runtime**

Remove direct `create_agent()` and direct `AgentLoop(...)` construction from user entrypoints.

- [ ] **Step 4: Run targeted tests to verify GREEN**

Run:
```bash
pytest tests/test_agent_router_runtime.py -q
```

Expected:
- router runtime tests pass

### Task 3: Repair `LiteLLMBackend`

**Files:**
- Modify: `nanobot/agent/backends/litellm_backend.py`
- Modify: `nanobot/agent/protocol.py`
- Test: `tests/test_agent_router_runtime.py`

- [ ] **Step 1: Fix backend initialization to match `AgentLoop` constructor**

Pass only the supported arguments and shared resources that the current loop actually accepts.

- [ ] **Step 2: Fix response mapping**

Remove invalid `AgentResponse` construction and normalize any metadata handling into the shared protocol if needed.

- [ ] **Step 3: Verify the LiteLLM backend path works through the router**

Run:
```bash
pytest tests/test_agent_router_runtime.py -q
```

Expected:
- LiteLLM router path initializes and processes successfully

### Task 4: Repair `ClaudeSDKBackend` And Close The Biggest Parity Gaps

**Files:**
- Modify: `nanobot/agent/backends/claude_sdk_backend.py`
- Modify: `nanobot/agent/tool_adapter.py`
- Modify: `nanobot/agent/protocol.py`
- Test: `tests/test_agent_router_runtime.py`
- Test: `tests/test_message_tool.py`
- Test: `tests/test_tool_validation.py`

- [ ] **Step 1: Inject tool routing context in the SDK path**

Ensure current `channel`, `chat_id`, and `message_id` reach SDK-backed tools before tool execution.

- [ ] **Step 2: Enforce workspace restriction parity**

Apply `allowed_dir` and `restrict_to_workspace` consistently inside `ToolAdapter`.

- [ ] **Step 3: Align visible tool names and behavior**

Remove mismatches like `shell` vs `exec` where user-facing prompt and actual tool registration disagree.

- [ ] **Step 4: Run targeted parity tests**

Run:
```bash
pytest tests/test_agent_router_runtime.py tests/test_message_tool.py tests/test_tool_validation.py -q
```

Expected:
- SDK routing and safety tests pass

### Task 5: Unify Runtime Behavior Shared By Both Backends

**Files:**
- Modify: `nanobot/agent/runtime.py`
- Modify: `nanobot/agent/protocol.py`
- Modify: `nanobot/agent/backends/litellm_backend.py`
- Modify: `nanobot/agent/backends/claude_sdk_backend.py`
- Test: `tests/test_restart_command.py`
- Test: `tests/test_task_cancel.py`

- [ ] **Step 1: Preserve slash commands at the runtime layer**

Support `/new`, `/help`, `/stop`, and `/restart` from the unified runtime path.

- [ ] **Step 2: Preserve direct-processing behavior needed by cron and heartbeat**

Make sure `process_direct()` works consistently for both backends.

- [ ] **Step 3: Run runtime behavior tests**

Run:
```bash
pytest tests/test_task_cancel.py tests/test_restart_command.py -q
```

Expected:
- shared runtime command behavior passes

### Task 6: Reduce Transitional Duplication

**Files:**
- Modify: `nanobot/agent/claude_sdk_loop.py`
- Modify: `nanobot/cli/commands.py`
- Modify: `docs/DUAL_AGENT_ARCHITECTURE.md`
- Modify: `docs/CLAUDE_SDK_AGENT_REVIEW_2026-03-20.md`

- [ ] **Step 1: Mark or remove obsolete runtime-only branches**

Reduce `claude_sdk_loop.py` to helper code only, or clearly mark it as legacy transitional code if it must remain temporarily.

- [ ] **Step 2: Update docs to match the real runtime**

Document the router-backed final architecture and note any remaining parity gaps honestly.

- [ ] **Step 3: Run docs-adjacent smoke verification**

Run:
```bash
pytest tests/test_agent_router_runtime.py -q
```

Expected:
- no runtime regression from cleanup

### Task 7: Final Verification

**Files:**
- Verify only

- [ ] **Step 1: Run focused backend/runtime test suite**

Run:
```bash
pytest tests/test_agent_router_runtime.py tests/test_task_cancel.py tests/test_restart_command.py tests/test_message_tool.py tests/test_tool_validation.py -q
```

Expected:
- all pass

- [ ] **Step 2: Run a broader regression slice**

Run:
```bash
pytest tests/test_filesystem_tools.py tests/test_cron_service.py tests/test_message_tool_suppress.py tests/test_loop_consolidation_tokens.py -q
```

Expected:
- no regressions in core shared behaviors

- [ ] **Step 3: Record residual risks**

Note any still-open gaps around:
- SDK-native handoffs
- full memory parity
- provider registry consolidation
