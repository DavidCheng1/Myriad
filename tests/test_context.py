"""Phase 8: context manager - token limits, fixed priority, no blocking, controlled failures."""

from __future__ import annotations

import pytest

from myriad.context.manager import HISTORY_BUDGET_TOKENS, ContextManager, ContextTooLarge
from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession
from myriad.llm.mock import MockProvider
from myriad.memory.access import MemoryAccess
from myriad.memory.store import SQLiteMemoryStore, normalize_subject
from myriad.models import MEMORY_PREFIX, ChatMessage, FailureReason, Task, TaskStatus
from myriad.resources.tokens import estimate_messages

SYSTEM = "S" * 400  # ~100 tokens


def task_with(goal: str, *extra: ChatMessage) -> Task:
    task = Task(goal=goal, assigned_to="atlas", created_by="user")
    task.messages += [ChatMessage("user", goal, origin="user"), *extra]
    return task


def memory_block(chars: int) -> ChatMessage:
    return ChatMessage("user", f"{MEMORY_PREFIX} " + "m" * chars, origin="engine")


def history(n: int, chars: int = 200) -> list[ChatMessage]:
    return [ChatMessage("user" if i % 2 == 0 else "assistant", f"H{i} " + "h" * chars) for i in range(n)]


# --- context manager (unit) -------------------------------------------------


def test_required_context_is_always_kept_and_history_capped() -> None:
    built = ContextManager().build(SYSTEM, task_with("Frage"), history(60), input_limit=100_000)
    assert built.messages[0].content == SYSTEM and built.messages[-1].content == "Frage"
    assert built.history_tokens <= HISTORY_BUDGET_TOKENS


def test_oldest_history_is_dropped_first() -> None:
    old = history(30)
    built = ContextManager().build(SYSTEM, task_with("Frage"), old, input_limit=100_000)
    kept = built.messages[1:-1]
    assert kept == old[-len(kept):]  # a contiguous block of the newest messages
    assert 0 < len(kept) < len(old)


def test_tight_limit_trims_history_before_failing() -> None:
    task = task_with("Frage")
    required = estimate_messages([ChatMessage("system", SYSTEM), *task.messages])
    built = ContextManager().build(SYSTEM, task, history(10), input_limit=required + 60)
    assert built.input_tokens <= required + 60 and built.history_tokens <= 60
    with pytest.raises(ContextTooLarge):
        ContextManager().build(SYSTEM, task, history(10), input_limit=required - 1)


def test_memory_has_priority_over_history_but_never_blocks() -> None:
    memory = memory_block(400)  # ~100 tokens, below the 800-token recall budget
    task = task_with("Frage", memory)
    required = estimate_messages([ChatMessage("system", SYSTEM), task.messages[0]])
    with_room = ContextManager().build(SYSTEM, task, history(10), input_limit=required + 110)
    assert memory in with_room.messages and with_room.history_tokens == 0  # history goes first
    no_room = ContextManager().build(SYSTEM, task, history(10), input_limit=required + 10)
    assert memory not in no_room.messages and no_room.memory_skipped  # then memory, never the task
    assert no_room.messages[-1].content == "Frage"


def test_follow_up_keeps_goal_and_current_input_pinned() -> None:
    task = task_with("Mach mal einen Plan.", ChatMessage("assistant", "x" * 8000),
                     ChatMessage("user", "Für Python", origin="user"))
    built = ContextManager().build(SYSTEM, task, history(4), input_limit=100_000)
    contents = [m.content for m in built.messages]
    assert "[TASK GOAL] Mach mal einen Plan." in contents and contents[-1] == "Für Python"
    assert "x" * 8000 not in contents  # 2000-token task message exceeds the history cap


# --- through the engine -----------------------------------------------------


def make(atlas, sage=None, *, memory=None, context_window=8192, script=None):
    agents = {"atlas": atlas, **({"sage": sage} if sage else {})}
    engine = TaskEngine(agents, MockProvider(script, context_window=context_window), memory=memory)
    return ChatSession(engine), engine.provider


def test_long_dialog_stays_within_limits(atlas) -> None:
    session, provider = make(atlas)
    for i in range(20):
        assert session.send(f"Frage {i}: " + "x" * 400).task.status is TaskStatus.DONE
    limit = provider.context_window - atlas.llm.max_output_tokens
    for call in provider.calls:
        assert estimate_messages(call) <= limit
        assert estimate_messages(call[1:-1]) <= HISTORY_BUDGET_TOKENS  # history between system and input
    last = provider.calls[-1]
    assert last[0].content == session.engine.runtimes["atlas"].system_prompt
    assert last[-1].content.startswith("Frage 19:")
    assert all("Frage 0:" not in m.content for m in last)  # oldest dropped


def test_large_accepted_input_does_not_block_short_follow_ups(atlas) -> None:
    session, provider = make(atlas)
    large = session.send("Bitte lies das: " + "y" * 20_000)  # ~5000 tokens, fits the window
    assert large.task.status is TaskStatus.DONE
    follow_up = session.send("Hallo")
    assert follow_up.task.status is TaskStatus.DONE
    assert all("y" * 1000 not in m.content for m in provider.calls[-1])


def test_oversized_required_context_fails_controlled(atlas) -> None:
    session, provider = make(atlas)
    reply = session.send("z" * 40_000)
    assert reply.task.failure_reason is FailureReason.INPUT_TOO_LARGE and provider.calls == []


def test_memory_block_is_skipped_when_it_exceeds_the_input_limit(atlas, sage, mvp_cases) -> None:
    store = SQLiteMemoryStore(":memory:")
    MemoryAccess(atlas, store).write(  # ~265 tokens: below the 800-token recall budget
        kind="episode", content="Plan: " + "p" * 1050, source_task_id="old",
        subject=normalize_subject(mvp_cases["B"]), reusable=True,
    )
    probe, probe_provider = make(atlas, sage, memory=store)
    assert probe.send(mvp_cases["B"]).llm_calls == 1  # memory fits: reused
    full = estimate_messages(probe_provider.calls[0])

    window = full - 1 + atlas.llm.max_output_tokens  # required context fits, memory does not
    session, provider = make(atlas, sage, memory=store, context_window=window)
    reply = session.send(mvp_cases["B"])
    assert reply.task.status is TaskStatus.DONE and reply.llm_calls == 3  # normal delegation
    assert not any(m.content.startswith(MEMORY_PREFIX) for m in provider.calls[0])
    _, messages = session.engine.protocol(reply.task.root_id)
    assert any(m.content.startswith("skipped") for m in messages)
    store.close()
