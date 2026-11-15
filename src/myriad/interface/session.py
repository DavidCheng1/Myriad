"""Chat session: one conversation with Atlas, held in memory only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from myriad.engine.task_engine import TaskEngine
from myriad.models import ChatMessage, FailureReason, Task, TaskStatus


@dataclass(frozen=True)
class Reply:
    text: str
    task: Task
    notices: tuple[str, ...]
    llm_calls: int
    turn_tokens: int
    tree_tokens: int
    tree_budget: int
    session_tokens: int
    simulated: bool
    estimated: bool


class ChatSession:
    """Routes user messages to the engine. UI-independent, so a later GUI can reuse it."""

    def __init__(self, engine: TaskEngine) -> None:
        self.engine = engine
        self.id = uuid.uuid4().hex[:8]
        self.history: list[ChatMessage] = []  # compact: goal + final reply per finished task
        self.pending: Task | None = None  # task waiting for the user's answer
        self.root_ids: list[str] = []

    def send(self, text: str) -> Reply:
        text = text.strip()
        if not text:
            raise ValueError("empty message")
        tracker = self.engine.tracker
        if self.pending is not None:
            before = tracker.total(root_id=self.pending.root_id)
            result = self.engine.resume(self.pending, text, self.history)
        else:
            before = 0
            result = self.engine.start(text, self.history)
            self.root_ids.append(result.task.root_id)

        task = result.task
        self.pending = task if task.status is TaskStatus.NEEDS_INPUT else None
        # A rejected oversized input never reached the model and must not block later requests.
        if self.pending is None and task.failure_reason is not FailureReason.INPUT_TOO_LARGE:
            self.history += [ChatMessage("user", task.goal), ChatMessage("assistant", result.text)]
        tree = tracker.total(root_id=task.root_id)
        return Reply(
            text=result.text,
            task=task,
            notices=tuple(result.notices),
            llm_calls=result.llm_calls,
            turn_tokens=tree - before,
            tree_tokens=tree,
            tree_budget=self.engine.root.limits.token_budget_task_tree,
            session_tokens=self.total_tokens(),
            simulated=self.engine.provider.simulated,
            estimated=any(r.estimated for r in tracker.records if r.root_id == task.root_id),
        )

    def abandon_pending(self) -> bool:
        if self.pending is None:
            return False
        self.engine.abandon(self.pending)
        self.pending = None
        return True

    def total_tokens(self) -> int:
        return sum(self.engine.tracker.total(root_id=r) for r in self.root_ids)
