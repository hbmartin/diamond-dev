"""Primitive TOML value readers shared by the config loader."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from diamond_dev.errors import ConfigError
from diamond_dev.naming import is_git_remote_url

_AGENT_NAME_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class TableLegacyAlias:
    """Mapping between a table key and its legacy top-level alias."""

    table_name: str
    modern_key: str
    legacy_key: str


def required_string(raw_config: dict[str, Any], key: str, config_path: Path) -> str:
    """Return a required non-empty string value."""
    value = raw_config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ConfigError(f"Config {config_path} requires a non-empty `{key}` string")


def required_git_remote_url(
    raw_config: dict[str, Any],
    key: str,
    config_path: Path,
) -> str:
    """Return a required Git remote URL value."""
    value = required_string(raw_config, key, config_path)
    if is_git_remote_url(value):
        return value
    raise ConfigError(f"Config {config_path} `{key}` must be a valid Git remote URL")


def optional_string(raw_config: dict[str, Any], key: str) -> str | None:
    """Return an optional string value."""
    return optional_string_with_label(raw_config, key, f"`{key}`")


def optional_string_with_label(
    raw_config: dict[str, Any],
    key: str,
    label: str,
) -> str | None:
    """Return an optional string value using a display label for errors."""
    value = raw_config.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        stripped_value = value.strip()
        return stripped_value or None
    raise ConfigError(f"Optional config key {label} must be a string when set")


def optional_git_remote_url(
    raw_config: dict[str, Any],
    key: str,
    config_path: Path,
) -> str | None:
    """Return an optional Git remote URL value."""
    value = optional_string(raw_config, key)
    if value is None:
        return None
    if is_git_remote_url(value):
        return value
    raise ConfigError(f"Config {config_path} `{key}` must be a valid Git remote URL")


def reject_renamed_key(
    raw_config: dict[str, Any],
    *,
    old_key: str,
    new_key: str,
    config_path: Path,
) -> None:
    """Fail when a removed legacy key is present."""
    if old_key in raw_config:
        raise ConfigError(
            f"Config {config_path} uses removed key `{old_key}`; use `{new_key}`",
        )


def optional_table(
    raw_config: dict[str, Any],
    key: str,
    config_path: Path,
) -> dict[str, Any]:
    """Return an optional TOML table value."""
    value = raw_config.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ConfigError(f"Config {config_path} optional `{key}` must be a table")


def modern_or_legacy_string(
    *,
    raw_config: dict[str, Any],
    table: dict[str, Any],
    alias: TableLegacyAlias,
    config_path: Path,
) -> str | None:
    """Return a table value, honoring its legacy top-level alias."""
    modern_value = optional_string_with_label(
        table,
        alias.modern_key,
        f"`{alias.table_name}.{alias.modern_key}`",
    )
    legacy_value = optional_string(raw_config, alias.legacy_key)
    if alias.modern_key in table and alias.legacy_key in raw_config:
        raise ConfigError(
            f"Config {config_path} sets both "
            f"`{alias.table_name}.{alias.modern_key}` "
            f"and legacy `{alias.legacy_key}`",
        )
    return modern_value or legacy_value


def optional_string_sequence(
    raw_config: dict[str, Any],
    key: str,
    label: str,
) -> tuple[str, ...] | None:
    """Return an optional array of non-empty strings."""
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


def optional_positive_int(
    raw_config: dict[str, Any],
    key: str,
    label: str,
) -> int | None:
    """Return an optional positive integer value."""
    value = raw_config.get(key)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise ConfigError(f"Optional config key {label} must be a positive integer")


def optional_agent_name(
    raw_config: dict[str, Any],
    key: str,
    label: str,
    config_path: Path,
) -> str | None:
    """Return an optional validated agent name."""
    agent_name = optional_string_with_label(raw_config, key, label)
    if agent_name is None:
        return None
    validate_agent_name(agent_name, config_path)
    return agent_name


def validate_agent_names(agent_names: tuple[str, ...], config_path: Path) -> None:
    """Validate a sequence of configured agent names."""
    for agent_name in agent_names:
        validate_agent_name(agent_name, config_path)


def validate_agent_name(agent_name: str, config_path: Path) -> None:
    """Validate one configured agent name."""
    if _AGENT_NAME_PATTERN.fullmatch(agent_name) is None:
        raise ConfigError(
            f"Config {config_path} agent name `{agent_name}` must contain only "
            "lowercase letters, numbers, and single hyphen separators",
        )
