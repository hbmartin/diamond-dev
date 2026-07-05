"""Tests for the optional Rich phase-progress display."""

from __future__ import annotations

import pytest
from loguru import logger

from diamond_dev import tui
from diamond_dev.errors import DiamondDevError
from diamond_dev.tui import PhaseProgressDisplay


def test_phase_progress_display_tracks_phase_records() -> None:
    pytest.importorskip("rich")
    display = PhaseProgressDisplay()
    with display:
        logger.bind(phase="load config", phase_status="started").info("started")
        logger.bind(
            phase="load config",
            phase_status="succeeded",
            duration_seconds=1.25,
        ).info("done")
        logger.bind(phase="preflight", phase_status="failed").info("boom")
        logger.info("no phase extra; ignored by the display")

    assert display._phases == {  # noqa: SLF001
        "load config": ("succeeded", 1.25),
        "preflight": ("failed", None),
    }
    assert display._sink_id is None  # noqa: SLF001


def test_phase_progress_display_requires_rich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_rich(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(tui, "import_module", missing_rich)

    with pytest.raises(DiamondDevError, match="Rich"):
        PhaseProgressDisplay()
