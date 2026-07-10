# xbot Bug Fix Plan (Revised)

## Overview

This plan addresses 34 issues identified during a comprehensive code review of the xbot codebase. Issues are categorized by severity and module.

**Revised based on /autoplan review findings** — Critical issues around security and design ambiguity have been addressed with explicit specifications.

## Goals

1. Fix all critical security vulnerabilities with explicit specifications
2. Address medium-severity bugs affecting reliability and data integrity
3. Resolve low-severity issues for improved code quality
4. Ensure no regressions through comprehensive testing

---

## Phase 1: Critical Security Fixes (P0)

### 1.1 Remove Hardcoded Default Password

**File:** `xbot/webui/auth.py:15-16`

**Issue:** Default password "nanobot" is hardcoded in source code

**User Journey (Current):**
```
User runs `xbot webui` → sees "Running on http://localhost:8080" 
→ opens browser → enters "admin" / "nanobot" → success
```

**User Journey (After Fix):**
```
User runs `xbot webui` → FIRST RUN DETECTED
→ Console prints: "Generated WebUI password: <24-char-password>"
→ Console prints: "Password saved to ~/.xbot/webui/password"
→ WebUI starts
→ User opens browser → enters "admin" / <password-from-console> → success
→ User can change password via CLI or WebUI settings
```

**Specification:**

1. **On first launch** (no password file exists):
   ```python
   password = secrets.token_urlsafe(24)  # ~32 chars, URL-safe
   password_file = Path("~/.xbot/webui/password").expanduser()
   password_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
   password_file.write_text(hash_password(password), encoding="utf-8")
   password_file.chmod(0o600)
   print(f"\n{'='*60}")
   print("WebUI generated a secure password for first-time setup:")
   print(f"  Username: admin")
   print(f"  Password: {password}")
   print(f"\nPassword hash saved to: {password_file}")
   print("Please save this password securely. It will not be shown again.")
   print(f"{'='*60}\n")
   ```

2. **On subsequent launches** (password file exists):
   - Read hash from file normally
   - If file is corrupted/missing: **FAIL** with error message:
     ```
     Error: WebUI password file missing or corrupted.
     Run `xbot webui --reset-password` to generate a new password.
     ```

3. **Password change** via CLI:
   ```bash
   xbot webui --set-password <new-password>
   # Output: "Password updated successfully."
   ```

4. **Password retrieval** (if user forgot):
   ```bash
   xbot webui --reset-password
   # Output: "New password generated: <password>"
   # Output: "Password saved to ~/.xbot/webui/password"
   ```

**Files to modify:**
- `xbot/webui/auth.py` — Remove `DEFAULT_PASSWORD`, add password generation logic
- `xbot/webui/app.py` — Add startup password generation
- `xbot/cli/commands.py` — Add `--set-password` and `--reset-password` options

---

### 1.2 Restrict CLI `@path` File Access to Workspace

**File:** `xbot/cli/commands.py:116-143`

**Issue:** `@path` syntax allows reading arbitrary system files

**Approach:** ALLOWLIST only — reject all paths outside workspace

**Specification:**

1. **Validation logic:**
   ```python
   def validate_path_in_workspace(path: Path, workspace: Path) -> bool:
       """Allowlist: only paths within workspace are permitted."""
       try:
           resolved = path.resolve()  # Follows symlinks
           workspace_resolved = workspace.resolve()
           return resolved.is_relative_to(workspace_resolved)
       except (OSError, ValueError):
           return False
   ```

2. **Error message to user:**
   ```
   Error: '@path' can only reference files within the workspace directory.
   Workspace: /Users/you/workspace
   Attempted: /etc/passwd
   ```

3. **Symlink handling:**
   - Resolve both the path AND the workspace root
   - Check `is_relative_to()` after resolution
   - This prevents `@workspace/symlink_to_etc_passwd` attacks

4. **Test cases:**
   ```python
   # Should ALLOW:
   @path file.txt                    # workspace/file.txt
   @path ./subdir/file.txt           # workspace/subdir/file.txt
   @path ~/workspace/file.txt        # explicit workspace path
   
   # Should REJECT with error:
   @path /etc/passwd                 # absolute outside workspace
   @path ~/.ssh/id_rsa               # home outside workspace
   @path ../other-project/file.txt   # relative escape
   @path ./symlink_to_etc_passwd     # symlink escape (resolved)
   ```

---

