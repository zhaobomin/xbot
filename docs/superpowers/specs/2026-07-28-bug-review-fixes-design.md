# Bug Review Fixes Design

## Goal

Fix the reproducible defects from `docs/bug-review-2026-07-28.md` with the
smallest behavior changes possible. Do not refactor subsystem architecture,
rename unrelated APIs, or introduce generalized frameworks.

## Fix Scope

### Gateway and security

- Bind top-level static-file routes with a zero-argument handler so request
  parameters cannot override the filesystem path.
- Treat IPv4-mapped IPv6 addresses as their mapped IPv4 address during SSRF
  validation.
- Persist message revocation through a focused `ConversationStore` deletion
  operation, decrementing `last_consolidated` only when a deleted index was
  already consolidated.
- Make the skill toggle endpoint change real persisted skill state and make
  skill discovery/loading honor that state.
- Reject bcrypt inputs longer than 72 UTF-8 bytes with a client error. Do not
  truncate passwords.
- Exclude the WebUI S3 credential file from workspace exports.

### Configuration and utility correctness

- Merge split provider credentials into an existing provider mapping instead
  of replacing unrelated fields.
- Stop the legacy `ProvidersConfig.custom` getter from mutating configuration
  during reads; callers that intentionally create the compatibility entry must
  do so explicitly.
- Recognize MP3 files with an ID3 header.
- Write the main configuration through a same-directory temporary file and
  atomic replacement while retaining current permissions.
- Reject unsafe media namespace components before creating directories.
- Format memory messages safely when `timestamp` is missing or `None`.

### Permission and orchestration correctness

- Remove ambiguous `ok` and `确认` responses from high-risk permission
  authorization keywords while retaining explicit allow terms.
- Preserve fallback planner entries containing colons and log manager-proposed
  task names that do not exist.
- Keep the current state-machine rejection, message-bus lock, and client-pool
  disconnect semantics unchanged; only correct inaccurate documentation where
  needed.

### WhatsApp bridge

- Reconnect only for retryable Baileys disconnect reasons and never reconnect
  after an explicit `disconnect()`.
- Cancel reconnect timers and dispose of the current socket when replacing or
  disconnecting it, using existing Baileys cleanup behavior.
- Isolate each inbound message in its own error boundary so one malformed
  message cannot stop processing the remainder of a batch. Media download
  remains sequential; no concurrency refactor is included.
- Validate incoming send-command fields before calling Baileys.
- Add a bounded shutdown fallback so an unresponsive WebSocket client cannot
  block bridge shutdown indefinitely.
- Generate Telegram draft IDs with per-instance uniqueness rather than only a
  millisecond timestamp.

## Explicit Non-Goals

- No Gateway, configuration, session-store, or bridge architecture refactor.
- No new generic lifecycle manager, persistence layer, or protocol version.
- No change for the alleged normal-reconnect listener leak: Baileys already
  destroys the closed socket event buffer.
- No change to `MessageBus.clear_session_requests()` based on the reported
  uncontended cross-loop sequence, which does not reproduce.
- No request-ID protocol expansion: the current Python sender does not consume
  send acknowledgements, so this would be a feature rather than a bug fix.
- No changes solely to silence warnings about intentional APIs such as
  state-transition methods returning `False`.

## Test Strategy

Use red-green TDD in small groups:

1. Add focused Python regression tests and verify each fails for the expected
   behavior before changing production code.
2. Apply the smallest Python implementation changes and rerun the focused
   tests.
3. Add bridge tests for disconnect classification, manual disconnect, batch
   isolation, command validation, and bounded stop; verify failure before
   implementation.
4. Run the relevant suites after each group.
5. Finish with the full Python test suite, Ruff, bridge tests and TypeScript
   build, WebUI tests/build, and the xbot review orchestrator.

## Success Criteria

- Every in-scope defect has a regression test that was observed failing before
  its fix and passing afterward.
- Existing public behavior changes only where described above.
- Full verification commands exit successfully.
- No unrelated tracked or untracked user files are modified or committed.
