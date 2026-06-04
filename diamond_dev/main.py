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
        nargs="?",
        help="Use `init` to generate config, or pass a markdown plan file to run.",
    )
    args = parser.parse_args(argv)
    if args.command_or_plan is None:
        parser.error("the following arguments are required: plan_path or command")
    if args.command_or_plan == "init":
        args.command = "init"
        args.plan_path = None
        return args
    if args.force:
        parser.error("--force is only supported with init")
    args.command = "run"
    args.plan_path = Path(args.command_or_plan)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the application."""
    configure_logging()
    args = parse_args(argv)

    try:
        if args.command == "init":
            run_config_init(Path.cwd(), args.config, force=args.force)
            return 0
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
