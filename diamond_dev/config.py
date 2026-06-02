"""Configuration loading for Diamond Dev."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from diamond_dev.errors import ConfigError

CONFIG_FILE_NAME: Final = ".diamond-dev.toml"


@dataclass(frozen=True, slots=True)
class DiamondDevConfig:
    """Typed `.diamond-dev.toml` settings."""

    config_path: Path
    repository_url: str
    notes_repository_url: str | None = None
    gemini_comparison_prompt_file: str | None = None
    notify_initial_implementation_url: str | None = None
    notify_comparison_url: str | None = None
    notify_comparison_implementation_url: str | None = None
    notify_review_input_needed_url: str | None = None
    notify_open_pr_url: str | None = None

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


def load_config(cwd: Path) -> DiamondDevConfig:
    """Load and validate `.diamond-dev.toml` from the invocation directory."""
    config_path = cwd / CONFIG_FILE_NAME
    if not config_path.is_file():
        raise ConfigError(f"Missing required config file: {config_path}")

    with config_path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    repository_url = _required_string(raw_config, "repository_url", config_path)
    return DiamondDevConfig(
        config_path=config_path,
        repository_url=repository_url,
        notes_repository_url=_optional_string(raw_config, "notes_repository_url"),
        gemini_comparison_prompt_file=_optional_string(
            raw_config,
            "gemini_comparison_prompt_file",
        ),
        notify_initial_implementation_url=_optional_string(
            raw_config,
            "notify_initial_implementation_url",
        ),
        notify_comparison_url=_optional_string(raw_config, "notify_comparison_url"),
        notify_comparison_implementation_url=_optional_string(
            raw_config,
            "notify_comparison_implementation_url",
        ),
        notify_review_input_needed_url=_optional_string(
            raw_config,
            "notify_review_input_needed_url",
        ),
        notify_open_pr_url=_optional_string(raw_config, "notify_open_pr_url"),
    )


def read_gemini_prompt(config: DiamondDevConfig) -> str | None:
    """Read the optional Gemini comparison prompt file."""
    prompt_path = config.gemini_prompt_path()
    if prompt_path is None:
        return None
    if not prompt_path.is_file():
        raise ConfigError(f"Gemini comparison prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _required_string(raw_config: dict[str, Any], key: str, config_path: Path) -> str:
    value = raw_config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ConfigError(f"Config {config_path} requires a non-empty `{key}` string")


def _optional_string(raw_config: dict[str, Any], key: str) -> str | None:
    value = raw_config.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        stripped_value = value.strip()
        return stripped_value or None
    raise ConfigError(f"Optional config key `{key}` must be a string when set")
