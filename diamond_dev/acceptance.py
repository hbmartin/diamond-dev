"""Acceptance marker helpers for wiki comparison pages."""

from __future__ import annotations

import re
from typing import Final, Literal

from diamond_dev.errors import MalformedAcceptanceError

type AgentChoice = Literal["codex", "claude"]

ACCEPTANCE_CHECKBOX: Final = "- [ ] Accept: (codex/claude)"
CODEX_ACCEPTED_LINE: Final = "- [x] Accept: codex"
CLAUDE_ACCEPTED_LINE: Final = "- [x] Accept: claude"
_ACCEPTANCE_LINE_PATTERN: Final = re.compile(r"^- \[[ xX]\] Accept:")


def append_acceptance_checkbox(markdown: str) -> str:
    """Append the deterministic acceptance checkbox to markdown content."""
    separator = "" if markdown.endswith("\n") else "\n"
    return f"{markdown}{separator}{ACCEPTANCE_CHECKBOX}\n"


def ensure_acceptance_checkbox(markdown: str) -> str:
    """Return markdown with exactly one valid acceptance marker."""
    acceptance_lines = _acceptance_lines(markdown)
    if acceptance_lines:
        parse_acceptance(markdown)
        return markdown
    return append_acceptance_checkbox(markdown)


def parse_acceptance(markdown: str) -> AgentChoice | None:
    """Parse the comparison acceptance marker."""
    acceptance_lines = _acceptance_lines(markdown)
    if not acceptance_lines:
        return None
    if len(acceptance_lines) > 1:
        raise MalformedAcceptanceError("Comparison file has multiple accept markers")

    match acceptance_lines[0]:
        case "- [ ] Accept: (codex/claude)":
            return None
        case "- [x] Accept: codex":
            return "codex"
        case "- [x] Accept: claude":
            return "claude"
        case invalid_line:
            raise MalformedAcceptanceError(
                f"Invalid acceptance marker: {invalid_line}",
            )


def acceptance_wait_delays() -> tuple[int, ...]:
    """Return acceptance polling waits in seconds."""
    return (120, *(minutes * 60 for minutes in range(3, 13)))


def _acceptance_lines(markdown: str) -> list[str]:
    return [
        stripped_line
        for line in markdown.splitlines()
        if _ACCEPTANCE_LINE_PATTERN.match(stripped_line := line.strip())
    ]
