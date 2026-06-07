"""Tests for application logging setup."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING, Any

from loguru import logger

from diamond_dev import logging_setup
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


def test_env_bool_parses_known_values_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_bool = logging_setup._env_bool

    monkeypatch.setenv("DIAMOND_DEV_TEST_BOOL", " yes ")
    assert env_bool("DIAMOND_DEV_TEST_BOOL", default=False) is True

    monkeypatch.setenv("DIAMOND_DEV_TEST_BOOL", "OFF")
    assert env_bool("DIAMOND_DEV_TEST_BOOL", default=True) is False

    monkeypatch.setenv("DIAMOND_DEV_TEST_BOOL", "sometimes")
    assert env_bool("DIAMOND_DEV_TEST_BOOL", default=True) is True

    monkeypatch.delenv("DIAMOND_DEV_TEST_BOOL")
    assert env_bool("DIAMOND_DEV_TEST_BOOL", default=False) is False


def test_trace_context_patcher_adds_current_span_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_span = _FakeSpan(
        _FakeSpanContext(
            span_id=0x12,
            trace_id=0x34,
            trace_flags=_FakeTraceFlags(sampled=True),
        ),
    )

    class TraceModule:
        INVALID_SPAN = object()
        INVALID_SPAN_CONTEXT = object()

        @staticmethod
        def get_current_span() -> _FakeSpan:
            return current_span

        @staticmethod
        def get_tracer_provider() -> _FakeTracerProvider:
            return _FakeTracerProvider({"service.name": "diamond-tests"})

    def import_trace_module(module_name: str) -> type[TraceModule]:
        assert module_name == "opentelemetry.trace"
        return TraceModule

    monkeypatch.setattr(logging_setup, "import_module", import_trace_module)
    patcher = logging_setup._trace_context_patcher()
    record: dict[str, dict[str, object]] = {"extra": {}}

    patcher(record)

    assert record["extra"]["otelServiceName"] == "diamond-tests"
    assert record["extra"]["otelSpanID"] == "0000000000000012"
    assert record["extra"]["otelTraceID"] == "00000000000000000000000000000034"
    assert record["extra"]["otelTraceSampled"] is True


def test_trace_context_returns_none_for_incomplete_trace_module() -> None:
    trace_context = logging_setup._trace_context

    assert trace_context(object()) is None

    class TraceModule:
        INVALID_SPAN = object()
        INVALID_SPAN_CONTEXT = object()

        @staticmethod
        def get_current_span() -> object:
            return object()

        @staticmethod
        def get_tracer_provider() -> _FakeTracerProvider:
            return _FakeTracerProvider(["not", "a", "mapping"])

    assert trace_context(TraceModule())[3] == ""


def test_add_span_context_ignores_invalid_spans_and_formats_non_int_ids() -> None:
    add_span_context = logging_setup._add_span_context
    invalid_span = object()
    invalid_context = object()
    record: dict[str, dict[str, object]] = {"extra": {"kept": True}}

    add_span_context(
        record,
        span=invalid_span,
        invalid_span=invalid_span,
        invalid_span_context=invalid_context,
    )
    add_span_context(
        record,
        span=object(),
        invalid_span=invalid_span,
        invalid_span_context=invalid_context,
    )
    add_span_context(
        record,
        span=_FakeSpan(invalid_context),
        invalid_span=invalid_span,
        invalid_span_context=invalid_context,
    )
    add_span_context(
        record,
        span=_FakeSpan(
            _FakeSpanContext(
                span_id="not-an-int",
                trace_id=object(),
                trace_flags=object(),
            ),
        ),
        invalid_span=invalid_span,
        invalid_span_context=invalid_context,
    )

    assert record["extra"]["kept"] is True
    assert record["extra"]["otelSpanID"] == "0"
    assert record["extra"]["otelTraceID"] == "0"
    assert record["extra"]["otelTraceSampled"] is False


def _read_first_json_payload(json_log_file: Path) -> dict[str, Any]:
    first_line = json_log_file.read_text(encoding="utf-8").splitlines()[0]
    payload = json.loads(first_line)
    assert isinstance(payload, dict)
    return payload


def _raise_runtime_error() -> None:
    raise RuntimeError("full exception detail")


class _FakeTraceFlags:
    def __init__(self, *, sampled: bool) -> None:
        self.sampled = sampled


class _FakeSpanContext:
    def __init__(
        self,
        *,
        span_id: object,
        trace_id: object,
        trace_flags: object,
    ) -> None:
        self.span_id = span_id
        self.trace_id = trace_id
        self.trace_flags = trace_flags


class _FakeSpan:
    def __init__(self, context: object) -> None:
        self.context = context

    def get_span_context(self) -> object:
        return self.context


class _FakeTracerProvider:
    def __init__(self, attributes: object) -> None:
        self.resource = _FakeOtelResource(attributes)


class _FakeOtelResource:
    def __init__(self, attributes: object) -> None:
        self.attributes = attributes
