"""Decisions as native tool calls.

Small models answer better in free text than inside a JSON string. In "tools" mode the model
answers in plain text and acts through tool calls; this module builds the tool list for one
agent and maps tool calls back to the unchanged decision contract (models.Decision).
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any

from myriad.agents.definition import AgentDefinition
from myriad.models import Answer, AskUser, Decision, Delegate, Task, ToolCall
from myriad.tools.memory_tools import TOOL_SPECS

DEFAULT_OUTPUT_LIMIT = 400


def _function(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(properties)},
        },
    }


def _text(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def action_specs(
    agent: AgentDefinition,
    task: Task,
    team: Mapping[str, AgentDefinition] | None,
    available_tools: Collection[str] | None,
) -> list[dict[str, Any]]:
    """The tools one agent may use in this task. Permissions are checked again in code."""
    specs = []
    if task.is_root:
        specs.append(_function(
            "ask_user", "Ask the user one focused clarifying question. Use it only when the request is unclear.",
            {"question": _text("The question for the user")},
        ))
    else:
        specs.append(_function(
            "need_more_info", "Tell whoever briefed you which information is missing. Never guess it.",
            {"question": _text("What is missing")},
        ))
    specs.append(_function(
        "report_failure", "Report that the task cannot be completed, with the reason.",
        {"reason": _text("Why it cannot be done")},
    ))
    delegates = [a for a in agent.job.delegates_to if team is not None and a in team]
    if delegates:
        specs.append(_function(
            "delegate",
            "Hand a task to a specialist. The specialist sees nothing but this brief, so make it self-contained.",
            {
                # Brief guidance lives here, where it applies: as a skill in the system prompt
                # an 8B model copied the checklist into its answer instead of delegating.
                "agent": {"type": "string", "enum": delegates, "description": "Specialist id"},
                "goal": _text("One sentence: what must exist when the specialist is done"),
                "context": _text("Every fact that changes the result: the user's request in their words, "
                                 "deadline, time available, skill level, tools, preferences, decisions "
                                 "already made, and the relevant MYRIAD context"),
                "expected_output": _text("Form and quality bar of the result, e.g. numbered steps "
                                         "with checkable outcomes"),
                "output_limit": {"type": "integer", "description": "Maximum answer length in tokens (300-600)"},
            },
        ))
    for tool in agent.skills.tools:
        if available_tools is not None and tool in available_tools and tool in TOOL_SPECS:
            specs.append(TOOL_SPECS[tool])
    return specs


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def decision_from_tool_call(name: str, raw_arguments: Any) -> str:
    """Map one tool call to decision JSON. Missing fields stay empty so validation rejects them."""
    args = _arguments(raw_arguments)
    if name == "ask_user":
        decision: dict[str, Any] = {"type": "ask_user", "question": args.get("question", "")}
    elif name == "need_more_info":
        decision = {"type": "answer", "outcome": "needs_input", "text": args.get("question", "")}
    elif name == "report_failure":
        decision = {"type": "answer", "outcome": "failed", "text": args.get("reason", "")}
    elif name == "delegate":
        limit = args.get("output_limit", DEFAULT_OUTPUT_LIMIT)
        if isinstance(limit, str) and limit.strip().isdigit():
            limit = int(limit)
        decision = {
            "type": "delegate",
            "agent": args.get("agent", ""),
            "brief": {
                "goal": args.get("goal", ""),
                "context": args.get("context", ""),
                "expected_output": args.get("expected_output", ""),
                "output_limit": limit,
            },
        }
    else:  # memory tools and anything unknown; the engine checks permissions
        decision = {"type": "tool_call", "tool": name, "arguments": args}
    return json.dumps(decision, ensure_ascii=False)


def decision_from_text(text: str) -> str:
    """A plain-text reply is the final answer."""
    return json.dumps({"type": "answer", "outcome": "done", "text": text}, ensure_ascii=False)


def tool_call_from_decision(decision: Decision) -> dict[str, Any] | None:
    """Earlier decisions in the history, rendered back as native tool calls (None = plain text)."""
    if isinstance(decision, Delegate):
        return {"name": "delegate", "arguments": {"agent": decision.agent, **decision.brief.model_dump()}}
    if isinstance(decision, ToolCall):
        return {"name": decision.tool, "arguments": decision.arguments}
    return None


def text_from_decision(decision: Decision) -> str:
    if isinstance(decision, Answer):
        return decision.text
    if isinstance(decision, AskUser):
        return decision.question
    return ""
