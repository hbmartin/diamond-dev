"""Tests for fast external dependency preflight checks."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from diamond_dev import preflight
from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import (
    CommandLogRecord,
    CommandResult,
    CommandRunner,
    re_slug,
)


class _AuthRunner(CommandRunner):
    """Runner fake for `gh auth status` checks."""

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
        preflight.run_preflight(runner=_AuthRunner(tmp_path / "logs"), cwd=tmp_path)


def test_run_preflight_runs_gh_auth_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda cli_name: f"/usr/bin/{cli_name}",
    )
    runner = _AuthRunner(tmp_path / "logs")

    summary = preflight.run_preflight(runner=runner, cwd=tmp_path)

    assert runner.commands == [("gh", "auth", "status")]
    assert summary.gh_auth_log_path == tmp_path / "logs" / "preflight-gh-auth.log"
    assert summary.cli_checks[-1].name == "gh"
