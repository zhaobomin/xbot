# Code Review Findings — 2026-07-09

Full codebase review after v2.0.33 cleanup. 5 parallel review agents covered: service.py/runtime, CLI commands, config/providers, channels/gateway, and README-vs-code consistency.

---

## 🔴 Critical Bugs

### 1. Dangling Import: GroqTranscriptionProvider

**File:** `xbot/channels/base.py` line 45

```python
from xbot.platform.providers.transcription import GroqTranscriptionProvider
```

`xbot/platform/providers/transcription.py` does not exist. The import is caught by `try/except` in `transcribe_audio()` so it degrades gracefully, but the code path is dead and misleading.

**Fix:** Remove the import and the entire `_transcription_provider` / `transcribe_audio` code path from `base.py`. Audio transcription is not functional without a registered provider.

---

### 2. Dead Code: Groq Provider Lookup in Channel Manager

**File:** `xbot/channels/manager.py` lines 45-56

```python
def _transcription_api_key(self) -> str:
    groq = getattr(providers, "groq", None)      # groq removed from registry
    if groq is None:
        groq = custom_providers.get("groq")
    ...
```

`groq` is no longer a field on `ProvidersConfig`. `getattr` always returns `None`, so this method always returns `""`, silently disabling audio transcription for all channels (Telegram, Matrix, Feishu, WhatsApp).

**Fix:** Remove `_transcription_api_key()` entirely, or replace with a no-op stub that logs a warning.

---

### 3. Nanobot Naming Remnants in DingTalk Channel

**File:** `xbot/channels/dingtalk.py`

| Line | Issue |
|------|-------|
| 43 | Class name: `NanobotDingTalkHandler` |
| 46 | Docstring: "forwards them to the Nanobot channel" |
| 129 | Comment: "Forward to Nanobot via _on_message" |
| 219 | `handler = NanobotDingTalkHandler(self)` |
| **443** | **User-visible:** reply card title `"Nanobot Reply"` |
| 536 | Docstring: "called by NanobotDingTalkHandler" |

**Fix:** Rename class to `XbotDingTalkHandler`, change all "Nanobot" → "xbot" in comments/docstrings, change reply card title to `"xbot Reply"`.

---

### 4. session_id Inconsistency in Multimodal Query Path

**File:** `xbot/runtime/core/service.py` line 354

```python
# process() multimodal branch
frame = {
    "type": "user",
    "message": {"role": "user", "content": query_prompt},
    "parent_tool_use_id": None,
    "session_id": "default",   # ← hardcoded
}
```

vs `_enqueue_worker_message()` line 2874:

```python
"session_id": worker.session_key,  # ← correct session key
```

If the SDK uses `session_id` for state tracking, the `process()` path always collapses to `"default"`.

**Fix:** Replace `"default"` with `context.session_key`.

---

### 5. README: Docker Section References Deleted Files

**File:** `README.md` lines 1276-1313

The entire Docker section (`docker build`, `docker compose run`, `docker compose up`, etc.) references `Dockerfile` and `docker-compose.yml` which were **deleted in commit `b5342c4a` (2026-06-04)**.

**Fix:** Remove the Docker section from README.

---

### 6. README: References to Non-Existent Documents

**File:** `README.md`

| Line | Reference | Status |
|------|-----------|--------|
| 8-9 | `[./COMMUNICATION.md]` (Feishu/WeChat badges) | File deleted |
| 1409 | `[CONTRIBUTING.md](./CONTRIBUTING.md)` | File deleted |

**Fix:** Remove or replace the broken links.

---

### 7. Slack Channel: No Inbound Media Support

**File:** `xbot/channels/slack.py` lines 282-294

`_handle_message()` does not pass `media` parameter, but Slack `send()` (lines 143-152) supports sending media. Users' files/images sent to the bot are silently ignored.

**Fix:** Add media download and pass `media=media_paths` in `_handle_message()`.

---

## 🟡 Medium Issues

