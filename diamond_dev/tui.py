"""Optional live phase-progress display built on Rich."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace, TracebackType
from typing import TYPE_CHECKING, Any, Final, Self

from loguru import logger

from diamond_dev.errors import DiamondDevError

if TYPE_CHECKING:
    from loguru import Message, Record

_STATUS_SYMBOLS: Final = {
    "started": "…",
    "succeeded": "✓",
    "failed": "✗",
}


class PhaseProgressDisplay:
    """Render orchestration phase progress as a live terminal table.

    The display subscribes to Loguru records that carry the ``phase`` and
    ``phase_status`` extras emitted at phase boundaries, so it needs no
    orchestrator hooks. Requires the optional Rich dependency.
    """

    def __init__(self) -> None:
        """Load Rich and prepare an empty progress table."""
        self._rich = _load_rich()
        self._phases: dict[str, tuple[str, float | None]] = {}
        self._sink_id: int | None = None
        console = self._rich.console.Console(stderr=True)
        self._live = self._rich.live.Live(
            self._render_table(),
            console=console,
            refresh_per_second=4,
        )

    def __enter__(self) -> Self:
        """Start live rendering and subscribe to phase log records."""
        self._live.start()
        self._sink_id = logger.add(
            self._handle_message,
            level="INFO",
            filter=_phase_record_filter,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Unsubscribe from log records and stop live rendering."""
        if self._sink_id is not None:
            logger.remove(self._sink_id)
            self._sink_id = None
        self._live.stop()

    def _handle_message(self, message: Message) -> None:
        record: Record = message.record
        extra = record["extra"]
        phase = str(extra["phase"])
        status = str(extra.get("phase_status", ""))
        duration = extra.get("duration_seconds")
        duration_seconds = (
            float(duration) if isinstance(duration, int | float) else None
        )
        self._phases[phase] = (status, duration_seconds)
        self._live.update(self._render_table())

    def _render_table(self) -> Any:  # noqa: ANN401
        table = self._rich.table.Table(title="diamond-dev progress")
        table.add_column("Phase")
        table.add_column("Status")
        table.add_column("Duration", justify="right")
        for phase, (status, duration_seconds) in self._phases.items():
            symbol = _STATUS_SYMBOLS.get(status, "?")
            duration_text = (
                f"{duration_seconds:.1f}s" if duration_seconds is not None else ""
            )
            table.add_row(phase, f"{symbol} {status}", duration_text)
        return table


def _phase_record_filter(record: Record) -> bool:
    return "phase" in record["extra"]


def _load_rich() -> SimpleNamespace:
    try:
        return SimpleNamespace(
            console=import_module("rich.console"),
            live=import_module("rich.live"),
            table=import_module("rich.table"),
        )
    except (ImportError,) as error:
        raise DiamondDevError(
            "The --tui flag requires the optional Rich dependency; install it "
            "with `pip install rich` or `uv pip install rich`",
        ) from error
