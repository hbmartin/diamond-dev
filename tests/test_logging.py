"""Tests for application logging setup."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING, Any

from loguru import logger

from diamond_dev import main as main_module
from diamond_dev.errors import DiamondDevError
from diamond_dev.logging_setup import configure_logging

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_configure_logging_writes_to_text_and_json_files(tmp_path: Path) -> None:
    """Verify configured file sinks receive log messages."""
    log_file = tmp_path / "diamond-dev.log"
    json_log_file = tmp_path / "diamond-dev.jsonl"

    configured_log_file = configure_logging(
        log_file=log_file,
        json_log_file=json_log_file,
        log_level="INFO",
    )
    logger.info("smoke test log entry")
    logger.complete()

    assert configured_log_file == log_file
    assert "smoke test log entry" in log_file.read_text(encoding="utf-8")

    payload = _read_first_json_payload(json_log_file)
    record = payload["record"]
    assert record["message"] == "smoke test log entry"
    assert record["extra"]["otelTraceID"] == "0"
    assert record["extra"]["otelSpanID"] == "0"
    assert record["extra"]["otelTraceSampled"] is False
    assert record["extra"]["otelServiceName"] == ""


def test_exception_logging_writes_descriptive_traceback(tmp_path: Path) -> None:
    log_file = tmp_path / "diamond-dev.log"
    json_log_file = tmp_path / "diamond-dev.jsonl"
    configure_logging(log_file=log_file, json_log_file=json_log_file)

    try:
        _raise_runtime_error()
    except (RuntimeError,):
        logger.exception("descriptive failure context")
    logger.complete()

    text_log = log_file.read_text(encoding="utf-8")
    assert "descriptive failure context" in text_log
    assert "Traceback" in text_log
    assert "RuntimeError: full exception detail" in text_log

    record = _read_first_json_payload(json_log_file)["record"]
    assert record["message"] == "descriptive failure context"
    assert record["exception"]["type"] == "RuntimeError"
    assert record["exception"]["value"] == "full exception detail"
    assert record["exception"]["traceback"] is True


def test_warnings_are_captured_by_loguru(tmp_path: Path) -> None:
    log_file = tmp_path / "diamond-dev.log"
    json_log_file = tmp_path / "diamond-dev.jsonl"
    configure_logging(log_file=log_file, json_log_file=json_log_file)

    warnings.warn("warning bridge smoke", UserWarning, stacklevel=1)
    logger.complete()

    text_log = log_file.read_text(encoding="utf-8")
    assert "UserWarning: warning bridge smoke" in text_log


def test_main_logs_diamond_dev_errors_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_file = tmp_path / "diamond-dev.log"
    json_log_file = tmp_path / "diamond-dev.jsonl"
    monkeypatch.setenv("DIAMOND_DEV_LOG_FILE", str(log_file))
    monkeypatch.setenv("DIAMOND_DEV_JSON_LOG_FILE", str(json_log_file))

    class FailingOrchestrator:
        def __init__(self, *, config_path: Path | None = None) -> None:
            del config_path

        def run(self, plan_path: Path) -> int:
            del plan_path
            raise DiamondDevError("planned failure")

    monkeypatch.setattr(main_module, "DiamondDevOrchestrator", FailingOrchestrator)

    exit_code = main_module.main([str(tmp_path / "plan.md")])
    logger.complete()

    assert exit_code == 1
    text_log = log_file.read_text(encoding="utf-8")
    assert "Diamond Dev failed: planned failure" in text_log
    assert "Traceback" not in text_log
    assert "DiamondDevError: planned failure" not in text_log


def _read_first_json_payload(json_log_file: Path) -> dict[str, Any]:
    first_line = json_log_file.read_text(encoding="utf-8").splitlines()[0]
    payload = json.loads(first_line)
    assert isinstance(payload, dict)
    return payload


def _raise_runtime_error() -> None:
    raise RuntimeError("full exception detail")
