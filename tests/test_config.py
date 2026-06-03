"""Tests for Diamond Dev config loading."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from diamond_dev.config import (
    CONFIG_FILE_NAME,
    DiamondDevConfig,
    load_config,
    read_gemini_prompt,
    read_prompt_file,
)
from diamond_dev.errors import ConfigError


def test_load_config_requires_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_reads_required_and_optional_values(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompts" / "compare.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("Compare these branches.", encoding="utf-8")
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'wiki_repository_url = "git@github.com:owner/repo.wiki.git"\n'
        'gemini_comparison_prompt_file = "prompts/compare.md"\n'
        'notify_initial_implementation_url = "https://example.test/initial"\n'
        'notify_comparison_url = "https://example.test/comparison"\n'
        'notify_comparison_implementation_url = "https://example.test/followup"\n'
        'notify_review_input_needed_url = "https://example.test/review"\n'
        'notify_open_pr_url = "https://example.test/open-pr"',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.repository_url == "git@github.com:owner/repo.git"
    assert config.wiki_repository_url == "git@github.com:owner/repo.wiki.git"
    assert config.gemini_prompt_path() == prompt_file
    assert read_gemini_prompt(config) == "Compare these branches."
    assert config.notifications.initial_implementation_url == (
        "https://example.test/initial"
    )
    assert config.notifications.comparison_url == "https://example.test/comparison"
    assert config.notifications.comparison_implementation_url == (
        "https://example.test/followup"
    )
    assert config.notifications.review_input_needed_url == (
        "https://example.test/review"
    )
    assert config.notifications.open_pr_url == "https://example.test/open-pr"


def test_load_config_reads_config_tables(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    initial_prompt = prompt_dir / "initial.md"
    initial_prompt.write_text("Implement carefully.", encoding="utf-8")
    gemini_prompt = prompt_dir / "compare.md"
    gemini_prompt.write_text("Compare deeply.", encoding="utf-8")
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'wiki_repository_url = "git@github.com:owner/repo.wiki.git"\n'
        "[notifications]\n"
        'initial_implementation_url = "https://example.test/initial"\n'
        'comparison_url = "https://example.test/comparison"\n'
        'comparison_implementation_url = "https://example.test/followup"\n'
        'review_input_needed_url = "https://example.test/review"\n'
        'open_pr_url = "https://example.test/open-pr"\n'
        "[prompts]\n"
        'initial_implementation_file = "prompts/initial.md"\n'
        'gemini_comparison_file = "prompts/compare.md"\n'
        'comparison_implementation_file = "prompts/followup.md"\n'
        'review_judgment_file = "prompts/judgment.md"\n'
        'review_fix_file = "prompts/fixes.md"\n'
        "[agents.codex]\n"
        'model = "gpt-5"\n'
        "[agents.claude]\n"
        'model = "opus"\n'
        "[agents.gemini]\n"
        'model = "gemini-3"',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.notifications.open_pr_url == "https://example.test/open-pr"
    assert config.prompts.initial_implementation_file == "prompts/initial.md"
    assert config.prompts.gemini_comparison_file == "prompts/compare.md"
    assert config.gemini_comparison_prompt_file == "prompts/compare.md"
    assert read_prompt_file(
        config,
        config.prompts.initial_implementation_file,
        label="Initial implementation prompt",
    ) == "Implement carefully."
    assert read_gemini_prompt(config) == "Compare deeply."
    assert config.prompt_path(config.prompts.initial_implementation_file) == initial_prompt
    assert config.gemini_prompt_path() == gemini_prompt
    assert config.agents.codex.model == "gpt-5"
    assert config.agents.claude.model == "opus"
    assert config.agents.gemini.model == "gemini-3"


def test_load_config_rejects_removed_notes_repository_url(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'notes_repository_url = "git@github.com:owner/repo.wiki.git"',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="wiki_repository_url"):
        load_config(tmp_path)


def test_load_config_reads_explicit_relative_config_path(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = config_dir / "diamond.toml"
    config_path.write_text(
        'repository_url = "git@github.com:owner/repo.git"',
        encoding="utf-8",
    )

    config = load_config(tmp_path, Path("configs/diamond.toml"))

    assert config.config_path == config_path
    assert config.config_dir == config_dir


def test_direct_config_legacy_gemini_prompt_path(tmp_path: Path) -> None:
    config = DiamondDevConfig(
        config_path=tmp_path / CONFIG_FILE_NAME,
        repository_url="git@github.com:owner/repo.git",
        gemini_comparison_prompt_file="compare.md",
    )

    assert config.gemini_prompt_path() == tmp_path / "compare.md"


def test_load_config_rejects_non_string_optional_value(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        "notify_open_pr_url = 1",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "config_text",
    [
        (
            'repository_url = "git@github.com:owner/repo.git"\n'
            'notify_open_pr_url = "https://example.test/legacy"\n'
            "[notifications]\n"
            'open_pr_url = "https://example.test/table"'
        ),
        (
            'repository_url = "git@github.com:owner/repo.git"\n'
            'gemini_comparison_prompt_file = "legacy.md"\n'
            "[prompts]\n"
            'gemini_comparison_file = "table.md"'
        ),
    ],
)
def test_load_config_rejects_legacy_table_conflicts(
    tmp_path: Path,
    config_text: str,
) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(config_text, encoding="utf-8")

    with pytest.raises(ConfigError, match="sets both"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "config_text",
    [
        'repository_url = "git@github.com:owner/repo.git"\nnotifications = "bad"',
        'repository_url = "git@github.com:owner/repo.git"\nprompts = "bad"',
        'repository_url = "git@github.com:owner/repo.git"\nagents = "bad"',
        (
            'repository_url = "git@github.com:owner/repo.git"\n'
            "[agents]\n"
            'codex = "bad"'
        ),
    ],
)
def test_load_config_rejects_bad_table_types(
    tmp_path: Path,
    config_text: str,
) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(config_text, encoding="utf-8")

    with pytest.raises(ConfigError, match="must be a table"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "repository_url",
    ["owner/repo", "https://", "git@github.com"],
)
def test_load_config_rejects_malformed_repository_url(
    tmp_path: Path,
    repository_url: str,
) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        f'repository_url = "{repository_url}"',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="valid Git remote URL"):
        load_config(tmp_path)


def test_load_config_wraps_malformed_toml(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'repository_url = "git@github.com:owner/repo.git"\n[',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_read_gemini_prompt_requires_existing_file(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'gemini_comparison_prompt_file = "missing.md"',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        read_gemini_prompt(load_config(tmp_path))


def test_read_gemini_prompt_wraps_read_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompt_file = tmp_path / "compare.md"
    prompt_file.write_text("Compare these branches.", encoding="utf-8")
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'gemini_comparison_prompt_file = "compare.md"',
        encoding="utf-8",
    )

    def fail_read_text(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    with pytest.raises(ConfigError):
        read_gemini_prompt(load_config(tmp_path))
