"""Tests for fast external dependency preflight checks."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from diamond_dev import preflight
from diamond_dev.config import DiamondDevConfig
from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import (
    CommandLogRecord,
    CommandResult,
    CommandRunner,
    re_slug,
)


def _config(tmp_path: Path) -> DiamondDevConfig:
    return DiamondDevConfig(
        config_path=tmp_path / ".diamond-dev.toml",
        repository_url="git@github.com:owner/repo.git",
        wiki_repository_url="git@github.com:owner/repo.wiki.git",
    )


class _AuthRunner(CommandRunner):
    """Runner fake for doctor command checks."""

    def __init__(self, log_dir: Path) -> None:
        super().__init__(log_dir)
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert isinstance(check, bool)
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        log_path = self.log_dir / f"{re_slug(log_name)}.log"
        self.command_logs.append(
            CommandLogRecord(
                label=log_name,
                command=command_tuple,
                cwd=cwd,
                log_path=log_path,
            ),
        )
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=0,
            log_path=log_path,
            output="",
        )


def test_run_preflight_requires_all_cli_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def which(cli_name: str) -> str | None:
        if cli_name == "gemini":
            return None
        return f"/usr/bin/{cli_name}"

    monkeypatch.setattr(preflight.shutil, "which", which)

    with pytest.raises(DiamondDevError, match="gemini"):
        preflight.run_preflight(
            runner=_AuthRunner(tmp_path / "logs"),
            cwd=tmp_path,
            config=_config(tmp_path),
            required_cli_names=("codex", "claude", "gemini", "coderabbit"),
        )


def test_run_preflight_runs_doctor_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda cli_name: f"/usr/bin/{cli_name}",
    )
    runner = _AuthRunner(tmp_path / "logs")

    summary = preflight.run_preflight(
        runner=runner,
        cwd=tmp_path,
        config=_config(tmp_path),
        required_cli_names=("codex", "claude", "gemini", "coderabbit"),
    )

    assert runner.commands[:5] == [
        ("gh", "auth", "status"),
        ("codex", "login", "status"),
        ("claude", "auth", "status", "--text"),
        (
            "gemini",
            "-p",
            preflight._DOCTOR_GEMINI_PROMPT,  # noqa: SLF001
            "--skip-trust",
        ),
        ("coderabbit", "auth", "status", "--agent"),
    ]
    assert ("git", "ls-remote", "git@github.com:owner/repo.wiki.git") in (
        runner.commands
    )
    assert any(
        command[:3] == ("git", "push", "--dry-run")
        and command[3] == "git@github.com:owner/repo.wiki.git"
        and command[4].startswith("HEAD:refs/heads/diamond-dev-doctor-")
        for command in runner.commands
    )
    assert summary.gh_auth_log_path == tmp_path / "logs" / "preflight-gh-auth.log"
    assert any(cli_check.name == "gh" for cli_check in summary.cli_checks)
    assert [check.agent_name for check in summary.agent_auth_checks] == [
        "codex",
        "claude",
        "gemini",
        "coderabbit",
    ]
    assert summary.wiki_access_check is not None
    assert summary.wiki_access_check.url == "git@github.com:owner/repo.wiki.git"
    assert {check.label for check in summary.write_permission_checks} == {
        "workspace",
        "logs",
    }
