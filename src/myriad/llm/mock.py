"""Deterministic mock provider. No real model, no costs - every reply is marked as simulated."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Sequence
from typing import Any

from myriad.llm.base import Completion
from myriad.models import BRIEF_PREFIX, ENGINE_PREFIX, MEMORY_PREFIX, ChatMessage
from myriad.resources.tokens import estimate_messages, estimate_tokens

SIMULATED = "[SIMULIERT]"

_DEFAULT_ANSWER = (
    "Ich bin Atlas, dein zentraler Ansprechpartner in MYRIAD. Einfache Fragen beantworte "
    "ich selbst, Fachaufgaben gebe ich an spezialisierte Agenten weiter. "
    "Dies ist eine simulierte Antwort ohne echtes Sprachmodell."
)


def _json(**fields: object) -> str:
    return json.dumps(fields, ensure_ascii=False)


def _answer(outcome: str, text: str) -> str:
    return _json(type="answer", outcome=outcome, text=f"{SIMULATED} {text}")


class MockProvider:
    """Rule-based by default; replays raw outputs in order when a script is given (tests)."""

    name = "mock"
    simulated = True
    decision_mode = "json"

    def __init__(self, script: Iterable[str] | None = None, context_window: int = 8192) -> None:
        self._script = deque(script) if script is not None else None
        self.model = "mock-scripted" if script is not None else "mock-rules"
        self.context_window = context_window
        self.calls: list[list[ChatMessage]] = []

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
        self.calls.append(list(messages))
        if self._script is not None:
            if not self._script:
                raise RuntimeError("mock script exhausted")
            text = self._script.popleft()
        else:
            text = self._respond(messages)
        return Completion(
            text=text,
            input_tokens=estimate_messages(messages),
            output_tokens=estimate_tokens(text),  # full text; the runtime enforces the limit
            estimated=True,
            simulated=True,
        )

    @staticmethod
    def _respond(messages: Sequence[ChatMessage]) -> str:
        last_message = messages[-1]
        last = last_message.content
        from_engine = last_message.from_engine  # origin field, never the text prefix alone
        if from_engine and last.startswith(ENGINE_PREFIX):
            detail = last.removeprefix(ENGINE_PREFIX).strip()
            if "rejected" in detail:
                return _answer("done", "Korrigierte Antwort nach einem abgewiesenen Format.")
            if detail.startswith("Result from"):  # successful delegation: Atlas consolidates
                result = detail.split("): ", 1)[-1].replace(SIMULATED, "").strip()
                return _answer("done", f"Ergebnis geprüft und zusammengefasst: {result}")
            return _answer("failed", f"Das konnte ich nicht erledigen. {detail}")

        if from_engine and last.startswith(MEMORY_PREFIX):  # reusable result recalled by the engine
            stored = last.split("\n", 1)[-1].replace(SIMULATED, "").strip()
            return _answer("done", f"Aus dem Gedächtnis (bereits geprüft): {stored}")

        if from_engine and last.startswith(BRIEF_PREFIX):  # Sage works on a brief
            goal = next(
                (line.removeprefix("Goal: ") for line in last.splitlines() if line.startswith("Goal: ")),
                "das Ziel",
            )
            if "#fail" in last.lower():
                return _answer("failed", f"Für „{goal}“ ist kein sinnvoller Plan möglich (simulierter Fehlschlag).")
            return _answer(
                "done",
                f"Plan für „{goal}“: 1. Ziel und Rahmen festlegen. 2. Inhalte in Etappen aufteilen. "
                "3. Etappen mit kleinen Übungen umsetzen. 4. Ergebnis prüfen und anpassen.",
            )

        text = last.lower()
        if "#invalid" in text:
            return "Dies ist absichtlich kein gültiges JSON."
        previous = messages[-2] if len(messages) >= 2 else None
        answered_question = (
            previous is not None
            and previous.role == "assistant"
            and '"ask_user"' in previous.content
        )
        if "plan" in text and len(text.split()) <= 4 and not answered_question:
            return _json(
                type="ask_user",
                question=f"{SIMULATED} Gern. Wofür soll der Plan sein, und über welchen Zeitraum?",
            )
        if "plan" in text or answered_question:
            return _json(
                type="delegate",
                agent="sage",
                brief={
                    "goal": last,
                    "context": "Request from the user via Atlas.",
                    "expected_output": "A plan with at least 3 steps.",
                    "output_limit": 400,
                },
            )
        if "merk" in text or "erinner" in text:
            return _json(type="tool_call", tool="search_memory", arguments={"query": last})
        return _answer("done", _DEFAULT_ANSWER)
