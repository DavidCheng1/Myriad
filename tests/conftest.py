"""Shared fixtures: agent definitions and the MVP examples A-D from docs/ROADMAP.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myriad.agents.definition import AgentDefinition, load_agent

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"
ATLAS_PATH = AGENTS_DIR / "atlas.yaml"


def j(**fields: object) -> str:
    """Raw model output as the provider would return it."""
    return json.dumps(fields, ensure_ascii=False)


BRIEF = {
    "goal": "Plan a 4-week Python learning project.",
    "context": "",
    "expected_output": "A plan with at least 3 steps.",
    "output_limit": 400,
}
ANSWER_DONE = j(type="answer", outcome="done", text="[SIMULIERT] Erledigt.")
ANSWER_FAILED = j(type="answer", outcome="failed", text="[SIMULIERT] Das konnte ich nicht erledigen.")
ASK = j(type="ask_user", question="[SIMULIERT] Wofür soll der Plan sein?")
DELEGATE_SAGE = j(type="delegate", agent="sage", brief=BRIEF)
SAGE_PLAN = j(type="answer", outcome="done", text="[SIMULIERT] 1. Grundlagen. 2. Übungen. 3. Projekt. 4. Review.")
SAGE_FAILED = j(type="answer", outcome="failed", text="[SIMULIERT] Kein sinnvoller Plan möglich.")

# Scripted mock outputs for the MVP cases A-D (docs/ROADMAP.md), in call order Atlas -> Sage -> Atlas.
MOCK_SCRIPTS: dict[str, list[str]] = {
    "A": [ANSWER_DONE],
    "B": [DELEGATE_SAGE, SAGE_PLAN, ANSWER_DONE],
    "C": [ASK],
    "D": [DELEGATE_SAGE, SAGE_FAILED, ANSWER_FAILED],
}


@pytest.fixture(scope="session")
def atlas() -> AgentDefinition:
    return load_agent(ATLAS_PATH)


@pytest.fixture(scope="session")
def sage() -> AgentDefinition:
    return load_agent(AGENTS_DIR / "sage.yaml")


@pytest.fixture
def mvp_cases() -> dict[str, str]:
    """User messages of the MVP scenario (docs/ROADMAP.md).

    D has no own wording in the roadmap: it is the planning request B where Sage fails.
    """
    return {
        "A": "Was kannst du für mich tun?",
        "B": "Plane ein 4-Wochen-Lernprojekt für Python.",
        "C": "Mach mal einen Plan.",
        "D": "Plane ein 4-Wochen-Lernprojekt für Python.",
    }


@pytest.fixture
def mock_scripts() -> dict[str, list[str]]:
    return {case: list(script) for case, script in MOCK_SCRIPTS.items()}
