"""Guided `.diamond-dev.toml` generation."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

from loguru import logger

from diamond_dev.config import load_config, resolve_config_path
from diamond_dev.errors import ConfigError
from diamond_dev.naming import is_git_remote_url

type LineReader = Callable[[], str]
type PromptWriter = Callable[[str], None]

_NOTIFICATION_URLS: Final = (
    ("initial_implementation_url", "Initial implementation notification URL"),
    ("comparison_url", "Comparison notification URL"),
    ("comparison_implementation_url", "Comparison implementation notification URL"),
    ("review_input_needed_url", "Review input-needed notification URL"),
    ("open_pr_url", "Open PR notification URL"),
)
_YES_ANSWERS: Final = frozenset({"y", "yes"})
_NO_ANSWERS: Final = frozenset({"", "n", "no"})
_NOTIFICATION_SCHEMES: Final = frozenset({"http", "https"})
_TOML_ESCAPES: Final = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


@dataclass(frozen=True, slots=True)
class GeneratedConfigInput:
    """Values collected by the guided config initializer."""

    repository_url: str
    wiki_repository_url: str | None = None
    notification_urls: dict[str, str] = field(default_factory=dict)


def run_config_init(
    cwd: Path,
    config_path: Path | None = None,
    *,
    force: bool = False,
    read_line: LineReader | None = None,
    write_prompt: PromptWriter | None = None,
) -> Path | None:
    """Interactively generate and validate a Diamond Dev config file."""
    target_path = resolve_config_path(cwd, config_path)
    reader = read_line or sys.stdin.readline
    writer = write_prompt or _write_prompt

    if not target_path.parent.is_dir():
        raise ConfigError(f"Config directory does not exist: {target_path.parent}")

    if (
        target_path.exists()
        and not force
        and not _confirm_overwrite(target_path, read_line=reader, write_prompt=writer)
    ):
        logger.info("Existing config left unchanged: {}", target_path)
        return None

    config_input = _collect_config_input(read_line=reader, write_prompt=writer)
    try:
        target_path.write_text(_build_config_toml(config_input), encoding="utf-8")
    except (OSError,) as error:
        raise ConfigError(
            f"Could not write config file {target_path}: {error}",
        ) from error

    load_config(cwd, config_path)
    logger.info("Wrote Diamond Dev config: {}", target_path)
    return target_path


def _confirm_overwrite(
    target_path: Path,
    *,
    read_line: LineReader,
    write_prompt: PromptWriter,
) -> bool:
    while True:
        answer = _prompt(
            f"Config file {target_path} already exists. Overwrite? [y/N]: ",
            read_line=read_line,
            write_prompt=write_prompt,
            label="overwrite confirmation",
        ).strip().lower()
        if answer in _YES_ANSWERS:
            return True
        if answer in _NO_ANSWERS:
            return False
        write_prompt("Please answer yes or no.\n")


def _collect_config_input(
    *,
    read_line: LineReader,
    write_prompt: PromptWriter,
) -> GeneratedConfigInput:
    repository_url = _prompt_required_git_remote_url(
        "Repository URL: ",
        key="repository_url",
        read_line=read_line,
        write_prompt=write_prompt,
    )
    wiki_repository_url = _prompt_optional_git_remote_url(
        "Wiki repository URL (optional): ",
        key="wiki_repository_url",
        read_line=read_line,
        write_prompt=write_prompt,
    )
    notification_urls = {
        key: url
        for key, label in _NOTIFICATION_URLS
        if (
            url := _prompt_optional_notification_url(
                f"{label} (optional): ",
                key=key,
                read_line=read_line,
                write_prompt=write_prompt,
            )
        )
        is not None
    }
    return GeneratedConfigInput(
        repository_url=repository_url,
        wiki_repository_url=wiki_repository_url,
        notification_urls=notification_urls,
    )


def _prompt_required_git_remote_url(
    prompt: str,
    *,
    key: str,
    read_line: LineReader,
    write_prompt: PromptWriter,
) -> str:
    while True:
        value = _prompt(
            prompt,
            read_line=read_line,
            write_prompt=write_prompt,
            label=key,
        ).strip()
        if not value:
            write_prompt(f"`{key}` is required.\n")
            continue
        if is_git_remote_url(value):
            return value
        write_prompt("Invalid Git remote URL. Try again.\n")


def _prompt_optional_git_remote_url(
    prompt: str,
    *,
    key: str,
    read_line: LineReader,
    write_prompt: PromptWriter,
) -> str | None:
    while True:
        value = _prompt(
            prompt,
            read_line=read_line,
            write_prompt=write_prompt,
            label=key,
        ).strip()
        if not value:
            return None
        if is_git_remote_url(value):
            return value
        write_prompt("Invalid Git remote URL. Try again.\n")


def _prompt_optional_notification_url(
    prompt: str,
    *,
    key: str,
    read_line: LineReader,
    write_prompt: PromptWriter,
) -> str | None:
    while True:
        value = _prompt(
            prompt,
            read_line=read_line,
            write_prompt=write_prompt,
            label=key,
        ).strip()
        if not value:
            return None
        if _is_notification_url(value):
            return value
        write_prompt("Notification URLs must be http or https URLs with a host.\n")


def _prompt(
    prompt: str,
    *,
    read_line: LineReader,
    write_prompt: PromptWriter,
    label: str,
) -> str:
    write_prompt(prompt)
    answer = read_line()
    if answer == "":
        raise ConfigError(f"Input ended while reading `{label}`")
    return answer.rstrip("\r\n")


def _is_notification_url(value: str) -> bool:
    try:
        parsed_url = urlparse(value)
        hostname = parsed_url.hostname
        _ = parsed_url.port
    except (ValueError,):
        return False
    return (
        parsed_url.scheme.lower() in _NOTIFICATION_SCHEMES
        and hostname is not None
    )


def _build_config_toml(config_input: GeneratedConfigInput) -> str:
    lines = [f"repository_url = {_toml_string(config_input.repository_url)}"]
    if config_input.wiki_repository_url is not None:
        lines.append(
            f"wiki_repository_url = {_toml_string(config_input.wiki_repository_url)}",
        )
    if config_input.notification_urls:
        lines.extend(("", "[notifications]"))
        lines.extend(
            f"{key} = {_toml_string(value)}"
            for key, value in config_input.notification_urls.items()
        )
    return "\n".join(lines) + "\n"


def _toml_string(value: str) -> str:
    return f'"{"".join(_toml_escaped_char(character) for character in value)}"'


def _toml_escaped_char(character: str) -> str:
    if character in _TOML_ESCAPES:
        return _TOML_ESCAPES[character]
    codepoint = ord(character)
    if codepoint < 0x20 or codepoint == 0x7F:
        return f"\\u{codepoint:04x}"
    return character


def _write_prompt(prompt: str) -> None:
    sys.stdout.write(prompt)
    sys.stdout.flush()
