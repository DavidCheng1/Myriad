"""Quality round: native tool calls, thinking, context, examples, briefs and memory substance."""

from __future__ import annotations

from myriad.agents.definition import render_system_prompt
from myriad.agents.runtime import AgentRuntime
from myriad.config import load_context, load_model_config
from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession
from myriad.llm.actions import action_specs, decision_from_text, decision_from_tool_call
from myriad.llm.mock import MockProvider
from myriad.llm.ollama import OllamaProvider
from myriad.memory.store import SQLiteMemoryStore, normalize_subject
from myriad.models import (
    ENGINE_PREFIX,
    TOOL_FORMAT,
    Answer,
    AskUser,
    ChatMessage,
    Delegate,
    Task,
    TaskStatus,
    ToolCall,
    parse_decision,
)

from conftest import AGENTS_DIR, ANSWER_DONE, BRIEF, DELEGATE_SAGE, SAGE_PLAN, j

CONTEXT = "MYRIAD is a local multi-agent system. Agents are AI agents, never people."


class FakeTransport:
    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> dict:
        self.requests.append(payload)
        return self.responses.pop(0)


def reply(content: str = "", tool: tuple[str, dict] | None = None, **extra: object) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [{"function": {"name": tool[0], "arguments": tool[1]}}]
    return {"message": message, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 20, **extra}


def names(specs: list[dict]) -> list[str]:
    return [s["function"]["name"] for s in specs]


# --- tool specs and mapping ---------------------------------------------------


def test_root_and_child_get_different_actions(atlas, sage) -> None:
    team = {"atlas": atlas, "sage": sage}
    root = Task(goal="g", assigned_to="atlas", created_by="user")
    atlas_tools = names(action_specs(atlas, root, team, {"search_memory", "write_memory"}))
    assert atlas_tools == ["ask_user", "report_failure", "delegate", "search_memory", "write_memory"]
    child = root.spawn_child("sage", parse_decision(DELEGATE_SAGE)[0].brief)
    assert names(action_specs(sage, child, team, set())) == ["need_more_info", "report_failure"]


def test_delegate_lists_only_loaded_specialists(atlas, sage) -> None:
    root = Task(goal="g", assigned_to="atlas", created_by="user")
    spec = next(s for s in action_specs(atlas, root, {"atlas": atlas, "sage": sage}, set())
                if s["function"]["name"] == "delegate")
    assert spec["function"]["parameters"]["properties"]["agent"]["enum"] == ["sage"]
    assert "delegate" not in names(action_specs(atlas, root, {"atlas": atlas}, set()))


def test_tool_calls_map_to_valid_decisions() -> None:
    cases = [
        ("ask_user", '{"question": "Which deadline?"}', AskUser),  # JSON-string arguments
        ("need_more_info", {"question": "Missing scope"}, Answer),
        ("report_failure", {"reason": "Impossible"}, Answer),
        ("delegate", {"agent": "sage", "goal": "g", "context": "c", "expected_output": "e",
                      "output_limit": "300"}, Delegate),  # numeric string is accepted
        ("search_memory", {"query": "python"}, ToolCall),
    ]
    for name, arguments, expected in cases:
        decision, error = parse_decision(decision_from_tool_call(name, arguments))
        assert error is None and isinstance(decision, expected), name
    outcomes = [parse_decision(decision_from_tool_call(n, {"question": "q", "reason": "r"}))[0].outcome
                for n in ("need_more_info", "report_failure")]
    assert outcomes == ["needs_input", "failed"]
    assert parse_decision(decision_from_text("Hello"))[0] == Answer(type="answer", outcome="done", text="Hello")


def test_missing_output_limit_gets_a_default_and_missing_goal_is_rejected() -> None:
    decision, _ = parse_decision(decision_from_tool_call(
        "delegate", {"agent": "sage", "goal": "g", "context": "", "expected_output": "e"}))
    assert decision.brief.output_limit == 400
    assert parse_decision(decision_from_tool_call("delegate", {"agent": "sage"}))[1] is not None


# --- Ollama in tools mode -----------------------------------------------------


def test_tools_mode_sends_tools_and_free_text_answers(atlas) -> None:
    transport = FakeTransport(reply("SQLite, because it needs no server."))
    provider = OllamaProvider("m", decision_mode="tools", transport=transport)
    specs = [{"type": "function", "function": {"name": "ask_user"}}]
    completion = provider.complete([ChatMessage("user", "Hi", origin="user")], 400, tools=specs)
    payload = transport.requests[0]
    assert payload["tools"] == specs and "format" not in payload
    assert parse_decision(completion.text)[0].text == "SQLite, because it needs no server."


