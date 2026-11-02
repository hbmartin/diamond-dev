"""Application entry point."""

from __future__ import annotations

import tomllib
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Final

from loguru import logger

from diamond_dev.config_init import run_config_init
from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import CommandRunner
from diamond_dev.inspection import run_clean, run_dry_run, run_status, run_validate
from diamond_dev.logging_setup import configure_logging
from diamond_dev.orchestrator import DiamondDevOrchestrator
from diamond_dev.tui import PhaseProgressDisplay

PACKAGE_NAME = "diamond-dev"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NO_ARG_COMMANDS: Final = frozenset({"init", "doctor", "validate"})
_ONE_ARG_COMMANDS: Final = frozenset({"status", "clean"})


def parse_args(argv: Sequence[str] | None = None) -> Namespace:
    """Parse command-line arguments."""
    parser = ArgumentParser(
        prog="diamond-dev",
        description="Run a multi-agent implementation workflow from a markdown plan.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_package_version()}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to the Diamond Dev TOML config file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite an existing config file when running init, or skip "
            "clone-safety checks when running clean."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and log the run plan without cloning or launching agents.",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help=(
            "Show a live phase-progress display "
            "(requires the optional Rich dependency)."
        ),
    )
    parser.add_argument(
        "command_or_plan",
        nargs="*",
        help=(
            "Use `init` to generate config, `doctor` to check local readiness, "
            "`validate` to check the config file, `status <plan-or-slug>` to "
            "inspect a run, `clean <plan-or-slug>` to remove generated clones, "
            "pass a markdown plan file to run, or pass two commit-ish refs to "
            "compare."
        ),
    )
    args = parser.parse_args(argv)
    _resolve_command(parser, args)
    _validate_flags(parser, args)
    return args


def _resolve_command(parser: ArgumentParser, args: Namespace) -> None:
    positional_args = args.command_or_plan
    args.plan_path = None
    args.commit_args = None
    args.target = None
    if not positional_args:
        parser.error(
            "the following arguments are required: plan_path, command, "
            "or two commit-ish refs",
        )
    command = positional_args[0]
    if command in _NO_ARG_COMMANDS:
        if len(positional_args) > 1:
            parser.error(f"{command} does not accept positional arguments")
        args.command = command
        return
    if command in _ONE_ARG_COMMANDS:
        if len(positional_args) != 2:
            parser.error(f"{command} requires exactly one plan path or slug")
        args.command = command
        args.target = positional_args[1]
        return
    if len(positional_args) == 2:
        args.command = "compare-commits"
        args.commit_args = (positional_args[0], positional_args[1])
        return
    if len(positional_args) > 2:
        parser.error("expected a plan path or exactly two commit-ish refs")
    args.command = "run"
    args.plan_path = Path(command)


def _validate_flags(parser: ArgumentParser, args: Namespace) -> None:
    if args.force and args.command not in {"init", "clean"}:
        parser.error("--force is only supported with init and clean")
    if args.dry_run and args.command != "run":
        parser.error("--dry-run is only supported when running a plan")
    if args.tui and args.command not in {"run", "compare-commits"}:
        parser.error("--tui is only supported when running a workflow")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the application."""
    args = parse_args(argv)
    configure_logging(console_level="WARNING" if args.tui else None)

    try:
        if args.tui:
            with PhaseProgressDisplay():
                return _dispatch(args)
        return _dispatch(args)
    except (KeyboardInterrupt,):
        logger.warning("Diamond Dev interrupted")
        return 130
    except (DiamondDevError,) as error:
        logger.error("Diamond Dev failed: {}", error)
        return 1


def _dispatch(args: Namespace) -> int:
    cwd = Path.cwd()
    utility_result = _dispatch_utility(args, cwd)
    if utility_result is not None:
        return utility_result
    return _dispatch_workflow(args)


def _dispatch_utility(args: Namespace, cwd: Path) -> int | None:
    match args.command:
        case "init":
            run_config_init(cwd, args.config, force=args.force)
            return 0
        case "validate":
            return run_validate(cwd, args.config)
        case "status":
            run_status(
                cwd=cwd,
                runner=CommandRunner(cwd / "logs"),
                plan_or_slug=args.target,
                config_path=args.config,
            )
            return 0
        case "clean":
            run_clean(
                cwd=cwd,
                runner=CommandRunner(cwd / "logs"),
                plan_or_slug=args.target,
                config_path=args.config,
                force=args.force,
            )
            return 0
        case _:
            return None


def _dispatch_workflow(args: Namespace) -> int:
    if args.command == "doctor":
        return DiamondDevOrchestrator(config_path=args.config).doctor()
    if args.command == "compare-commits":
        return DiamondDevOrchestrator(config_path=args.config).run_commits(
            args.commit_args,
        )
    if args.dry_run:
        return run_dry_run(
            cwd=Path.cwd(),
            plan_path=args.plan_path,
            config_path=args.config,
        )
    return DiamondDevOrchestrator(config_path=args.config).run(args.plan_path)


def _package_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except (metadata.PackageNotFoundError,):
        return _source_tree_version()


def _source_tree_version() -> str:
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    try:
        pyproject_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "unknown"
    project_data = pyproject_data.get("project")
    if not isinstance(project_data, dict):
        return "unknown"
    version = project_data.get("version")
    if not isinstance(version, str):
        return "unknown"
    return version


if __name__ == "__main__":
    raise SystemExit(main())
