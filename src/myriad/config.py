"""Model configuration (config/models.yaml): abstract tiers -> concrete models, provider selection."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from myriad.agents.definition import AgentDefinition
from myriad.artifacts import SQLiteArtifactStore
from myriad.executors.claude_code import ClaudeCodeExecutor, find_claude
from myriad.executors.service import CodingService
from myriad.executors.workspace import GitWorkspace, WorkspaceError
from myriad.llm.base import LLMProvider
from myriad.llm.mock import MockProvider
from myriad.llm.ollama import OllamaProvider

ProviderName = Literal["mock", "ollama"]


class ConfigError(ValueError):
    """The model configuration is missing, invalid or does not fit the loaded agents."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OllamaSettings(_Strict):
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = Field(default=180, gt=0)
    # "tools": free-text answers + native tool calls (better for small models); "json": decision JSON
    decision_mode: Literal["json", "tools"] = "tools"


class TierSettings(_Strict):
    model: str
    context_window: int = Field(gt=0)


class ForgeSettings(_Strict):
    executor: Literal["claude_code"] = "claude_code"
    command: str = "auto"
    repo: str  # target repository, relative to the MYRIAD folder or absolute
    timeout_seconds: float = Field(default=900, gt=0)
    allowed_tools: str = "Read Edit Write Glob Grep"
    test_command: list[str] = []


class ModelConfig(_Strict):
    default_provider: ProviderName = "mock"
    ollama: OllamaSettings = OllamaSettings()
    forge: ForgeSettings | None = None
    tiers: dict[str, TierSettings]


def load_model_config(path: str | Path) -> ModelConfig:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ModelConfig.model_validate(data)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read file ({exc.strerror})") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{path}: invalid model configuration - {exc}") from exc


def build_provider(
    name: ProviderName, config: ModelConfig, agents: Mapping[str, AgentDefinition]
) -> LLMProvider:
    if name == "mock":
        return MockProvider()
    if name == "ollama":
        tiers = {agent.llm.tier for agent in agents.values()}
        if len(tiers) != 1:  # one provider instance serves all agents for now
            raise ConfigError(f"all agents must share one llm.tier for now, found: {sorted(tiers)}")
        (tier,) = tiers
        settings = config.tiers.get(tier)
        if settings is None:
            raise ConfigError(f"no model configured for tier '{tier}'")
        return OllamaProvider(
            settings.model,
            base_url=config.ollama.base_url,
            context_window=settings.context_window,
            timeout=config.ollama.timeout_seconds,
            decision_mode=config.ollama.decision_mode,
        )
    raise ConfigError(f"unknown provider '{name}'")


def build_coding_service(
    settings: ForgeSettings | None, root: Path, data_dir: Path
) -> tuple[CodingService | None, str]:
    """The service behind Forge, or None with the reason why it is off."""
    if settings is None:
        return None, "nicht konfiguriert"
    repo = Path(settings.repo)
    repo = repo if repo.is_absolute() else root / repo
    if not repo.is_dir():
        return None, f"Repository fehlt ({repo}); scripts/create_practice_repo.py legt das Übungs-Repo an"
    command = find_claude(settings.command)
    if command is None:
        return None, "Claude Code nicht gefunden"
    workspace = GitWorkspace(repo, data_dir / "worktrees")
    try:
        workspace.check()
    except WorkspaceError as exc:
        return None, f"kein Git-Repository mit Commit: {exc}"
    executor = ClaudeCodeExecutor(command, allowed_tools=settings.allowed_tools,
                                  timeout=settings.timeout_seconds)
    artifacts = SQLiteArtifactStore(data_dir / "myriad.db")
    return CodingService(executor, workspace, artifacts, settings.test_command), f"an ({repo.name})"


def load_context(path: str | Path) -> str:
    """Organization profile shown to every agent (config/context.md). Missing file -> empty."""
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read file ({exc.strerror})") from exc
