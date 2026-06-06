"""Acceptance marker helpers for wiki comparison pages."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from diamond_dev.errors import MalformedAcceptanceError

DEFAULT_ACCEPTANCE_AGENTS: Final = ("codex", "claude")
DEFAULT_ACCEPTANCE_POLL_INTERVAL_SECONDS: Final = 120
DEFAULT_ACCEPTANCE_MAX_WAIT_SECONDS: Final = 4_620
ACCEPTANCE_CHECKBOX: Final = "- [ ] Accept: (codex/claude)"
_ACCEPTANCE_LINE_PATTERN: Final = re.compile(r"^- \[[ xX]\] Accept:")


def append_acceptance_checkbox(
    markdown: str,
    agent_names: Sequence[str] = DEFAULT_ACCEPTANCE_AGENTS,
) -> str:
    """Append the deterministic acceptance checkbox to markdown content."""
    separator = "" if markdown.endswith("\n") else "\n"
    return f"{markdown}{separator}{acceptance_checkbox(agent_names)}\n"


def ensure_acceptance_checkbox(
    markdown: str,
    agent_names: Sequence[str] = DEFAULT_ACCEPTANCE_AGENTS,
) -> str:
    """Return markdown with exactly one valid acceptance marker."""
    acceptance_lines = _acceptance_lines(markdown)
    if acceptance_lines:
        parse_acceptance(markdown, agent_names)
        return markdown
    return append_acceptance_checkbox(markdown, agent_names)


def parse_acceptance(
    markdown: str,
    agent_names: Sequence[str] = DEFAULT_ACCEPTANCE_AGENTS,
) -> str | None:
    """Parse the comparison acceptance marker."""
    allowed_agents = tuple(agent_names)
    acceptance_lines = _acceptance_lines(markdown)
    if not acceptance_lines:
        return None
    if len(acceptance_lines) > 1:
        raise MalformedAcceptanceError("Comparison file has multiple accept markers")

    line = acceptance_lines[0]
    if line == acceptance_checkbox(allowed_agents):
        return None
    normalized_line = line.replace("[X]", "[x]", 1)
    for agent_name in allowed_agents:
        if normalized_line == accepted_line(agent_name):
            return agent_name
    raise MalformedAcceptanceError(f"Invalid acceptance marker: {line}")


def acceptance_checkbox(agent_names: Sequence[str]) -> str:
    """Return the unchecked acceptance marker for allowed agents."""
    return f"- [ ] Accept: ({'/'.join(agent_names)})"


def accepted_line(agent_name: str) -> str:
    """Return the checked acceptance marker for one agent."""
    return f"- [x] Accept: {agent_name}"


def acceptance_wait_delays(
    *,
    poll_interval_seconds: int = DEFAULT_ACCEPTANCE_POLL_INTERVAL_SECONDS,
    max_wait_seconds: int = DEFAULT_ACCEPTANCE_MAX_WAIT_SECONDS,
) -> tuple[int, ...]:
    """Return acceptance polling waits in seconds."""
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    if max_wait_seconds <= 0:
        raise ValueError("max_wait_seconds must be positive")

    delays: list[int] = []
    remaining_seconds = max_wait_seconds
    while remaining_seconds > 0:
        delay_seconds = min(poll_interval_seconds, remaining_seconds)
        delays.append(delay_seconds)
        remaining_seconds -= delay_seconds
    return tuple(delays)


def _acceptance_lines(markdown: str) -> list[str]:
    return [
        stripped_line
        for line in markdown.splitlines()
        if _ACCEPTANCE_LINE_PATTERN.match(stripped_line := line.strip())
    ]
