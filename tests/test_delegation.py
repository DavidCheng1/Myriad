"""Phase 6: delegation from Atlas to Sage - cases B and D, brief-only context, limits, protocol."""

from __future__ import annotations

import re

from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession
from myriad.llm.mock import MockProvider
from myriad.models import ENGINE_PREFIX, FailureReason, MessageType, Task, TaskStatus

from conftest import ANSWER_DONE, ANSWER_FAILED, ASK, BRIEF, DELEGATE_SAGE, SAGE_FAILED, SAGE_PLAN, j


def make(atlas, sage, script=None, **atlas_limits):
    root = atlas.model_copy(update={"limits": atlas.limits.model_copy(update=atlas_limits)})
    provider = MockProvider(script)
    engine = TaskEngine({"atlas": root, "sage": sage}, provider)
    return ChatSession(engine), provider


def children(session: ChatSession) -> list[Task]:
    return [t for t in session.engine.tasks.values() if not t.is_root]


# --- case B -----------------------------------------------------------------


def test_case_b_atlas_delegates_to_sage_and_answers_itself(atlas, sage, mvp_cases) -> None:
    session, provider = make(atlas, sage)
    reply = session.send(mvp_cases["B"])
    (child,) = children(session)
    assert reply.task.status is TaskStatus.DONE
    assert len(provider.calls) == reply.llm_calls == 3  # Atlas -> Sage -> Atlas
    assert len(re.findall(r"\b\d\. ", reply.text)) >= 3  # plan with at least 3 steps
    assert child.status is TaskStatus.DONE
    assert (child.parent_id, child.root_id, child.depth) == (reply.task.id, reply.task.root_id, 1)
    assert (child.assigned_to, child.created_by) == ("sage", "atlas")
    assert child.brief is not None and child.brief.goal == mvp_cases["B"]


def test_case_b_scripted(atlas, sage, mvp_cases, mock_scripts) -> None:
    session, provider = make(atlas, sage, mock_scripts["B"])
    reply = session.send(mvp_cases["B"])
    assert reply.task.status is TaskStatus.DONE and len(provider.calls) == 3


def test_sage_receives_only_the_brief(atlas, sage, mvp_cases) -> None:
    session, provider = make(atlas, sage)
    session.send(mvp_cases["A"])  # earlier conversation that Sage must not see
    session.send(mvp_cases["B"])
    sage_call = provider.calls[2]
    assert [m.role for m in sage_call] == ["system", "user"]
    assert sage_call[0].content == session.engine.runtimes["sage"].system_prompt
    assert sage_call[1].content.startswith("[BRIEF from atlas]")
    assert all(mvp_cases["A"] not in m.content for m in sage_call)


def test_sage_usage_counts_against_the_tree_budget(atlas, sage, mvp_cases) -> None:
    session, _ = make(atlas, sage)
    reply = session.send(mvp_cases["B"])
    records = session.engine.tracker.records
    assert [r.agent_id for r in records] == ["atlas", "sage", "atlas"]
    assert all(r.root_id == reply.task.root_id for r in records)
    assert reply.tree_tokens == sum(r.total for r in records)


# --- case D and the relaxed prototype rule ----------------------------------


def test_case_d_sage_failure_is_reported_transparently(atlas, sage, mvp_cases, mock_scripts) -> None:
    session, provider = make(atlas, sage, mock_scripts["D"])
    reply = session.send(mvp_cases["D"])
    (child,) = children(session)
    assert child.failure_reason is FailureReason.REPORTED_BY_AGENT
    assert reply.task.failure_reason is FailureReason.REPORTED_BY_AGENT
    assert any("'sage'" in n and "failed" in n for n in reply.notices)
    assert len(provider.calls) == 3


def test_case_d_with_rule_mock(atlas, sage) -> None:
    session, _ = make(atlas, sage)
    reply = session.send("Plane ein Projekt, das scheitern soll #fail")
    assert reply.task.status is TaskStatus.FAILED
    assert children(session)[0].status is TaskStatus.FAILED


def test_claimed_success_after_failed_child_is_not_done(atlas, sage, mvp_cases) -> None:
    session, _ = make(atlas, sage, [DELEGATE_SAGE, SAGE_FAILED, ANSWER_DONE])
    reply = session.send(mvp_cases["D"])
    assert reply.task.failure_reason is FailureReason.UNRESOLVED_ACTION
    assert any(n.startswith("Request not fulfilled") for n in reply.notices)


def test_success_after_a_failed_attempt_can_be_done(atlas, sage, mvp_cases) -> None:
    script = [DELEGATE_SAGE, SAGE_FAILED, DELEGATE_SAGE, SAGE_PLAN, ANSWER_DONE]
    session, provider = make(atlas, sage, script)
    reply = session.send(mvp_cases["B"])
    assert reply.task.status is TaskStatus.DONE
    assert [c.status for c in children(session)] == [TaskStatus.FAILED, TaskStatus.DONE]
    assert len(provider.calls) == 5 and len(reply.notices) == 1


