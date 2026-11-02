"""Reading configured prompt override files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from diamond_dev.errors import ConfigError

if TYPE_CHECKING:
    from diamond_dev.config.schema import DiamondDevConfig


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
