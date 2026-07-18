# xbot Bug Patterns Checklist

Historical bug patterns from prior xbot reviews. Scanners target these
categories; agents deep-diving should keep this checklist in hand.

1. **async 阻塞** — async function containing sync network/IO calls
   (e.g. `requests.get`, `time.sleep`, blocking file reads inside `async def`).

2. **私有 API 访问** — access to stdlib private attributes
   (e.g. `Condition._waiters`, `Thread._bootstrap_inner`).

3. **fail-open 权限** — unknown names admitted without rejection
   (auth/allowlist logic that defaults to permit on miss).

4. **后台任务 GC** — `ensure_future`/`create_task` result not assigned,
   leaving the task eligible for garbage collection before completion.

5. **SSRF 未校验** — user-controlled input flowing into outbound request URL
   without scheme/host validation.

6. **重试无 jitter** — fixed `time.sleep` / `asyncio.sleep` in retry loops
   without randomized backoff, causing thundering-herd retries.

7. **可变默认参数** — `[]`, `{}`, or `set()` used as function default
   arguments (shared across calls).

8. **死代码/残留文件** — unused imports, dead functions, orphaned modules
   left over from refactors.

9. **命名遗留** — references to the old `Nanobot` name in code, docs, or
   config keys that should have been renamed to `xbot`.

10. **session_id 不一致** — `session_id` mismatch across code paths
    (handler vs. client_pool vs. context), leading to cross-session leakage
    or stale state.

11. **property 副作用** — `@property` getter that mutates state or triggers
    IO, violating the expectation that property access is pure.
