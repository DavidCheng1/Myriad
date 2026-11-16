"""Isolated workspaces: one Git worktree and branch per coding task; the user's checkout is never touched."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

FORGE_IDENTITY = ("-c", "user.name=MYRIAD Forge", "-c", "user.email=forge@myriad.local")


class WorkspaceError(Exception):
    """A Git operation on the target repository or a worktree failed."""


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base: str  # commit the worktree started from


@dataclass(frozen=True)
class TestReport:
    passed: bool
    output: str  # tail of the test run's output


class GitWorkspace:
    def __init__(self, repo: Path, root: Path, git: str = "git") -> None:
        self.repo = Path(repo)
        self.root = Path(root)  # where worktrees are created, outside the target repository
        self.git = git

    def _run(self, *args: str, cwd: Path | None = None) -> str:
        try:
            process = subprocess.run(
                [self.git, *args], cwd=cwd or self.repo, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError(f"git {args[0]} could not run: {exc}") from exc
        if process.returncode != 0:
            raise WorkspaceError(f"git {args[0]} failed: {(process.stderr or process.stdout).strip()[:300]}")
        return process.stdout

    def check(self) -> None:
        """The target must be a Git repository with at least one commit."""
        self._run("rev-parse", "--verify", "HEAD")

    def create(self, task_id: str) -> Worktree:
        base = self._run("rev-parse", "HEAD").strip()
        branch = f"forge/{task_id}"
        path = self.root / task_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("worktree", "add", "-q", "-b", branch, str(path), base)
        return Worktree(path, branch, base)

    def finalize(self, worktree: Worktree, message: str) -> tuple[str, str]:
        """Commit every change on the worktree's branch; returns (diff, stat), both empty if unchanged."""
        self._run("add", "-A", cwd=worktree.path)
        stat = self._run("diff", "--cached", "--stat", cwd=worktree.path).strip()
        if not stat:
            return "", ""
        diff = self._run("diff", "--cached", cwd=worktree.path)
        self._run(*FORGE_IDENTITY, "commit", "-q", "-m", message, cwd=worktree.path)
        return diff, stat

    def run_tests(self, worktree_path: Path, command: list[str], timeout: float = 600.0) -> TestReport:
        try:
            process = subprocess.run(
                command, cwd=worktree_path, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return TestReport(False, f"tests did not finish within {timeout:.0f} s")
        except OSError as exc:
            return TestReport(False, f"tests could not run: {exc}")
        output = "\n".join((process.stdout + process.stderr).strip().splitlines()[-30:])
        return TestReport(process.returncode == 0, output)

    def apply(self, branch: str) -> None:
        """Fast-forward the user's checked-out branch to the reviewed change."""
        self._run("merge", "--ff-only", "-q", branch)

    def discard(self, worktree_path: Path, branch: str) -> None:
        """Remove the worktree and its branch (also used to clean up after an apply)."""
        if worktree_path.exists():
            self._run("worktree", "remove", "--force", str(worktree_path))
        self._run("worktree", "prune")
        self._run("branch", "-D", branch)
