"""Pull request helpers for Diamond Dev."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from diamond_dev.errors import DiamondDevError

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
        f"- Accepted implementation: {selected.accepted_agent}",
        f"- Selected branch: {selected.branch}",
        f"- Base branch: {context.implementation.base_branch}",
        f"- Comparison wiki page: {context.wiki.comparison_file.name}",
        f"- Review wiki page: {context.wiki.review_file.name}",
    ]
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
