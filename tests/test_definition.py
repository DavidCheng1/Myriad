"""Phase 3: agent definitions load, and broken definitions give clear errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from myriad.agents.definition import DefinitionError, load_agent, load_agents, render_system_prompt

from conftest import AGENTS_DIR, ATLAS_PATH


def _atlas_data() -> dict[str, Any]:
    return yaml.safe_load(ATLAS_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_atlas_loads(atlas) -> None:
    assert atlas.id == "atlas"
    assert "sage" in atlas.job.delegates_to
    assert atlas.limits.token_budget_task_tree == 15000
    assert atlas.limits.max_steps_per_task == 8
    assert atlas.limits.max_delegation_depth == 1


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DefinitionError, match="cannot read file"):
        load_agent(tmp_path / "missing.yaml")


def test_invalid_yaml_syntax(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("id: [unclosed", encoding="utf-8")
    with pytest.raises(DefinitionError, match="invalid YAML"):
        load_agent(path)


def test_python_tags_are_rejected_by_safe_load(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/object/apply:os.system ['echo unsafe']", encoding="utf-8")
    with pytest.raises(DefinitionError, match="invalid YAML"):
        load_agent(path)


def test_missing_section_is_named(tmp_path: Path) -> None:
    data = _atlas_data()
    del data["soul"]
    with pytest.raises(DefinitionError, match=r"soul: Field required"):
        load_agent(_write(tmp_path, data))


def test_unknown_field_is_named(tmp_path: Path) -> None:
    data = _atlas_data()
    data["behavior"]["mood"] = "grumpy"
    with pytest.raises(DefinitionError, match=r"behavior\.mood: Extra inputs are not permitted"):
        load_agent(_write(tmp_path, data))


def test_wildcard_namespace_is_rejected(tmp_path: Path) -> None:
    data = _atlas_data()
    data["memory"]["shared_namespaces"].append({"name": "shared:project:*", "access": "read"})
    with pytest.raises(DefinitionError, match=r"memory\.shared_namespaces\.1\.name"):
        load_agent(_write(tmp_path, data))


def test_private_namespace_must_match_id(tmp_path: Path) -> None:
    data = _atlas_data()
    data["memory"]["private_namespace"] = "agent:someone_else"
    with pytest.raises(DefinitionError, match="agent:atlas"):
        load_agent(_write(tmp_path, data))


def test_prompt_contains_labelled_sections_in_order(atlas) -> None:
    prompt = render_system_prompt(atlas, "FORMAT")
    headers = ["## Character", "## Job", "## Team", "## Behavior", "## Skills", "## Output"]
    positions = [prompt.index(h) for h in headers]
    assert positions == sorted(positions)
    assert prompt.endswith("FORMAT")


def test_prompt_contains_soul_job_behavior_values(atlas) -> None:
    prompt = render_system_prompt(atlas, "FORMAT")
    for value in (
        atlas.soul.personality[0],
        atlas.soul.principles[1],
        atlas.job.mission,
        atlas.job.responsibilities[2],
        atlas.behavior.routing[0],
        atlas.behavior.decision_rules[0],
        atlas.behavior.escalation.when[0],
        "Tools: search_memory, write_memory",
    ):
        assert value in prompt


def test_prompt_is_deterministic(atlas) -> None:
    assert render_system_prompt(atlas, "F") == render_system_prompt(atlas, "F")


def test_prompt_lists_only_available_delegates_and_tools(atlas, sage) -> None:
    prompt = render_system_prompt(atlas, "F", team={"atlas": atlas, "sage": sage}, available_tools=set())
    assert "- sage (planner): " in prompt
    assert "Tools: none" in prompt
    assert "nova" not in prompt


# --- phase 6: Sage and loading all definitions --------------------------------


def test_sage_loads(sage) -> None:
    assert sage.id == "sage"
    assert sage.job.delegates_to == [] and sage.skills.tools == []
    assert sage.job.reports_to == "atlas"
    assert sage.limits.max_delegation_depth == 0


def test_load_agents_loads_all_definitions() -> None:
    assert set(load_agents(AGENTS_DIR)) == {"atlas", "sage"}


def test_load_agents_requires_id_to_match_file_name(tmp_path: Path) -> None:
    (tmp_path / "other.yaml").write_text(ATLAS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(DefinitionError, match="must match the file name"):
        load_agents(tmp_path)


def test_load_agents_requires_at_least_one_definition(tmp_path: Path) -> None:
    with pytest.raises(DefinitionError, match="no agent definitions"):
        load_agents(tmp_path)
