"""Fast external dependency checks for Diamond Dev runs."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from diamond_dev.errors import DiamondDevError

if TYPE_CHECKING:
    from diamond_dev.executor import CommandRunner

REQUIRED_CLI_NAMES: Final = ("git", "codex", "claude", "gemini", "coderabbit", "gh")


@dataclass(frozen=True, slots=True)
class CliCheck:
    """Resolved executable path for a required external CLI."""

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class PreflightSummary:
    """Successful preflight checks for a run."""

    cli_checks: tuple[CliCheck, ...]
    gh_auth_log_path: Path


def run_preflight(*, runner: CommandRunner, cwd: Path) -> PreflightSummary:
    """Fail quickly when required external commands or GitHub auth are missing."""
    cli_checks: list[CliCheck] = []
    missing_cli_names: list[str] = []

    for cli_name in REQUIRED_CLI_NAMES:
        cli_path = shutil.which(cli_name)
        if cli_path is None:
            missing_cli_names.append(cli_name)
            continue
        cli_checks.append(CliCheck(name=cli_name, path=cli_path))

    if missing_cli_names:
        missing_list = ", ".join(missing_cli_names)
        raise DiamondDevError(f"Missing required external CLIs on PATH: {missing_list}")

    gh_auth_result = runner.run(
        ("gh", "auth", "status"),
        cwd=cwd,
        log_name="preflight-gh-auth",
    )
    logger.info("Preflight checks passed for {}", ", ".join(REQUIRED_CLI_NAMES))
    return PreflightSummary(
        cli_checks=tuple(cli_checks),
        gh_auth_log_path=gh_auth_result.log_path,
    )
