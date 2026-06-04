"""Configuration loading for Diamond Dev."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from diamond_dev.agents import (
    AgentAdapter,
    AgentCapability,
    adapter_names,
    resolve_adapter,
)
from diamond_dev.errors import ConfigError, DiamondDevError
from diamond_dev.naming import is_git_remote_url

CONFIG_FILE_NAME: Final = ".diamond-dev.toml"
_AGENT_NAME_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_IMPLEMENTERS: Final = ("codex", "claude")
DEFAULT_COMPARISON_MAX_TOTAL_DIFF_BYTES: Final = 200_000
DEFAULT_COMPARISON_MAX_FILE_DIFF_BYTES: Final = 40_000
DEFAULT_COMPARISON_MAX_TEST_OUTPUT_BYTES: Final = 20_000


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    """Best-effort notification webhook settings."""

    initial_implementation_url: str | None = None
    comparison_url: str | None = None
    comparison_implementation_url: str | None = None
    review_input_needed_url: str | None = None
    open_pr_url: str | None = None


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
    max_total_diff_bytes: int = DEFAULT_COMPARISON_MAX_TOTAL_DIFF_BYTES
    max_file_diff_bytes: int = DEFAULT_COMPARISON_MAX_FILE_DIFF_BYTES
    max_test_output_bytes: int = DEFAULT_COMPARISON_MAX_TEST_OUTPUT_BYTES


@dataclass(frozen=True, slots=True)
class _TableLegacyAlias:
    """Mapping between a table key and its legacy top-level alias."""

    table_name: str
    modern_key: str
    legacy_key: str


_NOTIFICATION_ALIASES: Final = {
    "initial_implementation_url": _TableLegacyAlias(
        table_name="notifications",
        modern_key="initial_implementation_url",
        legacy_key="notify_initial_implementation_url",
    ),
    "comparison_url": _TableLegacyAlias(
        table_name="notifications",
        modern_key="comparison_url",
        legacy_key="notify_comparison_url",
    ),
    "comparison_implementation_url": _TableLegacyAlias(
        table_name="notifications",
        modern_key="comparison_implementation_url",
        legacy_key="notify_comparison_implementation_url",
    ),
    "review_input_needed_url": _TableLegacyAlias(
        table_name="notifications",
        modern_key="review_input_needed_url",
        legacy_key="notify_review_input_needed_url",
    ),
    "open_pr_url": _TableLegacyAlias(
        table_name="notifications",
        modern_key="open_pr_url",
        legacy_key="notify_open_pr_url",
    ),
}


@dataclass(frozen=True, slots=True)
class DiamondDevConfig:
    """Typed `.diamond-dev.toml` settings."""

    config_path: Path
    repository_url: str
    wiki_repository_url: str | None = None
    gemini_comparison_prompt_file: str | None = None
    notifications: NotificationConfig = NotificationConfig()
    prompts: PromptConfig = PromptConfig()
    agents: AgentConfigs = field(default_factory=AgentConfigs)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    comparison: ComparisonConfig = ComparisonConfig()

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


def load_config(cwd: Path, config_path: Path | None = None) -> DiamondDevConfig:
    """Load and validate `.diamond-dev.toml` from the invocation directory."""
    resolved_config_path = _resolve_config_path(cwd, config_path)
    if not resolved_config_path.is_file():
        raise ConfigError(f"Missing required config file: {resolved_config_path}")

    try:
        with resolved_config_path.open("rb") as config_file:
            raw_config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(
            f"Could not read config file {resolved_config_path}: {error}",
        ) from error

    repository_url = _required_git_remote_url(
        raw_config,
        "repository_url",
        resolved_config_path,
    )
    _reject_renamed_key(
        raw_config,
        old_key="notes_repository_url",
        new_key="wiki_repository_url",
        config_path=resolved_config_path,
    )
    prompts = _load_prompts(raw_config, resolved_config_path)
    workflow = _load_workflow(raw_config, resolved_config_path)
    agents = _load_agents(raw_config, resolved_config_path)
    _validate_agent_configuration(agents, workflow, resolved_config_path)
    return DiamondDevConfig(
        config_path=resolved_config_path,
        repository_url=repository_url,
        wiki_repository_url=_optional_git_remote_url(
            raw_config,
            "wiki_repository_url",
            resolved_config_path,
        ),
        gemini_comparison_prompt_file=prompts.comparison_judgment_file,
        notifications=_load_notifications(raw_config, resolved_config_path),
        prompts=prompts,
        agents=agents,
        workflow=workflow,
        comparison=_load_comparison(raw_config, resolved_config_path),
    )


def read_gemini_prompt(config: DiamondDevConfig) -> str | None:
    """Read the optional Gemini comparison prompt file."""
    return read_comparison_judgment_prompt(config)


def read_comparison_judgment_prompt(config: DiamondDevConfig) -> str | None:
    """Read the optional comparison judgment prompt file."""
    return read_prompt_file(
        config,
        config.prompts.comparison_judgment_file
        or config.prompts.gemini_comparison_file
        or config.gemini_comparison_prompt_file,
        label="Comparison judgment prompt",
    )


def read_prompt_file(
    config: DiamondDevConfig,
    prompt_file: str | None,
    *,
    label: str,
) -> str | None:
    """Read an optional configured prompt override file."""
    prompt_path = config.prompt_path(prompt_file)
    if prompt_path is None:
        return None
    if not prompt_path.is_file():
        raise ConfigError(f"{label} file not found: {prompt_path}")
    try:
        return prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(
            f"Could not read {label} file {prompt_path}: {error}",
        ) from error


def _resolve_config_path(cwd: Path, config_path: Path | None) -> Path:
    if config_path is None:
        return cwd / CONFIG_FILE_NAME
    if config_path.is_absolute():
        return config_path
    return cwd / config_path


def _required_string(raw_config: dict[str, Any], key: str, config_path: Path) -> str:
    value = raw_config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ConfigError(f"Config {config_path} requires a non-empty `{key}` string")


def _required_git_remote_url(
    raw_config: dict[str, Any],
    key: str,
    config_path: Path,
) -> str:
    value = _required_string(raw_config, key, config_path)
    if is_git_remote_url(value):
        return value
    raise ConfigError(f"Config {config_path} `{key}` must be a valid Git remote URL")


def _optional_string(raw_config: dict[str, Any], key: str) -> str | None:
    return _optional_string_with_label(raw_config, key, f"`{key}`")


def _optional_string_with_label(
    raw_config: dict[str, Any],
    key: str,
    label: str,
) -> str | None:
    value = raw_config.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        stripped_value = value.strip()
        return stripped_value or None
    raise ConfigError(f"Optional config key {label} must be a string when set")


def _optional_git_remote_url(
    raw_config: dict[str, Any],
    key: str,
    config_path: Path,
) -> str | None:
    value = _optional_string(raw_config, key)
    if value is None:
        return None
    if is_git_remote_url(value):
        return value
    raise ConfigError(f"Config {config_path} `{key}` must be a valid Git remote URL")


def _reject_renamed_key(
    raw_config: dict[str, Any],
    *,
    old_key: str,
    new_key: str,
    config_path: Path,
) -> None:
    if old_key in raw_config:
        raise ConfigError(
            f"Config {config_path} uses removed key `{old_key}`; use `{new_key}`",
        )


def _optional_table(
    raw_config: dict[str, Any],
    key: str,
    config_path: Path,
) -> dict[str, Any]:
    value = raw_config.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ConfigError(f"Config {config_path} optional `{key}` must be a table")


def _modern_or_legacy_string(
    *,
    raw_config: dict[str, Any],
    table: dict[str, Any],
    alias: _TableLegacyAlias,
    config_path: Path,
) -> str | None:
    modern_value = _optional_string_with_label(
        table,
        alias.modern_key,
        f"`{alias.table_name}.{alias.modern_key}`",
    )
    legacy_value = _optional_string(raw_config, alias.legacy_key)
    if alias.modern_key in table and alias.legacy_key in raw_config:
        raise ConfigError(
            f"Config {config_path} sets both "
            f"`{alias.table_name}.{alias.modern_key}` "
            f"and legacy `{alias.legacy_key}`",
        )
    return modern_value or legacy_value


def _load_notifications(
    raw_config: dict[str, Any],
    config_path: Path,
) -> NotificationConfig:
    notifications = _optional_table(raw_config, "notifications", config_path)
    return NotificationConfig(
        initial_implementation_url=_notification_string(
            raw_config=raw_config,
            notifications=notifications,
            key="initial_implementation_url",
            config_path=config_path,
        ),
        comparison_url=_notification_string(
            raw_config=raw_config,
            notifications=notifications,
            key="comparison_url",
            config_path=config_path,
        ),
        comparison_implementation_url=_notification_string(
            raw_config=raw_config,
            notifications=notifications,
            key="comparison_implementation_url",
            config_path=config_path,
        ),
        review_input_needed_url=_notification_string(
            raw_config=raw_config,
            notifications=notifications,
            key="review_input_needed_url",
            config_path=config_path,
        ),
        open_pr_url=_notification_string(
            raw_config=raw_config,
            notifications=notifications,
            key="open_pr_url",
            config_path=config_path,
        ),
    )


def _notification_string(
    *,
    raw_config: dict[str, Any],
    notifications: dict[str, Any],
    key: str,
    config_path: Path,
) -> str | None:
    return _modern_or_legacy_string(
        raw_config=raw_config,
        table=notifications,
        alias=_NOTIFICATION_ALIASES[key],
        config_path=config_path,
    )


def _load_prompts(raw_config: dict[str, Any], config_path: Path) -> PromptConfig:
    prompts = _optional_table(raw_config, "prompts", config_path)
    comparison_judgment_file, gemini_comparison_file = _comparison_judgment_files(
        raw_config,
        prompts,
        config_path,
    )
    return PromptConfig(
        initial_implementation_file=_optional_string_with_label(
            prompts,
            "initial_implementation_file",
            "`prompts.initial_implementation_file`",
        ),
        comparison_judgment_file=comparison_judgment_file,
        gemini_comparison_file=gemini_comparison_file,
        comparison_implementation_file=_optional_string_with_label(
            prompts,
            "comparison_implementation_file",
            "`prompts.comparison_implementation_file`",
        ),
        review_judgment_file=_optional_string_with_label(
            prompts,
            "review_judgment_file",
            "`prompts.review_judgment_file`",
        ),
        review_fix_file=_optional_string_with_label(
            prompts,
            "review_fix_file",
            "`prompts.review_fix_file`",
        ),
    )


def _comparison_judgment_files(
    raw_config: dict[str, Any],
    prompts: dict[str, Any],
    config_path: Path,
) -> tuple[str | None, str | None]:
    comparison_judgment_file = _optional_string_with_label(
        prompts,
        "comparison_judgment_file",
        "`prompts.comparison_judgment_file`",
    )
    gemini_comparison_file = _modern_or_legacy_string(
        raw_config=raw_config,
        table=prompts,
        alias=_TableLegacyAlias(
            table_name="prompts",
            modern_key="gemini_comparison_file",
            legacy_key="gemini_comparison_prompt_file",
        ),
        config_path=config_path,
    )
    if comparison_judgment_file is not None and gemini_comparison_file is not None:
        raise ConfigError(
            f"Config {config_path} sets both "
            "`prompts.comparison_judgment_file` and a Gemini comparison prompt key",
        )
    return comparison_judgment_file or gemini_comparison_file, gemini_comparison_file


def _load_workflow(raw_config: dict[str, Any], config_path: Path) -> WorkflowConfig:
    workflow = _optional_table(raw_config, "workflow", config_path)
    implementers = _optional_string_sequence(
        workflow,
        "implementers",
        "`workflow.implementers`",
    ) or DEFAULT_IMPLEMENTERS
    if len(implementers) < 2:
        raise ConfigError(
            f"Config {config_path} `workflow.implementers` requires at least "
            "two agents",
        )
    if len(set(implementers)) != len(implementers):
        raise ConfigError(
            f"Config {config_path} `workflow.implementers` contains duplicate agents",
        )
    _validate_agent_names(implementers, config_path)

    comparison_judge = _optional_agent_name(
        workflow,
        "comparison_judge",
        "`workflow.comparison_judge`",
        config_path,
    ) or "gemini"
    comparison_fixer = _optional_agent_name(
        workflow,
        "comparison_fixer",
        "`workflow.comparison_fixer`",
        config_path,
    )
    review_provider = _optional_agent_name(
        workflow,
        "review_provider",
        "`workflow.review_provider`",
        config_path,
    ) or "coderabbit"
    review_judge = _optional_agent_name(
        workflow,
        "review_judge",
        "`workflow.review_judge`",
        config_path,
    ) or "codex"
    review_fixer = _optional_agent_name(
        workflow,
        "review_fixer",
        "`workflow.review_fixer`",
        config_path,
    ) or "codex"
    final_reviewer = _optional_agent_name(
        workflow,
        "final_reviewer",
        "`workflow.final_reviewer`",
        config_path,
    ) or "claude"
    return WorkflowConfig(
        implementers=implementers,
        comparison_judge=comparison_judge,
        comparison_fixer=comparison_fixer,
        review_provider=review_provider,
        review_judge=review_judge,
        review_fixer=review_fixer,
        final_reviewer=final_reviewer,
    )


def _load_comparison(raw_config: dict[str, Any], config_path: Path) -> ComparisonConfig:
    comparison = _optional_table(raw_config, "comparison", config_path)
    return ComparisonConfig(
        test_commands=_optional_string_sequence(
            comparison,
            "test_commands",
            "`comparison.test_commands`",
        ) or (),
        max_total_diff_bytes=_optional_positive_int(
            comparison,
            "max_total_diff_bytes",
            "`comparison.max_total_diff_bytes`",
        ) or DEFAULT_COMPARISON_MAX_TOTAL_DIFF_BYTES,
        max_file_diff_bytes=_optional_positive_int(
            comparison,
            "max_file_diff_bytes",
            "`comparison.max_file_diff_bytes`",
        ) or DEFAULT_COMPARISON_MAX_FILE_DIFF_BYTES,
        max_test_output_bytes=_optional_positive_int(
            comparison,
            "max_test_output_bytes",
            "`comparison.max_test_output_bytes`",
        ) or DEFAULT_COMPARISON_MAX_TEST_OUTPUT_BYTES,
    )


def _load_agents(raw_config: dict[str, Any], config_path: Path) -> AgentConfigs:
    raw_agents = _optional_table(raw_config, "agents", config_path)
    agent_configs: dict[str, AgentConfig] = {}
    for agent_name in raw_agents:
        _validate_agent_name(agent_name, config_path)
        agent_configs[agent_name] = _load_agent_config(
            raw_agents,
            agent_name,
            config_path,
        )
    return AgentConfigs(by_name=agent_configs)


def _load_agent_config(
    raw_agents: dict[str, Any],
    agent_name: str,
    config_path: Path,
) -> AgentConfig:
    agent_config = _optional_table(raw_agents, agent_name, config_path)
    return AgentConfig(
        adapter=_optional_agent_name(
            agent_config,
            "adapter",
            f"`agents.{agent_name}.adapter`",
            config_path,
        ),
        model=_optional_string_with_label(
            agent_config,
            "model",
            f"`agents.{agent_name}.model`",
        ),
    )


def _optional_string_sequence(
    raw_config: dict[str, Any],
    key: str,
    label: str,
) -> tuple[str, ...] | None:
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, list | tuple):
        raise ConfigError(f"Optional config key {label} must be an array when set")
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(
                f"Optional config key {label}[{index}] must be a non-empty string",
            )
        strings.append(item.strip())
    return tuple(strings)


def _optional_positive_int(
    raw_config: dict[str, Any],
    key: str,
    label: str,
) -> int | None:
    value = raw_config.get(key)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise ConfigError(f"Optional config key {label} must be a positive integer")


def _optional_agent_name(
    raw_config: dict[str, Any],
    key: str,
    label: str,
    config_path: Path,
) -> str | None:
    agent_name = _optional_string_with_label(raw_config, key, label)
    if agent_name is None:
        return None
    _validate_agent_name(agent_name, config_path)
    return agent_name


def _validate_agent_names(agent_names: tuple[str, ...], config_path: Path) -> None:
    for agent_name in agent_names:
        _validate_agent_name(agent_name, config_path)


def _validate_agent_name(agent_name: str, config_path: Path) -> None:
    if _AGENT_NAME_PATTERN.fullmatch(agent_name) is None:
        raise ConfigError(
            f"Config {config_path} agent name `{agent_name}` must contain only "
            "lowercase letters, numbers, and single hyphen separators",
        )


def _validate_agent_configuration(
    agents: AgentConfigs,
    workflow: WorkflowConfig,
    config_path: Path,
) -> None:
    for agent_name, agent_config in agents.by_name.items():
        _validate_agent_adapter(agent_name, agent_config, config_path)
    for agent_name in workflow.role_agent_names():
        agent_config = agents.get(agent_name)
        _validate_agent_adapter(agent_name, agent_config, config_path)
    for agent_name, capability in workflow.role_capabilities():
        adapter = _resolve_config_adapter(
            agent_name,
            agents.get(agent_name),
            config_path,
        )
        if not adapter.has_capability(capability):
            raise ConfigError(
                f"Config {config_path} agent `{agent_name}` uses adapter "
                f"`{adapter.name}`, which does not support `{capability}`",
            )


def _validate_agent_adapter(
    agent_name: str,
    agent_config: AgentConfig,
    config_path: Path,
) -> None:
    _resolve_config_adapter(agent_name, agent_config, config_path)


def _resolve_config_adapter(
    agent_name: str,
    agent_config: AgentConfig,
    config_path: Path,
) -> AgentAdapter:
    adapter_name = agent_config.adapter_name(agent_name)
    try:
        return resolve_adapter(adapter_name)
    except (DiamondDevError,) as error:
        raise ConfigError(
            f"Config {config_path} agent `{agent_name}` uses unknown adapter "
            f"`{adapter_name}`",
        ) from error
