"""Coding service behind Forge: worktree -> coding agent -> commit -> test gate -> artifact.

Nothing reaches the user's checkout without an explicit /apply: the change lives on its own
branch in its own worktree until it is applied or discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from myriad.artifacts import Artifact, SQLiteArtifactStore
from myriad.executors.base import CodingExecutor
from myriad.executors.workspace import GitWorkspace, WorkspaceError
from myriad.models import FailureReason, Task

SUMMARY_CHARS = 600  # what Atlas sees of the coding agent's report; the full change stays in the artifact


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True)
class CodingOutcome:
    ok: bool
    text: str  # compact result for the delegating agent
    artifact_id: str | None = None
    reason: FailureReason | None = None


class CodingService:
    def __init__(
        self,
        executor: CodingExecutor,
        workspace: GitWorkspace,
        artifacts: SQLiteArtifactStore,
        test_command: list[str] | None = None,
    ) -> None:
        self.executor = executor
        self.workspace = workspace
        self.artifacts = artifacts
        self.test_command = test_command or None

    @property
    def repo(self) -> Path:
        return self.workspace.repo

    def run(self, task: Task, brief: str, instructions: str) -> CodingOutcome:
        try:
            worktree = self.workspace.create(task.id)
        except WorkspaceError as exc:
            return CodingOutcome(False, f"Workspace error: {exc}", reason=FailureReason.EXECUTOR_ERROR)

        result = self.executor.run(brief, worktree.path, instructions)
        if not result.ok:
            self._cleanup(worktree.path, worktree.branch)
            return CodingOutcome(False, f"The coding agent failed: {_clip(result.error or '', 300)}",
                                 reason=FailureReason.EXECUTOR_ERROR)
        try:
            diff, stat = self.workspace.finalize(worktree, f"Forge: {_clip(task.goal, 60)}")
        except WorkspaceError as exc:
            self._cleanup(worktree.path, worktree.branch)
            return CodingOutcome(False, f"Workspace error: {exc}", reason=FailureReason.EXECUTOR_ERROR)
        if not stat:
            self._cleanup(worktree.path, worktree.branch)
            return CodingOutcome(True, f"No code changes were needed. {_clip(result.summary, SUMMARY_CHARS)}")

        report = self.workspace.run_tests(worktree.path, self.test_command) if self.test_command else None
        artifact = self.artifacts.add(Artifact(
            root_id=task.root_id, task_id=task.id, title=_clip(task.goal, 80), summary=result.summary,
            diff_stat=stat, diff=diff, tests_passed=None if report is None else report.passed,
            test_output="" if report is None else report.output,
            worktree=str(worktree.path), branch=worktree.branch, base=worktree.base,
        ))
        tests = "not run" if report is None else ("passed" if report.passed else "FAILED")
        files = stat.splitlines()[-1].strip()
        text = (
            f"Code change {artifact.id} is ready for review (tests {tests}; {files}). "
            f"Summary: {_clip(result.summary, SUMMARY_CHARS)} "
            f"The user can inspect it with /review {artifact.id} and apply it with /apply {artifact.id}."
        )
        passed = report is None or report.passed
        return CodingOutcome(passed, text, artifact.id, None if passed else FailureReason.TESTS_FAILED)

    # --- the user's decisions ----------------------------------------------

    def pending(self) -> list[Artifact]:
        return self.artifacts.list("pending")

    def apply(self, artifact_id: str, force: bool = False) -> str:
        artifact = self._pending(artifact_id)
        if artifact.tests_passed is False and not force:
            raise WorkspaceError(f"tests failed for {artifact_id}; use /apply {artifact_id} force to apply anyway")
        self.workspace.apply(artifact.branch)
        self._cleanup(Path(artifact.worktree), artifact.branch)
        self.artifacts.set_status(artifact_id, "applied")
        return f"Change {artifact_id} applied to {self.repo}."

    def discard(self, artifact_id: str) -> str:
        artifact = self._pending(artifact_id)
        self._cleanup(Path(artifact.worktree), artifact.branch)
        self.artifacts.set_status(artifact_id, "discarded")
        return f"Change {artifact_id} discarded."

    def _pending(self, artifact_id: str) -> Artifact:
        artifact = self.artifacts.get(artifact_id)
        if artifact is None:
            raise WorkspaceError(f"no change with id {artifact_id}")
        if artifact.status != "pending":
            raise WorkspaceError(f"change {artifact_id} is already {artifact.status}")
        return artifact

    def _cleanup(self, path: Path, branch: str) -> None:
        try:
            self.workspace.discard(path, branch)
        except WorkspaceError:
            pass  # best effort: a leftover worktree is harmless and visible in `git worktree list`
