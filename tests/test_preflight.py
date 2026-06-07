"""Tests for fast external dependency preflight checks."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Self

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


def test_check_write_permission_reports_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_file_path = tmp_path / ".diamond-dev-doctor-failing.tmp"

    class _FailingTemporaryFile:
        name = str(temp_file_path)

        def __enter__(self) -> Self:
            temp_file_path.touch()
            return self

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> bool:
            return False

        def write(self, text: str) -> int:
            assert text == "ok\n"
            raise OSError("disk full")

        def flush(self) -> None:
            pytest.fail("flush should not run after a failed write")

    def named_temporary_file(**kwargs: object) -> _FailingTemporaryFile:
        assert kwargs == {
            "mode": "w",
            "encoding": "utf-8",
            "dir": tmp_path,
            "prefix": ".diamond-dev-doctor-",
            "suffix": ".tmp",
            "delete": False,
        }
        return _FailingTemporaryFile()

    monkeypatch.setattr(preflight.tempfile, "NamedTemporaryFile", named_temporary_file)

    with pytest.raises(DiamondDevError, match=r"Doctor cannot write .*disk full"):
        preflight._check_write_permission(  # noqa: SLF001
            label="workspace",
            path=tmp_path,
        )

    assert not temp_file_path.exists()


def test_check_write_permission_reports_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_unlink = Path.unlink
    attempted_paths: list[Path] = []

    def unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.parent == tmp_path and path.name.startswith(".diamond-dev-doctor-"):
            attempted_paths.append(path)
            raise OSError("permission denied")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    with pytest.raises(
        DiamondDevError,
        match=r"Doctor cannot clean up write check .*permission denied",
    ):
        preflight._check_write_permission(  # noqa: SLF001
            label="workspace",
            path=tmp_path,
        )

    assert attempted_paths
    for attempted_path in attempted_paths:
        if attempted_path.exists():
            real_unlink(attempted_path)
