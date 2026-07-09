"""Session management for conversation history."""

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from xbot.platform.config.paths import get_legacy_sessions_dir
from xbot.platform.logging.core import get_logger
from xbot.platform.utils.helpers import ensure_dir, safe_filename

logger = get_logger(__name__)
try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None


@dataclass
class ConversationSession:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.

    ## Design Note: Dual History Management

    xbot and Claude SDK maintain separate history management systems:

    - **xbot Session.messages**: Used for memory consolidation (MEMORY.md/HISTORY.md)
      and token estimation to trigger consolidation. The `last_consolidated` offset
      tracks which messages have been archived.

    - **Claude SDK session**: Manages its own conversation history via the `resume`
      parameter in ClaudeAgentOptions. SDK uses `sdk_session_id` to restore context.

    These two systems are intentionally independent:
    - xbot consolidation → persistent long-term memory (survives restarts)
    - SDK history → context window management (temporary, per-session)

    This separation allows xbot to maintain searchable memory archives while
    letting the SDK handle context window optimization independently.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files
    _new_messages: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _metadata_dirty: bool = field(default=False, repr=False)  # True when metadata changed but not yet flushed

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self._new_messages.append(msg)
        self.updated_at = datetime.now()

    def mark_metadata_dirty(self) -> None:
        """Mark metadata as changed so the next save() triggers a full write."""
        self._metadata_dirty = True

    @staticmethod
    def _find_legal_start(messages: list[dict[str, Any]]) -> int:
        """Find first index where every tool result has a matching assistant tool_call.

        Deprecated: kept for backward compatibility.  New callers should prefer
        ``_filter_orphan_tool_results`` which removes *only* orphan tool results
        instead of discarding every message that follows an orphan.
        """
        declared_at: dict[str, int] = {}
        start = 0

        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared_at[str(tc["id"])] = i
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid and declared_at.get(str(tid), -1) < start:
                    start = i + 1

        return start

    @staticmethod
    def _filter_orphan_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove orphan tool results whose matching assistant tool_call is absent.

        Unlike ``_find_legal_start`` which advances a start pointer (discarding
        all subsequent valid messages), this filter removes *only* the orphan
        tool result messages and keeps everything else intact.
        """
        declared_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared_ids.add(str(tc["id"]))

        return [
            msg for msg in messages
            if not (
                msg.get("role") == "tool"
                and msg.get("tool_call_id")
                and str(msg["tool_call_id"]) not in declared_ids
            )
        ]

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a legal tool-call boundary."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # Drop leading non-user messages to avoid starting mid-turn when possible.
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        # Remove orphan tool results whose matching assistant tool_call fell
        # outside the fixed-size history window.  This is safer than the old
        # ``_find_legal_start`` approach which discarded *all* messages after
        # the first orphan, potentially losing valid user/assistant turns.
        sliced = self._filter_orphan_tool_results(sliced)

        out: list[dict[str, Any]] = []
        for message in sliced:
            entry: dict[str, Any] = {"role": message["role"], "content": message.get("content", "")}
            for key in ("tool_calls", "tool_call_id", "name"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self._new_messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()
        # Mark dirty so the next save() triggers a full rewrite, overwriting
        # the on-disk content with the now-empty message list.
        self._metadata_dirty = True


class ConversationStore:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path, max_cache_size: int = 500):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self._cache: dict[str, ConversationSession] = {}
        self._max_cache_size = max_cache_size

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        return self.sessions_dir / self._hashed_session_filename(key)

    def _get_legacy_session_path(self, key: str) -> Path:
        """Global session path (~/.xbot/sessions/)."""
        return self.legacy_sessions_dir / self._hashed_session_filename(key)

    @staticmethod
    def _hashed_session_filename(key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{digest}.jsonl"

    @staticmethod
    def _safe_session_filename(key: str) -> str:
        safe_key = safe_filename(key.replace(":", "_"))
        return f"{safe_key}.jsonl"

    @staticmethod
    def _message_preview(content: Any, *, max_chars: int = 80) -> str:
        if not isinstance(content, str):
            return ""
        preview = " ".join(content.strip().split())
        if len(preview) <= max_chars:
            return preview
        return f"{preview[:max_chars].rstrip()}..."

    def _get_old_session_path(self, key: str) -> Path:
        return self.sessions_dir / self._safe_session_filename(key)

    def _get_old_legacy_session_path(self, key: str) -> Path:
        return self.legacy_sessions_dir / self._safe_session_filename(key)

    def _session_paths_for_read(self, key: str) -> list[Path]:
        paths = [
            self._get_session_path(key),
            self._get_old_session_path(key),
            self._get_legacy_session_path(key),
            self._get_old_legacy_session_path(key),
        ]
        # Backward compatibility: sessions written before the `im:` namespace
        # prefix were stored under the bare `{channel}:{chat_id}` key. Fall
        # back to those paths so upgraded IM users keep their conversation
        # history instead of starting an empty session.
        if key.startswith("im:"):
            legacy_key = key[3:]
            paths.extend([
                self._get_session_path(legacy_key),
                self._get_old_session_path(legacy_key),
                self._get_legacy_session_path(legacy_key),
                self._get_old_legacy_session_path(legacy_key),
            ])
        return paths

    def _session_paths_for_delete(self, key: str) -> list[Path]:
        seen: set[Path] = set()
        paths: list[Path] = []
        for path in self._session_paths_for_read(key):
            if path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    @contextmanager
    def _file_lock(self, path: Path, exclusive: bool = True) -> Generator[None, None, None]:
        """Acquire a file lock for safe concurrent access.

        Args:
            path: Path to the file (lock file will be path + '.lock')
            exclusive: True for exclusive (write) lock, False for shared (read) lock

        Yields:
            None when lock is acquired
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_fd = None
        try:
            # Ensure parent directory exists
            lock_path.parent.mkdir(parents=True, exist_ok=True)

            # Open/create lock file
            lock_fd = open(lock_path, "w")

            if fcntl is not None:
                lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(lock_fd.fileno(), lock_type)

            yield
        finally:
            if lock_fd is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    logger.debug("Error releasing file lock", exc_info=True)

    def get(self, key: str) -> ConversationSession | None:
        """
        Get an existing session without creating a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session if it exists, None otherwise.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is not None:
            self._cache[key] = session
            self._evict_if_needed()
        return session

    def get_or_create(self, key: str) -> ConversationSession:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = ConversationSession(key=key)

        self._cache[key] = session
        self._evict_if_needed()
        return session

    def _load(self, key: str) -> ConversationSession | None:
        """Load a session from disk."""
        path = next((candidate for candidate in self._session_paths_for_read(key) if candidate.exists()), None)
        if path is None:
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0
            saw_valid_row = False

            # Use shared lock for reading to prevent reading partial writes
            with self._file_lock(path, exclusive=False):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError as e:
                            logger.warning(
                                "Skipping corrupt JSONL row in session %s: %s",
                                key,
                                e,
                            )
                            continue
                        saw_valid_row = True

                        if data.get("_type") == "metadata":
                            metadata = data.get("metadata", {})
                            created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                            last_consolidated = data.get("last_consolidated", 0)
                        else:
                            messages.append(data)

            if not saw_valid_row:
                return None

            return ConversationSession(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session %s: %s", key, e)
            return None

    def save(self, session: ConversationSession) -> None:
        """Save a session to disk.

        Uses append-only mode for new messages to improve performance.
        Falls back to atomic full write if:
        - the file doesn't exist yet (first save), or
        - metadata was changed (``session.mark_metadata_dirty()`` was called).
        """
        path = self._get_session_path(session.key)

        # Use exclusive lock for writing to prevent concurrent writes
        with self._file_lock(path, exclusive=True):
            needs_full_save = not path.exists() or session._metadata_dirty

            if needs_full_save:
                # Full atomic rewrite: covers initial creation and metadata updates
                self._save_full(session, path)
                session._new_messages.clear()
                session._metadata_dirty = False
            elif session._new_messages:
                try:
                    # Append mode: just write new messages
                    with open(path, "a", encoding="utf-8") as f:
                        for msg in session._new_messages:
                            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                    session._new_messages.clear()
                except Exception:
                    # Fallback to full save if append fails
                    self._save_full(session, path)
                    session._new_messages.clear()
            # else: no new messages and no metadata change — nothing to persist

        self._cache[session.key] = session
        self._evict_if_needed()

    def _save_full(self, session: ConversationSession, path: Path) -> None:
        """Perform an atomic full write of the session file."""
        tmp_path = path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated
                }
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

            os.replace(str(tmp_path), str(path))
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def _evict_if_needed(self) -> None:
        """Evict oldest cache entries when capacity is exceeded.

        Uses ``updated_at`` as a proxy for age: the sessions that haven't been
        updated recently are removed first.  Dirty (unsaved) sessions are never
        evicted to avoid data loss.
        """
        overflow = len(self._cache) - self._max_cache_size
        if overflow <= 0:
            return
        # Sort by updated_at ascending; skip dirty sessions.
        candidates = [
            (key, sess)
            for key, sess in self._cache.items()
            if not sess._metadata_dirty and not sess._new_messages
        ]
        if not candidates:
            return
        candidates.sort(key=lambda item: item[1].updated_at)
        for key, _ in candidates[:overflow]:
            self._cache.pop(key, None)

    def delete(self, key: str) -> bool:
        """Delete a session file and remove it from in-memory cache."""
        self.invalidate(key)
        paths = self._session_paths_for_delete(key)
        lock_paths = [path.with_suffix(path.suffix + ".lock") for path in paths]

        if not any(path.exists() for path in paths) and not any(path.exists() for path in lock_paths):
            return False

        for path in paths:
            with self._file_lock(path, exclusive=True):
                path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".lock").unlink(missing_ok=True)
        return True

    def compact(self, session: ConversationSession) -> None:
        """Compact a session file by rewriting it to remove duplicates or old state.

        This method rewrites the entire session file, which can help reduce file size
        and improve load times by removing any redundant data.

        Args:
            session: The session to compact.
        """
        path = self._get_session_path(session.key)

        # Use exclusive lock for writing
        with self._file_lock(path, exclusive=True):
            # Rewrite the entire file using _save_full
            self._save_full(session, path)
            # Clear new messages since we've rewritten everything
            session._new_messages.clear()
            session._metadata_dirty = False

        # Update the cache
        self._cache[session.key] = session

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        from xbot.platform.bus.events import parse_session_key

        sessions_by_key: dict[str, dict[str, Any]] = {}

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                first_message = ""
                last_message = ""
                last_message_at = ""
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    msg = json.loads(line)
                                except json.JSONDecodeError:
                                    logger.warning("Skipping corrupt JSONL row in %s: %s", path, line[:100])
                                    continue
                                if msg.get("_type") == "metadata":
                                    continue
                                content = self._message_preview(msg.get("content"))
                                if not content:
                                    continue
                                if msg.get("role") == "user" and not first_message:
                                    first_message = content
                                last_message = content
                                if isinstance(msg.get("timestamp"), str):
                                    last_message_at = msg["timestamp"]
                            item = {
                                "key": key,
                                "channel": parse_session_key(key)[0],
                                "first_message": first_message,
                                "last_message": last_message,
                                "created_at": data.get("created_at"),
                                "updated_at": last_message_at or data.get("updated_at"),
                                "path": str(path)
                            }
                            current = sessions_by_key.get(key)
                            if current is None or self._prefer_session_list_item(item, current):
                                sessions_by_key[key] = item
            except Exception:
                continue

        return sorted(sessions_by_key.values(), key=lambda x: x.get("updated_at", ""), reverse=True)

    def _prefer_session_list_item(self, candidate: dict[str, Any], current: dict[str, Any]) -> bool:
        """Return True when candidate is the better list representative for the same key."""
        candidate_updated = str(candidate.get("updated_at") or "")
        current_updated = str(current.get("updated_at") or "")
        if candidate_updated != current_updated:
            return candidate_updated > current_updated

        key = str(candidate.get("key") or "")
        canonical_path = str(self._get_session_path(key))
        if candidate.get("path") == canonical_path and current.get("path") != canonical_path:
            return True
        return False
