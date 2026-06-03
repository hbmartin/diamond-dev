"""Application entry point."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

from loguru import logger

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
    parser.add_argument("plan_path", type=Path, help="Path to the markdown plan file.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the application."""
    configure_logging()
    args = parse_args(argv)

    try:
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
