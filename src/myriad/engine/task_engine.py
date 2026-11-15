"""Task engine: owns task status, steps, corrections, permissions, limits, delegation and memory."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial

from myriad.agents.definition import AgentDefinition
from myriad.agents.runtime import AgentRuntime, LimitExceeded
from myriad.llm.base import Completion, LLMProvider, ProviderError
from myriad.memory.access import MemoryAccess
from myriad.memory.store import MemoryStore, normalize_subject
from myriad.models import (
    ENGINE_PREFIX,
    MEMORY_PREFIX,
    Answer,
    AskUser,
    ChatMessage,
    Decision,
    Delegate,
    FailureReason,
    Message,
    MessageType,
    Task,
    TaskStatus,
    TokenUsage,
    ToolCall,
    render_brief,
)
from myriad.resources.tokens import TokenTracker, check_call, estimate_tokens
from myriad.skills.loader import Skill
from myriad.tools.memory_tools import TOOL_HINTS, run_memory_tool

MAX_CORRECTIONS = 2
MEMORY_BUDGET_TOKENS = 800  # the complete [MEMORY DATA] block, marker included (free-text plans ~400)
MIN_REUSABLE_CHARS = 200  # shorter answers ("Sage is ready to help") are not worth reusing
MEMORY_TOOLS = frozenset(TOOL_HINTS)


@dataclass
class TaskResult:
    task: Task
    text: str
    notices: list[str] = field(default_factory=list)
    llm_calls: int = 0  # including calls of delegated child tasks


@dataclass(frozen=True)
class _ActionResult:
    kind: str  # "delegate" or the tool name
    feedback: str
    ok: bool
    llm_calls: int = 0


def _engine_data(text: str) -> ChatMessage:
    return ChatMessage("user", f"{ENGINE_PREFIX} {text}", origin="engine")


class TaskEngine:
    def __init__(
        self,
        agents: Mapping[str, AgentDefinition],
        provider: LLMProvider,
        root_agent: str = "atlas",
        tracker: TokenTracker | None = None,
        memory: MemoryStore | None = None,
        context: str = "",
        skills: Mapping[str, Skill] | None = None,
    ) -> None:
        """`context` is the organization profile (config/context.md) shown to every agent;
        `skills` is the skill library (skills/), of which each agent gets its `skills.library`."""
        if root_agent not in agents:
            raise ValueError(f"root agent '{root_agent}' is not loaded")
        self.agents = dict(agents)
        self.skills = dict(skills or {})
        if self.skills:  # without a library (tests, minimal setups) skill lists are ignored
            for aid, agent in self.agents.items():
                missing = [s for s in agent.skills.library if s not in self.skills]
                if missing:
                    raise ValueError(f"agent '{aid}' lists unknown skills: {', '.join(missing)}")
        self.root = self.agents[root_agent]
        self.provider = provider
        self.memory = memory
        self.available_tools = MEMORY_TOOLS if memory is not None else frozenset()
        self.access = (
            {aid: MemoryAccess(a, memory) for aid, a in self.agents.items()} if memory else {}
        )
        self.runtimes = {
            aid: AgentRuntime(
                a,
                provider,
                team=self.agents,
                available_tools=self.available_tools,
                tool_hints=TOOL_HINTS,
                context=context,
                skills={s: self.skills[s] for s in a.skills.library if s in self.skills},
            )
            for aid, a in self.agents.items()
        }
        self.tracker = tracker or TokenTracker()
        self.tasks: dict[str, Task] = {}
        self.messages: list[Message] = []  # protocol of delegations, tool calls and memory

    # --- lifecycle -------------------------------------------------------

    def start(self, goal: str, history: Sequence[ChatMessage] = ()) -> TaskResult:
        """A new, independent request: new root task with its own budget."""
        task = Task(goal=goal, assigned_to=self.root.id, created_by="user")
        task.messages.append(ChatMessage("user", goal, origin="user"))
        self.tasks[task.id] = task
        recalled = self._recall(task)
        if recalled is not None:
            task.messages.append(recalled)
        return self.run(task, history)

    def resume(self, task: Task, reply: str, history: Sequence[ChatMessage] = ()) -> TaskResult:
        """Continue the same task after ask_user: ids, steps and budget are kept."""
        if task.status is not TaskStatus.NEEDS_INPUT:
            raise ValueError(f"task {task.id} is not waiting for input")
        task.messages.append(ChatMessage("user", reply, origin="user"))
        return self.run(task, history)

    def abandon(self, task: Task) -> None:
        task.status = TaskStatus.FAILED
        task.failure_reason = FailureReason.ABANDONED

    def run(self, task: Task, history: Sequence[ChatMessage] = ()) -> TaskResult:
        agent = self.agents[task.assigned_to]
        runtime = self.runtimes[task.assigned_to]
        task.status = TaskStatus.RUNNING
        notices: list[str] = []
        calls = 0
        while True:
            if task.steps >= agent.limits.max_steps_per_task:
                detail = (
                    f"Step limit reached ({task.steps}/{agent.limits.max_steps_per_task} "
                    "model calls incl. corrections)."
                )
                return self._fail(task, FailureReason.STEP_LIMIT, detail, notices, calls)
            try:
                step = runtime.step(
                    task, history, partial(self._preflight, task), partial(self._input_limit, task)
                )
            except LimitExceeded as exc:
                return self._fail(task, exc.reason, exc.detail, notices, calls)
            except ProviderError as exc:  # no answer, nothing to book
                detail = f"Model provider error: {exc}"
                return self._fail(task, FailureReason.PROVIDER_ERROR, detail, notices, calls)
            calls += 1
            task.steps += 1
            self._book(task, step.completion)  # also for invalid output
            if step.memory_skipped:
                self._log(task, "memory", task.assigned_to, MessageType.INFO,
                          "skipped: memory block does not fit the input limit")
            if step.skill is not None and step.skill not in task.skills_used:
                task.skills_used.append(step.skill)
                self._log(task, "skills", task.assigned_to, MessageType.INFO, f"activated {step.skill}")
            if step.skill_skipped is not None:
                self._log(task, "skills", task.assigned_to, MessageType.INFO,
                          f"skipped {step.skill_skipped}: does not fit the budget")
            task.messages.append(ChatMessage("assistant", step.raw_text))

            decision = step.decision
            error = step.error if decision is None else self._permission_error(task, agent, decision)
            if error is not None:
                if task.corrections >= MAX_CORRECTIONS:
                    detail = f"Invalid model output after {MAX_CORRECTIONS} correction attempts: {error}"
                    return self._fail(task, FailureReason.INVALID_OUTPUT, detail, notices, calls)
                task.corrections += 1
                task.messages.append(_engine_data(
                    f"Your last output was rejected: {error}. {runtime.correction_hint}"
                ))
                continue

            if isinstance(decision, Answer):
                return self._finish(task, decision, notices, calls)
            if isinstance(decision, AskUser):
                task.status = TaskStatus.NEEDS_INPUT
                task.asked_user = True
                return TaskResult(task, decision.question, notices, calls)
            if isinstance(decision, Delegate):
                action = self._delegate(task, decision)
            else:
                action = self._call_tool(task, decision)
            calls += action.llm_calls
            if not action.ok:
                task.unresolved_actions.add(action.kind)
                task.failed_actions.append(action.feedback)
                notices.append(action.feedback)
            else:
                task.unresolved_actions.discard(action.kind)  # only a success of the same kind
            task.messages.append(_engine_data(action.feedback))

    def protocol(self, root_id: str) -> tuple[list[Task], list[Message]]:
        """All tasks and protocol messages of one request (task tree)."""
        tasks = [t for t in self.tasks.values() if t.root_id == root_id]
        return tasks, [m for m in self.messages if m.root_id == root_id]

    # --- checks ----------------------------------------------------------

    def _preflight(self, task: Task, input_tokens: int, reserved_output: int) -> None:
        problem = check_call(
            input_tokens=input_tokens,
            reserved_output=reserved_output,
            context_window=self.provider.context_window,
            budget=self.root.limits.token_budget_task_tree,
            used=self.tracker.total(root_id=task.root_id),
        )
        if problem is not None:
            raise LimitExceeded(*problem)

    def _input_limit(self, task: Task, reserved_output: int) -> int:
        """Available input tokens: min(context window, remaining tree budget) - reserved output."""
        remaining = self.root.limits.token_budget_task_tree - self.tracker.total(root_id=task.root_id)
        return min(self.provider.context_window, remaining) - reserved_output

    def _permission_error(self, task: Task, agent: AgentDefinition, decision: Decision) -> str | None:
        if isinstance(decision, AskUser) and not task.is_root:
            return "ask_user is only allowed in the root task; use answer with outcome needs_input"
        if isinstance(decision, Delegate):
            allowed = agent.job.delegates_to
            if decision.agent not in allowed:
                return (
                    f"delegation to '{decision.agent}' is not permitted "
                    f"(allowed: {', '.join(allowed) or 'none'})"
                )
            if task.depth + 1 > self.root.limits.max_delegation_depth:
                return f"maximum delegation depth {self.root.limits.max_delegation_depth} reached"
        if isinstance(decision, ToolCall) and decision.tool not in agent.skills.tools:
            return (
                f"tool '{decision.tool}' is not permitted "
                f"(allowed: {', '.join(agent.skills.tools) or 'none'})"
            )
        return None

    # --- memory (code, not model) ----------------------------------------

    def _recall(self, task: Task) -> ChatMessage | None:
        """Bounded pre-retrieval before the first model call: only a current, reusable result
        for the same normalized request, and only if the complete block fits the budget."""
        access = self.access.get(task.assigned_to)
        if access is None:
            return None
        record = access.find_reusable(normalize_subject(task.goal))
        if record is None:
            return None
        block = (
            f"{MEMORY_PREFIX} Result of an earlier successful identical request "
            f"(task {record.source_task_id}, {record.created_at:%Y-%m-%d}):\n{record.content}"
        )
        if estimate_tokens(block) > MEMORY_BUDGET_TOKENS:
            return None  # never truncate a stored result; use the normal flow instead
        self._log(task, "memory", task.assigned_to, MessageType.INFO, f"recalled {record.id}")
        return ChatMessage("user", block, origin="engine")

    def _remember(self, task: Task, text: str) -> None:
        """Store a reusable result: only for root tasks that succeeded through a delegation
        without asking the user (the original goal alone describes those completely)."""
        access = self.access.get(task.assigned_to)
        if access is None or not task.is_root or not task.delegation_succeeded or task.asked_user:
            return
        if len(text.strip()) < MIN_REUSABLE_CHARS:  # meta replies carry no reusable content
            return
        if access.retention_for("episode") is None:
            return
        record = access.write(
            kind="episode", content=text, source_task_id=task.id,
            subject=normalize_subject(task.goal), reusable=True,
        )
        self._log(task, task.assigned_to, "memory", MessageType.INFO, f"stored {record.id}")

    # --- actions ---------------------------------------------------------

    def _delegate(self, parent: Task, decision: Delegate) -> _ActionResult:
        target = decision.agent
        if target not in self.agents:  # availability is checked before any child exists
            return _ActionResult(
                "delegate",
                f"Delegation to '{target}' failed: the agent is permitted but not loaded.",
                ok=False,
            )
        child = parent.spawn_child(target, decision.brief)
        child.messages.append(ChatMessage(
            "user", render_brief(decision.brief, parent.assigned_to, self._user_request(parent)),
            origin="engine",
        ))
        self.tasks[child.id] = child
        self._log(child, parent.assigned_to, target, MessageType.REQUEST,
                  decision.brief.model_dump_json())
        result = self.run(child)  # synchronous; the brief is all the child sees - no history
        self._log(child, target, parent.assigned_to, MessageType.RESPONSE,
                  f"{child.status}: {result.text}")

        if child.status is TaskStatus.DONE:
            parent.delegation_succeeded = True
            feedback = f"Result from '{target}' (child task {child.id}, done): {result.text}"
            return _ActionResult("delegate", feedback, ok=True, llm_calls=result.llm_calls)
        if child.status is TaskStatus.NEEDS_INPUT:
            feedback = f"'{target}' needs more information (child task {child.id}): {result.text}"
        else:
            feedback = (
                f"Delegation to '{target}' failed "
                f"(child task {child.id}, {child.failure_reason}): {result.text}"
            )
        return _ActionResult("delegate", feedback, ok=False, llm_calls=result.llm_calls)

    @staticmethod
    def _user_request(task: Task) -> str:
        """The user's own words for a root task: the goal plus the latest reply, if different."""
        if not task.is_root:
            return ""
        latest = next(
            (m.content for m in reversed(task.messages) if m.role == "user" and not m.from_engine),
            task.goal,
        )
        return task.goal if latest == task.goal else f"{task.goal} | latest user message: {latest}"

    def _call_tool(self, task: Task, decision: ToolCall) -> _ActionResult:
        tool = decision.tool
        access = self.access.get(task.assigned_to)
        if access is None or tool not in self.available_tools:
            feedback = f"Tool '{tool}' failed: permitted but not available."
            ok = False
        else:
            feedback, ok = run_memory_tool(tool, decision.arguments, access, task.id)
        self._log(task, task.assigned_to, f"tool:{tool}", MessageType.REQUEST,
                  json.dumps(decision.arguments, ensure_ascii=False))
        self._log(task, f"tool:{tool}", task.assigned_to, MessageType.RESPONSE, feedback)
        return _ActionResult(tool, feedback, ok=ok)

    # --- results ---------------------------------------------------------

    def _log(self, task: Task, sender: str, recipient: str, kind: MessageType, content: str) -> None:
        self.messages.append(Message(
            task_id=task.id, root_id=task.root_id, sender=sender,
            recipient=recipient, type=kind, content=content,
        ))

    def _book(self, task: Task, completion: Completion) -> None:
        self.tracker.record(TokenUsage(
            task_id=task.id,
            root_id=task.root_id,
            agent_id=task.assigned_to,
            provider=self.provider.name,
            model=self.provider.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            estimated=completion.estimated,
        ))

    def _finish(self, task: Task, answer: Answer, notices: list[str], calls: int) -> TaskResult:
        if answer.outcome == "needs_input":
            task.status = TaskStatus.NEEDS_INPUT
            task.asked_user = task.asked_user or task.is_root
            return TaskResult(task, answer.text, notices, calls)
        if answer.outcome == "failed":
            return self._fail(task, FailureReason.REPORTED_BY_AGENT, answer.text, notices, calls)
        if task.unresolved_actions:  # no success while any failed action is unresolved
            notices.append(
                "Request not fulfilled: a failed action in this request is still unresolved, "
                "so the answer is not counted as a success."
            )
            return self._fail(task, FailureReason.UNRESOLVED_ACTION, answer.text, notices, calls)
        task.status = TaskStatus.DONE
        task.result = answer.text
        self._remember(task, answer.text)
        return TaskResult(task, answer.text, notices, calls)

    @staticmethod
    def _fail(
        task: Task, reason: FailureReason, text: str, notices: list[str], calls: int
    ) -> TaskResult:
        task.status = TaskStatus.FAILED
        task.failure_reason = reason
        task.result = text
        return TaskResult(task, text, notices, calls)
