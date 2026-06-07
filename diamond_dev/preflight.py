"""External dependency and environment health checks for Diamond Dev runs."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from diamond_dev.agents import adapter_names
from diamond_dev.errors import DiamondDevError
from diamond_dev.naming import derive_wiki_repository_url, wiki_directory_name

if TYPE_CHECKING:
    from diamond_dev.config import DiamondDevConfig
    from diamond_dev.executor import CommandResult, CommandRunner

REQUIRED_CORE_CLI_NAMES: Final = ("git", "gh")
_DOCTOR_GEMINI_PROMPT: Final = (
    "Diamond Dev doctor authentication check. Reply with exactly OK and do not "
    "use tools."
)
_DOCTOR_GIT_USER_NAME: Final = "diamond-dev"
_DOCTOR_GIT_USER_EMAIL: Final = "diamond-dev@example.invalid"


@dataclass(frozen=True, slots=True)
class CliCheck:
    """Resolved executable path for a required external CLI."""

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class AgentAuthCheck:
    """Successful authentication probe for one configured agent."""

    agent_name: str
    adapter_name: str
    command: tuple[str, ...]
    log_path: Path


@dataclass(frozen=True, slots=True)
class WikiAccessCheck:
    """Successful wiki repository read and dry-run push probes."""

    url: str
    directory: Path
    read_log_path: Path
    push_log_path: Path


@dataclass(frozen=True, slots=True)
class WritePermissionCheck:
    """Successful local directory write probe."""

    label: str
    path: Path


@dataclass(frozen=True, slots=True)
class PreflightSummary:
    """Successful doctor-grade preflight checks for a run."""

    cli_checks: tuple[CliCheck, ...]
    gh_auth_log_path: Path
    agent_auth_checks: tuple[AgentAuthCheck, ...] = ()
    wiki_access_check: WikiAccessCheck | None = None
    write_permission_checks: tuple[WritePermissionCheck, ...] = ()


def run_preflight(
    *,
    runner: CommandRunner,
    cwd: Path,
    config: DiamondDevConfig,
    required_cli_names: Sequence[str] = (),
    wiki_dir: Path | None = None,
) -> PreflightSummary:
    """Fail quickly when required commands, auth, or wiki access are missing."""
    return run_doctor(
        runner=runner,
        cwd=cwd,
        config=config,
        required_cli_names=required_cli_names,
        wiki_dir=wiki_dir,
    )


def run_doctor(
    *,
    runner: CommandRunner,
    cwd: Path,
    config: DiamondDevConfig,
    required_cli_names: Sequence[str] = (),
    wiki_dir: Path | None = None,
) -> PreflightSummary:
    """Run all startup health checks without launching implementation agents."""
    wiki_url = config.wiki_repository_url or derive_wiki_repository_url(
        config.repository_url,
    )
    resolved_wiki_dir = wiki_dir or cwd / wiki_directory_name(wiki_url)
    cli_checks = _check_required_cli_names(
        cli_names=_required_cli_names(config, required_cli_names),
    )
    write_permission_checks = _check_write_permissions(
        cwd=cwd,
        runner=runner,
        wiki_dir=resolved_wiki_dir,
    )
    gh_auth_result = runner.run(
        ("gh", "auth", "status"),
        cwd=cwd,
        log_name="preflight-gh-auth",
    )
    agent_auth_checks = _check_agent_auth(
        runner=runner,
        cwd=cwd,
        config=config,
        required_cli_names=required_cli_names,
    )
    wiki_access_check = _check_wiki_access(
        runner=runner,
        cwd=cwd,
        wiki_url=wiki_url,
        wiki_dir=resolved_wiki_dir,
    )
    logger.info(
        "Doctor checks passed for {}",
        ", ".join(cli_check.name for cli_check in cli_checks),
    )
    return PreflightSummary(
        cli_checks=cli_checks,
        gh_auth_log_path=gh_auth_result.log_path,
        agent_auth_checks=agent_auth_checks,
        wiki_access_check=wiki_access_check,
        write_permission_checks=write_permission_checks,
    )


def _required_cli_names(
    config: DiamondDevConfig,
    required_cli_names: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *REQUIRED_CORE_CLI_NAMES,
                *config.required_cli_names(),
                *required_cli_names,
            ),
        ),
    )


def _check_required_cli_names(*, cli_names: Sequence[str]) -> tuple[CliCheck, ...]:
    cli_checks: list[CliCheck] = []
    missing_cli_names: list[str] = []

    for cli_name in cli_names:
        cli_path = shutil.which(cli_name)
        if cli_path is None:
            missing_cli_names.append(cli_name)
            continue
        cli_checks.append(CliCheck(name=cli_name, path=cli_path))

    if missing_cli_names:
        missing_list = ", ".join(missing_cli_names)
        raise DiamondDevError(f"Missing required external CLIs on PATH: {missing_list}")

    return tuple(cli_checks)


def _check_write_permissions(
    *,
    cwd: Path,
    runner: CommandRunner,
    wiki_dir: Path,
) -> tuple[WritePermissionCheck, ...]:
    checks: list[WritePermissionCheck] = []
    seen_paths: set[Path] = set()
    for label, path in _write_permission_targets(
        cwd=cwd,
        runner=runner,
        wiki_dir=wiki_dir,
    ):
        if path in seen_paths:
            continue
        seen_paths.add(path)
        checks.append(_check_write_permission(label=label, path=path))
    return tuple(checks)


def _write_permission_targets(
    *,
    cwd: Path,
    runner: CommandRunner,
    wiki_dir: Path,
) -> tuple[tuple[str, Path], ...]:
    log_dir = getattr(runner, "log_dir", None)
    targets: list[tuple[str, Path]] = [("workspace", cwd)]
    if isinstance(log_dir, Path):
        targets.append(("logs", log_dir))
    targets.append(("wiki", wiki_dir if wiki_dir.exists() else wiki_dir.parent))
    return tuple(targets)


def _check_write_permission(*, label: str, path: Path) -> WritePermissionCheck:
    if not path.exists():
        raise DiamondDevError(f"Doctor write check target does not exist: {path}")
    if not path.is_dir():
        raise DiamondDevError(f"Doctor write check target is not a directory: {path}")

    temp_file_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path,
            prefix=".diamond-dev-doctor-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file_path = Path(temp_file.name)
            temp_file.write("ok\n")
            temp_file.flush()
    except (OSError,) as error:
        raise DiamondDevError(
            f"Doctor cannot write to {label} directory {path}: {error}",
        ) from error
    finally:
        if temp_file_path is not None:
            _clean_up_write_check_file(
                label=label,
                path=path,
                temp_file_path=temp_file_path,
            )
    return WritePermissionCheck(label=label, path=path)


def _clean_up_write_check_file(
    *,
    label: str,
    path: Path,
    temp_file_path: Path,
) -> None:
    try:
        temp_file_path.unlink()
    except (OSError,) as error:
        raise DiamondDevError(
            f"Doctor cannot clean up write check in {label} directory {path}: {error}",
        ) from error


def _check_agent_auth(
    *,
    runner: CommandRunner,
    cwd: Path,
    config: DiamondDevConfig,
    required_cli_names: Sequence[str],
) -> tuple[AgentAuthCheck, ...]:
    checks: list[AgentAuthCheck] = []
    for agent_name in _auth_agent_names(config, required_cli_names):
        agent_config = config.agent_config(agent_name)
        adapter_name = agent_config.adapter_name(agent_name)
        command = _agent_auth_command(adapter_name, model=agent_config.model)
        result = runner.run(
            command,
            cwd=cwd,
            log_name=f"doctor-agent-{agent_name}-auth",
        )
        checks.append(
            AgentAuthCheck(
                agent_name=agent_name,
                adapter_name=adapter_name,
                command=command,
                log_path=result.log_path,
            ),
        )
    return tuple(checks)


def _auth_agent_names(
    config: DiamondDevConfig,
    required_cli_names: Sequence[str],
) -> tuple[str, ...]:
    agent_names = [*config.workflow.role_agent_names()]
    covered_adapter_names = {
        config.agent_adapter_name(agent_name) for agent_name in agent_names
    }
    for cli_name in required_cli_names:
        if (
            cli_name in adapter_names()
            and cli_name not in agent_names
            and cli_name not in covered_adapter_names
        ):
            agent_names.append(cli_name)
            covered_adapter_names.add(cli_name)
    return tuple(agent_names)


def _agent_auth_command(
    adapter_name: str,
    *,
    model: str | None,
) -> tuple[str, ...]:
    match adapter_name:
        case "codex":
            return ("codex", "login", "status")
        case "claude":
            return ("claude", "auth", "status", "--text")
        case "gemini":
            command = ["gemini"]
            if model is not None:
                command.extend(("-m", model))
            command.extend(("-p", _DOCTOR_GEMINI_PROMPT, "--skip-trust"))
            return tuple(command)
        case "coderabbit":
            return ("coderabbit", "auth", "status", "--agent")
        case _:
            raise DiamondDevError(f"Unknown agent adapter: {adapter_name}")


def _check_wiki_access(
    *,
    runner: CommandRunner,
    cwd: Path,
    wiki_url: str,
    wiki_dir: Path,
) -> WikiAccessCheck:
    read_result = runner.run(
        ("git", "ls-remote", wiki_url),
        cwd=cwd,
        log_name="doctor-wiki-ls-remote",
    )
    push_result = _check_wiki_push_access(
        runner=runner,
        cwd=cwd,
        wiki_url=wiki_url,
    )
    return WikiAccessCheck(
        url=wiki_url,
        directory=wiki_dir,
        read_log_path=read_result.log_path,
        push_log_path=push_result.log_path,
    )


def _check_wiki_push_access(
    *,
    runner: CommandRunner,
    cwd: Path,
    wiki_url: str,
) -> CommandResult:
    branch_name = f"diamond-dev-doctor-{uuid.uuid4().hex}"
    try:
        with tempfile.TemporaryDirectory(
            prefix=".diamond-dev-doctor-",
            dir=cwd,
        ) as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            runner.run(("git", "init"), cwd=temp_dir, log_name="doctor-wiki-push-init")
            runner.run(
                (
                    "git",
                    "-c",
                    f"user.name={_DOCTOR_GIT_USER_NAME}",
                    "-c",
                    f"user.email={_DOCTOR_GIT_USER_EMAIL}",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "diamond-dev doctor",
                ),
                cwd=temp_dir,
                log_name="doctor-wiki-push-commit",
            )
            return runner.run(
                (
                    "git",
                    "push",
                    "--dry-run",
                    wiki_url,
                    f"HEAD:refs/heads/{branch_name}",
                ),
                cwd=temp_dir,
                log_name="doctor-wiki-push-dry-run",
            )
    except (OSError,) as error:
        raise DiamondDevError(
            f"Could not create doctor temporary Git repository under {cwd}: {error}",
        ) from error
