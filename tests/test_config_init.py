"""Tests for guided config generation."""

from __future__ import annotations

import sys
from collections.abc import Callable
from io import StringIO
from pathlib import Path

import pytest

from diamond_dev.config import CONFIG_FILE_NAME, load_config
from diamond_dev.config_init import run_config_init
from diamond_dev.errors import ConfigError


def test_run_config_init_writes_minimal_config(tmp_path: Path) -> None:
    prompts: list[str] = []

    result = run_config_init(
        tmp_path,
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=prompts.append,
    )

    config_path = tmp_path / CONFIG_FILE_NAME
    assert result == config_path
    assert config_path.read_text(encoding="utf-8") == (
        'repository_url = "git@github.com:owner/repo.git"\n'
    )
    config = load_config(tmp_path)
    assert config.repository_url == "git@github.com:owner/repo.git"
    assert config.wiki_repository_url is None
    assert config.notifications.initial_implementation_url is None
    assert prompts[0] == "Repository URL: "


def test_run_config_init_writes_wiki_and_notification_urls(tmp_path: Path) -> None:
    run_config_init(
        tmp_path,
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "git@github.com:owner/repo.wiki.git\n",
            "https://example.test/initial\n",
            "\n",
            "https://example.test/followup\n",
            "\n",
            "https://example.test/open-pr\n",
        ),
        write_prompt=lambda _prompt: None,
    )

    config_path = tmp_path / CONFIG_FILE_NAME
    assert config_path.read_text(encoding="utf-8") == (
        'repository_url = "git@github.com:owner/repo.git"\n'
        'wiki_repository_url = "git@github.com:owner/repo.wiki.git"\n'
        "\n"
        "[notifications]\n"
        'initial_implementation_url = "https://example.test/initial"\n'
        'comparison_implementation_url = "https://example.test/followup"\n'
        'open_pr_url = "https://example.test/open-pr"\n'
    )
    config = load_config(tmp_path)
    assert config.wiki_repository_url == "git@github.com:owner/repo.wiki.git"
    assert config.notifications.initial_implementation_url == (
        "https://example.test/initial"
    )
    assert config.notifications.comparison_url is None
    assert config.notifications.comparison_implementation_url == (
        "https://example.test/followup"
    )
    assert config.notifications.review_input_needed_url is None
    assert config.notifications.open_pr_url == "https://example.test/open-pr"


def test_run_config_init_validates_relative_config_path_from_relative_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    config_dir = project_dir / "configs"
    config_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    result = run_config_init(
        Path("project"),
        Path("configs/diamond.toml"),
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=lambda _prompt: None,
    )

    assert result == Path("project/configs/diamond.toml")
    assert load_config(
        project_dir,
        Path("configs/diamond.toml"),
    ).repository_url == "git@github.com:owner/repo.git"


def test_run_config_init_escapes_toml_strings(tmp_path: Path) -> None:
    notification_url = 'https://example.test/hook?name="diamond"&path=C:\\tmp&del=\x7f'

    run_config_init(
        tmp_path,
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "\n",
            f"{notification_url}\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=lambda _prompt: None,
    )

    config_text = (tmp_path / CONFIG_FILE_NAME).read_text(encoding="utf-8")
    assert '\\"diamond\\"' in config_text
    assert "C:\\\\tmp" in config_text
    assert "\\u007f" in config_text
    assert load_config(
        tmp_path,
    ).notifications.initial_implementation_url == notification_url


def test_run_config_init_reprompts_invalid_repository_url(tmp_path: Path) -> None:
    prompts: list[str] = []

    run_config_init(
        tmp_path,
        read_line=_answers(
            "not a remote\n",
            "git@github.com:owner/repo.git\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=prompts.append,
    )

    assert "Invalid Git remote URL. Try again.\n" in prompts
    assert load_config(tmp_path).repository_url == "git@github.com:owner/repo.git"


def test_run_config_init_reprompts_invalid_notification_url(tmp_path: Path) -> None:
    prompts: list[str] = []

    run_config_init(
        tmp_path,
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "\n",
            "ftp://example.test/hook\n",
            "https://example.test/hook\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=prompts.append,
    )

    assert "Notification URLs must be http or https URLs with a host.\n" in prompts
    assert load_config(
        tmp_path,
    ).notifications.initial_implementation_url == "https://example.test/hook"


def test_run_config_init_reprompts_malformed_notification_url(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    run_config_init(
        tmp_path,
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "\n",
            "http://[example.com]\n",
            "https://example.test/hook\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=prompts.append,
    )

    assert "Notification URLs must be http or https URLs with a host.\n" in prompts
    assert load_config(
        tmp_path,
    ).notifications.initial_implementation_url == "https://example.test/hook"


def test_run_config_init_writes_default_prompts_to_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    run_config_init(
        tmp_path,
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
    )

    assert stdout.getvalue().startswith("Repository URL: ")


def test_run_config_init_leaves_existing_file_when_overwrite_declined(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / CONFIG_FILE_NAME
    config_path.write_text("keep me", encoding="utf-8")

    result = run_config_init(
        tmp_path,
        read_line=_answers("n\n"),
        write_prompt=lambda _prompt: None,
    )

    assert result is None
    assert config_path.read_text(encoding="utf-8") == "keep me"


def test_run_config_init_overwrites_existing_file_when_confirmed(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / CONFIG_FILE_NAME
    config_path.write_text("replace me", encoding="utf-8")

    result = run_config_init(
        tmp_path,
        read_line=_answers(
            "yes\n",
            "git@github.com:owner/repo.git\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=lambda _prompt: None,
    )

    assert result == config_path
    assert load_config(tmp_path).repository_url == "git@github.com:owner/repo.git"


def test_run_config_init_force_overwrites_without_confirmation(tmp_path: Path) -> None:
    config_path = tmp_path / CONFIG_FILE_NAME
    config_path.write_text("replace me", encoding="utf-8")

    run_config_init(
        tmp_path,
        force=True,
        read_line=_answers(
            "git@github.com:owner/repo.git\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
            "\n",
        ),
        write_prompt=lambda _prompt: None,
    )

    assert load_config(tmp_path).repository_url == "git@github.com:owner/repo.git"


def test_run_config_init_rejects_eof_on_required_input(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="repository_url"):
        run_config_init(
            tmp_path,
            read_line=_answers(),
            write_prompt=lambda _prompt: None,
        )


def test_run_config_init_rejects_missing_target_parent(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config directory does not exist"):
        run_config_init(
            tmp_path,
            Path("missing/config.toml"),
            read_line=_answers(),
            write_prompt=lambda _prompt: None,
        )


def _answers(*answers: str) -> Callable[[], str]:
    answer_iterator = iter(answers)

    def read_line() -> str:
        try:
            return next(answer_iterator)
        except (StopIteration,):
            return ""

    return read_line
