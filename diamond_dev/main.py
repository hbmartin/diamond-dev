"""Application entry point and logging setup."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger

DEFAULT_LOG_FILE = Path("logs/diamond-dev.log")
DEFAULT_LOG_LEVEL = "INFO"
CONSOLE_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}"
FILE_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} | {message}"
)


def configure_logging(
    *,
    log_file: Path | None = None,
    log_level: str | None = None,
) -> Path:
    """Configure Loguru console and rotating file logging."""
    selected_log_file = log_file or Path(
        os.environ.get("DIAMOND_DEV_LOG_FILE", str(DEFAULT_LOG_FILE)),
    )
    selected_log_level = (
        log_level or os.environ.get("DIAMOND_DEV_LOG_LEVEL", DEFAULT_LOG_LEVEL)
    ).upper()

    selected_log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stderr,
        level=selected_log_level,
        format=CONSOLE_LOG_FORMAT,
        diagnose=False,
    )
    logger.add(
        selected_log_file,
        level=selected_log_level,
        format=FILE_LOG_FORMAT,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
        diagnose=False,
    )

    return selected_log_file


def main() -> None:
    """Run the application."""
    configure_logging()
    logger.info("Hello from diamond-dev!")


if __name__ == "__main__":
    main()