### 1.3 Validate Skill/MCP Path Names

**File:** `xbot/webui/app.py:962-1002`

**Issue:** No validation of skill name, potential path traversal

**Specification:**

1. **Validation function:**
   ```python
   import unicodedata
   import re
   
   SKILL_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
   
   def validate_skill_name(name: str) -> str:
       """
       Validate skill name is safe for filesystem use.
       
       - Normalize Unicode (NFKC) to prevent encoding attacks
       - Reject path traversal sequences
       - Enforce alphanumeric + dash/underscore only
       - Max 64 chars
       """
       # Unicode normalization (handles ..%2f, fullwidth slashes, etc.)
       normalized = unicodedata.normalize('NFKC', name)
       
       if not SKILL_NAME_PATTERN.match(normalized):
           raise ValueError(
               f"Invalid skill name '{name}'. "
               "Use only letters, numbers, dashes, and underscores. "
               "Must start with a letter or number. Max 64 characters."
           )
       
       # Additional safety: reject any path separators
       if '/' in normalized or '\\' in normalized:
           raise ValueError(f"Skill name cannot contain path separators: {name}")
       
       return normalized
   ```

2. **Error response (HTTP 400):**
   ```json
   {
     "error": "Invalid skill name",
     "message": "Use only letters, numbers, dashes, and underscores. Must start with a letter or number. Max 64 characters.",
     "example": "my-skill-name"
   }
   ```

3. **Path verification after creation:**
   ```python
   skill_dir = workspace / "skills" / validated_name
   skill_dir.resolve().is_relative_to(workspace.resolve())  # Must be True
   ```

---

## Phase 2: Concurrency & Resource Fixes (P1)

### 2.1 Add Lock Protection for `_session_contexts`

**File:** `xbot/agent/backends/claude_sdk_backend.py:2053-2059`

**Issue:** Dict modification without lock, race condition under concurrent load

**Specification:**

1. **Add dedicated lock:**
   ```python
   # In __init__:
   self._session_contexts_lock = asyncio.Lock()
   ```

2. **Protect all operations:**
   ```python
   async with self._session_contexts_lock:
       session_contexts = self._shared_resources.setdefault("_session_contexts", {})
       _MAX_SESSION_CONTEXTS = 500
       if len(session_contexts) > _MAX_SESSION_CONTEXTS:
           excess = len(session_contexts) - _MAX_SESSION_CONTEXTS
           for _ in range(excess):
               session_contexts.pop(next(iter(session_contexts)))
   ```

3. **Read operations also need lock:**
   ```python
   async with self._session_contexts_lock:
       context = self._shared_resources.get("_session_contexts", {}).get(session_key)
   ```

---

### 2.2 Fix Client Creation Future Cleanup Race

**File:** `xbot/agent/backends/claude_sdk_backend.py:1470-1478`

**Issue:** Future popped before result set, waiters may miss signal

**Specification:**

1. **Set result BEFORE popping:**
   ```python
   # Success path (line ~1472):
   async with self._clients_lock:
       pending = self._client_creation_futures.get(session_key)
       if pending is not None and not pending.done():
           pending.set_result(client)  # SET FIRST
       self._client_creation_futures.pop(session_key, None)  # THEN POP
   
   # Exception path (line ~1476):
   except BaseException as e:
       async with self._clients_lock:
           pending = self._client_creation_futures.get(session_key)
           if pending is not None and not pending.done():
               pending.set_exception(e)  # SET FIRST
           self._client_creation_futures.pop(session_key, None)  # THEN POP
       raise
   ```

---

### 2.3 Periodic Cleanup for `_background_release_tasks`

**File:** `xbot/agent/backends/claude_sdk_backend.py:172`

**Issue:** Set grows indefinitely, memory leak

**Specification:**

1. **Add done callback on task creation:**
   ```python
   def _on_task_done(task: asyncio.Task) -> None:
       self._background_release_tasks.discard(task)
   
   task = asyncio.create_task(...)
   task.add_done_callback(_on_task_done)
   self._background_release_tasks.add(task)
   ```

2. **This ensures:**
   - Tasks removed from set when complete
   - Exceptions logged by callback handler
   - No unbounded growth

---

### 2.4 Handle Client Disconnect Failures Properly

**File:** `xbot/agent/state/store.py:554-558`

**Issue:** Silent failure leaves connections leaked

**Specification:**

