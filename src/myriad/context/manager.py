"""Context manager: builds the prompt for one model call by fixed priority within a token limit.

Priority (highest first):
1. Required - never shortened: system prompt, task goal, current user input and every message
   after it (own outputs, engine feedback).
2. Optional memory block (`[MEMORY DATA]`, complete or skipped - never truncated).
3. Optional skill (trusted instructions from skills/), appended to the system prompt so the
   static prefix stays cacheable; complete or skipped - never truncated.
4. Additional history: older messages of the task first, then earlier session messages, filled
   from newest to oldest as one contiguous block, capped at HISTORY_BUDGET_TOKENS.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from myriad.models import MEMORY_PREFIX, ChatMessage, Task
from myriad.resources.tokens import estimate_messages, estimate_tokens

HISTORY_BUDGET_TOKENS = 1200


class ContextTooLarge(Exception):
    """The required context alone does not fit the input limit."""

    def __init__(self, required_tokens: int, input_limit: int) -> None:
        super().__init__(f"required context ~{required_tokens} tokens > input limit {input_limit}")
        self.required_tokens = required_tokens
        self.input_limit = input_limit


@dataclass(frozen=True)
class BuiltContext:
    messages: list[ChatMessage]
    input_tokens: int
    history_tokens: int
    memory_skipped: bool
    skill_used: bool = False
    skill_skipped: bool = False


def last_user_input(messages: Sequence[ChatMessage]) -> int:
    """Index of the newest message typed by the user (origin field, not text prefix)."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == "user" and not message.from_engine:
            return index
    return 0


def _is_memory(message: ChatMessage) -> bool:
    return message.from_engine and message.content.startswith(MEMORY_PREFIX)


class ContextManager:
    def __init__(self, history_budget: int = HISTORY_BUDGET_TOKENS) -> None:
        self.history_budget = history_budget

    def build(
        self,
        system_prompt: str,
        task: Task,
        history: Sequence[ChatMessage],
        input_limit: int,
        skill: str = "",
    ) -> BuiltContext:
        messages = task.messages
        tail_start = last_user_input(messages)
        tail = messages[tail_start:]
        if tail_start == 0:
            goal: list[ChatMessage] = []
            middle: list[ChatMessage] = []
        else:
            goal = [ChatMessage("user", f"[TASK GOAL] {task.goal}", origin="user")]
            middle = messages[1:tail_start]

        system = ChatMessage("system", system_prompt)
        required = [system, *goal, *(m for m in tail if not _is_memory(m))]
        required_tokens = estimate_messages(required)
        if required_tokens > input_limit:
            raise ContextTooLarge(required_tokens, input_limit)

        memory_tokens = estimate_messages([m for m in tail if _is_memory(m)])
        include_memory = required_tokens + memory_tokens <= input_limit
        free = input_limit - required_tokens - (memory_tokens if include_memory else 0)

        skill_text = f"\n\n{skill}" if skill else ""
        include_skill = bool(skill) and estimate_tokens(skill_text) <= free
        if include_skill:
            free -= estimate_tokens(skill_text)
            system = ChatMessage("system", system_prompt + skill_text)

        kept: list[ChatMessage] = []
        budget = min(self.history_budget, free)
        for message in [*reversed(middle), *reversed(history)]:  # newest first
            cost = estimate_tokens(message.content)
            if cost > budget:
                break  # contiguous history: the first message that does not fit ends it
            kept.append(message)
            budget -= cost
        from_task = min(len(kept), len(middle))
        kept_middle = list(reversed(kept[:from_task]))
        kept_prior = list(reversed(kept[from_task:]))

        final_tail = [m for m in tail if include_memory or not _is_memory(m)]
        built = [system, *kept_prior, *goal, *kept_middle, *final_tail]
        return BuiltContext(
            messages=built,
            input_tokens=estimate_messages(built),
            history_tokens=estimate_messages(kept),
            memory_skipped=memory_tokens > 0 and not include_memory,
            skill_used=include_skill,
            skill_skipped=bool(skill) and not include_skill,
        )
