"""Configuration loading for Diamond Dev."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from diamond_dev.errors import ConfigError
from diamond_dev.naming import is_git_remote_url

CONFIG_FILE_NAME: Final = ".diamond-dev.toml"


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
    gemini_comparison_file: str | None = None
    comparison_implementation_file: str | None = None
    review_judgment_file: str | None = None
    review_fix_file: str | None = None


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """External agent command settings."""

    model: str | None = None


@dataclass(frozen=True, slots=True)
class AgentConfigs:
    """External agent settings by agent name."""

    codex: AgentConfig = AgentConfig()
    claude: AgentConfig = AgentConfig()
    gemini: AgentConfig = AgentConfig()


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
    agents: AgentConfigs = AgentConfigs()

    @property
    def config_dir(self) -> Path:
        """Return the directory containing the loaded config file."""
        return self.config_path.parent

    def gemini_prompt_path(self) -> Path | None:
        """Return the configured Gemini prompt file path, if any."""
        return self.prompt_path(
            self.prompts.gemini_comparison_file
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
    return DiamondDevConfig(
        config_path=resolved_config_path,
        repository_url=repository_url,
        wiki_repository_url=_optional_git_remote_url(
            raw_config,
            "wiki_repository_url",
            resolved_config_path,
        ),
        gemini_comparison_prompt_file=prompts.gemini_comparison_file,
        notifications=_load_notifications(raw_config, resolved_config_path),
        prompts=prompts,
        agents=_load_agents(raw_config, resolved_config_path),
    )


def read_gemini_prompt(config: DiamondDevConfig) -> str | None:
    """Read the optional Gemini comparison prompt file."""
    return read_prompt_file(
        config,
        config.prompts.gemini_comparison_file
        or config.gemini_comparison_prompt_file,
        label="Gemini comparison prompt",
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
    except (OSError,) as error:
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
    return PromptConfig(
        initial_implementation_file=_optional_string_with_label(
            prompts,
            "initial_implementation_file",
            "`prompts.initial_implementation_file`",
        ),
        gemini_comparison_file=_modern_or_legacy_string(
            raw_config=raw_config,
            table=prompts,
            alias=_TableLegacyAlias(
                table_name="prompts",
                modern_key="gemini_comparison_file",
                legacy_key="gemini_comparison_prompt_file",
            ),
            config_path=config_path,
        ),
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


def _load_agents(raw_config: dict[str, Any], config_path: Path) -> AgentConfigs:
    agents = _optional_table(raw_config, "agents", config_path)
    return AgentConfigs(
        codex=_load_agent_config(agents, "codex", config_path),
        claude=_load_agent_config(agents, "claude", config_path),
        gemini=_load_agent_config(agents, "gemini", config_path),
    )


def _load_agent_config(
    raw_agents: dict[str, Any],
    agent_name: str,
    config_path: Path,
) -> AgentConfig:
    agent_config = _optional_table(raw_agents, agent_name, config_path)
    return AgentConfig(
        model=_optional_string_with_label(
            agent_config,
            "model",
            f"`agents.{agent_name}.model`",
        ),
    )
