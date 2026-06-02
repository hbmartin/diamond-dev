"""Application logging setup."""

from __future__ import annotations

import os
import sys
import warnings
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, TextIO

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

DEFAULT_LOG_FILE: Final = Path("logs/diamond-dev.log")
DEFAULT_JSON_LOG_FILE: Final = Path("logs/diamond-dev.jsonl")
DEFAULT_LOG_LEVEL: Final = "INFO"
DEFAULT_LOG_DIAGNOSE: Final = True
_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final = frozenset({"0", "false", "no", "off"})
CONSOLE_LOG_FORMAT: Final = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}\n{exception}"
)
FILE_LOG_FORMAT: Final = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} | "
    "trace_id={extra[otelTraceID]} span_id={extra[otelSpanID]} "
    "trace_sampled={extra[otelTraceSampled]} "
    "service={extra[otelServiceName]} | {message}\n{exception}"
)


class _ShowWarningHook(Protocol):
    # pylint: disable-next=too-many-positional-arguments
    def __call__(  # noqa: PLR0913
        self,
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: TextIO | None = None,
        line: str | None = None,
    ) -> None: ...


def configure_logging(
    *,
    log_file: Path | None = None,
    json_log_file: Path | None = None,
    log_level: str | None = None,
    diagnose: bool | None = None,
) -> Path:
    """Configure Loguru console, text file, and JSONL file logging."""
    selected_log_file = log_file or Path(
        os.environ.get("DIAMOND_DEV_LOG_FILE", str(DEFAULT_LOG_FILE)),
    )
    selected_json_log_file = json_log_file or Path(
        os.environ.get("DIAMOND_DEV_JSON_LOG_FILE", str(DEFAULT_JSON_LOG_FILE)),
    )
    selected_log_level = (
        log_level or os.environ.get("DIAMOND_DEV_LOG_LEVEL", DEFAULT_LOG_LEVEL)
    ).upper()
    selected_diagnose = (
        diagnose
        if diagnose is not None
        else _env_bool("DIAMOND_DEV_LOG_DIAGNOSE", default=DEFAULT_LOG_DIAGNOSE)
    )

    for selected_file in (selected_log_file, selected_json_log_file):
        selected_file.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.configure(patcher=_trace_context_patcher())
    showwarning: _ShowWarningHook = _showwarning
    setattr(warnings, "showwarning", showwarning)  # noqa: B010
    logger.add(
        sys.stderr,
        level=selected_log_level,
        format=CONSOLE_LOG_FORMAT,
        backtrace=True,
        diagnose=selected_diagnose,
    )
    logger.add(
        selected_log_file,
        level=selected_log_level,
        format=FILE_LOG_FORMAT,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=selected_diagnose,
        encoding="utf-8",
        errors="backslashreplace",
        opener=_private_log_file_opener,
    )
    logger.add(
        selected_json_log_file,
        level=selected_log_level,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=selected_diagnose,
        serialize=True,
        encoding="utf-8",
        errors="backslashreplace",
        opener=_private_log_file_opener,
    )

    return selected_log_file


def _private_log_file_opener(file: str, flags: int) -> int:
    return os.open(file, flags, 0o600)


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default

    normalized_value = value.strip().lower()
    if normalized_value in _TRUE_VALUES:
        return True
    if normalized_value in _FALSE_VALUES:
        return False
    return default


def _trace_context_patcher() -> Callable[[Record], None]:
    try:
        trace_module = import_module("opentelemetry.trace")
    except (ImportError, ModuleNotFoundError):
        return _add_default_trace_context

    get_current_span = getattr(trace_module, "get_current_span", None)
    get_tracer_provider = getattr(trace_module, "get_tracer_provider", None)
    if not callable(get_current_span) or not callable(get_tracer_provider):
        return _add_default_trace_context

    invalid_span = getattr(trace_module, "INVALID_SPAN", None)
    invalid_span_context = getattr(trace_module, "INVALID_SPAN_CONTEXT", None)
    provider = get_tracer_provider()
    service_name: str | None = None

    def add_trace_context(record: Record) -> None:
        _add_default_trace_context(record)

        nonlocal service_name
        if service_name is None:
            resource = getattr(provider, "resource", None)
            attributes = getattr(resource, "attributes", {})
            if isinstance(attributes, Mapping):
                service_name = str(attributes.get("service.name") or "")
            else:
                service_name = ""

        record["extra"]["otelServiceName"] = service_name
        span = get_current_span()
        if span == invalid_span:
            return

        get_span_context = getattr(span, "get_span_context", None)
        if not callable(get_span_context):
            return

        context = get_span_context()
        if context == invalid_span_context:
            return

        span_id = getattr(context, "span_id", 0)
        trace_id = getattr(context, "trace_id", 0)
        trace_flags = getattr(context, "trace_flags", None)
        record["extra"]["otelSpanID"] = _format_otel_id(span_id, width=16)
        record["extra"]["otelTraceID"] = _format_otel_id(trace_id, width=32)
        record["extra"]["otelTraceSampled"] = bool(
            getattr(trace_flags, "sampled", False),
        )

    return add_trace_context


def _add_default_trace_context(record: Record) -> None:
    record["extra"]["otelSpanID"] = "0"
    record["extra"]["otelTraceID"] = "0"
    record["extra"]["otelTraceSampled"] = False
    record["extra"]["otelServiceName"] = ""


def _format_otel_id(value: object, *, width: int) -> str:
    return format(value, f"0{width}x") if isinstance(value, int) else "0"


# pylint: disable-next=too-many-positional-arguments
def _showwarning(  # noqa: PLR0913
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: TextIO | None = None,
    line: str | None = None,
) -> None:
    del file, line
    logger.warning("{}:{}: {}: {}", filename, lineno, category.__name__, message)
