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
class DiamondDevConfig:
    """Typed `.diamond-dev.toml` settings."""

    config_path: Path
    repository_url: str
    notes_repository_url: str | None = None
    gemini_comparison_prompt_file: str | None = None
    notifications: NotificationConfig = NotificationConfig()

    @property
    def config_dir(self) -> Path:
        """Return the directory containing the loaded config file."""
        return self.config_path.parent

    def gemini_prompt_path(self) -> Path | None:
        """Return the configured Gemini prompt file path, if any."""
        if self.gemini_comparison_prompt_file is None:
            return None

        prompt_path = Path(self.gemini_comparison_prompt_file)
        if prompt_path.is_absolute():
            return prompt_path
        return self.config_dir / prompt_path

    @property
    def notify_initial_implementation_url(self) -> str | None:
        """Return the initial-implementation notification URL."""
        return self.notifications.initial_implementation_url

    @property
    def notify_comparison_url(self) -> str | None:
        """Return the comparison-ready notification URL."""
        return self.notifications.comparison_url

    @property
    def notify_comparison_implementation_url(self) -> str | None:
        """Return the comparison-implementation notification URL."""
        return self.notifications.comparison_implementation_url

    @property
    def notify_review_input_needed_url(self) -> str | None:
        """Return the review-input-needed notification URL."""
        return self.notifications.review_input_needed_url

    @property
    def notify_open_pr_url(self) -> str | None:
        """Return the open-pull-request notification URL."""
        return self.notifications.open_pr_url


def load_config(cwd: Path) -> DiamondDevConfig:
    """Load and validate `.diamond-dev.toml` from the invocation directory."""
    config_path = cwd / CONFIG_FILE_NAME
    if not config_path.is_file():
        raise ConfigError(f"Missing required config file: {config_path}")

    try:
        with config_path.open("rb") as config_file:
            raw_config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(
            f"Could not read config file {config_path}: {error}",
        ) from error

    repository_url = _required_git_remote_url(raw_config, "repository_url", config_path)
    return DiamondDevConfig(
        config_path=config_path,
        repository_url=repository_url,
        notes_repository_url=_optional_string(raw_config, "notes_repository_url"),
        gemini_comparison_prompt_file=_optional_string(
            raw_config,
            "gemini_comparison_prompt_file",
        ),
        notifications=_load_notifications(raw_config),
    )


def read_gemini_prompt(config: DiamondDevConfig) -> str | None:
    """Read the optional Gemini comparison prompt file."""
    prompt_path = config.gemini_prompt_path()
    if prompt_path is None:
        return None
    if not prompt_path.is_file():
        raise ConfigError(f"Gemini comparison prompt file not found: {prompt_path}")
    try:
        return prompt_path.read_text(encoding="utf-8")
    except (OSError,) as error:
        raise ConfigError(
            f"Could not read Gemini comparison prompt file {prompt_path}: {error}",
        ) from error


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
    value = raw_config.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        stripped_value = value.strip()
        return stripped_value or None
    raise ConfigError(f"Optional config key `{key}` must be a string when set")


def _load_notifications(raw_config: dict[str, Any]) -> NotificationConfig:
    return NotificationConfig(
        initial_implementation_url=_optional_string(
            raw_config,
            "notify_initial_implementation_url",
        ),
        comparison_url=_optional_string(raw_config, "notify_comparison_url"),
        comparison_implementation_url=_optional_string(
            raw_config,
            "notify_comparison_implementation_url",
        ),
        review_input_needed_url=_optional_string(
            raw_config,
            "notify_review_input_needed_url",
        ),
        open_pr_url=_optional_string(raw_config, "notify_open_pr_url"),
    )
