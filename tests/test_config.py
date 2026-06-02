"""Tests for Diamond Dev config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.config import CONFIG_FILE_NAME, load_config, read_gemini_prompt
from diamond_dev.errors import ConfigError


def test_load_config_requires_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_reads_required_and_optional_values(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompts" / "compare.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("Compare these branches.", encoding="utf-8")
    (tmp_path / CONFIG_FILE_NAME).write_text(
        "\n".join(
            [
                'repository_url = "git@github.com:owner/repo.git"',
                'notes_repository_url = "git@github.com:owner/repo.wiki.git"',
                'gemini_comparison_prompt_file = "prompts/compare.md"',
                'notify_open_pr_url = "https://example.test/open-pr"',
            ],
        ),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.repository_url == "git@github.com:owner/repo.git"
    assert config.notes_repository_url == "git@github.com:owner/repo.wiki.git"
    assert config.gemini_prompt_path() == prompt_file
    assert read_gemini_prompt(config) == "Compare these branches."
    assert config.notify_open_pr_url == "https://example.test/open-pr"


def test_load_config_rejects_non_string_optional_value(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        "\n".join(
            [
                'repository_url = "git@github.com:owner/repo.git"',
                "notify_open_pr_url = 1",
            ],
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_read_gemini_prompt_requires_existing_file(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        "\n".join(
            [
                'repository_url = "git@github.com:owner/repo.git"',
                'gemini_comparison_prompt_file = "missing.md"',
            ],
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        read_gemini_prompt(load_config(tmp_path))