```python
if entry.client:
    try:
        await entry.client.disconnect()
    except Exception as e:
        logger.warning(
            "SessionStore: client disconnect failed for session %s: %s",
            key, e
        )
        # Track for debugging
        self._failed_disconnects[key] = {
            "error": str(e),
            "timestamp": time.time()
        }
```

---

### 2.5 Fix `asyncio.Lock` default_factory Syntax

**File:** `xbot/agent/state/store.py:73`

**Issue:** Type annotation inconsistency

**Specification:**

```python
# Option A (preferred):
lock: asyncio.Lock = field(default_factory=lambda: asyncio.Lock())

# Option B:
lock: asyncio.Lock | None = field(default=None)
    
def __post_init__(self):
    if self.lock is None:
        self.lock = asyncio.Lock()
```

---

## Phase 3: Message Delivery Guarantees (P1)

### 3.1 Add Retry Mechanism to Channel Manager

**File:** `xbot/channels/manager.py:184-187`

**Issue:** No retry on send failure, message loss

**Specification:**

```python
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds

async def _send_with_retry(channel: BaseChannel, payload: OutboundMessage) -> None:
    """Send message with retry on transient failures."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            await channel.send(payload)
            return
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "Channel %s send failed (attempt %d/%d), retrying in %ds: %s",
                    payload.channel, attempt + 1, MAX_RETRIES, delay, e
                )
                await asyncio.sleep(delay)
    
    # All retries exhausted
    logger.error(
        "Channel %s send failed after %d attempts, message lost: %s",
        payload.channel, MAX_RETRIES, last_error
    )
    # Optionally: store failed message for manual recovery
```

**Error message in logs:**
```
ERROR: Channel slack send failed after 3 attempts, message lost: ConnectionTimeout
```

---

### 3.2 Add Retry to Slack Channel

**File:** `xbot/channels/slack.py:119-157`

**Specification:**

Use the same `_send_with_retry` pattern from 3.1, with Slack-specific handling:

```python
# Handle Slack-specific errors:
# - 429 Rate Limit: respect Retry-After header
# - 500/502/503: retry with backoff
# - 401/403: do NOT retry (auth error)
```

---

### 3.3 Add Retry for Telegram Local File Uploads

**File:** `xbot/channels/telegram.py:400-406`

**Specification:**

Wrap existing `_call_with_retry` around local file uploads:

```python
async with open(media_path, "rb") as f:
    await _call_with_retry(
        sender,
        chat_id=chat_id,
        **{param: f},
        reply_parameters=reply_params,
        **thread_kwargs,
    )
```

---

### 3.4 Add Message Deduplication to Discord

**File:** `xbot/channels/discord.py:290-351`

**Specification:**

```python
# In __init__:
self._processed_message_ids: dict[str, float] = {}
self._message_dedup_ttl = 300  # 5 minutes
self._dedup_lock = asyncio.Lock()

async def _is_duplicate_message(self, message_id: str) -> bool:
    """Check if message was already processed."""
    async with self._dedup_lock:
        now = time.time()
        # Cleanup expired entries
        expired = [k for k, v in self._processed_message_ids.items() 
                   if now - v > self._message_dedup_ttl]
        for k in expired:
            del self._processed_message_ids[k]
        
        # Check if duplicate
        if message_id in self._processed_message_ids:
            return True
        self._processed_message_ids[message_id] = now
        return False
```

---

### 3.5 Fix Discord Heartbeat Failure Detection

**File:** `xbot/channels/discord.py:283-285`

**Specification:**

```python
# In __init__:
self._heartbeat_failed = asyncio.Event()

# In heartbeat loop:
async def heartbeat_loop():
    while self._running:
        try:
            # ... heartbeat logic ...
        except Exception as e:
            logger.warning("Discord heartbeat failed: %s", e)
            self._heartbeat_failed.set()  # Signal main loop
            break

# In main gateway loop:
async def gateway_loop():
    while self._running:
        # Check heartbeat health
        if self._heartbeat_failed.is_set():
            logger.warning("Discord heartbeat failed, triggering reconnect")
            self._heartbeat_failed.clear()
            await self._reconnect()
            continue
        
        # ... rest of gateway loop ...
```

---

### 3.6 Clean Up Old WebSocket Resources on Feishu Restart

**File:** `xbot/channels/feishu.py:260-272`

**Specification:**

