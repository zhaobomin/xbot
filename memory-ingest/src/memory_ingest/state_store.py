from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .models import CandidateMemory, ScannedFile


class StateStore:
    def __init__(self, sqlite_path: str) -> None:
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scanned_files (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                last_scanned_at TEXT NOT NULL,
                last_import_status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS imported_memories (
                fingerprint TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                memory_text TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                remote_result_id TEXT
            );
            """
        )
        self.conn.commit()

    def should_process(self, scanned_file: ScannedFile) -> bool:
        row = self.conn.execute(
            "SELECT content_hash FROM scanned_files WHERE path = ?",
            (scanned_file.path,),
        ).fetchone()
        return row is None or row["content_hash"] != scanned_file.content_hash

    def mark_scanned(self, scanned_file: ScannedFile, status: str) -> None:
        self.conn.execute(
            """
            INSERT INTO scanned_files(path, content_hash, last_scanned_at, last_import_status)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                content_hash=excluded.content_hash,
                last_scanned_at=excluded.last_scanned_at,
                last_import_status=excluded.last_import_status
            """,
            (
                scanned_file.path,
                scanned_file.content_hash,
                datetime.now(timezone.utc).isoformat(),
                status,
            ),
        )
        self.conn.commit()

    def has_fingerprint(self, fingerprint: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM imported_memories WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        return row is not None

    def record_import(self, candidate: CandidateMemory, remote_result_id: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO imported_memories(
                fingerprint, source_path, memory_text, imported_at, remote_result_id
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                candidate.fingerprint,
                candidate.source_path,
                candidate.memory_text,
                datetime.now(timezone.utc).isoformat(),
                remote_result_id,
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
