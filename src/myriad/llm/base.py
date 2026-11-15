"""LLM provider contract. Every adapter (mock, local, cloud) implements this."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from myriad.models import ChatMessage


class ProviderError(Exception):
    """The model provider could not deliver an answer (unreachable, timeout, missing model, ...)."""


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    estimated: bool  # token counts are estimates, not provider-reported
    simulated: bool  # text was not produced by a real model
    truncated: bool = False  # the provider stopped at the output limit
    # Length of the visible answer when output_tokens also contains hidden reasoning tokens.
    answer_tokens: int | None = None


class LLMProvider(Protocol):
    name: str
    model: str
    context_window: int
    simulated: bool
    # "json": the model writes decision JSON; "tools": free-text answers + native tool calls.
    decision_mode: Literal["json", "tools"]

    def complete(
        self,
        messages: Sequence[ChatMessage],
        max_output_tokens: int,
        temperature: float | None = None,
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        think: bool = False,
        thinking_budget: int = 0,
    ) -> Completion:
        """Always returns decision JSON in Completion.text, whatever the decision mode."""
        ...
