"""Loading and validation for `.diamond-dev.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Final, cast

from diamond_dev.acceptance import (
    DEFAULT_ACCEPTANCE_MAX_WAIT_SECONDS,
    DEFAULT_ACCEPTANCE_POLL_INTERVAL_SECONDS,
)
from diamond_dev.agents import AgentAdapter, resolve_adapter
from diamond_dev.config.schema import (
    CONFIG_FILE_NAME,
    DEFAULT_COMPARISON_MAX_FILE_DIFF_BYTES,
    DEFAULT_COMPARISON_MAX_SIGNAL_OUTPUT_BYTES,
    DEFAULT_COMPARISON_MAX_TEST_OUTPUT_BYTES,
    DEFAULT_COMPARISON_MAX_TOTAL_DIFF_BYTES,
    DEFAULT_IMPLEMENTERS,
    AcceptanceConfig,
    AgentConfig,
    AgentConfigs,
    ComparisonConfig,
    DiamondDevConfig,
    NotificationConfig,
    PromptConfig,
    WorkflowConfig,
)
from diamond_dev.config.values import (
    TableLegacyAlias,
    modern_or_legacy_string,
    optional_agent_name,
    optional_git_remote_url,
    optional_positive_int,
    optional_string_sequence,
    optional_string_with_label,
    optional_table,
    reject_renamed_key,
    required_git_remote_url,
    validate_agent_name,
    validate_agent_names,
)
from diamond_dev.errors import ConfigError, DiamondDevError
from diamond_dev.notify import (
    DEFAULT_NOTIFICATION_FORMAT,
    NOTIFICATION_FORMATS,
    NotificationFormat,
)

_NOTIFICATION_ALIASES: Final = {
    "initial_implementation_url": TableLegacyAlias(
        table_name="notifications",
        modern_key="initial_implementation_url",
        legacy_key="notify_initial_implementation_url",
    ),
    "comparison_url": TableLegacyAlias(
        table_name="notifications",
        modern_key="comparison_url",
        legacy_key="notify_comparison_url",
    ),
    "comparison_implementation_url": TableLegacyAlias(
        table_name="notifications",
        modern_key="comparison_implementation_url",
        legacy_key="notify_comparison_implementation_url",
    ),
    "review_input_needed_url": TableLegacyAlias(
        table_name="notifications",
        modern_key="review_input_needed_url",
        legacy_key="notify_review_input_needed_url",
    ),
    "open_pr_url": TableLegacyAlias(
        table_name="notifications",
        modern_key="open_pr_url",
        legacy_key="notify_open_pr_url",
    ),
}


def load_config(cwd: Path, config_path: Path | None = None) -> DiamondDevConfig:
    """Load and validate `.diamond-dev.toml` from the invocation directory."""
    resolved_config_path = resolve_config_path(cwd, config_path)
    if not resolved_config_path.is_file():
        raise ConfigError(f"Missing required config file: {resolved_config_path}")

    try:
        with resolved_config_path.open("rb") as config_file:
            raw_config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(
            f"Could not read config file {resolved_config_path}: {error}",
        ) from error

    repository_url = required_git_remote_url(
        raw_config,
        "repository_url",
        resolved_config_path,
    )
    reject_renamed_key(
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
        wiki_repository_url=optional_git_remote_url(
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
        acceptance=_load_acceptance(raw_config, resolved_config_path),
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


def resolve_config_path(cwd: Path, config_path: Path | None) -> Path:
    """Return the absolute or cwd-relative config path to load."""
    if config_path is None:
        return cwd / CONFIG_FILE_NAME
    if config_path.is_absolute():
        return config_path
    return cwd / config_path


def _load_notifications(
    raw_config: dict[str, Any],
    config_path: Path,
) -> NotificationConfig:
    notifications = optional_table(raw_config, "notifications", config_path)
    return NotificationConfig(
        format=_notification_format(notifications, config_path),
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


def _notification_format(
    notifications: dict[str, Any],
    config_path: Path,
) -> NotificationFormat:
    value = optional_string_with_label(
        notifications,
        "format",
        "`notifications.format`",
    )
    if value is None:
        return DEFAULT_NOTIFICATION_FORMAT
    if value not in NOTIFICATION_FORMATS:
        allowed = ", ".join(sorted(NOTIFICATION_FORMATS))
        raise ConfigError(
            f"Config {config_path} `notifications.format` must be one of: {allowed}",
        )
    return cast("NotificationFormat", value)


def _notification_string(
    *,
    raw_config: dict[str, Any],
    notifications: dict[str, Any],
    key: str,
    config_path: Path,
) -> str | None:
    return modern_or_legacy_string(
        raw_config=raw_config,
        table=notifications,
        alias=_NOTIFICATION_ALIASES[key],
        config_path=config_path,
    )


def _load_prompts(raw_config: dict[str, Any], config_path: Path) -> PromptConfig:
    prompts = optional_table(raw_config, "prompts", config_path)
    comparison_judgment_file, gemini_comparison_file = _comparison_judgment_files(
        raw_config,
        prompts,
        config_path,
    )
    return PromptConfig(
        initial_implementation_file=optional_string_with_label(
            prompts,
            "initial_implementation_file",
            "`prompts.initial_implementation_file`",
        ),
        comparison_judgment_file=comparison_judgment_file,
        gemini_comparison_file=gemini_comparison_file,
        comparison_implementation_file=optional_string_with_label(
            prompts,
            "comparison_implementation_file",
            "`prompts.comparison_implementation_file`",
        ),
        review_judgment_file=optional_string_with_label(
            prompts,
            "review_judgment_file",
            "`prompts.review_judgment_file`",
        ),
        review_fix_file=optional_string_with_label(
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
    comparison_judgment_file = optional_string_with_label(
        prompts,
        "comparison_judgment_file",
        "`prompts.comparison_judgment_file`",
    )
    gemini_comparison_file = modern_or_legacy_string(
        raw_config=raw_config,
        table=prompts,
        alias=TableLegacyAlias(
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
    workflow = optional_table(raw_config, "workflow", config_path)
    configured_implementers = optional_string_sequence(
        workflow,
        "implementers",
        "`workflow.implementers`",
    )
    implementers = (
        DEFAULT_IMPLEMENTERS
        if configured_implementers is None
        else configured_implementers
    )
    if len(implementers) < 2:
        raise ConfigError(
            f"Config {config_path} `workflow.implementers` requires at least "
            "two agents",
        )
    if len(set(implementers)) != len(implementers):
        raise ConfigError(
            f"Config {config_path} `workflow.implementers` contains duplicate agents",
        )
    validate_agent_names(implementers, config_path)

    comparison_judge = optional_agent_name(
        workflow,
        "comparison_judge",
        "`workflow.comparison_judge`",
        config_path,
    ) or "gemini"
    comparison_fixer = optional_agent_name(
        workflow,
        "comparison_fixer",
        "`workflow.comparison_fixer`",
        config_path,
    )
    review_provider = optional_agent_name(
        workflow,
        "review_provider",
        "`workflow.review_provider`",
        config_path,
    ) or "coderabbit"
    review_judge = optional_agent_name(
        workflow,
        "review_judge",
        "`workflow.review_judge`",
        config_path,
    ) or "codex"
    review_fixer = optional_agent_name(
        workflow,
        "review_fixer",
        "`workflow.review_fixer`",
        config_path,
    ) or "codex"
    final_reviewer = optional_agent_name(
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
    comparison = optional_table(raw_config, "comparison", config_path)
    return ComparisonConfig(
        test_commands=optional_string_sequence(
            comparison,
            "test_commands",
            "`comparison.test_commands`",
        ) or (),
        signal_commands=optional_string_sequence(
            comparison,
            "signal_commands",
            "`comparison.signal_commands`",
        ) or (),
        max_total_diff_bytes=optional_positive_int(
            comparison,
            "max_total_diff_bytes",
            "`comparison.max_total_diff_bytes`",
        ) or DEFAULT_COMPARISON_MAX_TOTAL_DIFF_BYTES,
        max_file_diff_bytes=optional_positive_int(
            comparison,
            "max_file_diff_bytes",
            "`comparison.max_file_diff_bytes`",
        ) or DEFAULT_COMPARISON_MAX_FILE_DIFF_BYTES,
        max_test_output_bytes=optional_positive_int(
            comparison,
            "max_test_output_bytes",
            "`comparison.max_test_output_bytes`",
        ) or DEFAULT_COMPARISON_MAX_TEST_OUTPUT_BYTES,
        max_signal_output_bytes=optional_positive_int(
            comparison,
            "max_signal_output_bytes",
            "`comparison.max_signal_output_bytes`",
        ) or DEFAULT_COMPARISON_MAX_SIGNAL_OUTPUT_BYTES,
    )


def _load_acceptance(raw_config: dict[str, Any], config_path: Path) -> AcceptanceConfig:
    acceptance = optional_table(raw_config, "acceptance", config_path)
    return AcceptanceConfig(
        poll_interval_seconds=optional_positive_int(
            acceptance,
            "poll_interval_seconds",
            "`acceptance.poll_interval_seconds`",
        )
        or DEFAULT_ACCEPTANCE_POLL_INTERVAL_SECONDS,
        max_wait_seconds=optional_positive_int(
            acceptance,
            "max_wait_seconds",
            "`acceptance.max_wait_seconds`",
        )
        or DEFAULT_ACCEPTANCE_MAX_WAIT_SECONDS,
    )


def _load_agents(raw_config: dict[str, Any], config_path: Path) -> AgentConfigs:
    raw_agents = optional_table(raw_config, "agents", config_path)
    agent_configs: dict[str, AgentConfig] = {}
    for agent_name in raw_agents:
        validate_agent_name(agent_name, config_path)
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
    agent_config = optional_table(raw_agents, agent_name, config_path)
    return AgentConfig(
        adapter=optional_agent_name(
            agent_config,
            "adapter",
            f"`agents.{agent_name}.adapter`",
            config_path,
        ),
        model=optional_string_with_label(
            agent_config,
            "model",
            f"`agents.{agent_name}.model`",
        ),
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
