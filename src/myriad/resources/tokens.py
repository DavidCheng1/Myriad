"""Token estimation, tracking and the pre-call budget check."""

from __future__ import annotations

import math
from collections.abc import Sequence

from myriad.models import ChatMessage, FailureReason, TokenUsage


def estimate_tokens(text: str) -> int:
    """Rough estimate: characters / 4, rounded up. Not a real tokenizer."""
    return math.ceil(len(text) / 4)


def estimate_messages(messages: Sequence[ChatMessage]) -> int:
    return sum(estimate_tokens(m.content) for m in messages)


class TokenTracker:
    """In-memory record of every model call."""

    def __init__(self) -> None:
        self._records: list[TokenUsage] = []

    def record(self, usage: TokenUsage) -> None:
        self._records.append(usage)

    @property
    def records(self) -> tuple[TokenUsage, ...]:
        return tuple(self._records)

    def total(self, *, root_id: str | None = None, task_id: str | None = None) -> int:
        return sum(
            r.total
            for r in self._records
            if (root_id is None or r.root_id == root_id)
            and (task_id is None or r.task_id == task_id)
        )


def check_call(
    *, input_tokens: int, reserved_output: int, context_window: int, budget: int, used: int
) -> tuple[FailureReason, str] | None:
    """Check input plus reserved output against context window and remaining budget."""
    needed = input_tokens + reserved_output
    if needed > context_window:
        return FailureReason.INPUT_TOO_LARGE, (
            f"Input too large: ~{input_tokens} tokens + {reserved_output} reserved output "
            f"exceed the context window of {context_window}."
        )
    remaining = budget - used
    if needed > remaining:
        return FailureReason.BUDGET_EXHAUSTED, (
            f"Task tree budget exhausted: ~{needed} tokens needed, "
            f"{max(remaining, 0)} of {budget} left."
        )
    return None
