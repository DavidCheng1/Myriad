"""Model configuration (config/models.yaml): abstract tiers -> concrete models, provider selection."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from myriad.agents.definition import AgentDefinition
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


class ModelConfig(_Strict):
    default_provider: ProviderName = "mock"
    ollama: OllamaSettings = OllamaSettings()
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


def load_context(path: str | Path) -> str:
    """Organization profile shown to every agent (config/context.md). Missing file -> empty."""
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read file ({exc.strerror})") from exc
