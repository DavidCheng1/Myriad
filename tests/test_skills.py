"""Phase 10a: skill library in the Agent Skills format - parsing, selection, context, engine, CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from myriad.agents.runtime import SKILL_BUDGET_TOKENS
from myriad.context.manager import ContextManager
from myriad.engine.task_engine import TaskEngine
from myriad.interface.cli import main
from myriad.interface.session import ChatSession
from myriad.llm.mock import MockProvider
from myriad.models import ENGINE_PREFIX, MEMORY_PREFIX, ChatMessage, Task
from myriad.resources.tokens import estimate_messages
from myriad.skills.loader import Skill, SkillError, load_skills, parse_skill, select_skill

from conftest import AGENTS_DIR, ANSWER_DONE, DELEGATE_SAGE, SAGE_PLAN

SKILLS_DIR = AGENTS_DIR.parent / "skills"
VALID = """---
name: {name}
description: Does one thing well. Use when the user asks for it.
license: MIT
metadata:
  myriad-when: request
  myriad-triggers: "plan, roadmap"
---

# Instructions
Do the thing.
"""
MINE = VALID.format(name="my-skill")


def write_skill(root: Path, name: str, text: str | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    path.write_text(text if text is not None else VALID.format(name=name), encoding="utf-8")
    return path


def skill(name: str, when: str = "request", triggers: tuple[str, ...] = (), body: str = "Do it.") -> Skill:
    return Skill(name, f"{name} skill", body, Path(name) / "SKILL.md", when, triggers)


# --- parsing (Agent Skills rules) ------------------------------------------------


def test_valid_skill_is_parsed(tmp_path) -> None:
    parsed = parse_skill(write_skill(tmp_path, "my-skill"))
    assert (parsed.name, parsed.when, parsed.triggers) == ("my-skill", "request", ("plan", "roadmap"))
    assert parsed.body.startswith("# Instructions") and parsed.block().startswith("## Active skill: my-skill")


@pytest.mark.parametrize(("text", "fragment"), [
    ("# no frontmatter", "missing YAML frontmatter"),
    (VALID.format(name="My-Skill"), "name"),
    (VALID.format(name="my--skill"), "name"),
    (MINE.replace("Does one thing well. Use when the user asks for it.", "x" * 1025), "description"),
    (MINE.replace('myriad-triggers: "plan, roadmap"', "version: 1.0"), "metadata.version"),
    (MINE.replace("license: MIT", "colour: blue"), "Extra inputs"),
    (MINE.replace('  myriad-triggers: "plan, roadmap"\n', ""), "needs metadata.myriad-triggers"),
    (MINE.replace("myriad-when: request", "myriad-when: always"), "myriad-when"),
    (MINE.replace("# Instructions\nDo the thing.\n", ""), "instructions after the frontmatter are empty"),
])
def test_invalid_skills_are_rejected(tmp_path, text: str, fragment: str) -> None:
    folder = "my--skill" if "my--skill" in text else "my-skill" if "My-Skill" not in text else "My-Skill"
    with pytest.raises(SkillError, match=fragment):
        parse_skill(write_skill(tmp_path, folder, text))


def test_name_must_match_folder(tmp_path) -> None:
    with pytest.raises(SkillError, match="must match the folder name"):
        parse_skill(write_skill(tmp_path, "other-name", VALID.format(name="my-skill")))


def test_repository_skills_load_and_stay_within_budget() -> None:
    skills = load_skills(SKILLS_DIR)
    assert set(skills) == {"result-check", "project-planning", "decision-record"}
    assert skills["result-check"].when == "delegation-result"
    assert all(len(s.block()) / 4 <= SKILL_BUDGET_TOKENS for s in skills.values())
    assert load_skills(SKILLS_DIR / "missing") == {}


# --- selection (code, not model) ---------------------------------------------------


def test_triggers_match_word_starts_and_the_best_score_wins() -> None:
    planning = skill("planning", triggers=("plan", "week"))
    deciding = skill("deciding", triggers=("should i", "which"))
    pick = select_skill([planning, deciding], request="Planning my next 4 weeks", after_delegation=False)
    assert pick is planning
    assert select_skill([planning], request="An airplane trip", after_delegation=False) is None
    assert select_skill([planning, deciding], request="Should I plan, which one?", after_delegation=False) is deciding


def test_after_a_delegation_only_the_result_skill_applies() -> None:
    planning = skill("planning", triggers=("plan",))
    review = skill("review", when="delegation-result")
    assert select_skill([planning, review], request="plan it", after_delegation=True) is review
    assert select_skill([planning], request="plan it", after_delegation=True) is None


def test_runtime_selects_by_situation_and_brief(atlas, sage) -> None:
    skills = load_skills(SKILLS_DIR)
    engine = TaskEngine({"atlas": atlas, "sage": sage}, MockProvider(), skills=skills)
    root = Task(goal="Which laptop should I buy?", assigned_to="atlas", created_by="user")
    root.messages.append(ChatMessage("user", root.goal, origin="user"))
    assert engine.runtimes["atlas"].select_skill(root).name == "decision-record"
    root.messages.append(ChatMessage("user", f"{ENGINE_PREFIX} Result from 'sage' (child task x, done): 1. a",
                                     origin="engine"))
    assert engine.runtimes["atlas"].select_skill(root).name == "result-check"
    child = Task(goal="g", assigned_to="sage", created_by="atlas", parent_id=root.id)
    child.messages.append(ChatMessage("user", "[BRIEF from atlas]\nGoal: a 6-week launch plan", origin="engine"))
    assert engine.runtimes["sage"].select_skill(child).name == "project-planning"


# --- context ------------------------------------------------------------------------


def test_skill_is_appended_to_the_system_prompt_and_keeps_the_static_prefix() -> None:
    task = Task(goal="Plan", assigned_to="atlas", created_by="user")
    task.messages.append(ChatMessage("user", "Plan", origin="user"))
    built = ContextManager().build("SYSTEM", task, [], input_limit=10_000, skill="## Active skill: s\nDo it.")
    assert built.skill_used and built.messages[0].content == "SYSTEM\n\n## Active skill: s\nDo it."


def test_memory_outranks_the_skill_and_the_skill_is_never_truncated() -> None:
    memory = ChatMessage("user", f"{MEMORY_PREFIX} " + "m" * 400, origin="engine")
    task = Task(goal="Plan", assigned_to="atlas", created_by="user")
    task.messages += [ChatMessage("user", "Plan", origin="user"), memory]
    required = estimate_messages([ChatMessage("system", "SYSTEM"), task.messages[0]])
    built = ContextManager().build("SYSTEM", task, [], input_limit=required + 110, skill="S" * 400)
    assert memory in built.messages and built.skill_skipped and built.messages[0].content == "SYSTEM"


# --- engine ---------------------------------------------------------------------------


def test_unknown_skills_in_an_agent_definition_are_rejected(atlas, sage) -> None:
    with pytest.raises(ValueError, match="unknown skills: result-check"):
        TaskEngine({"atlas": atlas, "sage": sage}, MockProvider(),
                   skills={"brief-writing": skill("brief-writing", triggers=("plan",)),
                           "decision-record": skill("decision-record", triggers=("which",)),
                           "project-planning": skill("project-planning", triggers=("plan",))})


def test_activation_is_logged_and_the_prompt_carries_the_skill(atlas, sage, mvp_cases) -> None:
    engine = TaskEngine({"atlas": atlas, "sage": sage}, MockProvider([DELEGATE_SAGE, SAGE_PLAN, ANSWER_DONE]),
                        skills=load_skills(SKILLS_DIR))
    reply = ChatSession(engine).send(mvp_cases["B"])
    _, messages = engine.protocol(reply.task.root_id)
    activated = [m.content for m in messages if m.sender == "skills"]
    assert activated == ["activated project-planning", "activated result-check"]
    calls = engine.provider.calls
    assert calls[0][0].content == engine.runtimes["atlas"].system_prompt  # no skill matched the request
    assert calls[2][0].content.startswith(engine.runtimes["atlas"].system_prompt)  # static prefix intact
    assert "## Active skill: project-planning" in calls[1][0].content
    assert "## Active skill: result-check" in calls[2][0].content


def test_oversized_skill_is_skipped_and_logged(atlas, sage) -> None:
    huge = skill("decision-record", triggers=("which",), body="x" * (SKILL_BUDGET_TOKENS * 4 + 100))
    skills = {"decision-record": huge, "result-check": skill("result-check", when="delegation-result"),
              "project-planning": skill("project-planning", triggers=("plan",))}
    engine = TaskEngine({"atlas": atlas, "sage": sage}, MockProvider([ANSWER_DONE]), skills=skills)
    reply = ChatSession(engine).send("Which option is better?")
    _, messages = engine.protocol(reply.task.root_id)
    assert any(m.content.startswith("skipped decision-record") for m in messages)
    assert "## Active skill" not in engine.provider.calls[0][0].content


# --- CLI ------------------------------------------------------------------------------


def test_cli_lists_skills(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MYRIAD_DATA_DIR", str(tmp_path))
    inputs = iter(["/skills", "/exit"])
    output: list[str] = []
    assert main(input_fn=lambda _prompt: next(inputs), out=output.append) == 0
    text = "\n".join(output)
    assert "Skills: 3" in text
    assert "result-check · atlas · nach einem Fachagenten-Ergebnis" in text
    assert "project-planning · sage" in text
