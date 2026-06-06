"""Application entry point."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

from loguru import logger

from diamond_dev.config_init import run_config_init
from diamond_dev.errors import DiamondDevError
from diamond_dev.logging_setup import configure_logging
from diamond_dev.orchestrator import DiamondDevOrchestrator

PACKAGE_NAME = "diamond-dev"


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
        help="Overwrite an existing config file when running init.",
    )
    parser.add_argument(
        "command_or_plan",
        nargs="*",
        help=(
            "Use `init` to generate config, pass a markdown plan file to run, "
            "or pass two commit-ish refs to compare."
        ),
    )
    args = parser.parse_args(argv)
    positional_args = args.command_or_plan
    if not positional_args:
        parser.error("the following arguments are required: plan_path or command")
    if positional_args[0] == "init":
        if len(positional_args) > 1:
            parser.error("init does not accept positional arguments")
        args.command = "init"
        args.plan_path = None
        args.commit_args = None
        return args
    if args.force:
        parser.error("--force is only supported with init")
    if len(positional_args) == 2:
        args.command = "compare-commits"
        args.plan_path = None
        args.commit_args = (positional_args[0], positional_args[1])
        return args
    if len(positional_args) > 2:
        parser.error("expected a plan path or exactly two commit-ish refs")
    args.command = "run"
    args.plan_path = Path(positional_args[0])
    args.commit_args = None
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the application."""
    configure_logging()
    args = parse_args(argv)

    try:
        if args.command == "init":
            run_config_init(Path.cwd(), args.config, force=args.force)
            return 0
        if args.command == "compare-commits":
            return DiamondDevOrchestrator(config_path=args.config).run_commits(
                args.commit_args,
            )
        return DiamondDevOrchestrator(config_path=args.config).run(args.plan_path)
    except (DiamondDevError,) as error:
        logger.error("Diamond Dev failed: {}", error)
        return 1


def _package_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except (metadata.PackageNotFoundError,):
        return "0.1.0"


if __name__ == "__main__":
    raise SystemExit(main())