```python
async def _restart_ws_worker(self):
    """Restart WebSocket worker with proper cleanup."""
    # 1. Signal old worker to stop
    if self._ws_stop_event:
        self._ws_stop_event.set()
    
    # 2. Drain old queue (process remaining messages)
    if self._ws_event_queue:
        while True:
            try:
                event = self._ws_event_queue.get_nowait()
                await self._dispatch_worker_event(event)
            except queue.Empty:
                break
    
    # 3. Create new resources
    self._ws_event_queue = queue.Queue()
    self._ws_stop_event = threading.Event()
    
    # 4. Start new worker
    self._start_ws_worker()
```

---

## Phase 4: Error Handling Improvements (P1)

### 4.1 Handle MCP Connection Exceptions Properly

**File:** `xbot/agent/tools/mcp.py:285-288`

**Specification:**

```python
results = await asyncio.gather(*tasks, return_exceptions=True)
connected_count = 0
failed_count = 0
failed_servers = []

for i, result in enumerate(results):
    server_name = list(mcp_servers.keys())[i]
    
    if isinstance(result, MCPServerConnection) and result.connected:
        connected_count += 1
        stack.push_async_callback(_safe_close_stack, result.stack, result.name)
    elif isinstance(result, Exception):
        failed_count += 1
        failed_servers.append((server_name, result))
        logger.error("MCP server '%s' connection failed: %s", server_name, result)
    else:
        failed_count += 1
        failed_servers.append((server_name, "Unknown result type"))
        logger.warning("MCP server '%s' returned unexpected result: %s", server_name, type(result))

if failed_count > 0:
    logger.warning("MCP: %d servers connected, %d servers failed: %s",
                   connected_count, failed_count, [s[0] for s in failed_servers])
```

---

### 4.2 Raise Exception Instead of String on MCP Timeout

**File:** `xbot/agent/tools/mcp.py:60-62`

**Specification:**

```python
class MCPToolTimeoutError(Exception):
    """Raised when an MCP tool call times out."""
    def __init__(self, tool_name: str, timeout: float):
        self.tool_name = tool_name
        self.timeout = timeout
        super().__init__(f"MCP tool '{tool_name}' timed out after {timeout}s")

# In execute:
except asyncio.TimeoutError:
    logger.warning("MCP tool '%s' timed out after %ss", self._name, self._tool_timeout)
    raise MCPToolTimeoutError(self._name, self._tool_timeout)
```

**User-facing message (caught by caller):**
```
MCP tool 'my_tool' timed out after 30s
```

---

### 4.3 Fix TOCTOU in Memory Path Resolution

**File:** `xbot/memory/integration/service.py:145-154`

**Specification:**

```python
def _resolve_path_or_name(self, target: str) -> Path | None:
    """Resolve path without TOCTOU vulnerability."""
    candidate = Path(target)
    
    # Try direct resolution first (no exists check)
    try:
        return self.store.resolve_managed_path(candidate)
    except ValueError:
        pass  # Not in managed directory, continue
    
    # Try relative to memory_dir (no exists check)
    relative = self.store.memory_dir / target
    try:
        return self.store.resolve_managed_path(relative)
    except ValueError:
        pass
    
    # Fallback: search by name
    normalized = target.strip().lower()
    for header in self.store.list_memories():
        if normalized in {
            (header.name or "").strip().lower(),
            Path(header.filename).stem.lower(),
            header.filename.lower(),
            header.file_path.relative_to(self.store.memory_dir).as_posix().lower(),
        }:
            return header.file_path
    
    return None
```

---

### 4.4 Handle Memory Index Rebuild Failure

**File:** `xbot/memory/memdir/store.py:31-37`

**Specification:**

```python
def create_memory(...) -> Path:
    self._validate_type(memory_type)
    folder = self.memory_dir / memory_type
    folder.mkdir(parents=True, exist_ok=True)
    filename = self._slugify(title) + ".md"
    path = folder / filename
    
    # Atomic write
    self._atomic_write(path, self._render_document(memory_type, title, description, body))
    
    # Rebuild index, rollback on failure
    try:
        self.rebuild_index()
    except Exception as e:
        logger.error("Failed to rebuild index after creating memory, removing orphan: %s", e)
        path.unlink(missing_ok=True)
        raise
    
    return path
```

---

## Phase 5: Low-Priority Fixes (P2)

### 5.1 Add Login Rate Limiting

**File:** `xbot/webui/app.py:318-325`

**Specification:**

