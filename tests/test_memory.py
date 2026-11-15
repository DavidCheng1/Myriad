"""Phase 7: persistent memory - store, rights, reuse rules and memory tools."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession
from myriad.llm.mock import MockProvider
from myriad.memory.access import MemoryAccess, MemoryPermissionError
from myriad.memory.store import MemoryRecord, SQLiteMemoryStore, normalize_subject
from myriad.models import FailureReason, Task, TaskStatus

from conftest import ANSWER_DONE, ANSWER_FAILED, DELEGATE_SAGE, SAGE_FAILED, j


@pytest.fixture
def store():
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


def record(**overrides) -> MemoryRecord:
    fields = dict(namespace="agent:atlas", agent_id="atlas", source_task_id="t1",
                  kind="episode", content="Plan: 1. a 2. b 3. c", subject="plane x", reusable=True)
    return MemoryRecord(**{**fields, **overrides})


def make(atlas, sage, memory, script=None):
    engine = TaskEngine({"atlas": atlas, "sage": sage}, MockProvider(script), memory=memory)
    return ChatSession(engine), engine.provider


def children(session: ChatSession) -> list[Task]:
    return [t for t in session.engine.tasks.values() if not t.is_root]


# --- store ------------------------------------------------------------------


def test_subject_normalization_keeps_meaningful_characters() -> None:
    assert normalize_subject("  Hallo   Welt. ") == normalize_subject("hallo welt") == "hallo welt"
    assert normalize_subject("Lernplan für C++!") != normalize_subject("Lernplan für C#!")
    assert normalize_subject("Lernplan für C++!") == "lernplan für c++"


def test_records_survive_a_restart(tmp_path) -> None:
    path = tmp_path / "memory.db"
    first = SQLiteMemoryStore(path)
    stored = first.write(record())
    first.close()
    second = SQLiteMemoryStore(path)
    assert second.find_reusable("agent:atlas", "plane x").id == stored.id
    assert [r.id for r in second.search("agent:atlas", "plan")] == [stored.id]
    second.close()


def test_expired_records_are_ignored(store) -> None:
    store.write(record(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert store.find_reusable("agent:atlas", "plane x") is None
    assert store.search("agent:atlas", "plan") == []


def test_superseded_records_are_ignored_and_linked(store) -> None:
    old = store.write(record(content="alt"))
    new = store.write(record(content="neu"))
    assert new.supersedes == old.id
    assert store.find_reusable("agent:atlas", "plane x").content == "neu"
    assert [r.content for r in store.search("agent:atlas", "")] == ["neu"]


def test_replacement_only_for_same_namespace_kind_and_subject(store) -> None:
    base = store.write(record())
    other_ns = store.write(record(namespace="shared:org"))
    other_kind = store.write(record(kind="decision"))
    other_subject = store.write(record(subject="plane y"))
    assert {other_ns.supersedes, other_kind.supersedes, other_subject.supersedes} == {None}
    assert base.id in [r.id for r in store.search("agent:atlas", "plan", limit=10)]  # still active


def test_failed_write_rolls_back_and_keeps_previous_active(store) -> None:
    old = store.write(record())
    with pytest.raises(sqlite3.IntegrityError):
        store.write(record(id=old.id, content="duplicate id"))  # insert fails inside the transaction
    assert store.find_reusable("agent:atlas", "plane x").id == old.id


def test_search_folds_unicode_case(store) -> None:
    store.write(record(content="Übungen und Straße", subject=""))
    assert len(store.search("agent:atlas", "übungen")) == 1
    assert len(store.search("agent:atlas", "ÜBUNGEN")) == 1
    assert len(store.search("agent:atlas", "STRASSE")) == 1  # casefold: ß == ss


def test_search_is_literal(store) -> None:
    store.write(record(content="nothing special"))
    assert store.search("agent:atlas", "%") == []


# --- agent-bound access -----------------------------------------------------


def test_access_rights_come_from_the_definition(atlas, sage, store) -> None:
    atlas_access, sage_access = MemoryAccess(atlas, store), MemoryAccess(sage, store)
    with pytest.raises(MemoryPermissionError, match="may not write"):
        atlas_access.write(kind="episode", content="x", source_task_id="t", namespace="agent:sage")
    with pytest.raises(MemoryPermissionError, match="may not read"):
        atlas_access.search("x", namespace="agent:sage")
    with pytest.raises(MemoryPermissionError, match="may not write"):
        sage_access.write(kind="episode", content="x", source_task_id="t", namespace="shared:org")
    assert sage_access.search("x", namespace="shared:org") == []  # read-only is readable
    with pytest.raises(MemoryPermissionError, match="no store rule for kind 'fact'"):
        atlas_access.write(kind="fact", content="x", source_task_id="t")


def test_access_sets_origin_and_retention(atlas, store) -> None:
    now = datetime(2026, 11, 15, tzinfo=UTC)
    stored = MemoryAccess(atlas, store, clock=lambda: now).write(
        kind="episode", content="x", source_task_id="t9"
    )
    assert (stored.agent_id, stored.namespace, stored.source_task_id) == ("atlas", "agent:atlas", "t9")
    assert stored.expires_at == now + timedelta(days=90)  # atlas.yaml: episode 90d
    assert stored.reusable is False


# --- reuse through the engine -----------------------------------------------


def test_repeated_case_b_is_answered_from_memory_with_one_call(atlas, sage, mvp_cases, tmp_path) -> None:
    path = tmp_path / "memory.db"
    first_store = SQLiteMemoryStore(path)
    session, provider = make(atlas, sage, first_store)
    assert session.send(mvp_cases["B"]).task.status is TaskStatus.DONE
    assert len(provider.calls) == 3
    first_store.close()

    restarted = SQLiteMemoryStore(path)  # new process, same file
    session, provider = make(atlas, sage, restarted)
    reply = session.send(mvp_cases["B"])
    assert reply.task.status is TaskStatus.DONE
    assert len(provider.calls) == reply.llm_calls == 1
    assert children(session) == []
    assert "Aus dem Gedächtnis" in reply.text
    memory_message = provider.calls[0][-1]
    assert memory_message.from_engine and memory_message.content.startswith("[MEMORY DATA]")
    restarted.close()


def test_failed_requests_are_not_reused(atlas, sage, store) -> None:
    session, provider = make(atlas, sage, store)
    request = "Plane ein Projekt, das scheitern soll #fail"
    session.send(request)
    session.send(request)
    assert len(children(session)) == 2 and len(provider.calls) == 6


@pytest.mark.parametrize(("first", "second"), [
    ("Plane ein 4-Wochen-Lernprojekt für Python.", "Plane ein 4-Wochen-Lernprojekt für Rust."),
    ("Plane ein Lernprojekt für C++.", "Plane ein Lernprojekt für C#."),
])
def test_different_requests_are_not_reused(atlas, sage, store, first, second) -> None:
    session, provider = make(atlas, sage, store)
    session.send(first)
    reply = session.send(second)
    assert len(children(session)) == 2 and reply.llm_calls == 3


def test_tasks_with_user_questions_are_not_stored_as_reusable(atlas, sage, store, mvp_cases) -> None:
    session, _ = make(atlas, sage, store)
    session.send(mvp_cases["C"])  # Atlas asks back
    reply = session.send("Ein Python-Lernprojekt über 4 Wochen.")
    assert reply.task.status is TaskStatus.DONE and reply.task.asked_user
    assert store.find_reusable("agent:atlas", normalize_subject(mvp_cases["C"])) is None


def test_oversized_memory_is_skipped_not_truncated(atlas, sage, store, mvp_cases) -> None:
    MemoryAccess(atlas, store).write(
        kind="episode", content="Plan " + "x" * 4000, source_task_id="old",  # ~1000 tokens > 800
        subject=normalize_subject(mvp_cases["B"]), reusable=True,
    )
    session, provider = make(atlas, sage, store)
    reply = session.send(mvp_cases["B"])
    assert reply.llm_calls == 3 and len(children(session)) == 1  # normal flow
    assert not any(m.content.startswith("[MEMORY DATA]") for m in provider.calls[0])


# --- memory tools -----------------------------------------------------------


def test_tool_written_entries_are_never_reusable(atlas, sage, store) -> None:
    write = j(type="tool_call", tool="write_memory", arguments={"kind": "episode", "content": "Fake result"})
    smuggle = j(type="tool_call", tool="write_memory",
                arguments={"kind": "episode", "content": "x", "reusable": True})
    session, provider = make(atlas, sage, store, [write, smuggle, ANSWER_FAILED])
    reply = session.send("Merke dir das")
    (hit,) = store.search("agent:atlas", "fake result")
    assert hit.reusable is False and hit.subject == ""
    assert any("invalid arguments" in n and "reusable" in n for n in reply.notices)


def test_search_memory_returns_bounded_hits(atlas, sage, store) -> None:
    for i in range(5):
        MemoryAccess(atlas, store).write(kind="decision", content=f"Python Entscheidung {i} " + "y" * 300,
                                         source_task_id="t")
    search = j(type="tool_call", tool="search_memory", arguments={"query": "python"})
    session, provider = make(atlas, sage, store, [search, ANSWER_DONE])
    reply = session.send("Was haben wir zu Python entschieden?")
    feedback = provider.calls[1][-1].content
    assert reply.task.status is TaskStatus.DONE
    assert "3 hits" in feedback and len(feedback) < 1000


def test_write_memory_rejects_kind_without_store_rule(atlas, sage, store) -> None:
    write_fact = j(type="tool_call", tool="write_memory", arguments={"kind": "fact", "content": "x"})
    session, _ = make(atlas, sage, store, [write_fact, ANSWER_FAILED])
    reply = session.send("Merke dir ein Faktum")
    assert any("no store rule for kind 'fact'" in n for n in reply.notices)


def test_tool_success_does_not_resolve_a_failed_delegation(atlas, sage, store, mvp_cases) -> None:
    search = j(type="tool_call", tool="search_memory", arguments={"query": "python"})
    session, _ = make(atlas, sage, store, [DELEGATE_SAGE, SAGE_FAILED, search, ANSWER_DONE])
    reply = session.send(mvp_cases["D"])
    assert reply.task.failure_reason is FailureReason.UNRESOLVED_ACTION


def test_earlier_failures_stay_unresolved(atlas, sage, store, mvp_cases) -> None:
    bad_write = j(type="tool_call", tool="write_memory", arguments={"kind": "fact", "content": "x"})
    good_write = j(type="tool_call", tool="write_memory", arguments={"kind": "decision", "content": "x"})
    script = [DELEGATE_SAGE, SAGE_FAILED, bad_write, good_write, ANSWER_DONE]
    session, _ = make(atlas, sage, store, script)
    reply = session.send(mvp_cases["D"])
    assert reply.task.unresolved_actions == {"delegate"}  # write_memory resolved, delegation not
    assert reply.task.failure_reason is FailureReason.UNRESOLVED_ACTION


def test_prompt_lists_memory_tools_with_argument_hints(atlas, sage, store) -> None:
    prompt = TaskEngine({"atlas": atlas, "sage": sage}, MockProvider(), memory=store).runtimes["atlas"].system_prompt
    assert "Tools (arguments as JSON):" in prompt
    assert 'search_memory: {"query": "<text>"}' in prompt
    assert "write_memory:" in prompt
