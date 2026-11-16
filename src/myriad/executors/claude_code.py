"""Claude Code as a headless coding executor (`claude -p`), on the user's own subscription login."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from myriad.executors.base import ExecutionResult, Runner, run_process

# Removed from the child's environment so Claude Code can only use the subscription login,
# never pay-per-use API billing or another provider.
SCRUBBED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _version(path: Path) -> tuple[int, ...]:
    match = _VERSION.search(path.as_posix())
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def find_claude(command: str = "auto", extensions_dir: Path | None = None) -> str | None:
    """`auto`: the newest Claude Code bundled with the VS Code extension, else `claude` on PATH."""
    if command != "auto":
        return command if Path(command).is_file() or shutil.which(command) else None
    root = extensions_dir or Path.home() / ".vscode" / "extensions"
    bundled = [p for p in root.glob("anthropic.claude-code-*/resources/native-binary/*")
               if p.name in ("claude.exe", "claude")]
    if bundled:
        return str(max(bundled, key=_version))
    return shutil.which("claude.exe") or shutil.which("claude")


class ClaudeCodeExecutor:
    name = "claude_code"

    def __init__(
        self,
        command: str,
        *,
        allowed_tools: str,
        timeout: float = 900.0,
        runner: Runner | None = None,
    ) -> None:
        self.command = command
        self.allowed_tools = allowed_tools
        self.timeout = timeout
        self._runner = runner or run_process

    def argv(self, instructions: str) -> list[str]:
        return [
            self.command, "-p",
            "--output-format", "json",
            "--permission-mode", "acceptEdits",  # file edits inside the workspace; nothing else is pre-approved
            "--allowedTools", self.allowed_tools,  # in -p mode every other tool call is denied
            "--append-system-prompt", instructions,
            "--no-session-persistence",
            "--strict-mcp-config",  # no MCP servers from the user's settings
        ]

    def run(self, prompt: str, workspace: Path, instructions: str) -> ExecutionResult:
        env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
        started = time.monotonic()
        try:
            process = self._runner(self.argv(instructions), prompt, workspace, env, self.timeout)
        except FileNotFoundError:
            return ExecutionResult(False, "", error=f"Claude Code not found: {self.command}")
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, "", duration_s=self.timeout,
                                   error=f"Claude Code did not finish within {self.timeout:.0f} s")
        duration = time.monotonic() - started
        try:
            data = json.loads(process.stdout)
        except json.JSONDecodeError:
            detail = (process.stderr or process.stdout or "").strip()[:300]
            return ExecutionResult(False, "", duration_s=duration,
                                   error=f"unexpected output (exit {process.returncode}): {detail}")
        summary = str(data.get("result") or "").strip()
        failed = bool(data.get("is_error")) or process.returncode != 0
        return ExecutionResult(
            ok=not failed,
            summary=summary,
            turns=data.get("num_turns"),
            cost_usd=data.get("total_cost_usd"),
            duration_s=duration,
            error=(summary or str(data.get("subtype") or "failed")) if failed else None,
        )
