"""Structured run report writing for Diamond Dev."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger

from diamond_dev.review_judgments import read_review_judgments_status

if TYPE_CHECKING:
    from diamond_dev.executor import CommandLogRecord
    from diamond_dev.preflight import PreflightSummary
    from diamond_dev.workflow import RunContext, SelectedImplementation

type RunStatus = Literal["succeeded", "succeeded_with_warnings", "failed"]
type PhaseWarningStatus = Literal["failed", "skipped"]
type PhaseTimingStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class PhaseWarning:
    """Non-fatal phase degradation captured during a run."""

    phase: str
    status: PhaseWarningStatus
    message: str
    error: str | None
    log_name: str | None


@dataclass(frozen=True, slots=True)
class PhaseTiming:
    """Elapsed time for one orchestration phase."""

    name: str
    duration_seconds: float
    status: PhaseTimingStatus = "succeeded"
    error: str | None = None
    log_path: str | None = None


@dataclass(frozen=True, slots=True)
class RunReportTiming:
    """Timing data for a structured run report."""

    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    phase_timings: Sequence[PhaseTiming]


@dataclass(frozen=True, slots=True)
class RunReportWorkflow:
    """Workflow data for a structured run report."""

    context: RunContext | None
    selected: SelectedImplementation | None
    preflight_summary: PreflightSummary | None


@dataclass(frozen=True, slots=True)
class RunReport:
    """Inputs for writing a structured run report."""

    path: Path
    status: RunStatus
    timing: RunReportTiming
    workflow: RunReportWorkflow
    command_logs: Sequence[CommandLogRecord]
    phase_warnings: Sequence[PhaseWarning]
    error: str | None


def write_run_report(report: RunReport) -> None:
    """Write a deterministic JSON summary for an attempted run."""
    payload = {
        "status": report.status,
        "started_at": report.timing.started_at.isoformat(),
        "finished_at": report.timing.finished_at.isoformat(),
        "duration_seconds": round(report.timing.duration_seconds, 3),
        "error": report.error,
        "context": _context_payload(report.workflow.context),
        "selected_implementation": _selected_payload(report.workflow.selected),
        "preflight": _preflight_payload(report.workflow.preflight_summary),
        "phase_timings": _phase_timings_payload(report.timing.phase_timings),
        "phase_warnings": _phase_warnings_payload(report.phase_warnings),
        "command_logs": _command_logs_payload(report.command_logs),
    }
    _write_json_payload(report.path, payload)
    logger.info("Wrote run report: {}", report.path)
    summary_path = report.path.with_name("run.json")
    if summary_path != report.path:
        _write_json_payload(summary_path, payload)
        logger.info("Wrote run summary: {}", summary_path)


def _write_json_payload(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


def _context_payload(context: RunContext | None) -> dict[str, object] | None:
    if context is None:
        return None

    return {
        "mode": "commit_pair" if context.commit_pair is not None else "plan",
        "cwd": str(context.cwd),
        "config_path": str(context.config.config_path),
        "plan_path": str(context.plan.path),
        "commit_pair": _commit_pair_payload(context),
        "repository_url": context.config.repository_url,
        "wiki_repository_url": context.wiki.url,
        "branches": _branch_payload(context),
        "repositories": {
            "wiki": str(context.wiki.directory),
            **{
                branch.agent_name: str(branch.repo_dir)
                for branch in context.implementation.branches
            },
        },
        "workflow_roles": _workflow_roles_payload(context),
        "artifacts": {
            "comparison": str(context.wiki.comparison_file),
            "comparison_bundle": str(context.wiki.comparison_bundle_file),
            "review": str(context.wiki.review_file),
            "review_judgments": str(context.wiki.review_judgments_file),
            "review_judgments_parse_status": _review_judgments_parse_status(context),
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


def _commit_pair_payload(context: RunContext) -> dict[str, object] | None:
    if context.commit_pair is None:
        return None

    return {
        "slug": context.commit_pair.slug,
        "entries": [
            {
                "label": entry.label,
                "original_arg": entry.original_arg,
                "sha": entry.sha,
                "short_sha": entry.short_sha,
                "branch": entry.branch,
                "source": entry.source,
                "refs": list(entry.ref_names),
            }
            for entry in context.commit_pair.entries
        ],
    }


def _selected_payload(
    selected: SelectedImplementation | None,
) -> dict[str, object] | None:
    if selected is None:
        return None

    return {
        "accepted_agent": selected.accepted_agent,
        "comparison_fixer": selected.comparison_fixer,
        "branch": selected.branch,
        "repo_dir": str(selected.repo_dir),
    }


def _branch_payload(context: RunContext) -> dict[str, object]:
    return {
        "base": context.implementation.base_branch,
        **{
            branch.agent_name: branch.branch
            for branch in context.implementation.branches
        },
    }


def _workflow_roles_payload(context: RunContext) -> dict[str, object]:
    workflow = context.config.workflow
    return {
        "implementers": list(workflow.implementers),
        "comparison_judge": workflow.comparison_judge,
        "comparison_fixer": workflow.comparison_fixer,
        "review_provider": workflow.review_provider,
        "review_judge": workflow.review_judge,
        "review_fixer": workflow.review_fixer,
        "final_reviewer": workflow.final_reviewer,
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


def _review_judgments_parse_status(context: RunContext) -> dict[str, object]:
    status = read_review_judgments_status(context.wiki.review_judgments_file)
    return {
        "status": status.status,
        "path": str(status.path),
        "error": status.error,
    }


def _phase_timings_payload(
    phase_timings: Sequence[PhaseTiming],
) -> list[dict[str, object]]:
    return [
        {
            "name": phase_timing.name,
            "duration_seconds": round(phase_timing.duration_seconds, 3),
            "status": phase_timing.status,
            "error": phase_timing.error,
            "log_path": phase_timing.log_path,
        }
        for phase_timing in phase_timings
    ]


def _phase_warnings_payload(
    phase_warnings: Sequence[PhaseWarning],
) -> list[dict[str, object]]:
    return [
        {
            "phase": phase_warning.phase,
            "status": phase_warning.status,
            "message": phase_warning.message,
            "error": phase_warning.error,
            "log_name": phase_warning.log_name,
        }
        for phase_warning in phase_warnings
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
