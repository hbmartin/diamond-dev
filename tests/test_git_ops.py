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
