"""Phase 10b: Forge - Claude Code executor, Git worktrees, test gate, artifacts, approval.

Real Git repositories in temporary folders; Claude Code itself is simulated (no subscription use).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from myriad.agents.definition import load_agent
from myriad.artifacts import SQLiteArtifactStore
from myriad.config import ForgeSettings, build_coding_service
from myriad.engine.task_engine import TaskEngine
from myriad.executors.base import ExecutionResult
from myriad.executors.claude_code import SCRUBBED_ENV, ClaudeCodeExecutor, find_claude
from myriad.executors.service import CodingService
from myriad.executors.workspace import GitWorkspace, WorkspaceError
from myriad.interface.cli import _command
from myriad.interface.session import ChatSession
from myriad.llm.mock import MockProvider
from myriad.models import FailureReason, Task, TaskStatus

from conftest import AGENTS_DIR, ANSWER_DONE, BRIEF, j

PASS = [sys.executable, "-c", "import sys; sys.exit(0)"]
FAIL = [sys.executable, "-c", "import sys; print('1 failed'); sys.exit(1)"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    (path / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return path


class FakeExecutor:
    """Stands in for Claude Code: writes files into the workspace and reports."""

    name = "fake"

    def __init__(self, files: dict[str, str] | None = None, ok: bool = True) -> None:
        self.files = files if files is not None else {"app.py": "def hello():\n    return 'hello'\n"}
        self.ok = ok
        self.calls: list[tuple[str, Path, str]] = []

    def run(self, prompt: str, workspace: Path, instructions: str) -> ExecutionResult:
        self.calls.append((prompt, workspace, instructions))
        if not self.ok:
            return ExecutionResult(False, "", error="permission denied")
        for name, content in self.files.items():
            (workspace / name).write_text(content, encoding="utf-8")
        return ExecutionResult(True, "Changed hello() to return 'hello' and ran the tests.", turns=3)


def service(repo: Path, tmp_path: Path, executor=None, tests=PASS) -> CodingService:
    workspace = GitWorkspace(repo, tmp_path / "worktrees")
    return CodingService(executor or FakeExecutor(), workspace, SQLiteArtifactStore(":memory:"), tests)


def task(goal: str = "Return 'hello' from hello()") -> Task:
    return Task(goal=goal, assigned_to="forge", created_by="atlas", parent_id="p", root_id="root1")


# --- Claude Code executor ---------------------------------------------------------


def test_find_claude_prefers_the_newest_bundled_binary(tmp_path) -> None:
    for version in ("2.1.99", "2.1.325"):
        binary = tmp_path / f"anthropic.claude-code-{version}-win32-x64" / "resources" / "native-binary" / "claude.exe"
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")
    assert "2.1.325" in find_claude("auto", extensions_dir=tmp_path)
    assert find_claude(str(tmp_path / "missing.exe")) is None


def test_executor_runs_headless_with_restricted_tools_and_no_api_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    seen: dict = {}

    def runner(argv, stdin, cwd, env, timeout):
        seen.update(argv=argv, stdin=stdin, cwd=cwd, env=env, timeout=timeout)
        out = json.dumps({"result": "Done.", "is_error": False, "num_turns": 4, "total_cost_usd": 0.12})
        return subprocess.CompletedProcess(argv, 0, out, "")

    result = ClaudeCodeExecutor("claude.exe", allowed_tools="Read Edit", timeout=60, runner=runner).run(
        "Do the task", tmp_path, "Be careful.")
    assert result.ok and result.summary == "Done." and result.turns == 4
    argv = seen["argv"]
    assert argv[:2] == ["claude.exe", "-p"] and seen["stdin"] == "Do the task" and seen["cwd"] == tmp_path
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--allowedTools") + 1] == "Read Edit"
    assert argv[argv.index("--append-system-prompt") + 1] == "Be careful."
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert not any(key in seen["env"] for key in SCRUBBED_ENV)


@pytest.mark.parametrize(("runner_result", "fragment"), [
    (subprocess.CompletedProcess([], 0, json.dumps({"result": "Hit a wall", "is_error": True}), ""), "Hit a wall"),
    (subprocess.CompletedProcess([], 1, "not json", "boom"), "unexpected output (exit 1): boom"),
    (subprocess.TimeoutExpired("claude", 60), "did not finish within"),
    (FileNotFoundError(), "Claude Code not found"),
])
def test_executor_failures_are_reported(tmp_path, runner_result, fragment) -> None:
    def runner(*_args):
        if isinstance(runner_result, BaseException):
            raise runner_result
        return runner_result

    result = ClaudeCodeExecutor("claude", allowed_tools="Read", runner=runner).run("x", tmp_path, "y")
    assert not result.ok and fragment in result.error


# --- Git workspace ----------------------------------------------------------------


def test_worktree_isolates_commits_and_applies_by_fast_forward(repo, tmp_path) -> None:
    workspace = GitWorkspace(repo, tmp_path / "worktrees")
    worktree = workspace.create("t1")
    assert worktree.path.is_dir() and worktree.branch == "forge/t1"
    (worktree.path / "app.py").write_text("changed\n", encoding="utf-8")
    diff, stat = workspace.finalize(worktree, "Forge: change")
    assert "changed" in diff and "app.py" in stat
    assert (repo / "app.py").read_text(encoding="utf-8").startswith("def hello")  # user's checkout untouched
    workspace.apply(worktree.branch)
    assert (repo / "app.py").read_text(encoding="utf-8") == "changed\n"
    workspace.discard(worktree.path, worktree.branch)
    assert not worktree.path.exists() and "forge/t1" not in git(repo, "branch")


def test_unchanged_worktree_has_no_diff_and_tests_report_status(repo, tmp_path) -> None:
    workspace = GitWorkspace(repo, tmp_path / "worktrees")
    worktree = workspace.create("t2")
    assert workspace.finalize(worktree, "nothing") == ("", "")
    assert workspace.run_tests(worktree.path, PASS).passed
    failed = workspace.run_tests(worktree.path, FAIL)
    assert not failed.passed and "1 failed" in failed.output


def test_a_folder_without_git_is_rejected(tmp_path) -> None:
    with pytest.raises(WorkspaceError):
        GitWorkspace(tmp_path, tmp_path / "w").check()


# --- coding service -----------------------------------------------------------------


def test_successful_change_becomes_a_pending_artifact(repo, tmp_path) -> None:
    coding = service(repo, tmp_path)
    outcome = coding.run(task(), "[BRIEF from atlas]\nGoal: say hello", "rules")
    artifact = coding.artifacts.get(outcome.artifact_id)
    assert outcome.ok and artifact.status == "pending" and artifact.tests_passed is True
    assert f"/review {artifact.id}" in outcome.text and "tests passed" in outcome.text
    assert "return 'hello'" in artifact.diff and artifact.root_id == "root1"
    assert [a.id for a in coding.pending()] == [artifact.id]


def test_failing_tests_keep_the_artifact_but_fail_the_task(repo, tmp_path) -> None:
    coding = service(repo, tmp_path, tests=FAIL)
    outcome = coding.run(task(), "brief", "rules")
    assert not outcome.ok and outcome.reason is FailureReason.TESTS_FAILED
    assert coding.artifacts.get(outcome.artifact_id).tests_passed is False
    with pytest.raises(WorkspaceError, match="force"):
        coding.apply(outcome.artifact_id)
    assert "applied" in coding.apply(outcome.artifact_id, force=True)


def test_executor_failure_cleans_up_the_worktree(repo, tmp_path) -> None:
    coding = service(repo, tmp_path, executor=FakeExecutor(ok=False))
    outcome = coding.run(task(), "brief", "rules")
    assert not outcome.ok and outcome.reason is FailureReason.EXECUTOR_ERROR
    assert "permission denied" in outcome.text
    assert "forge/" not in git(repo, "branch") and not list((tmp_path / "worktrees").glob("*"))


def test_no_changes_means_success_without_artifact(repo, tmp_path) -> None:
    outcome = service(repo, tmp_path, executor=FakeExecutor(files={})).run(task(), "brief", "rules")
    assert outcome.ok and outcome.artifact_id is None and "No code changes" in outcome.text


def test_apply_and_discard_happen_only_once(repo, tmp_path) -> None:
    coding = service(repo, tmp_path)
    first = coding.run(task(), "brief", "rules").artifact_id
    assert "applied" in coding.apply(first)
    assert "return 'hello'" in (repo / "app.py").read_text(encoding="utf-8")
    with pytest.raises(WorkspaceError, match="already applied"):
        coding.apply(first)
    coding.executor = FakeExecutor({"app.py": "def hello():\n    return 'bye'\n"})  # a different change
    second = coding.run(task("again"), "brief", "rules").artifact_id
    assert "discarded" in coding.discard(second)
    assert "return 'hello'" in (repo / "app.py").read_text(encoding="utf-8")  # discard left the project alone


# --- engine, definition, configuration, CLI ------------------------------------------


def test_forge_definition_loads(atlas) -> None:
    forge = load_agent(AGENTS_DIR / "forge.yaml")
    assert forge.executor == "coding" and forge.job.reports_to == "atlas"
    assert "forge" in atlas.job.delegates_to


def test_delegation_to_forge_runs_the_executor_without_a_local_model_call(atlas, repo, tmp_path) -> None:
    forge = load_agent(AGENTS_DIR / "forge.yaml")
    executor = FakeExecutor()
    coding = service(repo, tmp_path, executor=executor)
    delegate = j(type="delegate", agent="forge", brief={**BRIEF, "goal": "Return 'hello' from hello()"})
    provider = MockProvider([delegate, ANSWER_DONE])
    engine = TaskEngine({"atlas": atlas, "forge": forge}, provider, executors={"coding": coding})
    reply = ChatSession(engine).send("Make hello() return hello")
    child = next(t for t in engine.tasks.values() if not t.is_root)
    assert reply.task.status is TaskStatus.DONE and child.status is TaskStatus.DONE
    assert len(provider.calls) == 2  # Atlas twice; Forge itself never calls the local model
    prompt, _, instructions = executor.calls[0]
    assert prompt.startswith("[BRIEF from atlas]") and "Forge" in instructions and "tests" in instructions
    assert "Code change" in provider.calls[1][-1].content  # Atlas sees the compact result with the id
    _, messages = engine.protocol(reply.task.root_id)
    assert any(m.recipient == "artifacts" for m in messages)


def test_executor_agent_without_executor_is_rejected(atlas) -> None:
    with pytest.raises(ValueError, match="needs executor 'coding'"):
        TaskEngine({"atlas": atlas, "forge": load_agent(AGENTS_DIR / "forge.yaml")}, MockProvider())


def test_coding_service_is_built_only_when_everything_is_in_place(repo, tmp_path) -> None:
    assert build_coding_service(None, tmp_path, tmp_path)[0] is None
    settings = ForgeSettings(repo=str(tmp_path / "missing"), command=sys.executable)
    assert "Repository fehlt" in build_coding_service(settings, tmp_path, tmp_path)[1]
    settings = ForgeSettings(repo=str(repo), command=sys.executable, test_command=["python", "-V"])
    coding, status = build_coding_service(settings, tmp_path, tmp_path / "data")
    assert isinstance(coding, CodingService) and status == "an (project)"
    coding.artifacts.close()


def test_cli_commands_show_review_apply_and_discard(atlas, repo, tmp_path) -> None:
    coding = service(repo, tmp_path)
    session = ChatSession(TaskEngine({"atlas": atlas}, MockProvider(), executors={"coding": coding}))
    first = coding.run(task(), "brief", "rules").artifact_id
    assert first in _command(session, "/changes") and "Tests grün" in _command(session, "/changes")
    review = _command(session, "/review", first)
    assert "Zusammenfassung von Claude Code" in review and "return 'hello'" in review
    assert "applied" in _command(session, "/apply", first)
    assert "Nicht möglich" in _command(session, "/discard", first)
    assert "Bitte die ID angeben" in _command(session, "/apply", "")
    assert _command(session, "/changes") == "Keine offenen Änderungen."
