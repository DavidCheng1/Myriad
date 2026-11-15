"""Agent runtime: exactly one model call per step - context, limit check, provider call, validation."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from myriad.agents.definition import AgentDefinition, render_system_prompt
from myriad.context.manager import ContextManager, ContextTooLarge
from myriad.llm.actions import action_specs
from myriad.llm.base import Completion, LLMProvider
from myriad.models import (
    DECISION_FORMAT,
    ENGINE_PREFIX,
    TOOL_FORMAT,
    ChatMessage,
    Decision,
    FailureReason,
    Task,
    parse_decision,
)
from myriad.resources.tokens import estimate_tokens
from myriad.skills.loader import Skill, select_skill

SKILL_BUDGET_TOKENS = 600  # one complete skill block; a larger skill is skipped, never truncated

Preflight = Callable[[int, int], None]
"""Called with (estimated input tokens, reserved output tokens) before the provider call."""

InputLimit = Callable[[int], int]
"""Returns the available input tokens for a given reserved output (window and budget)."""


class LimitExceeded(Exception):
    def __init__(self, reason: FailureReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class StepResult:
    raw_text: str
    completion: Completion
    decision: Decision | None
    error: str | None
    memory_skipped: bool = False
    skill: str | None = None  # name of the skill that was in the prompt
    skill_skipped: str | None = None  # name of a selected skill that did not fit


class AgentRuntime:
    def __init__(
        self,
        agent: AgentDefinition,
        provider: LLMProvider,
        *,
        team: Mapping[str, AgentDefinition] | None = None,
        available_tools: Collection[str] | None = None,
        tool_hints: Mapping[str, str] | None = None,
        context: str = "",
        context_manager: ContextManager | None = None,
        skills: Mapping[str, Skill] | None = None,
    ) -> None:
        self.agent = agent
        self.provider = provider
        self.team = team
        self.available_tools = available_tools
        self.skills = dict(skills or {})  # only the skills this agent may use
        self.context_manager = context_manager or ContextManager()
        self.decision_mode = getattr(provider, "decision_mode", "json")
        tools_mode = self.decision_mode == "tools"
        self.system_prompt = render_system_prompt(
            agent,
            TOOL_FORMAT if tools_mode else DECISION_FORMAT,
            team=team,
            available_tools=available_tools,
            tool_hints=None if tools_mode else tool_hints,  # tool specs describe arguments natively
            context=context,
        )

    @property
    def correction_hint(self) -> str:
        if self.decision_mode == "tools":
            return "Answer in plain text or call exactly one of the provided tools."
        return "Reply with exactly one valid JSON decision."

    def max_output(self, task: Task) -> int:
        """Visible answer limit: the YAML limit, for delegated tasks also the brief's limit."""
        limit = self.agent.llm.max_output_tokens
        return min(limit, task.brief.output_limit) if task.brief is not None else limit

    def reserved_output(self, task: Task) -> int:
        """Tokens reserved per call: the visible answer plus the thinking budget, if enabled."""
        llm = self.agent.llm
        return self.max_output(task) + (llm.thinking_budget if llm.thinking else 0)

    def select_skill(self, task: Task) -> Skill | None:
        """Code picks the skill: right after a delegation result, or from the request text."""
        if not self.skills or not task.messages:
            return None
        last = task.messages[-1]
        after_delegation = last.from_engine and last.content.startswith(f"{ENGINE_PREFIX} Result from")
        if task.is_root:
            latest = next((m.content for m in reversed(task.messages)
                           if m.role == "user" and not m.from_engine), "")
            request = f"{task.goal}\n{latest}"
        else:
            request = task.messages[0].content  # a delegated task's request is its brief
        return select_skill(self.skills.values(), request=request, after_delegation=after_delegation)

    def build_messages(
        self, task: Task, history: Sequence[ChatMessage] = ()
    ) -> list[ChatMessage]:
        """Prompt as it would be sent with the full context window available."""
        limit = self.provider.context_window - self.reserved_output(task)
        return self.context_manager.build(self.system_prompt, task, history, limit).messages

    def step(
        self,
        task: Task,
        history: Sequence[ChatMessage],
        preflight: Preflight,
        input_limit: InputLimit,
    ) -> StepResult:
        llm = self.agent.llm
        max_output, reserved = self.max_output(task), self.reserved_output(task)
        skill = self.select_skill(task)
        block = skill.block() if skill is not None else ""
        oversized = bool(block) and estimate_tokens(block) > SKILL_BUDGET_TOKENS
        try:
            built = self.context_manager.build(
                self.system_prompt, task, history, input_limit(reserved),
                skill="" if oversized else block,
            )
        except ContextTooLarge as exc:
            preflight(exc.required_tokens, reserved)  # raises with the precise reason
            raise LimitExceeded(FailureReason.INPUT_TOO_LARGE, str(exc)) from exc
        preflight(built.input_tokens, reserved)  # last safety net, raises LimitExceeded
        tools = (
            action_specs(self.agent, task, self.team, self.available_tools)
            if self.decision_mode == "tools"
            else None
        )
        completion = self.provider.complete(
            built.messages,
            max_output,
            temperature=llm.temperature,
            tools=tools,
            think=llm.thinking,
            thinking_budget=llm.thinking_budget,
        )  # may raise ProviderError; the engine turns it into a controlled failure
        used = skill.name if skill is not None and built.skill_used else None
        skipped = skill.name if skill is not None and (oversized or built.skill_skipped) else None
        visible = completion.answer_tokens if completion.answer_tokens is not None else completion.output_tokens
        if completion.truncated or visible > max_output:  # rejected like invalid output, usage booked
            error = f"output exceeded max_output_tokens ({visible} > {max_output}); reply shorter"
            return StepResult(completion.text, completion, None, error, built.memory_skipped, used, skipped)
        decision, error = parse_decision(completion.text)
        return StepResult(completion.text, completion, decision, error, built.memory_skipped, used, skipped)