```python
from collections import defaultdict
from datetime import datetime, timedelta

LOGIN_ATTEMPTS: dict[str, list[datetime]] = defaultdict(list)
MAX_ATTEMPTS_PER_IP = 5
ATTEMPT_WINDOW_SECONDS = 60

@app.post("/api/auth/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    
    # Check rate limit
    now = datetime.utcnow()
    attempts = LOGIN_ATTEMPTS[client_ip]
    attempts[:] = [a for a in attempts if now - a < timedelta(seconds=ATTEMPT_WINDOW_SECONDS)]
    
    if len(attempts) >= MAX_ATTEMPTS_PER_IP:
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Try again in {ATTEMPT_WINDOW_SECONDS} seconds."
        )
    
    LOGIN_ATTEMPTS[client_ip].append(now)
    
    # ... existing login logic ...
```

**Error response (HTTP 429):**
```json
{
  "detail": "Too many login attempts. Try again in 60 seconds."
}
```

---

### 5.2 Make JWT Secret Configurable

**File:** `xbot/webui/app.py:246`

**Specification:**

```python
import os

JWT_SECRET_FILE = Path("~/.xbot/webui/jwt_secret").expanduser()

def get_or_create_jwt_secret() -> str:
    """Get JWT secret from env, file, or generate new one."""
    # 1. Check environment variable (production)
    env_secret = os.environ.get("XBOT_JWT_SECRET")
    if env_secret:
        return env_secret
    
    # 2. Check file (persists across restarts)
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text().strip()
    
    # 3. Generate and persist
    secret = secrets.token_hex(32)
    JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    JWT_SECRET_FILE.write_text(secret)
    JWT_SECRET_FILE.chmod(0o600)
    return secret

app.state.auth = AuthManager(get_or_create_jwt_secret())
```

---

### 5.3 Add File Size Limit to Memory Scanner

**File:** `xbot/memory/memdir/scan.py:24`

**Specification:**

```python
MAX_MEMORY_FILE_BYTES = 1 * 1024 * 1024  # 1 MB

def scan_memory_files(memory_dir: Path, limit: int | None = MAX_MEMORY_FILES) -> list[MemoryHeader]:
    # ... existing code ...
    
    try:
        file_size = path.stat().st_size
        if file_size > MAX_MEMORY_FILE_BYTES:
            logger.warning("Skipping oversized memory file (%d bytes): %s", file_size, path)
            continue
        content = path.read_text(encoding="utf-8")
        # ... rest of parsing ...
```

---

### 5.4 Add Filename Length Validation

**File:** `xbot/memory/memdir/store.py:33`

**Specification:**

```python
MAX_FILENAME_LENGTH = 200

def _slugify(self, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug or "memory"
    
    # Truncate to max length
    if len(slug) > MAX_FILENAME_LENGTH:
        slug = slug[:MAX_FILENAME_LENGTH].rstrip("-")
    
    # Handle collisions: append counter
    base_slug = slug
    counter = 1
    while (self.memory_dir / self.memory_type / f"{slug}.md").exists():
        suffix = f"-{counter}"
        slug = base_slug[:MAX_FILENAME_LENGTH - len(suffix)] + suffix
        counter += 1
    
    return slug
```

---

### 5.5 Add Exponential Backoff to Discord Reconnect

**File:** `xbot/channels/discord.py:79-81`

**Specification:**

```python
MAX_RECONNECT_DELAY = 60  # seconds
INITIAL_RECONNECT_DELAY = 1

# In gateway loop:
reconnect_attempts = 0
while self._running:
    try:
        # ... gateway logic ...
    except Exception as e:
        logger.warning("Discord gateway error: %s", e)
        if self._running:
            # Exponential backoff: 1, 2, 4, 8, 16, 32, 60, 60, ...
            delay = min(INITIAL_RECONNECT_DELAY * (2 ** reconnect_attempts), MAX_RECONNECT_DELAY)
            reconnect_attempts += 1
            logger.info("Reconnecting to Discord gateway in %ds (attempt %d)...", delay, reconnect_attempts)
            await asyncio.sleep(delay)
```

---

### 5.6 Improve Feishu Dedup Cleanup Performance

**File:** `xbot/channels/feishu.py:1066-1085`

**Specification:**

