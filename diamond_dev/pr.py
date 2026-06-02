"""Pull request helpers for Diamond Dev."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from diamond_dev.errors import DiamondDevError

if TYPE_CHECKING:
    from diamond_dev.workflow import RunContext, SelectedImplementation

_PR_URL_PATTERN = re.compile(r"https://\S+/pull/\d+")


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


def build_pr_body(context: RunContext, selected: SelectedImplementation) -> str:
    """Build deterministic pull request body text."""
    body_lines = [
        "Automated diamond-dev implementation.",
        "",
        f"- Accepted implementation: {selected.accepted_agent}",
        f"- Selected branch: {selected.branch}",
        f"- Base branch: {context.implementation.base_branch}",
        f"- Comparison notes: {context.notes.comparison_file.name}",
        f"- Review notes: {context.notes.review_file.name}",
    ]
    if context.dirty_records:
        body_lines.extend(("", "Uncommitted dirty files observed:"))
        body_lines.extend(
            (
                f"- {record.label} ({record.branch}): {', '.join(record.files)}"
                for record in context.dirty_records
            ),
        )
    return "\n".join(body_lines)