def test_tools_mode_maps_tool_calls_and_empty_replies() -> None:
    call = reply(tool=("delegate", {"agent": "sage", "goal": "g", "context": "c",
                                    "expected_output": "e", "output_limit": 300}))
    provider = OllamaProvider("m", decision_mode="tools", transport=FakeTransport(call, reply("")))
    delegate = parse_decision(provider.complete([ChatMessage("user", "x")], 400).text)[0]
    assert isinstance(delegate, Delegate) and delegate.brief.goal == "g"
    assert parse_decision(provider.complete([ChatMessage("user", "x")], 400).text)[1] is not None


def test_tools_mode_renders_history_as_tool_calls() -> None:
    history = [
        ChatMessage("user", "Plan it", origin="user"),
        ChatMessage("assistant", DELEGATE_SAGE),
        ChatMessage("user", f"{ENGINE_PREFIX} Result from 'sage': plan", origin="engine"),
        ChatMessage("assistant", j(type="ask_user", question="Which week?")),
        ChatMessage("user", "Week 2", origin="user"),
    ]
    transport = FakeTransport(reply("ok"))
    OllamaProvider("m", decision_mode="tools", transport=transport).complete(history, 400)
    rendered = transport.requests[0]["messages"]
    assert rendered[1]["tool_calls"][0]["function"]["name"] == "delegate"
    assert rendered[1]["tool_calls"][0]["function"]["arguments"]["goal"] == BRIEF["goal"]
    assert rendered[2]["role"] == "tool"
    assert rendered[3] == {"role": "assistant", "content": "Which week?"}  # questions stay plain text
    assert rendered[4] == {"role": "user", "content": "Week 2"}


def test_thinking_reserves_extra_tokens_and_limits_only_the_visible_answer() -> None:
    transport = FakeTransport(reply("Short plan.", eval_count=900))  # mostly hidden reasoning
    completion = OllamaProvider("m", decision_mode="tools", transport=transport).complete(
        [ChatMessage("user", "x")], 300, think=True, thinking_budget=1024)
    assert transport.requests[0]["think"] is True
    assert transport.requests[0]["options"]["num_predict"] == 1324
    assert completion.output_tokens == 900 and completion.answer_tokens < 300


# --- runtime, prompt, engine --------------------------------------------------


def test_runtime_in_tools_mode_uses_tool_format_and_reserves_thinking(atlas, sage) -> None:
    provider = OllamaProvider("m", decision_mode="tools", transport=FakeTransport())
    runtime = AgentRuntime(sage, provider, team={"sage": sage})
    assert runtime.system_prompt.endswith(TOOL_FORMAT)
    assert "plain text or call exactly one" in runtime.correction_hint
    child = Task(goal="g", assigned_to="sage", created_by="atlas",
                 brief=parse_decision(DELEGATE_SAGE)[0].brief, parent_id="p")
    assert runtime.max_output(child) == 400 and runtime.reserved_output(child) == 400 + 1024


def test_prompt_renders_context_team_cards_and_examples(atlas, sage) -> None:
    prompt = render_system_prompt(atlas, "F", team={"atlas": atlas, "sage": sage}, context=CONTEXT)
    assert prompt.index("## Context") < prompt.index("## Character")
    assert CONTEXT in prompt
    assert "- sage (planner): Turns the goal of a brief" in prompt and "Not for:" in prompt
    assert "## Examples" in prompt and atlas.behavior.examples[0] in prompt


def test_every_agent_sees_the_organization_context(atlas, sage) -> None:
    engine = TaskEngine({"atlas": atlas, "sage": sage}, MockProvider(), context=CONTEXT)
    assert all(CONTEXT in runtime.system_prompt for runtime in engine.runtimes.values())
    assert "never human employees" in load_context(AGENTS_DIR.parent / "config" / "context.md")
    assert load_context(AGENTS_DIR / "missing.md") == ""


def test_brief_carries_the_verbatim_user_request(atlas, sage, mvp_cases) -> None:
    session = ChatSession(TaskEngine({"atlas": atlas, "sage": sage},
                                     MockProvider([DELEGATE_SAGE, SAGE_PLAN, ANSWER_DONE])))
    session.send(mvp_cases["B"])
    sage_call = session.engine.provider.calls[1]
    assert f"User request (verbatim): {mvp_cases['B']}" in sage_call[-1].content


def test_short_results_are_not_stored_as_reusable(atlas, sage, mvp_cases) -> None:
    store = SQLiteMemoryStore(":memory:")
    short = j(type="answer", outcome="done", text="[SIMULIERT] Sage is ready to help.")
    session = ChatSession(TaskEngine({"atlas": atlas, "sage": sage},
                                     MockProvider([DELEGATE_SAGE, SAGE_PLAN, short]), memory=store))
    assert session.send(mvp_cases["B"]).task.status is TaskStatus.DONE
    assert store.find_reusable("agent:atlas", normalize_subject(mvp_cases["B"])) is None
    store.close()


def test_config_defaults_to_tools_mode_and_sage_thinks(atlas, sage) -> None:
    assert load_model_config(AGENTS_DIR.parent / "config" / "models.yaml").ollama.decision_mode == "tools"
    assert sage.llm.thinking is True and atlas.llm.thinking is False