```python
# Run cleanup every N messages instead of every message
DEDUP_CLEANUP_INTERVAL = 100
_message_counter = 0

# In _on_message:
global _message_counter
_message_counter += 1

if _message_counter % DEDUP_CLEANUP_INTERVAL == 0:
    await self._run_with_dedup_lock(_cleanup_dedup_state)
```

---

### 5.7 Log Telegram Draft Simulation Errors

**File:** `xbot/channels/telegram.py:492-493`

**Specification:**

```python
try:
    await self._app.bot.send_message_draft(
        chat_id=chat_id, draft_id=draft_id, text=text,
    )
    await asyncio.sleep(0.15)
except Exception as e:
    logger.debug("Telegram draft simulation failed (non-critical): %s", e)
await self._send_text(chat_id, text, reply_params, thread_kwargs)
```

---

### 5.8 Fix Slack Reaction Race Condition

**File:** `xbot/channels/slack.py:252-272`

**Specification:**

```python
async def _update_react_emoji(self, chat_id: str, ts: str | None) -> None:
    """Update reaction emoji with retry for race conditions."""
    if not self._web_client or not ts:
        return
    
    # Remove in-progress emoji (with retry)
    for attempt in range(3):
        try:
            await self._web_client.reactions_remove(
                channel=chat_id,
                name=self.config.react_emoji,
                timestamp=ts,
            )
            break
        except Exception as e:
            if "no_reaction" in str(e).lower():
                break  # Already removed, that's fine
            if attempt < 2:
                await asyncio.sleep(0.1)  # Small delay before retry
            else:
                logger.debug("Slack reactions_remove failed after retries: %s", e)
    
    # Add done emoji if configured
    if self.config.done_emoji:
        try:
            await self._web_client.reactions_add(
                channel=chat_id,
                name=self.config.done_emoji,
                timestamp=ts,
            )
        except Exception as e:
            logger.debug("Slack done reaction failed: %s", e)
```

---

### 5.9 Use Structured Logging in Config Loader

**File:** `xbot/config/loader.py:73,88`

**Specification:**

```python
import logging
logger = logging.getLogger(__name__)

# Replace print() with logger:
logger.warning("Failed to load config from %s: %s", path, e)
logger.warning("Using default configuration.")

# And:
logger.warning("Invalid configuration: %s", e)
logger.warning("Using default configuration.")
```

---

## Testing Strategy

### Unit Tests
- **Path validation:** Test with workspace paths, outside paths, symlinks, unicode
- **Password flow:** Test first-run, subsequent-run, reset, file corruption
- **Retry logic:** Test max retries, exponential backoff, different error types
- **Dedup logic:** Test cache overflow, TTL boundary, concurrent access
- **Lock patterns:** Test concurrent access with asyncio.gather

### Integration Tests
- Channel message delivery with network simulation
- WebSocket reconnection scenarios
- Memory store operations

### Security Tests
```python
# Path traversal tests
def test_path_traversal_attacks():
    assert validate_path_in_workspace(Path("/etc/passwd"), workspace) == False
    assert validate_path_in_workspace(Path("~/.ssh/id_rsa"), workspace) == False
    assert validate_path_in_workspace(Path("../other/file.txt"), workspace) == False
    # Symlink attack
    (workspace / "link").symlink_to("/etc/passwd")
    assert validate_path_in_workspace(workspace / "link", workspace) == False

# Skill name validation tests
def test_skill_name_attacks():
    with pytest.raises(ValueError):
        validate_skill_name("../../../etc/passwd")
    with pytest.raises(ValueError):
        validate_skill_name("..%2fpasswd")  # URL encoded
    with pytest.raises(ValueError):
        validate_skill_name("..／passwd")  # Unicode slash
```

---

## Implementation Order (Revised)

1. **Week 1:** P0 Security fixes with explicit specs + password flow
2. **Week 2:** P1 Concurrency fixes + add basic observability
3. **Week 3:** P1 Messaging fixes + integration tests
4. **Week 4:** P2 Polish + comprehensive test coverage

**Estimated Total:** 10-15 days (added buffer for testing)

---

## Success Criteria

- [ ] All P0 security issues resolved with explicit specifications
- [ ] Password flow tested end-to-end (first-run, reset, change)
- [ ] Path validation passes all security tests (symlink, unicode, encoding)
- [ ] No race conditions in concurrent tests
- [ ] Message delivery succeeds after transient failures
- [ ] No memory leaks in long-running tests
- [ ] All existing tests pass
- [ ] New test coverage for all fixed issues

---