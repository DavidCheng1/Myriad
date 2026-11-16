"""Artifacts: structured work results (code changes, test reports) stored by id.

Agents pass the id and a short summary instead of the full content - the shared workspace that
keeps prompts small. The user reviews, applies or discards a code change by its id.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ArtifactStatus = Literal["pending", "applied", "discarded"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    root_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    diff_stat TEXT NOT NULL,
    diff TEXT NOT NULL,
    tests_passed INTEGER,
    test_output TEXT NOT NULL,
    worktree TEXT NOT NULL,
    branch TEXT NOT NULL,
    base TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Artifact:
    root_id: str
    task_id: str
    title: str
    summary: str
    diff_stat: str
    diff: str
    tests_passed: bool | None
    test_output: str
    worktree: str
    branch: str
    base: str
    kind: str = "code_change"
    status: ArtifactStatus = "pending"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SQLiteArtifactStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def add(self, artifact: Artifact) -> Artifact:
        with self._db:
            self._db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.id, artifact.root_id, artifact.task_id, artifact.kind, artifact.status,
                    artifact.title, artifact.summary, artifact.diff_stat, artifact.diff,
                    None if artifact.tests_passed is None else int(artifact.tests_passed),
                    artifact.test_output, artifact.worktree, artifact.branch, artifact.base,
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        row = self._db.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._artifact(row) if row else None

    def list(self, status: ArtifactStatus | None = None) -> list[Artifact]:
        if status is None:
            rows = self._db.execute("SELECT * FROM artifacts ORDER BY created_at").fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM artifacts WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        return [self._artifact(row) for row in rows]

    def set_status(self, artifact_id: str, status: ArtifactStatus) -> None:
        with self._db:
            self._db.execute("UPDATE artifacts SET status = ? WHERE id = ?", (status, artifact_id))

    @staticmethod
    def _artifact(row: sqlite3.Row) -> Artifact:
        passed = row["tests_passed"]
        return Artifact(
            root_id=row["root_id"], task_id=row["task_id"], title=row["title"], summary=row["summary"],
            diff_stat=row["diff_stat"], diff=row["diff"],
            tests_passed=None if passed is None else bool(passed),
            test_output=row["test_output"], worktree=row["worktree"], branch=row["branch"],
            base=row["base"], kind=row["kind"], status=row["status"], id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
