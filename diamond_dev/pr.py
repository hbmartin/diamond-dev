"""Pull request helpers for Diamond Dev."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from diamond_dev.errors import DiamondDevError
from diamond_dev.review_judgments import (
    read_review_judgments_status,
    summarize_review_judgments,
)

if TYPE_CHECKING:
    from diamond_dev.report import PhaseWarning
    from diamond_dev.workflow import RunContext, SelectedImplementation

_PR_URL_PATTERN = re.compile(r"https://\S+/pull/\d+")


@dataclass(frozen=True, slots=True)
class ExistingPullRequest:
    """Existing pull request metadata returned by gh."""

    number: int
    state: str
    url: str


def parse_pr_url(gh_output: str) -> str:
    """Extract the GitHub pull request URL from gh output."""
    match = _PR_URL_PATTERN.search(gh_output)
    if match is None:
        raise DiamondDevError(f"Could not parse PR URL from gh output: {gh_output}")
    return match.group(0)


def parse_pr_number(gh_output: str) -> str:
    """Extract a GitHub pull request number from gh output."""
    pr_url = parse_pr_url(gh_output)
    match = re.search(r"/pull/(\d+)", pr_url)
    if match is None:
        raise DiamondDevError(f"Could not parse PR number from gh output: {gh_output}")
    return match.group(1)


def parse_existing_pull_request(gh_output: str) -> ExistingPullRequest | None:
    """Parse the first existing PR from `gh pr list --json` output."""
    try:
        payload = json.loads(gh_output)
    except (json.JSONDecodeError,) as error:
        raise DiamondDevError(f"Could not parse PR list JSON: {gh_output}") from error

    match payload:
        case []:
            return None
        case [
            {"number": int() as number, "state": str() as state, "url": str() as url},
            *_,
        ]:
            return ExistingPullRequest(number=number, state=state, url=url)
        case [dict(), *_]:
            raise DiamondDevError(
                f"PR list item missing number, state, or url: {gh_output}",
            )
        case [*_]:
            raise DiamondDevError(f"Expected PR list item object: {gh_output}")
        case _:
            raise DiamondDevError(f"Expected PR list JSON array: {gh_output}")


def build_pr_body(
    context: RunContext,
    selected: SelectedImplementation,
    warnings: Sequence[PhaseWarning] = (),
) -> str:
    """Build deterministic pull request body text."""
    body_lines = [
        "Automated diamond-dev implementation.",
        "",
        f"- Mode: {'commit-pair' if context.commit_pair is not None else 'plan'}",
        f"- Accepted implementation: {selected.accepted_agent}",
        f"- Comparison fixer: {selected.comparison_fixer}",
        f"- Selected branch: {selected.branch}",
        f"- Base branch: {context.implementation.base_branch}",
        f"- Comparison wiki page: {context.wiki.comparison_file.name}",
        f"- Review wiki page: {context.wiki.review_file.name}",
    ]
    body_lines.extend(("", "Implementation branches:"))
    body_lines.extend(
        f"- {branch.agent_name}: {branch.branch}"
        for branch in context.implementation.branches
    )
    if context.commit_pair is not None:
        body_lines.extend(("", "Compared commits:"))
        body_lines.extend(
            (
                f"- {entry.label}: {entry.short_sha} ({entry.source}) "
                f"from `{entry.original_arg}` on `{entry.branch}`"
                for entry in context.commit_pair.entries
            ),
        )
    body_lines.extend(("", "Workflow roles:"))
    body_lines.extend(_workflow_role_lines(context))
    if context.dirty_records:
        body_lines.extend(("", "Uncommitted dirty files observed:"))
        body_lines.extend(
            (
                f"- {record.label} ({record.branch}): {', '.join(record.files)}"
                for record in context.dirty_records
            ),
        )
    if warnings:
        body_lines.extend(("", "Workflow warnings:"))
        body_lines.extend(_warning_lines(warnings))
    if review_judgment_lines := _review_judgment_lines(context):
        body_lines.extend(("", "Structured review judgments:"))
        body_lines.extend(review_judgment_lines)
    return "\n".join(body_lines)


def _warning_lines(warnings: Sequence[PhaseWarning]) -> list[str]:
    return [
        _warning_line(warning)
        for warning in warnings
    ]


def _warning_line(warning: PhaseWarning) -> str:
    details = [warning.message]
    if warning.error:
        details.append(f"error: {warning.error}")
    if warning.log_name:
        details.append(f"log: {warning.log_name}")
    return f"- {warning.phase} ({warning.status}): {'; '.join(details)}"


def _workflow_role_lines(context: RunContext) -> list[str]:
    workflow = context.config.workflow
    comparison_fixer = workflow.comparison_fixer or "first non-selected implementer"
    return [
        f"- Implementers: {', '.join(workflow.implementers)}",
        f"- Comparison judge: {workflow.comparison_judge}",
        f"- Comparison fixer: {comparison_fixer}",
        f"- Review provider: {workflow.review_provider}",
        f"- Review judge: {workflow.review_judge}",
        f"- Review fixer: {workflow.review_fixer}",
        f"- Final reviewer: {workflow.final_reviewer}",
    ]


def _review_judgment_lines(context: RunContext) -> list[str]:
    status = read_review_judgments_status(context.wiki.review_judgments_file)
    if status.status != "valid" or status.judgments is None:
        return []
    summary = summarize_review_judgments(status.judgments)
    lines = [
        (
            "- Decisions: "
            f"fix={summary.fix}, decline={summary.decline}, "
            f"needs_input={summary.needs_input}"
        ),
        f"- Sidecar: {context.wiki.review_judgments_file.name}",
    ]
    if summary.needs_input_ids:
        lines.append(f"- Needs input IDs: {', '.join(summary.needs_input_ids)}")
    return lines
