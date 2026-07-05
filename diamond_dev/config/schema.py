"""Typed configuration dataclasses for Diamond Dev."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from diamond_dev.acceptance import (
    DEFAULT_ACCEPTANCE_MAX_WAIT_SECONDS,
    DEFAULT_ACCEPTANCE_POLL_INTERVAL_SECONDS,
)
from diamond_dev.agents import AgentCapability, adapter_names, resolve_adapter
from diamond_dev.errors import ConfigError

CONFIG_FILE_NAME: Final = ".diamond-dev.toml"
DEFAULT_IMPLEMENTERS: Final = ("codex", "claude")
DEFAULT_COMPARISON_MAX_TOTAL_DIFF_BYTES: Final = 200_000
DEFAULT_COMPARISON_MAX_FILE_DIFF_BYTES: Final = 40_000
DEFAULT_COMPARISON_MAX_TEST_OUTPUT_BYTES: Final = 20_000
DEFAULT_COMPARISON_MAX_SIGNAL_OUTPUT_BYTES: Final = 20_000


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    """Best-effort notification webhook settings."""

    initial_implementation_url: str | None = None
    comparison_url: str | None = None
    comparison_implementation_url: str | None = None
    review_input_needed_url: str | None = None
    open_pr_url: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    """Acceptance polling settings."""

    poll_interval_seconds: int = DEFAULT_ACCEPTANCE_POLL_INTERVAL_SECONDS
    max_wait_seconds: int = DEFAULT_ACCEPTANCE_MAX_WAIT_SECONDS


@dataclass(frozen=True, slots=True)
class PromptConfig:
    """Prompt override file settings."""

    initial_implementation_file: str | None = None
    comparison_judgment_file: str | None = None
    gemini_comparison_file: str | None = None
    comparison_implementation_file: str | None = None
    review_judgment_file: str | None = None
    review_fix_file: str | None = None


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """External agent command settings."""

    adapter: str | None = None
    model: str | None = None

    def adapter_name(self, agent_name: str) -> str:
        """Return the effective built-in adapter name for this agent."""
        return self.adapter or agent_name


@dataclass(frozen=True, slots=True)
class AgentConfigs:
    """External agent settings by agent name."""

    by_name: Mapping[str, AgentConfig] = field(default_factory=dict)

    def get(self, agent_name: str) -> AgentConfig:
        """Return explicit or implicit built-in config for an agent name."""
        if agent_name in self.by_name:
            return self.by_name[agent_name]
        if agent_name in adapter_names():
            return AgentConfig(adapter=agent_name)
        raise ConfigError(f"Unknown configured agent `{agent_name}`")

    def __getattr__(self, agent_name: str) -> AgentConfig:
        """Support legacy `config.agents.codex` style access."""
        try:
            return self.get(agent_name)
        except (ConfigError,) as error:
            raise AttributeError(agent_name) from error


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    """Workflow role settings by configured agent name."""

    implementers: tuple[str, ...] = DEFAULT_IMPLEMENTERS
    comparison_judge: str = "gemini"
    comparison_fixer: str | None = None
    review_provider: str = "coderabbit"
    review_judge: str = "codex"
    review_fixer: str = "codex"
    final_reviewer: str = "claude"

    def role_agent_names(self) -> tuple[str, ...]:
        """Return agent names referenced by this workflow."""
        role_names = [
            *self.implementers,
            self.comparison_judge,
            self.review_provider,
            self.review_judge,
            self.review_fixer,
            self.final_reviewer,
        ]
        if self.comparison_fixer is not None:
            role_names.append(self.comparison_fixer)
        return tuple(dict.fromkeys(role_names))

    def comparison_fixer_for(self, accepted_agent: str) -> str:
        """Return the configured or default comparison fixer."""
        if self.comparison_fixer is not None:
            return self.comparison_fixer
        for implementer in self.implementers:
            if implementer != accepted_agent:
                return implementer
        raise ConfigError(
            "Workflow requires at least one non-selected comparison fixer",
        )

    def role_capabilities(self) -> tuple[tuple[str, AgentCapability], ...]:
        """Return configured role-agent capability requirements."""
        requirements: list[tuple[str, AgentCapability]] = [
            (implementer, "implementation")
            for implementer in self.implementers
        ]
        requirements.extend(
            (
                (self.comparison_judge, "comparison_judge"),
                (self.review_provider, "review_provider"),
                (self.review_judge, "review_judge"),
                (self.review_fixer, "review_fixer"),
                (self.final_reviewer, "final_reviewer"),
            ),
        )
        if self.comparison_fixer is not None:
            requirements.append((self.comparison_fixer, "comparison_fixer"))
        else:
            requirements.extend(
                (implementer, "comparison_fixer")
                for implementer in self.implementers
            )
        return tuple(requirements)


@dataclass(frozen=True, slots=True)
class ComparisonConfig:
    """Comparison bundle generation settings."""

    test_commands: tuple[str, ...] = ()
    signal_commands: tuple[str, ...] = ()
    max_total_diff_bytes: int = DEFAULT_COMPARISON_MAX_TOTAL_DIFF_BYTES
    max_file_diff_bytes: int = DEFAULT_COMPARISON_MAX_FILE_DIFF_BYTES
    max_test_output_bytes: int = DEFAULT_COMPARISON_MAX_TEST_OUTPUT_BYTES
    max_signal_output_bytes: int = DEFAULT_COMPARISON_MAX_SIGNAL_OUTPUT_BYTES


@dataclass(frozen=True, slots=True)
class DiamondDevConfig:
    """Typed `.diamond-dev.toml` settings."""

    # pylint: disable=too-many-instance-attributes

    config_path: Path
    repository_url: str
    wiki_repository_url: str | None = None
    gemini_comparison_prompt_file: str | None = None
    notifications: NotificationConfig = NotificationConfig()
    prompts: PromptConfig = PromptConfig()
    agents: AgentConfigs = field(default_factory=AgentConfigs)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    comparison: ComparisonConfig = ComparisonConfig()
    acceptance: AcceptanceConfig = AcceptanceConfig()

    @property
    def config_dir(self) -> Path:
        """Return the directory containing the loaded config file."""
        return self.config_path.parent

    def gemini_prompt_path(self) -> Path | None:
        """Return the configured Gemini prompt file path, if any."""
        return self.comparison_judgment_prompt_path()

    def comparison_judgment_prompt_path(self) -> Path | None:
        """Return the configured comparison judgment prompt file path, if any."""
        return self.prompt_path(
            self.prompts.comparison_judgment_file
            or self.prompts.gemini_comparison_file
            or self.gemini_comparison_prompt_file,
        )

    def prompt_path(self, prompt_file: str | None) -> Path | None:
        """Return an absolute prompt override file path, if any."""
        if prompt_file is None:
            return None

        prompt_path = Path(prompt_file)
        if prompt_path.is_absolute():
            return prompt_path
        return self.config_dir / prompt_path

    def agent_config(self, agent_name: str) -> AgentConfig:
        """Return the effective config for an agent name."""
        return self.agents.get(agent_name)

    def agent_adapter_name(self, agent_name: str) -> str:
        """Return the built-in adapter backing a configured agent."""
        return self.agent_config(agent_name).adapter_name(agent_name)

    def required_cli_names(self) -> tuple[str, ...]:
        """Return external executables required by configured workflow agents."""
        executables = [
            resolve_adapter(self.agent_adapter_name(agent_name)).executable
            for agent_name in self.workflow.role_agent_names()
        ]
        return tuple(dict.fromkeys(executables))
