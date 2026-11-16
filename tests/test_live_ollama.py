"""Phase 9 acceptance with the real local model (roadmap cases A-D).

Runs only with MYRIAD_LIVE=1 and needs a running Ollama with the model from config/models.yaml.
A real model is not deterministic: these tests document its behaviour against the contract.
"""

from __future__ import annotations

import os
import re

import pytest

from myriad.agents.definition import load_agent
from myriad.config import build_provider, load_context, load_model_config
from myriad.executors.service import CodingOutcome
from myriad.models import FailureReason
from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession
from myriad.memory.store import SQLiteMemoryStore
from myriad.models import Task, TaskStatus
from myriad.skills.loader import load_skills

from conftest import AGENTS_DIR

pytestmark = pytest.mark.skipif(os.environ.get("MYRIAD_LIVE") != "1", reason="live test: set MYRIAD_LIVE=1")

CASES = {
    "A": "What can you do for me?",
    "B": "Plan a 4-week learning project for Python.",
    "C": "Make me a plan.",
}


@pytest.fixture
def make_session(atlas, sage, tmp_path):
    config = load_model_config(AGENTS_DIR.parent / "config" / "models.yaml")
    context = load_context(AGENTS_DIR.parent / "config" / "context.md")  # same setup as the CLI
    skills = load_skills(AGENTS_DIR.parent / "skills")
    stores: list[SQLiteMemoryStore] = []

    forge = load_agent(AGENTS_DIR / "forge.yaml")  # the team as the CLI loads it

    class RefusingCoder:  # no case here is a coding task; Claude Code is never started
        def run(self, task, brief, instructions) -> CodingOutcome:
            return CodingOutcome(False, "Live test: Forge is not available here.",
                                 reason=FailureReason.EXECUTOR_ERROR)

    def factory(agents=None) -> ChatSession:
        agents = agents or {"atlas": atlas, "sage": sage}
        agents = {**agents, "forge": forge}
        store = SQLiteMemoryStore(tmp_path / "live.db")
        stores.append(store)
        provider = build_provider("ollama", config, agents)
        return ChatSession(TaskEngine(agents, provider, memory=store, context=context, skills=skills,
                                      executors={"coding": RefusingCoder()}))

    yield factory
    for store in stores:
        store.close()


def children(session: ChatSession) -> list[Task]:
    return [t for t in session.engine.tasks.values() if not t.is_root]


def report(name: str, session: ChatSession, reply) -> None:
    tasks, messages = session.engine.protocol(reply.task.root_id)
    print(f"\n--- {name}: {reply.task.status} ({reply.task.failure_reason}) · calls {reply.llm_calls} "
          f"· tokens {reply.tree_tokens}")
    print(f"answer: {reply.text[:600]}")
    for task in tasks:
        print(f"  task {task.assigned_to}: {task.status}, {task.steps} steps, {task.corrections} corrections")
    for note in reply.notices:
        print(f"  notice: {note[:200]}")
    if reply.task.status is not TaskStatus.DONE:
        last = next((m for m in reversed(reply.task.messages) if m.role == "assistant"), None)
        print(f"  last raw decision: {last.content[:400] if last else '-'}")


def plan_steps(text: str) -> int:
    numbered = len(re.findall(r"(?:^|\s)\d+[.)]\s", text))
    bullets = len(re.findall(r"(?m)^\s*[-*•]\s", text))
    weeks = len(re.findall(r"\bweek\s*\d", text, re.IGNORECASE))
    return max(numbered, bullets, weeks)


def test_case_a_simple_question_is_answered_directly(make_session) -> None:
    session = make_session()
    reply = session.send(CASES["A"])
    report("A", session, reply)
    assert reply.task.status is TaskStatus.DONE
    assert reply.llm_calls == 1 and children(session) == []


def test_case_b_specialist_task_is_delegated_to_sage(make_session) -> None:
    session = make_session()
    reply = session.send(CASES["B"])
    report("B", session, reply)
    assert reply.task.status is TaskStatus.DONE
    assert [c.status for c in children(session)] == [TaskStatus.DONE]
    assert reply.llm_calls <= 3 and reply.tree_tokens <= 15_000
    assert plan_steps(reply.text) >= 3


def test_case_c_unclear_request_gets_a_question(make_session) -> None:
    session = make_session()
    reply = session.send(CASES["C"])
    report("C", session, reply)
    assert reply.task.status is TaskStatus.NEEDS_INPUT and reply.llm_calls == 1


def test_case_d_failed_delegation_is_reported(make_session, atlas, sage) -> None:
    broken_sage = sage.model_copy(update={"limits": sage.limits.model_copy(update={"max_steps_per_task": 0})})
    session = make_session({"atlas": atlas, "sage": broken_sage})  # Sage fails before any model call
    reply = session.send(CASES["B"])
    report("D", session, reply)
    assert reply.task.status is not TaskStatus.DONE
    assert any("'sage'" in note for note in reply.notices)


def test_repeated_request_is_answered_from_memory(make_session) -> None:
    first_session = make_session()
    first = first_session.send(CASES["B"])
    report("B (first)", first_session, first)
    assert first.task.status is TaskStatus.DONE
    session = make_session()  # new engine, same database file
    reply = session.send(CASES["B"])
    report("B (repeat)", session, reply)
    assert reply.task.status is TaskStatus.DONE and reply.llm_calls == 1 and children(session) == []
