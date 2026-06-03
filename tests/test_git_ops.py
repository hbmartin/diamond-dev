"""Tests for git operation helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import CommandResult, CommandRunner
from diamond_dev.git_ops import GitOperations


def test_remote_default_branch_rejects_empty_symbolic_ref_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)

    def empty_symbolic_ref(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert check
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=0,
            log_path=tmp_path / f"{log_name}.log",
            output=" \n",
        )

    monkeypatch.setattr(runner, "run", empty_symbolic_ref)

    with pytest.raises(DiamondDevError, match="No output returned"):
        git.remote_default_branch(tmp_path)


def test_local_branch_exists_checks_only_local_heads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)
    commands: list[tuple[str, ...]] = []

    def branch_missing(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert cwd == tmp_path
        assert log_name == "local-branch"
        assert not check
        command_tuple = tuple(command)
        commands.append(command_tuple)
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=1,
            log_path=tmp_path / f"{log_name}.log",
            output="",
        )

    monkeypatch.setattr(runner, "run", branch_missing)

    assert not git.local_branch_exists(tmp_path, "release", log_name="local-branch")
    assert commands == [
        ("git", "rev-parse", "--verify", "--quiet", "refs/heads/release"),
    ]


def test_branch_ahead_behind_parses_counts_from_last_output_line(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)

    def ahead_behind_output(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert check
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=0,
            log_path=tmp_path / f"{log_name}.log",
            output="warning: ignored diagnostic\n2\t3\n",
        )

    monkeypatch.setattr(runner, "run", ahead_behind_output)

    counts = git.branch_ahead_behind(
        tmp_path,
        branch="feature",
        base_branch="main",
        log_name="ahead-behind",
    )

    assert counts.behind == 2
    assert counts.ahead == 3


def test_branch_ahead_behind_rejects_empty_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)

    def empty_ahead_behind_output(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert check
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=0,
            log_path=tmp_path / f"{log_name}.log",
            output=" \n",
        )

    monkeypatch.setattr(runner, "run", empty_ahead_behind_output)

    with pytest.raises(DiamondDevError, match="No output returned"):
        git.branch_ahead_behind(
            tmp_path,
            branch="feature",
            base_branch="main",
            log_name="ahead-behind",
        )