def test_sage_needs_input_round_trip_keeps_root_and_budget(atlas, sage, mvp_cases) -> None:
    needs_input = j(type="answer", outcome="needs_input", text="[SIMULIERT] Wie viele Stunden pro Woche?")
    augmented = {**BRIEF, "context": "User: 2 hours per week."}
    script = [DELEGATE_SAGE, needs_input, ASK, j(type="delegate", agent="sage", brief=augmented),
              SAGE_PLAN, ANSWER_DONE]
    session, provider = make(atlas, sage, script)

    first = session.send(mvp_cases["B"])
    root_id, used = first.task.root_id, first.tree_tokens
    assert first.task.status is TaskStatus.NEEDS_INPUT
    assert children(session)[0].status is TaskStatus.NEEDS_INPUT
    assert any("needs more information" in n for n in first.notices)

    second = session.send("2 Stunden pro Woche")
    assert second.task.root_id == root_id and second.task is first.task
    assert second.tree_tokens == used + second.turn_tokens > used
    retry = children(session)[1]
    assert retry.brief is not None and retry.brief.context == "User: 2 hours per week."
    assert retry.status is TaskStatus.DONE and second.task.status is TaskStatus.DONE
    assert len(provider.calls) == 6


# --- Sage's rights and limits -----------------------------------------------


def test_sage_may_not_ask_the_user_or_delegate(atlas, sage, mvp_cases) -> None:
    script = [DELEGATE_SAGE, ASK, j(type="delegate", agent="atlas", brief=BRIEF), SAGE_PLAN, ANSWER_DONE]
    session, provider = make(atlas, sage, script)
    reply = session.send(mvp_cases["B"])
    (child,) = children(session)
    assert child.status is TaskStatus.DONE and child.corrections == 2
    assert reply.task.status is TaskStatus.DONE
    assert "only allowed in the root task" in provider.calls[2][-1].content
    assert "not permitted" in provider.calls[3][-1].content


def test_invalid_sage_output_fails_the_child_and_atlas_is_informed(atlas, sage, mvp_cases) -> None:
    session, provider = make(atlas, sage, [DELEGATE_SAGE, "x", "y", "z", ANSWER_FAILED])
    reply = session.send(mvp_cases["B"])
    (child,) = children(session)
    assert child.failure_reason is FailureReason.INVALID_OUTPUT
    feedback = provider.calls[-1][-1].content
    assert feedback.startswith(ENGINE_PREFIX) and "invalid_output" in feedback
    assert reply.task.status is TaskStatus.FAILED


def test_brief_output_limit_is_enforced_for_sage(atlas, sage, mvp_cases) -> None:
    short_brief = {**BRIEF, "output_limit": 20}
    short_plan = j(type="answer", outcome="done", text="1. a 2. b 3. c")
    script = [j(type="delegate", agent="sage", brief=short_brief), SAGE_PLAN, short_plan, ANSWER_DONE]
    session, provider = make(atlas, sage, script)
    reply = session.send(mvp_cases["B"])
    (child,) = children(session)
    assert child.status is TaskStatus.DONE and child.corrections == 1
    assert "> 20)" in provider.calls[2][-1].content
    assert reply.task.status is TaskStatus.DONE


def test_budget_can_run_out_inside_the_child(atlas, sage) -> None:
    script = [DELEGATE_SAGE, SAGE_PLAN, ANSWER_DONE]
    request = "Plane etwas Größeres für mich, bitte."
    probe, _ = make(atlas, sage, list(script))
    probe.send(request)
    first_call, sage_call = probe.engine.tracker.records[:2]
    budget = first_call.total + sage_call.input_tokens + BRIEF["output_limit"] - 1

    session, provider = make(atlas, sage, list(script), token_budget_task_tree=budget)
    reply = session.send(request)
    (child,) = children(session)
    assert child.failure_reason is FailureReason.BUDGET_EXHAUSTED
    assert reply.task.failure_reason is FailureReason.BUDGET_EXHAUSTED
    assert len(provider.calls) == 1


# --- protocol and prompt ----------------------------------------------------


def test_protocol_records_request_and_response(atlas, sage, mvp_cases) -> None:
    session, _ = make(atlas, sage)
    reply = session.send(mvp_cases["B"])
    tasks, messages = session.engine.protocol(reply.task.root_id)
    assert [t.assigned_to for t in tasks] == ["atlas", "sage"]
    request, response = messages
    assert (request.type, request.sender, request.recipient) == (MessageType.REQUEST, "atlas", "sage")
    assert mvp_cases["B"] in request.content
    assert (response.type, response.sender, response.recipient) == (MessageType.RESPONSE, "sage", "atlas")
    assert response.content.startswith("done: ")


def test_prompt_lists_only_loaded_delegation_targets(atlas, sage) -> None:
    with_sage = TaskEngine({"atlas": atlas, "sage": sage}, MockProvider()).runtimes["atlas"]
    atlas_only = TaskEngine({"atlas": atlas}, MockProvider()).runtimes["atlas"]
    assert "- sage (planner): " in with_sage.system_prompt
    assert "You may delegate to: nobody." in atlas_only.system_prompt
    assert "Tools: none" in with_sage.system_prompt  # memory tools follow in phase 7
