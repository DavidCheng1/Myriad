"""Skills: SKILL.md folders in the Agent Skills format, chosen by code and loaded only when needed.

Format (agentskills.io): YAML frontmatter with `name` and `description` (required) and
`license`, `compatibility`, `metadata`, `allowed-tools` (optional), then Markdown instructions.
MYRIAD reads two keys inside `metadata` (string values, as the standard requires):
- `myriad-when`: `request` (default) - matched against the task's request text, or
  `delegation-result` - used right after a specialist returned a result.
- `myriad-triggers`: comma-separated words or phrases; a request skill needs at least one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
WHEN_VALUES = ("request", "delegation-result")
FRONTMATTER = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)


class SkillError(ValueError):
    """A skill folder is missing, malformed or breaks the Agent Skills rules."""


class _Frontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    license: str | None = None
    compatibility: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: dict[str, str] = {}
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError("use lowercase letters, digits and single hyphens, no hyphen at either end")
        return value


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path
    when: str = "request"
    triggers: tuple[str, ...] = ()

    def block(self) -> str:
        """The text appended to the system prompt when the skill is active."""
        return f"## Active skill: {self.name}\n{self.body}"


def parse_skill(path: str | Path) -> Skill:
    """Parse and validate one SKILL.md."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"{path}: cannot read file ({exc.strerror})") from exc
    match = FRONTMATTER.match(text)
    if match is None:
        raise SkillError(f"{path}: missing YAML frontmatter between '---' lines")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillError(f"{path}: invalid YAML - {exc}") from exc
    if not isinstance(data, dict):
        raise SkillError(f"{path}: frontmatter must be a mapping")
    try:
        meta = _Frontmatter.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
        )
        raise SkillError(f"{path}: invalid frontmatter - {details}") from exc
    if meta.name != path.parent.name:
        raise SkillError(f"{path}: name '{meta.name}' must match the folder name '{path.parent.name}'")
    body = match.group(2).strip()
    if not body:
        raise SkillError(f"{path}: the instructions after the frontmatter are empty")

    when = meta.metadata.get("myriad-when", "request")
    if when not in WHEN_VALUES:
        raise SkillError(f"{path}: metadata.myriad-when must be one of {', '.join(WHEN_VALUES)}")
    triggers = tuple(
        t.strip().lower() for t in meta.metadata.get("myriad-triggers", "").split(",") if t.strip()
    )
    if when == "request" and not triggers:
        raise SkillError(f"{path}: a request skill needs metadata.myriad-triggers")
    return Skill(meta.name, meta.description, body, path, when, triggers)


def load_skills(directory: str | Path) -> dict[str, Skill]:
    """Load every `<name>/SKILL.md` below `directory`. A missing directory means no skills."""
    root = Path(directory)
    if not root.is_dir():
        return {}
    skills: dict[str, Skill] = {}
    for path in sorted(root.glob("*/SKILL.md")):
        skill = parse_skill(path)
        skills[skill.name] = skill
    return skills


def _matches(trigger: str, text: str) -> bool:
    """Whole-word start match: 'plan' matches 'plan', 'plans', 'planning', not 'airplane'."""
    return re.search(rf"\b{re.escape(trigger)}", text) is not None


def select_skill(skills: Iterable[Skill], *, request: str, after_delegation: bool) -> Skill | None:
    """Pick at most one skill, deterministically (code before LLM).

    Right after a delegation result only a `delegation-result` skill applies. Otherwise the
    request skill with the most matching triggers wins; ties keep the first (sorted) skill.
    """
    candidates = list(skills)
    if after_delegation:
        return next((s for s in candidates if s.when == "delegation-result"), None)
    text = request.lower()
    best, best_score = None, 0
    for skill in candidates:
        if skill.when != "request":
            continue
        score = sum(1 for trigger in skill.triggers if _matches(trigger, text))
        if score > best_score:
            best, best_score = skill, score
    return best
