"""Configuration schema. One Pydantic model drives validation, the JSON Schema and docs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Risk = Literal["read", "write", "destructive", "external"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Price(_Strict):
    """USD per million tokens (AD-07)."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float | None = None
    cache_write: float | None = None


class ModelProfile(_Strict):
    """A named model setup in models.yaml, selected with `model.profile`."""

    adapter: str = Field(description="Adapter name: anthropic, openai, openai_compat, mock.")
    model: str = Field(description="Provider model ID, e.g. claude-opus-5-5 or qwen3:14b.")
    base_url: str | None = None
    api_key: str | None = Field(
        default=None,
        description="Reference to a secret as ${ENV_VAR}; literal keys are rejected (CF-02).",
    )
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(
        default=None, description="Only sent when set; current Claude models reject it."
    )
    timeout_s: float = Field(default=600.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    price: Price | None = None
    capabilities: dict[str, bool | int] = Field(
        default_factory=dict, description="Overrides for the adapter's capability flags (AD-03)."
    )
    options: dict[str, Any] = Field(
        default_factory=dict, description="Adapter-specific settings, passed through unchanged."
    )


class Budgets(_Strict):
    """Per-run limits (FR-04). A run that hits one stops gracefully with a partial result."""

    max_steps: int = Field(default=25, gt=0)
    max_tokens: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, gt=0)
    timeout_s: float | None = Field(default=None, gt=0)


class AgentSection(_Strict):
    name: str = "agent"
    description: str = ""
    instructions: str = Field(default="", description="Rendered into the system prompt.")
    system_prompt: str = Field(
        default="system/default.md.j2", description="Jinja2 template under a prompts/ folder."
    )
    prompt_vars: dict[str, Any] = Field(default_factory=dict)
    loop: Literal["react", "single_shot"] = "react"
    budgets: Budgets = Field(default_factory=Budgets)


class ModelSection(_Strict):
    profile: str = "smart"
    effort: Effort | None = Field(
        default=None, description="Unified effort knob, mapped to each provider (AD-04)."
    )
    fallback: list[str] = Field(
        default_factory=list, description="Profiles to try when the primary fails (AD-06)."
    )


class SkillsConfig(_Strict):
    paths: list[str] = Field(
        default_factory=lambda: ["skills"],
        description="Project skill folders, relative to the project root.",
    )
    user_path: str | None = "~/.agent-base/skills"
    enabled: list[str] | None = Field(
        default=None, description="Skill names to load; null loads every skill found."
    )


class ToolsConfig(_Strict):
    enabled: list[str] | None = Field(
        default=None, description="Tool names or namespace globs (files.*); null enables all."
    )
    disabled: list[str] = Field(default_factory=list)
    require_approval: list[str] = Field(default_factory=list)
    auto_approve: list[str] = Field(default_factory=list)
    ask_for_risks: list[Risk] = Field(
        default_factory=lambda: ["write", "destructive", "external"],
        description="Risk levels that need approval unless auto-approved (SEC-02).",
    )
    timeout_s: float = Field(default=60.0, gt=0)


class LoggingConfig(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "WARNING"


class AgentConfig(_Strict):
    """Root of agent.yaml after all layers are merged (CF-01)."""

    agent: AgentSection = Field(default_factory=AgentSection)
    model: ModelSection = Field(default_factory=ModelSection)
    profiles: dict[str, ModelProfile] = Field(default_factory=dict)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def _profiles_exist(self) -> AgentConfig:
        for name in [self.model.profile, *self.model.fallback]:
            if name not in self.profiles:
                known = ", ".join(sorted(self.profiles)) or "none defined"
                raise ValueError(f"model profile '{name}' is not defined (known: {known})")
        return self


def json_schema() -> dict[str, Any]:
    schema = AgentConfig.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "agent-base agent configuration"
    return schema