### 8. Dead Code: OpenRouter in Legacy Model Prefixes

**File:** `xbot/platform/config/sdk_resolver.py` line 22

```python
_LEGACY_MODEL_PREFIXES = {
    "anthropic",
    "aliyun_coding_plan",
    "alrun",
    "openrouter",    # ← removed provider
}
```

**Fix:** Remove `"openrouter"` from the set.

---

### 9. Stale Comments in Config Schema

**File:** `xbot/platform/config/schema.py`

| Line | Issue |
|------|-------|
| 374 | Comment references `github-copilot`/`openai_codex` (removed providers) |
| 422 | Docstring example: `"deepseek"`, `"openrouter"` (removed providers) |

**Fix:** Update comments/docstrings to use current provider names.

---

### 10. CLI Onboarding URL Points to Removed Provider

**File:** `xbot/interfaces/cli/commands.py` line 651

```python
console.print("     Get one at: https://openrouter.ai/keys")
```

**Fix:** Change to Anthropic Console URL or remove the URL line.

---

### 11. Synchronous File I/O in _build_query_prompt

**File:** `xbot/runtime/core/service.py` line 636

```python
raw_bytes = path_obj.read_bytes()
```

Blocking call in an async context. Large images (20MB+) will stall the event loop.

**Fix:** Use `await asyncio.to_thread(path_obj.read_bytes)` — requires making `_build_query_prompt` async, or reading bytes before calling it.

---

### 12. README: MCP Transport Count Incorrect

**File:** `README.md` lines 1049-1054

Table says "Two transport modes are supported" and lists only Stdio and HTTP.

**Actual code** (`xbot/platform/config/schema.py` line 299):
```python
type: Literal["stdio", "sse", "streamableHttp"] | None = None
```

Three modes: `stdio`, `sse`, `streamableHttp`.

**Fix:** Update the table to list all three modes.

---

### 13. README: CLI Reference Incomplete

**File:** `README.md` lines 1232-1254

CLI Reference lists 12 commands but omits 15+ including the entire `crew` subsystem:

| Missing Command | Source |
|---|---|
| `xbot sessions list` | commands.py:1589 |
| `xbot sessions show` | commands.py:1646 |
| `xbot plugins list` | commands.py:1813 |
| `xbot crew run` | commands.py:1903 |
| `xbot crew show` | commands.py:2004 |
| `xbot crew init` | commands.py:2052 |
| `xbot crew validate` | commands.py:2124 |
| `xbot crew checkpoints` | commands.py:2239 |
| `xbot crew resume` | commands.py:2302 |
| `xbot crew history` | commands.py:2384 |
| `xbot crew graph` | commands.py:2471 |
| `xbot crew export` | commands.py:2552 |

**Fix:** Add missing commands to the CLI Reference table.

---

### 14. README: Agent Social Network Has No Code Implementation

**File:** `README.md` lines 829-838

The section implies first-class integration with Moltbook and ClawdChat. In reality, `grep` for these terms across all `.py` files returns zero results — they are external platforms accessible only by generic `Read https://...` prompts.

**Fix:** Reword to clarify these are third-party integrations via skill prompts, not native features.

---

### 15. Multiple Channels Have No Inbound Media Support

| Channel | File | Issue |
|---------|------|-------|
| Email | `channels/email.py:133-138` | No `media` parameter, email attachments ignored |
| Mochat | `channels/mochat.py:826-835` | No `media` parameter, media messages lost |
| QQ | `channels/qq.py:204-209` | No `media` parameter, only handles text |

**Fix:** Add media download and pass `media=media_paths` in each channel's `_handle_message()`.

---

### 16. Feishu Post Content: Links Silently Dropped

**File:** `xbot/channels/feishu_content.py` line 214

```python
if tag in ("text", "a"):
    texts.append(el.get("text", ""))
```

`<a>` tags only keep display text, `href` URL is discarded. The model loses link context from rich-text messages.

