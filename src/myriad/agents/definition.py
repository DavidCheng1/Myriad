"""Agent definitions: loading, validation and system prompt rendering."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class DefinitionError(ValueError):
    """An agent definition could not be read or is invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Soul(_Strict):
    personality: list[str]
    values: list[str]
    principles: list[str]


class Job(_Strict):
    mission: str
    responsibilities: list[str]
    out_of_scope: list[str] = []
    reports_to: str
    delegates_to: list[str] = []


class CommunicationStyle(_Strict):
    tone: str
    language: str
    format: str


class Escalation(_Strict):
    to: str
    when: list[str]


class Behavior(_Strict):
    communication_style: CommunicationStyle
    routing: list[str]
    decision_rules: list[str] = []
    escalation: Escalation
    examples: list[str] = []  # short situation -> response pairs; concrete beats abstract for small models


class Skills(_Strict):
    capabilities: list[str]
    tools: list[str] = []
    library: list[str] = []  # skills from skills/ this agent may load on demand


class SharedNamespace(_Strict):
    name: str = Field(pattern=r"^shared:[a-z0-9_:-]+$")  # exact names only, no wildcards
    access: Literal["read", "read_write"]


class StoreRule(_Strict):
    kind: Literal["fact", "decision", "episode", "preference"]
    what: str
    retention: str = Field(pattern=r"^(permanent|\d+d)$")


class Memory(_Strict):
    private_namespace: str
    shared_namespaces: list[SharedNamespace] = []
    store: list[StoreRule] = []


class LLMSettings(_Strict):
    tier: str
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(gt=0)
    thinking: bool = False  # hidden reasoning before answering (slower, deeper)
    thinking_budget: int = Field(default=1024, gt=0)  # extra tokens reserved for that reasoning


class Limits(_Strict):
    token_budget_task_tree: int = Field(gt=0)
    max_steps_per_task: int = Field(gt=0)
    max_delegation_depth: int = Field(ge=0)


class AgentDefinition(_Strict):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    version: str
    role: str
    summary: str
    soul: Soul
    job: Job
    behavior: Behavior
    skills: Skills
    memory: Memory
    llm: LLMSettings
    limits: Limits

    @model_validator(mode="after")
    def _private_namespace_matches_id(self) -> AgentDefinition:
        expected = f"agent:{self.id}"
        if self.memory.private_namespace != expected:
            raise ValueError(f"memory.private_namespace must be '{expected}'")
        return self


def load_agent(path: str | Path) -> AgentDefinition:
    """Load and validate an agent definition. Only safe YAML is accepted."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DefinitionError(f"{path}: cannot read file ({exc.strerror})") from exc
    except yaml.YAMLError as exc:
        raise DefinitionError(f"{path}: invalid YAML - {exc}") from exc
    if not isinstance(data, dict):
        raise DefinitionError(f"{path}: expected a mapping at the top level")
    try:
        return AgentDefinition.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise DefinitionError(f"{path}: invalid agent definition - {details}") from exc


def load_agents(directory: str | Path) -> dict[str, AgentDefinition]:
    """Load every *.yaml in a directory. The agent id must match the file name."""
    agents: dict[str, AgentDefinition] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        agent = load_agent(path)
        if agent.id != path.stem:
            raise DefinitionError(f"{path}: id '{agent.id}' must match the file name")
        agents[agent.id] = agent
    if not agents:
        raise DefinitionError(f"{directory}: no agent definitions found")
    return agents


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- none"


def _team_text(job: Job, team: Mapping[str, AgentDefinition] | None) -> str:
    """Capability cards of the specialists that are actually loaded."""
    if team is None:
        return f"You may delegate to: {', '.join(job.delegates_to) or 'nobody'}."
    delegates = [team[a] for a in job.delegates_to if a in team]
    if not delegates:
        return "You may delegate to: nobody. Do the work yourself or report what is missing."
    cards = []
    for member in delegates:
        not_for = f" Not for: {'; '.join(member.job.out_of_scope)}." if member.job.out_of_scope else ""
        cards.append(f"{member.id} ({member.role}): {member.summary}{not_for}")
    return "You may delegate to these specialists. They see only your brief:\n" + _bullets(cards)


def render_system_prompt(
    agent: AgentDefinition,
    decision_format: str,
    *,
    team: Mapping[str, AgentDefinition] | None = None,
    available_tools: Collection[str] | None = None,
    tool_hints: Mapping[str, str] | None = None,
    context: str = "",
) -> str:
    """Render the static system prompt from the definition.

    Compact and concrete on purpose: small models follow short rules, capability cards and
    examples better than long lists of traits. Only loaded specialists and available tools
    are listed; permissions are still checked in code. The output is deterministic so that
    it forms a stable, cacheable prefix.
    """
    soul, job, behavior, skills = agent.soul, agent.job, agent.behavior, agent.skills
    style = behavior.communication_style
    tools = [t for t in skills.tools if available_tools is None or t in available_tools]
    if tool_hints and tools:
        tool_text = "Tools (arguments as JSON):\n" + _bullets(
            [f"{t}: {tool_hints[t]}" if t in tool_hints else t for t in tools]
        )
    else:
        tool_text = f"Tools: {', '.join(tools) or 'none'}"

    sections = [f"You are {agent.name}, the {agent.role} of MYRIAD. {agent.summary}"]
    if context.strip():
        sections.append(f"## Context\n{context.strip()}")
    sections += [
        "## Character\n"
        f"Personality: {'; '.join(soul.personality)}.\n"
        f"Values: {'; '.join(soul.values)}.\n"
        f"Principles:\n{_bullets(soul.principles)}",
        "## Job\n"
        f"Mission: {job.mission}\n"
        f"Responsibilities:\n{_bullets(job.responsibilities)}\n"
        f"Not your job: {'; '.join(job.out_of_scope) or 'nothing specific'}.\n"
        f"You report to: {job.reports_to}.",
        "## Team\n" + _team_text(job, team),
        "## Behavior\n"
        f"Tone: {style.tone}. Language: {style.language}. Format: {style.format}.\n"
        f"Routing (apply in this order):\n{_bullets(behavior.routing)}\n"
        f"Rules:\n{_bullets(behavior.decision_rules)}\n"
        f"Escalate to {behavior.escalation.to} when: {'; '.join(behavior.escalation.when)}.",
        "## Skills\n" f"Capabilities: {'; '.join(skills.capabilities)}.\n" + tool_text,
    ]
    if behavior.examples:
        sections.append("## Examples\n" + _bullets(behavior.examples))
    sections.append(f"## Output\n{decision_format}")
    return "\n\n".join(sections)
