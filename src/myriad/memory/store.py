"""Memory records, the store contract and the SQLite adapter (stdlib sqlite3)."""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

MemoryKind = Literal["fact", "decision", "episode", "preference"]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_subject(text: str) -> str:
    """Literal task key: lowercase, collapsed whitespace, trailing .!? removed.

    Every other character is kept on purpose, so "C++" and "C#" stay different.
    """
    return re.sub(r"\s+", " ", text).strip().rstrip(".!?").strip().lower()


@dataclass(frozen=True)
class MemoryRecord:
    namespace: str
    agent_id: str
    source_task_id: str
    kind: MemoryKind
    content: str
    subject: str = ""  # normalized request; empty for notes without a task key
    reusable: bool = False  # set only by the engine for automatically stored results
    expires_at: datetime | None = None
    supersedes: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=_utc_now)


class MemoryStore(Protocol):
    def write(self, record: MemoryRecord) -> MemoryRecord: ...

    def search(self, namespace: str, query: str, limit: int = 3) -> list[MemoryRecord]: ...

    def find_reusable(self, namespace: str, subject: str) -> MemoryRecord | None: ...

    def close(self) -> None: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    subject TEXT NOT NULL,
    reusable INTEGER NOT NULL,
    supersedes TEXT,
    superseded_by TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS memory_lookup ON memory (namespace, kind, subject);
"""

_ACTIVE = "superseded_by IS NULL AND (expires_at IS NULL OR expires_at > ?)"


class SQLiteMemoryStore:
    """One table. Expired and superseded records are invisible to every read."""

    def __init__(self, path: str | Path, clock: Clock = _utc_now) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        # SQLite's lower() only folds ASCII; use Python's Unicode case folding instead.
        self._db.create_function("fold", 1, lambda s: s.casefold() if s else s, deterministic=True)
        self._db.executescript(_SCHEMA)
        self._now = clock

    def close(self) -> None:
        self._db.close()

    def write(self, record: MemoryRecord) -> MemoryRecord:
        """Insert a record. With a subject it replaces the active record that has the same
        namespace, kind and subject - check and replacement run in one transaction."""
        with self._db:  # commits on success, rolls back on any error
            previous = None
            if record.subject:
                row = self._db.execute(
                    "SELECT id FROM memory WHERE namespace = ? AND kind = ? AND subject = ? "
                    "AND superseded_by IS NULL",
                    (record.namespace, record.kind, record.subject),
                ).fetchone()
                previous = row["id"] if row else None
            stored = replace(record, supersedes=previous)
            self._db.execute(
                "INSERT INTO memory (id, namespace, agent_id, source_task_id, kind, content, "
                "subject, reusable, supersedes, superseded_by, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    stored.id, stored.namespace, stored.agent_id, stored.source_task_id,
                    stored.kind, stored.content, stored.subject, int(stored.reusable),
                    stored.supersedes, stored.created_at.isoformat(),
                    stored.expires_at.isoformat() if stored.expires_at else None,
                ),
            )
            if previous:
                self._db.execute(
                    "UPDATE memory SET superseded_by = ? WHERE id = ?", (stored.id, previous)
                )
        return stored

    def search(self, namespace: str, query: str, limit: int = 3) -> list[MemoryRecord]:
        """Literal, Unicode case-insensitive match in content or subject (no wildcards, no vectors)."""
        needle = query.strip().casefold()
        rows = self._db.execute(
            f"SELECT * FROM memory WHERE namespace = ? AND {_ACTIVE} "
            "AND (instr(fold(content), ?) > 0 OR instr(fold(subject), ?) > 0) "
            "ORDER BY created_at DESC LIMIT ?",
            (namespace, self._now().isoformat(), needle, needle, limit),
        ).fetchall()
        return [self._record(row) for row in rows]

    def find_reusable(self, namespace: str, subject: str) -> MemoryRecord | None:
        row = self._db.execute(
            f"SELECT * FROM memory WHERE namespace = ? AND subject = ? AND reusable = 1 "
            f"AND {_ACTIVE} ORDER BY created_at DESC LIMIT 1",
            (namespace, subject, self._now().isoformat()),
        ).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            namespace=row["namespace"],
            agent_id=row["agent_id"],
            source_task_id=row["source_task_id"],
            kind=row["kind"],
            content=row["content"],
            subject=row["subject"],
            reusable=bool(row["reusable"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            supersedes=row["supersedes"],
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