**Fix:** Include URL in output, e.g. `texts.append(f"[{el.get('text', '')}]({el.get('href', '')})")`.

---

### 17. Docstrings Say "Media URLs" but Code Uses File Paths

**File:** `xbot/channels/base.py` line 108, `xbot/platform/bus/events.py` line 65

```python
media: list[str] = field(default_factory=list)  # Media URLs
```

All channels pass **local file paths**, not URLs.

**Fix:** Change comment to `# Media file paths (local downloads)`.

---

## 🟢 Low Priority

### 18. Unused Import: Callable

**File:** `xbot/interfaces/cli/commands.py` line 13

```python
from typing import Any, Callable
```

`Callable` is never used (was only used by the removed OAuth login code).

**Fix:** Remove `Callable` from the import.

---

### 19. Redundant Local Imports

**File:** `xbot/interfaces/cli/commands.py`

| Line | Redundant Import | Already Imported At |
|------|-----------------|---------------------|
| 279 | `from xbot.platform.config.paths import get_workspace_path` | Line 45 |
| 701 | `import json` | Line 4 |
| 1917 | `from pathlib import Path` | Line 12 |
| 2273 | `import json` | Line 4 |
| 2418 | `import json` | Line 4 |
| 2419 | `from datetime import datetime` | Line 11 |
| 2569 | `from pathlib import Path` | Line 12 |

**Fix:** Remove redundant local imports.

---

### 20. Dead Function: _session_namespace

**File:** `xbot/interfaces/gateway/app.py` lines 507-508

Function defined but never called anywhere in the codebase.

**Fix:** Remove the function.

---

### 21. Bare Expression: session_key

**File:** `xbot/interfaces/gateway/app.py` line 845

```python
session_key  # reserved for future per-session memory lookup
```

No-op expression — evaluated but result not used.

**Fix:** Remove the line or use it.

---

### 22. Feishu Comment Contradicts Code

**File:** `xbot/channels/feishu.py` lines 1314-1320

Comment says `"media" is only valid as a tag inside "post" messages`, but code uses `media_type = "media"` as standalone msg_type.

**Fix:** Verify behavior and correct either the comment or the code.

---

### 23. ReMe Memory Defaults to OpenAI Backend

**File:** `xbot/memory/reme.py` lines 110, 112, 182, 191

```python
{"model_name": "gpt-4.1-nano", "backend": "openai"}
{"model_name": "text-embedding-3-small", "backend": "openai"}
```

OpenAI is not a registered LLM provider in xbot. If the intent was to remove OpenAI, these defaults need updating. If ReMe genuinely uses OpenAI for embeddings, this should be documented.

**Fix:** Document the OpenAI dependency for memory or switch to an Anthropic-compatible backend.

---

### 24. Test Files: nanobot Naming Remnants

| File | Lines | Issue |
|------|-------|-------|
| `tests/test_telegram_channel.py` | 44, 537-567 | `"nanobot_test"` username |
| `tests/test_dingtalk_channel.py` | 155, 176 | `"nanobot_dingtalk"` path |

**Fix:** Rename to `"xbot_test"` / `"xbot_dingtalk"`.

---

### 25. README: WebUI Pages List Incomplete

**File:** `README.md` line 149

Lists: `Dashboard`, `Chat`, `Channels`, `Tools`, `Settings`, `System Config`.

Missing: `cron-jobs`, `integrations`, `mcp-servers`, `Skills`.

**Fix:** Add missing pages to the list.

---

## Summary Statistics

| Severity | Count | Categories |
|----------|-------|------------|
| 🔴 Critical | 7 | 2 broken imports/dead code, 1 naming, 1 session bug, 2 README broken links, 1 missing feature |
| 🟡 Medium | 10 | 5 dead code/stale refs, 2 README inaccuracies, 3 missing channel features |
| 🟢 Low | 8 | 2 unused imports, 1 dead function, 1 bare expression, 1 misleading comment, 1 backend default, 2 test naming, 1 incomplete list |
