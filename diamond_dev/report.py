"""Structured run report writing for Diamond Dev."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger

if TYPE_CHECKING:
    from diamond_dev.executor import CommandLogRecord
    from diamond_dev.preflight import PreflightSummary
    from diamond_dev.workflow import RunContext, SelectedImplementation

type RunStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class PhaseTiming:
    """Elapsed time for one orchestration phase."""

    name: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class RunReport:
    """Inputs for writing a structured run report."""

    path: Path
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    phase_timings: Sequence[PhaseTiming]
    context: RunContext | None
    selected: SelectedImplementation | None
    preflight_summary: PreflightSummary | None
    command_logs: Sequence[CommandLogRecord]
    error: str | None


def write_run_report(report: RunReport) -> None:
    """Write a deterministic JSON summary for an attempted run."""
    report.path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": report.status,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "duration_seconds": round(report.duration_seconds, 3),
        "error": report.error,
        "context": _context_payload(report.context),
        "selected_implementation": _selected_payload(report.selected),
        "preflight": _preflight_payload(report.preflight_summary),
        "phase_timings": _phase_timings_payload(report.phase_timings),
        "command_logs": _command_logs_payload(report.command_logs),
    }
    report.path.write_text(
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )
    logger.info("Wrote run report: {}", report.path)


def _context_payload(context: RunContext | None) -> dict[str, object] | None:
    if context is None:
        return None

    return {
        "cwd": str(context.cwd),
        "config_path": str(context.config.config_path),
        "plan_path": str(context.plan.path),
        "repository_url": context.config.repository_url,
        "notes_repository_url": context.notes.url,
        "branches": {
            "base": context.implementation.base_branch,
            "codex": context.implementation.codex_branch,
            "claude": context.implementation.claude_branch,
        },
        "repositories": {
            "notes": str(context.notes.directory),
            "codex": str(context.implementation.codex_dir),
            "claude": str(context.implementation.claude_dir),
        },
        "artifacts": {
            "comparison": str(context.notes.comparison_file),
            "review": str(context.notes.review_file),
            "pr_url": context.pr_url,
        },
        "dirty_records": [
            {
                "label": dirty_record.label,
                "branch": dirty_record.branch,
                "files": list(dirty_record.files),
            }
            for dirty_record in context.dirty_records
        ],
    }


def _selected_payload(
    selected: SelectedImplementation | None,
) -> dict[str, object] | None:
    if selected is None:
        return None

    return {
        "accepted_agent": selected.accepted_agent,
        "opposite_agent": selected.opposite_agent,
        "branch": selected.branch,
        "repo_dir": str(selected.repo_dir),
    }


def _preflight_payload(
    preflight_summary: PreflightSummary | None,
) -> dict[str, object] | None:
    if preflight_summary is None:
        return None

    return {
        "cli_checks": [
            {"name": cli_check.name, "path": cli_check.path}
            for cli_check in preflight_summary.cli_checks
        ],
        "gh_auth_log_path": str(preflight_summary.gh_auth_log_path),
    }


def _phase_timings_payload(
    phase_timings: Sequence[PhaseTiming],
) -> list[dict[str, object]]:
    return [
        {
            "name": phase_timing.name,
            "duration_seconds": round(phase_timing.duration_seconds, 3),
        }
        for phase_timing in phase_timings
    ]


def _command_logs_payload(
    command_logs: Sequence[CommandLogRecord],
) -> list[dict[str, object]]:
    return [
        {
            "label": command_log.label,
            "command": list(command_log.command),
            "cwd": str(command_log.cwd),
            "log_path": str(command_log.log_path),
        }
        for command_log in command_logs
    ]
