"""Executor contract: an external coding agent that works on a brief inside a given directory."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

Runner = Callable[[list[str], str, Path, dict[str, str], float], "subprocess.CompletedProcess[str]"]
"""(argv, stdin text, working directory, environment, timeout) -> finished process. Replaceable in tests."""


def run_process(argv: list[str], stdin: str, cwd: Path, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, input=stdin, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    summary: str  # the coding agent's own final report
    turns: int | None = None
    cost_usd: float | None = None  # as reported by the executor (informational on a subscription)
    duration_s: float = 0.0
    error: str | None = None


class CodingExecutor(Protocol):
    name: str

    def run(self, prompt: str, workspace: Path, instructions: str) -> ExecutionResult:
        """Work on `prompt` inside `workspace`; `instructions` extend the agent's system prompt."""
        ...
