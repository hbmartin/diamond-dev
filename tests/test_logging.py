"""Tests for application logging setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.main import configure_logging

if TYPE_CHECKING:
    from pathlib import Path


def test_configure_logging_writes_to_file(tmp_path: Path) -> None:
    """Verify the configured file sink receives log messages."""
    log_file = tmp_path / "diamond-dev.log"

    configured_log_file = configure_logging(log_file=log_file, log_level="INFO")
    logger.info("smoke test log entry")
    logger.complete()

    assert configured_log_file == log_file
    assert "smoke test log entry" in log_file.read_text(encoding="utf-8")
